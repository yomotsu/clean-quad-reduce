import bpy, bmesh, math
from mathutils.geometry import intersect_point_line

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

def initialize(name, joint=False):
    old=bpy.data.meshes[name+'_ORIGINAL'];bm=bmesh.new();bm.from_mesh(old)
    bmesh.ops.remove_doubles(bm,verts=[v for v in bm.verts if v.is_boundary],dist=1e-6)
    bmesh.ops.join_triangles(bm,faces=list(bm.faces),cmp_uvs=True,cmp_materials=True,angle_face_threshold=math.radians(60),angle_shape_threshold=math.radians(90),topology_influence=1.0)
    if joint:
        bmesh.ops.unsubdivide(bm,verts=list(bm.verts),iterations=3)
        bmesh.ops.join_triangles(bm,faces=[f for f in bm.faces if len(f.verts)==3],cmp_uvs=True,cmp_materials=True,angle_face_threshold=math.radians(60),angle_shape_threshold=math.radians(60))
    seen=set(); scales={}
    for v in bm.verts:
        if v in seen:continue
        stack=[v];seen.add(v);part=[]
        while stack:
            a=stack.pop();part.append(a)
            for e in a.link_edges:
                w=e.other_vert(a)
                if w not in seen:seen.add(w);stack.append(w)
        diag=math.sqrt(sum((max(v.co[i] for v in part)-min(v.co[i] for v in part))**2 for i in range(3)))
        for v in part:scales[v]=min(0.008,max(0.0003,diag*0.025))
    return {'bm':bm,'scales':scales,'removed_loops':0,'name':name}

def loop_score(edges,scales):
    es=set(edges);vs={v for e in edges for v in e.verts};worst=0
    for v in vs:
        rest=[e.other_vert(v) for e in v.link_edges if e not in es]
        if len(rest)!=2:return None
        a,b=rest; delta=b.co-a.co
        if delta.length<1e-9:return None
        t=(v.co-a.co).dot(delta)/delta.length_squared
        if t<0.12 or t>0.88:return None
        err=(v.co-(a.co+delta*t)).length/scales[v]
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
            x=ns[0].co-v.co;y=ns[1].co-v.co
            if min(x.length,y.length)<1e-8:return None
            angle=x.angle(y)
            if angle<math.radians(18) or angle>math.radians(162):return None
            lens.extend([x.length,y.length])
        if max(lens)/min(lens)>6:return None
    return worst

def advance(state,count=30):
    bm=state['bm'];done=0;cost=None
    for _ in range(count):
        candidates=[]
        for es in closed_loops(bm):
            score=loop_score(es,state['scales'])
            if score is not None and score<=1.0:candidates.append((score,es))
        if not candidates:break
        cost,es=min(candidates,key=lambda x:x[0])
        bmesh.ops.dissolve_edges(bm,edges=es,use_verts=True,use_face_split=False)
        done+=1;state['removed_loops']+=1
    return {'name':state['name'],'vertices':len(bm.verts),'faces':len(bm.faces),'batch_loops':done,'total_loops':state['removed_loops'],'last_cost':cost}

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
