from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class GeneratorConfig:
    project_root: Path
    building_id: str
    building_admin_id: str
    display_name: str
    origin_lon: float
    origin_lat: float
    raw_dir: Path
    processed_dir: Path
    export_dir: Path
    building_details_url: str
    locations_url: str
    take: int
    floor_heights: dict[str, float]


@dataclass(frozen=True)
class SolutionConfig:
    project_root: Path
    solution_id: str
    raw_root: Path
    processed_root: Path
    export_root: Path
    buildings_sync_url: str
    venues_sync_url: str
    locations_url: str
    building_details_url_template: str
    take: int
    default_floor_spacing: float
    basement_floor_spacing: float
    failure_policy: str
    building_admin_ids: list[str]
    venue_ids: list[str]


@dataclass(frozen=True)
class BuildingGroupConfig:
    id: str
    display_name: str
    members: list[str]
    aliases: list[str]
    primary_member: str = ""


@dataclass(frozen=True)
class BuildingGroupsConfig:
    project_root: Path
    groups: list[BuildingGroupConfig]

    def get(self, group_id: str) -> BuildingGroupConfig:
        normalized = group_id.strip().lower()
        for group in self.groups:
            if group.id.lower() == normalized:
                return group
            if normalized in {alias.lower() for alias in group.aliases}:
                return group
        raise KeyError(f"Unknown building group: {group_id}")


def load_config(path: str | Path) -> GeneratorConfig:
    config_path = Path(path).resolve()
    project_root = config_path.parent.parent
    data = _read_yaml(config_path)
    building = data.get("building", {})
    mapsindoors = data.get("mapsindoors", {})
    paths = data.get("paths", {})

    origin = building.get("origin", [])
    if len(origin) != 2:
        raise ValueError("building.origin must contain [lon, lat]")

    def rel_path(value: str, default: str) -> Path:
        return (project_root / paths.get(value, default)).resolve()

    return GeneratorConfig(
        project_root=project_root,
        building_id=str(building.get("id", "oggb")),
        building_admin_id=str(building.get("admin_id", "260")),
        display_name=str(building.get("display_name", "Sir Owen G Glenn Building OGGB")),
        origin_lon=float(origin[0]),
        origin_lat=float(origin[1]),
        raw_dir=rel_path("raw_dir", "data/raw/oggb"),
        processed_dir=rel_path("processed_dir", "data/processed/oggb"),
        export_dir=rel_path("export_dir", "exports/oggb"),
        building_details_url=str(mapsindoors.get("building_details_url", "")),
        locations_url=str(mapsindoors.get("locations_url", "")),
        take=int(mapsindoors.get("take", 1000)),
        floor_heights={str(k): float(v) for k, v in data.get("floor_heights", {}).items()},
    )


def load_group_config(path: str | Path) -> BuildingGroupsConfig:
    config_path = Path(path).resolve()
    project_root = config_path.parent.parent
    data = _read_yaml(config_path)
    groups = []
    for item in data.get("groups", []):
        if not isinstance(item, dict):
            raise ValueError("Each group entry must be a mapping")
        group_id = str(item.get("id", "")).strip()
        members = [str(value).strip() for value in item.get("members", []) if str(value).strip()]
        if not group_id:
            raise ValueError("Building group missing id")
        if not members:
            raise ValueError(f"Building group {group_id} must have at least one member")
        aliases = [str(value).strip() for value in item.get("aliases", []) if str(value).strip()]
        aliases = _dedupe([group_id, *members, *aliases])
        groups.append(
            BuildingGroupConfig(
                id=group_id,
                display_name=str(item.get("display_name", group_id)).strip() or group_id,
                members=members,
                aliases=aliases,
                primary_member=str(item.get("primary_member", "")).strip(),
            )
        )
    return BuildingGroupsConfig(project_root=project_root, groups=groups)


def load_solution_config(path: str | Path) -> SolutionConfig:
    config_path = Path(path).resolve()
    project_root = config_path.parent.parent
    data = _read_yaml(config_path)
    solution = data.get("solution", {})
    mapsindoors = data.get("mapsindoors", {})
    paths = data.get("paths", {})
    generation = data.get("generation", {})
    filters = data.get("filters", {})
    solution_id = str(solution.get("id", data.get("solution_id", "auckland")))

    def rel_path(value: str, default: str) -> Path:
        return (project_root / paths.get(value, default)).resolve()

    return SolutionConfig(
        project_root=project_root,
        solution_id=solution_id,
        raw_root=rel_path("raw_root", f"data/raw/{solution_id}"),
        processed_root=rel_path("processed_root", f"data/processed/{solution_id}"),
        export_root=rel_path("export_root", f"exports/{solution_id}"),
        buildings_sync_url=str(mapsindoors.get("buildings_sync_url", "https://api-us-east.mapsindoors.com/sync/buildings")),
        venues_sync_url=str(mapsindoors.get("venues_sync_url", "https://api-us-east.mapsindoors.com/sync/venues")),
        locations_url=str(mapsindoors.get("locations_url", f"https://api-us-east.mapsindoors.com/{solution_id}/api/locations")),
        building_details_url_template=str(
            mapsindoors.get(
                "building_details_url_template",
                f"https://api-us-east.mapsindoors.com/{solution_id}/api/buildings/details/{{building_id}}?v=3",
            )
        ),
        take=int(mapsindoors.get("take", 1000)),
        default_floor_spacing=float(generation.get("default_floor_spacing", 4.2)),
        basement_floor_spacing=float(generation.get("basement_floor_spacing", 3.0)),
        failure_policy=str(generation.get("failure_policy", "continue")),
        building_admin_ids=[str(value) for value in filters.get("building_admin_ids", [])],
        venue_ids=[str(value) for value in filters.get("venue_ids", [])],
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} must contain a YAML mapping")
    return loaded


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        clean = value.strip()
        key = clean.lower()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result
