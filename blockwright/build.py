"""The block canvas and the primitives that write to it.

Everything here works on 2D masks lifted through a height range, rather than on
boxes. That is what lets a building sit at 52 degrees without any special
handling: the footprint is rasterised once, at the right angle, by `Frame.region`,
and a wall, a setback or a terrace is that same footprint with a morphological
operation applied. No primitive ever has to rotate anything.

Two coordinates drive the detail work:

    u, v    from Frame -- position along and across the building
    s       from Facade -- distance walked along the outside wall

`s` is the one that makes window bays land correctly. Spacing them by u works on
a straight facade and falls apart on the rotunda at the south end, where a
constant step in u covers a varying length of wall. Arc length does not care what
shape the wall is.

    canvas = Canvas(256, 40, 256)
    walls(canvas, footprint, 0, 12, "minecraft:white_concrete")
    canvas.to_schematic().write("massing.schem")
"""

from __future__ import annotations

import math
from array import array
from pathlib import Path

from . import blocks as blocklib
from .mask import Mask
from .schem import AIR, Schematic

# The four sides a connecting block reaches towards, and the world direction
# each one means. z grows south, matching Minecraft.
_SIDES = (("north", 0, -1), ("east", 1, 0), ("south", 0, 1), ("west", -1, 0))

# All eight, for asking which way is out of a wall.
_AROUND = ((-1, -1), (0, -1), (1, -1), (-1, 0),
           (1, 0), (-1, 1), (0, 1), (1, 1))

MAX_PALETTE = 1 << 16


class Canvas:
    """A dense box of blocks, addressed the same way a schematic is."""

    __slots__ = ("width", "height", "length", "palette", "_ids", "data")

    def __init__(self, width: int, height: int, length: int):
        self.width = width
        self.height = height
        self.length = length
        self.palette = [AIR]
        self._ids = {AIR: 0}
        # Two bytes a cell. A byte was enough while every block was a plain
        # cube, but state is part of the block string here, so one glass pane
        # becomes up to sixteen palette entries and one stair four -- a handful
        # of detailed families exhausts 256 on its own.
        self.data = array("H", bytes(2 * width * height * length))

    @classmethod
    def like(cls, schematic: Schematic, height: int | None = None) -> "Canvas":
        """A canvas on the same XZ grid as a layout, so coordinates line up."""
        return cls(schematic.width, height or schematic.height, schematic.length)

    def palette_id(self, block: str) -> int:
        i = self._ids.get(block)
        if i is None:
            if len(self.palette) >= MAX_PALETTE:
                raise ValueError(
                    f"canvas palette is limited to {MAX_PALETTE} blocks")
            i = len(self.palette)
            self.palette.append(block)
            self._ids[block] = i
        return i

    def _clamp(self, y0: int, y1: int) -> tuple[int, int]:
        return max(0, int(y0)), min(self.height, int(y1))

    # -- writing ----------------------------------------------------------

    def fill(self, mask: Mask, y0: int, y1: int, block: str) -> int:
        """Extrude a mask through [y0, y1). Returns blocks written."""
        if (mask.width, mask.length) != (self.width, self.length):
            raise ValueError("mask does not match the canvas grid")
        value = self.palette_id(block)
        y0, y1 = self._clamp(y0, y1)
        cells = [i for i, v in enumerate(mask.bits) if v]
        area = self.width * self.length
        for y in range(y0, y1):
            base = y * area
            for i in cells:
                self.data[base + i] = value
        return len(cells) * max(0, y1 - y0)

    def fill_fn(self, mask: Mask, y0: int, y1: int, fn) -> int:
        """Extrude a mask, asking `fn(x, y, z)` which block goes in each cell.

        The one place a primitive can vary block state across the volume it
        writes: a stair's facing, a slab's half, a rail's colour. Returning None
        leaves the cell as it was, so a sparse decoration needs no second mask
        built to hold its gaps.
        """
        if (mask.width, mask.length) != (self.width, self.length):
            raise ValueError("mask does not match the canvas grid")
        y0, y1 = self._clamp(y0, y1)
        cells = [i for i, v in enumerate(mask.bits) if v]
        area = self.width * self.length
        written = 0
        for y in range(y0, y1):
            base = y * area
            for i in cells:
                block = fn(i % self.width, y, i // self.width)
                if block is None:
                    continue
                self.data[base + i] = self.palette_id(block)
                written += 1
        return written

    def set(self, x: int, y: int, z: int, block: str) -> None:
        """One cell. Out of bounds is a mistake, not a silent no-op."""
        if not (0 <= x < self.width and 0 <= y < self.height
                and 0 <= z < self.length):
            raise IndexError(f"({x}, {y}, {z}) is outside the canvas")
        self.data[(y * self.length + z) * self.width + x] = self.palette_id(block)

    def carve(self, mask: Mask, y0: int, y1: int) -> int:
        """Cut a mask out again -- windows, doorways, light wells."""
        return self.fill(mask, y0, y1, AIR)

    def repeat(self, y0: int, y1: int, step: int, count: int,
               mask: Mask | None = None) -> int:
        """Copy the courses [y0, y1) upwards `count` more times, every `step`.

        For the storey a building has more than one of. Drawn once and stamped,
        every floor is identical because it is the same blocks -- where a loop
        that redraws each floor from the same numbers is identical only as long
        as nothing rounds differently at one level, and something always does:
        a level carried as 5.75 becomes 5 at one storey and 6 at the next, and
        the facade bands step out of line halfway up.

        Along y this is exact and needs no argument. The frame is
        two-dimensional and y is the one axis the rotation never touches, so a
        vertical stamp moves whole cells onto whole cells -- no resampling, no
        pinholes, nothing to round. That is precisely what a horizontal mirror
        of the block array is not; see `Frame.flipped`.

        `mask` limits it to one part of the plan, for a building whose tower
        repeats and whose podium does not. Blocks are copied as written and
        `finalize` still runs once at the end, so connecting states come out
        right for the neighbours each copy actually has.
        """
        if step <= 0:
            raise ValueError("a repeat rises by at least one course")
        y0, y1 = self._clamp(y0, y1)
        if y1 <= y0:
            return 0
        area = self.width * self.length
        cells = (list(range(area)) if mask is None
                 else [i for i, v in enumerate(mask.bits) if v])
        if mask is not None and (mask.width, mask.length) != (self.width,
                                                              self.length):
            raise ValueError("mask does not match the canvas grid")

        written = 0
        for n in range(1, count + 1):
            lift = n * step
            for y in range(y0, y1):
                if y + lift >= self.height:
                    break
                source = y * area
                target = (y + lift) * area
                for i in cells:
                    value = self.data[source + i]
                    if value:
                        self.data[target + i] = value
                        written += 1
        return written

    def finalize(self) -> int:
        """Give panes, bars, fences and walls the state their neighbours imply.

        WorldEdit pastes a schematic without running block updates, so whatever
        state is written is the state that stands in the world. A pane written
        plain stays a lone post forever, and a balustrade comes out as a row of
        disconnected stubs. The renderer already draws it that way -- honestly --
        and this is what makes the honest drawing the right one.

        One pass is enough, and the reason matters: the decision reads the
        neighbour's base name and its shape, neither of which this changes. No
        cell's answer depends on whether its neighbour has been visited yet, so
        there is no ordering to get wrong and no second pass to converge.

        Returns the number of blocks rewritten.
        """
        family = [blocklib.connects(b) for b in self.palette]
        if not any(family):
            return 0

        area = self.width * self.length
        joinable: dict[tuple[str, int], bool] = {}
        rewritten = 0

        for i in range(len(self.data)):
            value = self.data[i]
            kind = family[value]
            if kind is None:
                continue
            y, rest = divmod(i, area)
            z, x = divmod(rest, self.width)

            sides = {}
            for name, dx, dz in _SIDES:
                nx, nz = x + dx, z + dz
                if not (0 <= nx < self.width and 0 <= nz < self.length):
                    sides[name] = False
                    continue
                n = self.data[y * area + nz * self.width + nx]
                key = (kind, n)
                hit = joinable.get(key)
                if hit is None:
                    hit = blocklib.joins(kind, self.palette[n])
                    joinable[key] = hit
                sides[name] = hit

            if kind == "wall":
                values = {k: ("low" if v else "none") for k, v in sides.items()}
                # A wall shows its post unless it is a straight run with nothing
                # standing on it. That is vanilla's rule minus the cases about
                # what exactly is above, which is close enough to look right and
                # is stated here rather than being a mystery in the output.
                through = ((sides["north"] and sides["south"]
                            and not sides["east"] and not sides["west"])
                           or (sides["east"] and sides["west"]
                               and not sides["north"] and not sides["south"]))
                above = self.data[(y + 1) * area + rest] if y + 1 < self.height else 0
                values["up"] = "false" if through and not above else "true"
            else:
                values = {k: ("true" if v else "false") for k, v in sides.items()}

            new = self.palette_id(blocklib.with_state(self.palette[value], **values))
            while len(family) < len(self.palette):
                family.append(blocklib.connects(self.palette[len(family)]))
            if new != value:
                self.data[i] = new
                rewritten += 1

        return rewritten

    # -- reading ----------------------------------------------------------

    def get(self, x: int, y: int, z: int) -> str:
        return self.palette[self.data[(y * self.length + z) * self.width + x]]

    def layer(self, y: int) -> Mask:
        """Non-air cells of one layer, as a mask."""
        area = self.width * self.length
        chunk = self.data[y * area : (y + 1) * area]
        return Mask(
            self.width, self.length, bytearray(1 if v else 0 for v in chunk)
        )

    def silhouette(self) -> Mask:
        """Every column that holds anything -- the built footprint."""
        out = Mask(self.width, self.length)
        area = self.width * self.length
        for y in range(self.height):
            base = y * area
            for i in range(area):
                if self.data[base + i]:
                    out.bits[i] = 1
        return out

    def counts(self) -> dict[str, int]:
        tally: dict[str, int] = {}
        for value in self.data:
            if value:
                block = self.palette[value]
                tally[block] = tally.get(block, 0) + 1
        return dict(sorted(tally.items(), key=lambda kv: -kv[1]))

    def block_count(self) -> int:
        return sum(1 for v in self.data if v)

    # -- output -----------------------------------------------------------

    def to_schematic(
        self,
        origin: tuple[int, int, int] = (0, 0, 0),
        offset: tuple[int, int, int] = (0, 0, 0),
    ) -> Schematic:
        return Schematic(
            self.width,
            self.height,
            self.length,
            palette=list(self.palette),
            blocks=list(self.data),
            origin=origin,
            offset=offset,
        )

    def write(self, path: str | Path, **placement) -> None:
        self.to_schematic(**placement).write(path)


# -- architectural primitives ---------------------------------------------
#
# Each takes the canvas plus the footprint mask it applies to, so they compose:
# slice the footprint first, then build different things on each slice.


def solid(canvas: Canvas, footprint: Mask, y0: int, y1: int, block: str) -> int:
    """A filled volume. Massing studies only -- real buildings are hollow."""
    return canvas.fill(footprint, y0, y1, block)


def walls(
    canvas: Canvas,
    footprint: Mask,
    y0: int,
    y1: int,
    block: str,
    thickness: float = 1.0,
) -> int:
    """A closed wall ring following the footprint edge.

    Watertight by construction: the ring is the footprint minus its erosion, so
    it can never have the diagonal pinholes a hand-rasterised band would.
    """
    return canvas.fill(footprint.outline(thickness), y0, y1, block)


def slab(
    canvas: Canvas, footprint: Mask, y: float, block: str, inset: float = 0.0
) -> int:
    """One horizontal plate -- a floor, a roof, or a terrace deck.

    `y` is the level the underside of the plate sits at, so a whole number puts
    it on a cell boundary and a half puts it halfway up a cell. Half a level is
    only meaningful for a slab block: it is written `type=top`, and a whole level
    `type=bottom`, which is the volume a plain slab already occupies.

    Asking for half a level with anything else raises rather than rounding. A
    full cube cannot sit at half height, and quietly moving it half a metre is
    exactly the class of silent lie the rest of this pipeline exists to stop.
    """
    cell = math.floor(y)
    half = y - cell
    if half < 1e-9:
        half = 0.0
    elif abs(half - 0.5) < 1e-9:
        half = 0.5
    else:
        raise ValueError(f"a plate sits on a whole or a half level, not {y}")

    if blocklib.base(block).endswith("_slab"):
        block = blocklib.with_state(block, type="top" if half else "bottom")
    elif half:
        raise ValueError(f"{block} is not a slab, so it cannot sit at {y}")

    return canvas.fill(footprint.erode(inset), cell, cell + 1, block)


def storeys(
    canvas: Canvas,
    footprint: Mask,
    base: int,
    floor_height: int,
    count: int,
    wall: str,
    floor: str | None = None,
    thickness: float = 1.0,
    inset: float | None = None,
) -> int:
    """Repeat a wall ring and floor plate `count` times.

    Floor height is a per-building measurement, not a constant: it comes from
    the reference photographs and elevations, and a 1960s beachfront block does
    not share it with a modern tower.

    Floor plates are inset behind the wall by default. Letting them run out to
    the footprint edge puts a stripe of floor material on the facade at every
    level, which at 1 m per block leaves no continuous wall between the stripes
    -- the building ends up reading as a stack of plates.
    """
    if inset is None:
        inset = thickness
    written = 0
    for n in range(count):
        y = base + n * floor_height
        if floor:
            written += slab(canvas, footprint, y, floor, inset=inset)
        written += walls(canvas, footprint, y, y + floor_height, wall, thickness)
    return written


def band(
    canvas: Canvas,
    footprint: Mask,
    y0: int,
    y1: int,
    block: str,
    project: float = 0.0,
    thickness: float = 1.0,
) -> int:
    """A horizontal course wrapping the facade -- cornice, sill, eyebrow.

    `project` pushes it out past the wall face, which is the single strongest
    Streamline Moderne cue and the one that reads at 1:1 where surface detail
    does not.
    """
    ring = footprint.dilate(project) if project else footprint
    return canvas.fill(ring.outline(thickness + project), y0, y1, block)


def setback(footprint: Mask, distance: float) -> Mask:
    """The footprint of an upper volume that steps in by `distance`."""
    return footprint.erode(distance)


class Facade:
    """Distance walked along the outside of a footprint.

    The wall is traced once as an ordered ring, then that arc length is pushed
    inwards over the rest of the footprint by breadth-first search, so any wall
    cell -- including one on a thicker wall that is not itself on the ring --
    can answer "how far along the facade am I".

    The ring is smoothed before it is measured. A traced contour is a staircase,
    and summing step lengths along it measures the zigzag rather than the wall:
    on this footprint's 52-degree facades that over-reads by about 5%, which is
    eight metres of accumulated phase error over the building's length -- enough
    to walk the window bays out of step with everything else. Smoothing also
    settles the disagreement between a staircase's step and riser cells, which
    sit at the same point along the wall but a metre apart in raw arc length.
    """

    __slots__ = ("width", "length", "ring", "rings", "s", "facing",
                 "perimeter", "perimeters", "piece")

    SMOOTH = 2  # half-width of the averaging window, in contour cells

    def __init__(self, footprint: Mask):
        self.width = footprint.width
        self.length = footprint.length
        area = footprint.width * footprint.length
        self.s = [-1.0] * area
        self.facing = [-1] * area
        # Which piece of the footprint each cell belongs to, so a rhythm can be
        # set out per piece rather than per mask.
        self.piece = [-1] * area

        # One ring per piece, and this is the whole reason the class does not
        # simply call `contour()`.
        #
        # `Mask.contour` traces the outermost ring of **one** component -- it
        # says so -- and starts from the raster-first set cell. Handed a mask
        # with two pieces in it, everything below then ran on the first piece
        # alone: every cell of the second kept `s = -1`, `bays` skips those
        # (`if value < 0: continue`), and the caller got an empty opening mask
        # for half its building. Silently. A hotel shipped with one blank tower
        # and a villa row shipped with seven blank villas, and neither the
        # schedule audit nor the section can see a missing window.
        #
        # Arc length restarts at zero on each piece, which is also what makes a
        # mirror pair come out a mirror pair: two towers handed to one Facade
        # get the same rhythm from the same phase instead of the second one
        # inheriting whatever the first one's perimeter happened to leave.
        self.rings = []
        self.perimeters = []
        for index, part in enumerate(footprint.components(min_cells=1)):
            ring = part.contour()
            if not ring:
                continue
            path = self._smoothed(ring)
            walked = 0.0
            for i, (x, z) in enumerate(ring):
                if i:
                    px, pz = path[i - 1]
                    walked += math.hypot(path[i][0] - px, path[i][1] - pz)
                self.s[z * self.width + x] = walked
                self.piece[z * self.width + x] = len(self.rings)
            for (x, z), side in zip(ring, self._outward(part, ring)):
                self.facing[z * self.width + x] = side
            self.rings.append(ring)
            self.perimeters.append(walked)

        # Kept for the common case and for anything that reports one number.
        self.ring = self.rings[0] if self.rings else []
        self.perimeter = max(self.perimeters, default=0.0)

        # Spread inwards so thick walls inherit the ring's arc length, the
        # direction it looks out in, and which piece they are part of.
        queue = [z * self.width + x for ring in self.rings for x, z in ring]
        head = 0
        while head < len(queue):
            i = queue[head]
            head += 1
            x, z = i % self.width, i // self.width
            for dx, dz in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nx, nz = x + dx, z + dz
                if not (0 <= nx < self.width and 0 <= nz < self.length):
                    continue
                j = nz * self.width + nx
                if footprint.bits[j] and self.s[j] < 0:
                    self.s[j] = self.s[i]
                    self.facing[j] = self.facing[i]
                    self.piece[j] = self.piece[i]
                    queue.append(j)

    def _outward(self, footprint: Mask, ring) -> list[int]:
        """Which way each ring cell looks out, as an index into `_SIDES`.

        Taken from the clear side of each cell rather than from the contour's
        winding, so it does not quietly depend on which way round the tracer
        walked -- and then averaged over the same window that smooths the path.
        A single cell's clear neighbours on a 52-degree wall alternate between
        two cardinals from one step to the next, and a facing that alternates
        writes a run of stairs that rotate back and forth along a straight wall.
        """
        raw = []
        for x, z in ring:
            ox = oz = 0.0
            for dx, dz in _AROUND:
                if not footprint.get(x + dx, z + dz):
                    d = math.hypot(dx, dz)
                    ox += dx / d
                    oz += dz / d
            raw.append((ox, oz))

        n = len(raw)
        span = min(self.SMOOTH, (n - 1) // 2)
        out = []
        for i in range(n):
            sx = sz = 0.0
            for k in range(-span, span + 1):
                vx, vz = raw[(i + k) % n]
                sx += vx
                sz += vz
            if not sx and not sz:
                sx, sz = raw[i]     # a one-cell spike cancels; trust the cell
            best, score = 0, -1e9
            for c, (_name, dx, dz) in enumerate(_SIDES):
                dot = sx * dx + sz * dz
                if dot > score:
                    score, best = dot, c
            out.append(best)
        return out

    @classmethod
    def _smoothed(cls, ring: list[tuple[int, int]]) -> list[tuple[float, float]]:
        """Moving average around the closed ring, to take the stairs off it."""
        n = len(ring)
        if n <= 2 * cls.SMOOTH:
            return [(float(x), float(z)) for x, z in ring]
        span = 2 * cls.SMOOTH + 1
        out = []
        for i in range(n):
            sx = sz = 0.0
            for k in range(-cls.SMOOTH, cls.SMOOTH + 1):
                x, z = ring[(i + k) % n]
                sx += x
                sz += z
            out.append((sx / span, sz / span))
        return out

    def s_of(self, x: int, z: int) -> float:
        return self.s[z * self.width + x]

    def facing_of(self, x: int, z: int) -> str | None:
        """Which way the wall at this cell looks out, or None if it is not wall.

        The direction points away from the building. That is the raw fact; what
        each block wants doing with it is the call site's business. A stair's
        `facing` names the side its full-height part stands on, so a cornice
        shedding outwards takes `blocklib.opposite` of this; a trapdoor hung open
        against the wall takes it as it comes.

        This lives on Facade and not on Mask on purpose. A direction counted from
        a cell's clear neighbours and a direction taken from a smoothed contour
        normal disagree on every diagonal wall, and two plausible answers to the
        same question is the failure `blocks.py` was written to end.
        """
        side = self.facing[z * self.width + x]
        return None if side < 0 else _SIDES[side][0]

    def bays(self, period: float, width: float, phase: float = 0.0) -> Mask:
        """Cells that fall inside a repeating opening of `width` every `period`.

        The period is nudged so a whole number of bays fits the perimeter --
        otherwise the last bay before the loop closes lands hard against the
        first one. Nudged **per piece**, because that is where a ring closes: one
        step computed from the longest piece leaves every other piece with a
        doubled or a missing bay wherever its loop comes round.
        """
        if period <= 0:
            raise ValueError("bay period must be positive")
        # A piece too small to have a perimeter -- one cell, or a two-cell
        # splinter the rasteriser left at a corner -- has no rhythm to set out,
        # and dividing by its zero-length loop is a crash rather than an answer.
        # It falls back to the period it was asked for, which puts its one cell
        # in a bay: at that size the difference is invisible and the alternative
        # is the build stopping on a splinter.
        steps = [perimeter / max(1, round(perimeter / period)) if perimeter
                 else period
                 for perimeter in self.perimeters] or [period]
        out = Mask(self.width, self.length)
        for i, value in enumerate(self.s):
            if value < 0:
                continue
            step = steps[self.piece[i]] if self.piece[i] >= 0 else steps[0]
            if (value - phase) % step < width:
                out.bits[i] = 1
        return out


# -- planting ---------------------------------------------------------------
#
# Not architecture, and here anyway. Five buildings each wrote their own palm
# and their own scatter, three of them wrote the same paragraph of comment
# explaining the same mistake, and one of them still has the mistake.


def scatter(mask: Mask, pitch, seed: int = 0) -> list[tuple[int, int]]:
    """One position per `pitch` metres of a region, jittered inside its cell.

    Two ways of doing this are wrong and both have been built.

    Every nth cell of the mask spaces by *cell order*, which runs along the
    world grid. On a frame at fifty degrees to it that is not a distance at all:
    the picks bunch into diagonal streaks, and a belt that runs diagonally holds
    a different number of cells per metre than one square to the grid, so the
    same `n` plants a row that thins out wherever the wall turns.

    One at every grid intersection spaces correctly and reads as an avenue.
    Photographs of these sites are groves: things stand *near* a rhythm, not on
    it.

    So: bucket by a `pitch` grid, and take one cell out of each bucket chosen by
    a hash of the bucket. Deterministic -- the same crop gives the same grove on
    every run, which is what makes a render diff mean something -- and off the
    grid lines, which is what makes it read as planting.

    `pitch` may be a callable `(x, z) -> float`, for a region that is dense belt
    at one end and open terrace at the other.
    """
    buckets: dict[tuple, list[tuple[int, int]]] = {}
    for x, z in mask.cells():
        step = pitch(x, z) if callable(pitch) else pitch
        if step <= 0:
            continue
        buckets.setdefault(
            (int(x // step), int(z // step), round(step, 3)), []).append((x, z))

    out = []
    for key in sorted(buckets):
        cells = buckets[key]
        mix = (key[0] * 73856093) ^ (key[1] * 19349663) ^ seed
        out.append(cells[(mix & 0x7FFFFFFF) % len(cells)])
    return out


# The name three buildings called it, kept because it says where the jitter
# comes from. One function, so the two names cannot drift apart.
hash_scatter = scatter


def palm(
    canvas: Canvas,
    at: tuple[int, int],
    *,
    base: int,
    height: int,
    trunk: str,
    frond: str,
    radius: float = 2.0,
    crown: int = 1,
    shape: str = "round",
) -> Mask:
    """One tree: a trunk with a head on it, standing at `at`.

    Returns trunk and head together, and that is not a convenience. A head with
    no trunk under it is what `checks.floating` calls adrift and a trunk with no
    head is a post, so a caller that declares them separately has two parts that
    can each pass while the tree is broken.

    The head narrows by a metre a course, which is what makes it read as a palm
    rather than a mushroom at one block to the metre, and it is capped on the
    axis so the trunk does not end in open air.

    `shape` is "round" for a disc and "diamond" for the taxicab version, which is
    a metre narrower on the diagonals and stops a close-planted row from closing
    into a green slab at altitude.
    """
    if crown < 1:
        raise ValueError("a tree has at least one course of head")
    if blocklib.base(frond).endswith("_leaves") \
            and "persistent=true" not in frond:
        raise SystemExit(
            f"{frond} has no persistent=true, so Minecraft will destroy every "
            "leaf more than six blocks from a log -- which is most of a palm's "
            "head -- a couple of minutes after the schematic is pasted. Write "
            f'"{blocklib.base(frond)}[persistent=true]".')

    x, z = at
    if not (0 <= x < canvas.width and 0 <= z < canvas.length):
        return Mask(canvas.width, canvas.length)

    stem = Mask(canvas.width, canvas.length)
    stem.set(x, z)
    top = base + height
    canvas.fill(stem, base, top, trunk)

    placed = stem.copy()
    for course in range(crown):
        reach = radius - course
        if reach <= 0:
            break
        head = Mask(canvas.width, canvas.length)
        span = int(reach)
        for dz in range(-span, span + 1):
            for dx in range(-span, span + 1):
                near = (abs(dx) + abs(dz) if shape == "diamond"
                        else math.hypot(dx, dz))
                if near > reach:
                    continue
                nx, nz = x + dx, z + dz
                if 0 <= nx < canvas.width and 0 <= nz < canvas.length:
                    head.set(nx, nz)
        canvas.fill(head - stem, top - 1 + course, top + course, frond)
        placed = placed | head

    # The bud, so the trunk does not end in daylight.
    canvas.fill(stem, top + crown - 1, top + crown, frond)
    return placed


def openings(
    footprint: Mask,
    facade: Facade,
    period: float,
    width: float,
    thickness: float = 1.0,
    phase: float = 0.0,
) -> Mask:
    """The cells a rhythm of window bays occupies in a wall ring.

    Separate from `windows` so that a build can declare the bays to its schedule
    without recomputing them from the same four numbers. Two expressions of the
    same geometry drift apart, and the one in the declaration drifting is the
    worse half: the manifest then grades a part that is not the part that was
    built.
    """
    return footprint.outline(thickness) & facade.bays(period, width, phase)


def windows(
    canvas: Canvas,
    footprint: Mask,
    facade: Facade,
    y0: int,
    y1: int,
    period: float,
    width: float,
    thickness: float = 1.0,
    phase: float = 0.0,
    block: str | None = None,
) -> int:
    """Punch (or glaze) a rhythm of openings through a wall ring."""
    bays = openings(footprint, facade, period, width, thickness, phase)
    if block is None:
        return canvas.carve(bays, y0, y1)
    return canvas.fill(bays, y0, y1, block)


def recess(
    canvas: Canvas,
    footprint: Mask,
    facade: Facade,
    y0: int,
    y1: int,
    period: float,
    width: float,
    depth: float = 1.0,
    storey: int = 3,
    thickness: float = 1.0,
    phase: float = 0.0,
    floor: str | None = None,
    rail: str | None = None,
) -> Mask:
    """Balconies cut into the wall plane instead of hung off it.

    A balcony built as a shelf reads well and grades badly: the reference holds
    the facade at one line, the build stands a slab a metre and a half proud of
    it, and every station the shelf touches fails the section by the depth of
    the shelf. Cut inward and the measured line survives, the rhythm still reads
    from every camera the review uses, and the loggia is a place rather than a
    ledge.

    Cut to `depth` behind the outer face, floored once per `storey` and railed
    at the opening. Returns the cells it took, for the schedule.

    The exception worth knowing: a balcony the capture itself holds as an
    overhang is a measured fact, and is built where it was measured.
    """
    bays = openings(footprint, facade, period, width, thickness + depth, phase)
    canvas.carve(bays, y0, y1)
    if floor:
        for y in range(y0, y1, max(1, storey)):
            canvas.fill(bays, y, y + 1, floor)
    if rail:
        lip = bays & footprint.outline(thickness)
        for y in range(y0, y1, max(1, storey)):
            canvas.fill(lip, y + 1, y + 2, rail)
    return bays
