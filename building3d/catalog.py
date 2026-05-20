from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any


def write_building_catalog(index_path: str | Path, output_path: str | Path) -> Path:
    index_file = Path(index_path)
    output_file = Path(output_path)
    with index_file.open("r", encoding="utf-8") as handle:
        index = json.load(handle)
    buildings = sorted(index.get("buildings", []), key=lambda item: (str(item.get("status", "")), str(item.get("admin_id", ""))))
    markdown = _render_catalog(index, buildings, index_file, output_file)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    output_file.write_text(markdown, encoding="utf-8")
    return output_file


def _render_catalog(index: dict[str, Any], buildings: list[dict[str, Any]], index_file: Path, output_file: Path) -> str:
    summary = index.get("summary", {})
    lines = [
        "# Auckland Building Catalog",
        "",
        "Generated from the Building3D campus index. Use this file as the human navigation page for the generated building packages.",
        "",
        "## Summary",
        "",
        f"- Total discovered buildings: {summary.get('total', len(buildings))}",
        f"- Generated packages: {summary.get('generated', 0)}",
        f"- Skipped buildings: {summary.get('skipped', 0)}",
        f"- Failed buildings: {summary.get('failed', 0)}",
        f"- Source index: [{_relative_link(output_file, index_file)}]({_relative_link(output_file, index_file)})",
        "",
        "## How To Find A Building",
        "",
        "- Search this file by building number, room-building prefix, building name, slug, or venue.",
        "- Open the manifest for room IDs, floor metadata, anchors, portal records, warnings, and source URLs.",
        "- Open the visual GLB for the generated 3D model and the nav GLB for simplified navigation geometry.",
        "- Skipped buildings were discovered but did not expose usable indoor room or portal geometry.",
        "",
        "## Quick Index",
        "",
    ]
    for building in sorted(buildings, key=lambda item: str(item.get("admin_id", ""))):
        heading = _heading(building)
        status = str(building.get("status", "unknown"))
        counts = _counts(building)
        lines.append(f"- [{heading}](#{_anchor(heading)}) - `{status}`, {counts}")

    lines.extend(["", "## Building Details", ""])
    for building in sorted(buildings, key=lambda item: str(item.get("admin_id", ""))):
        lines.extend(_render_building(building, output_file, index_file.parent))
    return "\n".join(lines).rstrip() + "\n"


def _render_building(building: dict[str, Any], output_file: Path, export_root: Path) -> list[str]:
    artifacts = building.get("artifacts", {}) if isinstance(building.get("artifacts"), dict) else {}
    warnings = building.get("warnings", []) or []
    errors = building.get("errors", []) or []
    source_urls = building.get("source_urls", []) or []
    heading = _heading(building)
    lines = [
        f'<a id="{_anchor(heading)}"></a>',
        f"### {heading}",
        "",
        f"- Status: `{building.get('status', 'unknown')}`",
        f"- Slug: `{building.get('slug', '')}`",
        f"- Venue: {building.get('venue_name') or 'Unknown'}",
        f"- MapsIndoors building ID: `{building.get('mapsindoors_id', '')}`",
        f"- External ID: `{building.get('external_id', '')}`",
        f"- Counts: {_counts(building)}",
        f"- Files: {_artifact_links(artifacts, output_file, export_root)}",
        f"- Notes: {_notes(warnings, errors)}",
    ]
    if source_urls:
        lines.append(f"- Source URLs: {_source_links(source_urls)}")
    lines.append("")
    return lines


def _heading(building: dict[str, Any]) -> str:
    return f"{building.get('admin_id', '')} - {building.get('display_name', '')}".strip()


def _counts(building: dict[str, Any]) -> str:
    return f"{building.get('rooms', 0)} rooms, {building.get('floors', 0)} floors, {building.get('portals', 0)} portals"


def _artifact_links(artifacts: dict[str, str], output_file: Path, export_root: Path) -> str:
    if not artifacts:
        return "none"
    labels = [
        ("manifest", "manifest"),
        ("visual_glb", "visual GLB"),
        ("nav_glb", "nav GLB"),
        ("readme", "README"),
    ]
    links = []
    for key, label in labels:
        value = artifacts.get(key)
        if value:
            links.append(f"[{label}]({_relative_link(output_file, export_root / value)})")
    return ", ".join(links) if links else "none"


def _notes(warnings: list[str], errors: list[str]) -> str:
    if errors:
        return f"{len(errors)} errors. First error: {errors[0]}"
    if warnings:
        return f"{len(warnings)} warnings. First warning: {warnings[0]}"
    return "none"


def _source_links(urls: list[str]) -> str:
    return ", ".join(f"[source {index + 1}]({url})" for index, url in enumerate(urls[:4]))


def _relative_link(output_file: Path, target: Path) -> str:
    return os.path.relpath(target, output_file.parent).replace("\\", "/")


def _anchor(text: str) -> str:
    anchor = re.sub(r"[^a-z0-9 -]", "", text.lower())
    anchor = re.sub(r"\s+", "-", anchor.strip())
    return re.sub(r"-+", "-", anchor)
