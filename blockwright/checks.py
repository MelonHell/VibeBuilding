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

from . import measure
from .blocks import AIR, base, connects, unknown_blocks
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


def cadence(model, mask: Mask, thickness: float = 1.0,
            lo: float = 2.0, hi: float = 9.0, harmonic: float = 0.85) -> dict:
    """The storey rhythm the *build* actually stands at, read off its own wall.

    Every other reading of a storey in this pipeline is taken from something
    that is not the build: the capture's vertex histogram, a count off a
    photograph, a figure declared in words. All of them say what the storey
    ought to be, and nothing says what got laid. That gap is where the defect
    the schedule notes warns about lives -- a loop that distributes `H % step`
    across the levels instead of stamping one step and giving up a floor, so the
    building comes out 3-2-3-3-2 and is a stack of shelves of different
    thicknesses. It has the same total height, the same silhouette, the same
    section station by station and the same single-number storey as a building
    that is right, which is to say every other row here passes it.

    Read the same way the capture is read, and for the same reason: a floor slab,
    a window head and a sill course each put a change in the wall at their own
    level and the blank wall between them does not, so the course-to-course
    *change* along the outside face has one spike per floor and its period is the
    storey. Autocorrelated rather than peak-picked -- the spikes are two and
    three courses wide, so counting gaps between maxima reports 3, 4, 5 and 6 on
    a wall that is perfectly regular, and the correlation reports 5.

    Read **twice**, because a storey line is drawn two different ways and a build
    may use either. A balcony, a loggia and a setback cut air into the wall and
    show in its silhouette; a slab band, a sill course and a spandrel panel cut
    nothing and show only as a change of block. Neither reading covers the other:
    on one building here the silhouette reads r=0.64 where the material reads
    0.37, and on the next it is 0.12 against 0.76 -- on the same wall, for the
    same period. The stronger of the two is returned and `by` says which it was.
    Taking the stronger rather than combining them is what keeps the two from
    diluting each other, and it is safe in a way a maximum usually is not,
    because the verdict this feeds is the *period* and not the score: where
    either reading is confident the two agree about the period, and where they
    disagree neither clears the floor.

    Measured over the outline ring, because that is the face the rhythm is on;
    over the whole footprint the floor plates dominate and every building
    reports its slab spacing whether or not its wall shows one. Courses above the
    part's own top are dropped before the correlation: a tail of zeros is
    perfectly self-similar at every lag and would score any period at all.

    `score` is the correlation and the number to threshold on. A blank wall has
    no rhythm to find and comes back near zero, which is not a failure and not a
    small period -- it is the same "nothing to correlate here" that
    `measure.storey_height` reports on a smooth model.

    `read` is false when the correlation never ran, and it is a different answer
    from a score of zero. A canopy three courses tall cannot show a period of
    five twice over and so cannot be asked about one; reporting `r=0.00` for it
    reads as a wall that was measured and found blank, which is the one thing a
    caller must not conclude about a part that was never measured.

    `harmonic` is how much of the winning correlation a *divisor* of it has to
    keep to be preferred to it -- see the comment where it is applied. Nine tenths
    is too tight to catch the case it is for and a half starts preferring noise.
    """
    height = model.height
    area = model.width * model.length
    air = {i for i, block in enumerate(model.palette) if block == AIR}
    cells = [z * model.width + x for x, z in mask.outline(thickness).cells()]
    if not cells:
        return {"cells": 0, "courses": 0, "period": 0.0, "score": 0.0,
                "read": False}

    courses = []
    for y in range(height):
        base_i = y * area
        courses.append([model.blocks[base_i + i] for i in cells])
    top = max((y for y, c in enumerate(courses)
               if any(b not in air for b in c)), default=-1)
    courses = courses[:top + 1]

    # Four courses is `measure.period`'s own floor, and a part shorter than
    # three of the longest period it is asked about cannot show that period
    # twice. Both are "there was nothing to measure", not "the period is zero".
    if len(courses) - 1 < max(4, int(3 * hi)):
        return {"cells": len(cells), "courses": max(0, len(courses) - 1),
                "period": 0.0, "score": 0.0, "read": False}

    best = (0.0, 0.0, "")
    for how, wall in (("silhouette", [[b not in air for b in c] for c in courses]),
                      ("material", courses)):
        change = [
            sum(1 for p, q in zip(wall[y - 1], wall[y]) if p != q) / len(cells)
            for y in range(1, len(wall))
        ]
        found = measure.period(change, 1.0, lo, hi)
        # A signal that repeats every three courses repeats every six as well,
        # and the correlation says so: the harmonic is a peak of its own, within
        # a few per cent of the fundamental, and it wins outright about as often
        # as it loses. Two towers of one building read 3 and 6 off the same wall
        # detail that way, which is a storey of three reported as one of six.
        #
        # So the fundamental is preferred over its own multiples, and *only*
        # over its own multiples. Not "prefer the smallest peak": a wall stepping
        # 3-2-3-3-2 peaks at 8 and again at 5, neither of which divides the
        # other, and taking the smaller there would report a period the wall does
        # not stand at and hide the very defect this is for.
        value, score = found.value, found.score
        for lag, r in sorted(found.peaks):
            if lag < value and r >= harmonic * score and abs(
                    value / lag - round(value / lag)) < 1e-9:
                value, score = lag, r
                break
        if score > best[1]:
            best = (value, score, how)
    return {"cells": len(cells), "courses": len(courses) - 1,
            "period": best[0], "score": round(best[1], 3), "by": best[2],
            "read": True}


def copies(model, motif: Mask, y0: int, y1: int,
           step: tuple[int, int], count: int) -> dict:
    """Are the copies of a repeated section the same blocks, cell for cell?

    Nothing else here can ask this. Eight sections and eight nearly-identical
    sections cast the same silhouette, cut the same section, cover the same
    plan, hold the same materials in the same proportions and satisfy the same
    schedule; the difference between them is visible from the ground and to no
    row of the gate. It is also the difference between a terrace somebody built
    with copy-paste and one that was drawn eight times, which is the whole
    reason `Canvas.stamp` and `blockwright.lattice` exist.

    **Byte-identity, with no tolerance at all.** A budget of "near enough"
    readmits the loop that redraws each section from the same numbers and lands
    each one in its own sub-cell phase -- which is exactly what is being
    stopped, and which differs from a copy by a handful of cells per section.

    The one exception is named rather than measured. `Canvas.finalize` gives
    panes, fences, bars and walls the state their neighbours imply, and the
    copies at the two ends of a run have different neighbours: the outer face of
    the last section joins nothing, where the same face of the middle sections
    joins the next one. Those cells are on the motif's rim and are of a family
    `blocks.connects` knows, and they are compared by name without state. Every
    one is counted and reported, so an excuse that starts covering half the
    building says so.
    """
    if count < 2:
        raise ValueError("a repeat is two copies or more")
    dx, dz = int(step[0]), int(step[1])
    cells = motif.cells()
    rim = {(x, z) for x, z in motif.rim().cells()}
    courses = list(range(int(y0), int(y1)))
    width, length = model.width, model.length
    height = model.height

    def read(x: int, y: int, z: int) -> str | None:
        if not (0 <= x < width and 0 <= z < length and 0 <= y < height):
            return None
        return model.get(x, y, z)

    prints: list[int] = []
    differ: list[int] = []
    excused = 0
    families: dict[str, int] = {}
    first = None
    outside = 0

    for n in range(count):
        ox, oz = dx * n, dz * n
        seen: list[str] = []
        wrong = 0
        for x, z in cells:
            loose = (x, z) in rim
            for y in courses:
                block = read(x + ox, y, z + oz)
                if block is None:
                    outside += 1
                    seen.append("<off the grid>")
                    continue
                if loose and connects(block):
                    # A joining block on the rim is compared by what it is, not
                    # by what it found beside it.
                    seen.append(base(block))
                    if n and block != read(x, y, z):
                        excused += 1
                        families[connects(block)] = \
                            families.get(connects(block), 0) + 1
                    continue
                seen.append(block)
                if n and block != read(x, y, z):
                    wrong += 1
                    if first is None:
                        first = (n, x + ox, y, z + oz, read(x, y, z), block)
        prints.append(hash(tuple(seen)))
        differ.append(wrong)

    distinct = len(set(prints))
    return {
        "copies": count,
        "cells": len(cells),
        "courses": len(courses),
        "step": [dx, dz],
        "distinct": distinct,
        "differ": differ,
        "worst": max(differ) if differ else 0,
        "first": first,
        "excused": excused,
        "families": families,
        "outside": outside,
        "ok": distinct == 1 and not outside,
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


def mix_matrix(mixes: dict) -> list[tuple[str, str, float]]:
    """Every pair of facade stretches and how far apart they are, closest first.

    `facade_mix` answers one stretch and `mix_apart` answers one pair; this is
    the table, and the table is what says the thing neither of them can. A gate
    row can only ask what somebody thought to ask it -- "these two should
    differ" -- and the failure that actually ships is the opposite one: two
    stretches the build made identical without anybody noticing they are, and so
    without anybody going to look at whether the reference agrees.

    Closest first because that end is the interesting one. Two stretches at 0.01
    are the same wall by construction, and either the building really is uniform
    there or a wing has just been given its neighbour's balconies.
    """
    names = list(mixes)
    out = []
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            one, two = mixes[a], mixes[b]
            if not one.get("cells") or not two.get("cells"):
                continue
            out.append((a, b, mix_apart(one["mix"], two["mix"])))
    out.sort(key=lambda row: row[2])
    return out


def conformance(drawn: Mask, built: Mask, within: Mask | None = None) -> dict:
    """How far the build stands from the plan it was drawn from.

    **Conformance and not resemblance**, and the two must not be read as one --
    `docs/sources.md`, "Сходство и соответствие". The plan is an input, so
    nothing here bears on whether the building looks like the real one; what it
    says is whether the machinery between the drawing and the schematic kept the
    shape it was handed. That is an ordinary verification, and between those two
    files sit the palette, the decomposition, the frame fit, the tracing, the
    pad, the closing, the straightening and the whole of the recipe.

    Two numbers, because the two directions fail for different reasons and get
    fixed in different files:

    `missing` -- the share of the drawn part the build did not stand on. A
    footprint that was closed, padded and straightened into something smaller
    than the map drew; a wing the recipe forgot; a court built closed. Always
    worth grading: the plan says material is there.

    `outside` -- the share, in units of the drawn part's own area, of build
    material standing beyond `within` (the whole drawn plan, normally, so a
    neighbouring part does not count as an escape). Legitimately non-zero on
    most buildings -- a balcony, a cornice, a canopy and a deck all overhang a
    plan the map drew as walls -- so it is a number to print and to threshold
    per building rather than a fault by itself.

    **`worst_missing` and `worst_outside` are what to grade, and the shares are
    what to print.** The two states this has to tell apart are "the outline
    moved by a cell all the way round", which is the pad and the straightening
    doing their job, and "there is a wedge of forty-four cells out past the
    corner", which is a defect -- and by area they are nearly the same building.
    One real case: a clean build scored 0.981 and the one with the wedge in it
    scored 0.974, six thousandths apart and both plausible. The largest
    connected piece of the disagreement separates them at a glance: one to three
    cells against forty-four. So the blob is the number with a budget on it and
    the overlap is a number to read.

    `built` should be a filled slice -- `slice | slice.holes()` -- or every room
    in the building reads as a hole the build failed to fill.
    """
    from .mask import iou

    want = drawn.count()
    if not want:
        return {"missing": 0.0, "outside": 0.0, "iou": 1.0, "cells": 0,
                "worst_missing": 0, "worst_outside": 0}
    inside = (built & drawn).count()
    short = drawn - built
    beyond = built - (within if within is not None else drawn)

    def biggest(mask: Mask) -> int:
        found = mask.components()
        return found[0].count() if found else 0

    return {
        "missing": 1.0 - inside / want,
        "outside": beyond.count() / want,
        "iou": iou(drawn, built & drawn) if inside else 0.0,
        "cells": want,
        "worst_missing": biggest(short),
        "worst_outside": biggest(beyond),
    }


def corners(mask: Mask, frame, tolerance: float = 1.0,
            square: float = 20.0) -> dict:
    """How many of a shape's corners are right angles, and what the rest are.

    The question no other check here asks, and the one a person asks first
    standing in front of a render: **is that corner square?** Every other number
    in this file is blind to it. A rounded corner casts the same silhouette,
    grades the same at every station of the section, encloses the area it should
    to within a few cells, and is as straight as the map along both of the edges
    that meet at it -- `jaggedness` measures a shape against its own
    straightened reading, and a chamfer four cells deep survives any tolerance
    worth using, because it is further off the chord than drawing noise ever is.

    It matters because the pipeline puts the defect in by itself. `Site.footprint`
    closes a traced mask and pads it, both Euclidean, and a Euclidean dilation is
    a disc: every convex corner comes back bitten by a quarter-disc of about
    `pad + CLOSE`. Add the chamfer a hand draws and a building whose plan is
    three rectangles arrives with twelve rounded corners, silently.

    Measured on the simplified outline -- the same reading `jaggedness` and
    `Mask.straighten` use, so the three agree about what a vertex is -- as the
    turn at each vertex. A turn within `square` degrees of a right angle counts
    as one. `square` is deliberately loose: at one block to the metre a right
    angle traced across a diagonal frame comes back a few degrees off however
    well it was drawn, and the difference being looked for is between 90 and 45,
    not between 90 and 87.
    """
    rings = mask._rings(frame, tolerance)
    turns: list[float] = []
    for ring in rings:
        n = len(ring)
        if n < 3:
            continue
        for i in range(n):
            before, here, after = ring[i - 1], ring[i], ring[(i + 1) % n]
            one = math.atan2(here[1] - before[1], here[0] - before[0])
            two = math.atan2(after[1] - here[1], after[0] - here[0])
            turn = math.degrees(two - one)
            while turn > 180.0:
                turn -= 360.0
            while turn < -180.0:
                turn += 360.0
            turns.append(turn)
    right = sum(1 for t in turns if abs(abs(t) - 90.0) <= square)
    return {
        "vertices": len(turns),
        "right": right,
        "share": right / len(turns) if turns else 0.0,
        "turns": sorted(round(t, 1) for t in turns),
    }


def rectangular(mask: Mask, frame, trim: float = 0.02,
                around: Mask | None = None) -> dict:
    """How much of a shape is the rectangle fitted to it.

    For a surface somebody declared rectangular: a poured slab, a deck over a
    garage, a court, a car park. The reading it is built from is never a
    rectangle -- a height map of paving read off photogrammetry has a ragged
    edge, a bite where the pool is, and a scatter of missing cells under the
    trees -- and every one of those is the reading rather than the thing.

    `around` is what stands in it: the building's own footprint, usually. A
    ground is a rectangle with the building bitten out of it and comparing it
    against a whole rectangle would condemn every one of them, so the shape it
    is held to is the fitted rectangle *less* whatever was standing there.

    `share` is the overlap between the two, so 1.0 is a rectangle and anything
    much under it is a shape that is not one. Which of the two readings is right
    is the caller's business: this says how far apart they are.
    """
    from .mask import iou

    box = mask.squared(frame, trim=trim)
    if box is None:
        return {"share": 0.0, "box": None, "cells": 0, "box_cells": 0}
    u0, u1, v0, v1 = box
    drawn = frame.rect(mask.width, mask.length, u0, u1, v0, v1)
    if around is not None:
        drawn = drawn - around
    return {
        "share": iou(mask, drawn),
        "box": [round(n, 1) for n in box],
        "cells": mask.count(),
        "box_cells": drawn.count(),
    }


def corner_reach(mask: Mask, frame, box) -> dict:
    """How far from each corner of a box the shape inside it actually starts.

    `rectangular` cannot answer this and the arithmetic says why. A pad and a
    closing take a quarter-disc of about a metre and a half off a convex
    corner; four of those are seven square metres, and on a part of twelve
    hundred cells that is six tenths of one per cent against a budget of five.
    The share is *thirty times too coarse to see the defect the row exists
    for*, while being fully sensitive to things the plan never had -- a facade
    rhythm of two-cell recesses cost one building eight per cent of its share
    and no corner at all.

    So the corner is measured where it is: the distance from each corner of the
    box to the nearest cell the shape holds, in metres. A square corner reads
    about half a cell, a corner bitten by a disc of `pad + CLOSE` reads that
    much and a half again, and the two do not overlap.

    `box` is `(u0, u1, v0, v1)` in frame coordinates, as `rectangular` returns
    it. Pass the *drawn* box for both readings when comparing a build against
    its plan: the question is whether the corner is still where the plan put
    it, and a box refitted to the build moves with the defect.
    """
    u0, u1, v0, v1 = box
    wanted = [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    reach = [float("inf")] * 4
    for x, z in mask.cells():
        u = frame.u_of(x + 0.5, z + 0.5)
        v = frame.v_of(x + 0.5, z + 0.5)
        for i, (cu, cv) in enumerate(wanted):
            d = math.hypot(u - cu, v - cv)
            if d < reach[i]:
                reach[i] = d
    return {
        "reach": [round(d, 2) for d in reach],
        "worst": max(reach) if reach else float("inf"),
        "corners": [(round(u, 1), round(v, 1)) for u, v in wanted],
    }


def surface(mask: Mask, frame, reading, low: float,
            high: float | None = None) -> dict:
    """A ground the build laid, against the ground the reference reads there.

    Two numbers and not a shape, which is the whole design. The clip is of the
    site, so the reference does hold the deck, the road and the beach -- it
    still does not witness their *shape*, and without this row they can be any
    shape at all and every other row stays green. A capture is no authority on
    the shape of a ground either (`docs/sources.md`), so asking about one would
    be asking the wrong witness. What a capture *is* an authority on is height,
    and a height band gives an area and an extent: how much of the plot stands
    at deck level, and how far it reaches. Those two are enough to catch a deck
    built at half its size or running the length of the block.

    This used to say the clip cut the grounds away by construction, which was
    the doctrine when the clip was cut round the building. That premise is gone;
    the design it argued for is not, because the missing witness was never the
    clip -- it was that nothing photographs a shape into a number.

    `reading` is a `measure.Ground`. The bands are the building's, because which
    level is a deck and which is a road is a fact about that plot.
    """
    cell = reading.cell
    area = cell * cell
    read_cells = [(a, b) for (a, b), h in reading.cells.items()
                  if h >= low and (high is None or h < high)]
    built = [frame.to_local(x + 0.5, z + 0.5) for x, z in mask.cells()]

    def extent(values):
        return (min(values), max(values)) if values else (0.0, 0.0)

    read_u = extent([a * cell + cell / 2 for a, _ in read_cells])
    read_v = extent([b * cell + cell / 2 for _, b in read_cells])
    return {
        "built_area": float(len(built)),
        "read_area": len(read_cells) * area,
        "built_u": [round(n, 1) for n in extent([u for u, _ in built])],
        "built_v": [round(n, 1) for n in extent([v for _, v in built])],
        "read_u": [round(n, 1) for n in read_u],
        "read_v": [round(n, 1) for n in read_v],
        "read_cells": len(read_cells),
    }


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
