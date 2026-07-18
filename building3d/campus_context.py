"""All-campus context layer for UNIMATE's CampusMain scene.

Generates ONE lightweight GLB containing an extruded footprint for every
MapsIndoors building that is not already a placed UNIMATE building, plus an
index JSON with names/centroids for labels. The footprints are projected into
CampusMain placement space with the same reference calibration as campus
placement and campus paths, so the layer drops in under the Buildings-parent
transform and lines up with the placed buildings and generated roads.

This is intentionally NOT a per-building pipeline: a couple of hundred
footprint extrusions in a single GLB cost a handful of draw calls, no scripts,
no navigation - campus context should never contribute to frame time.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from building3d.campus_paths import CampusPathsConfig, project_to_campus
from building3d.config import BuildingGroupsConfig, SolutionConfig
from building3d.geometry import MeshData, build_floor_slab, build_wall_mesh
from building3d.gltf import write_glb

# Buildings that predate the publish pipeline but are placed in CampusMain by
# hand (kate = 315) or single-building publishes (business/OGGB = 260).
DEFAULT_EXCLUDED_ADMIN_IDS = {"260", "315"}
FLOOR_HEIGHT_M = 3.4
BASE_HEIGHT_M = 3.0
MAX_HEIGHT_M = 58.0
ROOF_THICKNESS_M = 0.4


def generate_campus_context(
    solution_config: SolutionConfig,
    cfg: CampusPathsConfig,
    groups: BuildingGroupsConfig | None = None,
    *,
    extra_excluded_admin_ids: set[str] | None = None,
) -> dict[str, Any]:
    buildings_path = solution_config.raw_root / "buildings.json"
    buildings = json.loads(buildings_path.read_text(encoding="utf-8"))
    if not isinstance(buildings, list):
        raise SystemExit(f"{buildings_path} must contain a list of buildings")

    excluded = set(DEFAULT_EXCLUDED_ADMIN_IDS)
    excluded.update(extra_excluded_admin_ids or set())
    for group in (groups.groups if groups else []):
        excluded.update(str(member).upper() for member in group.members)

    meshes: list[MeshData] = []
    index: list[dict[str, Any]] = []
    skipped_placed = 0
    skipped_geometry = 0
    for building in buildings:
        if not isinstance(building, dict):
            continue
        admin_id = str(building.get("administrativeId") or "").upper()
        if admin_id in excluded:
            skipped_placed += 1
            continue
        rings = _polygon_rings(building.get("geometry"))
        if not rings:
            skipped_geometry += 1
            continue

        floors = building.get("floors")
        floor_count = len(floors) if isinstance(floors, list) and floors else 1
        height = min(BASE_HEIGHT_M + floor_count * FLOOR_HEIGHT_M, MAX_HEIGHT_M) * cfg.reference.scale
        info = building.get("buildingInfo") if isinstance(building.get("buildingInfo"), dict) else {}
        display_name = str(info.get("name") or admin_id or "Building")
        slug = admin_id.lower() or str(building.get("id") or len(index))

        centroid_x = 0.0
        centroid_z = 0.0
        centroid_n = 0
        for ring_index, ring in enumerate(rings):
            projected = [_project_vertex(point, cfg) for point in ring]
            projected = [point for point in projected if point is not None]
            if len(projected) < 3:
                continue
            base_ring = [[x, 0.0, z] for x, z in projected]
            roof_ring = [[x, height, z] for x, z in projected]
            suffix = f"_{ring_index}" if ring_index else ""
            meshes.append(
                build_wall_mesh(
                    f"ctx__{slug}{suffix}__walls",
                    base_ring,
                    height=height,
                    material="campus_context",
                )
            )
            meshes.append(
                build_floor_slab(
                    f"ctx__{slug}{suffix}__roof",
                    roof_ring,
                    thickness=ROOF_THICKNESS_M * cfg.reference.scale,
                    material="campus_context_roof",
                )
            )
            for x, z in projected:
                centroid_x += x
                centroid_z += z
                centroid_n += 1
        if centroid_n == 0:
            skipped_geometry += 1
            continue
        index.append(
            {
                "admin_id": admin_id,
                "name": display_name,
                "floors": floor_count,
                "height": round(height, 2),
                "centroid": [round(centroid_x / centroid_n, 2), round(centroid_z / centroid_n, 2)],
            }
        )

    export_dir = solution_config.export_root / "campus_context"
    export_dir.mkdir(parents=True, exist_ok=True)
    glb_path = export_dir / "campus_context.glb"
    index_path = export_dir / "campus_context_index.json"
    write_glb(meshes, glb_path)
    index_path.write_text(
        json.dumps({"schema_version": 1, "buildings": index}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "export_dir": str(export_dir),
        "glb": str(glb_path),
        "index": str(index_path),
        "buildings": len(index),
        "skipped_placed": skipped_placed,
        "skipped_geometry": skipped_geometry,
        "meshes": len(meshes),
    }


def _polygon_rings(geometry: Any) -> list[list[list[float]]]:
    """Exterior rings of a GeoJSON Polygon/MultiPolygon as [lon, lat] lists."""
    if not isinstance(geometry, dict):
        return []
    geometry_type = geometry.get("type")
    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list):
        return []
    if geometry_type == "Polygon":
        polygons = [coordinates]
    elif geometry_type == "MultiPolygon":
        polygons = coordinates
    else:
        return []
    rings = []
    for polygon in polygons:
        if isinstance(polygon, list) and polygon and isinstance(polygon[0], list):
            exterior = polygon[0]
            ring = [point for point in exterior if isinstance(point, list) and len(point) >= 2]
            if len(ring) >= 4:
                rings.append(ring)
    return rings


def _project_vertex(point: list[float], cfg: CampusPathsConfig) -> tuple[float, float] | None:
    try:
        return project_to_campus(float(point[0]), float(point[1]), cfg.reference)
    except (TypeError, ValueError, IndexError):
        return None


def publish_campus_context_to_unimate(
    export_dir: str | Path,
    godot_dir: str | Path,
    *,
    campus_scene_rel: str = "Scene/Campus/CampusMain.tscn",
    node_name: str = "CampusContext",
) -> dict[str, Any]:
    """Copy the context GLB into Godot and text-wire it into CampusMain.

    Mirrors wire_campus_main: pure text insertion under the SubViewport with
    the Buildings node's transform, never re-packing the hand-made scene.
    """
    import shutil

    from building3d.unimate_publish import _unique_ext_resource_id

    export_path = Path(export_dir)
    godot_path = Path(godot_dir)
    asset_dir = godot_path / "Assets" / "Campus"
    asset_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for name in ("campus_context.glb", "campus_context_index.json"):
        source = export_path / name
        if not source.exists():
            raise FileNotFoundError(source)
        shutil.copy2(source, asset_dir / name)
        copied.append(asset_dir / name)

    scene_path = godot_path / campus_scene_rel
    text = scene_path.read_text(encoding="utf-8")
    context_res = "res://Assets/Campus/campus_context.glb"
    if context_res in text or f'[node name="{node_name}"' in text:
        return {"campus_main": str(scene_path), "changed": False, "copied": copied}

    import re

    buildings_match = re.search(
        r'\[node name="Buildings"[^\]]*parent="([^"]+)"[^\]]*\]\n(transform = [^\n]+)?',
        text,
    )
    if buildings_match is None:
        raise ValueError(f"{scene_path} has no Buildings node")
    subviewport_parent = buildings_match.group(1)
    transform_line = buildings_match.group(2) or "transform = Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0)"

    ext_id = _unique_ext_resource_id(text, "campus_context_glb")
    ext_line = f'[ext_resource type="PackedScene" path="{context_res}" id="{ext_id}"]'
    lines = text.split("\n")
    ext_indexes = [index for index, line in enumerate(lines) if line.startswith("[ext_resource")]
    lines.insert(ext_indexes[-1] + 1, ext_line)
    text = "\n".join(lines)

    node_block = (
        f'[node name="{node_name}" parent="{subviewport_parent}" instance=ExtResource("{ext_id}")]\n'
        f"{transform_line}\n"
    )
    # Insert right before the Buildings node so the context renders with the
    # same parent and transform but stays out of the Buildings subtree that
    # CampusMain scans for interactive buildings.
    buildings_header = text.find('[node name="Buildings"')
    text = text[:buildings_header] + node_block + "\n" + text[buildings_header:]
    scene_path.write_text(text, encoding="utf-8", newline="\n")
    return {"campus_main": str(scene_path), "changed": True, "copied": copied, "ext_id": ext_id}
