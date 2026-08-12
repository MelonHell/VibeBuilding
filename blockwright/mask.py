"""2D block masks over a schematic's XZ grid.

A footprint is drawn as clean shapes in the building's own frame and rasterised
through `Frame.region`, which is where the 52-degree staircase comes from. Once it
is a mask, everything derived from it -- wall rings, setbacks, cores -- is a
morphological operation, so no primitive downstream has to know about the angle.

Offsets use a true Euclidean distance transform rather than a chamfer
approximation, because a setback of 3 has to mean 3 metres in every direction,
including along a 52-degree facade where a Chebyshev offset would be out by 40%.

Watertightness is the one place a metric offset is not enough on its own. No
1-block band along a 52-degree wall can hold water -- the geometry needs
|cos t| + |sin t| = 1.41 blocks -- so `outline` adds the rim explicitly rather
than trusting a Euclidean band to close.
"""

from __future__ import annotations

import math
from pathlib import Path

from . import fast
from .measure import staircase

INF = 1e12

# Clockwise from west. Boundary tracing needs a fixed cyclic order of the eight
# neighbours so "the next one round" is well defined.
_MOORE = [(-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1)]

# The large primes are `build.scatter`'s, so the two places in the library that
# turn a position into a number do it the same way. The avalanche step after
# them is what `scatter` does not need and this does: scatter reduces its hash
# modulo a bucket's cell count, where this compares the low bits against a
# threshold, and the raw XOR of two multiples is far too orderly down there --
# without a mix, neighbouring cells share most of their low bits and the coin
# comes out combed rather than random.
def _coin(x: int, z: int, seed: int = 0) -> int:
    """A stable 16-bit number for a cell. Same cell, same answer, every run."""
    h = (x * 73856093) ^ (z * 19349663) ^ (seed * 83492791)
    h &= 0xFFFFFFFF
    h = ((h ^ (h >> 15)) * 2246822519) & 0xFFFFFFFF
    h = ((h ^ (h >> 13)) * 3266489917) & 0xFFFFFFFF
    return (h ^ (h >> 16)) & 0xFFFF


def _edt_1d(f: list[float], out: list[float]) -> None:
    """Exact squared distance transform of a 1D sampled function.

    Felzenszwalb & Huttenlocher: the lower envelope of the parabolas
    (q - v)^2 + f[v] is found in one forward scan, then sampled in one more.
    """
    n = len(f)
    v = [0] * n
    z = [0.0] * (n + 1)
    k = 0
    z[0] = -INF
    z[1] = INF
    for q in range(1, n):
        while True:
            s = ((f[q] + q * q) - (f[v[k]] + v[k] * v[k])) / (2 * q - 2 * v[k])
            if s > z[k]:
                break
            k -= 1
        k += 1
        v[k] = q
        z[k] = s
        z[k + 1] = INF
    k = 0
    for q in range(n):
        while z[k + 1] < q:
            k += 1
        d = q - v[k]
        out[q] = d * d + f[v[k]]


class Edge:
    """One face of a shape, as the line its own stations fitted.

    The fifth idiom for reading a drawn part, and the one the other four keep
    almost being. `Site.footprint` keeps every wobble; `box` and `squared` hand
    back a rectangle, which is the right answer only where all four faces run
    along u or v; `straighten` keeps whatever corners the trace had, chamfers
    and all. None of them can say **"this face is a straight line, here is its
    equation, and here is how well it fits"** -- and that sentence is what a
    raked end, a splayed wing or a plot boundary needs, because what carries the
    measurement there is the *direction* of the face, which no extent records.

    The equation is `a*u + b*v = c` in the building's frame, with `(a, b)` a
    unit normal pointing **out** of the shape. So `side` is a signed distance in
    metres, negative inside, and a part is the place where every one of its
    faces reads negative -- which is how a build draws it: `half_planes` turns a
    list of these into the predicate `Frame.region` wants, and the corners come
    out of the intersections rather than out of the trace, straight by
    construction.

    Nothing about the fit is hidden. `spread` is the RMS distance of the
    stations from the line, `worst` the furthest single one, `share` is how many
    of the stations the face spans held a point that survived, and `absent` and
    `outliers` say which way the rest went. A bowed face, a face read across a
    neighbour and a face that is really two come back with numbers that say so;
    whether to draw one anyway is the caller's business, and `Mask.edges` never
    decides it silently.

    `spread` and `worst` are both here because a bow shows only in the second.
    An arc's stations all sit on one side of its chord, so a fifteen-metre face
    across a sixteen-metre radius misses by two metres in the middle and still
    averages well under one; read on the RMS alone a disc is a good heptagon.
    """

    __slots__ = ("a", "b", "c", "ends", "run", "span", "kept", "absent",
                 "outliers", "spread", "worst", "share")

    def __init__(self, a: float, b: float, c: float, ends, run: float,
                 span: int, kept: int, absent: int, outliers: int,
                 spread: float, worst: float, share: float):
        self.a, self.b, self.c = a, b, c
        self.ends = ends            # the two points the fitted line runs between
        self.run = run              # length of that run, in metres
        self.span = span            # stations the face covers, end to end
        self.kept = kept            # stations that held a point and were fitted
        self.absent = absent        # stations with nothing on them at all
        self.outliers = outliers    # stations thrown out before the final fit
        self.spread = spread        # RMS of the kept stations off the line, m
        self.worst = worst          # furthest kept station off the line, m
        self.share = share          # kept / span

    def side(self, u: float, v: float) -> float:
        """Signed distance from this face, in metres. Negative is inside."""
        return self.a * u + self.b * v - self.c

    def holds(self, u: float, v: float, pad: float = 0.0) -> bool:
        """Whether (u, v) is on the inside of this face, `pad` metres out."""
        return self.side(u, v) <= pad

    def moved(self, distance: float) -> "Edge":
        """The same line pushed `distance` metres outward."""
        return Edge(self.a, self.b, self.c + distance, self.ends, self.run,
                    self.span, self.kept, self.absent, self.outliers,
                    self.spread, self.worst, self.share)

    @property
    def angle(self) -> float:
        """Bearing of the face within the frame, in degrees, folded to [0, 180)."""
        return math.degrees(math.atan2(-self.a, self.b)) % 180.0

    def meets(self, other: "Edge") -> tuple[float, float] | None:
        """Where this face's line crosses another's. `None` if they are parallel."""
        det = self.a * other.b - other.a * self.b
        if abs(det) < 1e-9:
            return None
        return ((self.c * other.b - other.c * self.b) / det,
                (self.a * other.c - other.a * self.c) / det)

    def __repr__(self) -> str:
        (u0, v0), (u1, v1) = self.ends
        return (f"<edge ({u0:.1f}, {v0:.1f})..({u1:.1f}, {v1:.1f}) "
                f"at {self.angle:.1f} deg, {self.kept}/{self.span} stations, "
                f"spread {self.spread:.2f} m, worst {self.worst:.2f} m>")


class Mask:
    """A width x length grid of bits, indexed the same way as a schematic layer."""

    __slots__ = ("width", "length", "bits")

    def __init__(self, width: int, length: int, bits: bytearray | None = None):
        self.width = width
        self.length = length
        self.bits = bits if bits is not None else bytearray(width * length)

    # -- construction -----------------------------------------------------

    @classmethod
    def from_schematic(cls, schematic, blocks, y: int = 0) -> "Mask":
        """Cells of layer `y` whose block is in `blocks`."""
        wanted = {
            schematic.palette.index(b) for b in blocks if b in schematic.palette
        }
        m = cls(schematic.width, schematic.length)
        base = y * schematic.length * schematic.width
        area = schematic.width * schematic.length
        layer = schematic.blocks[base : base + area]
        for i, v in enumerate(layer):
            if v in wanted:
                m.bits[i] = 1
        return m

    def copy(self) -> "Mask":
        return Mask(self.width, self.length, bytearray(self.bits))

    def empty_like(self) -> "Mask":
        return Mask(self.width, self.length)

    @staticmethod
    def union(masks, width: int = 0, length: int = 0) -> "Mask":
        """Every mask in a sequence, merged.

        The size arguments are what makes this worth having over `reduce`: a
        filtered list of components can come back empty, and an empty union
        still has to be a mask of the right shape to keep composing.
        """
        masks = list(masks)
        if not masks:
            return Mask(width, length)
        out = masks[0].copy()
        for m in masks[1:]:
            for i, v in enumerate(m.bits):
                if v:
                    out.bits[i] = 1
        return out

    # -- access -----------------------------------------------------------

    def index(self, x: int, z: int) -> int:
        return z * self.width + x

    def get(self, x: int, z: int) -> int:
        if not (0 <= x < self.width and 0 <= z < self.length):
            return 0
        return self.bits[z * self.width + x]

    def set(self, x: int, z: int, value: int = 1) -> None:
        if 0 <= x < self.width and 0 <= z < self.length:
            self.bits[z * self.width + x] = 1 if value else 0

    def __bool__(self) -> bool:
        """Whether anything is set.

        Without this a Mask is always truthy, and `mask.erode(1.0) or mask` --
        the obvious way to write "shrink it, but not to nothing" -- never takes
        its fallback. It reads as a guard and is not one, so the day the erosion
        does empty the mask the caller quietly builds nothing at all.
        """
        return any(self.bits)

    def count(self) -> int:
        return fast.count(self.bits) if fast.HAVE else sum(self.bits)

    def cells(self) -> list[tuple[int, int]]:
        w = self.width
        return [(i % w, i // w) for i, v in enumerate(self.bits) if v]

    def bounds(self) -> tuple[int, int, int, int] | None:
        """(x0, z0, x1, z1), inclusive. None if empty."""
        xs = self.width
        zs = self.length
        x0, z0, x1, z1 = xs, zs, -1, -1
        w = self.width
        for i, v in enumerate(self.bits):
            if not v:
                continue
            x, z = i % w, i // w
            if x < x0:
                x0 = x
            if x > x1:
                x1 = x
            if z < z0:
                z0 = z
            if z > z1:
                z1 = z
        return None if x1 < 0 else (x0, z0, x1, z1)

    # -- carrying one between scripts ---------------------------------------

    def dumps(self) -> str:
        """The mask as one line of text, for `derived.json`.

        A measured shape that is not a rectangle or a disc cannot otherwise
        cross from the probe that measured it to the script that builds it, and
        the two alternatives are both bad: re-measure the reference inside the
        build (which is the split this pipeline exists to keep) or approximate
        the shape by its bounding box (which is how a stepped roof becomes one
        slab again).

        Run lengths, alternating clear and set, one row per `;`. A footprint is
        a handful of runs per row, so a whole roof terrace costs a few hundred
        bytes -- small enough to sit in `derived.json` beside the numbers it
        belongs to.
        """
        rows = []
        w = self.width
        for z in range(self.length):
            row = self.bits[z * w:(z + 1) * w]
            runs: list[int] = []
            want = 0
            run = 0
            for bit in row:
                if (1 if bit else 0) == want:
                    run += 1
                else:
                    runs.append(run)
                    want = 1 - want
                    run = 1
            if want == 1 or runs:
                runs.append(run)
            rows.append(",".join(str(n) for n in runs))
        return f"{w}x{self.length}:" + ";".join(rows)

    @classmethod
    def loads(cls, text: str) -> "Mask":
        """A mask back out of `dumps`."""
        head, _, body = text.partition(":")
        w, _, length = head.partition("x")
        mask = cls(int(w), int(length))
        for z, row in enumerate(body.split(";")):
            if not row:
                continue
            x = 0
            value = 0
            for chunk in row.split(","):
                n = int(chunk)
                if value:
                    for i in range(x, x + n):
                        mask.bits[z * mask.width + i] = 1
                x += n
                value = 1 - value
        return mask

    # -- set algebra ------------------------------------------------------

    def _combine(self, other: "Mask", op, name: str) -> "Mask":
        """Cell by cell, or in one go where numpy is installed.

        Both paths produce identical bytes -- `tools/mask_selftest.py` checks it
        on random masks -- so this is a speed decision and never a behavioural
        one. See `fast.py` for why that distinction is the whole rule.
        """
        if (self.width, self.length) != (other.width, other.length):
            raise ValueError("mask sizes differ")
        if fast.HAVE:
            return Mask(self.width, self.length,
                        fast.combine(self.bits, other.bits, name))
        out = self.empty_like()
        a, b, o = self.bits, other.bits, out.bits
        for i in range(len(a)):
            o[i] = 1 if op(a[i], b[i]) else 0
        return out

    def __and__(self, other: "Mask") -> "Mask":
        return self._combine(other, lambda a, b: a and b, "and")

    def __or__(self, other: "Mask") -> "Mask":
        return self._combine(other, lambda a, b: a or b, "or")

    def __sub__(self, other: "Mask") -> "Mask":
        return self._combine(other, lambda a, b: a and not b, "sub")

    def __invert__(self) -> "Mask":
        if fast.HAVE:
            return Mask(self.width, self.length, fast.invert(self.bits))
        out = self.empty_like()
        for i, v in enumerate(self.bits):
            out.bits[i] = 0 if v else 1
        return out

    def filter(self, predicate) -> "Mask":
        """Keep set cells for which predicate(x, z) is true."""
        out = self.empty_like()
        w = self.width
        for i, v in enumerate(self.bits):
            if v and predicate(i % w, i // w):
                out.bits[i] = 1
        return out

    def speckle(self, share: float = 0.5, seed: int = 0) -> "Mask":
        """A deterministic `share` of the set cells, picked cell by cell.

        The thing five call sites across two buildings wrote by hand, all five
        the same way and all five wrong: `(x * 7 + z * 3) % 11 < 4`. A modulus of
        a small linear combination is a lattice, not a coin. That one repeats
        along the diagonal 82% of the time -- so what it lays down is diagonal
        stripes, and on a planting bed at one block to the metre the stripes are
        the first thing a render shows. A coin repeats along the diagonal 50% of
        the time, which is to say it has no diagonal at all.

        Whether that matters is not a matter of taste here. The one piece of
        texture that was measured rather than recommended -- three fifths of
        every dithered surface on a 50-million-block city -- came out as an
        honest fifty-fifty coin per cell: no checkerboard, no stripes, no
        clumps, median run of two, which is simply what a coin does. See
        `docs/gta5-style-findings.md`. A lattice reproduces none of that.

        Hashed on position and not drawn from a sequence, for two reasons that
        both carry weight. The render loop proves a fix landed by showing a view
        moved -- `tools/review_diff.py` answers it mechanically -- and a
        sequential generator would dirty every view on every run. And a mask
        that grows by one cell when the map is redrawn shifts a positional hash
        by one cell, where a sequential one reshuffles the whole surface.

        `share` is the fraction kept, so 0.5 is the measured recipe and anything
        else is a decision the caller is making on purpose.
        """
        if not 0.0 <= share <= 1.0:
            raise ValueError(f"a share is between 0 and 1, not {share}")
        out = self.empty_like()
        w = self.width
        cut = int(share * 0x10000)
        for i, v in enumerate(self.bits):
            if v and _coin(i % w, i // w, seed) < cut:
                out.bits[i] = 1
        return out

    # -- morphology -------------------------------------------------------

    def distance_field(self, inside: bool = True) -> list[float]:
        """Squared Euclidean distance from every cell to the nearest cell of the
        opposite value. `inside=True` measures set cells to the nearest clear
        cell; `inside=False` measures clear cells to the nearest set cell.

        Cells outside the grid count as clear, so a footprint touching the border
        is treated as ending there rather than continuing forever.
        """
        w, h = self.width, self.length
        seed = 1 if inside else 0
        f = [INF if v == seed else 0.0 for v in self.bits]
        buf = [0.0] * max(w, h)
        col_in = [0.0] * h
        col_out = [0.0] * h
        for x in range(w):
            for z in range(h):
                col_in[z] = f[z * w + x]
            _edt_1d(col_in, col_out)
            for z in range(h):
                f[z * w + x] = col_out[z]
        row_out = buf[:w]
        for z in range(h):
            base = z * w
            _edt_1d(f[base : base + w], row_out)
            f[base : base + w] = row_out
        return f

    def erode(self, radius: float) -> "Mask":
        """Shrink by `radius` blocks, measured Euclidean."""
        if radius <= 0:
            return self.copy()
        limit = radius * radius
        field = self.distance_field(inside=True)
        out = self.empty_like()
        for i, v in enumerate(self.bits):
            if v and field[i] > limit:
                out.bits[i] = 1
        return out

    def dilate(self, radius: float) -> "Mask":
        """Grow by `radius` blocks, measured Euclidean."""
        if radius <= 0:
            return self.copy()
        limit = radius * radius
        field = self.distance_field(inside=False)
        out = self.copy()
        for i, v in enumerate(self.bits):
            if not v and field[i] <= limit:
                out.bits[i] = 1
        return out

    def rim(self) -> "Mask":
        """Set cells with any of the eight neighbours clear.

        The thinnest ring that still holds water: an interior cell has all eight
        neighbours set, so no path -- 4-connected or diagonal -- reaches it from
        outside without crossing the rim.
        """
        out = self.empty_like()
        w, h = self.width, self.length
        for i, v in enumerate(self.bits):
            if not v:
                continue
            x, z = i % w, i // w
            for dx, dz in _MOORE:
                if not self.get(x + dx, z + dz):
                    out.bits[i] = 1
                    break
        return out

    def close(self, radius: float) -> "Mask":
        """Grown then shrunk: gaps narrower than twice `radius` are filled in.

        What it is for is a site rather than a wall -- two wings across a court
        are one building on one plot, and a pad that follows each of them
        separately leaves them connected by nothing.

        It does not return the original outline exactly, and that is a property
        of the pair rather than an accident: `dilate` takes cells at exactly
        `radius` and `erode` keeps only cells past it, so a straight edge comes
        back half a block proud. On a podium that is the tolerance you wanted;
        anywhere it is not, do not use a closing to get an outline back.
        """
        return self.dilate(radius).erode(radius)

    def open(self, radius: float) -> "Mask":
        """Shrunk then grown: spurs narrower than twice `radius` are cut off.

        The other half of the pair, and the one that answers a drawn edge. A
        closing fills notches; an opening removes spikes. A traced map edge has
        both, and a shape that has been through neither shows every one of them
        at a block to the metre.

        Use it on anything whose thin parts are noise rather than architecture:
        the tail a flood fill leaves where two surfaces touch, the single-cell
        whisker a decomposition leaves at a corner. Do not use it on a building
        that has a genuinely thin part -- a colonnade, a bridge, a fin -- because
        it cannot tell that part from a whisker. `frame.staircase` is the width
        below which nothing can be drawn continuously anyway, and it is the
        floor for any radius chosen here.
        """
        return self.erode(radius).dilate(radius)

    def round(self, radius: float) -> "Mask":
        """Both corners taken off: notches filled, then spikes cut.

        A closing followed by an opening. Concave corners come back at `radius`,
        convex ones come back at `radius`, and a straight edge comes back
        straight -- which is the whole reason to run both rather than one.
        Running only a closing leaves the spikes and reads as a blob with teeth;
        running only an opening leaves the notches and reads as a comb.

        This is a **drawing** operation and it moves the outline. It is not a
        way to recover an outline, and it is not the tool for a shape whose own
        wobble carries a measurement -- for a drawn edge that should be straight,
        `straighten` keeps the corners the building has and drops only the noise
        between them, which is almost always what was wanted. Reach for a
        rounding when the corners themselves should be soft: a pool, a pond, a
        lawn, a bed of planting, the head of a plaza.

        Order matters and was measured rather than reasoned. Opening first: a
        closing thickens the root of a spike before the opening can reach it, so
        a six-cell whisker on a test block survives a closing-then-opening at
        three cells and does not survive an opening-then-closing at all. The
        notch fills either way, but not at the same radius: the opening widens
        a notch slightly before the closing sees it, so a radius only just over
        half the notch stops bridging it. Ask for a little more than the
        arithmetic suggests.
        """
        return self.open(radius).close(radius)

    def outline(self, thickness: float = 1.0) -> "Mask":
        """The inward ring of the given thickness -- a watertight wall.

        A Euclidean band on its own is not watertight below 1.41 blocks. At the
        inner corner of every step of a 52-degree staircase the nearest clear
        cell is the diagonal one, so a 1-block band skips that corner and leaves
        a pinhole -- about a fifth of the wall on this footprint. The rim closes
        them. Past 1.41 the band already contains the rim and the union changes
        nothing, so thickness keeps meaning metres everywhere it matters.
        """
        return (self - self.erode(thickness)) | self.rim()

    # -- topology ---------------------------------------------------------

    def components(self, min_cells: int = 1, diagonal: bool = False
                   ) -> list["Mask"]:
        """Split into connected components, largest first.

        Four-connected by default, which is what an area wants: two rooms that
        touch only at a corner are two rooms. `diagonal` switches to the eight
        neighbours, which is what a *line* wants. A one-pixel line drawn at 52
        degrees is a staircase whose cells meet corner to corner, so under
        four-connectivity it is not one line but forty fragments of one or two
        cells each -- every one of them below any sane `min_cells`, so the answer
        comes back empty and the line has silently ceased to exist.
        """
        steps = _MOORE if diagonal else ((-1, 0), (1, 0), (0, -1), (0, 1))
        seen = bytearray(len(self.bits))
        w = self.width
        out = []
        for start, v in enumerate(self.bits):
            if not v or seen[start]:
                continue
            part = self.empty_like()
            stack = [start]
            seen[start] = 1
            while stack:
                i = stack.pop()
                part.bits[i] = 1
                x, z = i % w, i // w
                for dx, dz in steps:
                    nx, nz = x + dx, z + dz
                    if not (0 <= nx < w and 0 <= nz < self.length):
                        continue
                    j = nz * w + nx
                    if self.bits[j] and not seen[j]:
                        seen[j] = 1
                        stack.append(j)
            if part.count() >= min_cells:
                out.append(part)
        out.sort(key=lambda m: -m.count())
        return out

    def holes(self) -> "Mask":
        """The clear cells this shape encloses -- courts, light wells, a pool.

        Background reached from outside the grid is not a hole, so a shape open
        to the edge of the canvas has none. The search is eight-connected while
        `components` is four-connected by default, and that pairing is the point
        rather than an inconsistency: a wall drawn at fifty degrees is a
        staircase, and background allowed only cardinal steps would call the
        diagonal gap between two of its cells an enclosed hole. Eight-connected
        background asks the same question `Mask.rim` answers -- does water get
        out -- so the two agree about what a wall is.
        """
        outside = ~self
        out = self.empty_like()
        w, h = self.width, self.length
        for part in outside.components(diagonal=True):
            bounds = part.bounds()
            if bounds is None:
                continue
            x0, z0, x1, z1 = bounds
            if x0 == 0 or z0 == 0 or x1 == w - 1 or z1 == h - 1:
                continue  # touches the border, so it is the outside
            out = out | part
        return out

    def largest_rect(self) -> tuple[int, int, int, int] | None:
        """The biggest axis-aligned rectangle that fits inside, as (x0,z0,x1,z1).

        For putting something rectangular -- a pool, a court, a pad -- on ground
        that roads and water have cut into an awkward shape. Written by hand in
        one building's `build.py` at thirty-five lines; the need is not that
        building's.

        Largest by area, by the standard histogram sweep: for each row, how far
        up each column runs unbroken, then the largest rectangle under that
        histogram. Axis-aligned in *world* coordinates, so on a rotated building
        it is the biggest upright rectangle and not the biggest rectangle in the
        frame -- draw the latter with `Frame.rect`, which is what it is for.
        """
        best = None
        heights = [0] * self.width
        for z in range(self.length):
            base = z * self.width
            for x in range(self.width):
                heights[x] = heights[x] + 1 if self.bits[base + x] else 0
            stack: list[int] = []
            for x in range(self.width + 1):
                here = heights[x] if x < self.width else 0
                start = x
                while stack and heights[stack[-1]] >= here:
                    top = stack.pop()
                    area = heights[top] * (x - top)
                    if best is None or area > best[0]:
                        best = (area, top, z - heights[top] + 1, x - 1, z)
                    start = top
                stack.append(start)
        return None if best is None else best[1:]

    def squared(self, frame, trim: float = 0.02, keep: float | None = None,
                edge: str = "both", reach: tuple[float, float] | None = None
                ) -> tuple[float, float, float, float] | None:
        """The rectangle this shape actually is, as (u0, u1, v0, v1) in `frame`.

        The fourth idiom, and the one three buildings wrote by hand. `footprint`
        keeps every wobble, `box` takes the bounding extent, `straighten` keeps
        the corners and drops the noise between them -- and none of them helps
        with the case that keeps coming up: **a part that is a rectangle, drawn
        by somebody with a mouse.** There the traced edge has a chamfer at each
        corner over four or five cells, a long edge in two steps, and half a
        metre more rounding put on by `Site.footprint`'s own closing, which is a
        Euclidean dilation and therefore a disc. A building's corner is a right
        angle. Straightening keeps the chamfer, because a chamfer four cells
        deep is further off the chord than any tolerance worth using; boxing
        squares off the raked end the mapper drew on purpose, three parts away.

        So: measure the extent robustly and draw the rectangle. Both edges of
        each axis are a trimmed quantile rather than the extreme, because a
        traced part carries a few cells of whisker and one of them sets a
        bounding box. Returned as numbers and not as a mask, so the caller can
        overrule one edge with something it measured elsewhere -- a court's own
        bottom, a neighbour's face -- which is what every hand-written copy of
        this did.

        `keep` is what stops it from squaring off a deliberate rake. A part is
        cut into whole-metre stations along u, and a station whose own reach
        falls more than `keep` metres short of the fitted rectangle is not part
        of that rectangle: it is the corner the mapper cut back, and on a real
        capture that cut-back corner is there too. The run is then re-taken from
        the stations that are left and the rest of the shape is the caller's --
        union the rectangle back over the drawn mask and the rake survives.
        `edge` says which side to ask about: "low", "high", or "both", for a
        part whose other flank is somebody else's business. `reach` overrules
        the fitted (v0, v1) with numbers the caller measured better -- a wing's
        own tip off the drawn mass, a court's bottom -- which matters where this
        mask holds more than the part: a quantile taken over a wing *and* the
        slab it joins is a quantile of the pair, and the wing's tip is not in
        it.

        `None` for an empty mask. Every number returned is an outer edge, half a
        cell out from the centres it was measured on -- the same half cell
        `_offset_ring` pushes a straightened outline back by, and for the same
        reason: a contour read off cell centres describes a shape one cell
        smaller than the one that was drawn.
        """
        cells = self.cells()
        if not cells:
            return None
        local = [frame.to_local(x + 0.5, z + 0.5) for x, z in cells]

        def span(values: list[float]) -> tuple[float, float]:
            values = sorted(values)
            last = len(values) - 1
            return values[int(trim * last)], values[int((1.0 - trim) * last)]

        u0, u1 = span([u for u, _ in local])
        v0, v1 = span([v for _, v in local])
        u0, u1, v0, v1 = u0 - 0.5, u1 + 0.5, v0 - 0.5, v1 + 0.5
        if reach is not None:
            v0, v1 = float(reach[0]), float(reach[1])

        if keep is None:
            return (u0, u1, v0, v1)

        low: dict[int, float] = {}
        high: dict[int, float] = {}
        for u, v in local:
            k = int(math.floor(u))
            if v < low.get(k, 1e18):
                low[k] = v
            if v > high.get(k, -1e18):
                high[k] = v

        def reaches(k: int) -> bool:
            if edge in ("both", "low") and low[k] - 0.5 > v0 + keep:
                return False
            if edge in ("both", "high") and high[k] + 0.5 < v1 - keep:
                return False
            return True

        square = sorted(k for k in low if reaches(k))
        if not square:
            return None
        return (float(square[0]), float(square[-1] + 1), v0, v1)

    def contour(self) -> list[tuple[int, int]]:
        """The outer boundary of this mask, in order, walking clockwise.

        Moore-neighbour tracing with Jacob's stopping criterion: step to the
        first set neighbour clockwise from where you came, and stop once you
        leave the start cell in the direction you first left it. Starting from
        the raster-first cell guarantees its west neighbour is clear, which is
        the seed the walk needs.

        Only the outermost ring of one component; holes are not traced.
        """
        w = self.width
        try:
            start_index = next(i for i, v in enumerate(self.bits) if v)
        except StopIteration:
            return []
        start = (start_index % w, start_index // w)

        contour = [start]
        here = start
        came_from = (start[0] - 1, start[1])
        first_step = None
        for _ in range(8 * len(self.bits)):
            back = _MOORE.index((came_from[0] - here[0], came_from[1] - here[1]))
            step = None
            for k in range(1, 9):
                i = (back + k) % 8
                cand = (here[0] + _MOORE[i][0], here[1] + _MOORE[i][1])
                if self.get(cand[0], cand[1]):
                    step = cand
                    prev = _MOORE[(i - 1) % 8]
                    came_from = (here[0] + prev[0], here[1] + prev[1])
                    break
            if step is None:
                return contour  # a lone cell has no ring
            if first_step is None:
                first_step = step
            elif here == start and step == first_step:
                return contour[:-1]  # drop the repeated start
            contour.append(step)
            here = step
        return contour

    def edges(self, frame, tolerance: float = 1.0, least: int = 3,
              station: float | None = None) -> list["Edge"]:
        """This shape's outline read as a list of straight faces -- see `Edge`.

        The measurement `squared` is a special case of. A part that is a
        rectangle has four faces along u and v and a fitted extent describes it;
        a part with a raked end, a splayed wing, a slab following a plot
        boundary has a face whose *direction* is the measurement, and no extent
        holds a direction. Every building that needed one wrote it by hand off a
        pair of extreme points, and a pair of extreme points is exactly the
        reading a single cell of tracing whisker moves -- one of them put a five
        metre wedge down the side of a slab that is straight on the map.

        Read off the outer ring of the largest piece, in the frame, in four
        steps, and the order matters:

        1. **Break.** Douglas-Peucker at `tolerance` says where the corners are.
           Its vertices are only candidates: it cuts at whatever is furthest
           from a chord, so a gentle bow becomes two faces and a chamfered
           corner becomes three.
        2. **Fit, station by station.** Each run is cut into stations of
           `station` metres along its own chord and each station contributes the
           **median** of the points on it, so the rasterised staircase is a
           wobble about the line rather than a set of points pulling on it. A
           station the shape is not on contributes nothing, and it is dropped
           **before** the fit rather than being read as zero -- the difference
           between a face that stops early and a face that dives to the origin.
           The line is then re-fitted once without the stations more than
           `tolerance` off it, which is what keeps a neighbour's corner, caught
           in the same run, from tilting the whole face.

           `station` defaults to this frame's staircase, `|cos| + |sin|`, and
           metres would be the wrong unit for it. A boundary is traced through
           cells, so along a diagonal edge it steps corner to corner and lands
           1.41 m further on; cut into one-metre stations, a *perfect* edge
           leaves a quarter of them empty and reports itself three-quarters
           present. At the staircase the bins are exactly the period the
           rasteriser works at, every station of a whole edge holds a point, and
           `share` goes back to meaning what it says: how much of this face the
           shape was actually on.
        3. **Merge, by fit and not by angle.** Adjacent runs are merged
           best-first while the merged line still fits within `tolerance`. This
           is what decides a corner: two runs that were one gentle bow come back
           together, a real corner never does because merging across it throws
           the spread up. It settles the chamfered corner the same way and for
           the same reason -- the short skew run joins whichever neighbour it
           actually lies along, chosen by spread, **never** by which of the two
           is steeper. Steepness is a property of the drawing's noise as much as
           of the building; the fit is a property of the building.
        4. **Drop the short.** A run still under `least` stations is not a face:
           it is the corner a hand cut back, or the quarter-disc `Site.footprint`
           bit out. It is not fitted and it is not extended -- three points on a
           traced corner will fit a line beautifully and point anywhere at all.
           It is dropped, and the faces on either side of it meet where their
           own lines cross.

        So corners are intersections of measured lines, which is the whole
        point: a corner that comes out of a trace is as good as the trace, and a
        corner that comes out of two twenty-metre faces is as good as forty
        metres of edge.

        The outer ring of one piece and no holes: a court has no faces of its
        own that a build would draw this way, and a second piece is a second
        part. Empty when the shape is too small or too round to leave three
        faces standing, and a caller that gets fewer than three has no polygon
        and should say so rather than draw what is left.
        """
        if station is None:
            station = staircase(frame)
        pieces = self.components()
        if not pieces:
            return []
        ring = pieces[0].contour()
        if len(ring) < 4:
            return []
        raw = [frame.to_local(x + 0.5, z + 0.5) for x, z in ring]
        breaks = _keep_ring(raw, tolerance)
        if len(breaks) < 3:
            return []

        n = len(raw)
        runs = []
        for i, start in enumerate(breaks):
            stop = breaks[(i + 1) % len(breaks)]
            span, j = [], start
            while True:
                span.append(raw[j])
                if j == stop:
                    break
                j = (j + 1) % n
            runs.append(span)

        faces = [[span, _fit_face(span, tolerance, station)] for span in runs]
        faces = [f for f in faces if f[1] is not None]
        faces = _merge_faces(faces, tolerance, station)
        faces = [f for f in faces if f[1]["span"] >= least]
        if len(faces) < 3:
            return []

        # The ring's own winding, so "outward" is a fact about the shape rather
        # than an argument -- the same reading `_offset_ring` takes.
        twice_area = 0.0
        for i, (u, v) in enumerate(raw):
            pu, pv = raw[i - 1]
            twice_area += pu * v - u * pv
        turn = 1.0 if twice_area > 0.0 else -1.0
        return [_edge_of(fit, turn) for _, fit in faces]

    def faceted(self, frame, tolerance: float = 1.0, least: int = 3,
                pad: float = 0.0, edges: list["Edge"] | None = None,
                station: float | None = None) -> "Mask | None":
        """This shape redrawn as the polygon of its own fitted faces.

        `edges` measures; this draws. The faces are read, their neighbours are
        crossed to give the corners, and the polygon is rasterised once through
        `Frame.region` -- so the result is a clean staircase of straight edges
        meeting at corners that are straight by construction, at the building's
        own angle, whatever the trace did.

        Between `straighten` and `squared` and better than either wherever a
        part is a polygon that is not a rectangle. Straightening keeps a
        chamfered corner because a four-cell chamfer is further off the chord
        than any usable tolerance; squaring throws away the rake that the
        chamfer is a corner of. This keeps the rake, as a line fitted to every
        station of it, and squares the corner.

        `pad` opens the shape outward by that many metres, the way
        `Site.footprint`'s pad does, and unlike a dilation it does it by moving
        each face out along its own normal -- so a padded corner stays a corner
        instead of coming back as a quarter-disc. Half a cell is added on top,
        for the reason `_offset_ring` gives at length: every ring in this file is
        read through cell centres and describes a shape one cell small.

        `None`, and not a fallback, when fewer than three faces survive or the
        corners will not close. A shape too round or too small to have faces has
        no polygon, and quietly handing back the drawn mask would put an unpadded
        part into a build that asked for a padded one.
        """
        faces = (self.edges(frame, tolerance, least, station)
                 if edges is None else edges)
        if len(faces) < 3:
            return None
        ring = _corner_ring(faces, tolerance)
        if len(ring) < 3:
            return None
        ring = _offset_ring(ring, 0.5 + pad)
        return frame.region(self.width, self.length,
                            lambda u, v: _in_polygon(ring, u, v))

    # -- output -----------------------------------------------------------

    def straighten(self, frame, tolerance: float = 1.0) -> "Mask":
        """The same shape with its outline's staircase taken off.

        A map is a drawing and a drawn edge wobbles by a cell or two. Traced,
        that wobble becomes a sawtooth, and at one block to the metre a sawtooth
        is a metre deep -- it is the first thing anyone notices about a roof
        edge beside a photograph of the real one, and no other check here can
        see it: a jagged edge measures the same as a straight one at every
        station, encloses the same area, and casts the same silhouette.

        Two idioms already existed and neither covers this. `Site.footprint`
        keeps the drawn mask, sawtooth and all, which is right wherever the
        plan's own shape carries a measurement. `Site.box` replaces the part
        with its extent, which is straight and throws away every real corner:
        on a slab whose creek end is raked eight degrees it squares off the one
        thing the mapper drew on purpose.

        This is the third: keep the corners, drop the noise. The contour is read
        into the building's own frame, simplified by Douglas-Peucker at
        `tolerance`, and the simplified ring is rasterised again through
        `frame.region` -- so the angle is still handled in exactly one place and
        the result is a clean staircase of one straight edge instead of a
        stack of little ones.

        `tolerance` is in metres and is the whole control. Under a cell it
        preserves the wobble it was meant to remove; a real rake, a bow or a
        curve survives at any tolerance, because its vertices are further from
        the chord than the noise is. Above about two metres it starts cutting
        corners the building has, and `checks.jaggedness` is the number that
        says which side of that you are on.

        A couple of degrees away from the reference costs nothing. A sawtooth
        costs the whole edge.
        """
        rings = self._rings(frame, tolerance)
        if not rings:
            return self.copy()
        return frame.region(self.width, self.length,
                            lambda u, v: _fills(rings, u, v))

    def _rings(self, frame, tolerance: float) -> list[list[tuple[float, float]]]:
        """Every boundary of this shape as a simplified polygon in `frame`.

        One ring per connected piece, plus one per hole. Reading only
        `contour()` -- the outermost ring of the *first* piece -- was enough
        while this was one part of one plan, and is wrong the moment either
        thing is true: a building whose two wings arrive in one mask keeps only
        the wing that rasterises first, and a building with a court has its
        court filled in. Both would be silent. A court that closes over is not
        a failure anybody would trace back to a straightening.

        The rings are combined by the even-odd rule (see `_fills`), which is
        what makes holes work without any of the callers knowing about them: a
        cell inside a piece and inside its court is crossed twice.

        A piece too small to trace -- three cells or fewer -- contributes no
        ring and is dropped. Anything that small is below `frame.staircase` and
        cannot be drawn as a continuous thing at this scale anyway; keeping it
        would be keeping the whisker these operations exist to remove.
        """
        rings = []
        for piece in list(self.components()) + list(self.holes().components()):
            ring = piece.contour()
            if len(ring) < 4:
                continue
            local = [frame.to_local(x + 0.5, z + 0.5) for x, z in ring]
            kept = _simplify_ring(local, tolerance)
            if len(kept) >= 3:
                rings.append(_offset_ring(kept, 0.5))
        return rings

    @staticmethod
    def _reflect(rings, axis: float, along: str):
        if along == "u":
            return [[(2.0 * axis - u, v) for u, v in r] for r in rings]
        return [[(u, 2.0 * axis - v) for u, v in r] for r in rings]

    def mirrored(self, frame, axis: float, along: str = "u",
                 tolerance: float = 1.0) -> "Mask":
        """This shape reflected across `axis`, re-rasterised, not copied.

        The reflection happens to the **outline**, in the building's own frame,
        and the mirror image is then rasterised from scratch -- the same
        argument `Frame.flipped` makes at length, applied to a mask that already
        exists rather than to a shape about to be drawn. Mirroring the cells
        instead is a resampling: at fifty degrees to the world grid a reflection
        is an isometry of the plane and not of the cell lattice, so a forward
        splat leaves pinholes and a backward sample re-rasterises every edge.

        Prefer `Frame.flipped` when the second half has not been drawn yet.
        This is for the other case: a half that arrived from a measurement --
        map decomposition, roof steps -- and has to be matched.
        """
        if along not in ("u", "v"):
            raise ValueError(f"a mask is mirrored along u or v, not {along!r}")
        rings = self._rings(frame, tolerance)
        if not rings:
            return self.copy()
        flipped = self._reflect(rings, axis, along)
        return frame.region(self.width, self.length,
                            lambda u, v: _fills(flipped, u, v))

    def symmetrise(self, frame, axis: float, along: str = "u",
                   tolerance: float = 1.0, keep: str = "union") -> "Mask":
        """This shape made symmetric about `axis`, in one rasterisation.

        `checks.twins` measures whether two halves match and says by how much.
        Nothing until now made them match. A pair of wings measured off a
        drawing differs by a cell or two along every edge for no reason anybody
        would defend, and at a block to the metre that difference is the first
        thing a photograph of the pair shows up.

        Both the outline and its reflection are built as polygons and asked
        together, so the result is rasterised **once**, at the building's angle,
        through `Frame.region` -- the union or the intersection of two shapes,
        not the union of two staircases. Overlaying two separately rasterised
        masks would leave a one-cell fringe wherever the two staircases disagree,
        which is the sawtooth this is here to remove.

        `keep` is "union" to take the larger reading of every edge and
        "intersection" to take the smaller. Union is the default because a wing
        measured short is the usual failure -- a flood fill stopped at a shadow,
        a decomposition that lost a corner to a drawn line.

        This is a **decision**, not a measurement: it overrides one half of the
        reference with the other. Declare it where it is set, name the axis, and
        let `checks.twins` report what it cost.
        """
        if keep not in ("union", "intersection"):
            raise ValueError(
                f"symmetry keeps the union or the intersection, not {keep!r}")
        if along not in ("u", "v"):
            raise ValueError(f"a mask is mirrored along u or v, not {along!r}")
        rings = self._rings(frame, tolerance)
        if not rings:
            return self.copy()
        flipped = self._reflect(rings, axis, along)

        if keep == "union":
            def inside(u: float, v: float) -> bool:
                return _fills(rings, u, v) or _fills(flipped, u, v)
        else:
            def inside(u: float, v: float) -> bool:
                return _fills(rings, u, v) and _fills(flipped, u, v)

        return frame.region(self.width, self.length, inside)

    def to_png(
        self,
        path: str | Path,
        scale: int = 1,
        on: tuple[int, int, int] = (235, 235, 235),
        off: tuple[int, int, int] = (28, 30, 34),
    ):
        from PIL import Image

        image = Image.new("RGB", (self.width, self.length))
        px = image.load()
        for z in range(self.length):
            base = z * self.width
            for x in range(self.width):
                px[x, z] = on if self.bits[base + x] else off
        if scale != 1:
            image = image.resize(
                (self.width * scale, self.length * scale), Image.NEAREST
            )
        image.save(path)
        return image


def _in_polygon(ring: list[tuple[float, float]], u: float, v: float) -> bool:
    """Even-odd point in polygon, asked of a cell centre.

    The same question `Frame.region` asks of every other shape in this library,
    so a polygon rasterised through here lands where an inequality would.
    Winding is not consulted, which is what lets a reflected ring -- traced
    clockwise, mirrored to counter-clockwise -- be tested unchanged.
    """
    hit = False
    j = len(ring) - 1
    for i, (ui, vi) in enumerate(ring):
        uj, vj = ring[j]
        if (vi > v) != (vj > v) and \
                u < (uj - ui) * (v - vi) / (vj - vi) + ui:
            hit = not hit
        j = i
    return hit


def _offset_ring(ring: list[tuple[float, float]],
                 distance: float) -> list[tuple[float, float]]:
    """The same ring pushed `distance` outward, away from its own interior.

    Every ring in this file is traced through the **centres** of the boundary
    cells, so the polygon it describes is inset from the shape's real edge by up
    to half a cell. Rasterising it back gives a shape systematically smaller
    than the one that went in: on a plain rectangle at 52 degrees the loss is
    six per cent of the area, spread evenly round the perimeter where nobody
    would read it as a defect.

    That mattered less while `Mask.straighten` was opt-in and unused. It matters
    now in two places at once. Straightening runs on every part of every
    building, so the shrink would be a standing tax on the whole corpus; and
    `checks.jaggedness` is defined as the disagreement between a shape and its
    straightened reading, so the same half cell was being reported as six per
    cent of drawing noise on shapes that had none.

    Outward is defined by the ring's own winding rather than by an argument,
    which is what lets a hole be offset by the same call: a hole traced as a
    piece of its own grows away from its own middle, which is the direction that
    keeps the court the size it was.

    The corner treatment is a mitre, capped. Two edges meeting at a sharp angle
    would put the mitred vertex arbitrarily far out, so the extension is limited
    to three times the offset and the corner comes back slightly cut. At half a
    cell the cut is invisible; without the cap a hairpin in a traced contour
    would throw a spike across the building.
    """
    n = len(ring)
    if n < 3 or distance == 0.0:
        return list(ring)

    twice_area = 0.0
    for i, (u, v) in enumerate(ring):
        pu, pv = ring[i - 1]
        twice_area += pu * v - u * pv
    turn = 1.0 if twice_area > 0.0 else -1.0

    def normal(a, b):
        du, dv = b[0] - a[0], b[1] - a[1]
        length = math.hypot(du, dv)
        if length == 0.0:
            return None
        return (turn * dv / length, -turn * du / length)

    out = []
    for i, here in enumerate(ring):
        before = normal(ring[i - 1], here)
        after = normal(here, ring[(i + 1) % n])
        if before is None and after is None:
            out.append(here)
            continue
        if before is None:
            before = after
        if after is None:
            after = before
        bu, bv = before[0] + after[0], before[1] + after[1]
        length = math.hypot(bu, bv)
        if length < 1e-9:      # a spike doubling back on itself
            out.append(here)
            continue
        bu, bv = bu / length, bv / length
        reach = distance / max(0.34, bu * before[0] + bv * before[1])
        out.append((here[0] + bu * reach, here[1] + bv * reach))
    return out


def _fills(rings: list[list[tuple[float, float]]], u: float, v: float) -> bool:
    """Even-odd across a whole set of rings: is this cell centre in the shape?

    Pieces never overlap, so a cell in one piece is crossed once. A cell in a
    piece *and* in that piece's court is crossed twice and comes out clear,
    which is how holes survive an operation that only ever knew about outlines.
    """
    hit = False
    for ring in rings:
        if _in_polygon(ring, u, v):
            hit = not hit
    return hit


def _least_squares(rows: list[tuple[float, float]]) -> tuple[float, float]:
    """Slope and intercept of `offset against distance along`, by least squares."""
    n = len(rows)
    mean_t = sum(t for t, _ in rows) / n
    mean_o = sum(o for _, o in rows) / n
    stt = sum((t - mean_t) ** 2 for t, _ in rows)
    sto = sum((t - mean_t) * (o - mean_o) for t, o in rows)
    slope = sto / stt if stt > 1e-9 else 0.0
    return slope, mean_o - slope * mean_t


def _fit_face(points: list[tuple[float, float]], tolerance: float,
              station: float, trim: bool = True) -> dict | None:
    """The line a run of contour points describes -- see `Mask.edges`, step 2.

    Everything happens in the chord's own axes: distance along the run against
    offset from it. Fitting v against u instead would make a face parallel to v
    a division by zero and a face near one a fit ruled by its worst-conditioned
    station, and a building's faces run whichever way the street does.

    `trim` is the one re-fit without the stations that missed. `_merge_faces`
    turns it off: a merge has to be justified by every station of both runs, and
    a fit allowed to discard a sixth of them will justify joining two faces
    across the corner between them.
    """
    if len(points) < 2:
        return None
    au, av = points[0]
    bu, bv = points[-1]
    du, dv = bu - au, bv - av
    run = math.hypot(du, dv)
    if run < 1e-9:
        return None
    du, dv = du / run, dv / run

    bins: dict[int, list[float]] = {}
    for u, v in points:
        along = (u - au) * du + (v - av) * dv
        bins.setdefault(int(along // station), []).append(
            (u - au) * -dv + (v - av) * du)
    lo, hi = min(bins), max(bins)
    span = hi - lo + 1

    # One row per station, and the median of what is on it rather than the mean:
    # a rasterised edge is a staircase, so a station holds a short vertical run
    # of cells whose middle is the line and whose ends are the tread. A station
    # the shape is not on contributes no row at all, which is the whole of what
    # "dropped before the fit" means -- read as a zero it would pull the line
    # down to the origin, and read after the fit it would already have done it.
    rows = []
    for k in range(lo, hi + 1):
        offsets = bins.get(k)
        if not offsets:
            continue
        offsets.sort()
        rows.append(((k + 0.5) * station, offsets[len(offsets) // 2]))
    if len(rows) < 2:
        return None

    slope, base = _least_squares(rows)
    outliers = 0
    scale = math.hypot(1.0, slope)
    kept = [r for r in rows
            if abs(r[1] - slope * r[0] - base) / scale <= tolerance]
    # One re-fit and no more, and only while most of the run survives it. What
    # it removes is a neighbour's corner caught in the same run, which drags a
    # twenty-metre face by a degree or two; what it must not do is walk a face
    # down to its straightest few stations and report a spread of nothing.
    #
    # Without the floor this is not a rounding error, it is the whole primitive
    # inverted: a run traced round a right angle keeps the three stations
    # nearest whichever line the first pass landed on, comes back with a spread
    # of 0.0, and the merge pass then reads a corner as the best fit on the
    # building and joins across it. A rectangle came back as a triangle.
    floor = max(3, int(0.6 * len(rows)))
    if trim and len(kept) >= floor and len(kept) != len(rows):
        outliers = len(rows) - len(kept)
        rows = kept
        slope, base = _least_squares(rows)
        scale = math.hypot(1.0, slope)
    off = [abs(o - slope * t - base) / scale for t, o in rows]
    return {
        "point": (au, av), "axis": (du, dv), "m": slope, "q": base,
        "reach": (rows[0][0], rows[-1][0]),
        "run": run, "span": span, "kept": len(rows),
        "absent": span - len(bins), "outliers": outliers,
        "spread": math.sqrt(sum(d * d for d in off) / len(off)),
        "worst": max(off),
        "share": len(rows) / span,
    }


def _merge_faces(faces: list, tolerance: float, station: float) -> list:
    """Join adjacent runs while the joined line still fits -- best fit first.

    Best-first and not in ring order, because the merge that fits best is the
    one that was never a corner, and taking it first stops a long face from
    swallowing a short skew one on its way past. This is `Mask.edges` step 3,
    and it is the only place a corner is decided: nothing here looks at the
    angle between two runs, which is what a chamfer makes shallow and a traced
    right angle makes noisy.

    The candidate is judged on an untrimmed fit -- every station of both runs
    counted -- and only the winner is kept as the trimmed one. A trimmed fit
    asked whether two runs *can* be made to look like one line, and the answer
    across a right angle is yes: throw away the shorter limb and the longer one
    fits perfectly. The untrimmed fit asks whether they *are* one line, which is
    the question a corner has to answer.

    And it is judged on the *worst* station rather than on the RMS, which is
    what `tolerance` means everywhere else in this file and is the difference
    between reading a disc and merging one. A bow is systematic: every station
    sits on one side of the chord, so a fifteen-metre chord across a sixteen-
    metre radius misses by two metres in the middle and still averages under
    one. Judged on the RMS a thirty-metre disc came back as a heptagon whose
    every side was a metre outside the shape it was drawn from.
    """
    while len(faces) > 3:
        best = None
        for i in range(len(faces)):
            j = (i + 1) % len(faces)
            joined = faces[i][0][:-1] + faces[j][0]
            fit = _fit_face(joined, tolerance, station, trim=False)
            if fit is None or fit["worst"] > tolerance:
                continue
            if best is None or fit["spread"] < best[0]:
                best = (fit["spread"], i, j, joined)
        if best is None:
            return faces
        _, i, j, joined = best
        faces[i] = [joined, _fit_face(joined, tolerance, station)]
        faces.pop(j)
    return faces


def _edge_of(fit: dict, turn: float) -> Edge:
    """One fitted run as an `Edge`, with its normal pointing out of the shape.

    Outward is taken from the ring's own winding, the same reading
    `_offset_ring` makes, so no caller has to know which way the frame turns.
    """
    au, av = fit["point"]
    du, dv = fit["axis"]
    slope, base = fit["m"], fit["q"]
    nu, nv = -dv, du
    lu, lv = du + slope * nu, dv + slope * nv
    length = math.hypot(lu, lv)
    lu, lv = lu / length, lv / length
    a, b = turn * lv, -turn * lu
    c = a * (au + base * nu) + b * (av + base * nv)
    ends = tuple((au + t * du + (slope * t + base) * nu,
                  av + t * dv + (slope * t + base) * nv)
                 for t in fit["reach"])
    run = math.hypot(ends[1][0] - ends[0][0], ends[1][1] - ends[0][1])
    return Edge(a, b, c, ends, run, fit["span"], fit["kept"], fit["absent"],
                fit["outliers"], fit["spread"], fit["worst"], fit["share"])


def _corner_ring(edges: list[Edge], tolerance: float
                 ) -> list[tuple[float, float]]:
    """The polygon a list of faces encloses: every corner where two of them cross.

    `Mask.edges` step 4 finished. A corner read off a trace is as good as the
    trace; a corner where two twenty-metre faces cross is as good as forty
    metres of edge, and is a right angle exactly when the building's is.
    """
    out: list[tuple[float, float]] = []
    n = len(edges)
    for i, here in enumerate(edges):
        after = edges[(i + 1) % n]
        gap = math.hypot(after.ends[0][0] - here.ends[1][0],
                         after.ends[0][1] - here.ends[1][1])
        cross = here.meets(after)
        limit = max(4.0 * tolerance, 3.0 * gap)
        if cross is not None and all(
                math.hypot(cross[0] - end[0], cross[1] - end[1]) <= limit
                for end in (here.ends[1], after.ends[0])):
            out.append(cross)
            continue
        # Two faces that will not cross anywhere near the corner they are meant
        # to make: near-parallel, or with something dropped between them that
        # was longer than a chamfer. Keep both ends and leave the step. A
        # corner put in the wrong place is a spike across the building, which is
        # a great deal worse than the notch this was drawn to remove.
        out.append(here.ends[1])
        out.append(after.ends[0])
    return out


def half_planes(edges: list[Edge], pad: float = 0.0):
    """These faces as the predicate `Frame.region` wants: inside all of them.

    The build side of `Mask.edges`, and the form to write when a part is convex
    -- a slab, a wing, a deck cut off by a plot boundary. Each face is an
    inequality, the part is where all of them hold, and the corners are wherever
    the inequalities happen to cross, so a build never names a corner at all.

    Two things it does not do. It is **convex** by construction, so an L or an E
    drawn this way comes back filled in at the notch; `Mask.faceted` walks the
    corners instead and holds any polygon. And it adds no half cell of its own:
    every ring in this file is read through cell centres and describes a shape
    one cell small, so a caller drawing a part at its measured face wants
    `pad=0.5` and one drawing it at a padded face wants `0.5 + pad` -- which is
    what `Site.faceted` passes.

    Written as a closure over the numbers rather than over the edges, because
    `Frame.region` asks it once per cell of the canvas.
    """
    lines = [(e.a, e.b, e.c + pad) for e in edges]

    def inside(u: float, v: float) -> bool:
        return all(a * u + b * v <= c for a, b, c in lines)

    return inside


def _keep_run(points: list[tuple[float, float]],
              tolerance: float) -> list[int]:
    """Douglas-Peucker on an open run: keep the ends and whatever is furthest.

    Iterative rather than recursive. A traced contour of a large building runs
    to a few thousand points, and the noisy case -- a staircase, where almost
    nothing can be dropped -- is exactly the one that recurses deepest.

    Indices rather than points, because `Mask.edges` needs the run of the
    original contour that lies *between* two kept vertices, and a contour can
    visit the same coordinate twice: a one-cell neck is traced down one side and
    back up the other, so finding a vertex by its value finds the wrong end of
    the shape.
    """
    if len(points) < 3:
        return list(range(len(points)))
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        au, av = points[lo]
        bu, bv = points[hi]
        du, dv = bu - au, bv - av
        span = math.hypot(du, dv)
        worst, at = 0.0, lo
        for i in range(lo + 1, hi):
            u, v = points[i]
            off = (abs(dv * (u - au) - du * (v - av)) / span if span > 1e-9
                   else math.hypot(u - au, v - av))
            if off > worst:
                worst, at = off, i
        if worst > tolerance:
            keep[at] = True
            stack.append((lo, at))
            stack.append((at, hi))
    return [i for i, on in enumerate(keep) if on]


def _simplify(points: list[tuple[float, float]],
              tolerance: float) -> list[tuple[float, float]]:
    return [points[i] for i in _keep_run(points, tolerance)]


def _keep_ring(points: list[tuple[float, float]],
               tolerance: float) -> list[int]:
    """The same, on a closed loop: the indices of the vertices to keep.

    Cut at the two points furthest apart and simplify each side. A loop
    simplified from an arbitrary start keeps that start as a vertex whether or
    not it is a corner, which on a rectangle traced from the middle of an edge
    leaves a kink in the middle of that edge.
    """
    n = len(points)
    if n < 4:
        return list(range(n))
    au, av = points[0]
    far = max(range(n), key=lambda i: math.hypot(points[i][0] - au,
                                                 points[i][1] - av))
    bu, bv = points[far]
    other = max(range(n), key=lambda i: math.hypot(points[i][0] - bu,
                                                  points[i][1] - bv))
    lo, hi = sorted((far, other))
    first = _keep_run(points[lo:hi + 1], tolerance)
    second = _keep_run(points[hi:] + points[:lo + 1], tolerance)
    return ([lo + i for i in first[:-1]]
            + [(hi + i) % n for i in second[:-1]])


def _simplify_ring(points: list[tuple[float, float]],
                   tolerance: float) -> list[tuple[float, float]]:
    return [points[i] for i in _keep_ring(points, tolerance)]


def iou(a: Mask, b: Mask) -> float:
    """Intersection over union -- the massing agreement score."""
    inter = (a & b).count()
    union = (a | b).count()
    return inter / union if union else 1.0
