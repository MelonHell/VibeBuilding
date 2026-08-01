"""Reading a building out of a flat map crop.

The crop is a screenshot of a map at one pixel per metre. It is a drawing, not a
survey: its edges wobble, its internal lines are freehand, and its angle is only
as good as the pixel grid. What it is authoritative for is position, size and
rotation -- so it is read once, reduced to a frame, and then set aside. The
geometry is drawn in that frame afterwards.

Anything that needs to talk about the same building in the same coordinates --
the builder, and the checks that grade the builder -- should come through here
rather than copying the numbers, so there is one place for them to be wrong.

A building is also not the whole job. What gets built is the parcel: everything
inside the ring road, of which the building is one object standing on it. The
map draws that boundary and `parcel` reads it.

**Which greys mean what is a property of the map, not of this module.** Every
reader here takes a `Palette`, and the default is one particular map's. A crop
from anywhere else -- another game, an OSM export, a scanned plan in black on
white -- has its own, and it is stated in the building's `probes/derive.py` the
same way a model's up-axis is: the file does not say, so somebody has to.
`tools/map_probe.py` prints the greys a crop actually contains, which turns
choosing three numbers into reading a histogram.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .frame import Frame
from .mask import Mask

SURFACES = ("water", "grass", "ground")


@dataclass(frozen=True)
class Palette:
    """What the greys of one map mean.

    `fill` and `line` are inclusive ranges of the grey level: the building's
    body and the darker line drawn around and inside it. They must not overlap,
    and `line` must be the darker of the two -- the decomposition subtracts the
    line layer from the mass to find the rooms, and a palette with them the
    other way round returns the walls and calls them parts.
    """

    fill: tuple[int, int] = (150, 205)
    line: tuple[int, int] = (100, 150)
    grey: int = 6       # channels this far apart still count as grey
    road: int = 120     # the road core is darker than this and nothing else is
    tint: int = 10      # channels this far apart make a colour, not a grey

    def is_grey(self, r: int, g: int, b: int) -> bool:
        return abs(r - g) <= self.grey and abs(g - b) <= self.grey

    def mass(self, r: int, g: int, b: int) -> bool:
        return self.is_grey(r, g, b) and self.line[0] <= r <= self.fill[1]


DEFAULT = Palette()


_CACHE: dict[tuple[str, float, int], tuple] = {}


def _pixels(path: str):
    """The crop as (pixel accessor, width, length), decoded once.

    One run of the gate used to decode the same PNG five times: `footprint`
    called from `read`, again from `layers`, again from `guides`, and the whole
    lot again when `build.py` and `gate.py` each read the plan. Decoding is the
    single most expensive thing the map branch does, and doing it five times is
    four times more than the job needs.

    Keyed on the file's own modification time and size, so a crop redrawn
    between two calls in the same process is read again rather than remembered
    -- which is the case that matters, because the whole pipeline is built
    around noticing when an input has changed.
    """
    from PIL import Image

    stat = Path(path).stat()
    key = (str(path), stat.st_mtime, stat.st_size)
    if key not in _CACHE:
        src = Image.open(path).convert("RGB")
        w, h = src.size
        _CACHE.clear()
        _CACHE[key] = (src.load(), w, h)
    return _CACHE[key]


def greys(path: str, buckets: int = 16) -> list[tuple[int, int]]:
    """How many achromatic pixels the crop has, by grey level.

    For the message below and for `tools/map_probe.py`: the fastest way to a
    palette that fits an unfamiliar map is to look at where its greys actually
    pile up.
    """
    px, w, h = _pixels(path)
    step = 256 // buckets
    counts = [0] * buckets
    for z in range(h):
        for x in range(w):
            r, g, b = px[x, z]
            if abs(r - g) <= 12 and abs(g - b) <= 12:
                counts[min(buckets - 1, r // step)] += 1
    return [(i * step, n) for i, n in enumerate(counts) if n]


def footprint(path: str, min_cells: int = 200,
              palette: Palette | None = None) -> Mask:
    """The largest building blob in the crop, fill and outline together."""
    palette = palette or DEFAULT
    px, w, h = _pixels(path)
    mass = Mask(w, h)
    for z in range(h):
        for x in range(w):
            if palette.mass(*px[x, z]):
                mass.set(x, z)
    parts = mass.components(min_cells=min_cells)
    if not parts:
        raise SystemExit(
            f"no building found in {path}: {mass.count()} of {w * h} pixels "
            f"are grey between {palette.line[0]} and {palette.fill[1]}, and no "
            f"run of them is {min_cells} cells or more.\n"
            "This map's palette is not the default one. Its greys are:\n"
            + "".join(f"    {level:3d}..{level + 15:3d}  {n}\n"
                      for level, n in greys(path))
            + "Set MAP_PALETTE in probes/derive.py to the ranges that hold the "
              "building's body and the line drawn around it.")
    return parts[0]


class Layers:
    """The map's two greys, kept apart.

    `footprint` merges them, which is right for fitting a frame and wrong for
    everything after: the darker grey is the drawn line, and the drawn lines are
    the only record the map keeps of where one villa ends and the next begins.
    Merging them throws the plan away and leaves a silhouette.
    """

    __slots__ = ("fill", "line", "mass")

    def __init__(self, fill: Mask, line: Mask, mass: Mask):
        self.fill = fill
        self.line = line
        self.mass = mass

    def __repr__(self) -> str:
        return (f"<layers fill {self.fill.count()}, line {self.line.count()}, "
                f"mass {self.mass.count()} cells>")


def layers(path: str, min_cells: int = 200,
           palette: Palette | None = None) -> Layers:
    """Fill, line, and the merged mass, all on the same grid.

    Fill and line are cropped to the neighbourhood of the largest building, so a
    neighbour's outline on the far side of the road does not arrive as part of
    this building's plan.
    """
    palette = palette or DEFAULT
    px, w, h = _pixels(path)
    fill = Mask(w, h)
    line = Mask(w, h)
    for z in range(h):
        for x in range(w):
            r, g, b = px[x, z]
            if not palette.is_grey(r, g, b):
                continue
            if palette.fill[0] <= r <= palette.fill[1]:
                fill.bits[z * w + x] = 1
            elif palette.line[0] <= r < palette.fill[0]:
                line.bits[z * w + x] = 1

    mass = footprint(path, min_cells, palette)
    near = mass.dilate(2.0)
    return Layers(fill & near, line & near, mass)


class Guide:
    """One line the mapper drew inside the building, measured in its frame."""

    __slots__ = ("mask", "u0", "u1", "v0", "v1")

    def __init__(self, mask: Mask, frame: Frame):
        self.mask = mask
        us, vs = [], []
        for x, z in mask.cells():
            u, v = frame.to_local(x + 0.5, z + 0.5)
            us.append(u)
            vs.append(v)
        self.u0, self.u1 = min(us), max(us)
        self.v0, self.v1 = min(vs), max(vs)

    @property
    def along(self) -> str:
        """The axis the line runs down: 'u' for a long division, 'v' for a cut."""
        return "u" if (self.u1 - self.u0) >= (self.v1 - self.v0) else "v"

    def __repr__(self) -> str:
        return (f"<guide along {self.along}, u {self.u0:.1f}..{self.u1:.1f}, "
                f"v {self.v0:.1f}..{self.v1:.1f}, {self.mask.count()} cells>")


def guides(path: str, frame: Frame, min_cells: int = 8,
           palette: Palette | None = None) -> list[Guide]:
    """The interior drawn lines, longest first.

    The building's own outline is drawn in the same grey as its internal
    divisions, so it is subtracted first -- otherwise the outline arrives as one
    enormous guide that runs all the way round and says nothing.

    These are the map at its least reliable and its most useful. An individual
    line is freehand and its exact position means little; that a line is *there*
    means the mapper saw a division there, and that is a fact about the building
    no other reference carries.

    Split eight-connected: a line at this angle is a staircase of cells that meet
    only at their corners, so four-connectivity returns fragments rather than
    lines and `min_cells` then discards every one of them.
    """
    drawn = layers(path, palette=palette)
    interior = drawn.line - drawn.mass.outline(1.5)
    parts = interior.components(min_cells=min_cells, diagonal=True)
    return sorted((Guide(part, frame) for part in parts),
                  key=lambda g: -g.mask.count())


class Parcel:
    """The land inside the road loop, and what the map says is on it.

    `surfaces` partitions `mask` into water, grass and bare ground. It is a
    partition of the *whole* parcel, the building's own footprint included --
    what is under a building is ground, and a caller that wants only open land
    should subtract the plan rather than have this guess for it.
    """

    __slots__ = ("mask", "surfaces")

    def __init__(self, mask: Mask, surfaces: dict[str, Mask]):
        self.mask = mask
        self.surfaces = surfaces

    def __repr__(self) -> str:
        breakdown = ", ".join(
            f"{name} {self.surfaces[name].count()}"
            for name in SURFACES if self.surfaces[name].count()
        )
        return f"<parcel {self.mask.count()} cells: {breakdown}>"


def road(path: str, palette: Palette | None = None) -> Mask:
    """The road core -- the only thing on the map that reliably encloses.

    Not the road as drawn. The drawn road is dark grey with a wide anti-aliased
    fringe, and that fringe runs from the road's 82 to the ground's 217 through
    every value in between -- including 176, which is the building's own fill.
    So there is no grey threshold that separates road from building: pick one
    and the fringe joins up with the buildings into a single blob, and a flood
    seeded inside walks straight out of the picture.

    Only the core is unambiguous. It is thinner than the road looks, which costs
    nothing, because a barrier only has to be closed, not wide.
    """
    palette = palette or DEFAULT
    px, w, h = _pixels(path)
    out = Mask(w, h)
    for z in range(h):
        for x in range(w):
            r, g, b = px[x, z]
            if palette.is_grey(r, g, b) and r < palette.road:
                out.bits[z * w + x] = 1
    return out


def parcel(path: str, min_cells: int = 200,
           palette: Palette | None = None) -> Parcel:
    """Everything the road loop encloses around the largest building.

    Flooded outwards from the building rather than inwards from the border, so
    the answer is "the land this building stands on" and not "whichever region
    happened to be biggest". Raises if the flood reaches the edge of the crop:
    a parcel that escapes is not a parcel, it is a template cut too tight, and
    silently returning half the map would be worse than stopping.
    """
    palette = palette or DEFAULT
    px, w, h = _pixels(path)
    barrier = road(path, palette)
    seed = footprint(path, min_cells, palette)

    inside = Mask(w, h)
    stack = [i for i, v in enumerate(seed.bits) if v]
    for i in stack:
        inside.bits[i] = 1

    escaped = False
    while stack:
        i = stack.pop()
        x, z = i % w, i // w
        if x == 0 or z == 0 or x == w - 1 or z == h - 1:
            escaped = True
        for nx, nz in ((x - 1, z), (x + 1, z), (x, z - 1), (x, z + 1)):
            if not (0 <= nx < w and 0 <= nz < h):
                continue
            j = nz * w + nx
            if barrier.bits[j] or inside.bits[j]:
                continue
            inside.bits[j] = 1
            stack.append(j)

    if escaped:
        raise ValueError(
            f"the parcel in {path} is not closed by road within the crop: "
            f"the flood reached the border after {inside.count()} of {w * h} "
            "cells. Widen the template until the road loop is complete."
        )

    surfaces = {name: Mask(w, h) for name in SURFACES}
    for i, v in enumerate(inside.bits):
        if not v:
            continue
        r, g, b = px[i % w, i // w]
        if b > r + palette.tint:
            name = "water"
        elif g > b + palette.tint:
            name = "grass"
        else:
            name = "ground"
        surfaces[name].bits[i] = 1

    return Parcel(inside, surfaces)


def surrounds(path: str, mass: Mask, reach: float = 25.0,
              palette: Palette | None = None,
              min_cells: int = 200) -> Parcel:
    """The ground around a building, by reach rather than by enclosure.

    `parcel` floods outward until a drawn road stops it, and raises when the
    flood escapes the crop. That is the right answer when the building sits
    inside a closed ring of road, and four buildings in a row did not: a beach
    on one side, a dune on another, open park on a third, a road present only
    in one corner. Every one of them wrote the same replacement by hand --
    everything within N metres of the drawn mass, classified by the map's own
    colours -- so here it is.

    The difference from `parcel` is honest and worth keeping in mind: a parcel
    boundary is **found**, and this one is **chosen**. `reach` is a decision by
    whoever writes it down, and the surfaces inside it are as measured as ever.
    """
    palette = palette or DEFAULT
    px, w, h = _pixels(path)
    inside = mass.dilate(reach)

    # A road cuts it, where there is one: land across the street belongs to the
    # next building even when it is within reach.
    barrier = road(path, palette)
    if barrier.count():
        blocked = Mask(w, h)
        stack = [i for i, v in enumerate(mass.bits) if v]
        for i in stack:
            blocked.bits[i] = 1
        while stack:
            i = stack.pop()
            x, z = i % w, i // w
            for nx, nz in ((x - 1, z), (x + 1, z), (x, z - 1), (x, z + 1)):
                if not (0 <= nx < w and 0 <= nz < h):
                    continue
                j = nz * w + nx
                if blocked.bits[j] or barrier.bits[j] or not inside.bits[j]:
                    continue
                blocked.bits[j] = 1
                stack.append(j)
        inside = blocked

    surfaces = {name: Mask(w, h) for name in SURFACES}
    for i, v in enumerate(inside.bits):
        if not v:
            continue
        r, g, b = px[i % w, i // w]
        if b > r + palette.tint:
            name = "water"
        elif g > b + palette.tint:
            name = "grass"
        else:
            name = "ground"
        surfaces[name].bits[i] = 1
    return Parcel(inside, surfaces)


def read(path: str, palette: Palette | None = None) -> tuple[Mask, Frame]:
    """The footprint and the frame fitted to it."""
    mask = footprint(path, palette=palette)
    return mask, Frame.fit_mask(mask)


__all__ = ["DEFAULT", "Guide", "Layers", "Palette", "Parcel", "SURFACES",
           "footprint", "greys", "guides", "layers", "parcel", "read", "road",
           "surrounds"]
