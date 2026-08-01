"""Convert a raw Google Earth (MSFS scenery package) export into a single OBJ mesh.

The "Google Map/Earth Decoder for MSFS" tool family dumps a capture as a
Microsoft Flight Simulator scenery package:

    <export>/
        scene/objects.xml            placement of every tile (lat/lon/alt/heading)
        modelLib/<id>.xml            ModelInfo: GUID -> glTF file
        modelLib/<id>_LOD00.gltf     one tile of the mesh
        modelLib/<id>_LOD00.bin      its vertex/index buffer
        modelLib/texture/<id>_LOD00[_n].png

That layout is unusable directly:

  * The mesh is split across hundreds of glTF files.
  * Each glTF declares its texture as ``<id>_LOD00.png``, but the file actually
    lives in a ``texture/`` subdirectory, so a standard glTF importer silently
    loses every material.
  * Tile placement lives in a separate XML file in geographic coordinates.

This script merges everything into one OBJ + MTL with correct texture paths,
and writes a meta.json describing the result in metres.

Usage:
    python tools/ge_convert.py <export-dir> [-o <output-dir>]

Only the Python standard library is required.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

# --- glTF constants ---------------------------------------------------------

# componentType -> (struct format char, byte size)
COMPONENT = {
    5120: ("b", 1),  # BYTE
    5121: ("B", 1),  # UNSIGNED_BYTE
    5122: ("h", 2),  # SHORT
    5123: ("H", 2),  # UNSIGNED_SHORT
    5125: ("I", 4),  # UNSIGNED_INT
    5126: ("f", 4),  # FLOAT
}

TYPE_COUNT = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}

EARTH_RADIUS_M = 6378137.0

IDENTITY = (
    1.0, 0.0, 0.0, 0.0,
    0.0, 1.0, 0.0, 0.0,
    0.0, 0.0, 1.0, 0.0,
    0.0, 0.0, 0.0, 1.0,
)


# --- small 4x4 matrix helpers (row-major, column vectors) -------------------


def mat_mul(a: tuple, b: tuple) -> tuple:
    out = [0.0] * 16
    for r in range(4):
        for c in range(4):
            out[r * 4 + c] = (
                a[r * 4 + 0] * b[0 * 4 + c]
                + a[r * 4 + 1] * b[1 * 4 + c]
                + a[r * 4 + 2] * b[2 * 4 + c]
                + a[r * 4 + 3] * b[3 * 4 + c]
            )
    return tuple(out)


def mat_translate(x: float, y: float, z: float) -> tuple:
    return (
        1.0, 0.0, 0.0, x,
        0.0, 1.0, 0.0, y,
        0.0, 0.0, 1.0, z,
        0.0, 0.0, 0.0, 1.0,
    )


def mat_scale(x: float, y: float, z: float) -> tuple:
    return (
        x, 0.0, 0.0, 0.0,
        0.0, y, 0.0, 0.0,
        0.0, 0.0, z, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def mat_rotate_y(radians: float) -> tuple:
    c, s = math.cos(radians), math.sin(radians)
    return (
        c, 0.0, s, 0.0,
        0.0, 1.0, 0.0, 0.0,
        -s, 0.0, c, 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def mat_from_quaternion(x: float, y: float, z: float, w: float) -> tuple:
    return (
        1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w), 0.0,
        2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w), 0.0,
        2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y), 0.0,
        0.0, 0.0, 0.0, 1.0,
    )


def transform_point(m: tuple, p: tuple) -> tuple:
    x, y, z = p
    return (
        m[0] * x + m[1] * y + m[2] * z + m[3],
        m[4] * x + m[5] * y + m[6] * z + m[7],
        m[8] * x + m[9] * y + m[10] * z + m[11],
    )


def transform_direction(m: tuple, p: tuple) -> tuple:
    """Rotate a normal. Assumes the matrix has no non-uniform scale/shear."""
    x, y, z = p
    nx = m[0] * x + m[1] * y + m[2] * z
    ny = m[4] * x + m[5] * y + m[6] * z
    nz = m[8] * x + m[9] * y + m[10] * z
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return (0.0, 1.0, 0.0)
    return (nx / length, ny / length, nz / length)


# --- scenery package parsing ------------------------------------------------


class Placement:
    """One <SceneryObject> entry from scene/objects.xml."""

    __slots__ = ("guid", "lat", "lon", "alt", "heading", "pitch", "bank", "scale")

    def __init__(self, guid, lat, lon, alt, heading, pitch, bank, scale):
        self.guid = guid
        self.lat = lat
        self.lon = lon
        self.alt = alt
        self.heading = heading
        self.pitch = pitch
        self.bank = bank
        self.scale = scale

    def key(self):
        return (self.lat, self.lon, self.alt, self.heading, self.pitch, self.bank)


def parse_scene(path: Path) -> list[Placement]:
    root = ET.parse(path).getroot()
    placements = []
    for obj in root.iter("SceneryObject"):
        lib = obj.find("LibraryObject")
        if lib is None:
            continue
        guid = normalize_guid(lib.get("name", ""))
        if not guid:
            continue
        placements.append(
            Placement(
                guid=guid,
                lat=float(obj.get("lat", 0.0)),
                lon=float(obj.get("lon", 0.0)),
                alt=float(obj.get("alt", 0.0)),
                heading=float(obj.get("heading", 0.0)),
                pitch=float(obj.get("pitch", 0.0)),
                bank=float(obj.get("bank", 0.0)),
                scale=float(lib.get("scale", 1.0)),
            )
        )
    return placements


def normalize_guid(raw: str) -> str:
    return re.sub(r"[{}\s]", "", raw).lower()


def index_model_lib(model_lib: Path) -> dict[str, Path]:
    """Map GUID -> highest-detail glTF file, from the ModelInfo descriptors."""
    index: dict[str, Path] = {}
    for xml_path in sorted(model_lib.glob("*.xml")):
        try:
            root = ET.parse(xml_path).getroot()
        except ET.ParseError:
            continue
        guid = normalize_guid(root.get("guid", ""))
        if not guid:
            continue
        best: tuple[float, Path] | None = None
        for lod in root.iter("LOD"):
            model_file = lod.get("ModelFile")
            if not model_file:
                continue
            candidate = model_lib / model_file
            if not candidate.exists():
                continue
            # MinSize 0 is the closest / most detailed LOD in MSFS.
            min_size = float(lod.get("MinSize", 0) or 0)
            if best is None or min_size < best[0]:
                best = (min_size, candidate)
        if best is not None:
            index[guid] = best[1]
    return index


def geo_offset(lat0: float, lon0: float, lat: float, lon: float) -> tuple[float, float]:
    """Local east/north offset in metres (equirectangular, fine at city scale)."""
    east = math.radians(lon - lon0) * EARTH_RADIUS_M * math.cos(math.radians(lat0))
    north = math.radians(lat - lat0) * EARTH_RADIUS_M
    return east, north


def placement_matrix(p: Placement, origin: Placement,
                     heading: float = 0.0) -> tuple:
    """World matrix for a tile, in an X=East / Y=Up / Z=South frame.

    `heading` turns the whole export about the vertical axis, in degrees
    clockwise from above. It is the answer to the note the axes carry: the
    frame above is what a heading=0 export is *assumed* to be in, and an export
    whose model space does not sit that way comes out with its compass wrong --
    which shows up as the street elevation appearing in `east_tex.png` for a
    building whose street is to the west. Stating it here fixes it once, at the
    conversion, rather than leaving every renderer and comparison sheet
    downstream to carry the same correction.

    NOTE: pitch and bank are ignored; every Google Earth export observed so far
    has them at zero. The per-placement heading path is implemented but untested
    for the same reason -- if a future export carries a non-zero heading, verify
    it against the footprint mask before trusting the result.
    """
    east, north = geo_offset(origin.lat, origin.lon, p.lat, p.lon)
    up = p.alt - origin.alt
    m = mat_translate(east, up, -north)
    if heading:
        m = mat_mul(mat_rotate_y(-math.radians(heading)), m)
    if p.heading:
        m = mat_mul(m, mat_rotate_y(-math.radians(p.heading)))
    if p.scale != 1.0:
        m = mat_mul(m, mat_scale(p.scale, p.scale, p.scale))
    return m


# --- glTF reading -----------------------------------------------------------


class Gltf:
    def __init__(self, path: Path):
        self.path = path
        with path.open("r", encoding="utf-8") as handle:
            self.doc = json.load(handle)
        self.buffers: list[bytes] = []
        for buf in self.doc.get("buffers", []):
            uri = buf.get("uri")
            if uri is None:
                raise ValueError(f"{path.name}: GLB-embedded buffers are not supported")
            self.buffers.append((path.parent / uri).read_bytes())

    def accessor(self, index: int) -> list[tuple]:
        acc = self.doc["accessors"][index]
        if "sparse" in acc:
            raise ValueError(f"{self.path.name}: sparse accessors are not supported")
        count = acc["count"]
        ncomp = TYPE_COUNT[acc["type"]]
        fmt, size = COMPONENT[acc["componentType"]]
        view = self.doc["bufferViews"][acc["bufferView"]]
        data = self.buffers[view.get("buffer", 0)]
        base = view.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = view.get("byteStride") or ncomp * size
        unpack = struct.Struct("<" + fmt * ncomp).unpack_from
        return [unpack(data, base + i * stride) for i in range(count)]

    def mesh_instances(self):
        """Yield (mesh_index, world_matrix) for every mesh in the default scene."""
        scenes = self.doc.get("scenes")
        if not scenes:
            return
        scene = scenes[self.doc.get("scene", 0)]
        stack = [(node_index, IDENTITY) for node_index in scene.get("nodes", [])]
        while stack:
            node_index, parent = stack.pop()
            node = self.doc["nodes"][node_index]
            world = mat_mul(parent, self.node_matrix(node))
            if "mesh" in node:
                yield node["mesh"], world
            for child in node.get("children", []):
                stack.append((child, world))

    @staticmethod
    def node_matrix(node: dict) -> tuple:
        if "matrix" in node:
            # glTF stores matrices column-major; transpose to row-major.
            c = node["matrix"]
            return (
                c[0], c[4], c[8], c[12],
                c[1], c[5], c[9], c[13],
                c[2], c[6], c[10], c[14],
                c[3], c[7], c[11], c[15],
            )
        m = IDENTITY
        if "translation" in node:
            m = mat_mul(m, mat_translate(*node["translation"]))
        if "rotation" in node:
            m = mat_mul(m, mat_from_quaternion(*node["rotation"]))
        if "scale" in node:
            m = mat_mul(m, mat_scale(*node["scale"]))
        return m

    def material_texture(self, material_index: int | None) -> str | None:
        """Return the image URI backing a material's base colour, if any."""
        if material_index is None:
            return None
        materials = self.doc.get("materials", [])
        if material_index >= len(materials):
            return None
        pbr = materials[material_index].get("pbrMetallicRoughness", {})
        tex_ref = pbr.get("baseColorTexture")
        if not tex_ref:
            return None
        texture = self.doc.get("textures", [])[tex_ref["index"]]
        source = texture.get("source")
        if source is None:
            return None
        return self.doc.get("images", [])[source].get("uri")


# --- conversion -------------------------------------------------------------


class Clip:
    """Axis-aligned keep-box in metres, given in the render frame.

    An export covers whatever the exporter's camera saw -- neighbouring towers,
    trees, roads, and a terrain skirt hanging below grade. To use the mesh as a
    measuring stick for one building, that has to be cut away.

    Bounds are expressed as east/north/up because that is what the renders and
    render_meta.json report, so a box measured off a render can be passed
    straight in. The mapping onto the OBJ frame (x=east, y=up, z=south) happens
    here rather than in the caller's head.
    """

    __slots__ = ("east", "north", "up")

    def __init__(self, east=None, north=None, up=None):
        span = (-math.inf, math.inf)
        self.east = tuple(sorted(east)) if east else span
        self.north = tuple(sorted(north)) if north else span
        self.up = tuple(sorted(up)) if up else span

    def is_open(self) -> bool:
        return all(
            lo == -math.inf and hi == math.inf
            for lo, hi in (self.east, self.north, self.up)
        )

    def contains(self, point: tuple) -> bool:
        x, y, z = point
        return (
            self.east[0] <= x <= self.east[1]
            and self.up[0] <= y <= self.up[1]
            and self.north[0] <= -z <= self.north[1]
        )

    def as_dict(self) -> dict:
        def pair(v):
            return [None if v[0] == -math.inf else v[0], None if v[1] == math.inf else v[1]]

        return {"east": pair(self.east), "north": pair(self.north), "up": pair(self.up)}


def resolve_texture(model_lib: Path, uri: str | None) -> Path | None:
    """Find a texture on disk, working around the MSFS texture/ convention."""
    if not uri:
        return None
    for candidate in (model_lib / uri, model_lib / "texture" / uri):
        if candidate.exists():
            return candidate
    return None


def texture_reference(texture_path: Path, out_dir: Path) -> str:
    """Path to write into the MTL: relative if possible, absolute otherwise."""
    try:
        return texture_path.relative_to(out_dir).as_posix()
    except ValueError:
        pass
    try:
        import os

        return Path(os.path.relpath(texture_path, out_dir)).as_posix()
    except ValueError:
        # Different drive on Windows -- only an absolute path can work.
        return texture_path.as_posix()


def convert(
    export_dir: Path,
    out_dir: Path,
    write_normals: bool,
    clip: Clip | None = None,
    heading: float = 0.0,
) -> dict:
    scene_xml = export_dir / "scene" / "objects.xml"
    model_lib = export_dir / "modelLib"
    for required in (scene_xml, model_lib):
        if not required.exists():
            raise SystemExit(f"not a Google Earth / MSFS export: missing {required}")

    placements = parse_scene(scene_xml)
    if not placements:
        raise SystemExit(f"no <SceneryObject> entries in {scene_xml}")
    guid_to_gltf = index_model_lib(model_lib)

    distinct = {p.key() for p in placements}
    origin = placements[0]

    out_dir.mkdir(parents=True, exist_ok=True)
    obj_path = out_dir / "merged.obj"
    mtl_path = out_dir / "merged.mtl"

    materials: dict[str, str] = {}  # material name -> texture reference
    missing_textures: set[str] = set()
    missing_models: list[str] = []

    bbox_min = [math.inf] * 3
    bbox_max = [-math.inf] * 3
    vertex_total = 0
    triangle_total = 0
    normal_total = 0
    uv_total = 0
    tiles_written = 0
    triangles_clipped = 0
    if clip is not None and clip.is_open():
        clip = None

    with obj_path.open("w", encoding="utf-8", newline="\n") as obj:
        obj.write("# merged from a Google Earth / MSFS scenery export\n")
        obj.write(f"# source: {export_dir.as_posix()}\n")
        obj.write("mtllib merged.mtl\n")

        for placement in placements:
            gltf_path = guid_to_gltf.get(placement.guid)
            if gltf_path is None:
                missing_models.append(placement.guid)
                continue
            gltf = Gltf(gltf_path)
            world = placement_matrix(placement, origin, heading)
            tile_id = gltf_path.stem
            tile_wrote = False

            for mesh_index, node_world in gltf.mesh_instances():
                matrix = mat_mul(world, node_world)
                mesh = gltf.doc["meshes"][mesh_index]
                for prim_index, prim in enumerate(mesh.get("primitives", [])):
                    if prim.get("mode", 4) != 4:  # only TRIANGLES
                        continue
                    attributes = prim.get("attributes", {})
                    if "POSITION" not in attributes:
                        continue

                    positions = gltf.accessor(attributes["POSITION"])
                    normals = (
                        gltf.accessor(attributes["NORMAL"])
                        if write_normals and "NORMAL" in attributes
                        else None
                    )
                    uvs = (
                        gltf.accessor(attributes["TEXCOORD_0"])
                        if "TEXCOORD_0" in attributes
                        else None
                    )

                    if "indices" in prim:
                        indices = [i[0] for i in gltf.accessor(prim["indices"])]
                    else:
                        indices = list(range(len(positions)))
                    if len(indices) < 3:
                        continue

                    points = [transform_point(matrix, p[:3]) for p in positions]
                    triangles = [
                        (indices[k], indices[k + 1], indices[k + 2])
                        for k in range(0, len(indices) - 2, 3)
                    ]

                    # Clip per triangle, not per tile: photogrammetry triangles
                    # are well under a metre here, so a centroid test cuts close
                    # to the box. Surviving triangles are re-indexed so the
                    # output carries no orphan vertices and the bounding box
                    # reflects only what was kept.
                    remap = None
                    used = range(len(points))
                    if clip is not None:
                        kept = [t for t in triangles if clip.contains(centroid(points, t))]
                        triangles_clipped += len(triangles) - len(kept)
                        if not kept:
                            continue
                        triangles = kept
                        used = sorted({i for t in triangles for i in t})
                        remap = {old: new for new, old in enumerate(used)}

                    material_index = prim.get("material")
                    uri = gltf.material_texture(material_index)
                    texture_path = resolve_texture(model_lib, uri)
                    if uri and texture_path is None:
                        missing_textures.add(uri)
                    # Key by the source material index: a tile may hold several
                    # meshes, each with its own texture, and every one of them
                    # needs a distinct MTL entry.
                    material_name = (
                        f"{tile_id}_mat{material_index}"
                        if material_index is not None
                        else f"{tile_id}_untextured"
                    )
                    if texture_path is not None:
                        materials[material_name] = texture_reference(
                            texture_path, out_dir
                        )
                    else:
                        materials.setdefault(material_name, "")

                    obj.write(f"o {tile_id}_{mesh_index}_{prim_index}\n")
                    obj.write(f"usemtl {material_name}\n")

                    written = 0
                    for i in used:
                        x, y, z = points[i]
                        if x < bbox_min[0]:
                            bbox_min[0] = x
                        if y < bbox_min[1]:
                            bbox_min[1] = y
                        if z < bbox_min[2]:
                            bbox_min[2] = z
                        if x > bbox_max[0]:
                            bbox_max[0] = x
                        if y > bbox_max[1]:
                            bbox_max[1] = y
                        if z > bbox_max[2]:
                            bbox_max[2] = z
                        obj.write(f"v {x:.5f} {y:.5f} {z:.5f}\n")
                        written += 1

                    if uvs is not None:
                        for i in used:
                            u, v = uvs[i][:2]
                            # glTF UV origin is top-left, OBJ's is bottom-left.
                            obj.write(f"vt {u:.6f} {1.0 - v:.6f}\n")

                    if normals is not None:
                        for i in used:
                            nx, ny, nz = transform_direction(matrix, normals[i][:3])
                            obj.write(f"vn {nx:.5f} {ny:.5f} {nz:.5f}\n")

                    v0 = vertex_total + 1
                    t0 = uv_total + 1
                    n0 = normal_total + 1
                    for triangle in triangles:
                        obj.write(
                            "f "
                            + " ".join(
                                face_vertex(
                                    remap[i] if remap is not None else i,
                                    v0,
                                    t0,
                                    n0,
                                    uvs is not None,
                                    normals is not None,
                                )
                                for i in triangle
                            )
                            + "\n"
                        )
                    triangle_total += len(triangles)

                    vertex_total += written
                    if uvs is not None:
                        uv_total += written
                    if normals is not None:
                        normal_total += written
                    tile_wrote = True
            if tile_wrote:
                tiles_written += 1

    with mtl_path.open("w", encoding="utf-8", newline="\n") as mtl:
        mtl.write("# generated by tools/ge_convert.py\n")
        for name in sorted(materials):
            mtl.write(f"\nnewmtl {name}\n")
            mtl.write("Ka 1.000 1.000 1.000\n")
            mtl.write("Kd 1.000 1.000 1.000\n")
            mtl.write("d 1.0\n")
            mtl.write("illum 1\n")
            reference = materials[name]
            if reference:
                mtl.write(f"map_Kd {reference}\n")

    if triangle_total == 0:
        raise SystemExit(
            "the clip box kept nothing -- check it against the bounds in "
            "orthos/render_meta.json (east/north/up, metres)"
        )

    dims = [bbox_max[i] - bbox_min[i] for i in range(3)]
    meta = {
        "source": export_dir.as_posix(),
        "obj": obj_path.name,
        "mtl": mtl_path.name,
        "georeference": {
            "origin_lat": origin.lat,
            "origin_lon": origin.lon,
            "origin_alt_m": origin.alt,
            "distinct_placements": len(distinct),
        },
        "axes": {
            "x": "east",
            "y": "up",
            "z": "south",
            "units": "metres",
            "heading_deg": heading,
            "note": (
                "Assumed frame for a heading=0 export, turned by heading_deg "
                "about the vertical. Confirm the horizontal orientation against "
                "the footprint mask before relying on it: the street elevation "
                "landing in the wrong ortho is what a wrong frame looks like."
            ),
        },
        "bounds_m": {
            "min": {"x": bbox_min[0], "y": bbox_min[1], "z": bbox_min[2]},
            "max": {"x": bbox_max[0], "y": bbox_max[1], "z": bbox_max[2]},
        },
        "dimensions_m": {
            "east_west": dims[0],
            "vertical": dims[1],
            "north_south": dims[2],
        },
        "clip_m": clip.as_dict() if clip is not None else None,
        "counts": {
            "placements": len(placements),
            "tiles_written": tiles_written,
            "vertices": vertex_total,
            "triangles": triangle_total,
            "triangles_clipped": triangles_clipped,
            "materials": len(materials),
        },
        "warnings": {
            "missing_models": sorted(set(missing_models)),
            "missing_textures": sorted(missing_textures),
        },
    }
    (out_dir / "meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def centroid(points: list, triangle: tuple) -> tuple:
    a, b, c = (points[i] for i in triangle)
    return (
        (a[0] + b[0] + c[0]) / 3.0,
        (a[1] + b[1] + c[1]) / 3.0,
        (a[2] + b[2] + c[2]) / 3.0,
    )


def face_vertex(
    index: int, v0: int, t0: int, n0: int, has_uv: bool, has_normal: bool
) -> str:
    v = v0 + index
    if has_uv and has_normal:
        return f"{v}/{t0 + index}/{n0 + index}"
    if has_uv:
        return f"{v}/{t0 + index}"
    if has_normal:
        return f"{v}//{n0 + index}"
    return str(v)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Merge a Google Earth / MSFS scenery export into a single OBJ."
    )
    parser.add_argument("export_dir", type=Path, help="directory containing scene/ and modelLib/")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="output directory (default: <export_dir>/../<export_dir name>-obj)",
    )
    parser.add_argument(
        "--no-normals",
        action="store_true",
        help="omit vertex normals (smaller file, flat-shaded renders)",
    )
    parser.add_argument(
        "--heading",
        type=float,
        default=0.0,
        help=(
            "turn the whole export this many degrees clockwise about the "
            "vertical, so that +x really is east. Check it on the first "
            "conversion: the street elevation must appear in the ortho named "
            "for the side the street is on"
        ),
    )
    for axis, meaning in (
        ("east", "+east / -west"),
        ("north", "+north / -south"),
        ("up", "+up / -down"),
    ):
        parser.add_argument(
            f"--clip-{axis}",
            type=float,
            nargs=2,
            metavar=("MIN", "MAX"),
            default=None,
            help=(
                f"keep only geometry with a {meaning} coordinate in this range, "
                "in metres, measured off orthos/render_meta.json"
            ),
        )
    args = parser.parse_args(argv)

    export_dir = args.export_dir.resolve()
    out_dir = (
        args.output.resolve()
        if args.output
        else export_dir.parent / f"{export_dir.name}-obj"
    )

    clip = Clip(east=args.clip_east, north=args.clip_north, up=args.clip_up)
    meta = convert(
        export_dir,
        out_dir,
        write_normals=not args.no_normals,
        clip=None if clip.is_open() else clip,
        heading=args.heading,
    )

    dims = meta["dimensions_m"]
    counts = meta["counts"]
    print(f"wrote {out_dir / meta['obj']}")
    print(
        f"  tiles      {counts['tiles_written']}/{counts['placements']}"
        f"  materials {counts['materials']}"
    )
    print(f"  vertices   {counts['vertices']}  triangles {counts['triangles']}")
    if counts["triangles_clipped"]:
        print(f"  clipped    {counts['triangles_clipped']} triangles outside the box")
    print(
        "  size (m)   "
        f"E-W {dims['east_west']:.2f}  "
        f"N-S {dims['north_south']:.2f}  "
        f"vertical {dims['vertical']:.2f}"
    )
    print(f"  placements {meta['georeference']['distinct_placements']} distinct")
    for label, values in meta["warnings"].items():
        if values:
            print(f"  WARNING {label}: {len(values)} ({', '.join(values[:5])} ...)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
