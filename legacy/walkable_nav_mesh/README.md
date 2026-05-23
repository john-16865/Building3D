# Legacy Walkable Nav Mesh Code

This folder keeps a snapshot of the `building3d/groups.py` implementation from before route-debug output was reverted back to thin segment lines.

## Snapshot

- `groups_walkable_nav_mesh_legacy.py`

This is a full-file copy of the previous live `building3d/groups.py`.

It includes the route-derived walkable/navigation mesh generation code, including:

- `_complete_route_navigation_meshes`
- `_route_navigation_meshes_from_cache`
- `_route_navigation_meshes_with_stats_from_cache`
- `_write_floor_walkable_path_glbs`
- the merged/buffered route-debug centerline implementation that used polygon buffering

## Why It Is Here

The active pipeline should not use the generated walkable nav mesh as the final navigation source. That mesh was useful for debugging route connectivity, but the better Godot approach is:

1. remove the correct wall edges from the visual GLB
2. let Godot bake navigation from that wall-opened visual geometry
3. use route lines only as a visual debug overlay

The live `building3d/groups.py` route-debug writer now outputs thin route segment quads again. This legacy copy exists only as reference code in case the route-derived nav mesh path needs to be studied or recovered later.

Do not import this file from production code.
