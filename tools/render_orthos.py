"""Render a merged Google Earth mesh, headless: elevations, and shots.

Run with Blender, not the system Python:

    blender -b -P tools/render_orthos.py -- <merged.obj> [-o <output-dir>]
    blender -b -P tools/render_orthos.py -- <merged.obj> --view street:294,8
    blender -b -P tools/render_orthos.py -- <merged.obj> \\
        --shot court:-30,-90,32,-20,-60,40,75

Produces, for each view:

    <view>_tex.png      Workbench render with the photographic texture --
                        reads material, colour, window rhythm.
    <view>_solid.png    Flat shading with cavity, no texture -- reads massing
                        and geometry, which the noisy photo texture hides.

Also writes render_meta.json with the exact metres-per-pixel of every
orthographic view, so a pixel measured in a render converts back to metres
without guesswork.

A `--view` is a compass bearing and a pitch: where the camera stands relative to
the building, and how far above the ground it is lifted. Bearing 0 puts the
camera to the north looking south, 90 to the east; pitch 0 is level with the
subject and 90 is straight overhead. The five defaults are the axis-aligned cases
of that, and `--view name:bearing,pitch` adds any other. All of these are
parallel projections, which measure but do not look like anything.

A `--shot` is a perspective camera at a point, aimed at another point, both given
in Blender world metres. It measures nothing -- there is no metres-per-pixel for
a perspective image -- and in exchange it can stand somewhere. That is the only
way to see the inside of a courtyard: a parallel camera has no position, so it
cannot be put in one.

Input axes (as produced by ge_convert.py): X=east, Y=up, Z=south. Blender's
OBJ importer converts that Y-up frame to its own Z-up frame, giving
X=east, Y=north, Z=up.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

import bpy
import mathutils

# view name -> (compass bearing of the camera, pitch above the horizon), degrees.
# "north" means the camera sits to the north and looks south, so the render shows
# the building's north-facing side. Bearing is irrelevant at pitch 90; 180 is
# chosen for `top` because it puts east to the right and north up the page.
VIEWS = {
    "top": (180.0, 90.0),
    "north": (0.0, 0.0),
    "south": (180.0, 0.0),
    "east": (90.0, 0.0),
    "west": (270.0, 0.0),
}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("obj", type=Path, help="merged.obj produced by ge_convert.py")
    parser.add_argument(
        "-o", "--output", type=Path, default=None, help="output directory"
    )
    parser.add_argument(
        "-r", "--resolution", type=int, default=1400, help="square render size in pixels"
    )
    parser.add_argument(
        "--margin", type=float, default=1.04, help="fraction of padding around the mesh"
    )
    parser.add_argument(
        "--view", action="append", default=[], metavar="NAME:BEARING,PITCH",
        help="an extra view at an arbitrary camera bearing and pitch, in degrees",
    )
    parser.add_argument(
        "--shot", action="append", default=[],
        metavar="NAME:EX,EY,EZ,TX,TY,TZ,FOV",
        help="a perspective camera at a point, aimed at a point, in world metres",
    )
    parser.add_argument(
        "--shot-size", type=int, nargs=2, default=(1280, 720), metavar=("W", "H"),
        help="pixel size of every --shot, which unlike a view is not fitted",
    )
    parser.add_argument(
        "--only-extra", action="store_true",
        help="render only the --view and --shot cameras, leaving the five defaults alone",
    )
    return parser.parse_args(argv)


def parse_view(spec: str) -> tuple[str, tuple[float, float]]:
    """`name:bearing,pitch` -> (name, (bearing, pitch))."""
    name, _, angles = spec.partition(":")
    bearing, _, pitch = angles.partition(",")
    if not name or not bearing or not pitch:
        raise SystemExit(f"--view wants NAME:BEARING,PITCH, got {spec!r}")
    return name, (float(bearing), float(pitch))


def parse_shot(spec: str) -> tuple[str, tuple[float, ...]]:
    """`name:ex,ey,ez,tx,ty,tz,fov` -> (name, seven floats)."""
    name, _, rest = spec.partition(":")
    numbers = [part for part in rest.split(",") if part]
    if not name or len(numbers) != 7:
        raise SystemExit(f"--shot wants NAME:EX,EY,EZ,TX,TY,TZ,FOV, got {spec!r}")
    return name, tuple(float(n) for n in numbers)


def camera_basis(bearing: float, pitch: float):
    """Where the camera stands, which way it is turned, and its screen axes.

    Blender's camera looks along its local -Z, so the euler that aims it is the
    one that turns -Z onto the line from the camera back to the subject. Rotating
    `90 - pitch` about X tips the camera down from straight-up, and `180 -
    bearing` about Z swings it round the compass -- at bearing 0 the camera is
    north of the subject and must face south, which is a half turn.
    """
    b, p = math.radians(bearing), math.radians(pitch)
    euler = (90.0 - pitch, 0.0, 180.0 - bearing)
    offset = mathutils.Vector(
        (math.cos(p) * math.sin(b), math.cos(p) * math.cos(b), math.sin(p))
    )
    yaw = math.radians(180.0 - bearing)
    right = mathutils.Vector((math.cos(yaw), math.sin(yaw), 0.0))
    up = mathutils.Vector(
        (-math.sin(yaw) * math.sin(p), math.cos(yaw) * math.sin(p), math.cos(p))
    )
    return euler, offset, right, up


def screen_extent(lo, hi, right, up) -> tuple[float, float]:
    """How wide and how tall the mesh is *as the camera sees it*.

    The eight corners of the bounding box are projected onto the camera's own
    screen axes and the spans taken. For an axis-aligned view this returns the
    box dimensions unchanged; for an oblique one it returns what actually has to
    fit in the frame, which is larger and which a per-axis lookup cannot give.
    """
    corners = [
        mathutils.Vector((x, y, z))
        for x in (lo.x, hi.x) for y in (lo.y, hi.y) for z in (lo.z, hi.z)
    ]
    hs = [c.dot(right) for c in corners]
    vs = [c.dot(up) for c in corners]
    return max(hs) - min(hs), max(vs) - min(vs)


def scene_bounds() -> tuple[mathutils.Vector, mathutils.Vector]:
    lo = mathutils.Vector((math.inf,) * 3)
    hi = mathutils.Vector((-math.inf,) * 3)
    found = False
    for obj in bpy.data.objects:
        if obj.type != "MESH":
            continue
        for corner in obj.bound_box:
            world = obj.matrix_world @ mathutils.Vector(corner)
            for i in range(3):
                lo[i] = min(lo[i], world[i])
                hi[i] = max(hi[i], world[i])
            found = True
    if not found:
        raise SystemExit("no mesh objects were imported")
    return lo, hi


def configure_workbench() -> None:
    scene = bpy.context.scene
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_percentage = 100
    scene.render.image_settings.file_format = "PNG"
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"

    display = scene.display
    display.render_aa = "8"
    shading = display.shading
    shading.show_specular_highlight = False
    shading.background_type = "VIEWPORT"
    shading.background_color = (0.22, 0.24, 0.27)


def render_passes(name: str, out_dir: Path) -> dict:
    """The two passes every camera gets, whatever kind of camera it is."""
    scene = bpy.context.scene
    outputs = {}
    for suffix, color_type, light, cavity in (
        # FLAT for the texture pass: studio lighting tints the aerial imagery and
        # these renders are what the palette gets read from.
        ("tex", "TEXTURE", "FLAT", False),
        ("solid", "SINGLE", "STUDIO", True),
    ):
        scene.display.shading.color_type = color_type
        scene.display.shading.light = light
        scene.display.shading.show_cavity = cavity
        if cavity:
            scene.display.shading.cavity_type = "BOTH"
            scene.display.shading.cavity_ridge_factor = 1.0
            scene.display.shading.cavity_valley_factor = 1.0
        scene.display.shading.single_color = (0.72, 0.72, 0.70)
        path = out_dir / f"{name}_{suffix}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        outputs[suffix] = path.name
    return outputs


def render_shot(
    camera: bpy.types.Object,
    name: str,
    spec: tuple[float, ...],
    out_dir: Path,
    size: tuple[int, int],
) -> dict:
    """A perspective camera at a point, aimed at a point.

    `to_track_quat('-Z', 'Y')` aims the camera's own -Z along the line of sight
    and keeps its +Y -- screen up -- as near world up as it can, which is what a
    photographer with a spirit level gets. There is no framing to compute: the
    field of view and the aspect decide what is in shot, and cropping to the
    subject afterwards would quietly change the lens.
    """
    ex, ey, ez, tx, ty, tz, fov = spec
    eye = mathutils.Vector((ex, ey, ez))
    aim = mathutils.Vector((tx, ty, tz)) - eye
    if aim.length < 1e-6:
        raise SystemExit(f"--shot {name}: the camera is standing on its target")

    scene = bpy.context.scene
    scene.render.resolution_x, scene.render.resolution_y = size

    camera.location = eye
    camera.rotation_euler = aim.to_track_quat("-Z", "Y").to_euler()
    camera.data.type = "PERSP"
    camera.data.sensor_fit = "HORIZONTAL"
    camera.data.angle_x = math.radians(fov)
    camera.data.clip_start = 0.05
    camera.data.clip_end = 4000.0

    return {
        "files": render_passes(name, out_dir),
        "camera": {"eye": [ex, ey, ez], "target": [tx, ty, tz], "fov": fov},
        "resolution": list(size),
        "projection": "perspective",
    }


def render_view(
    camera: bpy.types.Object,
    name: str,
    angles: tuple[float, float],
    lo: mathutils.Vector,
    hi: mathutils.Vector,
    out_dir: Path,
    margin: float,
    resolution: int,
) -> dict:
    bearing, pitch = angles
    euler_deg, offset, right, up = camera_basis(bearing, pitch)

    center = (lo + hi) * 0.5
    size = hi - lo
    extent_h, extent_v = screen_extent(lo, hi, right, up)
    depth = size.length  # generous: guarantees the whole mesh is in front

    # Match the render aspect to the view's own extent. A square frame sized by
    # the largest dimension would leave an elevation occupying a fraction of the
    # image, throwing away exactly the resolution needed to read storey heights.
    scene = bpy.context.scene
    if extent_h >= extent_v:
        scene.render.resolution_x = resolution
        scene.render.resolution_y = max(1, round(resolution * extent_v / extent_h))
    else:
        scene.render.resolution_y = resolution
        scene.render.resolution_x = max(1, round(resolution * extent_h / extent_v))
    # Blender applies ortho_scale to the larger of the two resolution axes.
    span = max(extent_h, extent_v) * margin

    camera.rotation_euler = [math.radians(a) for a in euler_deg]
    camera.location = center + offset * depth
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = span
    camera.data.clip_start = 0.01
    camera.data.clip_end = depth * 3.0

    return {
        "files": render_passes(name, out_dir),
        "camera": {"bearing": bearing, "pitch": pitch},
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "metres_per_pixel": span / max(scene.render.resolution_x, scene.render.resolution_y),
        "extent_m": {"horizontal": extent_h, "vertical": extent_v},
        "screen_axes": {"right": list(right), "up": list(up)},
    }


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    obj_path = args.obj.resolve()
    out_dir = (args.output or obj_path.parent / "orthos").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.wm.obj_import(filepath=str(obj_path))

    lo, hi = scene_bounds()
    configure_workbench()

    camera_data = bpy.data.cameras.new("ortho")
    camera = bpy.data.objects.new("ortho", camera_data)
    bpy.context.scene.collection.objects.link(camera)
    bpy.context.scene.camera = camera

    wanted = {} if args.only_extra else dict(VIEWS)
    wanted.update(parse_view(spec) for spec in args.view)
    shots = dict(parse_shot(spec) for spec in args.shot)

    views = {
        name: render_view(camera, name, angles, lo, hi, out_dir,
                          args.margin, args.resolution)
        for name, angles in wanted.items()
    }
    views.update({
        name: render_shot(camera, name, spec, out_dir, tuple(args.shot_size))
        for name, spec in shots.items()
    })

    # An extra view is written alongside whatever is already there rather than
    # replacing the file, so rendering one photograph's angle does not silently
    # discard the five elevations the measurements were taken off.
    meta_path = out_dir / "render_meta.json"
    if args.only_extra and meta_path.exists():
        previous = json.loads(meta_path.read_text(encoding="utf-8"))
        views = {**previous.get("views", {}), **views}

    meta = {
        "obj": obj_path.as_posix(),
        "long_edge_resolution": args.resolution,
        "blender_axes": {"x": "east", "y": "north", "z": "up", "units": "metres"},
        "bounds_m": {
            "min": {"x": lo.x, "y": lo.y, "z": lo.z},
            "max": {"x": hi.x, "y": hi.y, "z": hi.z},
        },
        "dimensions_m": {
            "east_west": hi.x - lo.x,
            "north_south": hi.y - lo.y,
            "vertical": hi.z - lo.z,
        },
        "views": views,
    }
    meta_path.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print(f"[render_orthos] wrote {len(wanted)} views and {len(shots)} shots "
          f"to {out_dir}")
    return 0


if __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    sys.exit(main(argv))
