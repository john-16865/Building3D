from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import requests


def fetch_source_data(config) -> dict[str, Path]:
    config.raw_dir.mkdir(parents=True, exist_ok=True)
    outputs: dict[str, Path] = {}

    if config.building_details_url:
        building = _get_json(config.building_details_url)
        path = config.raw_dir / "building.json"
        _write_json(path, building)
        outputs["building"] = path

    skip = 0
    page_index = 0
    while True:
        url = config.locations_url
        params = {"building": config.building_admin_id, "take": config.take, "skip": skip, "v": 5}
        page = _get_json(url, params=params)
        path = config.raw_dir / f"locations_{skip:04d}.json"
        _write_json(path, page)
        outputs[f"locations_{skip:04d}"] = path
        count = len(_extract_items(page))
        page_index += 1
        if count < config.take or page_index > 20:
            break
        skip += config.take
    return outputs


def load_raw_locations(raw_dir: Path) -> list[dict[str, Any]]:
    locations: list[dict[str, Any]] = []
    for path in sorted(raw_dir.glob("locations_*.json")):
        with path.open("r", encoding="utf-8") as handle:
            locations.extend(_extract_items(json.load(handle)))
    return locations


def load_building_name(raw_dir: Path, default: str) -> str:
    path = raw_dir / "building.json"
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    props = data.get("properties") if isinstance(data, dict) else {}
    info = data.get("buildingInfo") if isinstance(data, dict) and isinstance(data.get("buildingInfo"), dict) else {}
    return str((props or {}).get("name") or info.get("name") or data.get("name") or default)


def source_urls(config) -> list[str]:
    urls = []
    if config.building_details_url:
        urls.append(config.building_details_url)
    if config.locations_url:
        urls.append(f"{config.locations_url}?building={config.building_admin_id}&take={config.take}")
    return urls


def _get_json(url: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params, timeout=120, headers={"User-Agent": "Building3D/0.1"})
    response.raise_for_status()
    return response.json()


def _write_json(path: Path, data: Any) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def _extract_items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("features", "locations", "data", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []
