import bpy,bmesh,math,time
exec(compile(open('/tmp/rigodotify_contact_reduce.py').read(),'/tmp/rigodotify_contact_reduce.py','exec'))

def prepare():
    states={n:initialize(n,False) for n in ['Beta_Joints','Beta_Surface']}
    protection=protect_contacts(states)
    for n,s in states.items():
        # Restores the component-specific budget, with half the ordinary error in contact bands.
        for v in s['protected']:s['scales'][v]*=0.5/0.15
        s['target']=3024 if n=='Beta_Joints' else 6274
    return states,protection

def advance_aligned(s,seconds=10):
    bm=s['bm'];start=time.monotonic();removed=0;passes=0;reason='time';cost=None
    while time.monotonic()-start<seconds:
        if len(bm.verts)<=s['target']:reason='target';break
        candidates=[]
        for es in open_and_closed_loops(bm):
            score=loop_score(es,s['scales'])
            if score is not None and score<=s.get('budget',0.9):candidates.append((score,es))
        if not candidates:reason='no_safe_loops';break
        candidates.sort(key=lambda p:p[0]);locked=set();done=0
        # Disjoint strips can be removed in the same pass without changing each other's scores.
        for score,es in candidates:
            if len(bm.verts)<=s['target']:break
            if not all(e.is_valid for e in es):continue
            vs={v for e in es for v in e.verts}
            surrounding={v for e in es for f in e.link_faces for v in f.verts}
            if surrounding & locked:continue
            locked.update(surrounding)
            bmesh.ops.dissolve_edges(bm,edges=es,use_verts=True,use_face_split=False)
            cost=score;done+=1;removed+=1;s['removed_loops']+=1
        passes+=1
        if not done:reason='no_safe_loops';break
    return {'name':s['name'],'verts':len(bm.verts),'faces':len(bm.faces),'quads':sum(len(f.verts)==4 for f in bm.faces),'passes':passes,'removed_loops':removed,'reason':reason,'last_cost':cost}
