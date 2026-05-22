extends SceneTree

const SCENE_PATH := "res://Scene/science.tscn"
const MANIFEST_PATH := "res://Assets/Buildings/Science/science_manifest.json"
const DOOR_POINTS_PATH := "res://Assets/Buildings/Science/science_room_door_points_route_derived.json"
const DEFAULT_MAX_PROBES := 200
const DEFAULT_PROBE_OUTPUT_LIMIT := 8
const MAX_ENDPOINT_DISTANCE := 2.5
const MAX_END_DISTANCE := 0.75
const ROUTE_PROBE_DOOR_SOURCES := {
	"route_boundary_intersection": true,
	"targeted_route_endpoint_override": true,
}

var _stats := {
	"scene_path": SCENE_PATH,
	"manifest_path": MANIFEST_PATH,
	"door_points_path": DOOR_POINTS_PATH,
	"checks": {},
	"counts": {},
	"navmesh": {},
	"probes": [],
	"errors": [],
}


func _init() -> void:
	call_deferred("_run")


func _run() -> void:
	var manifest := _load_json_dict(MANIFEST_PATH)
	var doors := _load_json_array(DOOR_POINTS_PATH)
	if manifest.is_empty():
		_error("Manifest did not load.")
		_finish()
		return
	if doors.is_empty():
		_error("Door point JSON did not load or is empty.")
		_finish()
		return

	var scene := load(SCENE_PATH)
	if not scene is PackedScene:
		_error("Science scene did not load as PackedScene.")
		_finish()
		return
	var root_node: Node3D = scene.instantiate()
	get_root().add_child(root_node)
	await physics_frame
	await physics_frame

	var nav_region := root_node.get_node_or_null("Floors/Floor0/NavigationRegion3D") as NavigationRegion3D
	if nav_region == null or nav_region.navigation_mesh == null:
		_error("Science scene is missing Floor0 NavigationRegion3D or its NavigationMesh.")
		_finish()
		return

	var map := nav_region.get_navigation_map()
	NavigationServer3D.map_set_active(map, true)
	_stats["navmesh"]["map_sync"] = await _await_map_sync(map, nav_region, 120)
	_stats["navmesh"]["vertices"] = nav_region.navigation_mesh.get_vertices().size()
	_stats["navmesh"]["polygons"] = nav_region.navigation_mesh.get_polygon_count()
	_stats["navmesh"]["regions"] = NavigationServer3D.map_get_regions(map).size()
	_stats["navmesh"]["bounds"] = _bounds(nav_region.navigation_mesh.get_vertices(), nav_region.global_transform)
	if int(_stats["navmesh"]["vertices"]) == 0 or int(_stats["navmesh"]["polygons"]) == 0:
		_error("Imported scene NavigationMesh is empty.")
		_finish()
		return

	var floor_name := _first_floor_name(manifest)
	var candidates := _route_probe_candidates(manifest, doors, floor_name)
	var selected := _select_evenly(candidates, _max_probes())
	_stats["counts"]["candidate_probes"] = candidates.size()
	_stats["counts"]["sampled_probes"] = selected.size()

	var successful := 0
	var failed := 0
	var max_from_distance := 0.0
	var max_to_distance := 0.0
	var max_end_distance := 0.0
	var verbose := OS.get_environment("SCIENCE_NAV_PROBE_VERBOSE") == "1"
	for probe in selected:
		var result := _sample_route(nav_region, map, probe)
		max_from_distance = max(max_from_distance, float(result.get("from_distance", 0.0)))
		max_to_distance = max(max_to_distance, float(result.get("to_distance", 0.0)))
		max_end_distance = max(max_end_distance, float(result.get("end_distance", 0.0)))
		if bool(result.get("ok", false)):
			successful += 1
		else:
			failed += 1
		if verbose or not bool(result.get("ok", false)) or _stats["probes"].size() < DEFAULT_PROBE_OUTPUT_LIMIT:
			_stats["probes"].append(result)
	_stats["counts"]["successful_probes"] = successful
	_stats["counts"]["failed_probes"] = failed
	_stats["counts"]["success_rate"] = snappedf(float(successful) / max(1.0, float(selected.size())), 0.001)
	_stats["counts"]["max_from_distance"] = snappedf(max_from_distance, 0.001)
	_stats["counts"]["max_to_distance"] = snappedf(max_to_distance, 0.001)
	_stats["counts"]["max_end_distance"] = snappedf(max_end_distance, 0.001)

	if selected.is_empty():
		_error("No route-derived portal-to-room candidates were found.")
	elif successful < int(ceil(float(selected.size()) * 0.8)):
		_error("Imported NavigationMesh resolved too few sampled route paths: %d/%d." % [successful, selected.size()])

	root_node.queue_free()
	_finish()


func _route_probe_candidates(manifest: Dictionary, doors: Array, floor_name: String) -> Array:
	var rooms_by_external := {}
	for room in manifest.get("rooms", []):
		if room is Dictionary and str(room.get("floor_name", "")) == floor_name:
			rooms_by_external[str(room.get("external_id", ""))] = room

	var portals_by_external := {}
	for portal in manifest.get("portals", []):
		if portal is Dictionary and str(portal.get("floor_name", "")) == floor_name:
			portals_by_external[str(portal.get("external_id", ""))] = portal

	var probes := []
	for door in doors:
		if not door is Dictionary:
			continue
		if str(door.get("floor_name", "")) != floor_name:
			continue
		if str(door.get("confidence", "")).to_lower() != "high":
			continue
		if not ROUTE_PROBE_DOOR_SOURCES.has(str(door.get("door_source", ""))):
			continue
		var room_id := str(door.get("external_id", ""))
		var portal_id := str(door.get("origin_external_id", ""))
		if not rooms_by_external.has(room_id) or not portals_by_external.has(portal_id):
			continue
		var from_point = _vec3_from_array(portals_by_external[portal_id].get("anchor", []))
		var to_point = _vec3_from_array(door.get("door_local", []))
		if from_point == null or to_point == null:
			continue
		probes.append({
			"room": room_id,
			"portal": portal_id,
			"from": from_point,
			"to": to_point,
		})
	return probes


func _sample_route(nav_region: NavigationRegion3D, map: RID, probe: Dictionary) -> Dictionary:
	var from_point: Vector3 = probe["from"]
	var to_point: Vector3 = probe["to"]
	var closest_from := NavigationServer3D.region_get_closest_point(nav_region.get_rid(), from_point)
	var closest_to := NavigationServer3D.region_get_closest_point(nav_region.get_rid(), to_point)
	var path := NavigationServer3D.map_get_path(map, closest_from, closest_to, true, int(nav_region.navigation_layers))
	var length := 0.0
	for index in range(1, path.size()):
		length += path[index - 1].distance_to(path[index])
	var end_distance := 9999.0
	if path.size() > 0:
		end_distance = path[path.size() - 1].distance_to(closest_to)
	var from_distance := from_point.distance_to(closest_from)
	var to_distance := to_point.distance_to(closest_to)
	var ok := path.size() >= 2 and from_distance <= MAX_ENDPOINT_DISTANCE and to_distance <= MAX_ENDPOINT_DISTANCE and end_distance <= MAX_END_DISTANCE
	return {
		"room": probe["room"],
		"portal": probe["portal"],
		"ok": ok,
		"point_count": path.size(),
		"length": snappedf(length, 0.001),
		"from_distance": snappedf(from_distance, 0.001),
		"to_distance": snappedf(to_distance, 0.001),
		"end_distance": snappedf(end_distance, 0.001),
		"from": _vec(from_point),
		"to": _vec(to_point),
		"closest_from": _vec(closest_from),
		"closest_to": _vec(closest_to),
	}


func _await_map_sync(map: RID, nav_region: NavigationRegion3D, max_frames: int) -> Dictionary:
	var iterations := []
	for frame in range(max_frames):
		NavigationServer3D.map_force_update(map)
		await physics_frame
		var map_iteration := NavigationServer3D.map_get_iteration_id(map)
		var region_iteration := NavigationServer3D.region_get_iteration_id(nav_region.get_rid())
		iterations.append([map_iteration, region_iteration])
		if frame >= 5 and map_iteration >= 2 and region_iteration > 0 and NavigationServer3D.map_get_regions(map).size() > 0:
			return {"frames": frame + 1, "map_iteration": map_iteration, "region_iteration": region_iteration}
	return {
		"frames": max_frames,
		"map_iteration": NavigationServer3D.map_get_iteration_id(map),
		"region_iteration": NavigationServer3D.region_get_iteration_id(nav_region.get_rid()),
		"iterations": iterations,
	}


func _select_evenly(items: Array, max_count: int) -> Array:
	if items.size() <= max_count:
		return items
	var selected := []
	var step := int(ceil(float(items.size()) / float(max_count)))
	for index in range(0, items.size(), max(1, step)):
		selected.append(items[index])
		if selected.size() >= max_count:
			break
	return selected


func _max_probes() -> int:
	var raw := OS.get_environment("SCIENCE_NAV_PROBE_MAX")
	if raw.is_valid_int():
		return max(1, raw.to_int())
	return DEFAULT_MAX_PROBES


func _first_floor_name(manifest: Dictionary) -> String:
	for floor in manifest.get("floors", []):
		if floor is Dictionary:
			return str(floor.get("floor_name", ""))
	return ""


func _load_json_dict(path: String) -> Dictionary:
	var value = JSON.parse_string(FileAccess.get_file_as_string(path))
	return value if value is Dictionary else {}


func _load_json_array(path: String) -> Array:
	var value = JSON.parse_string(FileAccess.get_file_as_string(path))
	return value if value is Array else []


func _vec3_from_array(value) -> Variant:
	if not value is Array or value.size() < 3:
		return null
	return Vector3(float(value[0]), float(value[1]), float(value[2]))


func _bounds(vertices: PackedVector3Array, transform: Transform3D) -> Dictionary:
	if vertices.is_empty():
		return {}
	var min_v := transform * vertices[0]
	var max_v := min_v
	for vertex in vertices:
		var point := transform * vertex
		min_v = Vector3(min(min_v.x, point.x), min(min_v.y, point.y), min(min_v.z, point.z))
		max_v = Vector3(max(max_v.x, point.x), max(max_v.y, point.y), max(max_v.z, point.z))
	return {"min": _vec(min_v), "max": _vec(max_v)}


func _vec(value: Vector3) -> Array:
	return [snappedf(value.x, 0.001), snappedf(value.y, 0.001), snappedf(value.z, 0.001)]


func _error(message: String) -> void:
	_stats["errors"].append(message)


func _finish() -> void:
	print(JSON.stringify(_stats, "\t"))
	quit(1 if _stats["errors"].size() > 0 else 0)
