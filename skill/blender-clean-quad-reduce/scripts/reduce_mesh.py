"""Standalone skill entry point: Blender --background model.blend --python ... -- --job job.json.

May also be loaded by an existing Blender Python/MCP session with runpy.run_path.
Uses only the sibling clean_quad_core.py; no installed add-on is required.
"""
import argparse
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path
import sys

import bpy

_core_path = Path(__file__).with_name("clean_quad_core.py")
_spec = importlib.util.spec_from_file_location("_standalone_clean_quad_core", _core_path)
core = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = core
_spec.loader.exec_module(core)


def _output_path(value, overwrite):
    if not value:
        return None
    path = Path(value).expanduser().resolve()
    if not path.parent.is_dir():
        raise core.ReductionError(f"Output directory does not exist: {path.parent}")
    if path.exists() and not overwrite:
        raise core.ReductionError(f"Output exists; choose another name or set overwrite: {path}")
    return path


def _objects(job):
    if "objects" in job:
        names = job["objects"]
        if not isinstance(names, list) or len(names) != len(set(names)):
            raise core.ReductionError("objects must be a list of unique object names")
        missing = [name for name in names if name not in bpy.context.scene.objects]
        if missing:
            raise core.ReductionError(f"Objects not in this scene: {missing}")
        objects = [bpy.context.scene.objects[name] for name in names]
        if any(o.type != "MESH" for o in objects):
            raise core.ReductionError("Every named object must be a mesh")
    else:
        objects = [o for o in bpy.context.selected_objects if o.type == "MESH"]
        if not objects and job.get("action", "analyze") == "analyze":
            objects = [o for o in bpy.context.scene.objects if o.type == "MESH" and o.visible_get()]
    objects = [o for o in objects if not o.get("cqr_backup") and not o.get("cqr_preview")]
    if not objects:
        raise core.ReductionError("Name or select at least one source mesh")
    return objects


def run_job(job):
    action = job.get("action", "analyze")
    if action == "count_vrm":
        return {"ok": True, "vrm": core.inspect_vrm(job["input_vrm"])}
    if action not in {"analyze", "reduce"}:
        raise core.ReductionError("action must be analyze, reduce or count_vrm")
    objects = _objects(job)
    analysis = [core.analyze_object(o) for o in objects]
    if action == "analyze":
        return {"ok": True, "meshes": analysis, "blender": bpy.app.version_string}
    if bpy.context.mode != "OBJECT":
        raise core.ReductionError("Switch to Object Mode before reducing")
    if any(o.get("cqr_preview") for o in bpy.context.scene.objects):
        raise core.ReductionError("Resolve existing previews before starting another reduction")
    options = core.preset(job.get("preset", "ALIGNED"), **job.get("settings", {}))
    overwrite = bool(job.get("overwrite", False))
    blend = _output_path(job.get("output_blend"), overwrite)
    vrm = _output_path(job.get("output_vrm"), overwrite)
    report_path = _output_path(job.get("report"), overwrite)
    export_objects = _objects({"objects": job["export_objects"]}) if vrm and "export_objects" in job else objects
    if vrm and not job.get("apply", False):
        raise core.ReductionError("VRM export requires apply=true; a preview is not an export commit")
    if len({str(p) for p in (blend, vrm, report_path) if p}) != sum(p is not None for p in (blend, vrm, report_path)):
        raise core.ReductionError("Output paths must be distinct")
    created, reports = [], []
    try:
        for obj in objects:
            mesh, report = core.reduce_object(obj, options, context_objects=export_objects)
            try:
                preview = core.create_preview(bpy.context, obj, mesh, report)
            except Exception:
                core.remove_mesh(mesh)
                raise
            created.append(preview)
            reports.append(report)
    except Exception:
        for preview in created:
            core.discard_preview(bpy.context, preview)
        raise
    if job.get("apply", False):
        for preview in created:
            core.accept_preview(bpy.context, preview)
    result = {"ok": True, "applied": bool(job.get("apply", False)), "meshes": reports,
              "blender_vertices": sum(r["result"]["vertices"] for r in reports),
              "options": asdict(options)}
    if vrm:
        result["vrm"] = core.export_vrm(bpy.context, export_objects, vrm)
        result["vrm"]["below_limit"] = result["vrm"]["vertices"] < int(job.get("vertex_limit", 10000))
    if blend:
        bpy.ops.wm.save_as_mainfile(filepath=str(blend))
        result["output_blend"] = str(blend)
    if report_path:
        report_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job", required=True, help="JSON job path")
    args = parser.parse_args(argv)
    try:
        job = json.loads(Path(args.job).read_text(encoding="utf-8"))
        result = run_job(job)
    except Exception as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise
    print("CQR_RESULT " + json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[sys.argv.index("--")+1:] if "--" in sys.argv else [])
