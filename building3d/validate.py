from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import struct

from building3d.artifacts import artifact_names

GLB_MAGIC = b"glTF"
GLB_VERSION = 2
JSON_CHUNK = 0x4E4F534A


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, message: str) -> None:
        self.errors.append(message)

    def add_warning(self, message: str) -> None:
        self.warnings.append(message)


def validate_dataset(dataset) -> ValidationResult:
    result = ValidationResult(warnings=list(dataset.warnings))
    if not dataset.building_admin_id:
        result.add_error("Dataset missing building_admin_id")
    if not dataset.rooms:
        result.add_warning("Dataset has no rooms")

    seen_aliases: dict[str, str] = {}
    for room in dataset.rooms:
        if not room.anchor_local:
            result.add_error(f"Room {room.external_id} missing local anchor")
        if not room.external_id:
            result.add_error("Room missing external_id")
        for alias in room.aliases:
            key = alias.lower()
            if key in seen_aliases and seen_aliases[key] != room.external_id:
                result.add_warning(f"Alias {alias} is shared by {seen_aliases[key]} and {room.external_id}")
            seen_aliases[key] = room.external_id

    for portal in dataset.portals:
        if portal.kind in {"stair", "elevator"} and not portal.group_id:
            result.add_warning(f"Portal {portal.external_id} missing group id")
    return result


def validate_export_package(path: str | Path, slug: str = "oggb") -> ValidationResult:
    root = Path(path)
    result = ValidationResult()
    names = artifact_names(slug)
    for filename in (names.visual_glb, names.nav_glb, names.manifest, names.readme):
        file_path = root / filename
        if not file_path.exists():
            result.add_error(f"Missing export package file: {filename}")
    for filename in (names.visual_glb, names.nav_glb):
        file_path = root / filename
        if file_path.exists():
            _validate_glb(file_path, result)
    return result


def _validate_glb(path: Path, result: ValidationResult) -> None:
    data = path.read_bytes()
    if len(data) < 20:
        result.add_error(f"{path.name} is too small to be a binary glTF file")
        return
    magic, version, declared_length = struct.unpack("<4sII", data[:12])
    if magic != GLB_MAGIC:
        result.add_error(f"{path.name} is not a binary glTF file")
        return
    if version != GLB_VERSION:
        result.add_error(f"{path.name} uses unsupported glTF binary version {version}")
    if declared_length != len(data):
        result.add_error(f"{path.name} declares length {declared_length} but file is {len(data)} bytes")
    json_length, chunk_type = struct.unpack("<II", data[12:20])
    if chunk_type != JSON_CHUNK:
        result.add_error(f"{path.name} first GLB chunk is not JSON")
    if 20 + json_length > len(data):
        result.add_error(f"{path.name} JSON chunk exceeds file length")
