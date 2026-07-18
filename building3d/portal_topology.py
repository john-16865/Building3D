from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

from shapely.geometry import Point, Polygon
from shapely.ops import unary_union

from building3d.geometry import MeshData, mesh_floor_name
from building3d.unimate import portal_set_id


PORTAL_TOPOLOGY_SCHEMA_VERSION = 1
VERTICAL_MODES = {"stair", "elevator"}
UNKNOWN_GROUP_IDS = {"", "DEFAULT", "MAIN"}
ADJACENT_ROUTE_COMPONENT_MAX_GAP = 0.75
ADJACENT_ROUTE_COMPONENT_TERMINAL_MAX_DISTANCE = 5.0
ADJACENT_ROUTE_COMPONENT_PORTAL_TYPES = {"door", "lift", "portal", "stair"}


def build_portal_topology(
    manifest: dict[str, Any],
    route_navigation_meshes: list[MeshData],
    *,
    generated_at: str | None = None,
    source_hash: str | None = None,
) -> dict[str, Any]:
    building_id = _building_id(manifest)
    floors_by_index = _floors_by_index(manifest)
    terminals = _topology_terminals(manifest, building_id, floors_by_index)
    terminals_by_key = _terminals_by_record_key(terminals)
    components_by_floor = _route_components_by_floor(route_navigation_meshes, floors_by_index)

    same_floor_transfer_edges = _same_floor_transfer_edges(manifest, terminals, terminals_by_key, components_by_floor)
    vertical_edges = _vertical_edges(manifest, terminals_by_key)
    public_terminals = _public_terminals(terminals)
    validation_source_hash = source_hash or _source_hash(manifest, route_navigation_meshes)

    validation = {
        "building_id": building_id,
        "generated_at": generated_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "source_hash": validation_source_hash,
        "terminal_count": len(public_terminals),
        "transfer_edge_count": len(same_floor_transfer_edges),
        "same_floor_transfer_edge_count": len(same_floor_transfer_edges),
        "vertical_edge_count": len(vertical_edges),
        "edge_count": len(same_floor_transfer_edges) + len(vertical_edges),
        "transfer_edge_reason_counts": _edge_reason_counts(same_floor_transfer_edges),
        "route_component_floor_count": len(components_by_floor),
        "route_component_count": sum(len(components) for components in components_by_floor.values()),
    }

    return {
        "schema_version": PORTAL_TOPOLOGY_SCHEMA_VERSION,
        "building_id": building_id,
        "terminals": public_terminals,
        "transfer_edges": same_floor_transfer_edges,
        "same_floor_transfer_edges": same_floor_transfer_edges,
        "vertical_edges": vertical_edges,
        "validation": validation,
    }


def write_portal_topology(topology: dict[str, Any], path: str | Path) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(topology, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def _topology_terminals(
    manifest: dict[str, Any],
    building_id: str,
    floors_by_index: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    records = [
        *[("portal", record) for record in manifest.get("portals", []) if isinstance(record, dict)],
        *[("external_door", record) for record in manifest.get("external_doors", []) if isinstance(record, dict)],
    ]
    base_terminals: list[dict[str, Any]] = []
    for collection_name, record in records:
        anchor = record.get("anchor")
        if not _valid_anchor(anchor):
            continue
        # Ungrouped verticals are judged on the RAW group id: the member-scoped
        # set id below prefixes the admin id, which would mask "" / "DEFAULT".
        if _record_is_ungrouped_vertical(record, str(record.get("group_id") or "")):
            continue
        group_id = _topology_group_id(record)
        floor_index = int(record.get("floor_index", 0))
        floor = floors_by_index.get(floor_index, {})
        floor_name = str(record.get("floor_name") or floor.get("floor_name", ""))
        terminal = {
            "id": "",
            "building_id": building_id,
            "floor_index": floor_index,
            "floor_number": _floor_number(floor_name),
            "section": _section(record, building_id),
            "portal_name": str(record.get("node_name") or record.get("display_name") or record.get("external_id") or ""),
            "portal_type": _portal_type(record, collection_name),
            "group_id": group_id,
            "position_local": [float(anchor[0]), float(anchor[1]), float(anchor[2])],
            "_external_id": str(record.get("external_id") or record.get("entry_id") or ""),
            "_source_id": str(record.get("source_id") or record.get("entry_id") or record.get("external_id") or ""),
        }
        terminal["id"] = _terminal_id(terminal)
        base_terminals.append(terminal)

    id_counts: dict[str, int] = {}
    terminals = []
    for terminal in sorted(base_terminals, key=_terminal_sort_key):
        terminal_id = str(terminal["id"])
        id_counts[terminal_id] = id_counts.get(terminal_id, 0) + 1
        if id_counts[terminal_id] > 1:
            terminal["id"] = f"{terminal_id}:{_slug(str(terminal.get('_source_id', 'source')))}"
        terminals.append(terminal)
    return terminals


def _public_terminals(terminals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value for key, value in terminal.items() if not key.startswith("_")}
        for terminal in terminals
    ]


def _same_floor_transfer_edges(
    manifest: dict[str, Any],
    terminals: list[dict[str, Any]],
    terminals_by_key: dict[tuple[str, str, int], dict[str, Any]],
    components_by_floor: dict[int, list[Polygon]],
) -> list[dict[str, Any]]:
    terminals_by_floor: dict[int, list[tuple[dict[str, Any], int]]] = {}
    for terminal in terminals:
        floor_index = int(terminal.get("floor_index", 0))
        point = _point_from_position(terminal.get("position_local"))
        if point is None:
            continue
        component_index = _component_index(point, components_by_floor.get(floor_index, []))
        if component_index is None:
            continue
        terminals_by_floor.setdefault(floor_index, []).append((terminal, component_index))

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    _append_manifest_walk_transfer_edges(manifest, terminals_by_key, edges, seen)
    for floor_index, floor_terminals in sorted(terminals_by_floor.items()):
        for start, start_component in floor_terminals:
            for end, end_component in floor_terminals:
                if start is end or start_component != end_component:
                    continue
                _append_transfer_edge_once(
                    edges,
                    seen,
                    {
                        "from": str(start["id"]),
                        "to": str(end["id"]),
                        "floor_index": floor_index,
                        "mode": "walk",
                        "cost": _distance(start["position_local"], end["position_local"]),
                        "reason": "same_navmesh_component",
                    },
                )
        _append_adjacent_route_component_transfer_edges(
            edges,
            seen,
            floor_index,
            floor_terminals,
            components_by_floor.get(floor_index, []),
        )
    return sorted(edges, key=lambda edge: (int(edge["floor_index"]), str(edge["from"]), str(edge["to"])))


def _append_manifest_walk_transfer_edges(
    manifest: dict[str, Any],
    terminals_by_key: dict[tuple[str, str, int], dict[str, Any]],
    edges: list[dict[str, Any]],
    seen: set[tuple[str, str]],
) -> None:
    links = manifest.get("nav", {}).get("links", [])
    if not isinstance(links, list):
        return
    for link in links:
        if not isinstance(link, dict) or str(link.get("kind") or "") != "walk":
            continue
        from_floor = int(link.get("from_floor_index", -999))
        to_floor = int(link.get("to_floor_index", -999))
        if from_floor != to_floor:
            continue
        start = _find_terminal_for_link_endpoint(link, "from", terminals_by_key)
        end = _find_terminal_for_link_endpoint(link, "to", terminals_by_key)
        if not start or not end or start is end:
            continue
        cost = _walk_link_cost(link, start, end)
        edge = {
            "from": str(start["id"]),
            "to": str(end["id"]),
            "floor_index": from_floor,
            "mode": "walk",
            "cost": cost,
            "reason": "manifest_walk_link",
        }
        _append_transfer_edge_once(edges, seen, edge)
        if bool(link.get("bidirectional", True)):
            _append_transfer_edge_once(
                edges,
                seen,
                {
                    "from": str(end["id"]),
                    "to": str(start["id"]),
                    "floor_index": from_floor,
                    "mode": "walk",
                    "cost": cost,
                    "reason": "manifest_walk_link",
                },
            )


def _append_adjacent_route_component_transfer_edges(
    edges: list[dict[str, Any]],
    seen: set[tuple[str, str]],
    floor_index: int,
    floor_terminals: list[tuple[dict[str, Any], int]],
    components: list[Polygon],
) -> None:
    for start_index, (start, start_component_index) in enumerate(floor_terminals):
        for end, end_component_index in floor_terminals[start_index + 1 :]:
            if start_component_index == end_component_index:
                continue
            if not _eligible_adjacent_component_transfer(start, end):
                continue
            if _distance(start.get("position_local"), end.get("position_local")) > ADJACENT_ROUTE_COMPONENT_TERMINAL_MAX_DISTANCE:
                continue
            if not (0 <= start_component_index < len(components) and 0 <= end_component_index < len(components)):
                continue
            component_gap = components[start_component_index].distance(components[end_component_index])
            if component_gap > ADJACENT_ROUTE_COMPONENT_MAX_GAP:
                continue
            cost = _distance(start["position_local"], end["position_local"])
            for edge_start, edge_end in ((start, end), (end, start)):
                _append_transfer_edge_once(
                    edges,
                    seen,
                    {
                        "from": str(edge_start["id"]),
                        "to": str(edge_end["id"]),
                        "floor_index": floor_index,
                        "mode": "walk",
                        "cost": cost,
                        "reason": "adjacent_route_navmesh_components",
                        "component_gap": round(float(component_gap), 3),
                    },
                )


def _append_transfer_edge_once(edges: list[dict[str, Any]], seen: set[tuple[str, str]], edge: dict[str, Any]) -> None:
    key = (str(edge.get("from", "")), str(edge.get("to", "")))
    if key in seen:
        return
    seen.add(key)
    edges.append(edge)


def _vertical_edges(
    manifest: dict[str, Any],
    terminals_by_key: dict[tuple[str, str, int], dict[str, Any]],
) -> list[dict[str, Any]]:
    links = manifest.get("nav", {}).get("links", [])
    if not isinstance(links, list):
        return []

    edges: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for link in links:
        if not isinstance(link, dict):
            continue
        kind = str(link.get("kind") or "")
        if kind not in VERTICAL_MODES:
            continue
        raw_group_id = str(link.get("group_id") or "")
        if raw_group_id.strip().upper() in UNKNOWN_GROUP_IDS:
            continue
        start = _find_terminal_for_link_endpoint(link, "from", terminals_by_key)
        end = _find_terminal_for_link_endpoint(link, "to", terminals_by_key)
        if not start or not end:
            continue
        mode = _edge_mode(kind)
        group_id = _edge_group_id(kind, raw_group_id, str(link.get("from_external_id") or ""))
        cost = _vertical_cost(start, end)
        edge = {
            "from": str(start["id"]),
            "to": str(end["id"]),
            "mode": mode,
            "group_id": group_id,
            "cost": cost,
        }
        _append_edge_once(edges, seen, edge)
        if bool(link.get("bidirectional", True)):
            _append_edge_once(
                edges,
                seen,
                {
                    "from": str(end["id"]),
                    "to": str(start["id"]),
                    "mode": mode,
                    "group_id": group_id,
                    "cost": cost,
                },
            )
    return sorted(edges, key=lambda edge: (str(edge["from"]), str(edge["to"]), str(edge["mode"])))


def _append_edge_once(edges: list[dict[str, Any]], seen: set[tuple[str, str, str]], edge: dict[str, Any]) -> None:
    key = (str(edge.get("from", "")), str(edge.get("to", "")), str(edge.get("mode", "")))
    if key in seen:
        return
    seen.add(key)
    edges.append(edge)


def _walk_link_cost(link: dict[str, Any], start: dict[str, Any], end: dict[str, Any]) -> float:
    raw_distance = link.get("distance")
    if isinstance(raw_distance, int | float) and raw_distance >= 0:
        return round(float(raw_distance), 3)
    return _distance(start.get("position_local"), end.get("position_local"))


def _eligible_adjacent_component_transfer(start: dict[str, Any], end: dict[str, Any]) -> bool:
    if str(start.get("section", "")) != str(end.get("section", "")):
        return False
    start_type = str(start.get("portal_type", ""))
    end_type = str(end.get("portal_type", ""))
    if start_type not in ADJACENT_ROUTE_COMPONENT_PORTAL_TYPES or end_type not in ADJACENT_ROUTE_COMPONENT_PORTAL_TYPES:
        return False
    return True


def _edge_reason_counts(edges: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for edge in edges:
        reason = str(edge.get("reason") or "unknown")
        counts[reason] = counts.get(reason, 0) + 1
    return dict(sorted(counts.items()))


def _find_terminal_for_link_endpoint(
    link: dict[str, Any],
    prefix: str,
    terminals_by_key: dict[tuple[str, str, int], dict[str, Any]],
) -> dict[str, Any] | None:
    floor_index = int(link.get(f"{prefix}_floor_index", 0))
    candidates = [
        ("source_id", str(link.get(f"{prefix}_source_id") or "")),
        ("external_id", str(link.get(f"{prefix}_external_id") or "")),
        ("node_name", str(link.get(f"{prefix}_node_name") or "")),
    ]
    for key_name, value in candidates:
        if not value:
            continue
        terminal = terminals_by_key.get((key_name, value, floor_index))
        if terminal:
            return terminal
    return None


def _terminals_by_record_key(terminals: list[dict[str, Any]]) -> dict[tuple[str, str, int], dict[str, Any]]:
    index: dict[tuple[str, str, int], dict[str, Any]] = {}
    for terminal in terminals:
        floor_index = int(terminal.get("floor_index", 0))
        values = {
            "node_name": str(terminal.get("portal_name", "")),
            "source_id": str(terminal.get("_source_id", "")),
            "external_id": str(terminal.get("_external_id", "")),
        }
        for key_name, value in values.items():
            if value:
                index[(key_name, value, floor_index)] = terminal
    return index


def _route_components_by_floor(
    route_navigation_meshes: list[MeshData],
    floors_by_index: dict[int, dict[str, Any]],
) -> dict[int, list[Polygon]]:
    floor_index_by_name = {
        str(floor.get("floor_name", "")): floor_index
        for floor_index, floor in floors_by_index.items()
    }
    polygons_by_floor_name: dict[str, list[Polygon]] = {}
    for mesh in route_navigation_meshes:
        floor_name = mesh_floor_name(mesh.name)
        if not floor_name or floor_name not in floor_index_by_name:
            continue
        polygons_by_floor_name.setdefault(floor_name, []).extend(_mesh_polygons(mesh))

    components_by_floor: dict[int, list[Polygon]] = {}
    for floor_name, polygons in polygons_by_floor_name.items():
        valid = [polygon for polygon in polygons if not polygon.is_empty and polygon.area > 0.0001]
        if not valid:
            continue
        merged = unary_union(valid).buffer(0)
        components_by_floor[floor_index_by_name[floor_name]] = [
            polygon for polygon in _iter_polygons(merged) if not polygon.is_empty and polygon.area > 0.0001
        ]
    return components_by_floor


def _mesh_polygons(mesh: MeshData) -> list[Polygon]:
    polygons: list[Polygon] = []
    for face in mesh.faces:
        coords = []
        for vertex_index in face:
            if not (0 <= int(vertex_index) < len(mesh.vertices)):
                continue
            vertex = mesh.vertices[int(vertex_index)]
            if len(vertex) < 3:
                continue
            coords.append((float(vertex[0]), float(vertex[2])))
        if len(coords) < 3:
            continue
        polygon = Polygon(coords).buffer(0)
        if polygon.is_empty:
            continue
        polygons.extend(_iter_polygons(polygon))
    return polygons


def _iter_polygons(geometry: Any) -> list[Polygon]:
    if isinstance(geometry, Polygon):
        return [geometry]
    return [polygon for polygon in getattr(geometry, "geoms", []) if isinstance(polygon, Polygon)]


def _component_index(point: Point, components: list[Polygon]) -> int | None:
    for index, component in enumerate(components):
        if component.covers(point) or component.distance(point) <= 0.05:
            return index
    return None


def _floors_by_index(manifest: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {
        int(floor.get("floor_index", 0)): floor
        for floor in manifest.get("floors", [])
        if isinstance(floor, dict)
    }


def _building_id(manifest: dict[str, Any]) -> str:
    return str(manifest.get("building", {}).get("id") or "building")


def _terminal_id(terminal: dict[str, Any]) -> str:
    group_id = str(terminal.get("group_id") or "ungrouped")
    return ":".join(
        [
            _slug(str(terminal["building_id"])),
            f"F{int(terminal['floor_index'])}",
            _slug(str(terminal["section"])),
            _slug(str(terminal["portal_type"])),
            _slug(group_id),
            _slug(str(terminal.get("_external_id") or terminal.get("_source_id") or "terminal")),
        ]
    )


def _terminal_sort_key(terminal: dict[str, Any]) -> tuple[int, str, str, str, str, str]:
    return (
        int(terminal.get("floor_index", 0)),
        str(terminal.get("section", "")),
        str(terminal.get("portal_type", "")),
        str(terminal.get("group_id", "")),
        str(terminal.get("_external_id", "")),
        str(terminal.get("_source_id", "")),
    )


def _section(record: dict[str, Any], building_id: str) -> str:
    value = str(record.get("source_building_admin_id") or "").strip()
    if value:
        return value
    external_id = str(record.get("external_id") or record.get("entry_id") or "").strip()
    if "-" in external_id:
        return external_id.split("-", 1)[0].strip()
    return building_id


def _portal_type(record: dict[str, Any], collection_name: str) -> str:
    if collection_name == "external_door":
        return "door"
    kind = str(record.get("kind") or "").lower()
    return _edge_mode(kind) if kind else "portal"


def _record_is_ungrouped_vertical(record: dict[str, Any], group_id: str) -> bool:
    kind = str(record.get("kind") or "").lower()
    if kind not in VERTICAL_MODES:
        return False
    return str(group_id).strip().upper() in UNKNOWN_GROUP_IDS


def _topology_group_id(record: dict[str, Any]) -> str:
    """Terminal group id, identical to the node name's _Set suffix.

    BuildingController derives PortalRef.group_id from the _Set suffix and the
    topology matcher compares group ids exactly, so vertical connectors must
    use the same member-scoped set id (building3d.unimate.portal_set_id).
    Doors have no _Set suffix, so their raw (usually empty) group id stays.
    """
    kind = str(record.get("kind") or "").lower()
    group_id = str(record.get("group_id") or "")
    if kind not in VERTICAL_MODES:
        return group_id
    return portal_set_id(kind, group_id, str(record.get("external_id") or ""))


def _edge_group_id(kind: str, group_id: str, external_id: str = "") -> str:
    return portal_set_id(kind, group_id, external_id)


def _edge_mode(kind: str) -> str:
    return "lift" if kind == "elevator" else str(kind)


def _floor_number(floor_name: str) -> int:
    text = str(floor_name).strip().upper()
    if text == "G":
        return 0
    if text.startswith("B-"):
        try:
            return -int(text[2:])
        except ValueError:
            return 0
    if text.startswith("M") and text[1:].isdigit():
        return int(text[1:])
    try:
        return int(float(text))
    except ValueError:
        return 0


def _valid_anchor(anchor: Any) -> bool:
    return isinstance(anchor, list) and len(anchor) >= 3 and all(isinstance(value, int | float) for value in anchor[:3])


def _point_from_position(position: Any) -> Point | None:
    if not _valid_anchor(position):
        return None
    return Point(float(position[0]), float(position[2]))


def _distance(start: Any, end: Any) -> float:
    if not _valid_anchor(start) or not _valid_anchor(end):
        return 0.0
    return round(math.hypot(float(start[0]) - float(end[0]), float(start[2]) - float(end[2])), 3)


def _vertical_cost(start: dict[str, Any], end: dict[str, Any]) -> float:
    floor_delta = abs(int(start.get("floor_index", 0)) - int(end.get("floor_index", 0)))
    return round(max(1, floor_delta) * 20.0, 3)


def _slug(value: str) -> str:
    clean = re.sub(r"[^A-Za-z0-9]+", "_", str(value).strip()).strip("_")
    return clean or "unknown"


def _source_hash(manifest: dict[str, Any], route_navigation_meshes: list[MeshData]) -> str:
    payload = {
        "building": manifest.get("building", {}),
        "floors": manifest.get("floors", []),
        "portals": manifest.get("portals", []),
        "external_doors": manifest.get("external_doors", []),
        "vertical_links": [
            link
            for link in manifest.get("nav", {}).get("links", [])
            if isinstance(link, dict) and str(link.get("kind", "")) in VERTICAL_MODES
        ],
        "walk_links": [
            link
            for link in manifest.get("nav", {}).get("links", [])
            if isinstance(link, dict) and str(link.get("kind", "")) == "walk"
        ],
        "route_meshes": [
            {
                "name": mesh.name,
                "vertices": mesh.vertices,
                "faces": mesh.faces,
            }
            for mesh in route_navigation_meshes
        ],
    }
    stable = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(stable.encode("utf-8")).hexdigest()
