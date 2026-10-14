"""Run against the supplied original avatar in an isolated Blender process."""
import json
from pathlib import Path
import sys
import time

import bpy

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "addon"))
from clean_quad_reduce import core

out = ROOT / "test-output"
out.mkdir(exist_ok=True)
start = time.monotonic()
# The source fixture may have been removed by independent workspace cleanup.
# Our own previous test file contains exact, hidden source-mesh backups.
for obj in list(bpy.context.scene.objects):
    if obj.get("cqr_backup"):
        original = bpy.data.objects.get(obj.get("cqr_original_name", ""))
        if original:
            original.data = obj.data
            original.hide_set(False)
            if "cqr_report" in original:
                del original["cqr_report"]
        bpy.data.objects.remove(obj, do_unlink=True)
options = core.preset("MINIMAL", max_error=0.025, min_quad_fraction=0.93)
reports, sources = [], []
for name in ("Beta_Surface", "Beta_Joints"):
    obj = bpy.data.objects[name]
    sources.append(obj)
    source_mesh = obj.data
    engine = core.Reducer(obj, options)
    try:
        print("START", name, engine.progress(), flush=True)
        previous = -1
        while not engine.done:
            status = engine.step()
            if status["strip_edits"]//25 != previous:
                previous = status["strip_edits"]//25
                print("PROGRESS", status, flush=True)
        mesh, report = engine.finish()
        assert obj.data == source_mesh, "Source mesh was modified"
        assert report["result"]["bad_edges"] == 0
        assert report["quad_fraction"] >= report["effective_quad_floor"]
        assert report["edge_flow_preserved"]
        assert report["grid_iterations"] % 2 == 0
        assert report["deviation_world_units"]["max"] <= options.max_error*1.0001
        preview = core.create_preview(bpy.context, obj, mesh, report)
        core.accept_preview(bpy.context, preview)
        reports.append(report)
        print("DONE", name, report["result"]["vertices"], report["quad_fraction"], flush=True)
    finally:
        engine.close()
bpy.ops.preferences.addon_enable(module="bl_ext.user_default.vrm")
vrm = core.export_vrm(bpy.context, sources, out / "real-model.vrm")
assert vrm["vertices"] > 0, vrm
vrm["below_10000"] = vrm["vertices"] < 10000
result = {"meshes": reports, "vrm": vrm, "seconds": round(time.monotonic()-start, 2)}
(out / "real-model-report.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
bpy.ops.wm.save_as_mainfile(filepath=str(out / "real-model.blend"))
print("REAL_MODEL_RESULT", json.dumps({"vrm": vrm, "seconds": result["seconds"]}), flush=True)
