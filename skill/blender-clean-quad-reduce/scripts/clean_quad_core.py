"""Quad-strip reduction. Runs in Blender; no add-on registration or AI required.

Every accepted edit is a quad-grid un-subdivision or dissolution of an entire
quad strip. No collapse decimation, remeshing, or generated replacement surface.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, replace
from array import array
import hashlib
import json
import math
from pathlib import Path
import struct

import bpy
import bmesh
from mathutils import Vector
from mathutils.bvhtree import BVHTree

VERSION = "1.0.0"
INDEX_LAYER = "_cqr_original_vertex"


class ReductionError(RuntimeError):
    pass


@dataclass(frozen=True)
class Options:
    method: str = "STRICT"
    # Distances are world-space Blender units, not assumed metres.
    max_error: float = 0.015
    relative_error: float = 0.05
    max_aspect: float = 6.0
    min_angle: float = 18.0
    min_quad_fraction: float = 0.90
    max_weight_error: float = 0.15
    protect_group: str = ""
    preserve_uv: bool = True
    preserve_materials: bool = True
    preserve_sharp: bool = True
    weld_uv_splits: bool = True
    allow_open_strips: bool = False
    # AUTO grid trials run only on untextured meshes without shape keys.
    grid_strategy: str = "AUTO"
    max_steps: int = 1500
    transfer_normals: bool = True
    contact_distance: float = 0.006
    contact_factor: float = 0.5
    aligned_local_error: float = 0.008
    aligned_min_error: float = 0.0003
    aligned_budget: float = 3.0
    aligned_target_vertices: int = 0
    aligned_spheres: bool = True
    sphere_large_extent: float = 0.075
    correction_threshold: float = 0.007
    correction_radius: float = 0.012
    correction_factor: float = 0.35
    mirror_correction: bool = True

    def validate(self):
        for name in ("max_error", "relative_error", "max_aspect", "min_angle",
                     "min_quad_fraction", "max_weight_error", "contact_distance", "contact_factor"):
            if not math.isfinite(getattr(self, name)):
                raise ReductionError(f"{name} must be finite")
        if self.max_error <= 0 or not 0 < self.relative_error <= 1:
            raise ReductionError("Error bounds must be positive; relative_error <= 1")
        if self.max_aspect < 1 or not 0 < self.min_angle < 90:
            raise ReductionError("Invalid quad aspect ratio / minimum angle")
        if not 0 <= self.min_quad_fraction <= 1 or not 0 <= self.max_weight_error <= 1:
            raise ReductionError("Quad fraction / weight error must be in [0, 1]")
        if self.grid_strategy not in {"AUTO", "LOOPS_ONLY"} or self.max_steps < 1:
            raise ReductionError("Invalid grid strategy / maximum steps")
        if self.contact_distance < 0 or not 0 < self.contact_factor <= 1:
            raise ReductionError("Contact distance must be >= 0 and contact factor in (0, 1]")
        if self.method not in {"STRICT", "ALIGNED"}:
            raise ReductionError("Unknown reduction method")
        for name in ("aligned_local_error", "aligned_min_error", "aligned_budget", "sphere_large_extent",
                     "correction_radius", "correction_factor"):
            if not math.isfinite(getattr(self, name)) or getattr(self, name) <= 0:
                raise ReductionError(f"{name} must be positive and finite")
        if self.aligned_target_vertices < 0 or self.correction_threshold < 0 or self.correction_factor > 1:
            raise ReductionError("Invalid reference calibration / correction settings")
        return self


def preset(name="BALANCED", **overrides):
    profiles = {
        "ALIGNED": Options(method="ALIGNED", max_error=0.025, relative_error=0.025,
                           min_quad_fraction=0.65, preserve_uv=False, allow_open_strips=True,
                           grid_strategy="LOOPS_ONLY"),
        "DETAIL": Options(max_error=0.005, relative_error=0.025,
                          min_quad_fraction=0.95, grid_strategy="LOOPS_ONLY"),
        "BALANCED": Options(),
        "MINIMAL": Options(max_error=0.025, relative_error=0.10,
                           min_quad_fraction=0.93, max_weight_error=0.20,
                           preserve_uv=False),
    }
    if name not in profiles:
        raise ReductionError(f"Unknown preset: {name}")
    return replace(profiles[name], **overrides).validate()


def _parts(bm):
    seen = set()
    out = []
    for vertex in bm.verts:
        if vertex in seen:
            continue
        seen.add(vertex)
        stack, part = [vertex], []
        while stack:
            v = stack.pop()
            part.append(v)
            for edge in v.link_edges:
                other = edge.other_vert(v)
                if other not in seen:
                    seen.add(other)
                    stack.append(other)
        out.append(part)
    return out


def _topology(bm):
    return {
        "vertices": len(bm.verts), "edges": len(bm.edges), "faces": len(bm.faces),
        "parts": len(_parts(bm)),
        "euler": len(bm.verts) - len(bm.edges) + len(bm.faces),
        "boundary_edges": sum(e.is_boundary for e in bm.edges),
        "bad_edges": sum(not e.is_manifold and not e.is_boundary for e in bm.edges),
        "degenerate_faces": sum(f.calc_area() < 1e-16 for f in bm.faces),
        "face_sides": dict(Counter(str(len(f.verts)) for f in bm.faces)),
    }


def _textured(obj):
    return any(m and m.use_nodes and m.node_tree and any(
        n.type == "TEX_IMAGE" and n.image for n in m.node_tree.nodes
    ) for m in obj.data.materials)


def analyze_object(obj):
    if obj.type != "MESH":
        raise ReductionError(f"{obj.name}: select a mesh")
    if obj.mode != "OBJECT" or obj.data.is_editmode:
        raise ReductionError(f"{obj.name}: switch to Object Mode first")
    if any(not math.isfinite(c) for v in obj.data.vertices for c in v.co):
        raise ReductionError(f"{obj.name}: non-finite vertex coordinates")
    if any(not math.isfinite(c) for row in obj.matrix_world for c in row):
        raise ReductionError(f"{obj.name}: non-finite world transform")
    bm = bmesh.new()
    try:
        bm.from_mesh(obj.data)
        info = _topology(bm)
    finally:
        bm.free()
    info.update({
        "object": obj.name, "mesh": obj.data.name,
        "shape_keys": list(obj.data.shape_keys.key_blocks.keys()) if obj.data.shape_keys else [],
        "uv_layers": list(obj.data.uv_layers.keys()), "textured": _textured(obj),
        "vertex_groups": len(obj.vertex_groups),
        "modifiers": [m.type for m in obj.modifiers if m.show_viewport],
        "world_unit_scale": bpy.context.scene.unit_settings.scale_length,
    })
    return info


def _weight_map(v, layer):
    return dict(v[layer]) if layer else {}


def _same_vertex_data(a, b, bm, tolerance):
    deform = bm.verts.layers.deform.active
    if _weight_map(a, deform) != _weight_map(b, deform):
        return False
    for layer in bm.verts.layers.shape.values():
        if (a[layer] - b[layer]).length > tolerance:
            return False
    # Welding is optional. Keep differing point attributes separate.
    for family in ("float", "int", "float_vector", "color", "float_color", "string"):
        layers = getattr(bm.verts.layers, family, None)
        if not layers:
            continue
        for layer in layers.values():
            if layer.name == INDEX_LAYER:
                continue
            x, y = a[layer], b[layer]
            if hasattr(x, "__len__") and not isinstance(x, (str, bytes)):
                if any(abs(i - j) > tolerance for i, j in zip(x, y)):
                    return False
            elif x != y:
                return False
    return True


def _weld_splits(bm, matrix):
    """Stitch reciprocal boundary edges, never coincident isolated contact points."""
    eps = 1e-7
    bm.verts.index_update()
    buckets = defaultdict(list)

    def key(v):
        return tuple(round(c / eps) for c in matrix @ v.co)

    for edge in bm.edges:
        if edge.is_boundary:
            a, b = (key(v) for v in edge.verts)
            buckets[tuple(sorted((a, b)))].append(edge)
    parent = {}

    def root(v):
        while v in parent:
            v = parent[v]
        return v

    for edges in buckets.values():
        # Multiple stacked surfaces are ambiguous; leave them separate.
        if len(edges) != 2:
            continue
        e1, e2 = edges
        f1, f2 = e1.link_faces[0], e2.link_faces[0]
        if f1 == f2 or f1.material_index != f2.material_index:
            continue
        if f1.normal.dot(f2.normal) < -0.1:
            continue
        l1, l2 = e1.link_loops[0], e2.link_loops[0]
        if key(l1.vert) != key(l2.link_loop_next.vert):
            continue
        pairs = []
        for a in e1.verts:
            b = next((v for v in e2.verts if key(v) == key(a)), None)
            if b is None or not _same_vertex_data(a, b, bm, eps):
                pairs = []
                break
            pairs.append((a, b))
        for a, b in pairs:
            a, b = root(a), root(b)
            if a != b:
                keep, drop = sorted((a, b), key=lambda v: v.index)
                parent[drop] = keep
    targets = {v: root(v) for v in parent}
    if targets:
        bmesh.ops.weld_verts(bm, targetmap=targets)
        bm.normal_update()
    return len(targets)


def _join_triangles(bm, options):
    bmesh.ops.join_triangles(
        bm, faces=[f for f in bm.faces if len(f.verts) == 3],
        cmp_seam=options.preserve_uv, cmp_sharp=options.preserve_sharp,
        cmp_uvs=options.preserve_uv, cmp_vcols=True,
        cmp_materials=options.preserve_materials, angle_face_threshold=math.radians(60),
        angle_shape_threshold=math.radians(90), topology_influence=1.0,
    )


def _edge_tags(edge, bm, options):
    tags = set()
    if not edge.is_manifold:
        tags.add("boundary")
        return tags
    a, b = edge.link_loops
    if options.preserve_materials and a.face.material_index != b.face.material_index:
        tags.add("material")
    if options.preserve_sharp and not edge.smooth:
        tags.add("sharp")
    if options.preserve_uv:
        if edge.seam:
            tags.add("uv")
        for layer in bm.loops.layers.uv.values():
            for v in edge.verts:
                x = a if a.vert == v else a.link_loop_next
                y = b if b.vert == v else b.link_loop_next
                if (x[layer].uv - y[layer].uv).length > 1e-6:
                    tags.add("uv")
    for family in ("color", "float_color"):
        for layer in getattr(bm.loops.layers, family).values():
            for v in edge.verts:
                x = a if a.vert == v else a.link_loop_next
                y = b if b.vert == v else b.link_loop_next
                if any(abs(i-j) > 1e-6 for i, j in zip(x[layer], y[layer])):
                    tags.add("color")
    return tags


def _strips(bm, allow_open):
    """Trace opposite edges at valence four; endpoints may be valence three."""
    seen = set()
    for edge in bm.edges:
        if edge in seen or not edge.is_manifold:
            continue
        if any(len(f.verts) != 4 for f in edge.link_faces):
            continue
        path, good, closed = {edge}, True, False
        for start in edge.verts:
            cur, v = edge, start
            while True:
                if allow_open and len(v.link_edges) == 3 and len(v.link_faces) == 3:
                    if all(len(f.verts) == 4 for f in v.link_faces):
                        break
                if len(v.link_edges) != 4 or len(v.link_faces) != 4:
                    good = False
                    break
                adjacent = {e for f in cur.link_faces for e in f.edges}
                nxt = [e for e in v.link_edges if e not in adjacent]
                if len(nxt) != 1:
                    good = False
                    break
                e = nxt[0]
                if e == edge:
                    closed = True
                    break
                if e in path or not e.is_manifold or any(len(f.verts) != 4 for f in e.link_faces):
                    good = False
                    break
                path.add(e)
                v, cur = e.other_vert(v), e
            if not good or closed:
                break
        if good:
            seen.update(path)
            yield sorted(path, key=lambda e: e.index)


class Reducer:
    """Incremental CPU reducer; caller owns the returned mesh, not the source."""

    def __init__(self, obj, options=None, context_objects=()):
        if bpy.app.version < (5, 1, 0):
            raise ReductionError("Clean Quad Reduce requires Blender 5.1 or later")
        self.obj = obj
        self.options = (options or Options()).validate()
        self.source_info = analyze_object(obj)
        if obj.library or obj.data.library:
            raise ReductionError(f"{obj.name}: linked data must be made local first")
        if abs(obj.matrix_world.determinant()) < 1e-12:
            raise ReductionError(f"{obj.name}: zero scale")
        if not obj.data.vertices or not obj.data.polygons:
            raise ReductionError(f"{obj.name}: empty mesh")
        self.matrix = obj.matrix_world.copy()
        self.context_objects = list(context_objects)
        self.bm = bmesh.new()
        self.done = False
        self.closed = False
        self.steps = 0
        self.rejected = 0
        self.grid_iterations = 0
        self.stop_reason = ""
        self.warnings = []
        try:
            self.bm.from_mesh(obj.data)
            self.bm.normal_update()
            if self.source_info["bad_edges"] or self.source_info["degenerate_faces"]:
                raise ReductionError(f"{obj.name}: repair non-manifold edges / degenerate faces first")
            layer = self.bm.verts.layers.int.get(INDEX_LAYER) or self.bm.verts.layers.int.new(INDEX_LAYER)
            self.bm.verts.index_update()
            for v in self.bm.verts:
                v[layer] = v.index
            self.welded = _weld_splits(self.bm, self.matrix) if self.options.weld_uv_splits else 0
            _join_triangles(self.bm, self.options)
            self.baseline = _topology(self.bm)
            self.quad_floor = min(self.options.min_quad_fraction,
                                  self.baseline["face_sides"].get("4", 0)/self.baseline["faces"])
            self._build_reference()
            self._build_contacts()
            self._build_reference_flow()
            self._prepare_constraints()
            if self.options.grid_strategy == "AUTO":
                self._try_grids()
            self.quad_floor = max(self.quad_floor, min(self.options.min_quad_fraction,
                                  sum(len(f.verts) == 4 for f in self.bm.faces)/len(self.bm.faces)))
            self._prepare_constraints()
            self.blocked = set()
            if any(m.show_viewport and m.type != "ARMATURE" for m in obj.modifiers):
                self.warnings.append("Shape bounds cover the base mesh; other modifiers are retained and need visual review.")
        except Exception:
            self.bm.free()
            self.closed = True
            raise

    def _build_reference(self):
        self.obj.data.calc_loop_triangles()
        self.reference_points = [self.matrix @ v.co for v in self.obj.data.vertices]
        triangles = [tuple(t.vertices) for t in self.obj.data.loop_triangles]
        self.reference_points.extend(
            sum((self.matrix @ self.obj.data.vertices[i].co for i in tri), Vector()) / 3
            for tri in triangles
        )
        self.reference_tree = BVHTree.FromPolygons(
            [self.matrix @ v.co for v in self.obj.data.vertices], triangles,
            all_triangles=True,
        )

    def _prepare_constraints(self):
        bm = self.bm
        layer = bm.verts.layers.int[INDEX_LAYER]
        self.scales = {}
        for part in _parts(bm):
            coords = [self.matrix @ v.co for v in part]
            diagonal = Vector(tuple(max(p[i] for p in coords)-min(p[i] for p in coords) for i in range(3))).length
            bound = min(self.options.max_error, diagonal * self.options.relative_error)
            for v in part:
                self.scales[v] = max(bound, 1e-12)
        if self.contact_tree:
            contact = {v for v in bm.verts if self.contact_tree.find_nearest(
                self.matrix @ v.co, self.options.contact_distance)[0] is not None}
            contact.update(e.other_vert(v) for v in list(contact) for e in v.link_edges)
            for v in contact:
                self.scales[v] *= self.options.contact_factor
            self.contact_vertex_count = len(contact)
        self.protected = set()
        deform = bm.verts.layers.deform.active
        group = self.obj.vertex_groups.get(self.options.protect_group) if self.options.protect_group else None
        if self.options.protect_group and group is None:
            raise ReductionError(f"Protection group not found: {self.options.protect_group}")
        if group and deform:
            self.protected.update(v[layer] for v in bm.verts if v[deform].get(group.index, 0) > 0)
        # Expression vertices and their one-ring retain their exact connectivity.
        shapes = list(bm.verts.layers.shape.values())
        if shapes:
            expressive = [v for v in bm.verts if any((v[s] - v.co).length > 1e-7 for s in shapes)]
            for v in expressive:
                self.protected.add(v[layer])
                self.protected.update(e.other_vert(v)[layer] for e in v.link_edges)

    def _build_contacts(self):
        self.contact_tree = None
        self.contact_vertex_count = 0
        self.contact_overlap_pairs = 0
        if not self.context_objects or self.options.contact_distance <= 0:
            return
        source = self.obj.data
        source.calc_loop_triangles()
        coords = [self.matrix @ v.co for v in source.vertices]
        triangles = [tuple(t.vertices) for t in source.loop_triangles]
        own_tree = BVHTree.FromPolygons(coords, triangles, all_triangles=True)
        hits = set()
        for other in self.context_objects:
            if other == self.obj or other.type != "MESH" or other.get("cqr_backup") or other.get("cqr_preview"):
                continue
            other.data.calc_loop_triangles()
            if not other.data.loop_triangles:
                continue
            tree = BVHTree.FromPolygons([other.matrix_world @ v.co for v in other.data.vertices],
                                       [tuple(t.vertices) for t in other.data.loop_triangles], all_triangles=True)
            overlaps = own_tree.overlap(tree)
            self.contact_overlap_pairs += len(overlaps)
            hits.update(i for i, _ in overlaps)
        if hits:
            self.contact_tree = BVHTree.FromPolygons(coords, [triangles[i] for i in sorted(hits)], all_triangles=True)

    def _build_reference_flow(self):
        """Allowed new edges follow an original quad row, never turn across a cell."""
        layer = self.bm.verts.layers.int[INDEX_LAYER]
        self.flow_neighbors = {}
        self.flow_next = {}
        self.flow_cache = {}
        for v in self.bm.verts:
            vid = v[layer]
            self.flow_neighbors[vid] = [e.other_vert(v)[layer] for e in v.link_edges]
            if len(v.link_edges) != 4 or len(v.link_faces) != 4 or any(len(f.verts) != 4 for f in v.link_faces):
                continue
            for edge in v.link_edges:
                adjacent = {e for f in edge.link_faces for e in f.edges}
                opposite = [e for e in v.link_edges if e not in adjacent]
                if len(opposite) == 1:
                    self.flow_next[(edge.other_vert(v)[layer], vid)] = opposite[0].other_vert(v)[layer]

    def _follows_flow(self, a, b):
        key = tuple(sorted((a, b)))
        if key in self.flow_cache:
            return self.flow_cache[key]
        for neighbor in self.flow_neighbors.get(a, ()):
            previous, current = a, neighbor
            seen = set()
            while (previous, current) not in seen:
                if current == b:
                    self.flow_cache[key] = True
                    return True
                seen.add((previous, current))
                nxt = self.flow_next.get((previous, current))
                if nxt is None:
                    break
                previous, current = current, nxt
        self.flow_cache[key] = False
        return False

    def _flow_valid(self, bm):
        layer = bm.verts.layers.int[INDEX_LAYER]
        return all(self._follows_flow(e.verts[0][layer], e.verts[1][layer]) for e in bm.edges)

    def _deviation(self, bm):
        bm.verts.index_update()
        points = [self.matrix @ v.co for v in bm.verts]
        tree = BVHTree.FromPolygons(points, [[v.index for v in f.verts] for f in bm.faces])
        distances = [tree.find_nearest(p)[3] for p in self.reference_points]
        # Bidirectional coverage catches oversized diagonals / bridging faces.
        for f in bm.faces:
            center = sum((self.matrix @ v.co for v in f.verts), Vector()) / len(f.verts)
            distances.append(self.reference_tree.find_nearest(center)[3])
        distances.sort()
        return {"max": distances[-1], "p95": distances[int((len(distances)-1)*0.95)]}

    def _valid(self, bm):
        if not self._flow_valid(bm):
            return False
        info = _topology(bm)
        if any(info[k] != self.baseline[k] for k in ("parts", "euler", "boundary_edges")):
            return False
        if info["bad_edges"] or info["degenerate_faces"]:
            return False
        quad = info["face_sides"].get("4", 0) / max(info["faces"], 1)
        if quad + 1e-9 < self.quad_floor:
            return False
        return self._deviation(bm)["max"] <= self.options.max_error * (1 + 1e-5)

    def _try_grids(self):
        if self.obj.data.shape_keys or self.source_info["textured"] or self.protected:
            self.warnings.append("Grid un-subdivision disabled for textures, shape keys or protected vertices; quad strips remain available.")
            return
        if any(_edge_tags(e, self.bm, self.options) for e in self.bm.edges):
            self.warnings.append("Grid un-subdivision disabled by protected UV, material, color, sharp or boundary edges.")
            return
        best = self.bm
        start = self.bm.copy()
        try:
            # Odd iterations create a diamond grid, explicitly forbidden here.
            for iterations in (2, 4):
                trial = start.copy()
                bmesh.ops.unsubdivide(trial, verts=list(trial.verts), iterations=iterations)
                _join_triangles(trial, self.options)
                if len(trial.verts) < len(best.verts) and self._valid(trial):
                    best.free()
                    best = trial
                    self.grid_iterations = iterations
                    self.bm = best
                else:
                    trial.free()
        finally:
            start.free()
        self.bm = best

    def _signature(self, edges):
        layer = self.bm.verts.layers.int[INDEX_LAYER]
        return tuple(sorted(tuple(sorted(v[layer] for v in e.verts)) for e in edges))

    def _score(self, edges, tags):
        bm, opt = self.bm, self.options
        es = set(edges)
        vs = {v for e in edges for v in e.verts}
        endpoints = sum(len(v.link_edges) == 3 for v in vs)
        remaining_faces = len(bm.faces)-len(edges)
        if (self.current_quad_count-len(edges)-endpoints)/max(remaining_faces, 1) + 1e-9 < self.quad_floor:
            return None
        layer = bm.verts.layers.int[INDEX_LAYER]
        deform = bm.verts.layers.deform.active
        if any(tags[e] for e in edges):
            return None
        worst = 0.0
        for v in vs:
            if v[layer] in self.protected or v.is_boundary:
                return None
            rest = [e for e in v.link_edges if e not in es]
            if len(rest) != 2 or tags[rest[0]] != tags[rest[1]]:
                return None
            a, b = (e.other_vert(v) for e in rest)
            pa, pb, p = (self.matrix @ w.co for w in (a, b, v))
            delta = pb-pa
            if delta.length_squared < 1e-20:
                return None
            t = (p-pa).dot(delta)/delta.length_squared
            if not 0.12 <= t <= 0.88:
                return None
            worst = max(worst, (p-(pa+delta*t)).length/self.scales[v])
            if deform:
                wa, wb, wv = (_weight_map(w, deform) for w in (a, b, v))
                keys = set(wa) | set(wb) | set(wv)
                if any(abs(wv.get(k, 0) - ((1-t)*wa.get(k, 0)+t*wb.get(k, 0))) > opt.max_weight_error for k in keys):
                    return None
            # UV coordinates on each incident side interpolate independently.
            if opt.preserve_uv:
                for removed_edge in (e for e in v.link_edges if e in es):
                    for uv in bm.loops.layers.uv.values():
                        ls = {l.vert: l for f in removed_edge.link_faces for l in f.loops}
                        if a in ls and b in ls:
                            expected = ls[a][uv].uv.lerp(ls[b][uv].uv, t)
                            actual = ls[v][uv].uv
                            span = (ls[a][uv].uv-ls[b][uv].uv).length
                            if (actual-expected).length > max(1e-5, span*0.1):
                                return None
        for edge in edges:
            fs = set(edge.link_faces)
            corners = {v for f in fs for v in f.verts if v not in vs}
            if len(corners) != 4:
                return None
            lengths = []
            for v in corners:
                adjacent = set()
                for e in v.link_edges:
                    if not any(f in fs for f in e.link_faces):
                        continue
                    w = e.other_vert(v)
                    if w in corners:
                        adjacent.add(w)
                    elif w in vs:
                        adjacent.update(e2.other_vert(w) for e2 in w.link_edges
                                        if e2.other_vert(w) in corners and e2.other_vert(w) != v
                                        and any(f in fs for f in e2.link_faces))
                if len(adjacent) != 2:
                    return None
                a, b = adjacent
                x = self.matrix.to_3x3() @ (a.co-v.co)
                y = self.matrix.to_3x3() @ (b.co-v.co)
                if min(x.length, y.length) < 1e-10:
                    return None
                angle = math.degrees(x.angle(y))
                if not opt.min_angle <= angle <= 180-opt.min_angle:
                    return None
                lengths.extend((x.length, y.length))
            if max(lengths)/min(lengths) > opt.max_aspect:
                return None
        return worst

    def step(self, count=1):
        if self.closed:
            raise ReductionError("Reducer already closed")
        for _ in range(count):
            if self.done:
                break
            if self.steps >= self.options.max_steps:
                self.stop_reason = "Maximum strip edits reached"
                self.done = True
                break
            self.bm.edges.index_update()
            self.current_quad_count = sum(len(f.verts) == 4 for f in self.bm.faces)
            tags = {e: _edge_tags(e, self.bm, self.options) for e in self.bm.edges}
            candidates = []
            for edges in _strips(self.bm, self.options.allow_open_strips):
                signature = self._signature(edges)
                if signature in self.blocked:
                    continue
                score = self._score(edges, tags)
                if score is not None and score <= 1:
                    candidates.append((score, signature, edges))
            if not candidates:
                self.stop_reason = "No more quad strips satisfy shape, UV, weight and face-quality constraints"
                self.done = True
                break
            _, signature, edges = min(candidates, key=lambda x: (x[0], x[1]))
            before = self.bm.copy()
            bmesh.ops.dissolve_edges(self.bm, edges=edges, use_verts=True, use_face_split=False)
            if not self._valid(self.bm):
                self.bm.free()
                self.bm = before
                self.blocked.add(signature)
                self.rejected += 1
                self._prepare_constraints()
            else:
                before.free()
                self.steps += 1
        return self.progress()

    def progress(self):
        return {"object": self.obj.name, "vertices": len(self.bm.verts),
                "strip_edits": self.steps, "rejected_edits": self.rejected,
                "grid_iterations": self.grid_iterations, "done": self.done,
                "stop_reason": self.stop_reason}

    def finish(self):
        if not self.done:
            raise ReductionError("Run step() until done before finish()")
        if not self._valid(self.bm):
            raise ReductionError("Final validation failed")
        layer = self.bm.verts.layers.int[INDEX_LAYER]
        self.bm.verts.index_update()
        source_indices = [v[layer] for v in self.bm.verts]
        info = _topology(self.bm)
        deviation = self._deviation(self.bm)
        mesh = self.obj.data.copy()
        mesh.name = self.obj.data.name + "_CleanQuad"
        mesh.use_fake_user = False
        try:
            self.bm.normal_update()
            self.bm.to_mesh(mesh)
            if mesh.attributes.get(INDEX_LAYER):
                mesh.attributes.remove(mesh.attributes[INDEX_LAYER])
            if mesh.attributes.get("custom_normal"):
                mesh.attributes.remove(mesh.attributes["custom_normal"])
            mesh.update()
            if self.obj.data.shape_keys:
                old_keys = self.obj.data.shape_keys.key_blocks
                new_keys = mesh.shape_keys.key_blocks if mesh.shape_keys else []
                if len(new_keys) != len(old_keys):
                    raise ReductionError("Shape-key remapping failed")
                for old_key, new_key in zip(old_keys, new_keys):
                    for new_index, source_index in enumerate(source_indices):
                        if (new_key.data[new_index].co-old_key.data[source_index].co).length > 1e-6:
                            raise ReductionError("Shape-key coordinates changed")
                animation = mesh.shape_keys.animation_data
                if animation:
                    for driver in animation.drivers:
                        for variable in driver.driver.variables:
                            for target in variable.targets:
                                if target.id == self.obj.data.shape_keys:
                                    target.id = mesh.shape_keys
            if self.options.transfer_normals:
                _transfer_normals(self.obj.data, mesh, source_indices)
            report = {"version": VERSION, "object": self.obj.name,
                      "source": self.source_info, "after_stitching": self.baseline,
                      "result": info, "deviation_world_units": deviation,
                      "strip_edits": self.steps, "rejected_edits": self.rejected,
                      "grid_iterations": self.grid_iterations, "stitched_vertices": self.welded,
                      "quad_fraction": info["face_sides"].get("4", 0)/max(info["faces"], 1),
                      "effective_quad_floor": self.quad_floor,
                      "edge_flow_preserved": self._flow_valid(self.bm),
                      "diagonal_grid_policy": "New edges follow original quad rows; odd un-subdivision disabled",
                      "contact_protection": {"overlap_pairs": self.contact_overlap_pairs,
                                             "vertices_in_band": self.contact_vertex_count,
                                             "distance": self.options.contact_distance,
                                             "error_factor": self.options.contact_factor},
                      "options": asdict(self.options), "stop_reason": self.stop_reason,
                      "warnings": self.warnings,
                      "shape_keys": "Expression vertices and their one-ring protected; all keys remapped" if self.obj.data.shape_keys else "None",
                      "export_vertices": None}
            return mesh, report
        except Exception:
            bpy.data.meshes.remove(mesh)
            raise
        finally:
            self.close()

    def close(self):
        if not self.closed:
            self.bm.free()
            self.closed = True


def _transfer_normals(source, target, indices):
    # All surviving positions are source vertices. Preserve authored corner normals
    # and choose the compatible side at a hard edge, including stitched aliases.
    lookup = defaultdict(list)
    coords = [tuple(round(c, 7) for c in v.co) for v in source.vertices]
    source_normals = source.corner_normals
    for polygon in source.polygons:
        for loop_index in polygon.loop_indices:
            index = source.loops[loop_index].vertex_index
            lookup[(coords[index], polygon.material_index)].append(source_normals[loop_index].vector.copy())
    target_normals = [n.vector.copy() for n in target.corner_normals]
    normals = [None] * len(target.loops)
    for polygon in target.polygons:
        for loop_index in polygon.loop_indices:
            index = indices[target.loops[loop_index].vertex_index]
            candidates = lookup.get((coords[index], polygon.material_index), ())
            computed = target_normals[loop_index]
            normals[loop_index] = max(candidates, key=lambda n: n.dot(computed)) if candidates else computed
    target.normals_split_custom_set(normals)


_aligned_type = None


def make_reducer(obj, options=None, context_objects=()):
    global _aligned_type
    options = (options or Options()).validate()
    if options.method == "ALIGNED":
        sharp = options.preserve_sharp and any(e.use_edge_sharp for e in obj.data.edges)
        if obj.data.shape_keys or _textured(obj) or obj.data.color_attributes or options.protect_group or options.preserve_uv or sharp:
            strict = replace(options, method="STRICT", preserve_uv=True, grid_strategy="LOOPS_ONLY")
            engine = Reducer(obj, strict, context_objects)
            engine.warnings.append("Aligned reference recipe deferred to strict quad strips for textures, UV protection, keys, colors or protection groups")
            return engine
        if _aligned_type is None:
            import importlib.util
            import sys
            path = Path(__file__).with_name("aligned.py")
            if not path.exists():
                path = Path(__file__).with_name("clean_quad_aligned.py")
            spec = importlib.util.spec_from_file_location(__name__+"_aligned", path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            _aligned_type = module.reducer_type(sys.modules[__name__])
        return _aligned_type(obj, options, context_objects)
    return Reducer(obj, options, context_objects)


def reduce_object(obj, options=None, progress=None, context_objects=()):
    engine = make_reducer(obj, options, context_objects)
    try:
        while not engine.done:
            status = engine.step()
            if progress:
                progress(status)
        return engine.finish()
    finally:
        engine.close()


def remove_mesh(mesh):
    if mesh and mesh.users == 0:
        bpy.data.meshes.remove(mesh)


def mesh_signature(obj):
    """Detect edits between preview and apply, including keys, skin and materials."""
    mesh = obj.data
    digest = hashlib.sha256()
    for collection, prop, width, typecode in ((mesh.vertices, "co", 3, "f"),
            (mesh.edges, "vertices", 2, "i"), (mesh.loops, "vertex_index", 1, "i"),
            (mesh.polygons, "loop_total", 1, "i"), (mesh.polygons, "material_index", 1, "i")):
        values = array(typecode, [0]) * (len(collection)*width)
        collection.foreach_get(prop, values)
        digest.update(values.tobytes())
    for uv in mesh.uv_layers:
        digest.update(uv.name.encode())
        values = array("f", [0]) * (len(uv.data)*2)
        uv.data.foreach_get("uv", values)
        digest.update(values.tobytes())
    for vertex in mesh.vertices:
        digest.update(repr([(g.group, g.weight) for g in vertex.groups]).encode())
    if mesh.shape_keys:
        for key in mesh.shape_keys.key_blocks:
            values = array("f", [0]) * (len(key.data)*3)
            key.data.foreach_get("co", values)
            digest.update(key.name.encode())
            digest.update(values.tobytes())
    digest.update(repr([g.name for g in obj.vertex_groups]).encode())
    digest.update(repr([m.name if m else None for m in mesh.materials]).encode())
    return digest.hexdigest()


def create_preview(context, source, mesh, report):
    preview = source.copy()
    preview.data = mesh
    preview.name = source.name + "_CQR_Preview"
    collection = source.users_collection[0] if source.users_collection else context.scene.collection
    collection.objects.link(preview)
    preview["cqr_preview"] = True
    preview["cqr_source_name"] = source.name
    preview["cqr_source_id"] = source
    preview["cqr_source_signature"] = mesh_signature(source)
    preview["cqr_report"] = json.dumps(report, ensure_ascii=False)
    preview["cqr_source_hidden"] = source.hide_get(view_layer=context.view_layer)
    # Preview alone is not a render/export commit.
    preview.hide_render = True
    preview.hide_set(False)
    source.hide_set(True)
    return preview


def source_for_preview(preview):
    if not preview.get("cqr_preview"):
        return None
    pointer = preview.get("cqr_source_id")
    if pointer and isinstance(pointer, bpy.types.Object):
        return pointer
    return bpy.data.objects.get(preview.get("cqr_source_name", ""))


def validate_preview(preview):
    source = source_for_preview(preview)
    if source is None:
        raise ReductionError("Preview source no longer exists; retain the preview and recover the source")
    if source.mode != "OBJECT" or mesh_signature(source) != preview.get("cqr_source_signature"):
        raise ReductionError(f"{source.name}: source changed after preview; discard and regenerate")
    return source


def discard_preview(context, preview):
    source = source_for_preview(preview)
    if source:
        source.hide_set(bool(preview.get("cqr_source_hidden", False)), view_layer=context.view_layer)
    mesh = preview.data
    bpy.data.objects.remove(preview, do_unlink=True)
    remove_mesh(mesh)
    return source


def accept_preview(context, preview):
    source = validate_preview(preview)
    collection = bpy.data.collections.get("CQR_Backups")
    if collection is None:
        collection = bpy.data.collections.new("CQR_Backups")
        context.scene.collection.children.link(collection)
    backup = source.copy()
    backup.name = source.name + "_CQR_Backup"
    collection.objects.link(backup)
    backup["cqr_backup"] = True
    backup["cqr_original_name"] = source.name
    backup.hide_render = True
    backup.hide_set(True)
    # Keep the original object's name, modifiers, groups and VRM bindings.
    source.data = preview.data
    source["cqr_report"] = preview["cqr_report"]
    source.hide_set(bool(preview.get("cqr_source_hidden", False)))
    bpy.data.objects.remove(preview, do_unlink=True)
    return source, backup


def inspect_vrm(path):
    with open(path, "rb") as handle:
        header = handle.read(12)
        if len(header) != 12:
            raise ReductionError("Not a GLB/VRM file")
        magic, version, total = struct.unpack("<4sII", header)
        if magic != b"glTF" or version != 2 or total != Path(path).stat().st_size:
            raise ReductionError("Invalid GLB header")
        doc = None
        while handle.tell() < total:
            chunk_header = handle.read(8)
            if len(chunk_header) != 8:
                raise ReductionError("Truncated GLB chunk")
            size, kind = struct.unpack("<II", chunk_header)
            if handle.tell()+size > total:
                raise ReductionError("Invalid GLB chunk length")
            if kind == 0x4E4F534A:
                doc = json.loads(handle.read(size))
            else:
                handle.seek(size, 1)
        if doc is None:
            raise ReductionError("GLB JSON chunk missing")
    extensions = doc.get("extensions", {})
    if not any(k in extensions for k in ("VRM", "VRMC_vrm")):
        raise ReductionError("File contains no VRM extension")
    accessors = doc.get("accessors", [])
    primitives = [p for m in doc.get("meshes", []) for p in m.get("primitives", [])]
    # Count each POSITION accessor once: shared attribute buffers are not duplicates.
    positions = {p["attributes"]["POSITION"] for p in primitives}
    vertices = sum(accessors[i]["count"] for i in positions)
    triangles = 0
    for p in primitives:
        count = accessors[p["indices"]]["count"] if "indices" in p else accessors[p["attributes"]["POSITION"]]["count"]
        mode = p.get("mode", 4)
        triangles += count//3 if mode == 4 else max(0, count-2) if mode in (5, 6) else 0
    return {"path": str(Path(path).resolve()), "vertices": vertices, "triangles": triangles,
            "skins": len(doc.get("skins", [])), "bytes": total,
            "vrm_version": "1.0" if "VRMC_vrm" in extensions else "0.x"}


def export_vrm(context, objects, path):
    try:
        bpy.ops.export_scene.vrm.get_rna_type()
    except (AttributeError, RuntimeError, KeyError):
        raise ReductionError("VRM Add-on for Blender is required for VRM export; reduction does not require it")
    if any(o.get("cqr_preview") for o in context.scene.objects):
        raise ReductionError("Accept or discard previews before exporting")
    meshes = [o for o in objects if o.type == "MESH" and not o.get("cqr_backup")]
    arms = {m.object for o in meshes for m in o.modifiers if m.type == "ARMATURE" and m.object}
    if len(arms) != 1:
        raise ReductionError("Select meshes bound to exactly one armature for VRM export")
    context.view_layer.update()
    selected, active = list(context.selected_objects), context.view_layer.objects.active
    try:
        for o in context.selected_objects:
            o.select_set(False)
        for o in [*meshes, *arms]:
            o.select_set(True)
        arm = next(iter(arms))
        context.view_layer.objects.active = arm
        context.view_layer.update()
        targets = [*meshes, arm]
        if any(not o.visible_get() for o in targets):
            raise ReductionError("Every export mesh and its armature must be visible in this view layer")
        # Add-on initialization and background contexts can invalidate cached
        # selected_objects. Supply the intended selection explicitly as well.
        for o in targets:
            o.select_set(True)
        with context.temp_override(selected_objects=targets, selected_editable_objects=targets,
                                   active_object=arm, object=arm):
            status = bpy.ops.export_scene.vrm(filepath=str(path), armature_object_name=arm.name,
                                             export_invisibles=False, export_only_selections=True)
        if "FINISHED" not in status or not Path(path).is_file():
            raise ReductionError("VRM exporter did not finish; review its validation messages")
        result = inspect_vrm(path)
        if result["vertices"] == 0 or result["skins"] == 0:
            raise ReductionError("VRM exporter returned an empty or unskinned avatar; output is not valid for this job")
        return result
    finally:
        for o in context.selected_objects:
            o.select_set(False)
        for o in selected:
            if o.name in context.view_layer.objects:
                o.select_set(True)
        context.view_layer.objects.active = active
