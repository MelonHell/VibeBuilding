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

INF = 1e12

# Clockwise from west. Boundary tracing needs a fixed cyclic order of the eight
# neighbours so "the next one round" is well defined.
_MOORE = [(-1, 0), (-1, -1), (0, -1), (1, -1), (1, 0), (1, 1), (0, 1), (-1, 1)]


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
        ring = self.contour()
        if len(ring) < 4:
            return self.copy()

        local = [frame.to_local(x + 0.5, z + 0.5) for x, z in ring]
        kept = _simplify_ring(local, tolerance)
        if len(kept) < 3:
            return self.copy()

        # Point in polygon, on the cell centre, which is the same question
        # `Frame.region` asks of every other shape in this library.
        def inside(u: float, v: float) -> bool:
            hit = False
            j = len(kept) - 1
            for i, (ui, vi) in enumerate(kept):
                uj, vj = kept[j]
                if (vi > v) != (vj > v) and \
                        u < (uj - ui) * (v - vi) / (vj - vi) + ui:
                    hit = not hit
                j = i
            return hit

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


def _simplify(points: list[tuple[float, float]],
              tolerance: float) -> list[tuple[float, float]]:
    """Douglas-Peucker on an open run: keep the ends and whatever is furthest.

    Iterative rather than recursive. A traced contour of a large building runs
    to a few thousand points, and the noisy case -- a staircase, where almost
    nothing can be dropped -- is exactly the one that recurses deepest.
    """
    if len(points) < 3:
        return list(points)
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
    return [p for p, on in zip(points, keep) if on]


def _simplify_ring(points: list[tuple[float, float]],
                   tolerance: float) -> list[tuple[float, float]]:
    """The same, on a closed loop.

    Cut at the two points furthest apart and simplify each side. A loop
    simplified from an arbitrary start keeps that start as a vertex whether or
    not it is a corner, which on a rectangle traced from the middle of an edge
    leaves a kink in the middle of that edge.
    """
    n = len(points)
    if n < 4:
        return list(points)
    au, av = points[0]
    far = max(range(n), key=lambda i: math.hypot(points[i][0] - au,
                                                 points[i][1] - av))
    bu, bv = points[far]
    other = max(range(n), key=lambda i: math.hypot(points[i][0] - bu,
                                                  points[i][1] - bv))
    lo, hi = sorted((far, other))
    first = _simplify(points[lo:hi + 1], tolerance)
    second = _simplify(points[hi:] + points[:lo + 1], tolerance)
    return first[:-1] + second[:-1]


def iou(a: Mask, b: Mask) -> float:
    """Intersection over union -- the massing agreement score."""
    inter = (a & b).count()
    union = (a | b).count()
    return inter / union if union else 1.0
