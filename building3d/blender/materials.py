from __future__ import annotations


MATERIAL_COLORS = {
    "floor": (0.72, 0.72, 0.72, 1.0),
    "lecture": (0.24, 0.52, 0.92, 1.0),
    "lab": (0.20, 0.67, 0.55, 1.0),
    "study": (0.85, 0.66, 0.22, 1.0),
    "toilet": (0.62, 0.54, 0.86, 1.0),
    "parking": (0.35, 0.36, 0.38, 1.0),
    "admin": (0.70, 0.48, 0.35, 1.0),
    "other": (0.58, 0.62, 0.66, 1.0),
    "wall_low": (0.88, 0.88, 0.84, 1.0),
    "anchor": (0.05, 0.85, 0.35, 1.0),
    "walkable_path": (0.0, 0.9, 0.85, 1.0),
    "route_centerline": (1.0, 0.18, 0.04, 1.0),
    "stair": (0.95, 0.45, 0.12, 1.0),
    "elevator": (0.12, 0.65, 0.95, 1.0),
    "door": (0.20, 0.20, 0.20, 1.0),
    "door_point_high": (1.0, 0.05, 0.05, 1.0),
    "door_point_medium": (1.0, 0.7, 0.0, 1.0),
    "door_point_low": (0.55, 0.05, 0.95, 1.0),
    "door_point_unknown": (1.0, 1.0, 1.0, 1.0),
    "default": (0.65, 0.65, 0.65, 1.0),
}


def material_color(name: str) -> tuple[float, float, float, float]:
    return MATERIAL_COLORS.get(name, MATERIAL_COLORS["default"])
