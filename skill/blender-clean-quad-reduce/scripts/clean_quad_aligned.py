"""Aligned-row recipe ported from the successful contact-aware workspace result.

Independent module: only Blender/Python dependencies; the shared core is injected.
"""
import bpy, bmesh, math
from collections import deque
from dataclasses import replace
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree
from mathutils.geometry import barycentric_transform, closest_point_on_tri

def closed_loops(bm):
    seen=set(); out=[]
    for edge in bm.edges:
        if edge in seen: continue
        cur=edge; v=edge.verts[0]; cycle=[]; visited=set(); good=True
        while True:
            if cur in visited:
                good=cur==edge
                break
            cycle.append(cur);visited.add(cur)
            if len(cur.link_faces)!=2 or any(len(f.verts)!=4 for f in cur.link_faces): good=False;break
            w=cur.other_vert(v)
            if len(w.link_edges)!=4 or len(w.link_faces)!=4: good=False;break
            adjacent={e for f in cur.link_faces for e in f.edges}
            nxt=[e for e in w.link_edges if e not in adjacent]
            if len(nxt)!=1:good=False;break
            v=w;cur=nxt[0]
        if good:
            seen.update(cycle);out.append(cycle)
    return out

def loop_score(edges,scales,matrix,max_aspect=6.0,min_angle=18.0):
    es=set(edges);vs={v for e in edges for v in e.verts};worst=0
    for v in vs:
        rest=[e.other_vert(v) for e in v.link_edges if e not in es]
        if len(rest)!=2:return None
        a,b=rest; pa=matrix @ a.co; pb=matrix @ b.co; p=matrix @ v.co; delta=pb-pa
        if delta.length<1e-9:return None
        t=(p-pa).dot(delta)/delta.length_squared
        if t<0.12 or t>0.88:return None
        err=(p-(pa+delta*t)).length/scales[v]
        worst=max(worst,err)
    # The dissolved strip must produce four-sided faces with reasonable angles.
    for edge in edges:
        fs=set(edge.link_faces)
        boundary=[e for f in fs for e in f.edges if e!=edge]
        corners={v for e in boundary for v in e.verts if v not in vs}
        if len(corners)!=4:return None
        points=list(corners)
        # Use boundary adjacency, skipping the two disappearing vertices.
        adj={v:set() for v in corners}
        for v in corners:
            for e in v.link_edges:
                if not any(f in fs for f in e.link_faces):continue
                w=e.other_vert(v)
                if w in corners:adj[v].add(w)
                elif w in vs:
                    for e2 in w.link_edges:
                        q=e2.other_vert(w)
                        if q in corners and q!=v and any(f in fs for f in e2.link_faces):adj[v].add(q)
        lens=[]
        for v in corners:
            ns=list(adj[v])
            if len(ns)!=2:return None
            x=matrix.to_3x3() @ (ns[0].co-v.co);y=matrix.to_3x3() @ (ns[1].co-v.co)
            if min(x.length,y.length)<1e-8:return None
            angle=x.angle(y)
            if angle<math.radians(min_angle) or angle>math.radians(180-min_angle):return None
            lens.extend([x.length,y.length])
        if max(lens)/min(lens)>max_aspect:return None
    return worst

def open_and_closed_loops(bm):
    out=closed_loops(bm);seen={e for es in out for e in es}
    for edge in bm.edges:
        if edge in seen:continue
        path={edge};good=True
        for start in edge.verts:
            cur=edge;v=start
            while True:
                if len(v.link_edges)==3 and all(len(f.verts)==4 for f in v.link_faces):break
                if len(v.link_edges)!=4 or len(v.link_faces)!=4:good=False;break
                adjacent={e for f in cur.link_faces for e in f.edges}
                nxt=[e for e in v.link_edges if e not in adjacent]
                if len(nxt)!=1:good=False;break
                e=nxt[0]
                if e in path:good=False;break
                if len(e.link_faces)!=2 or any(len(f.verts)!=4 for f in e.link_faces):good=False;break
                path.add(e);v=e.other_vert(v);cur=e
            if not good:break
        if good and all(len(e.link_faces)==2 and all(len(f.verts)==4 for f in e.link_faces) for e in path):
            seen.update(path);out.append(list(path))
    return out


def sphere_grid(bm, matrix, large_extent):
    """Recognize two-pole latitude/longitude components; keep their original axes."""
    bm.verts.ensure_lookup_table()
    bm.verts.index_update()
    seen, plans = set(), []
    for vertex in bm.verts:
        if vertex in seen:
            continue
        seen.add(vertex); stack, part = [vertex], []
        while stack:
            v = stack.pop(); part.append(v)
            for e in v.link_edges:
                w = e.other_vert(v)
                if w not in seen:
                    seen.add(w); stack.append(w)
        poles = sorted(part, key=lambda v: len(v.link_edges), reverse=True)[:2]
        if len(poles) < 2 or not all(len(v.link_edges) > 6 for v in poles):
            return None
        first, last = sorted(poles, key=lambda v: (v.co.z, v.co.y, v.co.x))
        distances, queue = {first: 0}, deque([first])
        while queue:
            v = queue.popleft()
            for edge in v.link_edges:
                w = edge.other_vert(v)
                if w not in distances:
                    distances[w] = distances[v] + 1
                    queue.append(w)
        level_one = {v for v in part if distances[v] == 1}
        current = min(level_one, key=lambda v: v.index)
        ring, previous = [current], None
        while len(ring) < len(level_one):
            choices = [e.other_vert(current) for e in current.link_edges
                       if e.other_vert(current) in level_one and e.other_vert(current) != previous
                       and e.other_vert(current) not in ring]
            if not choices:
                return None
            nxt = min(choices, key=lambda v: v.index)
            previous, current = current, nxt
            ring.append(current)
        rings = [ring]
        for level in range(2, distances[last]):
            next_ring = []
            for v in rings[-1]:
                choices = [e.other_vert(v) for e in v.link_edges if distances[e.other_vert(v)] == level]
                if len(choices) != 1:
                    return None
                next_ring.append(choices[0])
            if len(set(next_ring)) != len(ring):
                return None
            rings.append(next_ring)
        if 2 + len(rings)*len(ring) != len(part) or len(rings) < 5:
            return None
        materials = {f.material_index for v in part for f in v.link_faces}
        if len(materials) != 1:
            return None
        world = [matrix @ v.co for v in part]
        extent = max(max(p[i] for p in world)-min(p[i] for p in world) for i in range(3))
        if extent >= large_extent:
            chosen, stride = rings[1::2], 1
        else:
            fractions = (0.15, 0.30, 0.50, 0.70, 0.85) if len(ring) > 12 else (0.25, 0.50, 0.75)
            indices = sorted({max(0, min(len(rings)-1, round(t*(len(rings)+1))-1)) for t in fractions})
            chosen = [rings[i] for i in indices]
            stride = 2 if len(ring) % 2 == 0 else 1
        chosen = [r[::stride] for r in chosen]
        plans.append((part, first, last, chosen, next(iter(materials))))
    new_faces, keep, stats = [], set(), []
    for part, first, last, rings, material in plans:
        count = len(rings[0])
        center = sum((v.co for v in part), Vector())/len(part)
        for j in range(count):
            k = (j+1) % count
            faces = [[first, rings[0][j], rings[0][k]]]
            faces.extend([a[j], b[j], b[k], a[k]] for a, b in zip(rings, rings[1:]))
            faces.append([last, rings[-1][k], rings[-1][j]])
            for vs in faces:
                normal = (vs[1].co-vs[0].co).cross(vs[2].co-vs[0].co)
                if normal.dot(sum((v.co for v in vs), Vector())/len(vs)-center) < 0:
                    vs.reverse()
                new_faces.append((vs, material)); keep.update(vs)
        stats.append({"source": len(part), "vertices": count*len(rings)+2,
                      "longitudes": count, "latitudes": len(rings)})
    for face in list(bm.faces):
        bm.faces.remove(face)
    for edge in list(bm.edges):
        bm.edges.remove(edge)
    for vertex in list(bm.verts):
        if vertex not in keep:
            bm.verts.remove(vertex)
    for vs, material in new_faces:
        face = bm.faces.new(vs)
        face.material_index = material
        face.smooth = True
    bm.normal_update()
    index_layer = bm.verts.layers.int["_cqr_original_vertex"]
    allowed = {tuple(sorted(v[index_layer] for v in e.verts)) for e in bm.edges}
    return {"components": stats, "allowed_edges": allowed}


def transfer_uvs(bm, source):
    source.calc_loop_triangles()
    triangles = list(source.loop_triangles)
    coords = [v.co.copy() for v in source.vertices]
    tree = BVHTree.FromPolygons(coords, [tuple(t.vertices) for t in triangles], all_triangles=True)
    for source_layer in source.uv_layers:
        uv = bm.loops.layers.uv.get(source_layer.name) or bm.loops.layers.uv.new(source_layer.name)
        for face in bm.faces:
            center = face.calc_center_median()
            for loop in face.loops:
                _, _, index, _ = tree.find_nearest(loop.vert.co.lerp(center, 0.001))
                triangle = triangles[index]
                hit = closest_point_on_tri(loop.vert.co, *[coords[i] for i in triangle.vertices])
                value = barycentric_transform(hit, *[coords[i] for i in triangle.vertices],
                    *[Vector((*source_layer.data[i].uv, 0)) for i in triangle.loops])
                loop[uv].uv = (round(value.x, 6), round(value.y, 6))


def transfer_reference_normals(source, mesh):
    source.calc_loop_triangles()
    triangles = list(source.loop_triangles)
    coords = [v.co.copy() for v in source.vertices]
    source_normals = [n.vector.copy() for n in source.corner_normals]
    tree = BVHTree.FromPolygons(coords, [tuple(t.vertices) for t in triangles], all_triangles=True)
    normals, fallbacks = [None]*len(mesh.loops), 0
    for polygon in mesh.polygons:
        for index in polygon.loop_indices:
            v = mesh.vertices[mesh.loops[index].vertex_index].co
            point = v.lerp(polygon.center, 0.001)
            hit, _, triangle_index, distance = tree.find_nearest(point)
            triangle = triangles[triangle_index]
            if triangle.normal.dot(polygon.normal) < 0.15:
                candidates = [h for h in tree.find_nearest_range(point, distance+0.001)
                              if triangles[h[2]].normal.dot(polygon.normal) > 0.15]
                if candidates:
                    hit, _, triangle_index, distance = min(candidates, key=lambda h: h[3])
                    triangle = triangles[triangle_index]
            hit = closest_point_on_tri(v, *[coords[j] for j in triangle.vertices])
            normal = barycentric_transform(hit, *[coords[j] for j in triangle.vertices],
                                           *[source_normals[j] for j in triangle.loops])
            if normal.length_squared < 1e-12 or normal.dot(polygon.normal) <= 0:
                normal = polygon.normal.copy(); fallbacks += 1
            else:
                normal.normalize()
            normals[index] = tuple(normal)
    mesh.normals_split_custom_set(normals)
    mesh.update()
    return {"fallback_corners": fallbacks,
            "invalid_normals": sum(not all(math.isfinite(c) for c in n.vector) or n.vector.length < 0.99
                                   for n in mesh.corner_normals)}


def reducer_type(core):
    class AlignedReducer(core.Reducer):
        def __init__(self, obj, options, context_objects=()):
            self.stage = 0
            self.first_pass_steps = 0
            self.correction_points = []
            self.sphere_stats = None
            self.recipe_options = options
            self.budgets = sorted({min(0.9, options.aligned_budget), min(2.0, options.aligned_budget), options.aligned_budget})
            self.budget_phase = 0
            # Reconstruction matches the successful workspace: retain UV-aware
            # triangle pairs even when later row reduction may change UV cuts.
            initial = replace(options, preserve_uv=True, grid_strategy="LOOPS_ONLY")
            super().__init__(obj, initial, context_objects)
            self.options = options
            self.aligned_initial = self.bm.copy()
            if options.aligned_spheres:
                trial = self.bm.copy()
                stats = sphere_grid(trial, self.matrix, options.sphere_large_extent)
                if stats is not None and len(trial.verts) < len(self.bm.verts):
                    self.sphere_stats = stats["components"]
                    self.sphere_allowed_edges = stats["allowed_edges"]
                    if self._valid(trial):
                        self.bm.free(); self.bm = trial
                        transfer_uvs(self.bm, self.obj.data)
                    else:
                        trial.free(); self.sphere_stats = None
                        self.warnings.append("Sphere-grid proposal exceeded quality bounds; using aligned row strips")
                else:
                    trial.free()
            self.stop_reason = ""

        def _try_grids(self):
            # Neither odd nor even checkerboard un-subdivision is used.
            return

        def _prepare_constraints(self):
            super()._prepare_constraints()
            for part in core._parts(self.bm):
                points = [self.matrix @ v.co for v in part]
                diagonal = math.sqrt(sum((max(p[i] for p in points)-min(p[i] for p in points))**2 for i in range(3)))
                scale = min(self.recipe_options.aligned_local_error,
                            max(self.recipe_options.aligned_min_error, diagonal*self.recipe_options.relative_error))
                for v in part:
                    self.scales[v] = scale
            if self.contact_tree:
                contact = {v for v in self.bm.verts if self.contact_tree.find_nearest(
                    self.matrix @ v.co, self.recipe_options.contact_distance)[0] is not None}
                contact.update(e.other_vert(v) for v in list(contact) for e in v.link_edges)
                for v in contact:
                    self.scales[v] *= self.recipe_options.contact_factor
                self.contact_vertex_count = len(contact)

        def _flow_valid(self, bm):
            if self.sphere_stats is not None:
                # The detected latitude/longitude arrays define each new edge;
                # poles are intentional triangle fans, never rotated quad grids.
                layer = bm.verts.layers.int[core.INDEX_LAYER]
                actual = {tuple(sorted(v[layer] for v in e.verts)) for e in bm.edges}
                return actual == self.sphere_allowed_edges
            layer = bm.verts.layers.int[core.INDEX_LAYER]
            return all(self._follows_flow(e.verts[0][layer], e.verts[1][layer])
                       or any(len(f.verts) == 3 for f in e.link_faces) for e in bm.edges)

        def _valid(self, bm):
            info = core._topology(bm)
            if any(info[k] != self.baseline[k] for k in ("parts", "euler", "boundary_edges")):
                return False
            return (not info["bad_edges"] and not info["degenerate_faces"]
                    and self._flow_valid(bm)
                    and self._deviation(bm)["max"] <= self.options.max_error*(1+1e-5))

        def _finish_pass(self):
            if self.stage == 0 and self.sphere_stats is None and self.options.correction_threshold > 0:
                self.bm.verts.index_update()
                tree = BVHTree.FromPolygons([self.matrix @ v.co for v in self.bm.verts],
                    [[v.index for v in f.verts] for f in self.bm.faces])
                points = [self.matrix @ v.co for v in self.obj.data.vertices
                          if tree.find_nearest(self.matrix @ v.co)[3] > self.options.correction_threshold]
                self.correction_points = points
                if points:
                    local_points = [self.matrix.inverted() @ p for p in points]
                    if self.options.mirror_correction:
                        local_points += [Vector((-p.x, p.y, p.z)) for p in local_points]
                    points = [self.matrix @ p for p in local_points]
                    kd = KDTree(len(points))
                    for i, p in enumerate(points):
                        kd.insert(p, i)
                    kd.balance()
                    self.bm.free(); self.bm = self.aligned_initial.copy()
                    self._prepare_constraints()
                    for v in self.bm.verts:
                        if kd.find(self.matrix @ v.co)[2] < self.options.correction_radius:
                            self.scales[v] *= self.options.correction_factor
                    self.first_pass_steps = self.steps
                    self.steps = 0
                    self.stage = 1
                    self.budget_phase = 0
                    return
            self.done = True
            self.stop_reason = "Aligned rows: quality limit" if not self.options.aligned_target_vertices else "Aligned reference calibration finished"

        def step(self, count=1):
            if self.sphere_stats is not None:
                self.done = True
                self.stop_reason = "Recognized latitude/longitude grids retained with pole fans"
                return self.progress()
            for _ in range(count):
                if self.done:
                    break
                if self.steps+self.first_pass_steps >= self.options.max_steps:
                    self.done = True; self.stop_reason = "Maximum strip edits reached"
                    break
                if self.options.aligned_target_vertices and len(self.bm.verts) <= self.options.aligned_target_vertices:
                    self._finish_pass()
                    continue
                candidates = []
                tags = {e: core._edge_tags(e, self.bm, self.options) for e in self.bm.edges}
                layer = self.bm.verts.layers.int[core.INDEX_LAYER]
                finder = open_and_closed_loops if self.options.allow_open_strips else closed_loops
                for edges in finder(self.bm):
                    if any(tags[e] for e in edges):
                        continue
                    if any(v[layer] in self.protected or v.is_boundary for e in edges for v in e.verts):
                        continue
                    score = loop_score(edges, self.scales, self.matrix, self.options.max_aspect, self.options.min_angle)
                    if score is not None and score <= self.budgets[self.budget_phase]:
                        candidates.append((score, edges))
                if not candidates:
                    if self.budget_phase+1 < len(self.budgets):
                        self.budget_phase += 1
                    else:
                        self._finish_pass()
                    continue
                candidates.sort(key=lambda p: p[0])
                locked, edits = set(), 0
                for _, edges in candidates:
                    if self.options.aligned_target_vertices and len(self.bm.verts) <= self.options.aligned_target_vertices:
                        break
                    if not all(e.is_valid for e in edges):
                        continue
                    surrounding = {v for e in edges for f in e.link_faces for v in f.verts}
                    if surrounding & locked:
                        continue
                    locked.update(surrounding)
                    bmesh.ops.dissolve_edges(self.bm, edges=edges, use_verts=True, use_face_split=False)
                    edits += 1; self.steps += 1
                    if self.steps+self.first_pass_steps >= self.options.max_steps:
                        break
                if not edits:
                    self._finish_pass()
            return self.progress()

        def finish(self):
            mesh, report = super().finish()
            try:
                if self.options.transfer_normals:
                    report["reference_normals"] = transfer_reference_normals(self.obj.data, mesh)
            except Exception:
                core.remove_mesh(mesh)
                raise
            report["recipe"] = "Contact-aware aligned rows + latitude/longitude grids"
            report["sphere_components"] = self.sphere_stats
            report["correction_points"] = len(self.correction_points)
            report["first_pass_edits"] = self.first_pass_steps
            report["budget_schedule"] = self.budgets
            report["diagonal_grid_policy"] = "No checkerboard un-subdivision; original row axes and explicit pole fans"
            return mesh, report

        def close(self):
            if hasattr(self, "aligned_initial") and self.aligned_initial.is_valid:
                self.aligned_initial.free()
            super().close()

    return AlignedReducer
