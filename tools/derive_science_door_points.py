#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import Any

import requests
from shapely.geometry import GeometryCollection, LineString, MultiPoint, Point, Polygon
from shapely.ops import nearest_points

from building3d.projection import LocalProjector


ROUTE_URL = "https://api.mapsindoors.com/auckland/api/directions/{graph_id}"
GRAPH_DETAILS_URL = "https://api.mapsindoors.com/auckland/api/directions/details/{graph_id}"

TARGETED_ROUTE_ENDPOINT_DOOR_OVERRIDES = {
    # MapsIndoors routes to the building 305 corridor terminate at the corridor
    # anchor, but the synthetic room polygon boundary is far from that endpoint.
    # Using the boundary creates an unreachable Godot nav target for this key link.
    ("305-400C1", "303S-400E4"),
}


@dataclass(frozen=True)
class RoutePoint:
    lon: float
    lat: float
    zlevel: float
    floor_name: str


@dataclass(frozen=True)
class OriginCandidate:
    external_id: str
    source_id: str
    kind: str
    display_name: str
    source_floor: float
    lon: float
    lat: float
    local: list[float] | None
    distance_m: float


class RouteClient:
    def __init__(self, graph_id: str, cache_dir: Path, delay: float) -> None:
        self.graph_id = graph_id
        self.cache_dir = cache_dir
        self.delay = delay
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._headers = {"User-Agent": "Building3D science door research/0.1"}
        self._lock = Lock()
        self._last_request_at = 0.0

    def route(self, origin: RoutePoint, destination: RoutePoint) -> dict[str, Any]:
        origin_value = _point_param(origin)
        destination_value = _point_param(destination)
        key = hashlib.sha1(f"{origin_value}|{destination_value}|{self.graph_id}".encode("utf-8")).hexdigest()
        cache_path = self.cache_dir / f"route_{key}.json"
        if cache_path.exists():
            try:
                return _read_json(cache_path)
            except json.JSONDecodeError:
                cache_path.unlink(missing_ok=True)

        url = ROUTE_URL.format(graph_id=self.graph_id)
        params = {
            "origin": origin_value,
            "destination": destination_value,
            "mode": "WALKING",
            "lr": "en",
        }
        with self._lock:
            now = time.monotonic()
            wait = self.delay - (now - self._last_request_at)
            if wait > 0:
                time.sleep(wait)
            self._last_request_at = time.monotonic()
        response = requests.get(url, params=params, timeout=30, headers=self._headers)
        response.raise_for_status()
        data = response.json()
        _write_json(cache_path, data)
        return data

    def graph_details(self) -> dict[str, Any]:
        cache_path = self.cache_dir / f"graph_details_{self.graph_id}.json"
        if cache_path.exists():
            try:
                return _read_json(cache_path)
            except json.JSONDecodeError:
                cache_path.unlink(missing_ok=True)
        response = requests.get(
            GRAPH_DETAILS_URL.format(graph_id=self.graph_id),
            params={"lr": "en"},
            timeout=30,
            headers=self._headers,
        )
        response.raise_for_status()
        data = response.json()
        _write_json(cache_path, data)
        return data


def main() -> int:
    parser = argparse.ArgumentParser(description="Derive Science Centre door points from MapsIndoors route geometry.")
    parser.add_argument("--dataset", default="data/processed/auckland/groups/science/dataset.json")
    parser.add_argument("--inventory", default="data/processed/auckland/inventory.json")
    parser.add_argument("--output-dir", default="exports/auckland/groups/science")
    parser.add_argument("--graph-id", default="CITY_CAMPUS_Graph")
    parser.add_argument("--primary-member", default="302")
    parser.add_argument("--max-rooms", type=int, default=0, help="Limit room targets for a quick probe. 0 means all.")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0.03, help="Minimum delay between uncached API requests.")
    parser.add_argument("--origin-tries", type=int, default=5)
    parser.add_argument("--skip-rooms", action="store_true")
    parser.add_argument("--skip-external", action="store_true")
    parser.add_argument("--entry-targets-per-member", type=int, default=3)
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    inventory_path = Path(args.inventory)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = output_dir / "door_route_cache"

    dataset = _read_json(dataset_path)
    inventory = _read_json(inventory_path)
    origin_lon, origin_lat = _group_origin(inventory, args.primary_member)
    projector = LocalProjector(origin_lon, origin_lat)
    client = RouteClient(args.graph_id, cache_dir, args.delay)

    rooms = [room for room in dataset.get("rooms", []) if _room_is_usable_target(room)]
    if args.max_rooms > 0:
        rooms = rooms[: args.max_rooms]
    origins = _origin_candidates(dataset)

    print(f"Science room targets: {len(rooms)}", flush=True)
    print(f"Origin candidates: {len(origins)}", flush=True)
    print(f"Route cache: {cache_dir}", flush=True)

    room_rows: list[dict[str, Any]] = []
    room_json = output_dir / "science_room_door_points_route_derived.json"
    if args.skip_rooms and room_json.exists():
        room_rows = _read_json(room_json)
    elif not args.skip_rooms:
        room_rows = _derive_room_doors(
            rooms=rooms,
            origins=origins,
            client=client,
            projector=projector,
            origin_tries=args.origin_tries,
            workers=max(1, args.workers),
        )
        _write_json(room_json, room_rows)
        _write_csv(output_dir / "science_room_door_points_route_derived.csv", room_rows)

    entry_rows: list[dict[str, Any]] = []
    entry_json = output_dir / "science_external_entry_points_route_derived.json"
    if args.skip_external and entry_json.exists():
        entry_rows = _read_json(entry_json)
    elif not args.skip_external:
        entry_rows = _derive_building_entries(dataset, client, projector, args.entry_targets_per_member)
        _write_json(entry_json, entry_rows)
        _write_csv(output_dir / "science_external_entry_points_route_derived.csv", entry_rows)
    if entry_rows:
        _write_json(output_dir / "external_doors.json", entry_rows)

    report_path = output_dir / "science_door_research.md"
    _write_report(report_path, dataset_path, args.graph_id, room_rows, entry_rows)
    if room_rows:
        print(f"Wrote {output_dir / 'science_room_door_points_route_derived.json'}", flush=True)
    if entry_rows:
        print(f"Wrote {output_dir / 'science_external_entry_points_route_derived.json'}", flush=True)
    print(f"Wrote {report_path}", flush=True)
    return 0


def _derive_room_doors(
    *,
    rooms: list[dict[str, Any]],
    origins: list[dict[str, Any]],
    client: RouteClient,
    projector: LocalProjector,
    origin_tries: int,
    workers: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    completed = 0
    started_at = time.monotonic()

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_derive_one_room_door, room, origins, client, projector, origin_tries): room
            for room in rooms
        }
        for future in as_completed(futures):
            completed += 1
            room = futures[future]
            try:
                rows.append(future.result())
            except Exception as exc:  # keep the full batch moving and record the failed room
                rows.append(_failed_room_row(room, f"exception: {exc}"))
            if completed == 1 or completed % 25 == 0 or completed == len(futures):
                elapsed = max(0.001, time.monotonic() - started_at)
                rate = completed / elapsed
                print(f"room doors: {completed}/{len(futures)} ({rate:.1f}/s)", flush=True)

    rows.sort(key=lambda item: (str(item.get("source_building_admin_id", "")), str(item.get("floor_name", "")), str(item.get("external_id", ""))))
    return rows


def _derive_one_room_door(
    room: dict[str, Any],
    origins: list[dict[str, Any]],
    client: RouteClient,
    projector: LocalProjector,
    origin_tries: int,
) -> dict[str, Any]:
    anchor = room.get("anchor_lonlat")
    if not anchor or len(anchor) < 2:
        return _failed_room_row(room, "missing room anchor")
    polygon = _polygon(room.get("polygon_lonlat") or [])
    if polygon is None:
        return _failed_room_row(room, "missing/invalid room polygon")

    source_floor = _source_floor(room)
    destination = RoutePoint(lon=float(anchor[0]), lat=float(anchor[1]), zlevel=source_floor, floor_name=str(room.get("floor_name", "")))
    candidates = _nearest_origins(room, origins, polygon, origin_tries)
    if not candidates:
        return _failed_room_row(room, "no same-floor origin candidate")

    attempts: list[dict[str, Any]] = []
    best_fallback: dict[str, Any] | None = None
    for candidate in candidates:
        origin = RoutePoint(
            lon=candidate.lon,
            lat=candidate.lat,
            zlevel=candidate.source_floor,
            floor_name=str(room.get("floor_name", "")),
        )
        try:
            route = client.route(origin, destination)
        except Exception as exc:
            attempts.append({"origin_external_id": candidate.external_id, "status": "request_failed", "note": str(exc)})
            continue
        status = str(route.get("status", "UNKNOWN"))
        if status != "OK" or not route.get("routes"):
            attempts.append({"origin_external_id": candidate.external_id, "status": status, "note": _route_error(route)})
            continue
        line_points = _last_floor_run(route["routes"][0], source_floor)
        if len(line_points) < 2:
            attempts.append({"origin_external_id": candidate.external_id, "status": status, "note": "no final same-floor route run"})
            continue
        line = LineString([(point.lon, point.lat) for point in line_points])
        door = _route_boundary_door(line, polygon)
        door = _targeted_route_endpoint_door_override(room, candidate, route, door)
        row = _room_row(
            room=room,
            candidate=candidate,
            route=route,
            route_status=status,
            projector=projector,
            door=door,
            source_floor=source_floor,
            attempts=attempts,
        )
        if row["door_source"] in {"route_boundary_intersection", "targeted_route_endpoint_override"}:
            return row
        if best_fallback is None or _confidence_rank(row["confidence"]) > _confidence_rank(best_fallback["confidence"]):
            best_fallback = row
        attempts.append({"origin_external_id": candidate.external_id, "status": status, "note": row["door_source"]})

    if best_fallback is not None:
        return best_fallback
    geometry_fallback = _geometry_fallback_row(room, origins, projector)
    if geometry_fallback is not None:
        geometry_fallback["attempts"] = attempts
        return geometry_fallback
    row = _failed_room_row(room, "all route attempts failed")
    row["attempts"] = attempts
    return row


def _targeted_route_endpoint_door_override(
    room: dict[str, Any],
    candidate: OriginCandidate,
    route: dict[str, Any],
    door: dict[str, Any],
) -> dict[str, Any]:
    key = (str(room.get("external_id", "")), candidate.external_id)
    if key not in TARGETED_ROUTE_ENDPOINT_DOOR_OVERRIDES:
        return door
    route_end = _route_end(route)
    if route_end is None:
        return door
    return {
        "point": Point(route_end.lon, route_end.lat),
        "source": "targeted_route_endpoint_override",
        "confidence": "high",
        "distance_to_route_end_m": 0.0,
    }


def _geometry_fallback_row(
    room: dict[str, Any],
    origins: list[dict[str, Any]],
    projector: LocalProjector,
) -> dict[str, Any] | None:
    room_poly = _polygon(room.get("polygon_lonlat") or [])
    if room_poly is None:
        return None
    source_floor = _source_floor(room)
    best: dict[str, Any] | None = None
    for origin in origins:
        if origin.get("external_id") == room.get("external_id"):
            continue
        if abs(_source_floor(origin) - source_floor) > 0.001:
            continue
        origin_poly = _polygon(origin.get("polygon_lonlat") or [])
        origin_anchor = origin.get("anchor_lonlat") or []
        if origin_poly is None and len(origin_anchor) < 2:
            continue

        shared = None if origin_poly is None else room_poly.boundary.intersection(origin_poly.boundary)
        point = _shared_boundary_midpoint(shared) if shared is not None and not shared.is_empty else None
        source = "geometry_shared_boundary_midpoint"
        confidence = "medium"
        distance_m = 0.0
        if point is None:
            other_geometry = origin_poly.boundary if origin_poly is not None else Point(float(origin_anchor[0]), float(origin_anchor[1]))
            room_point, other_point = nearest_points(room_poly.boundary, other_geometry)
            point = room_point
            distance_m = _approx_distance_m(room_point, other_point)
            source = "geometry_nearest_routeable_boundary"
            confidence = "medium" if distance_m <= 0.75 else "low"
        candidate = {
            "point": point,
            "origin": origin,
            "source": source,
            "confidence": confidence,
            "distance_m": distance_m,
        }
        if best is None or _geometry_fallback_rank(candidate) > _geometry_fallback_rank(best):
            best = candidate

    if best is None:
        return None
    point: Point = best["point"]
    origin = best["origin"]
    local = projector.to_local(point.x, point.y, _height(room))
    anchor = room.get("anchor_lonlat") or [None, None]
    return {
        "external_id": room.get("external_id", ""),
        "source_id": room.get("source_id", ""),
        "display_name": room.get("display_name", ""),
        "category": room.get("category", ""),
        "source_building_admin_id": room.get("building_admin_id", ""),
        "floor_name": room.get("floor_name", ""),
        "floor_index": room.get("floor_index"),
        "source_floor": source_floor,
        "anchor_lon": anchor[0],
        "anchor_lat": anchor[1],
        "door_lon": point.x,
        "door_lat": point.y,
        "door_local": local,
        "door_source": best["source"],
        "confidence": best["confidence"],
        "distance_door_to_routeable_geometry_m": round(float(best["distance_m"]), 3),
        "route_status": "No route found",
        "origin_external_id": origin.get("external_id", ""),
        "origin_source_id": origin.get("source_id", ""),
        "origin_kind": origin.get("origin_kind") or origin.get("kind") or origin.get("category", ""),
        "origin_display_name": origin.get("display_name", ""),
        "note": "MapsIndoors returned no route; point inferred from nearest routeable/circulation polygon geometry.",
    }


def _derive_building_entries(
    dataset: dict[str, Any],
    client: RouteClient,
    projector: LocalProjector,
    entry_targets_per_member: int,
) -> list[dict[str, Any]]:
    details = client.graph_details()
    entry_points = [
        RoutePoint(lon=float(item["coordinates"][0]), lat=float(item["coordinates"][1]), zlevel=float(item["coordinates"][2]), floor_name="0")
        for item in details.get("entryPoints", [])
        if isinstance(item, dict) and isinstance(item.get("coordinates"), list) and len(item["coordinates"]) >= 3
    ]
    targets = _entry_targets(dataset, entry_targets_per_member)
    observations: list[dict[str, Any]] = []
    print(f"external entry probe: {len(entry_points)} graph entry points x {len(targets)} targets", flush=True)
    for graph_entry in entry_points:
        for target in targets:
            anchor = target.get("anchor_lonlat") or []
            if len(anchor) < 2:
                continue
            source_floor = _source_floor(target)
            destination = RoutePoint(
                lon=float(anchor[0]),
                lat=float(anchor[1]),
                zlevel=source_floor,
                floor_name=str(target.get("floor_name", "")),
            )
            try:
                route = client.route(graph_entry, destination)
            except Exception as exc:
                observations.append({"status": "request_failed", "note": str(exc), "target_external_id": target.get("external_id")})
                continue
            if str(route.get("status", "")) != "OK" or not route.get("routes"):
                observations.append({"status": str(route.get("status", "")), "note": _route_error(route), "target_external_id": target.get("external_id")})
                continue
            for point in _outside_to_inside_points(route["routes"][0]):
                local = projector.to_local(point.lon, point.lat, 0.0)
                observations.append(
                    {
                        "status": "OK",
                        "lon": point.lon,
                        "lat": point.lat,
                        "source_floor": point.zlevel,
                        "floor_name": point.floor_name,
                        "local": local,
                        "target_external_id": target.get("external_id", ""),
                        "target_building_admin_id": target.get("building_admin_id", ""),
                        "target_floor_name": target.get("floor_name", ""),
                    }
                )
    clusters = _cluster_entry_observations([item for item in observations if item.get("status") == "OK"])
    failed = len([item for item in observations if item.get("status") != "OK"])
    print(f"external entry observations: {len(observations) - failed} OK, {failed} failed, {len(clusters)} clustered points", flush=True)
    return clusters


def _route_boundary_door(line: LineString, polygon: Polygon) -> dict[str, Any]:
    intersection = line.intersection(polygon.boundary)
    points = _intersection_points(intersection)
    if points:
        point = max(points, key=lambda item: line.project(item))
        return {
            "point": point,
            "source": "route_boundary_intersection",
            "confidence": "high",
            "distance_to_route_end_m": _approx_distance_m(point, Point(line.coords[-1])),
        }

    route_end = Point(line.coords[-1])
    if polygon.boundary.distance(route_end) < 0.000003:
        nearest_boundary, _ = nearest_points(polygon.boundary, route_end)
        return {
            "point": nearest_boundary,
            "source": "route_endpoint_on_boundary",
            "confidence": "high",
            "distance_to_route_end_m": _approx_distance_m(nearest_boundary, route_end),
        }

    nearest_boundary, _ = nearest_points(polygon.boundary, route_end)
    return {
        "point": nearest_boundary,
        "source": "nearest_boundary_to_route_endpoint",
        "confidence": "medium" if polygon.contains(route_end) else "low",
        "distance_to_route_end_m": _approx_distance_m(nearest_boundary, route_end),
    }


def _room_row(
    *,
    room: dict[str, Any],
    candidate: OriginCandidate,
    route: dict[str, Any],
    route_status: str,
    projector: LocalProjector,
    door: dict[str, Any],
    source_floor: float,
    attempts: list[dict[str, Any]],
) -> dict[str, Any]:
    point: Point = door["point"]
    height = _height(room)
    local = projector.to_local(point.x, point.y, height)
    anchor = room.get("anchor_lonlat") or [None, None]
    route_end = _route_end(route)
    return {
        "external_id": room.get("external_id", ""),
        "source_id": room.get("source_id", ""),
        "display_name": room.get("display_name", ""),
        "category": room.get("category", ""),
        "source_building_admin_id": room.get("building_admin_id", ""),
        "floor_name": room.get("floor_name", ""),
        "floor_index": room.get("floor_index"),
        "source_floor": source_floor,
        "anchor_lon": anchor[0],
        "anchor_lat": anchor[1],
        "door_lon": point.x,
        "door_lat": point.y,
        "door_local": local,
        "door_source": door["source"],
        "confidence": door["confidence"],
        "distance_door_to_route_end_m": round(float(door["distance_to_route_end_m"]), 3),
        "route_end_lon": route_end.lon if route_end else None,
        "route_end_lat": route_end.lat if route_end else None,
        "route_status": route_status,
        "origin_external_id": candidate.external_id,
        "origin_source_id": candidate.source_id,
        "origin_kind": candidate.kind,
        "origin_display_name": candidate.display_name,
        "origin_distance_m": round(candidate.distance_m, 3),
        "attempts": attempts,
    }


def _failed_room_row(room: dict[str, Any], note: str) -> dict[str, Any]:
    anchor = room.get("anchor_lonlat") or [None, None]
    return {
        "external_id": room.get("external_id", ""),
        "source_id": room.get("source_id", ""),
        "display_name": room.get("display_name", ""),
        "category": room.get("category", ""),
        "source_building_admin_id": room.get("building_admin_id", ""),
        "floor_name": room.get("floor_name", ""),
        "floor_index": room.get("floor_index"),
        "source_floor": _source_floor(room),
        "anchor_lon": anchor[0],
        "anchor_lat": anchor[1],
        "door_lon": None,
        "door_lat": None,
        "door_local": None,
        "door_source": "failed",
        "confidence": "none",
        "route_status": "failed",
        "note": note,
    }


def _origin_candidates(dataset: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for portal in dataset.get("portals", []):
        if portal.get("anchor_lonlat"):
            candidates.append({**portal, "origin_kind": f"portal:{portal.get('kind', '')}"})
    for room in dataset.get("rooms", []):
        if not room.get("anchor_lonlat"):
            continue
        text = f"{room.get('display_name', '')} {room.get('category', '')} {room.get('source_properties', {}).get('type', '')}".lower()
        if any(token in text for token in ("corridor", "circulation", "lobby", "foyer", "hallway", "lift lobby")):
            candidates.append({**room, "origin_kind": "circulation"})
    return candidates


def _nearest_origins(
    room: dict[str, Any],
    origins: list[dict[str, Any]],
    polygon: Polygon,
    limit: int,
) -> list[OriginCandidate]:
    room_anchor = room.get("anchor_lonlat") or []
    room_local = room.get("anchor_local") or []
    if len(room_anchor) < 2:
        return []
    source_floor = _source_floor(room)
    room_point = Point(float(room_anchor[0]), float(room_anchor[1]))
    candidates: list[OriginCandidate] = []
    for origin in origins:
        if origin.get("external_id") == room.get("external_id"):
            continue
        if abs(_source_floor(origin) - source_floor) > 0.001:
            continue
        anchor = origin.get("anchor_lonlat") or []
        if len(anchor) < 2:
            continue
        point = Point(float(anchor[0]), float(anchor[1]))
        if polygon.contains(point):
            continue
        local = origin.get("anchor_local")
        if isinstance(local, list) and len(local) >= 3 and isinstance(room_local, list) and len(room_local) >= 3:
            dist = math.dist([float(room_local[0]), float(room_local[2])], [float(local[0]), float(local[2])])
        else:
            dist = _approx_distance_m(room_point, point)
        candidates.append(
            OriginCandidate(
                external_id=str(origin.get("external_id", "")),
                source_id=str(origin.get("source_id", "")),
                kind=str(origin.get("origin_kind") or origin.get("kind") or origin.get("category") or ""),
                display_name=str(origin.get("display_name", "")),
                source_floor=_source_floor(origin),
                lon=float(anchor[0]),
                lat=float(anchor[1]),
                local=local if isinstance(local, list) else None,
                distance_m=float(dist),
            )
        )
    candidates.sort(key=lambda item: (item.distance_m, item.kind))
    return candidates[:limit]


def _entry_targets(dataset: dict[str, Any], per_member: int) -> list[dict[str, Any]]:
    ground = [
        room for room in dataset.get("rooms", [])
        if room.get("anchor_lonlat") and _source_floor(room) in {0.0, 10.0}
    ]
    by_member: dict[str, list[dict[str, Any]]] = {}
    for room in ground:
        by_member.setdefault(str(room.get("building_admin_id", "")), []).append(room)

    targets: list[dict[str, Any]] = []
    for member, rooms in sorted(by_member.items()):
        preferred = [
            room for room in rooms
            if any(token in f"{room.get('display_name', '')} {room.get('category', '')} {room.get('source_properties', {}).get('type', '')}".lower()
                   for token in ("lobby", "corridor", "circulation", "general facility", "lecture", "lab"))
        ] or rooms
        preferred.sort(key=lambda room: (str(room.get("floor_name", "")), str(room.get("external_id", ""))))
        step = max(1, len(preferred) // max(1, per_member))
        targets.extend(preferred[::step][:per_member])
    return targets


def _outside_to_inside_points(route: dict[str, Any]) -> list[RoutePoint]:
    points: list[RoutePoint] = []
    previous_abutters = ""
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            abutters = str(step.get("abutters") or "")
            if abutters == "InsideBuilding" and previous_abutters and previous_abutters != "InsideBuilding":
                location = step.get("start_location") or {}
                points.append(
                    RoutePoint(
                        lon=float(location.get("lng")),
                        lat=float(location.get("lat")),
                        zlevel=float(location.get("zLevel", 0.0)),
                        floor_name=str(location.get("floor_name", "")),
                    )
                )
            previous_abutters = abutters
    return points


def _cluster_entry_observations(observations: list[dict[str, Any]], radius_m: float = 1.2) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    for observation in observations:
        local = observation.get("local") or []
        if len(local) < 3:
            continue
        assigned = False
        for cluster in clusters:
            centre = cluster["_centre"]
            if math.dist([float(local[0]), float(local[2])], centre) <= radius_m:
                cluster["_items"].append(observation)
                xs = [float(item["local"][0]) for item in cluster["_items"]]
                zs = [float(item["local"][2]) for item in cluster["_items"]]
                cluster["_centre"] = [sum(xs) / len(xs), sum(zs) / len(zs)]
                assigned = True
                break
        if not assigned:
            clusters.append({"_centre": [float(local[0]), float(local[2])], "_items": [observation]})

    rows: list[dict[str, Any]] = []
    for index, cluster in enumerate(sorted(clusters, key=lambda item: (-len(item["_items"]), item["_centre"][0])), start=1):
        items = cluster["_items"]
        lon = sum(float(item["lon"]) for item in items) / len(items)
        lat = sum(float(item["lat"]) for item in items) / len(items)
        local = [
            sum(float(item["local"][0]) for item in items) / len(items),
            0.0,
            sum(float(item["local"][2]) for item in items) / len(items),
        ]
        rows.append(
            {
                "entry_id": f"science_entry_{index:03d}",
                "lon": round(lon, 12),
                "lat": round(lat, 12),
                "source_floor": _common_value(items, "source_floor"),
                "floor_name": _canonical_entry_floor_name(_common_value(items, "floor_name")),
                "floor_index": 2,
                "local": [round(value, 6) for value in local],
                "source": "route_abutters_outside_to_inside",
                "confidence": "high" if len(items) >= 2 else "medium",
                "supporting_routes": len(items),
                "target_building_admin_ids": sorted({str(item.get("target_building_admin_id", "")) for item in items if item.get("target_building_admin_id")}),
                "target_external_ids": sorted({str(item.get("target_external_id", "")) for item in items if item.get("target_external_id")})[:25],
            }
        )
    return rows


def _common_value(items: list[dict[str, Any]], key: str) -> Any:
    values = [item.get(key) for item in items if item.get(key) not in (None, "")]
    if not values:
        return None
    counts: dict[Any, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))[0][0]


def _canonical_entry_floor_name(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"", "0", "G", "GROUND"}:
        return "G"
    return text


def _last_floor_run(route: dict[str, Any], source_floor: float) -> list[RoutePoint]:
    current: list[RoutePoint] = []
    last_good: list[RoutePoint] = []
    for leg in route.get("legs", []):
        for step in leg.get("steps", []):
            for item in step.get("geometry") or []:
                try:
                    zlevel = float(item.get("zLevel"))
                    lon = float(item.get("lng"))
                    lat = float(item.get("lat"))
                except (TypeError, ValueError):
                    continue
                if abs(zlevel - source_floor) <= 0.001:
                    point = RoutePoint(lon=lon, lat=lat, zlevel=zlevel, floor_name=str(item.get("floor_name", "")))
                    if not current or (current[-1].lon, current[-1].lat) != (point.lon, point.lat):
                        current.append(point)
                else:
                    if len(current) >= 2:
                        last_good = current
                    current = []
    if len(current) >= 2:
        return current
    return last_good


def _intersection_points(geometry: Any) -> list[Point]:
    points: list[Point] = []
    if geometry.is_empty:
        return points
    if isinstance(geometry, Point):
        return [geometry]
    if isinstance(geometry, MultiPoint):
        return list(geometry.geoms)
    if isinstance(geometry, GeometryCollection):
        for item in geometry.geoms:
            points.extend(_intersection_points(item))
        return points
    if geometry.geom_type in {"LineString", "LinearRing"}:
        coords = list(geometry.coords)
        if coords:
            points.append(Point(coords[0]))
            points.append(Point(coords[-1]))
        return points
    if hasattr(geometry, "geoms"):
        for item in geometry.geoms:
            points.extend(_intersection_points(item))
    return points


def _shared_boundary_midpoint(geometry: Any) -> Point | None:
    if geometry is None or geometry.is_empty:
        return None
    if isinstance(geometry, Point):
        return geometry
    if isinstance(geometry, MultiPoint):
        points = list(geometry.geoms)
        return points[0] if points else None
    if geometry.geom_type in {"LineString", "LinearRing"}:
        if geometry.length == 0:
            coords = list(geometry.coords)
            return Point(coords[0]) if coords else None
        return geometry.interpolate(0.5, normalized=True)
    if isinstance(geometry, GeometryCollection) or hasattr(geometry, "geoms"):
        lines = [item for item in geometry.geoms if item.geom_type in {"LineString", "LinearRing"} and item.length > 0]
        if lines:
            line = max(lines, key=lambda item: item.length)
            return line.interpolate(0.5, normalized=True)
        points = []
        for item in geometry.geoms:
            point = _shared_boundary_midpoint(item)
            if point is not None:
                points.append(point)
        return points[0] if points else None
    return None


def _polygon(ring: list[list[float]]) -> Polygon | None:
    if len(ring) < 4:
        return None
    poly = Polygon([(float(point[0]), float(point[1])) for point in ring])
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty:
        return None
    if poly.geom_type == "Polygon":
        return poly
    if hasattr(poly, "geoms"):
        polygons = [item for item in poly.geoms if item.geom_type == "Polygon"]
        if polygons:
            return max(polygons, key=lambda item: item.area)
    return None


def _route_end(route: dict[str, Any]) -> RoutePoint | None:
    try:
        leg = route["routes"][0]["legs"][-1]
        location = leg["end_location"]
        return RoutePoint(
            lon=float(location["lng"]),
            lat=float(location["lat"]),
            zlevel=float(location.get("zLevel", 0.0)),
            floor_name=str(location.get("floor_name", "")),
        )
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def _point_param(point: RoutePoint) -> str:
    return f"{point.lat:.12f},{point.lon:.12f},{point.zlevel:g}"


def _source_floor(record: dict[str, Any]) -> float:
    props = record.get("source_properties") or {}
    value = props.get("floor")
    if value is None:
        value = record.get("floor_name")
    try:
        return float(value)
    except (TypeError, ValueError):
        if str(value).upper() in {"G", "GROUND"}:
            return 0.0
        return float(record.get("floor_index") or 0)


def _height(record: dict[str, Any]) -> float:
    local = record.get("anchor_local")
    if isinstance(local, list) and len(local) >= 2:
        try:
            return float(local[1])
        except (TypeError, ValueError):
            pass
    return float(record.get("floor_index") or 0) * 4.2


def _room_is_usable_target(room: dict[str, Any]) -> bool:
    return bool(room.get("anchor_lonlat") and room.get("polygon_lonlat") and room.get("external_id"))


def _group_origin(inventory: Any, primary_member: str) -> tuple[float, float]:
    records = inventory if isinstance(inventory, list) else inventory.get("buildings", [])
    for record in records:
        if str(record.get("admin_id", "")) == str(primary_member):
            origin = record.get("origin") or []
            if len(origin) >= 2:
                return float(origin[0]), float(origin[1])
    raise ValueError(f"Could not find origin for primary member {primary_member}")


def _approx_distance_m(a: Point, b: Point) -> float:
    mean_lat = (a.y + b.y) / 2.0
    metres_per_degree_lon = 111_320.0 * math.cos(math.radians(mean_lat))
    dx = (a.x - b.x) * metres_per_degree_lon
    dy = (a.y - b.y) * 111_320.0
    return math.hypot(dx, dy)


def _confidence_rank(value: str) -> int:
    return {"none": 0, "low": 1, "medium": 2, "high": 3}.get(value, 0)


def _geometry_fallback_rank(candidate: dict[str, Any]) -> tuple[int, float]:
    source_score = 2 if candidate.get("source") == "geometry_shared_boundary_midpoint" else 1
    confidence_score = _confidence_rank(str(candidate.get("confidence", "")))
    return (source_score + confidence_score, -float(candidate.get("distance_m", 0.0)))


def _route_error(route: dict[str, Any]) -> str:
    for key in ("error_message", "message", "error"):
        if route.get(key):
            return str(route[key])
    return json.dumps(route)[:500]


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
    tmp_path.replace(path)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    scalar_keys = [
        key for key in rows[0].keys()
        if key not in {"attempts", "door_local", "local", "target_external_ids", "target_building_admin_ids"}
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=[*scalar_keys, "local_json"])
        writer.writeheader()
        for row in rows:
            item = {key: row.get(key) for key in scalar_keys}
            item["local_json"] = json.dumps(row.get("door_local") or row.get("local") or [])
            writer.writerow(item)


def _write_report(path: Path, dataset_path: Path, graph_id: str, rooms: list[dict[str, Any]], entries: list[dict[str, Any]]) -> None:
    successful = [room for room in rooms if room.get("door_source") != "failed"]
    high = [room for room in rooms if room.get("confidence") == "high"]
    medium = [room for room in rooms if room.get("confidence") == "medium"]
    failed = [room for room in rooms if room.get("door_source") == "failed"]
    top_entries = sorted(entries, key=lambda item: -int(item.get("supporting_routes", 0)))[:20]
    lines = [
        "# Science Centre Door Research",
        "",
        f"Generated from `{dataset_path}` and MapsIndoors directions graph `{graph_id}`.",
        "",
        "## Method",
        "",
        "- The cached Science location data does not expose semantic door objects.",
        "- Room door points are derived by requesting a MapsIndoors walking route to each room anchor, then intersecting the final route segment with the target room polygon boundary.",
        "- External building entries are derived from the MapsIndoors route step where `abutters` changes from outside routing to `InsideBuilding`.",
        "- Outputs are route-derived entry points, not official architectural door assets.",
        "",
        "## Outputs",
        "",
        "- `science_room_door_points_route_derived.json`",
        "- `science_room_door_points_route_derived.csv`",
        "- `science_external_entry_points_route_derived.json`",
        "- `science_external_entry_points_route_derived.csv`",
        "- `external_doors.json`",
        "",
        "## Result Summary",
        "",
        f"- Room targets processed: {len(rooms)}",
        f"- Room door points found: {len(successful)}",
        f"- High-confidence room boundary intersections: {len(high)}",
        f"- Medium-confidence room boundary fallbacks: {len(medium)}",
        f"- Failed room targets: {len(failed)}",
        f"- External Science entry clusters: {len(entries)}",
        "",
        "## External Entry Clusters",
        "",
        "| entry_id | lon | lat | local x/z | supporting_routes | target buildings |",
        "| --- | ---: | ---: | --- | ---: | --- |",
    ]
    for entry in top_entries:
        local = entry.get("local") or []
        xz = f"{local[0]:.3f}, {local[2]:.3f}" if len(local) >= 3 else ""
        lines.append(
            f"| {entry.get('entry_id')} | {entry.get('lon')} | {entry.get('lat')} | {xz} | "
            f"{entry.get('supporting_routes')} | {', '.join(entry.get('target_building_admin_ids', []))} |"
        )
    lines.extend(
        [
            "",
            "## Limitations",
            "",
            "- MapsIndoors returns route geometry and inside/outside step metadata, but this probe did not find a published door feature layer.",
            "- If a room anchor is in a corridor, lobby, or circulation polygon, the derived boundary point is a route transition point for that polygon, not a physical door leaf.",
            "- For architectural fidelity, replace these route-derived points with authorised BIM/CAD door schedules if UoA provides them.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
