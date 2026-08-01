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

from .blocks import base, unknown_blocks

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
