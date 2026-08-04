"""Structural questions asked of a finished build, before anyone looks at it.

The gate that already exists compares the build to the Google Earth mesh, which
answers "is it the right size and in the right place" and nothing else. It has
nothing to say about a canopy frame that broke off near the cone and curled into
the air, because a detached fragment sits exactly where the mesh says the canopy
should be.

These checks are about the build's own consistency, so they need no reference at
all. Each returns findings rather than passing judgement -- what counts as too
many free ends is a property of the building, not of the check -- and `gate.py`
decides.

    islands(canvas)        pieces not attached to the ground
    free_ends(canvas)      blocks hanging off a member with nothing beyond them
    layer_parts(canvas)    how many separate things exist at each height
    palette(canvas)        block strings nothing knows how to draw

Connectivity is face-only. Two cells that meet along a diagonal edge are counted
as separate, and that is deliberate: a wall built at 52 degrees is a staircase of
step and riser cells that do meet face to face, so a properly closed wall passes,
while a run of blocks joined only at their corners is something a person cannot
walk along and reads as broken -- which is the failure being looked for.
"""

from __future__ import annotations

import math

from .blocks import AIR, base, unknown_blocks
from .mask import Mask

# Blocks that are allowed to float or to end in mid-air: planting, water, and
# anything else that is scenery rather than structure. A leaf block with one
# neighbour is a tree, not a broken beam.
SOFT = {
    "minecraft:jungle_leaves",
    "minecraft:oak_leaves",
    "minecraft:spruce_leaves",
    "minecraft:acacia_leaves",
    "minecraft:azalea_leaves",
    "minecraft:moss_block",
    "minecraft:moss_carpet",
    "minecraft:grass",
    "minecraft:short_grass",
    "minecraft:tall_grass",
    "minecraft:fern",
    "minecraft:large_fern",
    "minecraft:water",
    "minecraft:vine",
    "minecraft:hanging_roots",
    "minecraft:glow_lichen",
}


def _grid(model):
    """Canvas and Schematic hold the same box under different names."""
    data = getattr(model, "data", None)
    if data is None:
        data = model.blocks
    return data, model.palette, model.width, model.height, model.length


class Group:
    """A connected piece of the build."""

    __slots__ = ("count", "cells", "x0", "x1", "y0", "y1", "z0", "z1")

    def __init__(self, cells, W, L):
        self.cells = cells
        self.count = len(cells)
        area = W * L
        self.x0 = self.y0 = self.z0 = 1 << 30
        self.x1 = self.y1 = self.z1 = -1
        for i in cells:
            y, rest = divmod(i, area)
            z, x = divmod(rest, W)
            if x < self.x0:
                self.x0 = x
            if x > self.x1:
                self.x1 = x
            if y < self.y0:
                self.y0 = y
            if y > self.y1:
                self.y1 = y
            if z < self.z0:
                self.z0 = z
            if z > self.z1:
                self.z1 = z

    def where(self) -> tuple[int, int, int]:
        """One cell in the middle of it, for going and looking."""
        return ((self.x0 + self.x1) // 2, (self.y0 + self.y1) // 2,
                (self.z0 + self.z1) // 2)

    def span(self, frame) -> tuple[float, float]:
        """How far the piece reaches along the building's long axis."""
        us = []
        for corner in ((self.x0, self.z0), (self.x1, self.z0),
                       (self.x0, self.z1), (self.x1, self.z1)):
            us.append(frame.u_of(corner[0] + 0.5, corner[1] + 0.5))
        return (min(us), max(us))

    def __repr__(self) -> str:
        x, y, z = self.where()
        return (f"Group({self.count} blocks at ({x}, {y}, {z}), "
                f"y {self.y0}..{self.y1})")


def islands(model, soft: set[str] | None = None) -> list[Group]:
    """Connected pieces of the build, largest first.

    A build should be one piece. Anything else is either floating -- a fragment
    that lost the member carrying it -- or a genuinely separate structure, and
    the check cannot tell those apart, so it reports both and leaves the reading
    to whoever knows what the building is.

    Planting is excluded before the search, or a canopy of leaves resting on two
    different roofs sews the two together and hides a real break.
    """
    data, pal, W, H, L = _grid(model)
    soft = SOFT if soft is None else soft
    skip = {v for v, b in enumerate(pal) if base(b) in soft}
    skip.add(0)

    area = W * L
    seen = bytearray(len(data))
    out: list[Group] = []
    for start, v in enumerate(data):
        if not v or v in skip or seen[start]:
            continue
        cells = []
        stack = [start]
        seen[start] = 1
        while stack:
            i = stack.pop()
            cells.append(i)
            y, rest = divmod(i, area)
            z, x = divmod(rest, W)
            if x + 1 < W:
                _push(data, seen, skip, stack, i + 1)
            if x:
                _push(data, seen, skip, stack, i - 1)
            if z + 1 < L:
                _push(data, seen, skip, stack, i + W)
            if z:
                _push(data, seen, skip, stack, i - W)
            if y + 1 < H:
                _push(data, seen, skip, stack, i + area)
            if y:
                _push(data, seen, skip, stack, i - area)
        out.append(Group(cells, W, L))
    out.sort(key=lambda g: -g.count)
    return out


def _push(data, seen, skip, stack, j) -> None:
    if not seen[j] and data[j] and data[j] not in skip:
        seen[j] = 1
        stack.append(j)


def floating(pieces: list[Group]) -> list[Group]:
    """Of those pieces, the ones that never reach the ground plane.

    The build's own y = 0 is the ground it was authored on, so a piece whose
    lowest block sits above it is holding itself up by nothing. Takes the result
    of `islands` rather than a model, because the flood fill is the expensive
    part and no caller needs it done twice.
    """
    return [g for g in pieces if g.y0 > 0]


def free_ends(model, soft: set[str] | None = None) -> list[tuple[int, int, int]]:
    """Blocks with at most one neighbour: the loose tip of a member.

    A beam that stops in mid-air has one such block at its end, and so does a
    single block left behind when a run was written one cell short. Both are
    worth seeing. A cantilever has them legitimately, which is why this returns
    the list and not a verdict -- the count is compared against what the
    building is supposed to have.
    """
    data, pal, W, H, L = _grid(model)
    soft = SOFT if soft is None else soft
    skip = {v for v, b in enumerate(pal) if base(b) in soft}

    area = W * L
    out = []
    for i, v in enumerate(data):
        if not v or v in skip:
            continue
        y, rest = divmod(i, area)
        z, x = divmod(rest, W)
        n = 0
        if x + 1 < W and data[i + 1]:
            n += 1
        if x and data[i - 1]:
            n += 1
        if z + 1 < L and data[i + W]:
            n += 1
        if z and data[i - W]:
            n += 1
        if y + 1 < H and data[i + area]:
            n += 1
        if y and data[i - area]:
            n += 1
        if n <= 1:
            out.append((x, y, z))
    return out


def layer_parts(model, y: int, min_cells: int = 8):
    """The separate things present at one height, as 2D masks.

    Promoted out of Terra's `probes/split_probe.py`, where it caught the failure
    that thirteen villas had merged into two long bars. Counting them is the
    only automatic test the pipeline has ever had for that, and a probe that
    reads `out/` is a verification, so it belongs here rather than beside the
    building it was first written for.
    """
    from .mask import Mask

    data, _pal, W, H, L = _grid(model)
    area = W * L
    chunk = data[y * area : (y + 1) * area]
    m = Mask(W, L, bytearray(1 if v else 0 for v in chunk))
    return m.components(min_cells=min_cells)


def palette(model) -> list[str]:
    """Blocks in the build that nothing knows how to draw."""
    _, pal, _, _, _ = _grid(model)
    return unknown_blocks(pal[1:])


# Diagonal steps included: a pinhole you can only get through cornerwise is
# still a hole you can see daylight through, and at 52 degrees every wall is
# made of them.
_MOORE = ((-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1))


def watertight(footprint, wall) -> list[tuple[int, int]]:
    """Cells inside `footprint` that an 8-connected flood from outside reaches.

    Promoted out of Terra's `probes/leak_probe.py`, which was written to settle
    whether a wall ring actually encloses anything and then left where nothing
    could call it. It is the test behind `Mask.outline`: a plain band, footprint
    minus its erosion, leaks at every diagonal, and unioning the band with
    `rim()` is what stops it. Having the test in the library is what keeps that
    from being rediscovered a third time.

    An empty result means watertight. The flood starts at cell (0, 0), which is
    outside any footprint a template produces -- `flatmap.parcel` already
    refuses a crop whose subject touches the border.
    """
    w, h = footprint.width, footprint.length
    if (wall.width, wall.length) != (w, h):
        raise ValueError("the wall and the footprint are on different grids")
    if wall.bits[0] or footprint.bits[0]:
        raise ValueError("cell (0, 0) is inside the building; the crop is too tight")

    seen = bytearray(w * h)
    seen[0] = 1
    stack = [0]
    leaked: list[tuple[int, int]] = []
    while stack:
        i = stack.pop()
        x, z = i % w, i // w
        for dx, dz in _MOORE:
            nx, nz = x + dx, z + dz
            if not (0 <= nx < w and 0 <= nz < h):
                continue
            j = nz * w + nx
            if seen[j] or wall.bits[j]:
                continue
            seen[j] = 1
            if footprint.bits[j]:
                leaked.append((nx, nz))
            stack.append(j)
    return leaked


# -- evenness ---------------------------------------------------------------
#
# Three questions the rest of this file cannot ask, because every check above
# compares the build against something outside it. These compare the build
# against **itself**: two parts that ought to match, a line that ought to be
# straight, a horizontal that ought to be one height.
#
# That is the whole blind spot the section has by construction. Two towers of
# the same footprint cast the same silhouette and the same profile whatever
# their facades do; a roof edge sawtoothed by a metre at every step measures the
# same as a straight one; a parapet at nine different heights along one building
# passes every station it is graded at. All three are the first thing a person
# sees in a render and none of them is a number anywhere else in this pipeline.


def twins(model, a: Mask, b: Mask, frame, axis: float, along: str = "u",
          y0: int = 0, y1: int | None = None) -> dict:
    """How far two parts that should be mirror images actually differ.

    `a` and `b` are the two footprints and `axis` is the u -- or the v, if
    `along` is "v" -- of the plane between them. Every filled cell of `a` is
    reflected across that plane and looked for in `b`, and vice versa, course by
    course.

    Reflected through the frame rather than through the block array, for the
    reason `Frame.flipped` gives at length: at fifty degrees to the world grid a
    voxel reflection is a resampling and would report its own rounding as the
    building's error. Here the reflected point is mapped back to a cell and
    tested, which rounds once and symmetrically, so a build that really is a
    mirror pair comes back at or near zero and the residue is honest.

    Returns the count each way, the worst course, and the share of the pair that
    matches -- `same`, which is the number to put a budget on.
    """
    height = model.height if y1 is None else min(model.height, y1)
    area = model.width * model.length
    air = {i for i, block in enumerate(model.palette) if block == AIR}
    mirror = frame.flipped(axis, along)

    def opposite(x: int, z: int) -> tuple[int, int] | None:
        u, v = frame.to_local(x + 0.5, z + 0.5)
        wx, wz = mirror.to_world(u, v)
        wx, wz = int(wx), int(wz)
        if 0 <= wx < model.width and 0 <= wz < model.length:
            return wx, wz
        return None

    pairs = []
    for source, target in ((a, b), (b, a)):
        for x, z in source.cells():
            other = opposite(x, z)
            if other is not None and target.get(*other):
                pairs.append((z * model.width + x,
                              other[1] * model.width + other[0]))

    matched = differ = 0
    worst, at = 0, None
    for y in range(max(0, y0), height):
        base = y * area
        wrong = 0
        for here, there in pairs:
            one = model.blocks[base + here] not in air
            two = model.blocks[base + there] not in air
            if one == two:
                matched += 1
            else:
                wrong += 1
        differ += wrong
        if wrong > worst:
            worst, at = wrong, y

    total = matched + differ
    return {
        "cells": len(pairs),
        "matched": matched,
        "differ": differ,
        "same": matched / total if total else 1.0,
        "worst_course": at,
        "worst": worst,
    }


def facade_mix(model, mask: Mask, frame, u0: float, u1: float,
               thickness: float = 1.0) -> dict:
    """What one stretch of the outside wall is made of, air included.

    The tally is over the wall **plane** and not over the wall's material, and
    that distinction is the whole check. A loggia is a hole: the blocks that used
    to be there are gone, so counting the blocks that remain and dividing by how
    many remain gives a balconied wing and a blank one nearly the same answer --
    both are almost entirely their wall block, because whatever was cut left the
    sum as well as the count. Measured against the plane, the hole is the signal:
    a carved wing reads as a fifth air and a blank one as none.

    The plane is the footprint's outline ring, and each of its columns is read
    from the ground to its own highest block, so open sky above a low stretch is
    not counted as a facade full of air.
    """
    ring = mask.outline(thickness)
    counts: dict[str, int] = {}
    seen = 0
    for x, z in ring.cells():
        u, _ = frame.to_local(x + 0.5, z + 0.5)
        if not (u0 <= u < u1):
            continue
        column = [base(model.get(x, y, z)) for y in range(model.height)]
        top = -1
        for y, block in enumerate(column):
            if block != AIR:
                top = y
        if top < 0:
            continue
        for y in range(top + 1):
            counts[column[y]] = counts.get(column[y], 0) + 1
            seen += 1

    return {"cells": seen,
            "mix": {b: n / seen for b, n in counts.items()} if seen else {}}


def mix_apart(a: dict, b: dict) -> float:
    """How far apart two facade mixes are, 0 identical and 1 sharing nothing.

    Total variation: half the sum of the differences, which is the share of one
    wall you would have to rebuild to make it the other. Readable as a number
    rather than only as an ordering, which matters because this is going into a
    budget somebody has to defend.
    """
    keys = set(a) | set(b)
    if not keys:
        return 0.0
    return 0.5 * sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in keys)


def jaggedness(mask: Mask, frame, tolerance: float = 1.0) -> dict:
    """How much of a shape is the drawing's wobble rather than the building.

    A map is drawn by hand and its edges wander by a cell or two. Traced, that
    wander becomes a sawtooth, and at one block to the metre a sawtooth is a
    metre deep -- the first thing anyone notices about a roof edge beside a
    photograph of the real one, and invisible to every other check here: a
    jagged edge measures the same at every station, encloses the same area and
    casts the same silhouette as a straight one.

    Measured as the difference between the shape and its own straightened
    reading -- `1 - iou(mask, mask.straighten(frame, tolerance))`. Defining it
    in terms of the fix is the point: the number is exactly what
    `Mask.straighten` would change, so a budget on it is a budget on how much of
    the part is drawing noise, and nothing else has to be agreed.

    The obvious metric is not this and was tried: the share of contour cells
    sitting off the chord their neighbours span. It counts the staircase, and a
    staircase is not the defect. A mathematically perfect line at eight degrees
    to the world grid is a staircase -- that is what rasterising a rotated line
    means -- and it scored *higher* than the hand-drawn edge it was meant to
    condemn. Any measure of local roughness has that problem; this one compares
    against the straightest reading of the same shape instead.

    `vertices` is the other half of the answer and often the more legible one:
    how many straight segments the outline really is. A rectangle the map drew
    square comes back four or five. Thirty means thirty, and no tolerance is
    going to make that a rectangle.
    """
    from .mask import iou

    ring = mask.contour()
    if len(ring) < 5 or not mask.count():
        return {"share": 0.0, "cells": 0, "vertices": len(ring)}

    straight = mask.straighten(frame, tolerance)
    return {
        "share": 1.0 - iou(mask, straight),
        "cells": (mask | straight).count() - (mask & straight).count(),
        "vertices": len(straight.contour()) and len(
            _simplify_count(mask, frame, tolerance)),
    }


def _simplify_count(mask: Mask, frame, tolerance: float):
    """The simplified ring itself, for the vertex count."""
    from .mask import _simplify_ring

    ring = mask.contour()
    local = [frame.to_local(x + 0.5, z + 0.5) for x, z in ring]
    return _simplify_ring(local, tolerance)


def level_runs(model, mask: Mask, y0: int = 0, y1: int | None = None) -> dict:
    """How many different heights a surface that should be one height has.

    The top of a wall, a parapet, a roof deck: things a building has one of and
    a build measured station by station has nine of. Photogrammetry noise on a
    flat roof is a metre and more, and a profile cut at every station turns each
    wobble across a block boundary into a step -- so the build comes out a
    staircase where the reference is a plane, and the section is perfectly happy
    because the staircase is exactly as tall as what it was read from.

    Returns the distinct top heights under `mask` and how many cells hold each,
    commonest first. One entry is a level surface. Two or three with one of them
    dominant is a real step. Nine, each with a handful of cells, is noise that
    was built.
    """
    height = model.height if y1 is None else min(model.height, y1)
    area = model.width * model.length
    air = {i for i, block in enumerate(model.palette) if block == AIR}

    tally: dict[int, int] = {}
    for x, z in mask.cells():
        i = z * model.width + x
        top = None
        for y in range(max(0, y0), height):
            if model.blocks[y * area + i] not in air:
                top = y
        if top is not None:
            tally[top] = tally.get(top, 0) + 1

    order = sorted(tally.items(), key=lambda kv: -kv[1])
    covered = sum(tally.values())
    return {
        "levels": order,
        "count": len(order),
        "cells": covered,
        # What share stands at the commonest height. A flat roof is near 1.
        "share": (order[0][1] / covered) if order else 1.0,
    }


class Findings:
    """What the checks saw. Numbers for the gate, lines for the reader.

    The gate needs to threshold on counts, so those are fields rather than text
    it would have to parse back out.
    """

    __slots__ = ("unknown", "pieces", "adrift", "ends", "layers")

    def __init__(self, unknown, pieces, adrift, ends, layers):
        self.unknown = unknown          # block names nothing can draw
        self.pieces = pieces            # list[Group], largest first
        self.adrift = adrift            # the ones that never touch y = 0
        self.ends = ends                # [(x, y, z)] loose member tips
        self.layers = layers            # {y: [Mask]} parts present at that height

    @property
    def strays(self) -> list[Group]:
        """Every piece but the main one."""
        return self.pieces[1:]

    def lines(self, frame=None, limit: int = 10) -> list[str]:
        out = []
        if self.unknown:
            out.append("palette: no colour for " + ", ".join(self.unknown))

        if self.pieces:
            loose = sum(g.count for g in self.strays)
            out.append(
                f"islands: {len(self.pieces)} piece(s), "
                f"largest {self.pieces[0].count} blocks, {loose} blocks elsewhere"
            )
        for g in self.strays[:limit]:
            x, y, z = g.where()
            tail = ""
            if frame is not None:
                u0, u1 = g.span(frame)
                tail = f", u {u0:.0f}..{u1:.0f}"
            flag = " FLOATING" if g.y0 > 0 else ""
            out.append(f"  stray {g.count} blocks at ({x}, {y}, {z}), "
                       f"y {g.y0}..{g.y1}{tail}{flag}")
        if len(self.strays) > limit:
            out.append(f"  ... and {len(self.strays) - limit} more")

        if self.adrift:
            out.append(f"floating: {len(self.adrift)} piece(s) never reach y = 0, "
                       f"{sum(g.count for g in self.adrift)} blocks")

        out.append(f"free ends: {len(self.ends)}")

        for y, comps in sorted(self.layers.items()):
            if frame is None:
                out.append(f"layer y={y:>3}: {len(comps)} part(s)")
                continue
            spans = []
            for c in comps:
                us = [frame.u_of(x + 0.5, z + 0.5) for x, z in c.cells()]
                spans.append(f"{min(us):.0f}-{max(us):.0f}")
            out.append(f"layer y={y:>3}: {len(comps):>2} part(s) at u "
                       + " ".join(spans))
        return out


def inspect(model, layers=(), min_cells: int = 8, soft: set[str] | None = None):
    """Run every check once. The flood fill is the expensive part; it runs once."""
    pieces = islands(model, soft)
    return Findings(
        unknown=palette(model),
        pieces=pieces,
        adrift=floating(pieces),
        ends=free_ends(model, soft),
        layers={y: layer_parts(model, y, min_cells=min_cells) for y in layers},
    )
