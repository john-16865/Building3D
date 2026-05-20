# UNIMATE Room Node and Building Grouping Report

Date: 2026-05-20

## Purpose

This report documents how UNIMATE currently represents indoor buildings in Godot, using Engineering as the reference. The goal is to make Building3D generate assets that fit UNIMATE's existing navigation system, especially for campuses where one logical building is made from several MapsIndoors/admin building parts.

The immediate problem is Science. The MapsIndoors data exposes Science Centre as separate building parts such as `301`, `302`, `303`, `303S`, and `305`, but in the UNIMATE user experience those may need to behave as one logical navigation entity: `science`.

## Main Finding

UNIMATE does not treat Engineering as separate logical buildings. It uses one Godot building scene/controller called `engineering`, while preserving physical source building prefixes inside room node names.

Examples from Engineering:

```text
405 422_Seminar Room 1
402 303_Leech Study Area
402E 334_Digital Lab
401 401_Lecture Theatre
405 400E1_Elevator_SetE1
405 400S2_Stair 2_Set2
```

This is the important architectural rule:

```text
RoomRef.building_id decides whether routing is same-building or cross-building.
```

If two rooms have the same `building_id`, UNIMATE routes inside one building controller. If they have different `building_id` values, UNIMATE creates a cross-building route with a campus leg.

Therefore, Science should not be imported as five separate logical Godot buildings if the target behavior is "route inside Science". The generated scene should be one logical `science` building, with room nodes preserving their physical prefixes.

## UNIMATE Files Reviewed

The relevant files are in the UNIMATE repo:

```text
Godot/Scene/BuildingMain.tscn
Godot/Scene/engineering.tscn
Godot/Scene/kate.tscn
Godot/Scripts/map/BuildingController.gd
Godot/Scripts/map/FloorController.gd
Godot/Scripts/map/BuildingMainController.gd
Godot/Scripts/utils/DataStoreIntegrator.gd
Godot/Scripts/singletons/DataStore.gd
Godot/Scripts/shared/dto.gd
Godot/Scripts/autoload/Navigator.gd
Godot/Scene/new_ui/screens/TimetableList.gd
```

No UNIMATE files were edited while preparing this report.

## Current Building Scene Structure

`BuildingMain.tscn` instances the active indoor buildings. At the time of inspection it includes Kate and Engineering.

Engineering is represented by:

```text
Engineering
  BuildingMesh
  Floors
    Floor1
    Floor2
    Floor3
    Floor4
    Floor5
  NavigationLinks
  ClickArea3D
```

The root node has `BuildingController.gd` attached and exports:

```gdscript
building_name = "engineering"
```

This `building_name` is the logical building ID used by `Navigator`.

## Engineering Multi-Part Pattern

Engineering combines several physical/admin building prefixes into one logical building:

```text
Logical building: engineering

Physical/source prefixes seen in room nodes:
- 401
- 402
- 402E
- 405
```

The scene does not create separate `BuildingController` nodes for `401`, `402`, `402E`, or `405`. They are all child room markers inside the one Engineering controller.

This is the pattern Science should follow:

```text
Logical building: science

Physical/source prefixes:
- 301
- 302
- 303
- 303S
- 305
```

## Room Node Contract

Room nodes are not optional decoration. They are the main semantic interface between the scene and UNIMATE's routing, labels, search, timetable integration, and portal detection.

### Required Hierarchy

Each generated building scene should follow this shape:

```text
BuildingRoot
  Floors
    Floor0
      NavigationRegion3D
        FloorMesh or generated floor mesh node
      Rooms
        <room marker nodes>
        <door marker nodes>
        <stair marker nodes>
        <elevator marker nodes>
      ClickArea3D
    Floor1
      NavigationRegion3D
      Rooms
      ClickArea3D
  NavigationLinks
```

The exact mesh hierarchy can vary, but these names and concepts are important:

```text
Floors
FloorX
NavigationRegion3D
Rooms
ClickArea3D
NavigationLinks
```

`FloorController.gd` expects the floor node to have:

```text
NavigationRegion3D
Rooms
```

`BuildingController.gd` expects the building to have:

```text
Floors
```

### Floor Nodes

Each floor node should:

```text
- be a Node3D
- have FloorController.gd attached
- set floor_index
- set floor_number
- set floor_name
- contain NavigationRegion3D
- contain Rooms
```

Example:

```text
Floor3
  script = FloorController.gd
  floor_name = "Floor 3"
  floor_index = 2
  floor_number = 3
```

Important distinction:

```text
floor_index  = zero-based internal route index
floor_number = user/display floor number
floor_name   = source/display floor label
```

For generated buildings, do not assume the MapsIndoors floor name is always a simple integer. Science has floor labels like `G` and `B-1` in generated manifests.

## How Room Nodes Are Read

`FloorController.setup_rooms()` scans every child under `Rooms`.

If a child has `get_room_data()`, that method is used. Otherwise, the node is converted into basic room data:

```gdscript
{
  "name": child.name,
  "position": child.global_position,
  "type": "generic",
  "node": child
}
```

This means a generated room marker can be a plain `Node3D`. It does not need a script for the first version.

## How Room Nodes Become RoomRef Values

`BuildingController.scan_floors()` reads the floor data and creates:

```gdscript
NavigationDTO.RoomRef.new(building_name, floor_index, code, position)
```

Where:

```text
building_name = logical building ID, e.g. "engineering"
code          = room node name, e.g. "405 422_Seminar Room 1"
position      = room node position converted to building-local coordinates
```

So the room node name becomes the lookup key.

This is why generated room nodes should preserve useful source identifiers in the node name.

## Room Node Naming Convention

Engineering and Kate use this style:

```text
<building-prefix> <room-number>_<category/name>
```

Examples:

```text
405 422_Seminar Room 1
402E 334_Digital Lab
315 033_Learning spaces_Study Room
```

Building3D currently has manifest records like:

```json
{
  "external_id": "405-422",
  "display_name": "Seminar Room",
  "floor_index": 3,
  "floor_name": "4",
  "anchor": [6.186981, 12.6, 8.934414]
}
```

The generator should convert that to a UNIMATE node name like:

```text
405 422_Seminar Room
```

Recommended room node naming rule:

```text
external_id "405-422" + display_name "Seminar Room"
-> "405 422_Seminar Room"
```

If the display name is empty or generic, still keep the source code:

```text
405 422
```

## Portal Node Contract

UNIMATE detects portals from room node names. A portal is a room marker whose name contains one of these words:

```text
stair
elevator
lift
door
entrance
```

The detected portal types are:

```text
stair    -> PortalRef.TYPE_STAIR
elevator -> PortalRef.TYPE_LIFT
lift     -> PortalRef.TYPE_LIFT
door     -> PortalRef.TYPE_DOOR
entrance -> PortalRef.TYPE_DOOR
```

### Vertical Connector Grouping

For stairs and elevators, the node name must include a `_SetX` suffix so UNIMATE can group the same connector across floors.

Examples:

```text
405 400E1_Elevator_SetE1
405 400E2_Elevator_SetE2
405 400S2_Stair 2_Set2
315 100S2_Stairs_Set2
Elevator_SetE10
Stair to L3_Set10
```

`BuildingController.gd` uses this regex:

```text
_Set(\w+)$
```

The captured value becomes `PortalRef.group_id`.

If a stair or elevator node does not have a `_SetX` suffix, it may be skipped as an ungroupable vertical connector.

### Recommended Generated Portal Names

For an elevator:

```text
external_id: 302-100E1
display_name: Elevator
group_id: E1

node name:
302 100E1_Elevator_SetE1
```

For a stair:

```text
external_id: 302-100S2
display_name: Stairs
group_id: S2

node name:
302 100S2_Stairs_Set2
```

For stairs, using `_Set2` matches the existing Kate/Engineering convention better than `_SetS2`. The generated manifest can still preserve `group_id = "S2"`; the Godot room node only needs a stable suffix that groups all floors of the same stair.

For doors:

```text
MainDoor
Door
North Entrance
302 G00_Main Entrance
```

Doors do not require `_SetX`.

## Why GLB Alone Is Not Enough

Building3D already exports visual GLB and nav GLB files. That is useful for model display, but UNIMATE currently uses room marker nodes for semantic behavior.

The GLB can show:

```text
- floor slabs
- room plates
- walls
- anchor markers
- portal markers
```

But UNIMATE also needs:

```text
- Node3D room markers under Floors/FloorX/Rooms
- node names that match room lookup/search conventions
- marker transforms at room/portal anchors
- FloorController nodes
- NavigationRegion3D nodes
- same logical building_id for grouped parts
```

Therefore, the generator should produce either:

```text
Option A: a complete Godot .tscn scene
Option B: a Godot loader script plus manifest that creates the same node tree at runtime
```

For UNIMATE, Option A is easier to inspect and debug. Option B is better if the scene must be regenerated frequently.

## DataStore Behavior

`DataStore.gd` registers:

```text
buildings: building_id -> BuildingInfo
rooms: room_id -> RoomRef
rooms_by_building: building_id -> Array[RoomRef]
rooms_by_code: room_code -> Array[RoomRef]
```

`DataStore.resolve_room()` primarily resolves exact room codes. It does not currently have a general alias system for building groups.

That is why Engineering has special-case timetable logic.

## Timetable Engineering Mapping

`TimetableList.gd` maps event room building data to known Godot buildings.

For Engineering, it recognizes:

```text
401
402
403
404
405
engineering
```

and resolves them to:

```text
engineering
```

This works for Engineering, but it should not be repeated manually for every grouped building. Science should be handled through generated group metadata.

## Recommended Grouping Model

Building3D should introduce a data-driven building group concept.

Suggested config:

```yaml
groups:
  - id: engineering
    display_name: Engineering
    members:
      - "401"
      - "402"
      - "402E"
      - "403"
      - "404"
      - "405"
    aliases:
      - engineering
      - eng
      - engineering building
      - "401"
      - "402"
      - "402E"
      - "403"
      - "404"
      - "405"

  - id: science
    display_name: Science Centre
    members:
      - "301"
      - "302"
      - "303"
      - "303S"
      - "305"
    aliases:
      - science
      - science centre
      - faculty of science
      - "301"
      - "302"
      - "303"
      - "303S"
      - "305"
```

Generated group manifest shape:

```json
{
  "schema_version": 2,
  "building": {
    "id": "science",
    "display_name": "Science Centre",
    "kind": "logical_group",
    "members": ["301", "302", "303", "303S", "305"],
    "aliases": ["science", "science centre", "301", "302", "303", "303S", "305"]
  },
  "rooms": [
    {
      "logical_building_id": "science",
      "source_building_admin_id": "302",
      "external_id": "302-101",
      "node_name": "302 101_Seminar Room",
      "display_name": "Seminar Room",
      "floor_index": 1,
      "floor_name": "1",
      "anchor": [12.3, 4.2, -8.1],
      "aliases": ["302-101", "302 101", "101", "SCIENCE 101"]
    }
  ],
  "portals": [
    {
      "logical_building_id": "science",
      "source_building_admin_id": "302",
      "external_id": "302-100E1",
      "node_name": "302 100E1_Elevator_SetE1",
      "kind": "elevator",
      "group_id": "E1",
      "floor_index": 1,
      "anchor": [3.0, 4.2, 9.2]
    }
  ]
}
```

## Recommended Generated Science Scene

Science should be generated as:

```text
Science
  script = BuildingController.gd
  building_name = "science"

  BuildingMesh
    science_visual.glb

  Floors
    Floor0
      script = FloorController.gd
      floor_index = 0
      floor_number = 0
      floor_name = "G"
      NavigationRegion3D
      Rooms
        301 G01_...
        302 G15_...
        303 G20_...
        303S G04_...
        305 G01_...
        302 G00E1_Elevator_SetE1
        303 G00S2_Stairs_Set2
        MainDoor

    Floor1
      script = FloorController.gd
      floor_index = 1
      floor_number = 1
      floor_name = "1"
      NavigationRegion3D
      Rooms
        301 101_...
        302 101_...
        303 101_...
```

All member rooms share:

```text
RoomRef.building_id = "science"
```

But their node names still reveal the source building:

```text
301 101_...
302 101_...
303 101_...
303S 101_...
305 101_...
```

## Coordinate Requirements

Generated room marker transforms should use the room or portal anchor in the same coordinate space as the floor scene.

Building3D currently stores local anchors like:

```json
"anchor": [6.186981, 12.6, 8.934414]
```

UNIMATE floor room nodes are usually placed relative to their floor node, with `y = 0` in most room markers. The floor node itself carries the vertical offset.

Recommended generation rule:

```text
floor_y = generated floor height
room global/local anchor = [x, floor_y, z]
room marker position under floor = [x, 0, z]
```

If the generated scene keeps all floor nodes at origin and puts absolute y on markers, then `BuildingController.scan_floors()` may still work, but it will be harder to match the existing Engineering/Kate pattern. The cleaner target is:

```text
Floor node position.y = floor height
Room marker position.y = 0
```

## Navigation Requirements

UNIMATE's current indoor navigation is not only a graph lookup. It uses Godot navigation regions and links for same-floor and inter-floor movement.

A generated building must provide:

```text
- NavigationRegion3D per floor
- a navigable floor mesh or navigation mesh resource
- vertical connector markers with stable _SetX group IDs
- generated NavigationLink3D nodes or enough portal data for BuildingController to rebuild them
```

Building3D's current `nav_glb` is a useful source, but UNIMATE will still need Godot `NavigationRegion3D` nodes in the scene.

## Design Decision for Grouped Buildings

There are two possible approaches.

### Approach 1: Normalize to One Logical Building ID

Generate grouped scenes like:

```text
science.tscn
building_name = "science"
RoomRef.building_id = "science"
```

This matches the Engineering pattern and requires the least routing change in UNIMATE.

Use this for V1.

### Approach 2: Keep Physical Building IDs and Add Group-Aware Routing

Generate separate scenes:

```text
301.tscn
302.tscn
303.tscn
303S.tscn
305.tscn
```

Then change `Navigator` so it knows that these buildings are equivalent under the `science` group.

This is more complex because same-building vs cross-building routing currently depends on direct `building_id` equality. It would require changes across `Navigator`, `DataStore`, building activation, labels, UI, and timetable resolution.

Do not use this for the first Science implementation.

## Required Building3D Changes

### 1. Add Building Group Config

Add a config file such as:

```text
configs/auckland_building_groups.yaml
```

It should define logical groups, members, aliases, and optional primary member.

### 2. Preserve Source and Logical IDs

Each generated record should preserve both:

```text
logical_building_id
source_building_admin_id
```

Example:

```json
{
  "logical_building_id": "science",
  "source_building_admin_id": "303S",
  "external_id": "303S-201"
}
```

### 3. Generate UNIMATE Node Names

Add a deterministic node-name builder:

```text
room external_id + display_name -> room node name
portal external_id + kind + group_id -> portal node name
```

Recommended examples:

```text
405-422 + Seminar Room -> 405 422_Seminar Room
302-100E1 + Elevator + E1 -> 302 100E1_Elevator_SetE1
302-100S2 + Stairs + S2 -> 302 100S2_Stairs_Set2
```

### 4. Generate a Godot Scene or Runtime Loader

The generator should output one of:

```text
science.tscn
```

or:

```text
science_manifest.json
GeneratedBuildingLoader.gd creates the node tree
```

For UNIMATE compatibility, the generated tree must include:

```text
BuildingController root
Floors
FloorController per floor
NavigationRegion3D per floor
Rooms per floor
Node3D room markers
Node3D portal markers
NavigationLinks
```

### 5. Export Alias Data

Building3D should export aliases so UNIMATE does not need more hard-coded functions like `_looks_like_engineering_building()`.

Example alias map:

```json
{
  "building_aliases": {
    "301": "science",
    "302": "science",
    "303": "science",
    "303s": "science",
    "305": "science",
    "science": "science",
    "science centre": "science"
  }
}
```

UNIMATE can then resolve timetable room `building = "302"` to logical building `science`.

## Required UNIMATE Changes Later

Do this only when the user explicitly asks to integrate into UNIMATE.

### 1. Add a Generated Building Alias Registry

Add an alias registry in or near `DataStore`:

```text
building_alias_to_id["302"] = "science"
building_alias_to_id["303S"] = "science"
building_alias_to_id["science centre"] = "science"
```

Then update:

```gdscript
DataStore.resolve_building_id(query)
```

to check aliases before falling back to exact ID/display-name matching.

### 2. Replace Hard-Coded Timetable Building Checks

Replace Engineering-only logic like:

```gdscript
_looks_like_engineering_building()
```

with data-driven alias resolution.

### 3. Keep Root Node Name and building_name Aligned

`BuildingMainController` uses `child.building_name.to_lower()` for active building registration.

`DataStoreIntegrator` currently derives a building ID from the node name in some paths. For generated buildings, keep these aligned:

```text
root node name: Science
building_name: science
DataStore building id: science
Navigator building id: science
```

If these diverge, search and routing may disagree.

## Acceptance Tests

### Building3D Tests

Add tests that verify:

```text
- group config loads
- science group contains 301, 302, 303, 303S, 305
- generated room records preserve source_building_admin_id
- generated room records set logical_building_id = science
- room node names match UNIMATE convention
- stair/elevator node names include _SetX
- group manifest includes aliases
```

### Generated Scene Tests

Verify the generated scene contains:

```text
Science
Science/Floors
Science/Floors/Floor0
Science/Floors/Floor0/NavigationRegion3D
Science/Floors/Floor0/Rooms
Science/NavigationLinks
```

And that `Rooms` contains nodes like:

```text
301 ...
302 ...
303 ...
303S ...
305 ...
..._Elevator_SetE1
..._Stairs_Set2
MainDoor or another entrance node
```

### UNIMATE Integration Tests Later

After integration into UNIMATE, verify:

```text
DataStore.resolve_building_id("302") == "science"
DataStore.resolve_building_id("Science Centre") == "science"
DataStore.get_rooms_in_building("science") contains 301/302/303/303S/305 rooms
DataStore.resolve_room("302 101_...") returns RoomRef.building_id == "science"
Navigator routes from 302 room to 303S room as same-building
Navigator routes from Engineering to Science as cross-building
Timetable event with backend room.building = "302" resolves to science
```

## Recommended Implementation Order

1. Add a group config format in Building3D.
2. Add group-aware manifest export.
3. Add UNIMATE node-name generation to manifest rooms/portals.
4. Generate `building_aliases` in the manifest or a separate JSON file.
5. Generate a `.tscn` or runtime loader output that creates room marker nodes.
6. Generate Science as one logical group package.
7. Only after that, integrate the generated Science package into UNIMATE.

## Final Recommendation

For grouped buildings, copy the Engineering pattern:

```text
One logical BuildingController.
Many physical room prefixes preserved in room node names.
All grouped rooms get the same RoomRef.building_id.
```

For Science, that means:

```text
logical building_id: science
members: 301, 302, 303, 303S, 305
room node examples:
  301 101_...
  302 101_...
  303 101_...
  303S 101_...
  305 G01_...
```

The most important generator upgrade is not visual geometry. It is generating the UNIMATE semantic scene contract:

```text
Floors -> FloorController -> Rooms -> Node3D room/portal markers
```

Without those room nodes, the model may display, but UNIMATE's search, labels, timetable routing, same-building routing, and vertical connector logic will not behave like Engineering.

## Building3D Implementation Completed

The Science group generator has now been implemented in Building3D without editing the UNIMATE checkout.

### New Generator Capabilities

```text
building3d group <group_id>
```

The command reads `configs/auckland_building_groups.yaml`, combines multiple MapsIndoors/admin building parts into one logical building, projects all member geometry into one shared coordinate space, and exports a UNIMATE-ready package.

For Science, the configured logical group is:

```text
id: science
display name: Science Centre
members: 301, 302, 303, 303S, 305
primary member/origin: 302
aliases: science, science centre, faculty of science, science building, 301, 302, 303, 303S, 305
```

### Generated Science Package

The current generated package is:

```text
exports/auckland/groups/science/
  science_visual.glb
  science_nav.glb
  science_manifest.json
  science_unimate.tscn
  README.md
```

It is generated with:

```bash
.venv/bin/python -m building3d group science --config configs/auckland.yaml --groups-config configs/auckland_building_groups.yaml --no-fetch
```

The generated manifest has:

```text
schema_version: 2
building.kind: logical_group
building.id: science
members: 301, 302, 303, 303S, 305
floors: 15
rooms: 1902
portals: 262
nav.regions: 15
nav.room_targets: 1902
nav.links: 243
```

Room distribution by source building:

```text
301: 473 rooms
302: 774 rooms
303: 460 rooms
303S: 184 rooms
305: 11 rooms
```

Generated floor stack:

```text
B-2, B-1, G, 1, 2, 3, 4, 5, 6, 7, 8, M8, 9, 10, 11
```

### UNIMATE Scene Contract Output

`science_unimate.tscn` generates:

```text
Science
  BuildingMesh
    Visual -> science_visual.glb
  Floors
    Floor0
      NavigationRegion3D
      Rooms
        301 ...
        302 ...
        303 ...
        303S ...
        305 ...
    ...
  NavigationLinks
  ClickArea3D
```

The scene currently includes:

```text
15 floor nodes
15 NavigationMesh subresources
2164 room/portal marker nodes
```

Example generated marker names:

```text
302 100E1_Elevator_SetE1
303S 175_Computer Lab-Science
305 G00C1_corridor
```

### Validation Results

The implementation was verified with:

```bash
python3 -m compileall building3d tests
.venv/bin/python -m pytest -q
.venv/bin/python -m building3d group science --config configs/auckland.yaml --groups-config configs/auckland_building_groups.yaml --no-fetch
git diff --check
```

Current test result:

```text
28 passed
```

Generated artifact validation:

```text
science_visual.glb: valid glTF 2.0 header, size 4,373,424 bytes
science_nav.glb: valid glTF 2.0 header, size 1,314,112 bytes
duplicate generated node names per floor: 0
missing anchors: 0
vertical connector names missing _Set suffix: 0
nav room target node-name mismatches: 0
nav link node-name mismatches: 0
```

Godot itself was not available on the local PATH during this Building3D pass, so the scene was validated by generated `.tscn` structure, subresource/reference consistency, manifest checks, GLB header parsing, and unit tests. The final in-engine load test should happen after the package is copied into UNIMATE.
