"""Blender UI for Clean Quad Reduce; independent of Codex / MCP / skills."""
bl_info = {
    "name": "Clean Quad Reduce", "author": "Clean Quad Reduce contributors",
    "version": (1, 0, 2), "blender": (5, 1, 0),
    "location": "View3D > Sidebar > Clean Quad", "category": "Mesh",
    "description": "Preview quad-strip reduction with sampled shape bounds",
}

import json
from pathlib import Path

import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ExportHelper, ImportHelper

from . import core

_RUNNING = None


def _preset_changed(self, context):
    opt = core.preset(self.profile)
    for key in ("max_error", "relative_error", "max_aspect", "min_angle",
                "min_quad_fraction", "max_weight_error", "preserve_uv",
                "grid_strategy", "method", "allow_open_strips"):
        setattr(self, key, getattr(opt, key))


class CQR_Settings(bpy.types.PropertyGroup):
    profile: EnumProperty(name="Quality / 品質", items=[
        ("ALIGNED", "Aligned reference / 良好版と同じ方式", "Contact-aware aligned rows and regular pole fans; untextured models"),
        ("DETAIL", "Detail / 形状優先", "Conservative quad-strip reduction"),
        ("BALANCED", "Balanced / 標準", "Preserve UV boundaries and shape"),
        ("MINIMAL", "Minimal / 頂点数優先", "More shape change; UV-boundary protection starts OFF"),
    ], default="ALIGNED", update=_preset_changed)
    method: EnumProperty(name="Method / 方式", items=[("STRICT", "Strict rows", "Conservative edge-path checks"),
                                                    ("ALIGNED", "Aligned reference", "Successful contact-aware workspace recipe")], default="ALIGNED")
    max_error: FloatProperty(name="Max sampled error / 形状差", default=0.025,
                             min=0.000001, soft_max=0.1, subtype="DISTANCE",
                             description="Maximum bidirectional sampled surface deviation in world units")
    relative_error: FloatProperty(name="Part error ratio / 部位比率", default=0.025,
                                  min=0.001, max=1, subtype="FACTOR")
    max_aspect: FloatProperty(name="Max aspect / 面の縦横比", default=6, min=1, soft_max=10)
    min_angle: FloatProperty(name="Min corner angle / 最小角度", default=18, min=1, max=89,
                             description="Degrees; merged quads must also stay below 180 minus this angle")
    min_quad_fraction: FloatProperty(name="Quad fraction / 四角面率", default=0.65,
                                     min=0, max=1, subtype="FACTOR")
    max_weight_error: FloatProperty(name="Weight error / ウェイト差", default=0.15,
                                    min=0, max=1)
    protect_group: StringProperty(name="Protect group / 保護グループ", default="",
                                  description="Vertices with nonzero weights in this group stay in place")
    preserve_uv: BoolProperty(name="Protect UV boundaries / UV境界を保護", default=False)
    preserve_materials: BoolProperty(name="Protect materials / 材質境界を保護", default=True)
    preserve_sharp: BoolProperty(name="Protect sharp edges / シャープ辺を保護", default=True)
    weld_uv_splits: BoolProperty(name="Stitch matching seams / 分離頂点を接続", default=True,
                                 description="Only reciprocal boundary edges with matching weights and shape-key coordinates")
    allow_open_strips: BoolProperty(name="Allow strip endpoints / 列の終端を許可", default=True,
                                    description="May leave triangles at strip endpoints; quad-fraction limit still applies")
    grid_strategy: EnumProperty(name="Grid / 格子処理", items=[
        ("AUTO", "Auto grid + strips / 自動", "Try regular un-subdivision on eligible untextured meshes"),
        ("LOOPS_ONLY", "Quad strips only / 列だけ", "Only dissolve quad strips"),
    ], default="LOOPS_ONLY")
    max_steps: IntProperty(name="Max edits / 最大編集回数", default=1500, min=1, max=10000)
    transfer_normals: BoolProperty(name="Transfer normals / 元の法線を引継ぐ", default=True)
    contact_distance: FloatProperty(name="Contact band / 接触部の保護幅", default=0.006,
                                    min=0, soft_max=0.05, subtype="DISTANCE")
    contact_factor: FloatProperty(name="Contact error factor / 接触部の誤差係数", default=0.5,
                                  min=0.01, max=1)
    aligned_local_error: FloatProperty(name="Local error cap / 列の形状差", default=0.008, min=0.000001, subtype="DISTANCE")
    aligned_min_error: FloatProperty(name="Local error floor", default=0.0003, min=0.000001, subtype="DISTANCE")
    aligned_budget: FloatProperty(name="Aligned budget / 削減係数", default=3, min=0.01, soft_max=5)
    aligned_target_vertices: IntProperty(name="Calibration target / 再現用頂点数", default=0, min=0,
                                        description="0 means quality limit; nonzero is only for reference-recipe reproduction")
    aligned_spheres: BoolProperty(name="Sphere grid / 球状関節の縦横格子", default=True)
    sphere_large_extent: FloatProperty(name="Large joint size / 大きい関節", default=0.075, min=0.000001, subtype="DISTANCE")
    correction_threshold: FloatProperty(name="Correction threshold / 補正の基準", default=0.007, min=0, subtype="DISTANCE")
    correction_radius: FloatProperty(name="Correction radius / 補正範囲", default=0.012, min=0.000001, subtype="DISTANCE")
    correction_factor: FloatProperty(name="Correction factor / 補正係数", default=0.35, min=0.01, max=1)
    mirror_correction: BoolProperty(name="Mirror correction / 左右にも補正", default=True)
    vertex_percent: FloatProperty(name="Target / 頂点数の基準", default=50.0,
                                   min=0.1, max=100.0, precision=1, subtype="PERCENTAGE",
                                   description="Percentage of current Blender vertices to retain: 50% means half. Comparison only; reduction continues to the quality limit")
    wire: BoolProperty(name="Wire overlay / ワイヤー表示", default=True)
    busy: BoolProperty(default=False, options={"SKIP_SAVE"})
    status: StringProperty(default="Select meshes, then Analyze / メッシュを選択して分析")
    report_text: StringProperty(default="")
    export_vertices: IntProperty(default=0)


def _options(settings):
    return core.Options(**{k: getattr(settings, k) for k in core.Options.__dataclass_fields__}).validate()


def _meshes(context):
    return [o for o in context.selected_objects if o.type == "MESH"
            and not o.get("cqr_preview") and not o.get("cqr_backup")]


def _previews(context):
    return [o for o in context.scene.objects if o.get("cqr_preview")]


def _vertex_target(context):
    """Use source counts throughout preview; recompute for the current selection otherwise."""
    previews = _previews(context)
    sources = ({source for preview in previews
                if (source := core.source_for_preview(preview)) is not None}
               if previews else _meshes(context))
    before = sum(len(obj.data.vertices) for obj in sources)
    target = max(1, int(before * context.scene.cqr.vertex_percent / 100)) if before else 0
    return before, target


def _write_report(context, name, value):
    text = bpy.data.texts.get(name) or bpy.data.texts.new(name)
    text.clear()
    text.write(json.dumps(value, ensure_ascii=False, indent=2))
    context.scene.cqr.report_text = text.name


class CQR_OT_analyze(bpy.types.Operator):
    bl_idname = "cqr.analyze"
    bl_label = "Analyze / 分析"
    bl_description = "Inspect selected meshes without changing them"

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and not context.scene.cqr.busy and bool(_meshes(context))

    def execute(self, context):
        try:
            info = [core.analyze_object(o) for o in _meshes(context)]
            _write_report(context, "CQR Analysis.json", info)
            vertices = sum(x["vertices"] for x in info)
            context.scene.cqr.status = f"{len(info)} mesh(es), {vertices:,} vertices / 頂点"
            self.report({"INFO"}, context.scene.cqr.status)
            return {"FINISHED"}
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class CQR_OT_preview(bpy.types.Operator):
    bl_idname = "cqr.preview"
    bl_label = "Create Preview / 削減プレビュー"
    bl_description = "ESC cancels and restores visibility; originals remain unchanged"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (context.mode == "OBJECT" and not context.scene.cqr.busy
                and not _previews(context) and bool(_meshes(context)))

    def _start(self, context):
        global _RUNNING
        self.reduction_options = _options(context.scene.cqr)
        self.queue = list(_meshes(context))
        self.source_objects = list(self.queue)
        # Preflight the complete selection before allocating or hiding anything.
        for obj in self.queue:
            core.analyze_object(obj)
            if obj.library or obj.data.library:
                raise core.ReductionError(f"{obj.name}: linked data is read-only")
        self.total = len(self.queue)
        self.engine = None
        self.created = []
        self.reports = []
        self.timer = None
        self.old_selected = list(context.selected_objects)
        self.old_active = context.view_layer.objects.active
        context.scene.cqr.busy = True
        context.window_manager.progress_begin(0, self.total)
        _RUNNING = self

    def _tick(self, context):
        if self.engine is None:
            if not self.queue:
                return True
            self.engine = core.make_reducer(self.queue.pop(0), self.reduction_options, self.source_objects)
        status = self.engine.step()
        context.scene.cqr.status = f"{status['object']}: {status['vertices']:,} vertices, {status['strip_edits']} strips / 列"
        if self.engine.done:
            source = self.engine.obj
            mesh, report = self.engine.finish()
            self.engine = None
            try:
                preview = core.create_preview(context, source, mesh, report)
            except Exception:
                core.remove_mesh(mesh)
                raise
            preview.show_wire = context.scene.cqr.wire
            preview.show_all_edges = context.scene.cqr.wire
            self.created.append(preview)
            self.reports.append(report)
            context.window_manager.progress_update(len(self.created))
        for area in context.screen.areas if context.screen else []:
            if area.type == "VIEW_3D":
                area.tag_redraw()
        return not self.queue and self.engine is None

    def _stop(self, context, failed=False):
        global _RUNNING
        if self.engine:
            self.engine.close()
            self.engine = None
        if self.timer:
            context.window_manager.event_timer_remove(self.timer)
            self.timer = None
        if failed:
            for preview in self.created:
                if preview.name in bpy.data.objects:
                    core.discard_preview(context, preview)
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in self.old_selected:
                if obj.name in context.view_layer.objects:
                    obj.select_set(True)
            context.view_layer.objects.active = self.old_active
            context.scene.cqr.status = "Cancelled; sources preserved / 中止・元データは保持"
        else:
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in self.created:
                obj.select_set(True)
            if self.created:
                context.view_layer.objects.active = self.created[0]
            vertices = sum(r["result"]["vertices"] for r in self.reports)
            context.scene.cqr.status = f"Preview: {vertices:,} Blender vertices / Apply または Revert"
            _write_report(context, "CQR Reduction.json", self.reports)
        context.scene.cqr.busy = False
        context.window_manager.progress_end()
        _RUNNING = None

    def execute(self, context):
        # EXEC_DEFAULT also works for scripted / background tests without a window.
        try:
            self._start(context)
            while not self._tick(context):
                pass
            self._stop(context)
            return {"FINISHED"}
        except Exception as error:
            if hasattr(self, "created"):
                self._stop(context, failed=True)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

    def invoke(self, context, event):
        try:
            self._start(context)
            self.timer = context.window_manager.event_timer_add(0.05, window=context.window)
            context.window_manager.modal_handler_add(self)
            return {"RUNNING_MODAL"}
        except Exception as error:
            if hasattr(self, "created"):
                self._stop(context, failed=True)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

    def modal(self, context, event):
        if event.type == "ESC":
            self._stop(context, failed=True)
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"} if event.type in {
                "MIDDLEMOUSE", "WHEELUPMOUSE", "WHEELDOWNMOUSE", "TRACKPADPAN",
                "TRACKPADZOOM", "NDOF_MOTION", "MOUSEMOVE", "INBETWEEN_MOUSEMOVE",
            } else {"RUNNING_MODAL"}
        try:
            if self._tick(context):
                self._stop(context)
                return {"FINISHED"}
        except Exception as error:
            self._stop(context, failed=True)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}


class CQR_OT_accept(bpy.types.Operator):
    bl_idname = "cqr.accept"
    bl_label = "Apply Preview / プレビューを適用"
    bl_description = "Keep original object names and VRM bindings; hide source backups"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and not context.scene.cqr.busy and bool(_previews(context))

    def execute(self, context):
        previews = _previews(context)
        if any(core.source_for_preview(o) is None for o in previews):
            self.report({"ERROR"}, "Source missing; preview retained / 元オブジェクトがありません")
            return {"CANCELLED"}
        sources = []
        try:
            for preview in previews:
                core.validate_preview(preview)
            for preview in previews:
                source, _ = core.accept_preview(context, preview)
                source.show_wire = context.scene.cqr.wire
                source.show_all_edges = context.scene.cqr.wire
                sources.append(source)
            for obj in context.selected_objects:
                obj.select_set(False)
            for obj in sources:
                obj.select_set(True)
            context.view_layer.objects.active = sources[0]
            context.scene.cqr.status = "Applied; backups in CQR_Backups / 適用・元メッシュはバックアップ済み"
            return {"FINISHED"}
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class CQR_OT_revert(bpy.types.Operator):
    bl_idname = "cqr.revert"
    bl_label = "Revert Preview / プレビューを破棄"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and not context.scene.cqr.busy and bool(_previews(context))

    def execute(self, context):
        for preview in _previews(context):
            core.discard_preview(context, preview)
        context.scene.cqr.status = "Sources restored / 元メッシュを表示"
        return {"FINISHED"}


class CQR_OT_export(bpy.types.Operator, ExportHelper):
    bl_idname = "cqr.export_vrm"
    bl_label = "Export VRM + Count / VRM出力・頂点数確認"
    filename_ext = ".vrm"
    filter_glob: StringProperty(default="*.vrm", options={"HIDDEN"})

    @classmethod
    def poll(cls, context):
        return context.mode == "OBJECT" and not context.scene.cqr.busy and not _previews(context) and bool(_meshes(context))

    def execute(self, context):
        try:
            result = core.export_vrm(context, _meshes(context), self.filepath)
            _write_report(context, "CQR VRM.json", result)
            settings = context.scene.cqr
            settings.export_vertices = result["vertices"]
            settings.status = f"VRM: {result['vertices']:,} vertices / {result['triangles']:,} triangles"
            self.report({"INFO"}, settings.status)
            return {"FINISHED"}
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class CQR_OT_count(bpy.types.Operator, ImportHelper):
    bl_idname = "cqr.count_vrm"
    bl_label = "Count Existing VRM / VRMファイルを計測"
    filename_ext = ".vrm"
    filter_glob: StringProperty(default="*.vrm", options={"HIDDEN"})

    def execute(self, context):
        try:
            result = core.inspect_vrm(self.filepath)
            _write_report(context, "CQR VRM.json", result)
            context.scene.cqr.export_vertices = result["vertices"]
            context.scene.cqr.status = f"VRM: {result['vertices']:,} vertices / {result['triangles']:,} triangles"
            self.report({"INFO"}, context.scene.cqr.status)
            return {"FINISHED"}
        except Exception as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}


class CQR_PT_panel(bpy.types.Panel):
    bl_label = "Clean Quad Reduce"
    bl_idname = "CQR_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Clean Quad"

    def draw(self, context):
        layout, s = self.layout, context.scene.cqr
        previews = bool(_previews(context))
        if context.mode != "OBJECT":
            layout.label(text="Object Mode / オブジェクトモード", icon="INFO")
        layout.operator("cqr.analyze", icon="VIEWZOOM")
        col = layout.column()
        col.enabled = not s.busy and not previews
        col.prop(s, "profile")
        target_box = col.box()
        target_box.prop(s, "vertex_percent")
        before, target = _vertex_target(context)
        if before:
            target_box.label(text=f"Before / 削減前: {before:,}")
            target_box.label(text=f"Target / 目標: {target:,}")
            target_box.label(text="Blender vertices / Blender頂点数")
        else:
            target_box.label(text="Select meshes / メッシュを選択", icon="INFO")
        col.prop(s, "preserve_uv")
        col.prop(s, "wire")
        col.operator("cqr.preview", icon="MOD_DECIM")
        if s.busy:
            layout.label(text="Working; ESC to cancel / ESCで中止", icon="TIME")
        if previews:
            layout.operator("cqr.accept", icon="CHECKMARK")
            layout.operator("cqr.revert", icon="LOOP_BACK")
        box = layout.box()
        for start in range(0, len(s.status), 42):
            box.label(text=s.status[start:start+42])
        layout.operator("cqr.export_vrm", icon="EXPORT")

        header, body = layout.panel("cqr_advanced", default_closed=True)
        header.label(text="Advanced / 詳細設定")
        if body is not None:
            body.enabled = not s.busy and not previews
            box = body.box()
            box.label(text="Shape / 形状")
            box.prop(s, "max_error")
            box.prop(s, "relative_error")
            if s.method == "STRICT":
                box.prop(s, "min_quad_fraction")
            else:
                for key in ("aligned_local_error", "aligned_budget",
                            "aligned_spheres", "sphere_large_extent"):
                    box.prop(s, key)
            box = body.box()
            box.label(text="Protection / 保護")
            for key in ("preserve_materials", "preserve_sharp", "protect_group"):
                box.prop(s, key)
            if s.method == "ALIGNED":
                for key in ("contact_distance", "contact_factor", "correction_threshold",
                            "correction_radius", "correction_factor", "mirror_correction"):
                    box.prop(s, key)
            box = body.box()
            box.label(text="Geometry / 四角面の品質")
            for key in ("max_aspect", "min_angle", "max_weight_error", "grid_strategy",
                        "allow_open_strips", "weld_uv_splits", "transfer_normals", "max_steps"):
                if s.method == "ALIGNED" and key in {"max_weight_error", "grid_strategy"}:
                    continue
                box.prop(s, key)

        header, body = layout.panel("cqr_vrm_tools", default_closed=True)
        header.label(text="Measurement / 計測・レポート")
        if body is not None:
            body.operator("cqr.count_vrm", icon="FILEBROWSER")
            if s.report_text:
                body.label(text=f"Text Editor: {s.report_text}", icon="TEXT")


CLASSES = (CQR_Settings, CQR_OT_analyze, CQR_OT_preview, CQR_OT_accept,
           CQR_OT_revert, CQR_OT_export, CQR_OT_count, CQR_PT_panel)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.cqr = PointerProperty(type=CQR_Settings)


def unregister():
    if _RUNNING is not None:
        _RUNNING._stop(bpy.context, failed=True)
    if hasattr(bpy.types.Scene, "cqr"):
        del bpy.types.Scene.cqr
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
