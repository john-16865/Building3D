from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


@dataclass
class FloorRecord:
    floor_name: str
    floor_index: int
    height: float = 0.0
    polygon_lonlat: list[list[float]] = field(default_factory=list)
    polygon_local: list[list[float]] = field(default_factory=list)


@dataclass
class RoomRecord:
    source_id: str
    external_id: str
    display_name: str
    building_admin_id: str
    floor_name: str
    floor_index: int
    category: str
    aliases: list[str]
    anchor_lonlat: list[float] | None
    anchor_local: list[float] | None
    polygon_lonlat: list[list[float]]
    polygon_local: list[list[float]]
    source_properties: dict[str, Any]


@dataclass
class PortalRecord:
    source_id: str
    external_id: str
    display_name: str
    building_admin_id: str
    floor_name: str
    floor_index: int
    kind: str
    group_id: str
    anchor_lonlat: list[float] | None
    anchor_local: list[float] | None
    polygon_lonlat: list[list[float]]
    polygon_local: list[list[float]]
    source_properties: dict[str, Any]


@dataclass
class NormalizedDataset:
    building_id: str
    building_admin_id: str
    building_name: str = ""
    floors: list[FloorRecord] = field(default_factory=list)
    rooms: list[RoomRecord] = field(default_factory=list)
    portals: list[PortalRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def normalize_locations(
    raw_locations: Any,
    *,
    building_admin_id: str,
    building_id: str,
    building_name: str = "Sir Owen G Glenn Building OGGB",
) -> NormalizedDataset:
    features = _as_features(raw_locations)
    candidates: list[tuple[dict[str, Any], dict[str, Any], list[list[float]]]] = []
    warnings: list[str] = []
    floor_names: set[str] = set()

    for feature in features:
        props = feature.get("properties") or {}
        if str(props.get("building") or props.get("buildingId") or building_admin_id).strip() != str(building_admin_id):
            continue
        ring = _first_polygon_ring(feature.get("geometry"))
        external_id = _clean_external_id(props)
        if not ring:
            warnings.append(f"Skipped {feature.get('id', '<unknown>')}: missing polygon geometry")
            continue
        if not external_id:
            warnings.append(f"Skipped {feature.get('id', '<unknown>')}: missing externalId/roomId")
            continue
        floor_name = str(props.get("floorName") or props.get("floor") or "0").strip()
        floor_names.add(floor_name)
        candidates.append((feature, props, ring))

    floor_index_by_name = {
        floor_name: index for index, floor_name in enumerate(sorted(floor_names, key=_floor_sort_key))
    }
    rooms: list[RoomRecord] = []
    portals: list[PortalRecord] = []

    for feature, props, ring in candidates:
        external_id = _clean_external_id(props)
        floor_name = str(props.get("floorName") or props.get("floor") or "0").strip()
        floor_index = floor_index_by_name[floor_name]
        display_name = str(props.get("name") or external_id)
        anchor = _anchor_lonlat(props)
        portal_kind = _portal_kind(display_name, str(props.get("type") or props.get("locationType") or ""), external_id)
        if portal_kind:
            portals.append(
                PortalRecord(
                    source_id=str(feature.get("id") or external_id),
                    external_id=external_id,
                    display_name=display_name,
                    building_admin_id=str(building_admin_id),
                    floor_name=floor_name,
                    floor_index=floor_index,
                    kind=portal_kind,
                    group_id=_portal_group_id(external_id, display_name, portal_kind),
                    anchor_lonlat=anchor,
                    anchor_local=None,
                    polygon_lonlat=ring,
                    polygon_local=[],
                    source_properties=dict(props),
                )
            )
            continue

        rooms.append(
            RoomRecord(
                source_id=str(feature.get("id") or external_id),
                external_id=external_id,
                display_name=display_name,
                building_admin_id=str(building_admin_id),
                floor_name=floor_name,
                floor_index=floor_index,
                category=_category(display_name, str(props.get("type") or "")),
                aliases=_aliases(external_id, building_id),
                anchor_lonlat=anchor,
                anchor_local=None,
                polygon_lonlat=ring,
                polygon_local=[],
                source_properties=dict(props),
            )
        )

    floors = [FloorRecord(floor_name=name, floor_index=index) for name, index in floor_index_by_name.items()]
    return NormalizedDataset(
        building_id=building_id,
        building_admin_id=str(building_admin_id),
        building_name=building_name,
        floors=sorted(floors, key=lambda floor: floor.floor_index),
        rooms=rooms,
        portals=portals,
        warnings=warnings,
    )


def dataset_from_dict(data: dict[str, Any]) -> NormalizedDataset:
    return NormalizedDataset(
        building_id=data["building_id"],
        building_admin_id=str(data["building_admin_id"]),
        building_name=data.get("building_name", ""),
        floors=[FloorRecord(**item) for item in data.get("floors", [])],
        rooms=[RoomRecord(**item) for item in data.get("rooms", [])],
        portals=[PortalRecord(**item) for item in data.get("portals", [])],
        warnings=list(data.get("warnings", [])),
    )


def _as_features(raw_locations: Any) -> list[dict[str, Any]]:
    if isinstance(raw_locations, list):
        return [item for item in raw_locations if isinstance(item, dict)]
    if isinstance(raw_locations, dict):
        for key in ("features", "locations", "data", "results"):
            value = raw_locations.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _first_polygon_ring(geometry: Any) -> list[list[float]]:
    if not isinstance(geometry, dict):
        return []
    if geometry.get("type") == "Polygon":
        coords = geometry.get("coordinates") or []
        if coords and isinstance(coords[0], list):
            return _clean_ring(coords[0])
    if geometry.get("type") == "MultiPolygon":
        coords = geometry.get("coordinates") or []
        if coords and coords[0] and isinstance(coords[0][0], list):
            return _clean_ring(coords[0][0])
    return []


def _clean_ring(ring: Any) -> list[list[float]]:
    result: list[list[float]] = []
    if not isinstance(ring, list):
        return result
    for point in ring:
        if isinstance(point, (list, tuple)) and len(point) >= 2:
            result.append([float(point[0]), float(point[1])])
    return result


def _clean_external_id(props: dict[str, Any]) -> str:
    value = props.get("externalId") or props.get("roomId") or props.get("external_id") or ""
    return str(value).strip()


def _anchor_lonlat(props: dict[str, Any]) -> list[float] | None:
    anchor = props.get("anchor") or {}
    coords = anchor.get("coordinates") if isinstance(anchor, dict) else None
    if isinstance(coords, list) and len(coords) >= 2:
        return [float(coords[0]), float(coords[1])]
    return None


def _aliases(external_id: str, building_id: str) -> list[str]:
    aliases = [external_id]
    if "-" in external_id:
        suffix = external_id.split("-", 1)[1]
        aliases.extend([suffix, external_id.replace("-", " ")])
        if building_id and "-" not in building_id and not building_id.isdigit():
            aliases.append(f"{building_id.upper()} {suffix}")
    return _dedupe(aliases)


def _category(name: str, source_type: str) -> str:
    text = f"{name} {source_type}".lower()
    if "lecture" in text or "auditorium" in text:
        return "lecture"
    if "lab" in text or "computer" in text:
        return "lab"
    if "toilet" in text or "bathroom" in text or "wc" in text:
        return "toilet"
    if "study" in text or "seminar" in text or "case" in text:
        return "study"
    if "parking" in text or "carpark" in text:
        return "parking"
    if "office" in text or "admin" in text:
        return "admin"
    return "other"


def _portal_kind(name: str, source_type: str, external_id: str) -> str:
    text = f"{name} {source_type} {external_id}".lower()
    if "elevator" in text or "lift" in text or re.search(r"e\d+$", external_id, re.IGNORECASE):
        return "elevator"
    if "stair" in text or re.search(r"s\d+$", external_id, re.IGNORECASE):
        return "stair"
    if "entrance" in text or "door" in text:
        return "door"
    return ""


def _portal_group_id(external_id: str, name: str, kind: str) -> str:
    prefix = "E" if kind == "elevator" else "S" if kind == "stair" else "D"
    match = re.search(rf"({prefix}\d+)$", external_id, re.IGNORECASE)
    if match:
        return match.group(1).upper()
    match = re.search(rf"{prefix}\s*(\d+)", name, re.IGNORECASE)
    if match:
        return f"{prefix}{match.group(1)}".upper()
    return "MAIN" if kind == "door" else "DEFAULT"


def _floor_sort_key(value: str) -> tuple[int, str]:
    try:
        return (int(value), value)
    except ValueError:
        return (0 if value.upper() == "G" else 10_000, value)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip()
        if clean and clean.lower() not in seen:
            seen.add(clean.lower())
            result.append(clean)
    return result
