"""The road network of a flat map, as a graph.

`flatmap` reads one building out of a crop. This reads the other thing a map
draws well: the streets between the buildings. It is the same authority rule --
the map is authoritative for position, size and rotation and for nothing else --
applied to a different object, so the same three facts come back per road:

    where it runs      the centreline, one cell per metre, in order
    how wide it is     the half-width at every point of that centreline
    which way it runs  the tangent, and with it the normal that markings sit on

Everything a builder wants to draw on a road -- edge lines, lane lines, a centre
line, a crossing, a stop bar -- is an offset along that normal at an arc length
along that centreline. So the whole job is to turn a stroke of dark pixels into
an ordered polyline with a radius, and then the drawing is arithmetic.

**The stroke is the carriageway.** A map draws a street as one stroke and the
pavement beside it as part of the block, so the mask is where the tarmac goes and
the kerb belongs just outside it. Nothing here widens the stroke to taste: a road
that comes out too narrow is a map that drew it narrow, and the place to argue
about that is the map.

**Which greys mean road is a property of the map, not of this module** -- the
same rule `flatmap` states for its own palette. `Roads` carries the two numbers
and the building's `probes/derive.py` states them, because the file does not.

Four stages, in this order:

    mask        the dark grey strokes, from the PNG
    dist        a chamfer distance transform inside them -- the half-width field
    skeleton    Zhang-Suen thinning down to a one-cell-wide medial line
    graph       that line cut at its junctions into ordered paths

The distance transform is chamfer 5-7 rather than Chebyshev. Chebyshev is one
call to a box filter and would be far quicker, and it reports a road crossing the
grid at 45 degrees as 30 per cent narrower than it is -- which on this map is
half the network, because the street grid is not aligned to north.
"""

from __future__ import annotations

from math import hypot
from pathlib import Path as _FsPath

# One orthogonal step and one diagonal step, in fifths of a block. 5-7 is the
# best pair a 3x3 neighbourhood admits: within about 2 per cent of Euclidean,
# where 1-1 (Chebyshev) is out by 30 per cent on the diagonal.
STEP = 5
DIAG = 7

# P2..P9 of the Zhang-Suen paper: north, then clockwise.
_RING = ((0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1))


class Roads:
    """Which greys of one map are road, and what counts as water beside it.

    `darker_than` is exclusive and is the whole classification: on a drawn map
    the carriageway is the darkest grey there is, and everything above the
    threshold -- building fill, building outline, the light grey of a block --
    is not road. `grey` is the same channel spread `flatmap.Palette` uses, and it
    is what keeps deep water out: water is dark and blue, roads are dark and
    colourless.
    """

    __slots__ = ("grey", "darker_than", "tint")

    def __init__(self, grey: int = 6, darker_than: int = 121, tint: int = 10):
        self.grey = grey
        self.darker_than = darker_than
        self.tint = tint

    def __repr__(self) -> str:
        return f"<roads grey<={self.grey}, r<{self.darker_than}>"

    def as_dict(self) -> dict:
        return {"grey": self.grey, "darker_than": self.darker_than,
                "tint": self.tint}


DEFAULT = Roads()


class Raster:
    """The two per-cell fields: what is road, and how far inside it each cell is.

    `dist` is in chamfer units, so `dist[i] / STEP` is a distance in blocks and a
    cell one step in from the edge holds 5. Cells outside the road hold 0, which
    makes the field its own mask and saves carrying a second test everywhere.
    """

    __slots__ = ("width", "length", "road", "water", "dist", "cells")

    def __init__(self, width, length, road, water, dist, cells):
        self.width = width
        self.length = length
        self.road = road
        self.water = water
        self.dist = dist
        self.cells = cells

    def __repr__(self) -> str:
        return (f"<raster {self.width}x{self.length}, {len(self.cells)} road "
                f"cells, widest {2 * max(self.dist) / STEP:.1f} blocks>")


class Path:
    """One run of road between two junctions, or out to a dead end.

    `cells` is in order along the road, one cell per step, eight-connected.
    `radius` is the half-width in chamfer units at each of those cells, and
    `arc` the distance travelled to reach it, in blocks -- so a dash pattern is
    a test on `arc` and a lane line is an offset on the normal.
    """

    __slots__ = ("cells", "radius", "arc", "ends")

    def __init__(self, cells, radius, ends=(None, None)):
        self.cells = cells
        self.radius = radius
        self.ends = ends
        arc = [0.0]
        for (x0, z0), (x1, z1) in zip(cells, cells[1:]):
            arc.append(arc[-1] + (1.0 if x0 == x1 or z0 == z1 else 1.4142135624))
        self.arc = arc

    def __len__(self) -> int:
        return len(self.cells)

    @property
    def length(self) -> float:
        return self.arc[-1]

    @property
    def half_width(self) -> float:
        """The half-width this road is drawn at, in blocks.

        Measured over the middle of the run and not over all of it. A path
        begins and ends in the mouth of a junction, and a junction is wider than
        either road that makes it -- so the ends are the two places along a road
        where the road is not its own width. On the short segments of a dense
        grid they are most of it.

        The median of what is left, and not the mean, for the same reason once
        more: whatever the trim did not catch is at an end and is high.
        """
        n = len(self.radius)
        cut = min(n // 5, (n - 1) // 2)
        middle = sorted(self.radius[cut:n - cut] or self.radius)
        return middle[len(middle) // 2] / STEP

    def tangent(self, i: int, span: int = 5) -> tuple[float, float]:
        """The unit direction of travel at cell `i`, over a window of cells.

        Over a window because a single step of an eight-connected line is one of
        eight directions, and a marking laid out on a 45-degree quantum of the
        real bearing wanders visibly on a long straight.
        """
        a = max(0, i - span)
        b = min(len(self.cells) - 1, i + span)
        dx = self.cells[b][0] - self.cells[a][0]
        dz = self.cells[b][1] - self.cells[a][1]
        n = hypot(dx, dz)
        if not n:
            return (1.0, 0.0)
        return (dx / n, dz / n)

    def normal(self, i: int, span: int = 5) -> tuple[float, float]:
        """The unit right-hand side of travel: +x turned towards +z."""
        tx, tz = self.tangent(i, span)
        return (-tz, tx)


class Junction:
    """Where three or more roads meet, and how far its influence reaches.

    `radius` is the largest half-width in the cluster, which is what a junction's
    reach actually is: a crossing of two boulevards has a wide mouth and a
    crossing of two lanes a narrow one, and both want their markings held back by
    their own size rather than by one typed number.
    """

    __slots__ = ("cells", "x", "z", "radius", "degree")

    def __init__(self, cells, radius, degree):
        self.cells = cells
        self.x = sum(c[0] for c in cells) / len(cells)
        self.z = sum(c[1] for c in cells) / len(cells)
        self.radius = radius
        self.degree = degree

    def __repr__(self) -> str:
        return (f"<junction ({self.x:.0f}, {self.z:.0f}) degree {self.degree}, "
                f"reach {self.radius:.1f}>")


class Network:
    __slots__ = ("raster", "skeleton", "paths", "junctions")

    def __init__(self, raster, skeleton, paths, junctions):
        self.raster = raster
        self.skeleton = skeleton
        self.paths = paths
        self.junctions = junctions

    def __repr__(self) -> str:
        return (f"<network {len(self.paths)} paths, "
                f"{sum(p.length for p in self.paths):.0f} blocks, "
                f"{len(self.junctions)} junctions>")


# -- stage 1: the mask ------------------------------------------------------


def raster(png: str | _FsPath, palette: Roads | None = None) -> Raster:
    """The road cells of a map crop, and the half-width field inside them.

    Read through PIL's band arithmetic rather than a per-pixel loop. The
    predicate is the same one `flatmap.road` states -- achromatic within `grey`,
    darker than `darker_than` -- and `max(|r-g|, |g-b|) <= grey` is exactly
    "both differences within grey", so the two agree cell for cell. A whole
    island is forty times the area of a building crop and the loop version of
    this alone took longer than the rest of the pipeline put together.
    """
    from PIL import Image, ImageChops

    palette = palette or DEFAULT
    src = Image.open(png).convert("RGB")
    w, h = src.size
    r, g, b = src.split()

    spread = ImageChops.lighter(ImageChops.difference(r, g),
                                ImageChops.difference(g, b))
    grey = spread.point(lambda v: 255 if v <= palette.grey else 0)
    dark = r.point(lambda v: 255 if v < palette.darker_than else 0)
    road = bytearray(
        ImageChops.multiply(grey, dark).point(lambda v: 1 if v else 0).tobytes()
    )

    # Water is dark and blue where road is dark and colourless; the kerb rule
    # needs to tell a causeway from a street and this is the only signal on the
    # map that does.
    blue = ImageChops.subtract(b, r).point(
        lambda v: 1 if v > palette.tint else 0)
    water = bytearray(blue.tobytes())

    cells = _indices(road)
    return Raster(w, h, road, water, _chamfer(road, cells, w, h), cells)


def _indices(bits: bytearray) -> list[int]:
    """Every set index, ascending.

    `bytearray.index` scans in C and this is called on ten million cells, so the
    only Python-level work is one call per cell actually found.
    """
    out = []
    at = 0
    try:
        while True:
            at = bits.index(1, at)
            out.append(at)
            at += 1
    except ValueError:
        return out


# -- stage 2: the half-width field ------------------------------------------


def _chamfer(road: bytearray, cells: list[int], w: int, h: int) -> list[int]:
    """Distance from each road cell to the nearest cell that is not road.

    Two raster sweeps, forward then backward, over the road cells only. Cells
    outside the road stay 0 and are read as the background they are, so the
    sweeps never have to test whether a neighbour is in the mask.

    A cell on the border of the crop is measured as if the road ended there. The
    causeway runs off the west edge and this makes its last few metres read
    narrow; nothing downstream cares, because a road that leaves the map has no
    junction to mark and no end to hold markings back from.
    """
    big = 1 << 20
    d = [0] * (w * h)
    for i in cells:
        d[i] = big
    last_row = w * (h - 1)
    for i in cells:
        x = i % w
        v = d[i]
        if x:
            t = d[i - 1] + STEP
            if t < v:
                v = t
        if i >= w:
            t = d[i - w] + STEP
            if t < v:
                v = t
            if x:
                t = d[i - w - 1] + DIAG
                if t < v:
                    v = t
            if x < w - 1:
                t = d[i - w + 1] + DIAG
                if t < v:
                    v = t
        d[i] = v
    for i in reversed(cells):
        x = i % w
        v = d[i]
        if x < w - 1:
            t = d[i + 1] + STEP
            if t < v:
                v = t
        if i < last_row:
            t = d[i + w] + STEP
            if t < v:
                v = t
            if x:
                t = d[i + w - 1] + DIAG
                if t < v:
                    v = t
            if x < w - 1:
                t = d[i + w + 1] + DIAG
                if t < v:
                    v = t
        d[i] = v
    return d


# -- stage 3: the medial line -----------------------------------------------


def thin(road: bytearray, cells: list[int], w: int, h: int) -> bytearray:
    """Zhang-Suen thinning: the road, eroded to a line one cell wide.

    The published algorithm rescans the whole image on every pass, which here is
    ten million cells times thirty passes. It does not have to: a cell with all
    eight neighbours set can never be deleted, so only the current border is ever
    a candidate, and after a pass the only new candidates are the survivors and
    the neighbours of what was deleted. That turns the total work into roughly
    one visit per cell over the whole run.
    """
    sk = bytearray(road)
    size = w * h

    def ring(i, x):
        """The eight neighbours in P2..P9 order, off-grid counting as clear."""
        out = []
        for dx, dz in _RING:
            nx = x + dx
            j = i + dz * w + dx
            out.append(sk[j] if 0 <= nx < w and 0 <= j < size else 0)
        return out

    active = set()
    for i in cells:
        x = i % w
        if 0 in ring(i, x):
            active.add(i)

    while active:
        removed = False
        for step in (0, 1):
            doomed = []
            for i in active:
                if not sk[i]:
                    continue
                p = ring(i, i % w)
                filled = sum(p)
                if filled < 2 or filled > 6:
                    continue
                crossings = 0
                for k in range(8):
                    if not p[k] and p[(k + 1) % 8]:
                        crossings += 1
                if crossings != 1:
                    continue
                if step == 0:
                    if p[0] * p[2] * p[4] or p[2] * p[4] * p[6]:
                        continue
                elif p[0] * p[2] * p[6] or p[0] * p[4] * p[6]:
                    continue
                doomed.append(i)
            if not doomed:
                continue
            removed = True
            for i in doomed:
                sk[i] = 0
            nxt = {i for i in active if sk[i]}
            for i in doomed:
                x = i % w
                for dx, dz in _RING:
                    nx = x + dx
                    j = i + dz * w + dx
                    if 0 <= nx < w and 0 <= j < size and sk[j]:
                        nxt.add(j)
            active = nxt
        if not removed:
            break
    return sk


# -- stage 4: the graph -----------------------------------------------------


def _neighbours(sk, i, w, size):
    x = i % w
    out = []
    for dx, dz in _RING:
        nx = x + dx
        j = i + dz * w + dx
        if 0 <= nx < w and 0 <= j < size and sk[j]:
            out.append(j)
    return out


def _trace(sk, w, size):
    """Cut the skeleton at its nodes, where a node is a *cluster* of cells.

    A cell with two neighbours continues a road; anything else -- a dead end, a
    crossing -- is a node. Cutting at individual node cells is the obvious thing
    and it is wrong here, because a thinned crossing is not one cell with four
    neighbours. It is a knot of five or ten cells that each have three, and every
    link inside that knot then comes back as a one-metre road: on this island
    that turned 350 streets into 8 800, of which 8 200 were a metre long.

    So adjacent node cells are merged first, and a path is a chain between two
    *clusters*. Rings with no node at all -- a roundabout, a loop road -- have
    nothing to cut at, so they are broken open at an arbitrary cell afterwards.

    Returns the runs, the degree of every cell, which cluster each node cell
    belongs to, and the clusters themselves.
    """
    cells = _indices(sk)
    degree = {i: len(_neighbours(sk, i, w, size)) for i in cells}

    owner: dict[int, int] = {}
    clusters: list[list[int]] = []
    for i in cells:
        if degree[i] == 2 or i in owner:
            continue
        gid = len(clusters)
        group = [i]
        owner[i] = gid
        stack = [i]
        while stack:
            c = stack.pop()
            for j in _neighbours(sk, c, w, size):
                if j in owner or degree[j] == 2:
                    continue
                owner[j] = gid
                group.append(j)
                stack.append(j)
        clusters.append(group)

    walked = set()
    runs = []
    for gid, group in enumerate(clusters):
        for start in group:
            for first in _neighbours(sk, start, w, size):
                if owner.get(first) == gid or (start, first) in walked:
                    continue
                run = [start, first]
                walked.add((start, first))
                walked.add((first, start))
                prev, here = start, first
                while here not in owner:
                    nxt = [j for j in _neighbours(sk, here, w, size) if j != prev]
                    if not nxt:
                        break
                    prev, here = here, nxt[0]
                    walked.add((prev, here))
                    walked.add((here, prev))
                    run.append(here)
                runs.append(run)

    seen = set()
    for run in runs:
        seen.update(run)
    for i in cells:
        if i in seen:
            continue
        run = [i]
        seen.add(i)
        prev, here = None, i
        while True:
            nxt = [j for j in _neighbours(sk, here, w, size)
                   if j != prev and j not in seen]
            if not nxt:
                break
            prev, here = here, nxt[0]
            seen.add(here)
            run.append(here)
        if len(run) > 2:
            runs.append(run)
    return runs, degree, owner, clusters


def _prune(sk, w, size, dist) -> bool:
    """Delete the stubs thinning leaves in the corners of a wide junction.

    Where four roads meet, the mask is a square blob and its medial axis is a
    star with four short arms poking into the corners. They are artefacts of the
    shape of the crossing and not roads, and left in they become four extra
    junction arms, each of which would be given a crossing and a stop line.

    A stub is short *for the road it hangs off*: the arm of a boulevard junction
    is longer than a whole alley. So the threshold is the half-width at the free
    end, and the one real thing it can eat -- a cul-de-sac shorter than about one
    and a half road widths -- is not something this map draws.

    Returns whether anything was deleted, so the caller can settle.
    """
    runs, degree, owner, _ = _trace(sk, w, size)
    cut = False
    for run in runs:
        free = [end for end in (run[0], run[-1]) if degree.get(end) == 1]
        if len(free) != 1 or len(run) < 2:
            continue
        tip = free[0]
        limit = max(6.0, 1.6 * dist[tip] / STEP)
        if len(run) >= limit:
            continue
        for i in run:
            if degree.get(i, 0) <= 2:
                sk[i] = 0
        cut = True
    return cut


def graph(sk: bytearray, dist: list[int], w: int, h: int):
    """The pruned skeleton as ordered paths, plus the junctions between them.

    A junction's degree is how many paths actually reach it, counted here rather
    than inferred from the neighbours of its cells: after clustering, the cell
    count says how knotted the thinning left the crossing and not how many
    streets meet at it.
    """
    size = w * h
    for _ in range(4):
        if not _prune(sk, w, size, dist):
            break

    runs, degree, owner, clusters = _trace(sk, w, size)

    arms = [0] * len(clusters)
    kept = []
    for run in runs:
        if len(run) < 2:
            continue
        for end in (run[0], run[-1]):
            gid = owner.get(end)
            if gid is not None:
                arms[gid] += 1
        kept.append(run)

    junctions = []
    number = {}
    for gid, group in enumerate(clusters):
        if arms[gid] < 3:
            continue
        number[gid] = len(junctions)
        radius = max(dist[i] for i in group) / STEP
        junctions.append(
            Junction([(i % w, i // w) for i in group], radius, arms[gid])
        )

    # Which junction each end runs into, so that a builder can hold its markings
    # back by the size of the crossing it is approaching rather than by a typed
    # number. `None` is an end that meets no junction: a dead end, or the edge of
    # the crop where the causeway leaves the map.
    paths = [
        Path(
            [(i % w, i // w) for i in run],
            [dist[i] for i in run],
            ends=(number.get(owner.get(run[0])), number.get(owner.get(run[-1]))),
        )
        for run in kept
    ]
    return paths, junctions


def read(png: str | _FsPath, palette: Roads | None = None) -> Network:
    """A map crop, all the way to a graph of streets."""
    grid = raster(png, palette)
    sk = thin(grid.road, grid.cells, grid.width, grid.length)
    paths, junctions = graph(sk, grid.dist, grid.width, grid.length)
    return Network(grid, sk, paths, junctions)


__all__ = ["DEFAULT", "DIAG", "Junction", "Network", "Path", "Raster", "Roads",
           "STEP", "graph", "raster", "read", "thin"]
