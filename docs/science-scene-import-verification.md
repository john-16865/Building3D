# Science Scene Import Verification

Date: 2026-05-20

Project checked: `/mnt/d/Users/johni/Documents/Unimate/Godot`

Scene checked: `res://Scene/science.tscn`

Assets checked:

- `res://Assets/Buildings/Science/science_visual.glb`
- `res://Assets/Buildings/Science/science_nav.glb`
- `res://Assets/Buildings/Science/science_manifest.json`

## Result

The visual Science model imports correctly. The full scene import is not perfect because the navigation asset is broken and the generated `NavigationRegion3D` nodes do not have navigation meshes assigned.

## Passed Checks

- `science_visual.glb` is a valid binary glTF 2.0 file.
- Khronos glTF Validator reported zero errors and zero warnings for the visual GLB. It only reported one info-level unused material.
- glTF-Transform inspected the visual GLB successfully.
- Assimp imported the visual GLB successfully.
- Blender imported the visual GLB successfully.
- Godot loaded `science_visual.glb` as a `PackedScene`.
- Godot loaded and instantiated `res://Scene/science.tscn`.
- The scene contains 15 floor nodes, matching the manifest.
- The scene contains 2,164 anchor nodes, matching 1,902 rooms plus 262 portals from the manifest.
- The scene contains 4,360 visual mesh instances.
- The browser-based Khronos glTF Sample Viewer loaded the visual GLB from a local HTTP server without console errors.
- A Blender preview render was generated at `exports/verification/science_visual_blender_preview.png`.

## Failed Checks

### `science_nav.glb` Is Not A GLB

`file` reports `science_nav.glb` as JSON text, not binary glTF.

The GLB header check shows:

```text
science_visual.glb: magic glTF, version 2
science_nav.glb: magic "{\n  ", not glTF
```

The contents of `science_nav.glb` are actually a glTF Validator JSON report for `science_visual.glb`. It looks like the navigation GLB was overwritten by validation output or copied from the wrong output path.

Tool failures:

- glTF-Transform: `Property 'asset' must be defined`
- Assimp: `No suitable reader found for the file format`
- Blender: `Bad glTF: no asset in json`
- Godot: `Failed loading resource: res://Assets/Buildings/Science/science_nav.glb`
- pygltflib: invalid GLB header
- trimesh: incorrect GLB header
- gltflib: not a valid GLB file
- gltf-pipeline: `File is not valid binary glTF`

`gltfpack` created a 204-byte output from `science_nav.glb`, but that output has no scenes and no meshes, so it is not a valid navigation result.

### Navigation Is Not Wired

The scene has:

```text
NavigationRegion3D nodes: 15
NavigationLink3D nodes: 0
NavigationRegion3D nodes with no NavigationMesh: 15
```

So the Science scene has placeholder navigation regions, but no usable Godot navigation mesh or vertical navigation links.

### Project Runtime Has Existing Unrelated Errors

Running Godot headless against the project also reports existing project-level issues:

- `godot_wry` cannot load because `libwebkit2gtk-4.1.so.0` is missing.
- `res://Scene/kate.tscn` and `res://Scene/Campus/CampusMain.tscn` report parse/resource errors during runtime startup.
- The kiosk helper warning appears because the local kiosk key environment is not configured.

These are not caused by the Science visual GLB, but they mean the current project boot is not clean.

## Tool Summary

| Tool | Visual GLB | Nav GLB | Notes |
| --- | --- | --- | --- |
| Khronos glTF Validator | Pass | Fail | Visual has 0 errors, nav is validator JSON not glTF. |
| glTF-Transform CLI | Pass | Fail | Nav has no `asset` field. |
| Assimp | Pass | Fail | Visual imports 4,360 meshes. |
| Blender CLI | Pass | Fail | Visual imports and renders; nav has bad glTF JSON. |
| Godot importer | Pass | Fail | Visual imports; nav `.import` is `valid=false`. |
| Godot scene load | Partial | N/A | Scene instantiates, but navigation is empty. |
| pygltflib | Pass | Fail | Nav header is invalid. |
| trimesh | Pass | Fail | Visual has 4,360 geometries. |
| gltflib | Pass | Fail | Nav is not a valid GLB. |
| gltf-pipeline | Pass | Fail | Visual round-trip works. |
| gltfpack | Pass | Not reliable | Nav produces an empty 204-byte GLB. |
| Khronos Sample Viewer | Pass | Not tested visually | Visual loaded from local HTTP server. |

## Concrete Counts

From the manifest:

```text
floors: 15
rooms: 1902
portals: 262
room + portal anchors: 2164
```

From `science.tscn` in Godot:

```text
floor nodes: 15
room/portal anchor nodes: 2164
visual mesh instances: 4360
navigation regions: 15
navigation links: 0
empty navigation regions: 15
```

From the visual GLB:

```text
meshes: 4360
vertices: 49545
faces/triangles: 47129
bounds: [-73.94, -6.12, -43.95] to [52.31, 47.43, 123.17]
```

## Verdict

The visual import is good. The scene is structurally present and the room/portal anchors line up with the manifest. But the import is not complete enough for UNIMATE navigation yet.

Fix required:

1. Regenerate `science_nav.glb` or stop naming non-GLB navigation data `.glb`.
2. Assign real `NavigationMesh` resources to the 15 `NavigationRegion3D` nodes, or switch this scene fully to the existing room/portal graph approach.
3. Generate `NavigationLink3D` nodes or equivalent graph edges for stairs/elevators.
4. Re-run the Godot import check until `science_nav.glb.import` is `valid=true` or the nav asset is replaced by a correctly named JSON graph.
