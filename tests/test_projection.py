from building3d.projection import LocalProjector


def test_projector_maps_origin_to_zero_and_uses_metre_scale():
    projector = LocalProjector(origin_lon=174.771359951698, origin_lat=-36.8529245870962)

    origin = projector.to_local(174.771359951698, -36.8529245870962, floor_height=4.2)
    east = projector.to_local(174.771459951698, -36.8529245870962, floor_height=4.2)
    north = projector.to_local(174.771359951698, -36.8528245870962, floor_height=4.2)

    assert origin == [0.0, 4.2, 0.0]
    assert 8.5 < east[0] < 9.2
    assert east[1] == 4.2
    assert abs(east[2]) < 0.01
    assert 11.0 < north[2] < 11.2


def test_project_polygon_preserves_ring_shape():
    projector = LocalProjector(origin_lon=174.771359951698, origin_lat=-36.8529245870962)
    ring = [
        [174.771359951698, -36.8529245870962],
        [174.771459951698, -36.8529245870962],
        [174.771459951698, -36.8528245870962],
        [174.771359951698, -36.8529245870962],
    ]

    projected = projector.project_ring(ring, floor_height=0.0)

    assert len(projected) == 4
    assert projected[0] == [0.0, 0.0, 0.0]
    assert projected[-1] == projected[0]
