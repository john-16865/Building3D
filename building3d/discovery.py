from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


@dataclass(frozen=True)
class VenueRecord:
    mapsindoors_id: str
    display_name: str


@dataclass(frozen=True)
class BuildingInventoryRecord:
    slug: str
    mapsindoors_id: str
    admin_id: str
    external_id: str
    display_name: str
    venue_id: str
    venue_name: str
    origin: list[float]
    bbox: list[float]
    default_floor: str
    floor_keys: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def building_slug(admin_id: str, name: str) -> str:
    clean_name = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    clean_admin = re.sub(r"[^a-z0-9]+", "-", str(admin_id).lower()).strip("-")
    return f"{clean_admin}-{clean_name}" if clean_name else clean_admin


def parse_venue_inventory(raw_venues: Any) -> dict[str, VenueRecord]:
    records: dict[str, VenueRecord] = {}
    for item in _extract_items(raw_venues):
        venue_id = str(item.get("id") or "").strip()
        if not venue_id:
            continue
        info = item.get("venueInfo") if isinstance(item.get("venueInfo"), dict) else {}
        name = str(info.get("name") or item.get("name") or venue_id).strip()
        records[venue_id] = VenueRecord(mapsindoors_id=venue_id, display_name=name)
    return records


def parse_building_inventory(raw_buildings: Any, *, venues_by_id: dict[str, VenueRecord] | None = None) -> list[BuildingInventoryRecord]:
    venues_by_id = venues_by_id or {}
    records: list[BuildingInventoryRecord] = []
    for item in _extract_items(raw_buildings):
        admin_id = str(item.get("administrativeId") or "").strip()
        mapsindoors_id = str(item.get("id") or "").strip()
        if not admin_id or not mapsindoors_id:
            continue
        info = item.get("buildingInfo") if isinstance(item.get("buildingInfo"), dict) else {}
        display_name = str(info.get("name") or item.get("name") or item.get("externalId") or admin_id).strip()
        venue_id = str(item.get("venueId") or "").strip()
        venue = venues_by_id.get(venue_id)
        floors = item.get("floors") if isinstance(item.get("floors"), dict) else {}
        records.append(
            BuildingInventoryRecord(
                slug=building_slug(admin_id, display_name),
                mapsindoors_id=mapsindoors_id,
                admin_id=admin_id,
                external_id=str(item.get("externalId") or "").strip(),
                display_name=display_name,
                venue_id=venue_id,
                venue_name=venue.display_name if venue else "",
                origin=_origin(item),
                bbox=_bbox(item),
                default_floor=str(item.get("defaultFloor") or "0"),
                floor_keys=sorted([str(key) for key in floors.keys()], key=_floor_sort_key),
            )
        )
    return sorted(records, key=lambda record: record.slug)


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("features", "buildings", "venues", "data", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _origin(item: dict[str, Any]) -> list[float]:
    anchor = item.get("anchor") if isinstance(item.get("anchor"), dict) else {}
    coords = anchor.get("coordinates") if isinstance(anchor, dict) else None
    if isinstance(coords, list) and len(coords) >= 2:
        return [float(coords[0]), float(coords[1])]
    bbox = _bbox(item)
    if len(bbox) == 4:
        return [(bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2]
    return [0.0, 0.0]


def _bbox(item: dict[str, Any]) -> list[float]:
    geometry = item.get("geometry") if isinstance(item.get("geometry"), dict) else {}
    bbox = geometry.get("bbox") if isinstance(geometry, dict) else None
    if isinstance(bbox, list) and len(bbox) == 4:
        return [float(value) for value in bbox]
    return []


def _floor_sort_key(value: str) -> tuple[int, str]:
    try:
        return (int(value), value)
    except ValueError:
        return (0 if value.upper() == "G" else 10_000, value)
