"""Audit UNIMATE campus alignment against MapsIndoors source data (offline).

Checks, in one pass:
  1. POSITION — each placed building's rendered world AABB centre (from a
     Godot headless dump, see UNIMATE tools/audit_campus_aabb_dump.gd) vs the
     union of its member buildings' MapsIndoors bboxes projected into campus
     placement space.
  2. SCALE — rendered AABB footprint extents vs geo bbox extents (metres x
     campus scale). The campus frame is east/north aligned, so the two are
     directly comparable per axis.
  3. ENTRANCE CONTRACT — instance transform * placement anchor must land on
     the projected entrance lon/lat (catches hand-edited campus transforms).
  4. ROADS — campus_paths_graph.json node positions vs the cached MapsIndoors
     route step geometry projected into the same frame (nearest-point
     distance percentiles).

Usage:
  .codex-venv/Scripts/python.exe tools/audit_campus_alignment.py \
      --godot-dir ../UNIMATE/Godot --dump campus_aabb_dump.json
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
INVENTORY = REPO / "data" / "processed" / "auckland" / "inventory.json"
GROUPS_YAML = REPO / "configs" / "auckland_building_groups.yaml"
CAMPUS_EXPORT = REPO / "exports" / "auckland" / "campus"

# Legacy hand-made scenes: report their drift for information, never FAIL on
# them (they predate the MapsIndoors pipeline and are kept intentionally).
LEGACY_IDS = {"kate", "engineering"}

# Buildings whose below-ground floors legitimately extend past their
# street-level MapsIndoors bbox (acoustics' b1 lab, architecture's SB level
# under the courtyard). Size/position flags are informational for these; the
# entrance contract still applies.
UNDERGROUND_SPRAWL_IDS = {"acoustics", "architecture"}

# Buildings whose UNIMATE id has no groups-config entry: match inventory by
# display name instead of member admin ids.
NAME_FALLBACK = {"kate": "Kate Edger Information Commons"}

POSITION_FAIL_M = 25.0
SIZE_RATIO_RANGE = (0.55, 1.8)
ENTRANCE_FAIL_UNITS = 1.0


def load_reference(placements: dict[str, dict]) -> dict:
    refs = {json.dumps(p["reference"], sort_keys=True) for p in placements.values()}
    if len(refs) > 1:
        raise SystemExit("FAIL: placement JSONs disagree on the campus reference frame")
    return json.loads(next(iter(refs)))


def make_projector(ref: dict):
    scale = float(ref["scale"])
    anchor = ref["local_anchor"]
    origin = ref["origin"]
    ref_entry_x = origin[0] + scale * anchor[0]
    ref_entry_z = origin[2] - scale * anchor[2]
    metres_per_lon = 111_320.0 * math.cos(ref["lat"] * math.pi / 180.0)

    def project(lon: float, lat: float) -> tuple[float, float]:
        east_m = (lon - ref["lon"]) * metres_per_lon
        north_m = (lat - ref["lat"]) * 111_320.0
        return (ref_entry_x + scale * east_m, ref_entry_z - scale * north_m)

    return project, scale, metres_per_lon


def world_to_local(dump: dict, point: list[float]) -> tuple[float, float, float]:
    basis = dump["buildings_transform"]["basis"]
    origin = dump["buildings_transform"]["origin"]
    d = [point[i] - origin[i] for i in range(3)]
    # basis rows are the transform's column vectors (x, y, z axes); invert.
    m = [[basis[c][r] for c in range(3)] for r in range(3)]
    det = (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )
    inv = [
        [
            (m[1][1] * m[2][2] - m[1][2] * m[2][1]) / det,
            (m[0][2] * m[2][1] - m[0][1] * m[2][2]) / det,
            (m[0][1] * m[1][2] - m[0][2] * m[1][1]) / det,
        ],
        [
            (m[1][2] * m[2][0] - m[1][0] * m[2][2]) / det,
            (m[0][0] * m[2][2] - m[0][2] * m[2][0]) / det,
            (m[0][2] * m[1][0] - m[0][0] * m[1][2]) / det,
        ],
        [
            (m[1][0] * m[2][1] - m[1][1] * m[2][0]) / det,
            (m[0][1] * m[2][0] - m[0][0] * m[2][1]) / det,
            (m[0][0] * m[1][1] - m[0][1] * m[1][0]) / det,
        ],
    ]
    return tuple(sum(inv[r][c] * d[c] for c in range(3)) for r in range(3))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--godot-dir", required=True, type=Path)
    parser.add_argument("--dump", required=True, type=Path)
    args = parser.parse_args()

    godot = args.godot_dir
    dump = json.loads(args.dump.read_text(encoding="utf-8"))
    registry = json.loads(
        (godot / "Assets" / "Buildings" / "building_registry.json").read_text(encoding="utf-8")
    )["buildings"]
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    by_admin = {str(r["admin_id"]): r for r in inventory}
    by_name = {r["display_name"].strip().lower(): r for r in inventory}
    groups = {g["id"]: g for g in yaml.safe_load(GROUPS_YAML.read_text(encoding="utf-8"))["groups"]}

    placements: dict[str, dict] = {}
    for entry in registry:
        placement_res = entry.get("campus_placement")
        if not placement_res:
            continue
        path = godot / str(placement_res).replace("res://", "")
        placements[entry["id"]] = json.loads(path.read_text(encoding="utf-8"))

    ref = load_reference(placements)
    project, scale, metres_per_lon = make_projector(ref)

    failures: list[str] = []
    print(f"reference: scale={scale} (units/m)  frame anchor: {ref['building_id']}")
    print()
    header = (
        f"{'building':<22}{'d_pos m':>9}{'size E act/exp':>18}{'size N act/exp':>18}"
        f"{'ratioE':>8}{'ratioN':>8}  flags"
    )
    print(header)
    print("-" * len(header))

    for entry in sorted(registry, key=lambda e: e["id"]):
        bid = entry["id"]
        node = dump["nodes"].get(entry["node_name"])
        if node is None:
            failures.append(f"{bid}: node {entry['node_name']} missing from scene dump")
            continue

        members: list[dict] = []
        if bid in groups:
            members = [by_admin[m] for m in groups[bid]["members"] if m in by_admin]
        elif bid in NAME_FALLBACK:
            rec = by_name.get(NAME_FALLBACK[bid].lower())
            members = [rec] if rec else []
        if not members:
            print(f"{bid:<22}{'-':>9}{'no MapsIndoors match':>36}")
            continue

        lon_min = min(m["bbox"][0] for m in members)
        lat_min = min(m["bbox"][1] for m in members)
        lon_max = max(m["bbox"][2] for m in members)
        lat_max = max(m["bbox"][3] for m in members)
        exp_x_min, exp_z_min = project(lon_min, lat_max)  # north edge -> smaller z
        exp_x_max, exp_z_max = project(lon_max, lat_min)
        exp_cx = (exp_x_min + exp_x_max) / 2.0
        exp_cz = (exp_z_min + exp_z_max) / 2.0
        exp_sx = abs(exp_x_max - exp_x_min)
        exp_sz = abs(exp_z_max - exp_z_min)

        local_center = world_to_local(dump, node["aabb_center"])
        act_sx, act_sz = node["aabb_size"][0], node["aabb_size"][2]
        d_units = math.hypot(local_center[0] - exp_cx, local_center[2] - exp_cz)
        d_m = d_units / scale
        ratio_e = act_sx / exp_sx if exp_sx else float("nan")
        ratio_n = act_sz / exp_sz if exp_sz else float("nan")

        flags = []
        legacy = bid in LEGACY_IDS
        if d_m > POSITION_FAIL_M:
            flags.append("POS")
        if not (SIZE_RATIO_RANGE[0] <= ratio_e <= SIZE_RATIO_RANGE[1]):
            flags.append("SIZE_E")
        if not (SIZE_RATIO_RANGE[0] <= ratio_n <= SIZE_RATIO_RANGE[1]):
            flags.append("SIZE_N")

        placement = placements.get(bid)
        if placement:
            door = placement["entrance"]
            exp_door = project(door["lon"], door["lat"])
            anchor = door["anchor"]
            ox, _, oz = node["origin"]
            bx = node["basis_x"]
            bz = node["basis_z"]
            act_door_x = ox + bx[0] * anchor[0] + bz[0] * anchor[2]
            act_door_z = oz + bx[2] * anchor[0] + bz[2] * anchor[2]
            act_local = world_to_local(dump, [act_door_x, node["origin"][1], act_door_z])
            door_err = math.hypot(act_local[0] - exp_door[0], act_local[2] - exp_door[1])
            if door_err > ENTRANCE_FAIL_UNITS:
                flags.append(f"DOOR({door_err:.1f}u)")

        underground = bid in UNDERGROUND_SPRAWL_IDS
        marker = " (legacy)" if legacy else (" (underground floors)" if underground else "")
        door_flags = [f for f in flags if f.startswith("DOOR")]
        blocking = door_flags if underground else flags
        if blocking and not legacy:
            failures.append(f"{bid}: {'/'.join(blocking)} d_pos={d_m:.1f}m ratios=({ratio_e:.2f},{ratio_n:.2f})")
        print(
            f"{bid:<22}{d_m:>9.1f}{act_sx:>8.0f}/{exp_sx:<9.0f}{act_sz:>8.0f}/{exp_sz:<9.0f}"
            f"{ratio_e:>8.2f}{ratio_n:>8.2f}  {'/'.join(flags)}{marker}"
        )

    # ---- Roads vs cached MapsIndoors route geometry -------------------------
    # Spur-only nodes are building DOOR endpoints (some hand-authored off the
    # walkable graph, e.g. kenneth_myers/acoustics) — they are not roads and
    # are excluded from the distance check.
    graph = json.loads((CAMPUS_EXPORT / "campus_paths_graph.json").read_text(encoding="utf-8"))
    non_spur_nodes: set[str] = set()
    for e in graph["edges"]:
        if e["highway"] != "spur":
            non_spur_nodes.add(str(e["a"]))
            non_spur_nodes.add(str(e["b"]))
    cloud: list[tuple[float, float]] = []
    for route_file in sorted((CAMPUS_EXPORT / "campus_route_cache").glob("route_*.json")):
        payload = json.loads(route_file.read_text(encoding="utf-8"))
        if str(payload.get("status")) != "OK":
            continue
        for r in payload.get("routes", []):
            for leg in r.get("legs", []):
                for step in leg.get("steps", []):
                    for p in step.get("geometry") or []:
                        if float(p.get("zLevel", p.get("z_level", 0.0)) or 0.0) == 0.0:
                            cloud.append(project(float(p["lng"]), float(p["lat"])))
    cell = 10.0
    grid: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for pt in cloud:
        grid.setdefault((int(pt[0] // cell), int(pt[1] // cell)), []).append(pt)

    def nearest(x: float, z: float) -> float:
        cx, cz = int(x // cell), int(z // cell)
        best = float("inf")
        for r in range(1, 4):
            for gx in range(cx - r, cx + r + 1):
                for gz in range(cz - r, cz + r + 1):
                    for px, pz in grid.get((gx, gz), ()):
                        best = min(best, math.hypot(px - x, pz - z))
            if best < (r - 0.5) * cell:
                break
        return best

    dists = sorted(
        nearest(x, z) / scale
        for nid, (x, z) in graph["nodes"].items()
        if nid in non_spur_nodes
    )
    n = len(dists)
    p50, p90, p99 = dists[n // 2], dists[int(n * 0.9)], dists[int(n * 0.99)]
    far = sum(1 for d in dists if d > 10.0)
    print()
    print(
        f"roads: {n} road nodes (spur doors excluded) vs {len(cloud)} cached "
        f"MapsIndoors points -> p50={p50:.2f}m p90={p90:.2f}m p99={p99:.2f}m "
        f"max={dists[-1]:.2f}m  >10m: {far}"
    )
    if p90 > 5.0 or dists[-1] > 30.0:
        failures.append(f"roads: p90={p90:.2f}m max={dists[-1]:.2f}m off the MapsIndoors geometry")

    rt = dump.get("roads_transform")
    if rt:
        bt = dump["buildings_transform"]
        drift = max(
            abs(rt["origin"][i] - bt["origin"][i]) for i in range(3)
        ) + max(abs(rt["basis"][r][c] - bt["basis"][r][c]) for r in range(3) for c in range(3))
        print(f"roads node transform matches Buildings transform: drift={drift:.6f}")
        if drift > 0.001:
            failures.append(f"roads: CampusRoads transform drifts {drift} from Buildings")

    print()
    if failures:
        print("AUDIT FAILURES:")
        for f in failures:
            print("  -", f)
        return 1
    print("CAMPUS ALIGNMENT AUDIT OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
