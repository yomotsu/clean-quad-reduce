import bpy, bmesh, math, time
from mathutils.bvhtree import BVHTree
exec(compile(open('/tmp/rigodotify_quad_reduce.py').read(), '/tmp/rigodotify_quad_reduce.py', 'exec'))

def protect_contacts(states):
    names=list(states)
    originals={n:bpy.data.meshes[n+'_ORIGINAL'] for n in names}
    coords={n:[bpy.data.objects[n].matrix_world @ v.co for v in originals[n].vertices] for n in names}
    polys={n:[tuple(p.vertices) for p in originals[n].polygons] for n in names}
    trees={n:BVHTree.FromPolygons(coords[n],polys[n],all_triangles=True) for n in names}
    overlaps=trees[names[0]].overlap(trees[names[1]])
    stats={}
    for i,n in enumerate(names):
        hit_faces={pair[i] for pair in overlaps}
        contact_tree=BVHTree.FromPolygons(coords[n],[polys[n][j] for j in hit_faces],all_triangles=True)
        s=states[n];bm=s['bm'];matrix=bpy.data.objects[n].matrix_world
        protected=set()
        for v in bm.verts:
            if contact_tree.find_nearest(matrix @ v.co,0.006)[0] is not None:
                protected.add(v)
        # One additional ring makes transitions to the retained contact bands gradual.
        protected.update({e.other_vert(v) for v in list(protected) for e in v.link_edges})
        s['protected']=protected
        for v in protected:s['scales'][v]*=0.15
        s['target']=2600 if n=='Beta_Joints' else 5800
        stats[n]={'crossing_faces':len(hit_faces),'protected_vertices':len(protected),'start_vertices':len(bm.verts)}
    return {'overlap_pairs':len(overlaps),'objects':stats}

def advance_contact(state,seconds=8):
    bm=state['bm'];start=time.monotonic();done=0;reason='time';cost=None
    while time.monotonic()-start < seconds:
        if len(bm.verts)<=state['target']:
            reason='target';break
        candidates=[]
        for es in open_and_closed_loops(bm):
            score=loop_score(es,state['scales'])
            if score is not None and score<=0.7:candidates.append((score,es))
        if not candidates:
            reason='no_safe_loops';break
        cost,es=min(candidates,key=lambda x:x[0])
        bmesh.ops.dissolve_edges(bm,edges=es,use_verts=True,use_face_split=False)
        state['removed_loops']+=1;done+=1
    return {'name':state['name'],'vertices':len(bm.verts),'faces':len(bm.faces),'batch_loops':done,'total_loops':state['removed_loops'],'reason':reason,'last_cost':cost}
