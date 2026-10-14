import bpy,bmesh
from collections import deque
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform,closest_point_on_tri

def build_aligned_joints():
    ns=bpy.app.driver_namespace['aligned_module']
    s=ns['initialize']('Beta_Joints',False);bm=s['bm']
    bm.verts.ensure_lookup_table();bm.verts.index_update()
    seen=set();parts=[]
    for v in bm.verts:
        if v in seen:continue
        part=[];stack=[v];seen.add(v)
        while stack:
            a=stack.pop();part.append(a)
            for e in a.link_edges:
                w=e.other_vert(a)
                if w not in seen:seen.add(w);stack.append(w)
        parts.append(part)
    new_faces=[];keep=set();stats=[]
    for part in parts:
        poles=sorted(part,key=lambda v:len(v.link_edges),reverse=True)[:2]
        assert all(len(v.link_edges)>6 for v in poles),'Unexpected sphere topology'
        first,last=sorted(poles,key=lambda v:v.co.z)
        dist={first:0};q=deque([first])
        while q:
            v=q.popleft()
            for e in v.link_edges:
                w=e.other_vert(v)
                if w not in dist:dist[w]=dist[v]+1;q.append(w)
        level1={v for v in part if dist[v]==1}
        current=min(level1,key=lambda v:v.index);ring=[current];prev=None
        while len(ring)<len(level1):
            ns1=[e.other_vert(current) for e in current.link_edges if e.other_vert(current) in level1 and e.other_vert(current)!=prev and e.other_vert(current) not in ring]
            assert ns1,'Ring ordering failed'
            nxt=min(ns1,key=lambda v:v.index);prev,current=current,nxt;ring.append(current)
        rings=[ring]
        for level in range(2,dist[last]):
            next_ring=[]
            for v in rings[-1]:
                choices=[e.other_vert(v) for e in v.link_edges if dist[e.other_vert(v)]==level]
                assert len(choices)==1,'Longitude ordering failed'
                next_ring.append(choices[0])
            rings.append(next_ring)
        size=max(max(v.co[i] for v in part)-min(v.co[i] for v in part) for i in range(3))
        if len(part)==382 and size>=0.075:
            selected_rings=[rings[i] for i in range(1,len(rings),2)];step=1
        elif len(part)==382:
            selected_rings=[rings[i] for i in [2,5,9,13,16]];step=2
        else:
            assert len(part)==134
            selected_rings=[rings[i] for i in [2,5,8]];step=2
        selected_rings=[r[::step] for r in selected_rings]
        count=len(selected_rings[0]);center=sum((v.co for v in part),Vector())/len(part)
        def add_face(vs):
            points=[v.co for v in vs]
            normal=(points[1]-points[0]).cross(points[2]-points[0])
            c=sum(points,Vector())/len(points)
            if normal.dot(c-center)<0:vs=list(reversed(vs))
            new_faces.append(vs);keep.update(vs)
        for j in range(count):
            k=(j+1)%count
            add_face([first,selected_rings[0][j],selected_rings[0][k]])
            for a,b in zip(selected_rings,selected_rings[1:]):add_face([a[j],b[j],b[k],a[k]])
            add_face([last,selected_rings[-1][k],selected_rings[-1][j]])
        stats.append({'source':len(part),'vertices':count*len(selected_rings)+2,'longitudes':count,'latitudes':len(selected_rings)})
    for f in list(bm.faces):bm.faces.remove(f)
    for e in list(bm.edges):bm.edges.remove(e)
    for v in list(bm.verts):
        if v not in keep:bm.verts.remove(v)
    for vs in new_faces:bm.faces.new(vs)
    bm.normal_update()
    return s,stats

def transfer_uvs(bm,src):
    src.calc_loop_triangles();tris=list(src.loop_triangles);coords=[v.co.copy() for v in src.vertices]
    tree=BVHTree.FromPolygons(coords,[tuple(t.vertices) for t in tris],all_triangles=True)
    uv=bm.loops.layers.uv.verify();srcuv=src.uv_layers.active.data
    for f in bm.faces:
        center=f.calc_center_median()
        for l in f.loops:
            hit,_,idx,_=tree.find_nearest(l.vert.co.lerp(center,0.001));t=tris[idx]
            hit=closest_point_on_tri(l.vert.co,*[coords[j] for j in t.vertices])
            val=barycentric_transform(hit,*[coords[j] for j in t.vertices],*[Vector((srcuv[j].uv.x,srcuv[j].uv.y,0)) for j in t.loops])
            l[uv].uv=(round(val.x,6),round(val.y,6))
