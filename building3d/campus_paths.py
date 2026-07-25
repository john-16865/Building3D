"""Generate the campus outdoor road/footpath network from MapsIndoors.

Samples MapsIndoors walking routes between campus entry points and building
anchors, keeps the OUTDOOR step geometry (the segments MapsIndoors routes over
real OSM footways/roads), merges them into a deduplicated graph, connects each
in-game building's entrance with a spur, and emits:

  - a road/footpath GLB mesh (vertices already in CampusMain placement space),
  - a nodes/edges graph JSON (placement space, per highway class),
  - a run stats JSON.

The projection into placement space reuses the same reference calibration the
building placement uses (building3d.campus_placement.CampusPlacementReference),
so the roads line up with the buildings the pipeline places, by construction.

This is the offline half; building3d.unimate_publish.publish_campus_paths_to_unimate
copies these artifacts into the Godot project, bakes the campus navigation mesh,
and upserts the roads into CampusMain.
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from building3d.campus_placement import CampusPlacementReference, _read_yaml, _float_list
from building3d.geometry import MeshData
from building3d.gltf import write_glb
from building3d.vertical_links import DEFAULT_GRAPH_ID

# The heavy MapsIndoors route client (thread-safe, disk-cached) lives with the
# door-derivation tool; reuse it rather than duplicating the HTTP + cache logic.
from tools.derive_science_door_points import RouteClient, RoutePoint


DEFAULT_WIDTHS_M = {
    "footway": 2.5,
    "path": 2.5,
    "residential": 5.0,
    "service": 3.5,
    "steps": 2.0,
    "spur": 2.5,
    "connector": 3.0,
    "underpass": 3.0,
    "default": 3.5,
}
# OSM highway class -> mesh material name (building3d.blender.materials).
HIGHWAY_MATERIAL = {
    "footway": "campus_footpath",
    "path": "campus_footpath",
    "residential": "campus_road",
    "service": "campus_service",
    "steps": "campus_steps",
    "spur": "campus_spur",
    "connector": "campus_connector",
    "underpass": "campus_underpass",
}


@dataclass(frozen=True)
class CampusPathsConfig:
    graph_id: str = DEFAULT_GRAPH_ID
    reference: CampusPlacementReference = field(default_factory=CampusPlacementReference)
    hub_count: int = 5
    building_pair_neighbors: int = 3
    max_targets: int = 140
    snap_m: float = 2.5
    simplify_m: float = 1.0
    min_stub_m: float = 6.0
    road_y: float = 0.35
    entrance_spur_max_m: float = 60.0
    widths_m: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WIDTHS_M))
    extra_entrances: list[dict[str, Any]] = field(default_factory=list)
    extra_edges: list[dict[str, Any]] = field(default_factory=list)
    delay: float = 0.03
    workers: int = 8
    campus_scene: str = "res://Scene/Campus/CampusMain.tscn"
    buildings_parent: str = "GameLayer/HBox/VBoxContainer/ViewportContainer/SubViewport/Buildings"
    subviewport_parent: str = "GameLayer/HBox/VBoxContainer/ViewportContainer/SubViewport"
    roads_node_name: str = "CampusRoads"
    output_scene: str = "res://Scene/campus_roads_baked.tscn"


def load_campus_paths_config(path: str | Path | None) -> CampusPathsConfig:
    if path is None:
        return CampusPathsConfig()
    data = _read_yaml(Path(path))
    ref_data = data.get("reference", {}) if isinstance(data.get("reference", {}), dict) else {}
    base_ref = CampusPlacementReference()
    reference = CampusPlacementReference(
        building_id=str(ref_data.get("building_id", base_ref.building_id)),
        lon=float(ref_data.get("lon", base_ref.lon)),
        lat=float(ref_data.get("lat", base_ref.lat)),
        local_anchor=_float_list(ref_data.get("local_anchor", base_ref.local_anchor), 3),
        origin=_float_list(ref_data.get("origin", base_ref.origin), 3),
        scale=float(ref_data.get("scale", base_ref.scale)),
    )
    widths = dict(DEFAULT_WIDTHS_M)
    for key, value in (data.get("widths_m", {}) or {}).items():
        widths[str(key)] = float(value)
    return CampusPathsConfig(
        graph_id=str(data.get("graph_id", DEFAULT_GRAPH_ID)),
        reference=reference,
        hub_count=int(data.get("hub_count", 5)),
        building_pair_neighbors=int(data.get("building_pair_neighbors", 3)),
        max_targets=int(data.get("max_targets", 140)),
        snap_m=float(data.get("snap_m", 2.5)),
        simplify_m=float(data.get("simplify_m", 1.0)),
        min_stub_m=float(data.get("min_stub_m", 6.0)),
        road_y=float(data.get("road_y", 0.35)),
        entrance_spur_max_m=float(data.get("entrance_spur_max_m", 60.0)),
        widths_m=widths,
        extra_entrances=[dict(item) for item in data.get("extra_entrances", []) if isinstance(item, dict)],
        extra_edges=[dict(item) for item in data.get("extra_edges", []) if isinstance(item, dict)],
        delay=float(data.get("delay", 0.03)),
        workers=int(data.get("workers", 8)),
        campus_scene=str(data.get("campus_scene", CampusPathsConfig.campus_scene)),
        buildings_parent=str(data.get("buildings_parent", CampusPathsConfig.buildings_parent)),
        subviewport_parent=str(data.get("subviewport_parent", CampusPathsConfig.subviewport_parent)),
        roads_node_name=str(data.get("roads_node_name", "CampusRoads")),
        output_scene=str(data.get("output_scene", CampusPathsConfig.output_scene)),
    )


# ---------------------------------------------------------------------------
# projection: lon/lat -> CampusMain placement space (matches campus_placement)
# ---------------------------------------------------------------------------

def project_to_campus(lon: float, lat: float, reference: CampusPlacementReference) -> tuple[float, float]:
    """Project lon/lat to CampusMain placement (X, Z), Godot -Z north convention."""
    scale = reference.scale
    ref_entry_x = reference.origin[0] + scale * reference.local_anchor[0]
    ref_entry_z = reference.origin[2] - scale * reference.local_anchor[2]
    metres_per_lon = 111_320.0 * math.cos(reference.lat * math.pi / 180.0)
    east_m = (lon - reference.lon) * metres_per_lon
    north_m = (lat - reference.lat) * 111_320.0
    return (ref_entry_x + scale * east_m, ref_entry_z - scale * north_m)


def placement_to_lonlat(x: float, z: float, reference: CampusPlacementReference) -> tuple[float, float]:
    """Inverse of :func:`project_to_campus` — placement (X, Z) back to lon/lat.

    Lets us sample MapsIndoors routes between the in-game buildings (whose
    entrances are known in placement space) so the direct optimal route between
    every real building is present in the network.
    """
    scale = reference.scale
    ref_entry_x = reference.origin[0] + scale * reference.local_anchor[0]
    ref_entry_z = reference.origin[2] - scale * reference.local_anchor[2]
    metres_per_lon = 111_320.0 * math.cos(reference.lat * math.pi / 180.0)
    east_m = (x - ref_entry_x) / scale
    north_m = -(z - ref_entry_z) / scale
    return (reference.lon + east_m / metres_per_lon, reference.lat + north_m / 111_320.0)


# ---------------------------------------------------------------------------
# sampling
# ---------------------------------------------------------------------------

def sample_campus_routes(
    cfg: CampusPathsConfig,
    targets: list[list[float]],
    client: RouteClient,
    *,
    entry_points: list[list[float]],
    building_points: list[tuple[float, float]] = (),
) -> list[dict[str, Any]]:
    hubs = _spread(entry_points, cfg.hub_count)
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for i in range(len(entry_points)):
        for j in range(i + 1, len(entry_points)):
            pairs.append((tuple(entry_points[i]), tuple(entry_points[j])))
    for hub in hubs:
        for target in targets:
            pairs.append((tuple(hub), tuple(target)))
    for a, b in _nearest_building_pairs(targets, cfg.building_pair_neighbors):
        pairs.append((tuple(a), tuple(b)))
    # ALL pairs among the in-game buildings: guarantees the direct MapsIndoors
    # optimal route between every real building is sampled, filling the
    # building-to-building corridors that hub-and-spoke sampling leaves empty
    # (without this the network is a near-tree and routing detours via a hub).
    # Also tie each in-game building to the hubs so it joins the wider network.
    bpts = [tuple(p) for p in building_points]
    for i in range(len(bpts)):
        for j in range(i + 1, len(bpts)):
            pairs.append((bpts[i], bpts[j]))
    for hub in hubs:
        for p in bpts:
            pairs.append((tuple(hub), p))
    # Deduplicate (order-insensitive) and sort for determinism.
    unique = sorted({tuple(sorted((p[0], p[1]))) for p in pairs})

    results: list[dict[str, Any]] = [None] * len(unique)  # type: ignore[list-item]
    with ThreadPoolExecutor(max_workers=max(1, cfg.workers)) as pool:
        futures = {pool.submit(_route, client, a, b): idx for idx, (a, b) in enumerate(unique)}
        done = 0
        for future in as_completed(futures):
            idx = futures[future]
            try:
                results[idx] = future.result()
            except Exception:
                results[idx] = {"status": "ERROR"}
            done += 1
            if done % 100 == 0:
                print(f"  routed {done}/{len(unique)}", flush=True)
    return [r for r in results if r]


def _route(client: RouteClient, a: tuple[float, float], b: tuple[float, float]) -> dict[str, Any]:
    origin = RoutePoint(lon=a[0], lat=a[1], zlevel=0.0, floor_name="0")
    destination = RoutePoint(lon=b[0], lat=b[1], zlevel=0.0, floor_name="0")
    return client.route(origin, destination)


def extract_outdoor_segments(routes: list[dict[str, Any]]) -> tuple[list, list]:
    """Keep the OUTDOOR step polylines, bridging indoor gaps within each route.

    MapsIndoors walking routes between buildings interleave outdoor steps with
    indoor ones (through-building portions, entrance/exit approaches). Dropping
    the indoor steps outright — the old behaviour — shatters a route's outdoor
    fragments into disconnected pieces, so routing has to detour around the gap
    (this is what made engineering->science loop 3.9x north instead of taking the
    direct corridor). Instead we replace each indoor stretch with a single
    straight ``connector`` segment spanning it (entry point -> exit point) and
    stitch across step boundaries, so each route stays one continuous polyline.
    Connectors are low priority and thin, so real road/footway classes win
    wherever geometry overlaps.
    """
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    highways: list[str] = []
    for route in routes:
        if not route or str(route.get("status")) != "OK":
            continue
        for r in route.get("routes", []):
            # A MapsIndoors route commonly puts an outdoor section, an indoor
            # passage, and the following outdoor section in three separate
            # legs.  The continuity state therefore belongs to the whole
            # route, not to an individual leg.  Resetting ``prev`` per leg
            # silently discarded every such passage connector and forced the
            # generated graph to take a long outdoor loop.
            prev: tuple[float, float] | None = None
            for leg in r.get("legs", []):
                for step in leg.get("steps", []):
                    geom = step.get("geometry") or []
                    pts = [(float(p["lng"]), float(p["lat"])) for p in geom if _z(p) == 0.0]
                    if not pts:
                        continue
                    if "Inside" in str(step.get("abutters") or ""):
                        # Skip the indoor stretch but leave `prev` at the last
                        # OUTDOOR point: when the next outdoor step arrives the
                        # gap-stitch below bridges the whole indoor passage with
                        # ONE connector. Routes that START or END indoors get no
                        # dangling connector into the building — the entrance
                        # spur owns the road->door link.
                        continue
                    hwy = str(step.get("highway") or "path")
                    if prev is not None and prev != pts[0]:
                        segments.append((prev, pts[0]))
                        highways.append("connector")
                    for u, v in zip(pts, pts[1:]):
                        segments.append((u, v))
                        highways.append(hwy)
                    prev = pts[-1]
    return segments, highways


def _z(point: dict[str, Any]) -> float:
    try:
        return float(point.get("zLevel", 0.0))
    except (TypeError, ValueError):
        return 0.0


def extra_edge_segments(cfg: CampusPathsConfig) -> tuple[list, list]:
    """Hand-authored edges from config, as lon/lat segments + highway classes.

    MapsIndoors sampling can only ever see zLevel-0 outdoor geometry
    (extract_outdoor_segments drops everything else), so below-grade passages
    like the B331 Southern Underpass never enter the network by sampling.
    Each config entry supplies a polyline either as ``points_lonlat``
    ([[lon, lat], ...], the authority for geo-known structures) or as
    ``points`` / ``points_placement`` ([[x, z], ...] in CampusMain placement
    space, converted through the same reference calibration the rest of the
    frame uses). The segments join the sampled ones BEFORE build_network so
    snapping, stub pruning, and component bridging treat them uniformly.
    """
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    highways: list[str] = []
    for entry in cfg.extra_edges:
        highway = str(entry.get("highway", "underpass")).strip() or "underpass"
        raw_points = entry.get("points_lonlat")
        if not isinstance(raw_points, list) or not raw_points:
            placement_points = entry.get("points") or entry.get("points_placement") or []
            raw_points = [
                list(placement_to_lonlat(float(point[0]), float(point[1]), cfg.reference))
                for point in placement_points
                if isinstance(point, (list, tuple)) and len(point) >= 2
            ]
        points = [
            (float(point[0]), float(point[1]))
            for point in raw_points
            if isinstance(point, (list, tuple)) and len(point) >= 2
        ]
        for u, v in zip(points, points[1:]):
            segments.append((u, v))
            highways.append(highway)
    return segments, highways


# ---------------------------------------------------------------------------
# graph build
# ---------------------------------------------------------------------------

def build_network(segments: list, highways: list, cfg: CampusPathsConfig) -> dict[str, Any]:
    """Snap-merge lon/lat segments into a deduplicated graph in placement space."""
    ref = cfg.reference
    # Snap in placement units so snap_m is consistent with the rest of the frame.
    snap = cfg.snap_m * ref.scale

    key_to_id: dict[tuple[int, int], int] = {}
    node_pts: dict[int, list[tuple[float, float]]] = {}

    def node_id(lon: float, lat: float) -> int:
        x, z = project_to_campus(lon, lat, ref)
        key = (round(x / snap), round(z / snap))
        if key not in key_to_id:
            key_to_id[key] = len(key_to_id)
            node_pts[len(key_to_id) - 1] = []
        nid = key_to_id[key]
        node_pts[nid].append((x, z))
        return nid

    # Priority so a shared edge keeps its most road-like class deterministically.
    # Connectors (indoor-gap bridges) are lowest, so any real class overrides them.
    # Hand-authored underpass edges outrank everything: they exist precisely
    # because sampling cannot see them, so a sampled class must not repaint them.
    priority = {"underpass": 5, "residential": 4, "service": 3, "footway": 2, "path": 1, "steps": 1, "connector": 0}
    edge_class: dict[tuple[int, int], str] = {}
    for (u, v), hwy in zip(segments, highways):
        a = node_id(*u)
        b = node_id(*v)
        if a == b:
            continue
        key = (a, b) if a < b else (b, a)
        current = edge_class.get(key)
        if current is None or priority.get(hwy, 0) > priority.get(current, 0):
            edge_class[key] = hwy

    nodes: dict[int, list[float]] = {}
    for nid, pts in node_pts.items():
        nodes[nid] = [round(sum(p[0] for p in pts) / len(pts), 4), round(sum(p[1] for p in pts) / len(pts), 4)]

    edges = [{"a": a, "b": b, "highway": edge_class[(a, b)]} for (a, b) in sorted(edge_class)]
    graph = {"nodes": nodes, "edges": edges}
    _prune_stubs(graph, cfg)
    _bridge_components(graph)
    return graph


def _connected_components(graph: dict[str, Any]) -> list[list[int]]:
    parent: dict[int, int] = {n: n for n in graph["nodes"]}

    def find(x: int) -> int:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for edge in graph["edges"]:
        parent[find(edge["a"])] = find(edge["b"])
    groups: dict[int, list[int]] = {}
    for node in graph["nodes"]:
        groups.setdefault(find(node), []).append(node)
    return sorted(groups.values(), key=len, reverse=True)


def _bridge_components(graph: dict[str, Any]) -> None:
    """Fuse the graph into a single connected component.

    MapsIndoors sampling yields a few stray outdoor islands (isolated footpaths
    or parking lots) that never snap-merged into the main network; left alone, a
    building spur can land on an island and become unreachable. Each smaller
    component is joined to the main one by its nearest node pair.
    """
    nodes = graph["nodes"]
    components = _connected_components(graph)
    if len(components) <= 1:
        return
    main = components[0]
    for island in components[1:]:
        best: tuple[float, int, int] | None = None
        for a in island:
            ax, az = nodes[a]
            for b in main:
                bx, bz = nodes[b]
                dist = math.hypot(bx - ax, bz - az)
                if best is None or dist < best[0]:
                    best = (dist, a, b)
        if best is None:
            continue
        _dist, a, b = best
        graph["edges"].append({"a": min(a, b), "b": max(a, b), "highway": "spur"})
        main.extend(island)
    graph["edges"].sort(key=lambda e: (e["a"], e["b"]))


def _prune_stubs(graph: dict[str, Any], cfg: CampusPathsConfig) -> None:
    """Drop short dead-end edges (GPS noise spikes) without splitting the graph."""
    min_stub = cfg.min_stub_m * cfg.reference.scale
    nodes = graph["nodes"]
    changed = True
    while changed:
        changed = False
        degree: dict[int, int] = {}
        for edge in graph["edges"]:
            degree[edge["a"]] = degree.get(edge["a"], 0) + 1
            degree[edge["b"]] = degree.get(edge["b"], 0) + 1
        keep = []
        for edge in graph["edges"]:
            a, b = edge["a"], edge["b"]
            is_stub = (degree.get(a, 0) == 1 or degree.get(b, 0) == 1)
            if is_stub and _node_dist(nodes[a], nodes[b]) < min_stub:
                changed = True
                continue
            keep.append(edge)
        graph["edges"] = keep
    _drop_orphan_nodes(graph)


def _drop_orphan_nodes(graph: dict[str, Any]) -> None:
    used = set()
    for edge in graph["edges"]:
        used.add(edge["a"])
        used.add(edge["b"])
    graph["nodes"] = {nid: pt for nid, pt in graph["nodes"].items() if nid in used}


# ---------------------------------------------------------------------------
# entrance spurs
# ---------------------------------------------------------------------------

def load_building_entrances(godot_dir: Path | None, cfg: CampusPathsConfig) -> list[dict[str, Any]]:
    """Entrance points in placement space, from published buildings + config extras.

    Every route-derived external entry gets its own spur, not just the primary
    placement entrance: multi-wing buildings (Elam) are entered through
    different doors depending on the destination wing, and the campus leg must
    be able to end at any of them on the road network.
    """
    entrances: list[dict[str, Any]] = []
    if godot_dir is not None:
        buildings_root = Path(godot_dir) / "Assets" / "Buildings"
        for placement_path in sorted(buildings_root.glob("*/*_campus_placement.json")):
            try:
                placement = json.loads(placement_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            building_id = str(placement.get("building_id", placement_path.parent.name))
            building_points: list[tuple[float, float]] = []
            point = _entrance_placement_point(placement)
            if point is not None:
                building_points.append(point)
                entrances.append({"building_id": building_id, "x": point[0], "z": point[1]})
            for entry_point in _external_entry_points(placement_path.parent, building_id, placement):
                if any(math.hypot(entry_point[0] - px, entry_point[1] - pz) < 2.0 for px, pz in building_points):
                    continue
                building_points.append(entry_point)
                entrances.append({"building_id": building_id, "x": entry_point[0], "z": entry_point[1]})
    for extra in cfg.extra_entrances:
        try:
            entrances.append({"building_id": str(extra.get("building_id", "extra")), "x": float(extra["x"]), "z": float(extra["z"])})
        except (KeyError, TypeError, ValueError):
            continue
    return entrances


def _external_entry_points(
    asset_dir: Path,
    building_id: str,
    placement: dict[str, Any],
) -> list[tuple[float, float]]:
    entries_path = asset_dir / f"{building_id}_full_external_entry_points_route_derived.json"
    if not entries_path.exists():
        return []
    try:
        entries = json.loads(entries_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    if not isinstance(entries, list):
        return []
    transform = placement.get("transform", {}) if isinstance(placement.get("transform", {}), dict) else {}
    origin = transform.get("origin")
    scale = float(placement.get("scale", 0.89260983))
    if not (isinstance(origin, list) and len(origin) == 3):
        return []
    points: list[tuple[float, float]] = []
    for entry in entries:
        local = entry.get("local") if isinstance(entry, dict) else None
        if not (isinstance(local, list) and len(local) == 3):
            continue
        # Same basis math as _entrance_placement_point: X keeps sign, Z flips.
        points.append((
            float(origin[0]) + scale * float(local[0]),
            float(origin[2]) - scale * float(local[2]),
        ))
    return points


def _entrance_placement_point(placement: dict[str, Any]) -> tuple[float, float] | None:
    """Entrance X/Z in Buildings-parent placement space.

    The building node basis carries a -Z flip (``Transform3D(s,0,0, 0,s,0,
    0,0,-s, ...)``), so the entrance anchor's local Z is negated relative to the
    building origin: ``entrance = origin + (s*ax, ., -s*az)``. X is not flipped.
    Verified against the live CampusMain scene (Art/Science entrance globals).
    """
    transform = placement.get("transform", {}) if isinstance(placement.get("transform", {}), dict) else {}
    origin = transform.get("origin")
    entrance = placement.get("entrance", {}) if isinstance(placement.get("entrance", {}), dict) else {}
    anchor = entrance.get("anchor")
    scale = float(placement.get("scale", 0.89260983))
    if not (isinstance(origin, list) and len(origin) == 3):
        return None
    if not (isinstance(anchor, list) and len(anchor) == 3):
        return (float(origin[0]), float(origin[2]))
    return (float(origin[0]) + scale * float(anchor[0]), float(origin[2]) - scale * float(anchor[2]))


def add_entrance_spurs(graph: dict[str, Any], entrances: list[dict[str, Any]], cfg: CampusPathsConfig) -> list[dict[str, Any]]:
    """Connect each entrance to the nearest graph node with a spur edge."""
    nodes = graph["nodes"]
    spur_stats: list[dict[str, Any]] = []
    next_id = (max(nodes) + 1) if nodes else 0
    max_spur = cfg.entrance_spur_max_m * cfg.reference.scale
    for entrance in sorted(entrances, key=lambda e: str(e.get("building_id", ""))):
        point = [round(float(entrance["x"]), 4), round(float(entrance["z"]), 4)]
        nearest_id, dist = _nearest_node(nodes, point)
        record = {"building_id": entrance.get("building_id", ""), "snap_m": round(dist / cfg.reference.scale, 2)}
        if nearest_id is None or dist > max_spur:
            record["connected"] = False
            spur_stats.append(record)
            continue
        entrance_id = next_id
        next_id += 1
        nodes[entrance_id] = point
        graph["edges"].append({"a": min(entrance_id, nearest_id), "b": max(entrance_id, nearest_id), "highway": "spur"})
        record["connected"] = True
        spur_stats.append(record)
    graph["edges"].sort(key=lambda e: (e["a"], e["b"]))
    return spur_stats


# ---------------------------------------------------------------------------
# mesh emission (quad strips per edge, one MeshData per highway class)
# ---------------------------------------------------------------------------

def graph_to_meshes(graph: dict[str, Any], cfg: CampusPathsConfig) -> list[MeshData]:
    nodes = graph["nodes"]
    y = cfg.road_y
    scale = cfg.reference.scale
    by_class: dict[str, tuple[list[list[float]], list[list[int]]]] = {}
    for edge in graph["edges"]:
        a = nodes.get(edge["a"])
        b = nodes.get(edge["b"])
        if a is None or b is None:
            continue
        hwy = str(edge.get("highway", "path"))
        material = HIGHWAY_MATERIAL.get(hwy, "campus_footpath")
        half = 0.5 * cfg.widths_m.get(hwy, cfg.widths_m.get("default", 3.5)) * scale
        verts, faces = by_class.setdefault(material, ([], []))
        _append_strip(verts, faces, a, b, half, y)
    meshes: list[MeshData] = []
    for material in sorted(by_class):
        verts, faces = by_class[material]
        if faces:
            meshes.append(MeshData(name=f"campus_paths__{material}", vertices=verts, faces=faces, material=material))
    return meshes


def _append_strip(verts: list, faces: list, a: list[float], b: list[float], half: float, y: float) -> None:
    ax, az = a
    bx, bz = b
    dx, dz = bx - ax, bz - az
    length = math.hypot(dx, dz)
    if length < 1e-6:
        return
    # Perpendicular offset in XZ; extend caps by `half` so joints overlap and fill.
    nx, nz = -dz / length * half, dx / length * half
    ex, ez = dx / length * half, dz / length * half
    base = len(verts)
    verts.extend([
        [round(ax - ex + nx, 4), y, round(az - ez + nz, 4)],
        [round(bx + ex + nx, 4), y, round(bz + ez + nz, 4)],
        [round(bx + ex - nx, 4), y, round(bz + ez - nz, 4)],
        [round(ax - ex - nx, 4), y, round(az - ez - nz, 4)],
    ])
    faces.append([base, base + 1, base + 2, base + 3])


# ---------------------------------------------------------------------------
# per-building-pair optimal route polylines
# ---------------------------------------------------------------------------
# A merged road graph's shortest path can only approximate the MapsIndoors
# optimal route (snap/merge always finds a slightly longer way, and for some
# pairs a much longer one). For the drawn campus route line we therefore ship
# the ACTUAL MapsIndoors route for each pair of in-game buildings, projected into
# placement space, so the line follows the true optimal walk exactly.

def _route_polyline_lonlat(route: dict[str, Any]) -> list[tuple[float, float]]:
    """Ordered (lon, lat) polyline for one route; indoor stretches collapse to
    their endpoints so the line bridges through buildings without detouring."""
    poly: list[tuple[float, float]] = []
    for r in route.get("routes", []):
        for leg in r.get("legs", []):
            for step in leg.get("steps", []):
                geom = step.get("geometry") or []
                pts = [(float(p["lng"]), float(p["lat"])) for p in geom if _z(p) == 0.0]
                if not pts:
                    continue
                seq = [pts[0], pts[-1]] if "Inside" in str(step.get("abutters") or "") else pts
                for p in seq:
                    if not poly or poly[-1] != p:
                        poly.append(p)
    return poly


def _simplify_polyline(points: list[list[float]], tolerance: float) -> list[list[float]]:
    """Ramer-Douglas-Peucker simplification in placement units."""
    if len(points) < 3 or tolerance <= 0.0:
        return points
    start, end = points[0], points[-1]
    dx, dz = end[0] - start[0], end[1] - start[1]
    seg_len = math.hypot(dx, dz)
    max_d, idx = -1.0, 0
    for i in range(1, len(points) - 1):
        px, pz = points[i]
        if seg_len < 1e-9:
            d = math.hypot(px - start[0], pz - start[1])
        else:
            d = abs(dz * px - dx * pz + end[0] * start[1] - end[1] * start[0]) / seg_len
        if d > max_d:
            max_d, idx = d, i
    if max_d <= tolerance:
        return [start, end]
    left = _simplify_polyline(points[: idx + 1], tolerance)
    right = _simplify_polyline(points[idx:], tolerance)
    return left[:-1] + right


def build_building_route_polylines(
    cfg: CampusPathsConfig, entrances: list[dict[str, Any]], client: RouteClient
) -> dict[str, list[list[float]]]:
    """Optimal MapsIndoors route per in-game building pair, in placement space.

    Keyed ``"<from>|<to>"`` by lowercase building id (one direction; the consumer
    reverses for the other). Endpoints are the buildings' entrance anchors, which
    project back onto the same door points the spurs use.
    """
    ref = cfg.reference
    # First door per building wins: load_building_entrances lists the primary
    # placement entrance first, and that is the door campus routing ends at.
    pts: dict[str, tuple[float, float]] = {}
    for e in entrances:
        pts.setdefault(
            str(e["building_id"]).lower(),
            placement_to_lonlat(float(e["x"]), float(e["z"]), ref),
        )
    ids = sorted(pts)
    out: dict[str, list[list[float]]] = {}
    failed_pairs = 0
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            a, b = ids[i], ids[j]
            try:
                route = _route(client, pts[a], pts[b])
            except Exception:
                # Directions 404s for entrances the venue graph cannot reach
                # (hand-authored doors on buildings MapsIndoors never routes
                # into). Those buildings still join the network via their
                # entrance spurs, which do not use the directions API.
                failed_pairs += 1
                continue
            if not route or str(route.get("status")) != "OK":
                continue
            poly_lonlat = _route_polyline_lonlat(route)
            if len(poly_lonlat) < 2:
                continue
            projected = [list(project_to_campus(lon, lat, ref)) for lon, lat in poly_lonlat]
            simplified = _simplify_polyline(projected, cfg.simplify_m * ref.scale)
            out[f"{a}|{b}"] = [[round(x, 3), round(z, 3)] for x, z in simplified]
    if failed_pairs:
        print(f"  building pair routes: {len(out)} OK, {failed_pairs} unroutable (skipped)", flush=True)
    if not out and failed_pairs:
        raise SystemExit("Every building pair route failed - directions API down?")
    return out


# ---------------------------------------------------------------------------
# orchestration
# ---------------------------------------------------------------------------

def generate_campus_paths(
    solution_config,
    cfg: CampusPathsConfig,
    *,
    godot_dir: Path | None = None,
    force_sample: bool = False,
) -> dict[str, Any]:
    export_dir = solution_config.export_root / "campus"
    cache_dir = export_dir / "campus_route_cache"
    export_dir.mkdir(parents=True, exist_ok=True)
    inventory_path = solution_config.processed_root / "inventory.json"

    client = RouteClient(cfg.graph_id, cache_dir, cfg.delay)
    details = client.graph_details()
    entry_points = [e["coordinates"][:2] for e in details.get("entryPoints", []) if isinstance(e.get("coordinates"), list)]
    bbox = details.get("bbox")
    targets = _building_targets(inventory_path, bbox, cfg.max_targets)
    entrances = load_building_entrances(godot_dir, cfg)
    # ONE representative point per building (its primary placement entrance)
    # for the all-pairs sampling. load_building_entrances returns EVERY door
    # (they all need spurs); doing all-pairs over every door exploded
    # C(24,2)=276 direct-corridor samples into C(~170,2)≈14k API routes, and
    # the flood of duplicated street coverage repainted the whole footpath
    # network at the widest ("residential") merge priority.
    primary_entrance_by_building: dict[str, tuple[float, float]] = {}
    for e in entrances:
        primary_entrance_by_building.setdefault(str(e["building_id"]), (float(e["x"]), float(e["z"])))
    building_points = [
        placement_to_lonlat(x, z, cfg.reference) for x, z in primary_entrance_by_building.values()
    ]
    print(
        f"entry points: {len(entry_points)}  building targets: {len(targets)}  "
        f"in-game buildings: {len(building_points)} (of {len(entrances)} doors)",
        flush=True,
    )

    routes = sample_campus_routes(
        cfg, targets, client, entry_points=entry_points, building_points=building_points
    )
    ok = sum(1 for r in routes if r and str(r.get("status")) == "OK")
    print(f"routes OK: {ok}/{len(routes)}", flush=True)

    segments, highways = extract_outdoor_segments(routes)
    extra_segments, extra_highways = extra_edge_segments(cfg)
    if extra_segments:
        segments = list(segments) + extra_segments
        highways = list(highways) + extra_highways
        print(f"extra edges: {len(extra_segments)} hand-authored segments", flush=True)
    graph = build_network(segments, highways, cfg)
    spur_stats = add_entrance_spurs(graph, entrances, cfg)
    meshes = graph_to_meshes(graph, cfg)

    total_len = sum(_node_dist(graph["nodes"][e["a"]], graph["nodes"][e["b"]]) for e in graph["edges"] if e["a"] in graph["nodes"] and e["b"] in graph["nodes"])
    glb_path = export_dir / "campus_roads.glb"
    write_glb(meshes, glb_path)
    graph_path = export_dir / "campus_paths_graph.json"
    graph_path.write_text(json.dumps(_serialisable_graph(graph), indent=1) + "\n", encoding="utf-8")

    # Authoritative per-pair optimal route polylines for the drawn campus line.
    building_routes = build_building_route_polylines(cfg, entrances, client)
    routes_path = export_dir / "campus_building_routes.json"
    routes_path.write_text(json.dumps(building_routes, indent=1) + "\n", encoding="utf-8")

    edge_types: dict[str, int] = {}
    for edge in graph["edges"]:
        edge_types[edge["highway"]] = edge_types.get(edge["highway"], 0) + 1
    stats = {
        "routes_ok": ok,
        "routes_total": len(routes),
        "raw_outdoor_segments": len(segments) - len(extra_segments),
        "extra_edge_segments": len(extra_segments),
        "nodes": len(graph["nodes"]),
        "edges": len(graph["edges"]),
        "total_length_units": round(total_len, 1),
        "total_length_m": round(total_len / cfg.reference.scale, 1),
        "edge_types": edge_types,
        "meshes": [m.name for m in meshes],
        "entrance_spurs": spur_stats,
        "building_routes": len(building_routes),
        "glb": str(glb_path),
        "graph": str(graph_path),
        "building_routes_json": str(routes_path),
    }
    stats_path = export_dir / "campus_paths_stats.json"
    stats_path.write_text(json.dumps(stats, indent=2) + "\n", encoding="utf-8")
    stats["stats_path"] = str(stats_path)
    stats["export_dir"] = str(export_dir)
    return stats


def _serialisable_graph(graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "nodes": {str(nid): pt for nid, pt in sorted(graph["nodes"].items())},
        "edges": graph["edges"],
    }


def _building_targets(inventory_path: Path, bbox, max_targets: int) -> list[list[float]]:
    if not inventory_path.exists():
        return []
    inv = json.loads(inventory_path.read_text(encoding="utf-8"))
    recs = inv if isinstance(inv, list) else inv.get("buildings", inv.get("records", []))
    pts = []
    for record in recs:
        origin = record.get("origin")
        if not origin or len(origin) < 2:
            continue
        if bbox and not (bbox[0] <= origin[0] <= bbox[2] and bbox[1] <= origin[1] <= bbox[3]):
            continue
        pts.append([float(origin[0]), float(origin[1])])
    return pts[:max_targets]


def _spread(points: list[list[float]], count: int) -> list[list[float]]:
    if len(points) <= count or count <= 0:
        return points
    step = len(points) / count
    return [points[int(i * step)] for i in range(count)]


def _nearest_building_pairs(targets: list[list[float]], k: int) -> list[tuple[list[float], list[float]]]:
    pairs = []
    for i, a in enumerate(targets):
        dists = sorted(((_lonlat_dist_m(a, b), j) for j, b in enumerate(targets) if j != i))
        for _dist, j in dists[:k]:
            pairs.append((a, targets[j]))
    return pairs


def _lonlat_dist_m(a: list[float], b: list[float]) -> float:
    mlat = 111_320.0
    mlon = 111_320.0 * math.cos(math.radians((a[1] + b[1]) / 2.0))
    return math.hypot((b[0] - a[0]) * mlon, (b[1] - a[1]) * mlat)


def _node_dist(a: list[float], b: list[float]) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _nearest_node(nodes: dict[int, list[float]], point: list[float]) -> tuple[int | None, float]:
    best_id = None
    best = float("inf")
    for nid, pt in nodes.items():
        d = math.hypot(pt[0] - point[0], pt[1] - point[1])
        if d < best:
            best = d
            best_id = nid
    return best_id, best
