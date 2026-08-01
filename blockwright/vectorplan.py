"""A plan read from an outline, rather than from a picture of one.

`flatmap` reads a map crop: it decides which greys mean building, subtracts the
line layer, and traces the enclosed areas. Half that module and a whole chapter
of the documentation exist because the outline arrived as pixels -- the palette,
the anti-aliased fringe that runs through the building's own fill, the road core
that has to stand in for a road, the eight-connected staircase a drawn line
becomes at 30 degrees.

None of it is needed when the outline arrives as an outline. A GeoJSON polygon
*is* the footprint; an SVG path *is* the wall. This module rasterises them onto
the same metre grid everything else works in and hands back the same three
things `plan.decompose` does -- mass, frame, parts -- so nothing downstream can
tell which reader ran.

Where the vector comes from:

    an OSM export (Overpass -> GeoJSON) for a real building;
    a CAD or GIS plan exported as GeoJSON in a projected coordinate system;
    an SVG traced or drawn by hand, with the metres-per-unit stated.

**Parts arrive named.** A map has to be cut along the lines somebody drew and
the pieces named afterwards; a FeatureCollection has one feature per part, and
if the data carries a name, that is the part's name. It is the one input that
does not need `STRIPS` filled in by hand.

**Coordinates.** Longitude and latitude are recognised and projected locally --
equirectangular about the centroid, which is exact enough over a building and
wrong over a city, and this is a building. Anything else is taken to be metres
already, which is what a projected export gives. An SVG carries no units at all
and must be told its scale, for the same reason an OBJ must: the file does not
say, so somebody has to.
"""

from __future__ import annotations

import json
import math
import re
import xml.etree.ElementTree as ElementTree
from pathlib import Path

from .frame import Frame
from .mask import Mask
from .plan import Part

MARGIN = 4          # blocks of clear grid around the plan
MIN_CELLS = 20      # a piece smaller than this is a sliver, not a part
EARTH = 6378137.0   # metres, for the local projection

# Longitude and latitude fall inside this and metres almost never do: a building
# in a projected system is tens of thousands of metres from its false origin at
# the very least, and one that genuinely sits within 180 m of it would have to
# be told the truth by hand.
DEGREES = (180.0, 90.0)


class Ring:
    """One closed loop of points, in metres."""

    __slots__ = ("points",)

    def __init__(self, points: list[tuple[float, float]]):
        self.points = list(points)
        if len(self.points) > 1 and self.points[0] == self.points[-1]:
            self.points.pop()

    def __len__(self) -> int:
        return len(self.points)

    def bounds(self) -> tuple[float, float, float, float]:
        xs = [p[0] for p in self.points]
        zs = [p[1] for p in self.points]
        return min(xs), min(zs), max(xs), max(zs)

    def holds(self, x: float, z: float) -> bool:
        """Whether a point is inside, by the crossing rule.

        Counts how many times a ray east from the point crosses the loop. Odd is
        inside. It is the same rule a rasteriser uses and it needs no winding
        convention, which matters because half the GeoJSON in the world has its
        rings wound the wrong way round and is otherwise perfectly good.
        """
        inside = False
        points = self.points
        n = len(points)
        for i in range(n):
            x0, z0 = points[i]
            x1, z1 = points[(i + 1) % n]
            if (z0 > z) != (z1 > z):
                cut = x0 + (z - z0) * (x1 - x0) / (z1 - z0)
                if cut > x:
                    inside = not inside
        return inside


class Shape:
    """One part: an outer ring, any holes in it, and what it is called."""

    __slots__ = ("name", "outer", "holes")

    def __init__(self, name: str, outer: Ring, holes: list[Ring] | None = None):
        self.name = name
        self.outer = outer
        self.holes = holes or []

    def holds(self, x: float, z: float) -> bool:
        if not self.outer.holds(x, z):
            return False
        return not any(hole.holds(x, z) for hole in self.holes)

    def bounds(self) -> tuple[float, float, float, float]:
        return self.outer.bounds()

    def __repr__(self) -> str:
        x0, z0, x1, z1 = self.bounds()
        return (f"<shape {self.name}: {x1 - x0:.1f} x {z1 - z0:.1f} m, "
                f"{len(self.outer)} points"
                + (f", {len(self.holes)} hole(s)" if self.holes else "") + ">")


def _degrees(shapes: list[Shape]) -> bool:
    for shape in shapes:
        for x, z in shape.outer.points:
            if abs(x) > DEGREES[0] or abs(z) > DEGREES[1]:
                return False
    return True


def _project(shapes: list[Shape]) -> None:
    """Longitude and latitude into metres, about the centroid of the lot.

    Equirectangular: east is a degree of longitude shrunk by the cosine of the
    latitude, north is a degree of latitude, both times the Earth's radius. Over
    a hundred metres the error against a proper projection is millimetres; over a
    city it is metres, and this module is for one building.

    North becomes -Z, because the rest of the pipeline is X east, Z south.
    """
    points = [p for shape in shapes
              for ring in [shape.outer, *shape.holes]
              for p in ring.points]
    lat0 = sum(p[1] for p in points) / len(points)
    lon0 = sum(p[0] for p in points) / len(points)
    scale = math.radians(EARTH)
    cos = math.cos(math.radians(lat0))
    for shape in shapes:
        for ring in [shape.outer, *shape.holes]:
            ring.points = [((lon - lon0) * scale * cos, -(lat - lat0) * scale)
                           for lon, lat in ring.points]


def read_geojson(path: str | Path) -> list[Shape]:
    """Every polygon in a GeoJSON file, named from its properties if it can be.

    `name`, then `ref`, then `id`, then the feature's position -- whichever
    turns up first. A part that arrives named is a part nobody has to name.
    """
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    features = doc.get("features") if doc.get("type") == "FeatureCollection" \
        else [doc]
    out: list[Shape] = []
    for i, feature in enumerate(features or []):
        geometry = feature.get("geometry", feature) or {}
        kind = geometry.get("type")
        props = feature.get("properties") or {}
        label = (props.get("name") or props.get("ref") or feature.get("id")
                 or f"part-{i + 1}")
        polygons = []
        if kind == "Polygon":
            polygons = [geometry.get("coordinates", [])]
        elif kind == "MultiPolygon":
            polygons = geometry.get("coordinates", [])
        else:
            continue
        for j, rings in enumerate(polygons):
            if not rings:
                continue
            name = str(label) if len(polygons) == 1 else f"{label}-{j + 1}"
            out.append(Shape(name, Ring([(float(p[0]), float(p[1]))
                                         for p in rings[0]]),
                             [Ring([(float(p[0]), float(p[1])) for p in ring])
                              for ring in rings[1:]]))
    if not out:
        raise SystemExit(
            f"{path} holds no polygons. A plan needs closed outlines: lines and "
            "points describe where something runs, not what it encloses.")
    return out


# An SVG path, reduced to the two commands a traced outline actually uses.
# Anything curved is refused rather than approximated: a plan drawn with beziers
# is a plan whose corners are a decision somebody made, and flattening them
# silently would be this module inventing them.
_TOKEN = re.compile(r"([MmLlHhVvZz])|(-?\d*\.?\d+(?:[eE][-+]?\d+)?)")


def _path_points(d: str) -> list[list[tuple[float, float]]]:
    rings: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    numbers: list[float] = []
    command = ""
    x = z = 0.0

    def flush() -> None:
        nonlocal current, numbers, x, z
        if not command or not numbers:
            numbers = []
            return
        step = {"M": 2, "L": 2, "H": 1, "V": 1}.get(command.upper(), 0)
        if step == 0:
            numbers = []
            return
        for i in range(0, len(numbers) - step + 1, step):
            if command in "MLml":
                dx, dz = numbers[i], numbers[i + 1]
                x, z = (x + dx, z + dz) if command.islower() else (dx, dz)
            elif command in "Hh":
                x = x + numbers[i] if command == "h" else numbers[i]
            else:
                z = z + numbers[i] if command == "v" else numbers[i]
            if command in "Mm" and i == 0 and current:
                rings.append(current)
                current = []
            current.append((x, z))
        numbers = []

    for match in _TOKEN.finditer(d):
        letter, number = match.groups()
        if number is not None:
            numbers.append(float(number))
            continue
        flush()
        if letter in "Zz":
            if current:
                rings.append(current)
                current = []
            command = ""
            continue
        if letter.upper() not in "MLHV":
            raise SystemExit(
                f"the path uses '{letter}', which is a curve or an arc. Export "
                "the plan with its curves flattened to line segments: "
                "approximating them here would be inventing corners the drawing "
                "did not have.")
        command = letter
    flush()
    if current:
        rings.append(current)
    return [r for r in rings if len(r) >= 3]


def read_svg(path: str | Path, scale: float) -> list[Shape]:
    """Every closed path and rectangle in an SVG, in metres.

    `scale` is metres per SVG unit and has to be stated: an SVG carries a
    viewBox and no units, so the file cannot say how big the building is. One
    guessed scale multiplies through every dimension downstream and nothing
    later can catch it.
    """
    if not scale or scale <= 0:
        raise SystemExit(
            f"{path} is an SVG, which carries no units. Say how many metres one "
            "SVG unit is -- MAP_SCALE in probes/derive.py -- rather than let it "
            "be guessed.")
    root = ElementTree.parse(Path(path)).getroot()
    out: list[Shape] = []
    for i, node in enumerate(root.iter()):
        tag = node.tag.rsplit("}", 1)[-1]
        label = node.get("id") or node.get("{http://www.inkscape.org/namespaces/inkscape}label")
        if tag == "path" and node.get("d"):
            rings = _path_points(node.get("d"))
            if not rings:
                continue
            name = label or f"part-{len(out) + 1}"
            out.append(Shape(
                name,
                Ring([(x * scale, z * scale) for x, z in rings[0]]),
                [Ring([(x * scale, z * scale) for x, z in ring])
                 for ring in rings[1:]]))
        elif tag == "rect":
            x = float(node.get("x", 0.0)) * scale
            z = float(node.get("y", 0.0)) * scale
            w = float(node.get("width", 0.0)) * scale
            h = float(node.get("height", 0.0)) * scale
            if w <= 0 or h <= 0:
                continue
            out.append(Shape(label or f"part-{len(out) + 1}",
                             Ring([(x, z), (x + w, z), (x + w, z + h),
                                   (x, z + h)])))
    if not out:
        raise SystemExit(
            f"{path} holds no closed paths or rectangles. A plan needs outlines "
            "that enclose something.")
    return out


class Layout:
    """A vector plan, in the three pieces every plan comes in."""

    __slots__ = ("mass", "frame", "parts", "order", "shapes", "origin")

    def __init__(self, mass, frame, parts, shapes, origin):
        self.mass = mass
        self.frame = frame
        self.parts = parts
        self.order = [s.name for s in shapes]
        self.shapes = shapes
        self.origin = origin

    def lines(self) -> list[str]:
        out = [repr(self.frame),
               f"  grid origin at ({self.origin[0]:.1f}, {self.origin[1]:.1f}) "
               "in the source's own coordinates"]
        for shape in self.shapes:
            part = self.parts[shape.name]
            du, dv = part.extent
            out.append(f"  {shape.name:16s} {du:7.1f} x {dv:6.1f} m  "
                       f"{part.mask.count():6d} cells  {part.kind}")
        return out


def rasterise(shapes: list[Shape], margin: int = MARGIN,
              min_cells: int = MIN_CELLS) -> Layout:
    """Outlines onto the metre grid, one cell per square metre.

    A cell belongs to a shape when its *centre* is inside -- the same rule
    `Frame.region` uses for everything drawn in this pipeline, so a vector plan
    and a shape drawn in (u, v) rasterise identically and a wall built along
    either lands in the same cells.
    """
    if not shapes:
        raise SystemExit("no shapes to rasterise")
    x0 = min(s.bounds()[0] for s in shapes) - margin
    z0 = min(s.bounds()[1] for s in shapes) - margin
    x1 = max(s.bounds()[2] for s in shapes) + margin
    z1 = max(s.bounds()[3] for s in shapes) + margin
    width = int(math.ceil(x1 - x0)) + 1
    length = int(math.ceil(z1 - z0)) + 1

    masks: dict[str, Mask] = {}
    for shape in shapes:
        mask = Mask(width, length)
        sx0, sz0, sx1, sz1 = shape.bounds()
        for z in range(max(0, int(sz0 - z0) - 1),
                       min(length, int(sz1 - z0) + 2)):
            for x in range(max(0, int(sx0 - x0) - 1),
                           min(width, int(sx1 - x0) + 2)):
                if shape.holds(x0 + x + 0.5, z0 + z + 0.5):
                    mask.bits[z * width + x] = 1
        if mask.count() >= min_cells:
            masks[shape.name] = mask

    if not masks:
        raise SystemExit(
            "every shape rasterised to fewer than "
            f"{min_cells} cells. Either the plan is in the wrong units -- a "
            "building a hundred units across is a hundred metres, not a "
            "hundred millimetres -- or the scale was not stated.")

    kept = [s for s in shapes if s.name in masks]
    mass = Mask.union(masks.values(), width, length)
    frame = Frame.fit_mask(mass)
    parts = {name: Part(mask, frame) for name, mask in masks.items()}
    return Layout(mass, frame, parts, kept, (x0, z0))


def read(path: str | Path, scale: float = 0.0, **kwargs) -> Layout:
    """A vector plan from GeoJSON or SVG, whichever the suffix says."""
    path = Path(path)
    if path.suffix.lower() in (".geojson", ".json"):
        shapes = read_geojson(path)
        if _degrees(shapes):
            _project(shapes)
        elif scale and scale != 1.0:
            for shape in shapes:
                for ring in [shape.outer, *shape.holes]:
                    ring.points = [(x * scale, z * scale) for x, z in ring.points]
    elif path.suffix.lower() == ".svg":
        shapes = read_svg(path, scale)
    else:
        raise SystemExit(f"{path.name}: a vector plan is .geojson or .svg")
    return rasterise(shapes, **kwargs)


def decompose(path: str | Path, scale: float = 0.0, **kwargs):
    """(mass, frame, parts) -- `plan.decompose`'s signature, off a vector."""
    layout = read(path, scale, **kwargs)
    return layout.mass, layout.frame, [layout.parts[n] for n in layout.order]


__all__ = ["Layout", "Ring", "Shape", "decompose", "rasterise", "read",
           "read_geojson", "read_svg"]
