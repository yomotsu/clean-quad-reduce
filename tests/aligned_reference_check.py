"""Rebuild the successful aligned-workspace recipe from its embedded originals."""
import json
from pathlib import Path
import sys
import time

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
from clean_quad_reduce import core

if bpy.context.mode != "OBJECT":
    bpy.ops.object.mode_set(mode="OBJECT")
sources = [bpy.data.objects[n] for n in ("Beta_Surface", "Beta_Joints")]
gold = {o.name: o.data for o in sources}
for obj in sources:
    obj.data = bpy.data.meshes[obj.name+"_ORIGINAL"]
out = ROOT / "test-output"
out.mkdir(exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(out/"aligned-source.blend"), copy=True)
prepared, reports = [], []
start = time.monotonic()
for obj in sources:
    options = core.preset("ALIGNED")
    engine = core.make_reducer(obj, options, sources)
    try:
        print("ALIGNED_START", obj.name, engine.progress(), flush=True)
        while not engine.done:
            status = engine.step()
            print("ALIGNED_PROGRESS", status, flush=True)
        mesh, report = engine.finish()
        expected = gold[obj.name]
        expected_coords = {tuple(round(c, 7) for c in v.co) for v in expected.vertices}
        coords = {tuple(round(c, 7) for c in v.co) for v in mesh.vertices}
        report["reference_comparison"] = {"expected_vertices": len(expected.vertices),
            "actual_vertices": len(mesh.vertices), "coordinate_set_matches": coords == expected_coords,
            "different_positions": len(coords.symmetric_difference(expected_coords))}
        assert len(mesh.vertices) == len(expected.vertices), report["reference_comparison"]
        assert coords == expected_coords, report["reference_comparison"]
        def faces(m):
            out = []
            for p in m.polygons:
                points = [tuple(round(c, 7) for c in m.vertices[i].co) for i in p.vertices]
                rotations = [tuple(points[i:]+points[:i]) for i in range(len(points))]
                out.append(min(rotations))
            return sorted(out)
        assert faces(mesh) == faces(expected), "Face connectivity / winding differs from reference"
        report["reference_comparison"]["face_connectivity_matches"] = True
        print("ALIGNED_RESULT", obj.name, report["reference_comparison"], flush=True)
        prepared.append((obj, mesh, report)); reports.append(report)
    finally:
        engine.close()
for obj, mesh, report in prepared:
    preview = core.create_preview(bpy.context, obj, mesh, report)
    core.accept_preview(bpy.context, preview)
bpy.ops.preferences.addon_enable(module="bl_ext.user_default.vrm")
vrm = core.export_vrm(bpy.context, sources, out/"aligned-reference-rebuilt.vrm")
assert vrm["vertices"] == 11647 and vrm["triangles"] == 19044, vrm
result = {"meshes": reports, "vrm": vrm, "seconds": time.monotonic()-start}
(out/"aligned-reference-report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
bpy.ops.wm.save_as_mainfile(filepath=str(out/"aligned-reference-rebuilt.blend"))
print("ALIGNED_FINAL", json.dumps({"vrm": vrm, "seconds": result["seconds"]}), flush=True)
