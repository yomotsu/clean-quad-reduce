# Clean Quad Reduce

Standalone Blender 5.1+ extension. Install this ZIP from Blender Preferences → Get Extensions → menu → Install from Disk. No Codex, skill, MCP connection, network service or external Python package is needed for reduction.

Select mesh objects in Object Mode. Open View3D → N sidebar → Clean Quad. Analyze → choose quality/protection settings → Create Preview → inspect → Apply Preview or Revert Preview. ESC cancels an active preview job. Sources are unchanged until application and are then kept as hidden objects in CQR_Backups. Original object names and rig/VRM references remain on the reduced objects.

The default Aligned reference recipe merges disjoint row strips in stages, halves error budgets around intersecting surfaces, detects latitude/longitude sphere grids, and repeats with protection near sampled shape errors. Sphere poles use intentional triangle fans. This recipe never calls un-subdivision. It transfers original materials and projects authored normals. Textures, keys, colors, explicit UV protection or protection groups fall back to strict, UV-protected row reduction. Strict mode verifies new edge paths and disables odd un-subdivision passes. These methods preserve existing flow rather than retopologizing a source that was already diagonal.

Detail/Balanced protect UV boundaries. Aligned/Minimal start with UV protection OFF; the Aligned mode automatically protects textured/keyed assets by using the strict fallback. Distances, contact widths and sphere-size thresholds are world-space Blender units. Shape-key vertices and their one-ring are protected; surviving shape coordinates and key properties remain. Sampled rest-surface bounds and topology checks do not replace visual texture/pose review.

VRM export is optional and requires the separate VRM Add-on for Blender. Select every intended avatar mesh before Export VRM + Count. The limit is a report threshold; reduction continues until quality constraints or the edit budget stop it. Read the stop reason and JSON report in Blender's Text Editor. Editable quads remain in the .blend; VRM exports triangles.

日本語: オブジェクトモードで対象を選択し、NパネルのClean Quadで分析・プレビュー・適用します。元の四角面の縦横方向を守り、斜め格子を新しく作る操作は拒否します。テクスチャがある場合はUV境界保護を有効にしてください。表情が動く頂点と周囲は保護されます。

License: GPL-3.0-or-later (see LICENSE).
