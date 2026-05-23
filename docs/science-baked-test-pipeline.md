# Science And Group Baked Scene Handoff

This is the current handoff for the Science building navigation pipeline and the reusable process for generating other logical building groups.

The fixed process is:

```text
MapsIndoors polygons
  -> Building3D projected room/portal polygons
  -> wall-opened visual GLB
  -> Godot NavigationRegion3D bake from that visual GLB
  -> baked Science scene
```

The important rule is:

```text
The visual GLB is the navigation bake source.
Do not use the generated walkable mesh as the real navigation source.
```

## Current Status

Working:

- Same-floor Science baked navigation is better with Godot baking from the wall-opened visual GLB.
- A full-building Science scene has been generated and baked:
  `C:\Users\johni\Documents\Unimate\Godot\Scene\science_baked_full.tscn`.
- `BuildingMain.tscn` is currently wired to `res://Scene/science_baked_full.tscn`.
- The full-building export used fresh door evidence for every room:
  15 floors, 1897 rooms, 1897 route-derived room door rows, and 6 external entry rows.
- The full-building floor order from the current dataset is:
  `B-2`, `B-1`, `G`, `1`, `2`, `3`, `4`, `5`, `6`, `7`, `8`, `M8`, `9`, `10`, `11`.
- The final full-building route wall opening total is 411 opened route-crossed wall edges.
- Floor `B-2` is the only documented zero-opening exception:
  it has 7 rooms, 7 door rows, 24 clipped route segments, and 77 wall blockers, but no route segment crosses a wall blocker interior.
- A three-floor Science scene for real floors 3, 4, and 5 is preserved as a known-good rollback/reference:
  `C:\Users\johni\Documents\Unimate\Godot\Scene\science_baked_floor3_4_5.tscn`.
- The floor 3/4/5 export used fresh door evidence for every room:
  floor 3 = 223 rooms, floor 4 = 185 rooms, floor 5 = 186 rooms.
- The final route wall opening stats for that export were:
  floor 3 = 36 opened route-crossed wall edges, floor 4 = 37, floor 5 = 40.
- Floor 4 has a baked test scene: `C:\Users\johni\Documents\Unimate\Godot\Scene\science_baked_test.tscn`.
- Floor 5 has a baked test scene: `C:\Users\johni\Documents\Unimate\Godot\Scene\science_baked_floor5.tscn`.
- Building3D now uses route-derived evidence to remove wall edges from the visual mesh.
- Route debug output is back to thin line/quad segments. It is only for inspection.

Not solved:

- Inter-floor navigation still needs route-level validation in gameplay with the full 15-floor scene.
- `science_baked_floor4_5.tscn` exists as an old two-floor experiment, but it used a synthetic connected navigation plane and should not be used as the model for future baked scenes.
- Structure tests can verify floors load and connector groups exist, but that does not prove the NPC route successfully travels floor-to-floor.

## Key Repositories

Building3D generates geometry:

```text
C:\Users\johni\Documents\Building3D
```

UNIMATE consumes the generated GLBs and scenes:

```text
C:\Users\johni\Documents\Unimate\Godot
```

## What Changed From The Old Approach

The old approach used a separate route/walkable mesh to make Godot navigation possible. That was useful for debugging, but it could disagree with the visible building:

```text
route mesh says passable
visible wall still exists
user sees a blocked route
```

The fixed approach makes Building3D remove the correct wall edges from the visual GLB first. Godot then bakes from the same geometry the user sees:

```text
wall removed from visual GLB -> Godot can bake through it
wall still in visual GLB -> Godot treats it as blocked
```

This keeps visual geometry and navigation geometry aligned.

## Building3D Pipeline

### 1. MapsIndoors polygons become records

MapsIndoors features are read in:

```text
building3d/mapsindoors.py
building3d/normalize.py
```

Rooms and portals are normalized into records with:

```text
polygon_lonlat
anchor_lonlat
external_id
display_name
floor_name
floor_index
category or portal kind
```

### 2. Records are projected into local metres

Group generation in `building3d/groups.py` combines Science buildings and projects lon/lat coordinates into local X/Z metre coordinates:

```text
polygon_local
anchor_local
```

The Science group is defined in:

```text
configs/auckland_building_groups.yaml
```

The Science group contains:

```text
301, 302, 303, 303S, 305
```

### 3. Walls are built from polygon edges

Wall faces are generated in:

```text
building3d/geometry.py
```

The key function is:

```text
build_wall_mesh()
```

Normally every polygon edge becomes a vertical wall face. If an edge is marked open, that face is skipped:

```text
for each polygon edge:
  if edge is open:
    skip wall face
  else:
    extrude wall face
```

This is the core of the fixed process. We remove the wall before Godot sees the GLB.

### 4. Door points open polygon sides

Route-derived door points are loaded and passed into mesh generation before walls are built.

The phase-1 rule is:

```text
snap the door point to the nearest usable polygon side
remove that whole side as the opening
also remove the matching shared side when present
```

This is less precise than cutting a fixed-width doorway, but it is robust and fast to verify.

Important code:

```text
building3d/geometry.py
  dataset_meshes()
  _door_wall_openings()
  _open_edges_for_ring()
  build_wall_mesh(open_edges=...)
```

### 5. Trusted cached routes open additional crossed walls

Door points alone did not remove every wall that a trusted route crosses. The current Building3D flow does a second pass:

```text
build meshes with door openings
  -> extract remaining wall blockers
  -> read cached route lines
  -> find wall edges crossed by trusted routes
  -> add those crossed wall edges as openings
  -> rebuild final visual meshes
```

Important code:

```text
building3d/groups.py
  _route_wall_blockers_by_floor()
  _route_wall_openings_from_cache()
  _add_route_wall_openings_for_line()
  _route_wall_opening_edge_key()
```

This is why some walls are removed even when there was no clean door-point match.

## Debug Output

Use thin route debug GLBs only to inspect whether the removed walls line up with trusted route lines.

The route debug output should be thin line-like quads, not a filled walkable mesh. The active `building3d/groups.py` route-debug writer emits one small quad per route segment using the `route_centerline` material.

Expected debug shape:

```text
thin route segment quads
material = route_centerline
no walkable_path material
not used as Godot navigation source
```

Do not confuse this with the old walkable/nav mesh output.

## Legacy Walkable Mesh Code

The old walkable mesh implementation was copied here for reference:

```text
legacy\walkable_nav_mesh\groups_walkable_nav_mesh_legacy.py
legacy\walkable_nav_mesh\README.md
```

That legacy file exists only so the old implementation can be studied later. It should not be imported by production code.

The live process should not depend on:

```text
WalkablePathVisual
science_*_walkable_paths.glb
merged/buffered route debug polygons
walkable-source NavigationMesh assignment
```

Those can be useful for debugging, but not as the final navigation source.

## Building3D Outputs To Use

For a single exported floor, the important output is the visual GLB:

```text
exports\auckland\groups\science\science_floor_0_visual.glb
```

This GLB must contain:

```text
floor slabs
room plates
portal plates
wall meshes with selected edges removed
```

It may also be useful to generate a route debug GLB for inspection, but that debug GLB should not be copied into the Godot scene as navigation source.

For multi-floor exports, do not rely on the generic index names after publishing to Godot. Building3D writes files like:

```text
science_floor_0_visual.glb
science_floor_1_visual.glb
science_floor_2_visual.glb
```

Those indices are remapped export indices, not real floor labels. Always read `science_manifest.json` and rename copied Godot assets with real floor labels.

For the current floor 3/4/5 export, the mapping was:

```text
real floor 3 -> science_floor_0_visual.glb -> science_floor3_4_5_floor3_visual.glb
real floor 4 -> science_floor_1_visual.glb -> science_floor3_4_5_floor4_visual.glb
real floor 5 -> science_floor_2_visual.glb -> science_floor3_4_5_floor5_visual.glb
```

The same rule matters even more for the full building, where export index 0 is not necessarily an intuitive real floor.

## Godot Bake Process

Godot should bake from mesh instances in `FloorVisual`.

The bake scripts clear out old debug/navigation source children, load only the wall-opened visual GLB under `NavigationRegion3D/FloorMesh/FloorVisual`, configure a fresh `NavigationMesh`, and call:

```gdscript
NavigationRegion3D.bake_navigation_mesh(false)
```

Current bake settings:

```text
agent_radius = 0.25
agent_height = 1.8
agent_max_climb = 0.4
cell_size = 0.25
cell_height = 0.25
geometry_parsed_geometry_type = PARSED_GEOMETRY_MESH_INSTANCES
geometry_source_geometry_mode = SOURCE_GEOMETRY_ROOT_NODE_CHILDREN
```

## Current Godot Scenes

### Floor 3 + Floor 4 + Floor 5 current scene

Source scene:

```text
C:\Users\johni\Documents\Unimate\Godot\Scene\science_floor3_4_5_source.tscn
```

Generator:

```text
C:\Users\johni\Documents\Unimate\Godot\tools\generate_science_baked_floor3_4_5_scene.gd
```

Output:

```text
C:\Users\johni\Documents\Unimate\Godot\Scene\science_baked_floor3_4_5.tscn
```

Checker:

```text
C:\Users\johni\Documents\Unimate\Godot\tools\check_science_floor3_4_5_scene.gd
```

Important source assets:

```text
C:\Users\johni\Documents\Unimate\Godot\Assets\Buildings\Science\science_floor3_4_5_floor3_visual.glb
C:\Users\johni\Documents\Unimate\Godot\Assets\Buildings\Science\science_floor3_4_5_floor4_visual.glb
C:\Users\johni\Documents\Unimate\Godot\Assets\Buildings\Science\science_floor3_4_5_floor5_visual.glb
```

The generated scene had:

```text
real floors = 3, 4, 5
baked nav polygons = 3833
room rows used for door evidence = 594
route wall openings = 113 total
```

The scene intentionally removes:

```text
WalkablePathVisual
NavTarget
```

It keeps each visual GLB under:

```text
NavigationRegion3D/FloorMesh/FloorVisual
```

That is the scene pattern to copy for the full building.

### Floor 4

Generator:

```text
C:\Users\johni\Documents\Unimate\Godot\tools\generate_science_baked_test_scene.gd
```

Output:

```text
C:\Users\johni\Documents\Unimate\Godot\Scene\science_baked_test.tscn
```

This starts from `science.tscn`, removes `NavTarget` children, removes `WalkablePathVisual`, loads `science_floor_0_visual.glb`, and bakes a new Godot navmesh.

### Floor 5

Generator:

```text
C:\Users\johni\Documents\Unimate\Godot\tools\generate_science_baked_floor5_scene.gd
```

Output:

```text
C:\Users\johni\Documents\Unimate\Godot\Scene\science_baked_floor5.tscn
```

Important source assets:

```text
C:\Users\johni\Documents\Unimate\Godot\Scene\science_floor5_source.tscn
C:\Users\johni\Documents\Unimate\Godot\Assets\Buildings\Science\science_floor_real_5_visual.glb
```

The generated floor 5 scene had:

```text
floor_number = 5
baked nav polygons = 1215
room/portal nodes = 210
```

### Floor 4 + Floor 5 experiment

Generator:

```text
C:\Users\johni\Documents\Unimate\Godot\tools\generate_science_baked_floor4_5_scene.gd
```

Output:

```text
C:\Users\johni\Documents\Unimate\Godot\Scene\science_baked_floor4_5.tscn
```

This combines the two already-baked one-floor scenes:

```text
Floor0 -> real floor 4, floor_index = 0, floor_number = 4
Floor1 -> real floor 5, floor_index = 1, floor_number = 5
```

The structure test verifies the scene shape and connector graph, but the user has observed that inter-floor navigation is not working properly. Treat this as an unfinished experiment.

Do not use `generate_science_baked_floor4_5_scene.gd` as the template for scaling up. That script creates connected navigation planes from `ClickArea3D` instead of baking from the wall-opened visual GLBs. It was useful as a temporary experiment but violates the fixed handoff rule.

## BuildingMain State

UNIMATE `BuildingMain.tscn` is currently pointed at:

```text
res://Scene/science_baked_floor3_4_5.tscn
```

The public building id remains:

```text
science
```

Do not change the node name or `building_name` when swapping the scene, otherwise DataStore, Navigator, and kiosk start-room logic can break.

Current focused checks:

```text
C:\Users\johni\Documents\Unimate\Godot\tests\test_buildingmain_uses_science_baked_scene.gd
C:\Users\johni\Documents\Unimate\Godot\tests\test_buildingmain_science_smoke.gd
```

These verify that BuildingMain loads the new scene, registers Science as `science`, resolves the kiosk start room on real floor 4, and can activate a same-floor Science route.

## Exact Scale-Up Runbook

Use this runbook when scaling from the current floor 3/4/5 scene to the full Science building. The most important point is to never let `building3d group` consume stale door JSON or stale `door_route_cache` files.

### 1. Choose the floor scope

For another limited test, pass explicit real floor labels:

```bash
FLOOR_FLAGS=(--only-floor 3 --only-floor 4 --only-floor 5)
```

For the full building, omit `--only-floor` entirely:

```bash
FLOOR_FLAGS=()
```

After the bootstrap export, confirm the actual full-building floor labels from `data/processed/auckland/groups/science/dataset.json`. The current full Science order from the 2026-05-23 run is:

```text
B-2, B-1, G, 1, 2, 3, 4, 5, 6, 7, 8, M8, 9, 10, 11
```

Do not assume this order. Confirm it from the generated dataset and manifest before publishing to Godot.

### 2. Quarantine stale door and route artifacts

Run this from `C:\Users\johni\Documents\Building3D` in WSL/Git Bash:

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="exports/auckland/groups/science/backups_before_science_scaleup_${STAMP}"
mkdir -p "$BACKUP"
shopt -s nullglob

for path in \
  exports/auckland/groups/science/science_room_door_points_route_derived.* \
  exports/auckland/groups/science/science_door_research.md \
  exports/auckland/groups/science/science_external_entry_points_route_derived.* \
  exports/auckland/groups/science/external_doors.json \
  exports/auckland/groups/science/door_route_cache \
  data/processed/auckland/groups/science/science_room_door_points_route_derived.* \
  data/processed/auckland/groups/science/science_external_entry_points_route_derived.* \
  data/processed/auckland/groups/science/external_doors.json
do
  if [ -e "$path" ]; then
    mv "$path" "$BACKUP"/
  fi
done

echo "$BACKUP"
```

This step is required. The floor 5 failure happened because a later export reused floor-4-only route evidence.

### 3. Bootstrap the Building3D dataset

Run `group` once with no door/cache artifacts present:

```bash
UV_LINK_MODE=copy uv run python -m building3d group science \
  --config configs/auckland.yaml \
  --groups-config configs/auckland_building_groups.yaml \
  --no-fetch \
  "${FLOOR_FLAGS[@]}"
```

This first run is only to generate the correct `dataset.json` and manifest for the selected floors. Do not publish these first-run GLBs to Godot yet.

### 4. Validate the bootstrap floor set

```bash
python3 - <<'PY'
import json
from collections import Counter
from pathlib import Path

base = Path("data/processed/auckland/groups/science")
export = Path("exports/auckland/groups/science")
dataset = json.loads((base / "dataset.json").read_text())
manifest = json.loads((export / "science_manifest.json").read_text())

room_counts = Counter(str(r.get("floor_name")) for r in dataset.get("rooms", []))
portal_counts = Counter(str(p.get("floor_name")) for p in dataset.get("portals", []))
floors = [(f.get("floor_index"), str(f.get("floor_name")), f.get("height")) for f in dataset.get("floors", [])]

print("dataset_floors", floors)
print("room_counts", dict(sorted(room_counts.items())))
print("portal_counts", dict(sorted(portal_counts.items())))
print("manifest_assets", manifest.get("assets", {}).get("floor_visual_glbs"))
print("door_json_exists_export", (export / "science_room_door_points_route_derived.json").exists())
print("door_cache_exists_export", (export / "door_route_cache").exists())

assert room_counts, "No Science rooms generated"
assert not (export / "science_room_door_points_route_derived.json").exists(), "Stale door JSON is still active"
assert not (export / "door_route_cache").exists(), "Stale route cache is still active"
PY
```

For the current floor 3/4/5 run, this printed:

```text
room_counts {'3': 223, '4': 185, '5': 186}
portal_counts {'3': 24, '4': 24, '5': 24}
```

For the full building, record the printed floor labels and room counts. Those counts become the expected counts for the rest of the run.

### 5. Derive fresh door and route evidence

For a middle-floor-only export like floor 3/4/5, skip external entries:

```bash
UV_LINK_MODE=copy uv run python tools/derive_science_door_points.py \
  --dataset data/processed/auckland/groups/science/dataset.json \
  --inventory data/processed/auckland/inventory.json \
  --output-dir exports/auckland/groups/science \
  --skip-external \
  --workers 8 \
  --delay 0.03
```

For the full building, include external entries by omitting `--skip-external`:

```bash
UV_LINK_MODE=copy uv run python tools/derive_science_door_points.py \
  --dataset data/processed/auckland/groups/science/dataset.json \
  --inventory data/processed/auckland/inventory.json \
  --output-dir exports/auckland/groups/science \
  --workers 8 \
  --delay 0.03
```

### 6. Validate fresh door coverage

```bash
python3 - <<'PY'
import json
from collections import Counter
from pathlib import Path

base = Path("data/processed/auckland/groups/science")
export = Path("exports/auckland/groups/science")
dataset = json.loads((base / "dataset.json").read_text())
doors = json.loads((export / "science_room_door_points_route_derived.json").read_text())

room_counts = Counter(str(r.get("floor_name")) for r in dataset.get("rooms", []))
door_counts = Counter(str(r.get("floor_name")) for r in doors)
cache_files = list((export / "door_route_cache").glob("route_*.json"))

print("room_counts", dict(sorted(room_counts.items())))
print("door_counts", dict(sorted(door_counts.items())))
print("total_rooms", sum(room_counts.values()), "total_doors", len(doors))
print("cache_route_files", len(cache_files))

assert door_counts == room_counts, (door_counts, room_counts)
assert len(doors) == sum(room_counts.values())
assert len(cache_files) >= len(doors)
invalid = [r for r in doors if not isinstance(r.get("door_local"), list) or len(r.get("door_local")) < 3]
assert not invalid, f"Invalid door_local records: {len(invalid)}"
PY
```

This is the gate that prevents the floor 5 mistake. Do not continue if any floor is missing or under-counted.

### 7. Run the final Building3D export

Run the same group command again. This time it will consume the fresh door JSON and fresh route cache:

```bash
UV_LINK_MODE=copy uv run python -m building3d group science \
  --config configs/auckland.yaml \
  --groups-config configs/auckland_building_groups.yaml \
  --no-fetch \
  "${FLOOR_FLAGS[@]}"
```

### 8. Validate final wall openings and GLBs

```bash
python3 - <<'PY'
import json, struct
from collections import Counter
from pathlib import Path

base = Path("data/processed/auckland/groups/science")
export = Path("exports/auckland/groups/science")
dataset = json.loads((base / "dataset.json").read_text())
doors = json.loads((export / "science_room_door_points_route_derived.json").read_text())
manifest = json.loads((export / "science_manifest.json").read_text())

room_counts = Counter(str(r.get("floor_name")) for r in dataset.get("rooms", []))
door_counts = Counter(str(r.get("floor_name")) for r in doors)
asset_map = [
    (a.get("floor_index"), str(a.get("floor_name")), a.get("filename"))
    for a in manifest.get("assets", {}).get("floor_visual_glbs", [])
]
route_openings = manifest.get("nav", {}).get("validation", {}).get("route_wall_openings", {})
zero_opening_exceptions = {"B-2"}  # 2026-05-23: valid B-2 exception, documented in science_full_run_validation.md

print("room_counts", dict(sorted(room_counts.items())))
print("door_counts", dict(sorted(door_counts.items())))
print("asset_map", asset_map)
print("route_wall_openings", route_openings)

assert door_counts == room_counts
assert asset_map, "No floor visual GLBs in manifest"

missing_opening_floors = [
    floor for floor in room_counts
    if route_openings.get("floors", {}).get(floor, 0) <= 0
]
unexpected_missing = sorted(set(missing_opening_floors) - zero_opening_exceptions)
assert not unexpected_missing, f"Floors with zero route wall openings: {unexpected_missing}"
if "B-2" in missing_opening_floors:
    assert (export / "science_full_run_validation.md").exists(), "B-2 exception must be documented"

for floor_index, floor_name, filename in asset_map:
    path = export / filename
    data = path.read_bytes()
    magic, version, length = struct.unpack_from("<4sII", data, 0)
    print(f"glb_floor_{floor_name}", filename, "bytes", len(data), "length_ok", length == len(data))
    assert magic == b"glTF" and version == 2 and length == len(data)
PY
```

Default to treating a zero route-wall-opening floor as a failure. The current full-building run documents `B-2` as a valid exception in `science_full_run_validation.md`; any new zero-opening floor needs its own evidence before continuing.

### 9. Publish assets to UNIMATE with real floor labels

Read `exports/auckland/groups/science/science_manifest.json`. For each `assets.floor_visual_glbs[]` entry, copy the corresponding GLB into:

```text
C:\Users\johni\Documents\Unimate\Godot\Assets\Buildings\Science
```

Use explicit real-floor names, not generic index names. Recommended full-building names:

```text
science_full_floor_b2_visual.glb
science_full_floor_b1_visual.glb
science_full_floor_g_visual.glb
science_full_floor_1_visual.glb
science_full_floor_2_visual.glb
...
science_full_floor_m8_visual.glb
science_full_floor_11_visual.glb
```

Also publish the matching manifest and door evidence with a full-building prefix:

```text
science_full_manifest.json
science_full_room_door_points_route_derived.json
science_full_door_research.md
```

After copying, verify hashes between Building3D and UNIMATE copies with `sha256sum`.

### 10. Generate the full-building Godot source scene

Start from the generated:

```text
exports\auckland\groups\science\science_unimate.tscn
```

Write a new source scene in UNIMATE:

```text
C:\Users\johni\Documents\Unimate\Godot\Scene\science_full_source.tscn
```

Rewrite the `ext_resource` paths so each visual GLB uses the explicit full-building asset name from step 9.

Do not leave references to:

```text
science_floor_0_visual.glb
science_floor_1_visual.glb
science_floor4_5_*
```

### 11. Import GLBs before baking

Godot must create `.import` files for newly named GLBs before a scene can load them as `PackedScene` resources:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --import
```

If you skip this step, the bake can fail with:

```text
No loader found for resource: res://Assets/Buildings/Science/<new>.glb
```

### 12. Bake from the visual GLBs

Create a full-building generator from the current good pattern:

```text
tools\generate_science_baked_floor3_4_5_scene.gd
```

Use these constants for the full building:

```gdscript
const SOURCE_SCENE := "res://Scene/science_full_source.tscn"
const OUTPUT_SCENE := "res://Scene/science_baked_full.tscn"
const EXPECTED_FLOORS := ["B-2", "B-1", "G", "1", "2", "3", "4", "5", "6", "7", "8", "M8", "9", "10", "11"]
```

Keep the same bake rule:

```text
NavigationRegion3D/FloorMesh/FloorVisual is the source.
WalkablePathVisual and NavTarget are removed.
NavigationRegion3D.bake_navigation_mesh(false) is called per floor.
```

Run the bake:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tools/generate_science_baked_full_scene.gd
```

### 13. Check the baked full scene before wiring BuildingMain

Create a checker from:

```text
tools\check_science_floor3_4_5_scene.gd
```

For the full building it must verify:

```text
expected floor labels and count
each floor has NavigationRegion3D with non-empty NavigationMesh
each floor keeps FloorVisual under NavigationRegion3D/FloorMesh
no WalkablePathVisual
no NavTarget
no science_floor4_5_* references
no generic science_floor_<index>_visual.glb references
```

Run it:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tools/check_science_full_scene.gd
```

Only after this passes should `BuildingMain.tscn` be pointed to:

```text
res://Scene/science_baked_full.tscn
```

Then update and run:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tests/test_buildingmain_uses_science_baked_scene.gd

& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tests/test_buildingmain_science_smoke.gd
```

## General Automation Runbook For Other Buildings

Use this section when making the same baked-scene pipeline for another logical building group such as `engineering`. The Science run above is the worked example; this section is the reusable version.

### 1. Select the group and derive names from config

The available logical groups are defined in:

```text
configs/auckland_building_groups.yaml
```

Current groups:

```text
engineering -> members 401, 402, 405; primary_member 405
science -> members 301, 302, 303, 303S, 305; primary_member 302
science_test -> member 303; primary_member 303
```

Do not hard-code Science paths. Every automation script should derive these values:

```text
GROUP_ID=engineering
PRIMARY_MEMBER=<primary_member from configs/auckland_building_groups.yaml>
EXPORT_DIR=exports/auckland/groups/${GROUP_ID}
PROCESSED_DIR=data/processed/auckland/groups/${GROUP_ID}
GODOT_BUILDING_DIR=C:\Users\johni\Documents\Unimate\Godot\Assets\Buildings\<PascalCaseGroupId>
SOURCE_SCENE=res://Scene/${GROUP_ID}_full_source.tscn
BAKED_SCENE=res://Scene/${GROUP_ID}_baked_full.tscn
ASSET_PREFIX=${GROUP_ID}_full
```

The Godot asset directory name must match Building3D's `_unimate_asset_base()` rule:

```text
engineering -> Assets/Buildings/Engineering
science -> Assets/Buildings/Science
science_test -> Assets/Buildings/ScienceTest
```

### 2. Use the same three-pass Building3D flow

For any group, the safe flow is:

```text
quarantine active door/cache artifacts
bootstrap group export with no active door/cache artifacts
derive fresh route door evidence
rename door evidence to the current group id
final group export consuming the fresh evidence
publish explicit real-floor assets to Godot
generate source scene
Godot import
bake from FloorVisual
check scene
wire BuildingMain only after checks pass
```

The generic group export command is:

```bash
UV_LINK_MODE=copy uv run python -m building3d group "$GROUP_ID" \
  --config configs/auckland.yaml \
  --groups-config configs/auckland_building_groups.yaml \
  --no-fetch
```

Keep `--no-fetch` for reproducible cached-data runs. Omit it only when intentionally refreshing raw MapsIndoors data.

### 3. Quarantine per-group stale artifacts

Before the bootstrap export, move only the active artifacts for the target group. Leave old backup folders alone.

```bash
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP="$EXPORT_DIR/backups_before_${GROUP_ID}_full_${STAMP}"
mkdir -p "$BACKUP/export" "$BACKUP/processed"
shopt -s nullglob

for path in \
  "$EXPORT_DIR/${GROUP_ID}_room_door_points_route_derived."* \
  "$EXPORT_DIR/${GROUP_ID}_external_entry_points_route_derived."* \
  "$EXPORT_DIR/${GROUP_ID}_door_research.md" \
  "$EXPORT_DIR/external_doors.json" \
  "$EXPORT_DIR/door_route_cache"
do
  [ -e "$path" ] && mv "$path" "$BACKUP/export/"
done

for path in \
  "$PROCESSED_DIR/${GROUP_ID}_room_door_points_route_derived."* \
  "$PROCESSED_DIR/${GROUP_ID}_external_entry_points_route_derived."* \
  "$PROCESSED_DIR/external_doors.json" \
  "$PROCESSED_DIR/door_route_cache"
do
  [ -e "$path" ] && mv "$path" "$BACKUP/processed/"
done
```

This is the step that prevents one building or floor scope from reusing another run's door evidence.

### 4. Door derivation is reusable, but filenames must be group-specific

`tools/derive_science_door_points.py` is currently named for Science and writes `science_*` output filenames. The route math can be reused for another group dataset, but an automatic runner must set `--primary-member` and rename the outputs before the final export.

Run it against the selected group dataset:

```bash
UV_LINK_MODE=copy uv run python tools/derive_science_door_points.py \
  --dataset "$PROCESSED_DIR/dataset.json" \
  --inventory data/processed/auckland/inventory.json \
  --output-dir "$EXPORT_DIR" \
  --primary-member "$PRIMARY_MEMBER" \
  --workers 8 \
  --delay 0.03
```

Then normalize filenames for the selected group:

```bash
mv "$EXPORT_DIR/science_room_door_points_route_derived.json" "$EXPORT_DIR/${GROUP_ID}_room_door_points_route_derived.json"
mv "$EXPORT_DIR/science_room_door_points_route_derived.csv" "$EXPORT_DIR/${GROUP_ID}_room_door_points_route_derived.csv"

if [ -f "$EXPORT_DIR/science_external_entry_points_route_derived.json" ]; then
  mv "$EXPORT_DIR/science_external_entry_points_route_derived.json" "$EXPORT_DIR/${GROUP_ID}_external_entry_points_route_derived.json"
fi
if [ -f "$EXPORT_DIR/science_external_entry_points_route_derived.csv" ]; then
  mv "$EXPORT_DIR/science_external_entry_points_route_derived.csv" "$EXPORT_DIR/${GROUP_ID}_external_entry_points_route_derived.csv"
fi
if [ -f "$EXPORT_DIR/science_door_research.md" ]; then
  mv "$EXPORT_DIR/science_door_research.md" "$EXPORT_DIR/${GROUP_ID}_door_research.md"
fi
```

Do not run the final export until this file exists:

```text
exports/auckland/groups/<group>/<group>_room_door_points_route_derived.json
```

Building3D looks for `<group>_room_door_points_route_derived.json`; if the file is still named `science_room_door_points_route_derived.json`, the final export will not remove the route-derived walls for that group.

### 5. Generic validation gates

After the bootstrap export:

```text
dataset floors are non-empty
room counts are non-empty
active door JSON does not exist yet
active door_route_cache does not exist yet
```

After door derivation:

```text
door_counts == room_counts
total door rows == total rooms
door_route_cache route_*.json count >= total rooms
each door row has a 3-value door_local
external entries are recorded if the route graph exposes building entries
```

After the final export:

```text
manifest floor_visual_glbs count == dataset floor count
every visual GLB has a valid GLB header
hashes match after copying GLBs to Godot
route_wall_openings are nonzero for every room floor unless the run output documents a real zero-opening exception
```

For zero-opening exceptions, copy the Science pattern:

```text
<group>_full_run_validation.md
```

The note must include room count, door count, route segment count, wall blocker count, and why the floor is a valid exception.

### 6. Publish assets with real floor labels

Never publish generic index names into Godot for final scenes. Read:

```text
exports/auckland/groups/<group>/<group>_manifest.json
```

For each `assets.floor_visual_glbs[]`, copy:

```text
<group>_floor_<index>_visual.glb
```

to:

```text
Assets/Buildings/<PascalCaseGroupId>/<group>_full_floor_<safe_floor_label>_visual.glb
```

Use the same rule for walkable path GLBs if the generated source scene references them:

```text
Assets/Buildings/<PascalCaseGroupId>/<group>_full_floor_<safe_floor_label>_walkable_paths.glb
```

Safe floor labels:

```text
B-2 -> b2
B-1 -> b1
G -> g
M8 -> m8
numeric labels stay numeric
other labels: lowercase and remove non-alphanumeric characters
```

Also publish:

```text
<group>_full_manifest.json
<group>_full_room_door_points_route_derived.json
<group>_full_external_entry_points_route_derived.json, if present
<group>_full_external_doors.json, if present
<group>_full_door_research.md
<group>_full_run_validation.md, if any floor has a documented exception
```

### 7. Generate Godot source, bake, and check scripts from templates

Do not hand-edit each floor. Generate these files from the manifest floor list:

```text
Scene/<group>_full_source.tscn
tools/generate_<group>_baked_full_scene.gd
tools/check_<group>_full_scene.gd
Scene/<group>_baked_full.tscn
```

The source scene starts from:

```text
exports/auckland/groups/<group>/<group>_unimate.tscn
```

Rewrite all `ext_resource` GLB paths from generic export names to the explicit `<group>_full_floor_<safe_floor_label>_*` names.

The generator constants should be:

```gdscript
const SOURCE_SCENE := "res://Scene/<group>_full_source.tscn"
const OUTPUT_SCENE := "res://Scene/<group>_baked_full.tscn"
const EXPECTED_FLOORS := <floor labels from manifest order>
```

The checker must verify:

```text
root building_name == <group>
floor labels match manifest order
every floor has a non-empty NavigationMesh
every floor keeps NavigationRegion3D/FloorMesh/FloorVisual
no WalkablePathVisual
no NavTarget
no generic <group>_floor_<index>_visual.glb references
no old experiment asset prefixes
```

Always run Godot import before baking newly named GLBs:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --import
```

### 8. BuildingMain wiring rules for other buildings

Only wire `BuildingMain.tscn` after the per-building checker passes.

For an existing node such as `Engineering`, replace only the ext_resource path:

```text
res://Scene/engineering.tscn
```

with:

```text
res://Scene/engineering_baked_full.tscn
```

Keep the public identity unchanged:

```text
node name = Engineering
building_name = "engineering"
```

Do not rename the node or `building_name`; DataStore, Navigator, room aliases, and UI building selection depend on the public group id.

If adding a new group that is not already in `BuildingMain.tscn`, add it only after:

```text
the group exists in configs/auckland_building_groups.yaml
its baked scene passes the checker
DataStore and UI aliases know the public group id
focused BuildingMain smoke tests exist for that group
```

### 9. Engineering example

For Engineering, the automation variables should be:

```text
GROUP_ID=engineering
PRIMARY_MEMBER=405
EXPORT_DIR=exports/auckland/groups/engineering
PROCESSED_DIR=data/processed/auckland/groups/engineering
GODOT_BUILDING_DIR=C:\Users\johni\Documents\Unimate\Godot\Assets\Buildings\Engineering
SOURCE_SCENE=res://Scene/engineering_full_source.tscn
BAKED_SCENE=res://Scene/engineering_baked_full.tscn
```

After validation, `BuildingMain.tscn` already has an `Engineering` node. Replace its ext_resource path with `res://Scene/engineering_baked_full.tscn`, but keep `building_name = "engineering"`.

## Commands

Godot binary used locally:

```powershell
C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe
```

Run the floor 4 bake:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tools/generate_science_baked_test_scene.gd
```

Run the floor 5 bake:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tools/generate_science_baked_floor5_scene.gd
```

Run the floor 4+5 combine:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tools/generate_science_baked_floor4_5_scene.gd
```

Run the floor 3+4+5 bake:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tools/generate_science_baked_floor3_4_5_scene.gd
```

Run the structure tests:

```powershell
& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tests/test_science_baked_floor5_scene_structure.gd

& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tests/test_science_baked_floor4_5_scene_structure.gd

& "C:\Program Files (x86)\Steam\steamapps\common\Godot Engine\godot.windows.opt.tools.64.exe" --headless --path C:\Users\johni\Documents\Unimate\Godot --script res://tools/check_science_floor3_4_5_scene.gd
```

These tests may pass while the full inter-floor NPC route still fails. They are structure checks, not complete route validation.

## Next Debugging Target

The next work should focus on inter-floor runtime behavior, not wall removal.

Likely areas to inspect in UNIMATE:

```text
Godot\Scripts\map\BuildingController.gd
Godot\Scripts\map\navigation_npc.gd
Godot\Scripts\map\BuildingMainController.gd
Godot\Scripts\autoload\AppBus.gd
```

Things to verify:

```text
floor_index vs floor_number is consistent
NavigationLink3D start/end positions are in the coordinate space Godot expects
links update after floor expansion/collapse
Navigator selects the intended floor-to-floor portal segment
NPC can traverse NavigationLink3D, not just find a portal graph entry
```

Do not go back to using the walkable mesh to hide this. The fixed geometry process is wall-opened visual GLB -> Godot bake. The remaining problem is inter-floor runtime navigation.
