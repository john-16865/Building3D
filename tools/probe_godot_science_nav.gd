extends SceneTree

const SCENE_PATH := "res://Scene/science.tscn"
const SAMPLE_ROOMS := ["MainDoor", "Door4", "302 G00C2_Unclassified Facilities", "303 SB00S4_Stairs_Set4"]
const SAMPLE_PATHS := [
	["MainDoor", "302 G00C2_Unclassified Facilities"],
	["MainDoor", "Door4"],
]

var _stats := {
	"scene_path": SCENE_PATH,
	"checks": {},
	"counts": {},
	"floor_summaries": [],
	"sample_points": [],
	"errors": [],
}


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var scene := load(SCENE_PATH)
	if not scene is PackedScene:
		_stats["errors"].append("Science scene did not load as PackedScene.")
		_finish()
		return

	var root_node: Node3D = scene.instantiate()
	get_root().add_child(root_node)
	await physics_frame
	await physics_frame
	var initial_map: RID = root_node.get_world_3d().get_navigation_map()
	NavigationServer3D.map_set_active(initial_map, true)
	_stats["initial_map_sync"] = await _await_map_sync(initial_map, 60)

	var floors := root_node.get_node_or_null("Floors")
	if floors == null:
		_stats["errors"].append("Science scene is missing Floors.")
		_finish()
		return

	var world_map: RID = root_node.get_world_3d().get_navigation_map()
	_stats["counts"]["world_map_iteration"] = NavigationServer3D.map_get_iteration_id(world_map)
	_stats["counts"]["map_regions"] = NavigationServer3D.map_get_regions(world_map).size()
	var empty_meshes := 0
	var total_polygons := 0
	for floor in floors.get_children():
		var floor_summary := _floor_summary(floor)
		_stats["floor_summaries"].append(floor_summary)
		total_polygons += int(floor_summary["polygons"])
		if int(floor_summary["vertices"]) == 0 or int(floor_summary["polygons"]) == 0:
			empty_meshes += 1
	_stats["counts"]["empty_navigation_meshes"] = empty_meshes
	_stats["counts"]["navigation_mesh_polygons"] = total_polygons
	if empty_meshes > 0:
		_stats["errors"].append("At least one floor has an empty NavigationMesh.")

	for room_name in SAMPLE_ROOMS:
		var sample := _sample_room(root_node, room_name)
		if not sample.is_empty():
			_stats["sample_points"].append(sample)
			if float(sample.get("region_closest_distance", sample.get("closest_distance", 0.0))) > 8.0:
				_stats["errors"].append("%s is too far from a navigation surface." % room_name)

	var paths := []
	for pair in SAMPLE_PATHS:
		var path_sample := await _sample_path(root_node, str(pair[0]), str(pair[1]))
		paths.append(path_sample)
		if int(path_sample.get("point_count", 0)) < 2:
			_stats["errors"].append("No same-floor navigation path from %s to %s." % [pair[0], pair[1]])
		elif float(path_sample.get("end_distance", 9999.0)) > 8.0:
			_stats["errors"].append("Same-floor path from %s to %s does not reach the destination." % [pair[0], pair[1]])
	_stats["paths"] = paths

	root_node.queue_free()
	_finish()


func _floor_summary(floor: Node) -> Dictionary:
	var nav_region := floor.get_node_or_null("NavigationRegion3D") as NavigationRegion3D
	var floor_name := ""
	if "floor_name" in floor:
		floor_name = str(floor.floor_name)
	var result := {
		"node": floor.name,
		"floor_name": floor_name,
		"vertices": 0,
		"polygons": 0,
		"global_y": 0.0,
	}
	if floor is Node3D:
		result["global_y"] = snappedf(floor.global_position.y, 0.001)
	if nav_region == null or nav_region.navigation_mesh == null:
		return result
	result["enabled"] = nav_region.enabled
	result["map_valid"] = nav_region.get_navigation_map().is_valid()
	result["region_iteration"] = NavigationServer3D.region_get_iteration_id(nav_region.get_rid())
	result["server_navigation_layers"] = NavigationServer3D.region_get_navigation_layers(nav_region.get_rid())
	result["map_regions"] = NavigationServer3D.map_get_regions(nav_region.get_navigation_map()).size() if nav_region.get_navigation_map().is_valid() else 0
	result["navigation_layers"] = nav_region.navigation_layers
	result["vertices"] = nav_region.navigation_mesh.get_vertices().size()
	result["polygons"] = nav_region.navigation_mesh.get_polygon_count()
	return result


func _sample_room(root_node: Node, room_name: String) -> Dictionary:
	var room := root_node.find_child(room_name, true, false) as Node3D
	if room == null:
		return {}
	var map: RID = root_node.get_world_3d().get_navigation_map()
	var closest := NavigationServer3D.map_get_closest_point(map, room.global_position)
	var region_closest := closest
	var region_distance := room.global_position.distance_to(region_closest)
	var floor := _floor_parent(room)
	var nav_region := floor.get_node_or_null("NavigationRegion3D") as NavigationRegion3D if floor else null
	if nav_region:
		region_closest = NavigationServer3D.region_get_closest_point(nav_region.get_rid(), room.global_position)
		region_distance = room.global_position.distance_to(region_closest)
	return {
		"name": room_name,
		"point": _vec(room.global_position),
		"closest": _vec(closest),
		"closest_distance": snappedf(room.global_position.distance_to(closest), 0.001),
		"region_closest": _vec(region_closest),
		"region_closest_distance": snappedf(region_distance, 0.001),
	}


func _floor_parent(node: Node) -> Node:
	var current := node
	while current:
		if current.name.begins_with("Floor"):
			return current
		current = current.get_parent()
	return null


func _sample_path(root_node: Node, from_name: String, to_name: String) -> Dictionary:
	var from_node := root_node.find_child(from_name, true, false) as Node3D
	var to_node := root_node.find_child(to_name, true, false) as Node3D
	if from_node == null or to_node == null:
		return {"from": from_name, "to": to_name, "point_count": 0, "error": "missing endpoint"}
	var from_floor := _floor_parent(from_node)
	var to_floor := _floor_parent(to_node)
	if from_floor == null or to_floor == null or from_floor != to_floor:
		return {"from": from_name, "to": to_name, "point_count": 0, "error": "different or missing floors"}
	var nav_region := from_floor.get_node_or_null("NavigationRegion3D") as NavigationRegion3D
	if nav_region == null:
		return {"from": from_name, "to": to_name, "point_count": 0, "error": "missing NavigationRegion3D"}
	var map: RID = nav_region.get_navigation_map()
	var sync := await _await_map_sync(map, 30)
	var layer_mask := int(nav_region.navigation_layers) if int(nav_region.navigation_layers) != 0 else 1
	var all_layers := 0
	for floor in root_node.get_node("Floors").get_children():
		var floor_region := floor.get_node_or_null("NavigationRegion3D") as NavigationRegion3D
		if floor_region:
			all_layers = all_layers | int(floor_region.navigation_layers)
	var path := NavigationServer3D.map_get_path(map, from_node.global_position, to_node.global_position, true, layer_mask)
	var all_layer_path := NavigationServer3D.map_get_path(map, from_node.global_position, to_node.global_position, true, all_layers)
	var min_y := INF
	var max_y := -INF
	var length := 0.0
	for index in range(path.size()):
		min_y = min(min_y, path[index].y)
		max_y = max(max_y, path[index].y)
		if index > 0:
			length += path[index - 1].distance_to(path[index])
	var end_distance := 9999.0
	if path.size() > 0:
		end_distance = path[path.size() - 1].distance_to(to_node.global_position)
	return {
		"from": from_name,
		"to": to_name,
		"layer_mask": layer_mask,
		"all_layers": all_layers,
		"sync": sync,
		"server_region_layers": NavigationServer3D.region_get_navigation_layers(nav_region.get_rid()),
		"region_iteration": NavigationServer3D.region_get_iteration_id(nav_region.get_rid()),
		"point_count": path.size(),
		"all_layer_point_count": all_layer_path.size(),
		"all_layer_path": _path(all_layer_path),
		"path": _path(path),
		"length": snappedf(length, 0.001),
		"end_distance": snappedf(end_distance, 0.001),
		"y_range": [snappedf(min_y, 0.001), snappedf(max_y, 0.001)] if path.size() > 0 else [],
	}


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


func _finish() -> void:
	print(JSON.stringify(_stats, "\t"))
	quit(1 if _stats["errors"].size() > 0 else 0)
