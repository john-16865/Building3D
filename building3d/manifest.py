from __future__ import annotations

import hashlib
import json
from typing import Any

from building3d.geometry import _room_mesh_name
from building3d.unimate import portal_node_name, room_node_name


def build_manifest(dataset, source_urls: list[str]) -> dict[str, Any]:
    manifest = {
        "schema_version": 1,
        "building": {
            "id": dataset.building_id,
            "mapsindoors_admin_id": dataset.building_admin_id,
            "display_name": dataset.building_name,
        },
        "floors": [
            {
                "floor_name": floor.floor_name,
                "floor_index": floor.floor_index,
                "height": floor.height,
            }
            for floor in dataset.floors
        ],
        "rooms": [
            {
                "external_id": room.external_id,
                "display_name": room.display_name,
                "floor_index": room.floor_index,
                "floor_name": room.floor_name,
                "logical_building_id": dataset.building_id,
                "mesh_name": _room_mesh_name(room.external_id, room.floor_name),
                "node_name": room_node_name(room.external_id, room.display_name),
                "anchor": room.anchor_local,
                "aliases": room.aliases,
                "category": room.category,
                "source_building_admin_id": room.building_admin_id,
                "source_id": room.source_id,
            }
            for room in dataset.rooms
        ],
        "portals": [
            {
                "external_id": portal.external_id,
                "display_name": portal.display_name,
                "floor_index": portal.floor_index,
                "floor_name": portal.floor_name,
                "kind": portal.kind,
                "group_id": portal.group_id,
                "logical_building_id": dataset.building_id,
                "node_name": portal_node_name(portal.external_id, portal.display_name, portal.kind, portal.group_id),
                "anchor": portal.anchor_local,
                "source_building_admin_id": portal.building_admin_id,
                "source_id": portal.source_id,
            }
            for portal in dataset.portals
        ],
        "nav": {
            "regions": [
                {"floor_index": floor.floor_index, "floor_name": floor.floor_name}
                for floor in dataset.floors
            ],
            "links": _nav_links(dataset.portals),
            "room_targets": [
                {
                    "external_id": room.external_id,
                    "floor_index": room.floor_index,
                    "anchor": room.anchor_local,
                    "node_name": room_node_name(room.external_id, room.display_name),
                    "logical_building_id": dataset.building_id,
                    "source_building_admin_id": room.building_admin_id,
                    "source_id": room.source_id,
                }
                for room in dataset.rooms
                if room.anchor_local is not None
            ],
        },
        "source_urls": list(source_urls),
        "warnings": list(dataset.warnings),
    }
    manifest["generation_hash"] = _hash_manifest(manifest)
    return manifest


def refresh_generation_hash(manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["generation_hash"] = _hash_manifest(manifest)
    return manifest


def write_manifest(manifest: dict[str, Any], path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _hash_manifest(manifest: dict[str, Any]) -> str:
    stable = {key: value for key, value in manifest.items() if key != "generation_hash"}
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _nav_links(portals) -> list[dict[str, Any]]:
    by_group: dict[tuple[str, str], list] = {}
    for portal in portals:
        if portal.kind not in {"stair", "elevator"}:
            continue
        by_group.setdefault((portal.kind, portal.group_id), []).append(portal)

    links: list[dict[str, Any]] = []
    for (kind, group_id), grouped in by_group.items():
        ordered = sorted(grouped, key=lambda portal: portal.floor_index)
        for index in range(len(ordered) - 1):
            start = ordered[index]
            end = ordered[index + 1]
            links.append(
                {
                    "kind": kind,
                    "group_id": group_id,
                    "from_external_id": start.external_id,
                    "to_external_id": end.external_id,
                    "from_source_id": start.source_id,
                    "to_source_id": end.source_id,
                    "from_node_name": portal_node_name(start.external_id, start.display_name, start.kind, start.group_id),
                    "to_node_name": portal_node_name(end.external_id, end.display_name, end.kind, end.group_id),
                    "from_floor_index": start.floor_index,
                    "to_floor_index": end.floor_index,
                    "from_anchor": start.anchor_local,
                    "to_anchor": end.anchor_local,
                    "bidirectional": True,
                }
            )
    return links
