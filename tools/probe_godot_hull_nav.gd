extends SceneTree

const FROM_POINT := Vector3(-19.35, 0.0, 118.065)
const TO_POINT := Vector3(24.417, 0.0, 10.613)
const HULL := [
	Vector3(-25.6532, 0.0, 123.17486),
	Vector3(-16.518228, 0.0, 115.76942),
	Vector3(-4.474928, 0.0, 105.404952),
	Vector3(21.2144, 0.0, 81.883208),
	Vector3(51.660661, 0.0, 24.119079),
	Vector3(44.495587, 0.0, 15.292769),
	Vector3(3.481012, 0.0, -31.356542),
	Vector3(-13.474968, 0.0, -27.260314),
	Vector3(-13.902062, 0.0, -26.855069),
	Vector3(-19.084224, 0.0, -19.190676),
	Vector3(-53.675692, 0.0, 32.962659),
	Vector3(-73.939814, 0.0, 63.634886),
]


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	for mode in ["polygon", "fan"]:
		for reverse in [false, true]:
			await _probe(mode, reverse)
	quit()


func _probe(mode: String, reverse: bool) -> void:
		var region := NavigationRegion3D.new()
		var mesh := NavigationMesh.new()
		var vertices := PackedVector3Array(HULL)
		var polygon := PackedInt32Array()
		if reverse:
			for i in range(vertices.size() - 1, -1, -1):
				polygon.append(i)
		else:
			for i in range(vertices.size()):
				polygon.append(i)
		mesh.vertices = vertices
		if mode == "fan":
			for i in range(1, polygon.size() - 1):
				mesh.add_polygon(PackedInt32Array([polygon[0], polygon[i], polygon[i + 1]]))
		else:
			mesh.add_polygon(polygon)
		region.navigation_mesh = mesh
		get_root().add_child(region)
		await physics_frame
		var map := region.get_navigation_map()
		NavigationServer3D.map_set_active(map, true)
		var sync := await _await_map_sync(map, 60)
		var path := NavigationServer3D.map_get_path(map, FROM_POINT, TO_POINT, true, 1)
		print(JSON.stringify({
			"mode": mode,
			"reverse": reverse,
			"area": _signed_area_xz(polygon, vertices),
			"sync": sync,
			"closest_from": _vec(NavigationServer3D.map_get_closest_point(map, FROM_POINT)),
			"closest_to": _vec(NavigationServer3D.map_get_closest_point(map, TO_POINT)),
			"path_count": path.size(),
			"path": _path(path),
			"regions": NavigationServer3D.map_get_regions(map).size(),
		}))
		region.queue_free()
		await physics_frame


func _signed_area_xz(polygon: PackedInt32Array, vertices: PackedVector3Array) -> float:
	var area := 0.0
	for offset in range(polygon.size()):
		var vertex := vertices[polygon[offset]]
		var next_vertex := vertices[polygon[(offset + 1) % polygon.size()]]
		area += vertex.x * next_vertex.z - next_vertex.x * vertex.z
	return snappedf(area / 2.0, 0.001)


func _await_map_sync(map: RID, max_frames: int) -> Dictionary:
	var iterations := []
	var start_iteration := NavigationServer3D.map_get_iteration_id(map)
	for frame in range(max_frames):
		NavigationServer3D.map_force_update(map)
		await physics_frame
		var iteration := NavigationServer3D.map_get_iteration_id(map)
		iterations.append(iteration)
		if frame >= 5 and iteration > 0 and (start_iteration == 0 or iteration > start_iteration):
			return {"iteration": iteration, "start_iteration": start_iteration, "frames": frame + 1, "iterations": iterations}
	return {"iteration": NavigationServer3D.map_get_iteration_id(map), "start_iteration": start_iteration, "frames": max_frames, "iterations": iterations}


func _path(path: PackedVector3Array) -> Array:
	var out := []
	for point in path:
		out.append(_vec(point))
	return out


func _vec(value: Vector3) -> Array:
	return [snappedf(value.x, 0.001), snappedf(value.y, 0.001), snappedf(value.z, 0.001)]
