from __future__ import annotations

from dataclasses import replace
from math import cos, pi
from typing import Iterable


class LocalProjector:
    """Project lon/lat positions into local Godot-style metre coordinates."""

    def __init__(self, origin_lon: float, origin_lat: float) -> None:
        self.origin_lon = origin_lon
        self.origin_lat = origin_lat
        self._metres_per_degree_lat = 111_320.0
        self._metres_per_degree_lon = 111_320.0 * cos(origin_lat * pi / 180.0)

    def to_local(self, lon: float, lat: float, floor_height: float = 0.0) -> list[float]:
        x = (lon - self.origin_lon) * self._metres_per_degree_lon
        z = (lat - self.origin_lat) * self._metres_per_degree_lat
        return [_round_zero(x), _round_zero(floor_height), _round_zero(z)]

    def project_ring(self, ring: Iterable[Iterable[float]], floor_height: float = 0.0) -> list[list[float]]:
        return [self.to_local(float(point[0]), float(point[1]), floor_height) for point in ring]


def project_dataset(dataset, origin_lon: float, origin_lat: float, floor_heights: dict[str, float]):
    """Return a copy of a normalized dataset with local metre coordinates filled in."""
    from building3d.normalize import NormalizedDataset

    projector = LocalProjector(origin_lon=origin_lon, origin_lat=origin_lat)
    projected_rooms = []
    for room in dataset.rooms:
        height = floor_heights.get(str(room.floor_name), float(room.floor_index) * 4.2)
        anchor_local = None
        if room.anchor_lonlat:
            anchor_local = projector.to_local(room.anchor_lonlat[0], room.anchor_lonlat[1], height)
        polygon_local = projector.project_ring(room.polygon_lonlat, height) if room.polygon_lonlat else []
        projected_rooms.append(replace(room, anchor_local=anchor_local, polygon_local=polygon_local))

    projected_portals = []
    for portal in dataset.portals:
        height = floor_heights.get(str(portal.floor_name), float(portal.floor_index) * 4.2)
        anchor_local = None
        if portal.anchor_lonlat:
            anchor_local = projector.to_local(portal.anchor_lonlat[0], portal.anchor_lonlat[1], height)
        polygon_local = projector.project_ring(portal.polygon_lonlat, height) if portal.polygon_lonlat else []
        projected_portals.append(replace(portal, anchor_local=anchor_local, polygon_local=polygon_local))

    projected_floors = []
    for floor in dataset.floors:
        height = floor_heights.get(str(floor.floor_name), float(floor.floor_index) * 4.2)
        polygon_local = projector.project_ring(floor.polygon_lonlat, height) if floor.polygon_lonlat else []
        projected_floors.append(replace(floor, height=height, polygon_local=polygon_local))

    return NormalizedDataset(
        building_id=dataset.building_id,
        building_admin_id=dataset.building_admin_id,
        building_name=dataset.building_name,
        floors=projected_floors,
        rooms=projected_rooms,
        portals=projected_portals,
        warnings=list(dataset.warnings),
    )


def _round_zero(value: float) -> float:
    rounded = round(value, 6)
    return 0.0 if abs(rounded) < 0.000001 else rounded
