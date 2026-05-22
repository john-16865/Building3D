from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any

from building3d.geometry import MeshData, mesh_floor_name
from building3d.gltf import triangulate_faces

_NAV_PRIMARY_MATERIALS = {"floor"}
_NAV_FALLBACK_EXCLUDED_MATERIALS = {"wall", "wall_low", "anchor"}


def room_node_name(external_id: str, display_name: str = "") -> str:
    prefix = _external_id_to_room_prefix(external_id)
    suffix = _clean_node_text(display_name)
    return f"{prefix}_{suffix}" if suffix else prefix


def portal_node_name(external_id: str, display_name: str, kind: str, group_id: str = "") -> str:
    prefix = _external_id_to_room_prefix(external_id)
    kind_label = "Elevator" if kind == "elevator" else "Stairs" if kind == "stair" else _clean_node_text(display_name) or "Door"
    if kind in {"stair", "elevator"}:
        set_id = _portal_set_id(kind, group_id)
        return f"{prefix}_{kind_label}_Set{set_id}"
    return f"{prefix}_{kind_label}" if prefix else kind_label


def write_unimate_scene(
    manifest: dict[str, Any],
    output_path: str | Path,
    *,
    asset_base_path: str = "res://Assets/Buildings/Science",
    navigation_meshes: list[MeshData] | None = None,
    floor_visual_paths: dict[int, str] | None = None,
    floor_walkable_path_visual_paths: dict[int, str] | None = None,
) -> Path:
    path = Path(output_path)
    building = manifest.get("building", {})
    building_id = str(building.get("id", "building")).strip() or "building"
    root_name = _pascal_name(building_id)
    floors = sorted(manifest.get("floors", []), key=lambda item: int(item.get("floor_index", 0)))
    rooms_by_floor = _records_by_floor(manifest.get("rooms", []))
    portals_by_floor = _records_by_floor(manifest.get("portals", []))
    external_doors_by_floor = _records_by_floor(manifest.get("external_doors", []))
    required_nav_floors = _required_navigation_floors(rooms_by_floor, portals_by_floor, external_doors_by_floor)
    meshes = navigation_meshes or []
    nav_resources = _navigation_mesh_resources(meshes, floors, required_nav_floors)
    click_shapes = _click_shape_resources(meshes, floors)
    building_click_shape = _building_click_shape_resource(meshes)
    floor_visual_resources = _floor_visual_resources(floor_visual_paths or {}, floors)
    floor_walkable_path_visual_resources = _floor_walkable_path_visual_resources(
        floor_walkable_path_visual_paths or {},
        floors,
    )
    ext_resource_count = 2 + len(floor_visual_resources) + len(floor_walkable_path_visual_resources)
    sub_resource_count = len(nav_resources) + len(click_shapes) + (1 if building_click_shape else 0)
    lines = [
        f"[gd_scene load_steps={1 + ext_resource_count + sub_resource_count} format=4]",
        "",
        '[ext_resource type="Script" path="res://Scripts/map/BuildingController.gd" id="1_building"]',
        '[ext_resource type="Script" path="res://Scripts/map/FloorController.gd" id="2_floor"]',
    ]
    for resource in floor_visual_resources.values():
        lines.append(f'[ext_resource type="PackedScene" path="{_quote_attr(resource["path"])}" id="{resource["id"]}"]')
    for resource in floor_walkable_path_visual_resources.values():
        lines.append(f'[ext_resource type="PackedScene" path="{_quote_attr(resource["path"])}" id="{resource["id"]}"]')
    lines.append("")
    for resource in nav_resources.values():
        lines.extend(
            [
                f'[sub_resource type="NavigationMesh" id="{resource["id"]}"]',
                f'vertices = PackedVector3Array({_vector_array(resource["vertices"])})',
                f'polygons = [{_polygon_array(resource["polygons"])}]',
                "",
            ]
        )
    for resource in click_shapes.values():
        lines.extend(
            [
                f'[sub_resource type="BoxShape3D" id="{resource["id"]}"]',
                f'size = {_vector3(resource["size"])}',
                "",
            ]
        )
    if building_click_shape:
        lines.extend(
            [
                f'[sub_resource type="BoxShape3D" id="{building_click_shape["id"]}"]',
                f'size = {_vector3(building_click_shape["size"])}',
                "",
            ]
        )

    lines.extend(
        [
            f'[node name="{_quote_attr(root_name)}" type="Node3D"]',
            'script = ExtResource("1_building")',
            f'building_name = "{_quote_attr(building_id)}"',
            "",
            '[node name="test_node" type="Node3D" parent="."]',
            "",
            '[node name="BuildingMesh" type="Node3D" parent="."]',
            "",
            '[node name="Lid" type="Node3D" parent="BuildingMesh"]',
            "",
            '[node name="Visual" type="Node3D" parent="BuildingMesh"]',
            "visible = false",
            "",
            '[node name="Floors" type="Node3D" parent="."]',
        ]
    )
    for floor in floors:
        floor_index = int(floor.get("floor_index", 0))
        floor_name = str(floor.get("floor_name", floor_index))
        height = float(floor.get("height", 0.0))
        floor_node = f"Floor{floor_index}"
        floor_parent = f"Floors/{floor_node}"
        lines.extend(
            [
                "",
                f'[node name="{floor_node}" type="Node3D" parent="Floors"]',
                f"transform = {_transform(0.0, height, 0.0)}",
                'script = ExtResource("2_floor")',
                f'floor_name = "{_quote_attr(floor_name)}"',
                f"floor_index = {floor_index}",
                f"floor_number = {_floor_number(floor_name)}",
                "",
                f'[node name="NavigationRegion3D" type="NavigationRegion3D" parent="{floor_parent}"]',
            ]
        )
        nav_resource = nav_resources.get(floor_index)
        if nav_resource:
            lines.append(f'navigation_mesh = SubResource("{nav_resource["id"]}")')
            lines.append(f"navigation_layers = {_navigation_layer_for_floor(floor_index)}")
        lines.extend(
            [
                "",
                f'[node name="FloorMesh" type="Node3D" parent="{floor_parent}/NavigationRegion3D"]',
            ]
        )
        visual_resource = floor_visual_resources.get(floor_index)
        if visual_resource:
            lines.extend(
                [
                    "",
                    f'[node name="FloorVisual" parent="{floor_parent}/NavigationRegion3D/FloorMesh" instance=ExtResource("{visual_resource["id"]}")]',
                ]
            )
        walkable_path_visual_resource = floor_walkable_path_visual_resources.get(floor_index)
        if walkable_path_visual_resource:
            lines.extend(
                [
                    "",
                    f'[node name="WalkablePathVisual" parent="{floor_parent}/NavigationRegion3D/FloorMesh" instance=ExtResource("{walkable_path_visual_resource["id"]}")]',
                ]
            )
        lines.extend(
            [
                "",
                f'[node name="Rooms" type="Node3D" parent="{floor_parent}"]',
            ]
        )
        floor_records = _scene_records_for_floor(
            rooms_by_floor.get(floor_index, []),
            portals_by_floor.get(floor_index, []),
            external_doors_by_floor.get(floor_index, []),
        )
        for record, should_snap in sorted(floor_records, key=lambda item: _record_sort_key(item[0])):
            node_name = str(record.get("node_name") or record.get("external_id") or "Room")
            anchor = record.get("anchor")
            if not _valid_anchor(anchor):
                continue
            if should_snap:
                anchor = _snap_anchor_to_navigation_mesh(anchor, nav_resource)
            node_parent = f"{floor_parent}/Rooms"
            node_path = f"{node_parent}/{_quote_attr(node_name)}"
            lines.extend(
                [
                    "",
                    f'[node name="{_quote_attr(node_name)}" type="Node3D" parent="{node_parent}"]',
                    f"transform = {_transform(float(anchor[0]), 0.0, float(anchor[2]))}",
                ]
            )
            navigation_offset = _navigation_target_offset(record, anchor, fallback_to_anchor=not should_snap)
            if navigation_offset is not None:
                lines.extend(
                    [
                        "",
                        f'[node name="NavTarget" type="Node3D" parent="{node_path}"]',
                        f"transform = {_transform(navigation_offset[0], 0.0, navigation_offset[2])}",
                    ]
                )
        lines.extend(
            [
                "",
                f'[node name="ClickArea3D" type="Area3D" parent="{floor_parent}"]',
            ]
        )
        click_shape = click_shapes.get(floor_index)
        if click_shape:
            lines.extend(
                [
                    "",
                    f'[node name="CollisionShape3D" type="CollisionShape3D" parent="{floor_parent}/ClickArea3D"]',
                    f'transform = {_transform(*click_shape["center"])}',
                    f'shape = SubResource("{click_shape["id"]}")',
                ]
            )

    lines.extend(
        [
            "",
            '[node name="NavigationLinks" type="Node3D" parent="."]',
            "",
            '[node name="ClickArea3D" type="Area3D" parent="."]',
        ]
    )
    if building_click_shape:
        lines.extend(
            [
                "",
                '[node name="CollisionShape3D" type="CollisionShape3D" parent="ClickArea3D"]',
                f'transform = {_transform(*building_click_shape["center"])}',
                f'shape = SubResource("{building_click_shape["id"]}")',
            ]
        )
    lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _external_id_to_room_prefix(external_id: str) -> str:
    return _clean_node_text(str(external_id).replace("-", " "))


def _portal_set_id(kind: str, group_id: str) -> str:
    clean = _clean_node_text(group_id).upper()
    if kind == "stair" and clean.startswith("S") and len(clean) > 1:
        return clean[1:]
    return clean or "MAIN"


def _clean_node_text(value: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", str(value)).encode("ascii", "ignore").decode("ascii")
    ascii_text = ascii_text.replace("/", "-").replace("\\", "-").replace('"', "'")
    ascii_text = re.sub(r"[\x00-\x1f:]+", " ", ascii_text)
    ascii_text = re.sub(r"\s+", " ", ascii_text).strip()
    return ascii_text


def _quote_attr(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _pascal_name(value: str) -> str:
    parts = re.split(r"[^a-zA-Z0-9]+", value)
    return "".join(part.capitalize() for part in parts if part) or "Building"


def _records_by_floor(records: list[dict[str, Any]]) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault(int(record.get("floor_index", 0)), []).append(record)
    return grouped


def _record_sort_key(record: dict[str, Any]) -> tuple[str, str]:
    return (
        str(record.get("source_building_admin_id", "")),
        str(record.get("external_id") or record.get("entry_id") or record.get("node_name", "")),
    )


def _valid_anchor(anchor: Any) -> bool:
    return isinstance(anchor, list) and len(anchor) >= 3 and all(isinstance(value, int | float) for value in anchor[:3])


def _navigation_target_offset(record: dict[str, Any], anchor: Any, *, fallback_to_anchor: bool) -> tuple[float, float, float] | None:
    navigation_anchor = record.get("navigation_anchor")
    if _valid_anchor(navigation_anchor):
        offset_x = float(navigation_anchor[0]) - float(anchor[0])
        offset_z = float(navigation_anchor[2]) - float(anchor[2])
        if fallback_to_anchor or abs(offset_x) > 0.001 or abs(offset_z) > 0.001:
            return (offset_x, 0.0, offset_z)
        return None
    if fallback_to_anchor:
        return (0.0, 0.0, 0.0)
    return None


def _required_navigation_floors(
    rooms_by_floor: dict[int, list[dict[str, Any]]],
    portals_by_floor: dict[int, list[dict[str, Any]]],
    external_doors_by_floor: dict[int, list[dict[str, Any]]],
) -> set[int]:
    required: set[int] = set()
    for grouped in (rooms_by_floor, portals_by_floor, external_doors_by_floor):
        for floor_index, records in grouped.items():
            if records:
                required.add(int(floor_index))
    return required


def _scene_records_for_floor(
    rooms: list[dict[str, Any]],
    portals: list[dict[str, Any]],
    external_doors: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], bool]]:
    return [
        *((record, False) for record in rooms),
        *((record, True) for record in portals),
        *((record, True) for record in external_doors),
    ]


def _navigation_mesh_resources(
    meshes: list[MeshData],
    floors: list[dict[str, Any]],
    required_floor_indexes: set[int] | None = None,
) -> dict[int, dict[str, Any]]:
    floor_index_by_name = {str(floor.get("floor_name", "")): int(floor.get("floor_index", 0)) for floor in floors}
    floor_height_by_index = {int(floor.get("floor_index", 0)): float(floor.get("height", 0.0)) for floor in floors}
    resources = _collect_navigation_resources(meshes, floor_index_by_name, floor_height_by_index, _is_primary_nav_mesh)
    required_floor_indexes = required_floor_indexes or set()

    missing_required = {floor_index for floor_index in required_floor_indexes if not resources.get(floor_index, {}).get("polygons")}
    if missing_required:
        fallback_resources = _collect_navigation_resources(meshes, floor_index_by_name, floor_height_by_index, _is_fallback_nav_mesh)
        for floor_index in missing_required:
            if fallback_resources.get(floor_index, {}).get("polygons"):
                resources[floor_index] = fallback_resources[floor_index]

    still_missing = sorted(floor_index for floor_index in required_floor_indexes if not resources.get(floor_index, {}).get("polygons"))
    if still_missing:
        floor_labels = ", ".join(f"Floor {floor_index}" for floor_index in still_missing)
        raise ValueError(f"{floor_labels} has room/portal records but no walkable navigation mesh source.")

    return {
        int(floor.get("floor_index", 0)): resources[int(floor.get("floor_index", 0))]
        for floor in floors
        if int(floor.get("floor_index", 0)) in resources and resources[int(floor.get("floor_index", 0))]["polygons"]
    }


def _collect_navigation_resources(
    meshes: list[MeshData],
    floor_index_by_name: dict[str, int],
    floor_height_by_index: dict[int, float],
    include_mesh,
) -> dict[int, dict[str, Any]]:
    resources: dict[int, dict[str, Any]] = {}
    vertex_keys: dict[int, dict[tuple[float, float, float], int]] = {}
    for mesh in sorted(meshes, key=lambda item: item.name):
        if not include_mesh(mesh):
            continue
        floor_name = _floor_name_from_mesh_name(mesh.name)
        if floor_name not in floor_index_by_name:
            continue
        floor_index = floor_index_by_name[floor_name]
        floor_height = floor_height_by_index.get(floor_index, 0.0)
        resource = resources.setdefault(
            floor_index,
            {"id": _navigation_mesh_id(floor_index), "vertices": [], "polygons": []},
        )
        floor_vertex_keys = vertex_keys.setdefault(floor_index, {})
        top_faces = (
            _top_surface_polygons(mesh)
            if mesh.metadata.get("godot_nav_overlay") == "route_corridor_grid"
            else _top_surface_triangles(mesh)
        )
        for face in top_faces:
            polygon = []
            for source_index in face:
                vertex = mesh.vertices[source_index]
                local_vertex = _floor_local_vertex(vertex, floor_height, zero_y=True)
                if local_vertex not in floor_vertex_keys:
                    floor_vertex_keys[local_vertex] = len(resource["vertices"])
                    resource["vertices"].append(list(local_vertex))
                polygon.append(floor_vertex_keys[local_vertex])
            if len(set(polygon)) >= 3:
                resource["polygons"].append(_godot_navigation_polygon(polygon, resource["vertices"]))
    return resources


def _add_connected_floor_hulls(
    resources: dict[int, dict[str, Any]],
    meshes: list[MeshData],
    floor_index_by_name: dict[str, int],
    floor_height_by_index: dict[int, float],
    required_floor_indexes: set[int],
) -> None:
    points_by_floor: dict[int, set[tuple[float, float, float]]] = {}
    for mesh in meshes:
        if not (_is_primary_nav_mesh(mesh) or _is_fallback_nav_mesh(mesh)):
            continue
        floor_name = _floor_name_from_mesh_name(mesh.name)
        if floor_name not in floor_index_by_name:
            continue
        floor_index = floor_index_by_name[floor_name]
        if floor_index not in required_floor_indexes:
            continue
        floor_height = floor_height_by_index.get(floor_index, 0.0)
        for vertex in mesh.vertices:
            points_by_floor.setdefault(floor_index, set()).add(_floor_local_vertex(vertex, floor_height, zero_y=True))

    for floor_index, points in points_by_floor.items():
        resource = resources.get(floor_index)
        if not resource or not resource.get("polygons"):
            continue
        hull = _convex_hull_xz(sorted(points))
        if len(hull) < 3:
            continue
        start_index = len(resource["vertices"])
        resource["vertices"].extend([list(point) for point in hull])
        polygon = list(range(start_index, start_index + len(hull)))
        resource["polygons"].append(_godot_navigation_polygon(polygon, resource["vertices"]))


def _godot_navigation_polygon(polygon: list[int], vertices: list[list[float]]) -> list[int]:
    # NavigationServer3D indexes flat manually-authored NavigationMesh polygons
    # only when their X/Z winding faces the direction Godot expects.
    if _polygon_signed_area_xz(polygon, vertices) > 0.0:
        return list(reversed(polygon))
    return polygon


def _polygon_signed_area_xz(polygon: list[int], vertices: list[list[float]]) -> float:
    area = 0.0
    for offset, vertex_index in enumerate(polygon):
        next_index = polygon[(offset + 1) % len(polygon)]
        if vertex_index >= len(vertices) or next_index >= len(vertices):
            continue
        vertex = vertices[vertex_index]
        next_vertex = vertices[next_index]
        area += float(vertex[0]) * float(next_vertex[2]) - float(next_vertex[0]) * float(vertex[2])
    return area / 2.0


def _convex_hull_xz(points: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    unique: list[tuple[float, float, float]] = []
    seen: set[tuple[float, float]] = set()
    for point in points:
        key = (float(point[0]), float(point[2]))
        if key not in seen:
            seen.add(key)
            unique.append(point)
    if len(unique) <= 3:
        return unique

    sorted_points = sorted(unique, key=lambda point: (point[0], point[2]))

    def cross(origin, a, b) -> float:
        return (a[0] - origin[0]) * (b[2] - origin[2]) - (a[2] - origin[2]) * (b[0] - origin[0])

    lower: list[tuple[float, float, float]] = []
    for point in sorted_points:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)

    upper: list[tuple[float, float, float]] = []
    for point in reversed(sorted_points):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)

    return lower[:-1] + upper[:-1]


def _is_primary_nav_mesh(mesh: MeshData) -> bool:
    return mesh.material in _NAV_PRIMARY_MATERIALS


def _is_fallback_nav_mesh(mesh: MeshData) -> bool:
    if mesh.material in _NAV_FALLBACK_EXCLUDED_MATERIALS:
        return False
    if mesh.name.startswith("anchor__") or mesh.name.startswith("portal__"):
        return False
    if mesh.name.startswith("portal_walls__") or mesh.name.startswith("walls__"):
        return False
    return True


def _floor_name_from_mesh_name(name: str) -> str:
    return mesh_floor_name(name)


def _floor_visual_resources(paths: dict[int, str], floors: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
    valid_indexes = {int(floor.get("floor_index", 0)) for floor in floors}
    resources = {}
    for floor_index, path in sorted(paths.items(), key=lambda item: int(item[0])):
        index = int(floor_index)
        if index not in valid_indexes or not str(path).strip():
            continue
        resources[index] = {"id": _floor_visual_resource_id(index), "path": str(path)}
    return resources


def _floor_walkable_path_visual_resources(paths: dict[int, str], floors: list[dict[str, Any]]) -> dict[int, dict[str, str]]:
    valid_indexes = {int(floor.get("floor_index", 0)) for floor in floors}
    resources = {}
    for floor_index, path in sorted(paths.items(), key=lambda item: int(item[0])):
        index = int(floor_index)
        if index not in valid_indexes or not str(path).strip():
            continue
        resources[index] = {"id": _floor_walkable_path_visual_resource_id(index), "path": str(path)}
    return resources


def _click_shape_resources(meshes: list[MeshData], floors: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    floor_index_by_name = {str(floor.get("floor_name", "")): int(floor.get("floor_index", 0)) for floor in floors}
    floor_height_by_index = {int(floor.get("floor_index", 0)): float(floor.get("height", 0.0)) for floor in floors}
    bboxes: dict[int, list[float]] = {}
    for mesh in meshes:
        floor_name = mesh_floor_name(mesh.name)
        if floor_name not in floor_index_by_name:
            continue
        floor_index = floor_index_by_name[floor_name]
        floor_height = floor_height_by_index.get(floor_index, 0.0)
        for vertex in mesh.vertices:
            x, y, z = _floor_local_vertex(vertex, floor_height)
            _extend_bbox(bboxes, floor_index, x, y, z)
    return {
        floor_index: _shape_resource(_click_shape_id(floor_index), bbox)
        for floor_index, bbox in bboxes.items()
    }


def _building_click_shape_resource(meshes: list[MeshData]) -> dict[str, Any] | None:
    bboxes: dict[str, list[float]] = {}
    for mesh in meshes:
        if mesh.name.startswith("anchor__") or mesh.name.startswith("portal__"):
            continue
        for vertex in mesh.vertices:
            _extend_bbox(bboxes, "root", float(vertex[0]), float(vertex[1]), float(vertex[2]))
    bbox = bboxes.get("root")
    if not bbox:
        return None
    return _shape_resource("ClickShape_building", bbox)


def _extend_bbox(bboxes: dict[Any, list[float]], key: Any, x: float, y: float, z: float) -> None:
    bbox = bboxes.setdefault(key, [x, y, z, x, y, z])
    bbox[0] = min(bbox[0], x)
    bbox[1] = min(bbox[1], y)
    bbox[2] = min(bbox[2], z)
    bbox[3] = max(bbox[3], x)
    bbox[4] = max(bbox[4], y)
    bbox[5] = max(bbox[5], z)


def _shape_resource(resource_id: str, bbox: list[float]) -> dict[str, Any]:
    min_x, min_y, min_z, max_x, max_y, max_z = bbox
    size = [
        max(0.25, max_x - min_x + 1.0),
        max(0.25, max_y - min_y + 0.5),
        max(0.25, max_z - min_z + 1.0),
    ]
    center = [
        (min_x + max_x) / 2.0,
        (min_y + max_y) / 2.0,
        (min_z + max_z) / 2.0,
    ]
    return {"id": resource_id, "size": size, "center": center}


def _floor_visual_resource_id(floor_index: int) -> str:
    if floor_index < 0:
        return f"floor_visual_neg{abs(floor_index)}"
    return f"floor_visual_{floor_index}"


def _floor_walkable_path_visual_resource_id(floor_index: int) -> str:
    if floor_index < 0:
        return f"walkable_path_visual_neg{abs(floor_index)}"
    return f"walkable_path_visual_{floor_index}"


def _click_shape_id(floor_index: int) -> str:
    if floor_index < 0:
        return f"ClickShape_floor_neg{abs(floor_index)}"
    return f"ClickShape_floor_{floor_index}"


def _top_surface_triangles(mesh: MeshData) -> list[list[int]]:
    if not mesh.vertices:
        return []
    top_y = max(float(vertex[1]) for vertex in mesh.vertices)
    triangles: list[list[int]] = []
    for face in mesh.faces:
        face_vertices = [mesh.vertices[index] for index in face if 0 <= index < len(mesh.vertices)]
        if len(face_vertices) < 3:
            continue
        y_values = [float(vertex[1]) for vertex in face_vertices]
        if max(y_values) - min(y_values) > 0.0001:
            continue
        if abs(y_values[0] - top_y) > 0.0001:
            continue
        triangles.extend(triangulate_faces([face], mesh.vertices))
    return triangles


def _top_surface_polygons(mesh: MeshData) -> list[list[int]]:
    if not mesh.vertices:
        return []
    top_y = max(float(vertex[1]) for vertex in mesh.vertices)
    polygons: list[list[int]] = []
    for face in mesh.faces:
        face_vertices = [mesh.vertices[index] for index in face if 0 <= index < len(mesh.vertices)]
        if len(face_vertices) < 3:
            continue
        y_values = [float(vertex[1]) for vertex in face_vertices]
        if max(y_values) - min(y_values) > 0.0001:
            continue
        if abs(y_values[0] - top_y) > 0.0001:
            continue
        polygons.append(face)
    return polygons


def _snap_anchor_to_navigation_mesh(anchor: list[float], nav_resource: dict[str, Any] | None) -> list[float]:
    if not nav_resource or not nav_resource.get("vertices") or not nav_resource.get("polygons"):
        return anchor
    point = (float(anchor[0]), float(anchor[2]))
    best_point: tuple[float, float] | None = None
    best_distance_sq = float("inf")
    vertices = nav_resource["vertices"]
    for polygon in nav_resource["polygons"]:
        points = [(float(vertices[index][0]), float(vertices[index][2])) for index in polygon if 0 <= index < len(vertices)]
        if len(points) < 3:
            continue
        if _point_in_polygon_2d(point, points):
            return [float(anchor[0]), float(anchor[1]), float(anchor[2])]
        for index, start in enumerate(points):
            end = points[(index + 1) % len(points)]
            candidate = _closest_point_on_segment_2d(point, start, end)
            distance_sq = _distance_sq_2d(point, candidate)
            if distance_sq < best_distance_sq:
                best_distance_sq = distance_sq
                best_point = candidate
    if best_point is None:
        return anchor
    return [round(best_point[0], 6), float(anchor[1]), round(best_point[1], 6)]


def _point_in_polygon_2d(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    x, z = point
    inside = False
    previous_x, previous_z = polygon[-1]
    for current_x, current_z in polygon:
        cross = (x - previous_x) * (current_z - previous_z) - (z - previous_z) * (current_x - previous_x)
        if abs(cross) < 0.000001 and min(previous_x, current_x) - 0.000001 <= x <= max(previous_x, current_x) + 0.000001 and min(previous_z, current_z) - 0.000001 <= z <= max(previous_z, current_z) + 0.000001:
            return True
        intersects = (current_z > z) != (previous_z > z)
        if intersects:
            x_intersection = (previous_x - current_x) * (z - current_z) / (previous_z - current_z) + current_x
            if x <= x_intersection:
                inside = not inside
        previous_x, previous_z = current_x, current_z
    return inside


def _closest_point_on_segment_2d(
    point: tuple[float, float],
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[float, float]:
    px, pz = point
    ax, az = start
    bx, bz = end
    dx = bx - ax
    dz = bz - az
    length_sq = dx * dx + dz * dz
    if length_sq <= 0.000000001:
        return start
    t = max(0.0, min(1.0, ((px - ax) * dx + (pz - az) * dz) / length_sq))
    return ax + t * dx, az + t * dz


def _distance_sq_2d(a: tuple[float, float], b: tuple[float, float]) -> float:
    dx = a[0] - b[0]
    dz = a[1] - b[1]
    return dx * dx + dz * dz


def _floor_local_vertex(vertex: list[float], floor_height: float, *, zero_y: bool = False) -> tuple[float, float, float]:
    local_y = 0.0 if zero_y else round(float(vertex[1]) - floor_height, 6)
    if abs(local_y) < 0.001:
        local_y = 0.0
    return (round(float(vertex[0]), 6), local_y, round(float(vertex[2]), 6))


def _navigation_mesh_id(floor_index: int) -> str:
    if floor_index < 0:
        return f"NavigationMesh_floor_neg{abs(floor_index)}"
    return f"NavigationMesh_floor_{floor_index}"


def _navigation_layer_for_floor(floor_index: int) -> int:
    if 0 <= floor_index < 32:
        return 1 << floor_index
    return 1


def _vector_array(vertices: list[list[float]]) -> str:
    return ", ".join(_fmt(value) for vertex in vertices for value in vertex[:3])


def _polygon_array(polygons: list[list[int]]) -> str:
    return ", ".join(f"PackedInt32Array({', '.join(str(index) for index in polygon)})" for polygon in polygons)


def _floor_number(floor_name: str) -> int:
    clean = floor_name.strip().upper()
    if clean in {"G", "GROUND"}:
        return 0
    if clean == "B":
        return -1
    if clean.startswith("B-"):
        try:
            return -int(clean[2:])
        except ValueError:
            return 0
    if clean.startswith("M"):
        clean = clean[1:]
    try:
        return int(clean)
    except ValueError:
        return 0


def _transform(x: float, y: float, z: float) -> str:
    return "Transform3D(1, 0, 0, 0, 1, 0, 0, 0, 1, %s, %s, %s)" % (_fmt(x), _fmt(y), _fmt(z))


def _vector3(values: list[float]) -> str:
    return "Vector3(%s, %s, %s)" % (_fmt(values[0]), _fmt(values[1]), _fmt(values[2]))


def _fmt(value: float) -> str:
    rounded = round(float(value), 6)
    if abs(rounded) < 0.000001:
        rounded = 0.0
    return f"{rounded:.6f}".rstrip("0").rstrip(".") if "." in f"{rounded:.6f}" else str(rounded)
