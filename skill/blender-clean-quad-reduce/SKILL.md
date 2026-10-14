---
name: blender-clean-quad-reduce
description: Reduce Blender mesh or VRM vertex counts by merging quad strips and regular quad grids while preserving edge flow. Use when the user requests clean topology, quad-based simplification, or VRM optimization that retains a rig; includes standalone Blender scripts without requiring the Clean Quad Reduce add-on.
---

# Clean quad reduction

Use the bundled scripts as the execution engine in Blender 5.1 or later. They do not depend on an installed reduction add-on, Codex-specific model names, or external Python packages. The VRM exporter is optional and needed only for VRM output.

## Inspect and choose constraints

For untextured, unkeyed models, start with `ALIGNED`: this is the successful contact-aware workspace recipe. It merges disjoint row strips in stages (0.9 → 2.0 → 3.0), halves local error budgets near intersections, preserves detected latitude/longitude axes with triangle fans only at sphere poles, then repeats the surface pass with extra protection near sampled shape errors. It uses no Un-Subdivide operation. `aligned_target_vertices=0` means continue to the quality limit; leave it at zero for general work. A nonzero value is a fixture-calibration control, not a VRM vertex goal.

`ALIGNED` automatically falls back to strict, UV-protected strips for textures, shape keys, color attributes, protection groups, or explicitly requested UV-boundary protection. This fallback is recorded in warnings. Sphere recognition must succeed for every component of that object; otherwise ordinary row reduction is used. A triangulated pole fan is intentional and does not mean that the regular quad grid rotated. The reported overall quad fraction includes pole triangles.

Run `scripts/reduce_mesh.py` with an `analyze` job, or load it in the connected Blender session with `runpy.run_path()` and call `run_job()`. Inspect the actual scene, object names, mode, UVs/textures, material boundaries, keys, weights, scale and modifiers. Read the returned analysis before choosing settings. Leave unrelated objects and existing user edits intact; use an isolated Blender process when working from a saved file.

Use quad-strip merging and regular un-subdivision. Triangles-to-quads reconstructs likely pairs; it cannot recover the author's original edge flow with certainty. Reduction changes connectivity; promise preserved flow and validated structure, not identical topology or universally optimal vertex counts.

Preserve original quad-row directions. Strict mode verifies resulting edges against the original opposite-edge paths after quad reconstruction. Odd Un-Subdivide counts create diagonal/diamond grids and are excluded; even strict-mode passes also need direction checks. `ALIGNED` uses no Un-Subdivide, merges original row strips, permits triangles at strip ends and sphere poles, and checks the generated sphere edges against the detected grid. Preserve these invariants when adjusting scripts; prioritize them over a vertex target. Existing diagonal flow in the source is retained rather than automatically retopologized.

Choose an error bound appropriate to the model's world scale. The engine measures world-space Blender units: with metre-based units, `0.005` is 5 mm. `DETAIL` preserves UV boundaries; `BALANCED` is moderate; `MINIMAL` permits more shape change and starts with UV-boundary protection OFF. For textured models, keep `preserve_uv=true`, normally choose `LOOPS_ONLY`, and visually inspect texture distortion. Preserve material, hard-edge and corner-color boundaries. Keep finger tips, contact rims, facial features or other important vertices in a protection group when the task needs them. For `ALIGNED`, scale the contact width, correction radius, local error cap and sphere-size threshold with the model; these are explicit world-unit controls, not model-name-specific constants.

Shape-key vertices and their one-ring are protected, and surviving key coordinates are checked after remapping. This may limit reduction on face/body meshes with extensive expressions. Unsupported or damaged meshes should produce an explicit limitation, not a fallback to collapse decimation or a remeshed replacement.

## Reduce and review

The entry point accepts a JSON job with `action`, explicit mesh `objects`, `preset`, and optional `settings`. Consult `--help` and `scripts/clean_quad_core.py:Options` for executable settings. `scripts/clean_quad_aligned.py` contains the portable aligned recipe and must stay beside the core script. Example for a protected / textured model:

```json
{
  "action": "reduce",
  "objects": ["Body", "Clothes"],
  "preset": "MINIMAL",
  "settings": {
    "max_error": 0.015,
    "preserve_uv": true,
    "grid_strategy": "LOOPS_ONLY",
    "min_quad_fraction": 0.95,
    "protect_group": "Keep"
  },
  "apply": false
}
```

Use actual discovered names and a protection group that exists on every selected mesh; omit `protect_group` when unnecessary. `apply=false` creates reversible preview objects and hides sources in the viewport. Sources remain unchanged. To discard/accept these previews, use the bundled core's `discard_preview(context, object)` / `accept_preview(context, object)` functions. Application keeps original object identities and names for VRM bindings, with hidden backups in `CQR_Backups`.

For a saved-file workflow:

```sh
"/path/to/blender" --background /path/to/input.blend --python-exit-code 1 \
  --python /path/to/blender-clean-quad-reduce/scripts/reduce_mesh.py -- --job /path/to/job.json
```

For a connected Blender Python session:

```python
import runpy
api = runpy.run_path('/path/to/blender-clean-quad-reduce/scripts/reduce_mesh.py')
result = api['run_job'](job)
```

Progressive edits stop at the configured quality constraints or maximum step count, not when a target vertex count is approached. Review the stop reason: a step-budget stop is not a quality limit; continue with a larger budget when warranted. If constraints prevent the requested reduction, report that outcome and adjust only constraints the user is willing to trade away.

Inspect shaded and wire views, row directions (especially diamond-grid rotation), silhouettes, texture seams, contact boundaries, and representative poses. Confirm `edge_flow_preserved=true` in every report. Structural checks and sampled rest-surface distances do not prove all animation poses or texture appearance. Non-armature modifiers remain on the objects, but error measurements cover the base mesh. Report any skipped / unreduced meshes explicitly. Compare from the original backup when changing settings rather than repeatedly degrading a previous output.

Process contacting meshes together so their original surfaces are available before application. `ALIGNED` preserves original surviving skin weights; its sampled checks do not bound all possible skinning poses. Strict mode also limits interpolated weight error. Keep the original materials and project authored corner normals from the reference surface for the aligned mode, rather than substituting a temporary inspection material.

## Save and measure

After review, use `apply=true`, unique `output_blend`, optional `output_vrm`, and `report` paths in a job from the original file. Existing paths require explicit `overwrite=true`; keep source files intact. If an original lives only in the running Blender session, save a backup before applying or exporting when needed to recover the full scene.

VRM output needs the separate VRM Add-on for Blender in that Blender process. Its validation failures are errors to resolve; do not bypass exporter warnings automatically. Include all intended avatar meshes in `export_objects` when exporting only some meshes after reduction; otherwise export defaults to the reduction `objects` list. Measure the complete avatar, including unreduced face/accessory meshes, rather than reporting only the simplified subset.

Use `action=count_vrm` and `input_vrm` to inspect an exported VRM without modifying the scene. The engine reads actual GLB POSITION accessor counts, including exported UV/normal splits. Report Blender vertices and exported VRM vertices separately; VRM triangles do not preserve editable quads. A `vertex_limit` is a reporting threshold, never an optimization stopping target. Deliver the quad-editable `.blend`, requested VRM, achieved quad fraction, sampled shape difference, and material limitations.
