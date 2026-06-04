from __future__ import annotations

from dataclasses import dataclass, field
import json
from math import cos, pi
from pathlib import Path
import re
from typing import Any

import yaml


DEFAULT_CAMPUS_SCENE = "res://Scene/Campus/CampusMain.tscn"
DEFAULT_BUILDINGS_PARENT = "GameLayer/HBox/VBoxContainer/ViewportContainer/SubViewport/Buildings"
DEFAULT_SCENE_TEMPLATE = "res://Scene/{building_id}_baked_full.tscn"


@dataclass(frozen=True)
class CampusPlacementReference:
    building_id: str = "science"
    lon: float = 174.76818825
    lat: float = -36.852228925
    local_anchor: list[float] = field(default_factory=lambda: [-18.06752, 0.0, 119.648002])
    origin: list[float] = field(default_factory=lambda: [-187.370620, 0.0, 870.065906])
    scale: float = 0.89260984


@dataclass(frozen=True)
class CampusPlacementConfig:
    campus_scene: str = DEFAULT_CAMPUS_SCENE
    buildings_parent: str = DEFAULT_BUILDINGS_PARENT
    scene_template: str = DEFAULT_SCENE_TEMPLATE
    reference: CampusPlacementReference = field(default_factory=CampusPlacementReference)
    entrance_marker_name: str = "Entrance"
    entrance_marker_size: list[float] = field(default_factory=lambda: [30.0, 30.0, 30.0])
    building_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_campus_placement_config(path: str | Path | None) -> CampusPlacementConfig:
    if path is None:
        return CampusPlacementConfig()
    config_path = Path(path)
    data = _read_yaml(config_path)
    reference_data = data.get("reference", {}) if isinstance(data.get("reference", {}), dict) else {}
    marker_data = data.get("entrance_marker", {}) if isinstance(data.get("entrance_marker", {}), dict) else {}
    overrides = data.get("overrides", {}) if isinstance(data.get("overrides", {}), dict) else {}
    return CampusPlacementConfig(
        campus_scene=str(data.get("campus_scene", DEFAULT_CAMPUS_SCENE)),
        buildings_parent=str(data.get("buildings_parent", DEFAULT_BUILDINGS_PARENT)),
        scene_template=str(data.get("scene_template", DEFAULT_SCENE_TEMPLATE)),
        reference=CampusPlacementReference(
            building_id=str(reference_data.get("building_id", "science")),
            lon=float(reference_data.get("lon", CampusPlacementReference().lon)),
            lat=float(reference_data.get("lat", CampusPlacementReference().lat)),
            local_anchor=_float_list(reference_data.get("local_anchor", CampusPlacementReference().local_anchor), 3),
            origin=_float_list(reference_data.get("origin", CampusPlacementReference().origin), 3),
            scale=float(reference_data.get("scale", CampusPlacementReference().scale)),
        ),
        entrance_marker_name=str(marker_data.get("name", "Entrance")),
        entrance_marker_size=_float_list(marker_data.get("size", [30.0, 30.0, 30.0]), 3),
        building_overrides={str(key).lower(): value for key, value in overrides.items() if isinstance(value, dict)},
    )


def build_campus_placement(manifest: dict[str, Any], config: CampusPlacementConfig | None = None) -> dict[str, Any]:
    config = config or CampusPlacementConfig()
    building = manifest.get("building", {}) if isinstance(manifest.get("building", {}), dict) else {}
    building_id = str(building.get("id") or "").strip()
    if not building_id:
        raise ValueError("Manifest building.id is required for campus placement")
    override = config.building_overrides.get(building_id.lower(), {})
    door = choose_placement_door(manifest, str(override.get("entry_id", "")).strip() or None)
    scale = float(override.get("scale", config.reference.scale))
    origin = _override_origin(override) or _compute_origin(door, config.reference, scale)
    scene_path = str(override.get("scene_path") or config.scene_template.format(building_id=building_id))
    node_name = str(override.get("node_name") or _node_name(building_id))
    basis = [
        [_round(scale, 8), 0.0, 0.0],
        [0.0, _round(scale, 8), 0.0],
        [0.0, 0.0, _round(-scale, 8)],
    ]
    origin = _round_vector(origin, 6)
    return {
        "schema_version": 1,
        "building_id": building_id,
        "display_name": str(building.get("display_name") or node_name),
        "node_name": node_name,
        "building_name": building_id,
        "campus_scene": config.campus_scene,
        "buildings_parent": config.buildings_parent,
        "scene_path": scene_path,
        "scale": _round(scale, 8),
        "transform": {
            "basis": basis,
            "origin": origin,
            "godot": _transform_string(scale, origin),
        },
        "entrance": {
            "entry_id": str(door.get("entry_id") or door.get("external_id") or ""),
            "external_id": str(door.get("external_id") or door.get("entry_id") or ""),
            "node_name": str(door.get("node_name") or ""),
            "display_name": str(door.get("display_name") or ""),
            "anchor": _round_vector(_anchor(door), 6),
            "lon": float(door.get("lon")),
            "lat": float(door.get("lat")),
            "marker_name": config.entrance_marker_name,
            "marker_size": _round_vector(config.entrance_marker_size, 6),
        },
        "reference": {
            "building_id": config.reference.building_id,
            "lon": config.reference.lon,
            "lat": config.reference.lat,
            "local_anchor": _round_vector(config.reference.local_anchor, 6),
            "origin": _round_vector(config.reference.origin, 6),
            "scale": _round(config.reference.scale, 8),
        },
    }


def write_campus_placement(
    manifest: dict[str, Any],
    output_path: str | Path,
    config: CampusPlacementConfig | None = None,
) -> Path:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    placement = build_campus_placement(manifest, config)
    path.write_text(json.dumps(placement, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return path


def choose_placement_door(manifest: dict[str, Any], entry_id: str | None = None) -> dict[str, Any]:
    doors = [door for door in manifest.get("external_doors", []) if isinstance(door, dict)]
    doors = [door for door in doors if _valid_door(door)]
    if not doors:
        raise ValueError("Manifest has no external doors with anchor, lon, and lat")
    if entry_id:
        for door in doors:
            if entry_id in {str(door.get("entry_id", "")), str(door.get("external_id", "")), str(door.get("node_name", ""))}:
                return door
        raise ValueError(f"No external door matched override entry_id {entry_id!r}")
    return max(enumerate(doors), key=lambda item: (*_door_score(item[1]), -item[0]))[1]


def _compute_origin(door: dict[str, Any], reference: CampusPlacementReference, scale: float) -> list[float]:
    ref_anchor = reference.local_anchor
    ref_entry = [
        reference.origin[0] + scale * float(ref_anchor[0]),
        reference.origin[1] + scale * float(ref_anchor[1]),
        reference.origin[2] - scale * float(ref_anchor[2]),
    ]
    metres_per_lon = 111_320.0 * cos(reference.lat * pi / 180.0)
    east_m = (float(door["lon"]) - reference.lon) * metres_per_lon
    north_m = (float(door["lat"]) - reference.lat) * 111_320.0
    campus_entry = [
        ref_entry[0] + scale * east_m,
        ref_entry[1],
        ref_entry[2] - scale * north_m,
    ]
    anchor = _anchor(door)
    return [
        campus_entry[0] - scale * float(anchor[0]),
        campus_entry[1] - scale * float(anchor[1]),
        campus_entry[2] + scale * float(anchor[2]),
    ]


def _override_origin(override: dict[str, Any]) -> list[float] | None:
    origin = override.get("origin")
    if origin is None:
        return None
    return _float_list(origin, 3)


def _door_score(door: dict[str, Any]) -> tuple[int, int, int]:
    confidence = str(door.get("confidence") or "unknown").strip().lower()
    confidence_score = {"high": 3, "medium": 2, "low": 1, "unknown": 0}.get(confidence, 0)
    support_score = int(door.get("supporting_routes") or 0)
    text = f"{door.get('node_name', '')} {door.get('display_name', '')}".lower()
    main_score = 1 if "maindoor" in text.replace(" ", "") or "main entrance" in text else 0
    return (confidence_score, support_score, main_score)


def _valid_door(door: dict[str, Any]) -> bool:
    try:
        _anchor(door)
        float(door["lon"])
        float(door["lat"])
    except (KeyError, TypeError, ValueError):
        return False
    return True


def _anchor(door: dict[str, Any]) -> list[float]:
    anchor = door.get("anchor") or door.get("local") or door.get("door_local")
    return _float_list(anchor, 3)


def _float_list(value: Any, length: int) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise ValueError(f"Expected {length} numeric values, got {value!r}")
    return [float(item) for item in value]


def _round_vector(values: list[float], digits: int) -> list[float]:
    return [_round(float(value), digits) for value in values]


def _round(value: float, digits: int) -> float:
    rounded = round(float(value), digits)
    return 0.0 if abs(rounded) < 10 ** -digits else rounded


def _transform_string(scale: float, origin: list[float]) -> str:
    values = [
        scale,
        0.0,
        0.0,
        0.0,
        scale,
        0.0,
        0.0,
        0.0,
        -scale,
        origin[0],
        origin[1],
        origin[2],
    ]
    return "Transform3D(%s)" % ", ".join(_fmt(value) for value in values)


def _fmt(value: float) -> str:
    if abs(value) < 0.000000005:
        return "0"
    return f"{value:.8f}".rstrip("0").rstrip(".")


def _node_name(building_id: str) -> str:
    parts = [part for part in re.split(r"[^a-zA-Z0-9]+", building_id) if part]
    return "".join(part[:1].upper() + part[1:] for part in parts) or "Building"


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded
