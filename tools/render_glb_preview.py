import math
import sys
from pathlib import Path

import bpy
import mathutils


def look_at(obj, target):
	direction = mathutils.Vector(target) - obj.location
	obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()


def main() -> None:
	if len(sys.argv) < 3:
		raise SystemExit("usage: blender --background --python tools/render_glb_preview.py -- input.glb output.png")

	input_path = Path(sys.argv[-2])
	output_path = Path(sys.argv[-1])
	output_path.parent.mkdir(parents=True, exist_ok=True)

	bpy.ops.object.select_all(action="SELECT")
	bpy.ops.object.delete()
	bpy.ops.import_scene.gltf(filepath=str(input_path))

	meshes = [obj for obj in bpy.context.scene.objects if obj.type == "MESH"]
	if not meshes:
		raise SystemExit("no mesh objects imported")

	points = []
	for obj in meshes:
		points.extend(obj.matrix_world @ mathutils.Vector(corner) for corner in obj.bound_box)

	bbox_min = mathutils.Vector((min(v[i] for v in points) for i in range(3)))
	bbox_max = mathutils.Vector((max(v[i] for v in points) for i in range(3)))
	center = (bbox_min + bbox_max) * 0.5
	size = bbox_max - bbox_min
	radius = max(size.x, size.y, size.z)

	camera = bpy.data.objects.new("VerificationCamera", bpy.data.cameras.new("VerificationCamera"))
	bpy.context.collection.objects.link(camera)
	camera.location = center + mathutils.Vector((0, -radius * 1.35, radius * 0.75))
	camera.data.lens = 28
	camera.data.clip_end = radius * 10
	look_at(camera, center)
	bpy.context.scene.camera = camera

	light = bpy.data.objects.new("VerificationSun", bpy.data.lights.new("VerificationSun", "SUN"))
	bpy.context.collection.objects.link(light)
	light.rotation_euler = (math.radians(45), 0, math.radians(35))
	light.data.energy = 2.5

	bpy.context.scene.render.engine = "BLENDER_WORKBENCH"
	bpy.context.scene.display.shading.light = "STUDIO"
	bpy.context.scene.display.shading.color_type = "MATERIAL"
	bpy.context.scene.render.resolution_x = 1280
	bpy.context.scene.render.resolution_y = 720
	bpy.context.scene.render.film_transparent = False
	bpy.context.scene.render.filepath = str(output_path)
	bpy.ops.render.render(write_still=True)

	print(
		"BLENDER_RENDER_RESULT "
		+ str(
			{
				"input": str(input_path),
				"output": str(output_path),
				"meshes": len(meshes),
				"bbox_min": [round(v, 4) for v in bbox_min],
				"bbox_max": [round(v, 4) for v in bbox_max],
			}
		)
	)


if __name__ == "__main__":
	main()
