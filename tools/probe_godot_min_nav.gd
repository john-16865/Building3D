extends SceneTree


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	for order in [[0, 1, 2, 3], [3, 2, 1, 0]]:
		var region := NavigationRegion3D.new()
		var mesh := NavigationMesh.new()
		mesh.vertices = PackedVector3Array([
			Vector3(0, 0, 0),
			Vector3(10, 0, 0),
			Vector3(10, 0, 10),
			Vector3(0, 0, 10),
		])
		mesh.add_polygon(PackedInt32Array(order))
		region.navigation_mesh = mesh
		get_root().add_child(region)
		await physics_frame
		var map: RID = region.get_navigation_map()
		NavigationServer3D.map_set_active(map, true)
		var iterations := []
		var start_iteration := NavigationServer3D.map_get_iteration_id(map)
		for frame in range(24):
			NavigationServer3D.map_force_update(map)
			await physics_frame
			iterations.append(NavigationServer3D.map_get_iteration_id(map))
			if frame >= 5 and NavigationServer3D.map_get_iteration_id(map) > 0 and (start_iteration == 0 or NavigationServer3D.map_get_iteration_id(map) > start_iteration):
				break
		var path := NavigationServer3D.map_get_path(map, Vector3(1, 0, 1), Vector3(9, 0, 9), true, 1)
		var closest := NavigationServer3D.map_get_closest_point(map, Vector3(1, 0, 1))
		var region_closest := NavigationServer3D.region_get_closest_point(region.get_rid(), Vector3(1, 0, 1))
		print(JSON.stringify({
			"order": order,
			"iterations": iterations,
			"regions": NavigationServer3D.map_get_regions(map).size(),
			"path_count": path.size(),
			"path": _path(path),
			"closest": _vec(closest),
			"region_closest": _vec(region_closest),
		}))
		region.queue_free()
		await physics_frame
	quit()


func _path(path: PackedVector3Array) -> Array:
	var out := []
	for point in path:
		out.append(_vec(point))
	return out


func _vec(value: Vector3) -> Array:
	return [snappedf(value.x, 0.001), snappedf(value.y, 0.001), snappedf(value.z, 0.001)]
