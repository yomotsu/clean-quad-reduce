"""Behavioral checks run in a factory-startup background Blender process."""
import json
from pathlib import Path
import runpy
import sys

import bpy
import bmesh
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
import clean_quad_reduce as addon
from clean_quad_reduce import core

checks = []


def check(name, condition):
    if not condition:
        raise AssertionError(name)
    checks.append(name)
    print("PASS", name, flush=True)


def cylinder(name, rings=9, count=16, expression=False):
    import math
    verts = [(math.cos(i*2*math.pi/count), math.sin(i*2*math.pi/count), j*0.2)
             for j in range(rings) for i in range(count)]
    faces = [(j*count+i, j*count+(i+1)%count, (j+1)*count+(i+1)%count, (j+1)*count+i)
             for j in range(rings-1) for i in range(count)]
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    group = obj.vertex_groups.new(name="Bone")
    group.add(list(range(len(verts))), 1, "REPLACE")
    uv = mesh.uv_layers.new(name="UVMap")
    for p in mesh.polygons:
        row, column = divmod(p.index, count)
        coords = [(column/count, row/(rings-1)), ((column+1)/count, row/(rings-1)),
                  ((column+1)/count, (row+1)/(rings-1)), (column/count, (row+1)/(rings-1))]
        for li, co in zip(p.loop_indices, coords):
            uv.data[li].uv = co
        p.use_smooth = True
    if expression:
        obj.shape_key_add(name="Basis")
        key = obj.shape_key_add(name="Smile")
        key.data[3*count+2].co.x += 0.03
        key.value = 0.25
        key.slider_min = -1
        key.slider_max = 1.5
        key.vertex_group = "Bone"
        key.driver_add("value").driver.expression = "0.25"
        variable = key.driver_add("value").driver.variables.new()
        variable.type = "SINGLE_PROP"
        variable.targets[0].id_type = "KEY"
        variable.targets[0].id = mesh.shape_keys
        variable.targets[0].data_path = 'key_blocks["Basis"].value'
    return obj


def boundary(mesh):
    import bmesh
    bm = bmesh.new(); bm.from_mesh(mesh)
    result = sorted(tuple(v.co) for v in bm.verts if v.is_boundary)
    bm.free()
    return result


def select(obj):
    for o in bpy.context.selected_objects:
        o.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


opt = core.Options(max_error=0.04, relative_error=0.1, max_steps=100,
                   grid_strategy="LOOPS_ONLY", max_aspect=8)
obj = cylinder("Cylinder")
old = obj.data
coordinates = [v.co.copy() for v in old.vertices]
mesh, report = core.reduce_object(obj, opt)
check("quad strips actually remove vertices", len(mesh.vertices) < len(old.vertices)*0.7)
check("source datablock unchanged", obj.data == old and all(a == b.co for a, b in zip(coordinates, old.vertices)))
check("all result faces are quads", all(len(p.vertices) == 4 for p in mesh.polygons))
check("report confirms original row directions", report["edge_flow_preserved"] and report["grid_iterations"] % 2 == 0)
check("no cylinder edge becomes diagonal", all(
    abs(mesh.vertices[e.vertices[0]].co.z-mesh.vertices[e.vertices[1]].co.z) < 1e-6
    or (Vector(mesh.vertices[e.vertices[0]].co[:2])-Vector(mesh.vertices[e.vertices[1]].co[:2])).length < 1e-6
    for e in mesh.edges))
check("open boundaries keep their exact positions", boundary(mesh) == boundary(old))
check("UV layer and weights survive", len(mesh.uv_layers) == 1 and all(v.groups and abs(v.groups[0].weight-1)<1e-6 for v in mesh.vertices))
check("sampled global shape bound is enforced", report["deviation_world_units"]["max"] <= opt.max_error*1.0001)
check("mesh has no new structural defects", report["result"]["bad_edges"] == 0 and report["result"]["degenerate_faces"] == 0)
preview = core.create_preview(bpy.context, obj, mesh, report)
check("preview hides source only in viewport", obj.hide_get() and not obj.hide_render and preview.hide_render)
mesh_name = mesh.name
core.discard_preview(bpy.context, preview)
check("discard restores source and removes result", not obj.hide_get() and obj.data == old and mesh_name not in bpy.data.meshes)

key_obj = cylinder("Keyed", rings=13, expression=True)
key_source = key_obj.data
keys_before = {k.name: [v.co.copy() for v in k.data] for k in key_source.shape_keys.key_blocks}
key_mesh, key_report = core.reduce_object(key_obj, opt)
check("keyed mesh can still reduce unaffected areas", len(key_mesh.vertices) < len(key_source.vertices))
check("all shape keys and their properties survive", list(key_mesh.shape_keys.key_blocks.keys()) == ["Basis", "Smile"] and key_mesh.shape_keys.key_blocks[1].slider_min == -1 and key_mesh.shape_keys.key_blocks[1].vertex_group == "Bone")
check("shape-key animation survives", bool(key_mesh.shape_keys.animation_data.drivers))
check("shape-key self-driver targets are remapped", key_mesh.shape_keys.animation_data.drivers[0].driver.variables[0].targets[0].id == key_mesh.shape_keys)
for key in key_mesh.shape_keys.key_blocks:
    source_key = key_source.shape_keys.key_blocks[key.name]
    for v in key_mesh.vertices:
        source_index = min(range(len(key_source.vertices)), key=lambda i: (key_source.vertices[i].co-v.co).length)
        check_name = (key.data[v.index].co-source_key.data[source_index].co).length < 1e-6
        if not check_name:
            raise AssertionError("shape-key remap coordinate")
check("every surviving shape-key coordinate maps to source", True)
preview = core.create_preview(bpy.context, key_obj, key_mesh, key_report)
source, backup = core.accept_preview(bpy.context, preview)
check("apply retains original object identity and name", source == key_obj and source.name == "Keyed" and source.data == key_mesh)
check("apply keeps hidden source backup", backup.data == key_source and backup.hide_get() and backup.hide_render)

stale = cylinder("Stale")
stale_mesh, stale_report = core.reduce_object(stale, opt)
stale_preview = core.create_preview(bpy.context, stale, stale_mesh, stale_report)
stale.data.vertices[0].co.x += 0.01
try:
    core.accept_preview(bpy.context, stale_preview)
except core.ReductionError:
    check("apply rejects source edits made after preview", True)
else:
    raise AssertionError("stale preview overwrote source edits")
core.discard_preview(bpy.context, stale_preview)

bpy.ops.mesh.primitive_torus_add(major_segments=16, minor_segments=8, major_radius=1, minor_radius=0.3)
torus = bpy.context.object
engine = core.Reducer(torus, core.preset("MINIMAL", max_error=0.15, relative_error=0.2))
try:
    check("automatic grid passes use even counts only", engine.grid_iterations in (0, 2, 4))
    base = bmesh.new(); base.from_mesh(torus.data)
    index_layer = base.verts.layers.int.new(core.INDEX_LAYER)
    for v in base.verts:
        v[index_layer] = v.index
    bmesh.ops.unsubdivide(base, verts=list(base.verts), iterations=1)
    check("diamond grid is rejected by original-flow validator", not engine._flow_valid(base))
    base.free()
    while not engine.done:
        engine.step()
    torus_mesh, torus_report = engine.finish()
    check("torus reduction keeps the original row directions", torus_report["edge_flow_preserved"])
    check("regular grid really reduces", len(torus_mesh.vertices) < len(torus.data.vertices))
    core.remove_mesh(torus_mesh)
finally:
    engine.close()

protected = cylinder("Protected")
group = protected.vertex_groups.new(name="Keep")
group.add(list(range(len(protected.data.vertices))), 1, "REPLACE")
mesh, report = core.reduce_object(protected, core.preset("MINIMAL", protect_group="Keep"))
check("protection group prevents all removal", len(mesh.vertices) == len(protected.data.vertices))
core.remove_mesh(mesh)

nonlinear = cylinder("NonlinearUV")
material = bpy.data.materials.new("TextureTest")
material.use_nodes = True
material.node_tree.nodes.new("ShaderNodeTexImage").image = bpy.data.images.new("CQR test texture", width=4, height=4)
nonlinear.data.materials.append(material)
for d in nonlinear.data.uv_layers.active.data:
    d.uv.y = d.uv.y**4
mesh, report = core.reduce_object(nonlinear, opt)
check("nonlinear texture UVs prevent destructive strip merges", len(mesh.vertices) == len(nonlinear.data.vertices))
core.remove_mesh(mesh)
engine = core.make_reducer(nonlinear, core.preset("ALIGNED"))
try:
    check("aligned mode protects textures through strict fallback", type(engine) == core.Reducer and engine.options.preserve_uv)
finally:
    engine.close()

bpy.ops.mesh.primitive_uv_sphere_add(segments=20, ring_count=20, radius=0.05)
sphere = bpy.context.object
for p in sphere.data.polygons:
    p.use_smooth = True
engine = core.make_reducer(sphere, core.preset("ALIGNED"))
try:
    while not engine.done:
        engine.step()
    mesh, report = engine.finish()
    check("aligned sphere keeps latitude-longitude grid", len(mesh.vertices) == 182 and report["sphere_components"][0]["longitudes"] == 20)
    check("aligned mode uses no checkerboard un-subdivision", report["grid_iterations"] == 0 and report["edge_flow_preserved"])
    check("reference normal projection is finite", report["reference_normals"]["invalid_normals"] == 0)
    core.remove_mesh(mesh)
finally:
    engine.close()

contact_a = cylinder("ContactA")
contact_b = cylinder("ContactB")
contact_b.location.x = 1
bpy.context.view_layer.update()
engine = core.Reducer(contact_a, opt, [contact_a, contact_b])
try:
    check("contact intersections and one-ring band detected", engine.contact_overlap_pairs > 0 and engine.contact_vertex_count > 0)
finally:
    engine.close()

scaled = cylinder("Scaled")
scaled.scale = (1, 2, 3)
bpy.context.view_layer.update()
mesh, report = core.reduce_object(scaled, opt)
check("nonuniform scale is evaluated in world space", report["deviation_world_units"]["max"] <= opt.max_error*1.0001)
core.remove_mesh(mesh)

# Independent skill entry point, with no registered reduction add-on.
api = runpy.run_path(str(ROOT / "skill/blender-clean-quad-reduce/scripts/reduce_mesh.py"))
analysis = api["run_job"]({"action": "analyze", "objects": [obj.name]})
check("standalone skill analyzes without registered addon", analysis["ok"] and not hasattr(bpy.types.Scene, "cqr"))
before = set(bpy.data.objects)
try:
    api["run_job"]({"action": "reduce", "objects": [obj.name], "settings": {"protect_group": "Missing"}})
except core.ReductionError:
    pass
except Exception as exc:
    check("skill reports invalid input", "Missing" in str(exc))
else:
    raise AssertionError("missing protection group accepted")
check("failed job creates no scene objects", set(bpy.data.objects) == before)

# UI registration and operator lifecycle in a fresh background session.
addon.register()
select(obj)
check("addon analysis operator works", "FINISHED" in bpy.ops.cqr.analyze())
s = bpy.context.scene.cqr
s.grid_strategy = "LOOPS_ONLY"; s.max_error = 0.04; s.max_aspect = 8
check("addon preview operator works", "FINISHED" in bpy.ops.cqr.preview())
check("addon preview leaves original mesh untouched", obj.data == old)
check("addon revert operator works", "FINISHED" in bpy.ops.cqr.revert() and not obj.hide_get())
select(obj)
check("addon second preview works", "FINISHED" in bpy.ops.cqr.preview())
check("addon apply operator works", "FINISHED" in bpy.ops.cqr.accept())
check("addon apply reduces the original object", len(obj.data.vertices) < len(old.vertices) and obj.name == "Cylinder")
addon.unregister()
check("addon unregister cleans RNA properties", not hasattr(bpy.types.Scene, "cqr"))

bad = ROOT / "test-output/not-a-vrm.bin"
bad.parent.mkdir(exist_ok=True)
bad.write_bytes(b"not a vrm")
try:
    core.inspect_vrm(bad)
except core.ReductionError:
    check("VRM inspector rejects invalid files", True)
else:
    raise AssertionError("invalid VRM accepted")

print("CQR_TEST_RESULTS", json.dumps({"passed": len(checks), "checks": checks}), flush=True)

# Fixture for a separate, real CLI invocation of the standalone skill package.
standalone = cylinder("StandaloneCylinder")
fixture = ROOT / "test-output/standalone-input.blend"
job = {"action": "reduce", "objects": [standalone.name], "preset": "DETAIL",
       "settings": {"max_error": 0.04, "max_aspect": 8}, "apply": True,
       "output_blend": str(ROOT / "test-output/standalone-output.blend"),
       "report": str(ROOT / "test-output/standalone-report.json"), "overwrite": True}
(ROOT / "test-output/standalone-job.json").write_text(json.dumps(job), encoding="utf-8")
bpy.ops.wm.save_as_mainfile(filepath=str(fixture))
