from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any, Protocol

import requests
from shapely.geometry import Point, Polygon
from shapely.ops import nearest_points

from building3d.manifest import _is_linkable_vertical_group
from building3d.normalize import NormalizedDataset, PortalRecord
from building3d.unimate import portal_node_name


ROUTE_URL = "https://api.mapsindoors.com/auckland/api/directions/{graph_id}"
DEFAULT_GRAPH_ID = "CITY_CAMPUS_Graph"
MAX_CANDIDATE_HORIZONTAL_DISTANCE_M = 25.0
MAX_TRANSITION_PORTAL_DISTANCE_M = 2.5


@dataclass(frozen=True)
class RoutePoint:
    external_id: str
    source_id: str
    lon: float
    lat: float
    zlevel: float
    floor_index: int
    floor_name: str


@dataclass(frozen=True)
class _GeometryPoint:
    lon: float
    lat: float
    zlevel: float
    floor_name: str


@dataclass(frozen=True)
class _Transition:
    from_point: _GeometryPoint
    to_point: _GeometryPoint


class RouteClient(Protocol):
    def route(self, origin: RoutePoint, destination: RoutePoint) -> dict[str, Any]:
        ...


class MapsIndoorsRouteClient:
    def __init__(
        self,
        *,
        graph_id: str = DEFAULT_GRAPH_ID,
        cache_dir: str | Path | None = None,
        delay: float = 0.03,
    ) -> None:
        self.graph_id = graph_id
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.delay = delay
        self._last_request_at = 0.0
        self._headers = {"User-Agent": "Building3D route-derived vertical links/0.1"}
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def route(self, origin: RoutePoint, destination: RoutePoint) -> dict[str, Any]:
        origin_value = _point_param(origin)
        destination_value = _point_param(destination)
        cache_path = self._cache_path(origin_value, destination_value)
        if cache_path and cache_path.exists():
            try:
                return json.loads(cache_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                cache_path.unlink(missing_ok=True)

        wait = self.delay - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

        response = requests.get(
            ROUTE_URL.format(graph_id=self.graph_id),
            params={
                "origin": origin_value,
                "destination": destination_value,
                "mode": "WALKING",
                "lr": "en",
            },
            timeout=30,
            headers=self._headers,
        )
        if response.ok:
            data = response.json()
        else:
            try:
                data = response.json()
            except ValueError:
                data = {"message": response.text}
            data.setdefault("status", f"HTTP_{response.status_code}")
            data.setdefault("http_status", response.status_code)
        if cache_path:
            cache_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return data

    def _cache_path(self, origin_value: str, destination_value: str) -> Path | None:
        if not self.cache_dir:
            return None
        key = hashlib.sha1(f"{origin_value}|{destination_value}|{self.graph_id}".encode("utf-8")).hexdigest()
        return self.cache_dir / f"route_{key}.json"


def apply_route_derived_vertical_links(
    dataset: NormalizedDataset,
    manifest: dict[str, Any],
    *,
    route_client: RouteClient | None = None,
    cache_dir: str | Path | None = None,
    graph_id: str = DEFAULT_GRAPH_ID,
) -> dict[str, Any]:
    client = route_client or MapsIndoorsRouteClient(graph_id=graph_id, cache_dir=cache_dir)
    unknown_portals = [
        portal
        for portal in dataset.portals
        if portal.kind in {"elevator", "stair"}
        and not _is_linkable_vertical_group(portal.group_id)
        and portal.anchor_lonlat
    ]

    stats: dict[str, Any] = {
        "graph_id": graph_id,
        "candidates": 0,
        "accepted": 0,
        "rejected": 0,
        "links": [],
        "rejections": [],
    }
    # Portals grouped by a previous derivation are re-linked from their existing
    # group ids alone, so a rebuild never needs to hit MapsIndoors again. Captured
    # before the API pass so freshly assigned groups are not double counted.
    pre_derived_edges = _already_derived_vertical_edges(dataset)

    accepted_edges: list[dict[str, Any]] = []
    for start, end in _candidate_pairs(unknown_portals):
        stats["candidates"] += 1
        origin = _route_point(start)
        destination = _route_point(end)
        try:
            route = client.route(origin, destination)
        except Exception as exc:  # keep generation moving if MapsIndoors is unreachable
            _record_rejection(stats, start, end, "request_failed", str(exc))
            continue
        transition = _matching_direct_transition(route, start, end)
        if transition is None:
            _record_rejection(stats, start, end, str(route.get("status", "rejected")), "no direct transition at both portal polygons")
            continue
        accepted_edges.append({"start": start, "end": end, "transition": transition})

    if not accepted_edges and not pre_derived_edges:
        _write_manifest_summary(manifest, stats)
        return stats

    group_ids = _assign_component_group_ids(accepted_edges) if accepted_edges else {}
    portals_by_source_id = {portal.source_id: portal for portal in dataset.portals}
    manifest_portals_by_source_id = {
        str(portal.get("source_id")): portal
        for portal in manifest.get("portals", [])
        if portal.get("source_id")
    }
    for source_id, group_id in group_ids.items():
        portal = portals_by_source_id.get(source_id)
        manifest_portal = manifest_portals_by_source_id.get(source_id)
        if portal is not None:
            portal.group_id = group_id
            portal.source_properties["vertical_group_source"] = "mapsindoors_route_graph"
            portal.source_properties["vertical_group_confidence"] = "high"
        if manifest_portal is not None:
            manifest_portal["group_id"] = group_id
            manifest_portal["node_name"] = portal_node_name(
                str(manifest_portal.get("external_id", "")),
                str(manifest_portal.get("display_name", "")),
                str(manifest_portal.get("kind", "")),
                group_id,
            )

    links = manifest.setdefault("nav", {}).setdefault("links", [])
    existing = _existing_vertical_edge_keys(links)
    combined_edges = [
        (edge["start"], edge["end"], group_ids[edge["start"].source_id], edge["transition"])
        for edge in accepted_edges
    ] + [
        (edge["start"], edge["end"], edge["group_id"], edge["transition"])
        for edge in pre_derived_edges
    ]
    for start, end, group_id, transition in combined_edges:
        key = _edge_key(start.kind, start.source_id, end.source_id)
        reverse_key = _edge_key(start.kind, end.source_id, start.source_id)
        if key in existing or reverse_key in existing:
            continue
        existing.add(key)
        start_manifest = manifest_portals_by_source_id.get(start.source_id, {})
        end_manifest = manifest_portals_by_source_id.get(end.source_id, {})
        links.append(_build_vertical_link(start, end, group_id, transition, start_manifest, end_manifest))
        stats["accepted"] += 1
        stats["links"].append(
            {
                "group_id": group_id,
                "from_external_id": start.external_id,
                "to_external_id": end.external_id,
                "kind": start.kind,
                "source_building_admin_id": start.building_admin_id,
            }
        )

    _write_manifest_summary(manifest, stats)
    return stats


def _already_derived_vertical_edges(dataset: NormalizedDataset) -> list[dict[str, Any]]:
    """Vertical edges for portals already grouped by a prior route derivation.

    Rebuilds the floor-to-floor links from the portals' existing route-derived
    group ids so a regeneration keeps the same links without any API calls.
    """
    marked = [
        portal
        for portal in dataset.portals
        if portal.kind in {"elevator", "stair"}
        and str((portal.source_properties or {}).get("vertical_group_source", "")) == "mapsindoors_route_graph"
        and _is_linkable_vertical_group(portal.group_id)
    ]
    by_group: dict[str, list[PortalRecord]] = {}
    for portal in marked:
        by_group.setdefault(str(portal.group_id), []).append(portal)
    edges: list[dict[str, Any]] = []
    for _group_id, portals in sorted(by_group.items()):
        per_floor: dict[int, PortalRecord] = {}
        for portal in sorted(portals, key=lambda item: (str(item.external_id), str(item.source_id))):
            per_floor.setdefault(int(portal.floor_index), portal)
        ordered = [per_floor[index] for index in sorted(per_floor)]
        for lower, upper in zip(ordered, ordered[1:]):
            if lower.floor_index == upper.floor_index:
                continue
            edges.append({"start": lower, "end": upper, "group_id": str(lower.group_id), "transition": None})
    return edges


def _build_vertical_link(
    start: PortalRecord,
    end: PortalRecord,
    group_id: str,
    transition: _Transition | None,
    start_manifest: dict[str, Any],
    end_manifest: dict[str, Any],
) -> dict[str, Any]:
    link: dict[str, Any] = {
        "kind": start.kind,
        "group_id": group_id,
        "source": "mapsindoors_route_graph",
        "confidence": "high",
        "source_building_admin_id": start.building_admin_id,
        "from_external_id": start.external_id,
        "to_external_id": end.external_id,
        "from_source_id": start.source_id,
        "to_source_id": end.source_id,
        "from_node_name": start_manifest.get("node_name", ""),
        "to_node_name": end_manifest.get("node_name", ""),
        "from_floor_index": start.floor_index,
        "to_floor_index": end.floor_index,
        "from_anchor": start.anchor_local,
        "to_anchor": end.anchor_local,
        "from_source_floor": _source_floor(start),
        "to_source_floor": _source_floor(end),
        "bidirectional": True,
    }
    if transition is not None:
        link["transition"] = {
            "from": [transition.from_point.lon, transition.from_point.lat, transition.from_point.zlevel],
            "to": [transition.to_point.lon, transition.to_point.lat, transition.to_point.zlevel],
        }
    return link


def _candidate_pairs(portals: list[PortalRecord]) -> list[tuple[PortalRecord, PortalRecord]]:
    candidates: list[tuple[PortalRecord, PortalRecord]] = []
    ordered = sorted(portals, key=lambda portal: (portal.building_admin_id, portal.kind, portal.floor_index, portal.external_id))
    for index, start in enumerate(ordered):
        for end in ordered[index + 1:]:
            if start.building_admin_id != end.building_admin_id:
                continue
            if start.kind != end.kind:
                continue
            if start.floor_index == end.floor_index:
                continue
            if _portal_distance_m(start, end) > MAX_CANDIDATE_HORIZONTAL_DISTANCE_M:
                continue
            lower, upper = (start, end) if start.floor_index < end.floor_index else (end, start)
            candidates.append((lower, upper))
    return candidates


def _route_point(portal: PortalRecord) -> RoutePoint:
    anchor = portal.anchor_lonlat or [0.0, 0.0]
    return RoutePoint(
        external_id=portal.external_id,
        source_id=portal.source_id,
        lon=float(anchor[0]),
        lat=float(anchor[1]),
        zlevel=_source_floor(portal),
        floor_index=portal.floor_index,
        floor_name=portal.floor_name,
    )


def _matching_direct_transition(route: dict[str, Any], start: PortalRecord, end: PortalRecord) -> _Transition | None:
    if str(route.get("status", "")).upper() != "OK" or not route.get("routes"):
        return None
    start_floor = _source_floor(start)
    end_floor = _source_floor(end)
    for transition in _vertical_transitions(route):
        if abs(transition.from_point.zlevel - start_floor) > 0.001:
            continue
        if abs(transition.to_point.zlevel - end_floor) > 0.001:
            continue
        start_distance = _distance_from_transition_to_portal_m(transition.from_point, start)
        end_distance = _distance_from_transition_to_portal_m(transition.to_point, end)
        if start_distance <= MAX_TRANSITION_PORTAL_DISTANCE_M and end_distance <= MAX_TRANSITION_PORTAL_DISTANCE_M:
            return transition
    return None


def _vertical_transitions(route: dict[str, Any]) -> list[_Transition]:
    transitions: list[_Transition] = []
    previous: _GeometryPoint | None = None
    for route_item in route.get("routes", []):
        for leg in route_item.get("legs", []):
            for step in leg.get("steps", []):
                for point in _step_geometry_points(step):
                    if previous is not None and abs(previous.zlevel - point.zlevel) > 0.001:
                        transitions.append(_Transition(previous, point))
                    previous = point
    return transitions


def _step_geometry_points(step: dict[str, Any]) -> list[_GeometryPoint]:
    raw_points = step.get("geometry") or []
    if not raw_points:
        raw_points = [step.get("start_location"), step.get("end_location")]
    points: list[_GeometryPoint] = []
    for item in raw_points:
        if not isinstance(item, dict):
            continue
        try:
            point = _GeometryPoint(
                lon=float(item.get("lng")),
                lat=float(item.get("lat")),
                zlevel=float(item.get("zLevel")),
                floor_name=str(item.get("floor_name", "")),
            )
        except (TypeError, ValueError):
            continue
        if not points or (points[-1].lon, points[-1].lat, points[-1].zlevel) != (point.lon, point.lat, point.zlevel):
            points.append(point)
    return points


def _assign_component_group_ids(accepted_edges: list[dict[str, Any]]) -> dict[str, str]:
    parent: dict[str, str] = {}
    portal_by_source: dict[str, PortalRecord] = {}

    def find(source_id: str) -> str:
        parent.setdefault(source_id, source_id)
        if parent[source_id] != source_id:
            parent[source_id] = find(parent[source_id])
        return parent[source_id]

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for edge in accepted_edges:
        start = edge["start"]
        end = edge["end"]
        portal_by_source[start.source_id] = start
        portal_by_source[end.source_id] = end
        union(start.source_id, end.source_id)

    components: dict[str, list[PortalRecord]] = {}
    for source_id, portal in portal_by_source.items():
        components.setdefault(find(source_id), []).append(portal)

    group_ids: dict[str, str] = {}
    ordered_components = sorted(
        components.values(),
        key=lambda portals: (
            portals[0].building_admin_id,
            portals[0].kind,
            min(portal.floor_index for portal in portals),
            sorted(portal.external_id for portal in portals)[0],
        ),
    )
    counters: dict[tuple[str, str], int] = {}
    for portals in ordered_components:
        admin_id = portals[0].building_admin_id
        kind = portals[0].kind
        key = (admin_id, kind)
        counters[key] = counters.get(key, 0) + 1
        group_id = f"MI_{_safe_group_token(admin_id)}_{_kind_token(kind)}_{counters[key]:03d}"
        for portal in portals:
            group_ids[portal.source_id] = group_id
    return group_ids


def _existing_vertical_edge_keys(links: list[dict[str, Any]]) -> set[tuple[str, str, str]]:
    keys: set[tuple[str, str, str]] = set()
    for link in links:
        if not isinstance(link, dict):
            continue
        kind = str(link.get("kind", ""))
        if kind not in {"elevator", "stair"}:
            continue
        keys.add(_edge_key(kind, str(link.get("from_source_id", "")), str(link.get("to_source_id", ""))))
    return keys


def _edge_key(kind: str, from_source_id: str, to_source_id: str) -> tuple[str, str, str]:
    return (kind, from_source_id, to_source_id)


def _record_rejection(stats: dict[str, Any], start: PortalRecord, end: PortalRecord, status: str, reason: str) -> None:
    stats["rejected"] += 1
    stats["rejections"].append(
        {
            "from_external_id": start.external_id,
            "to_external_id": end.external_id,
            "kind": start.kind,
            "source_building_admin_id": start.building_admin_id,
            "status": status,
            "reason": reason,
        }
    )


def _write_manifest_summary(manifest: dict[str, Any], stats: dict[str, Any]) -> None:
    if stats["candidates"] == 0 and stats["accepted"] == 0:
        return
    manifest.setdefault("nav", {})["vertical_route_derivation"] = {
        "graph_id": stats["graph_id"],
        "candidates": stats["candidates"],
        "accepted": stats["accepted"],
        "rejected": stats["rejected"],
    }


def _point_param(point: RoutePoint) -> str:
    return f"{point.lat:.12f},{point.lon:.12f},{point.zlevel:g}"


def _source_floor(portal: PortalRecord) -> float:
    value = (portal.source_properties or {}).get("floor")
    if value is None:
        value = portal.floor_name
    try:
        return float(value)
    except (TypeError, ValueError):
        text = str(value).strip().upper()
        if text in {"G", "GROUND"}:
            return 0.0
        if text.startswith("M") and text[1:].isdigit():
            return float(text[1:]) * 10.0 + 5.0
        return float(portal.floor_index)


def _portal_distance_m(start: PortalRecord, end: PortalRecord) -> float:
    if start.anchor_local and end.anchor_local and len(start.anchor_local) >= 3 and len(end.anchor_local) >= 3:
        return math.dist(
            [float(start.anchor_local[0]), float(start.anchor_local[2])],
            [float(end.anchor_local[0]), float(end.anchor_local[2])],
        )
    if start.anchor_lonlat and end.anchor_lonlat:
        return _approx_distance_m(
            Point(float(start.anchor_lonlat[0]), float(start.anchor_lonlat[1])),
            Point(float(end.anchor_lonlat[0]), float(end.anchor_lonlat[1])),
        )
    return float("inf")


def _distance_from_transition_to_portal_m(point: _GeometryPoint, portal: PortalRecord) -> float:
    transition_point = Point(point.lon, point.lat)
    polygon = _portal_polygon(portal)
    if polygon is not None:
        if polygon.contains(transition_point) or polygon.touches(transition_point):
            return 0.0
        nearest, _other = nearest_points(polygon, transition_point)
        return _approx_distance_m(nearest, transition_point)
    if portal.anchor_lonlat:
        return _approx_distance_m(
            transition_point,
            Point(float(portal.anchor_lonlat[0]), float(portal.anchor_lonlat[1])),
        )
    return float("inf")


def _portal_polygon(portal: PortalRecord) -> Polygon | None:
    if len(portal.polygon_lonlat) < 4:
        return None
    try:
        polygon = Polygon([(float(point[0]), float(point[1])) for point in portal.polygon_lonlat])
    except (TypeError, ValueError):
        return None
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    if polygon.is_empty or polygon.geom_type != "Polygon":
        return None
    return polygon


def _approx_distance_m(left: Point, right: Point) -> float:
    mean_lat = math.radians((left.y + right.y) / 2.0)
    dx = (right.x - left.x) * 111_320.0 * math.cos(mean_lat)
    dy = (right.y - left.y) * 110_540.0
    return math.hypot(dx, dy)


def _safe_group_token(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in str(value).upper()).strip("_") or "UNKNOWN"


def _kind_token(kind: str) -> str:
    return "ELEV" if kind == "elevator" else "STAIR" if kind == "stair" else _safe_group_token(kind)
