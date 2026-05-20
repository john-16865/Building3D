from __future__ import annotations

from dataclasses import dataclass
import csv
import json
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArtifactNames:
    visual_glb: str
    nav_glb: str
    manifest: str
    readme: str = "README.md"


def artifact_names(slug: str) -> ArtifactNames:
    return ArtifactNames(
        visual_glb=f"{slug}_visual.glb",
        nav_glb=f"{slug}_nav.glb",
        manifest=f"{slug}_manifest.json",
    )


def write_campus_index(export_root: str | Path, *, solution_id: str, records: list[dict[str, Any]]) -> Path:
    root = Path(export_root)
    root.mkdir(parents=True, exist_ok=True)
    ordered = sorted(records, key=lambda record: str(record.get("slug", "")))
    index = {
        "schema_version": 1,
        "solution_id": solution_id,
        "summary": _summary(ordered),
        "buildings": ordered,
    }
    index_path = root / "index.json"
    with index_path.open("w", encoding="utf-8") as handle:
        json.dump(index, handle, indent=2, sort_keys=True)
        handle.write("\n")
    _write_summary_csv(root / "summary.csv", ordered)
    return index_path


def _summary(records: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"total": len(records), "generated": 0, "skipped": 0, "failed": 0}
    for record in records:
        status = str(record.get("status", "failed"))
        if status not in counts:
            counts[status] = 0
        counts[status] += 1
    return counts


def _write_summary_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["slug", "admin_id", "display_name", "status", "rooms", "floors", "portals", "errors", "warnings"],
        )
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "slug": record.get("slug", ""),
                    "admin_id": record.get("admin_id", ""),
                    "display_name": record.get("display_name", ""),
                    "status": record.get("status", ""),
                    "rooms": record.get("rooms", 0),
                    "floors": record.get("floors", 0),
                    "portals": record.get("portals", 0),
                    "errors": "; ".join(record.get("errors", [])),
                    "warnings": "; ".join(record.get("warnings", [])),
                }
            )
