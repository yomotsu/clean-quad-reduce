import bpy,math
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

def restore_reference_normals(name):
    obj=bpy.data.objects[name]
    mesh=obj.data
    # Preserve the previous editable version inside the new blend too.
    mesh.use_fake_user=True
    mesh=mesh.copy()
    mesh.name=name+'_CleanQuadContactNormals'
    obj.data=mesh
    src=bpy.data.meshes[name+'_ORIGINAL']
    src.calc_loop_triangles()
    triangles=list(src.loop_triangles)
    coords=[v.co.copy() for v in src.vertices]
    source_normals=[n.vector.copy() for n in src.corner_normals]
    tree=BVHTree.FromPolygons(coords,[tuple(t.vertices) for t in triangles],all_triangles=True)
    old=[n.vector.copy() for n in mesh.corner_normals]
    normals=[None]*len(mesh.loops)
    correction_angles=[]
    fallbacks=0
    for poly in mesh.polygons:
        center=poly.center.copy()
        for li in poly.loop_indices:
            v=mesh.vertices[mesh.loops[li].vertex_index].co
            # Sample just inside the face so corners preserve authored splits.
            point=v.lerp(center,0.001)
            hit,_,idx,distance=tree.find_nearest(point)
            tri=triangles[idx]
            if tri.normal.dot(poly.normal)<0.15:
                candidates=tree.find_nearest_range(point,distance+0.001)
                candidates=[h for h in candidates if triangles[h[2]].normal.dot(poly.normal)>0.15]
                if candidates:
                    hit,_,idx,distance=min(candidates,key=lambda h:h[3]);tri=triangles[idx]
            normal=barycentric_transform(hit,*[coords[j] for j in tri.vertices],*[source_normals[j] for j in tri.loops])
            if normal.length_squared<1e-12 or normal.dot(poly.normal)<=0:
                normal=poly.normal.copy();fallbacks+=1
            else:normal.normalize()
            normals[li]=tuple(normal)
            correction_angles.append(math.degrees(old[li].angle(normal,0.0)))
    mesh.normals_split_custom_set(normals)
    mesh.update()
    actual=[n.vector.copy() for n in mesh.corner_normals]
    angles=sorted(correction_angles)
    return {'object':name,'vertices':len(mesh.vertices),'faces':len(mesh.polygons),'updated_corners_over_1deg':sum(a>1 for a in angles),'median_correction_deg':round(angles[len(angles)//2],2),'max_correction_deg':round(max(angles),2),'fallback_corners':fallbacks,'invalid_normals':sum(not all(math.isfinite(c) for c in n) or n.length<0.99 for n in actual),'normals_opposed_to_face':sum(actual[li].dot(p.normal)<=0 for p in mesh.polygons for li in p.loop_indices)}

result={'normal_adjustments':[restore_reference_normals(n) for n in ['Beta_Joints','Beta_Surface']]}
bpy.context.view_layer.update()
