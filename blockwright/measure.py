"""Measuring the references, as functions rather than as scripts.

Every building so far has grown a directory of probes: short scripts that read
the mesh or the map, print a histogram, and are then read by a person who copies
a number into a build script. That works exactly once. The second building
re-implements the same measurement, gets a slightly different answer, and now
the pipeline has two answers to one question -- which is the failure the rest of
it exists to prevent.

So the measurements live here, and the rule for this module is one line long:

    every function returns a number or a structure, and nothing prints.

A probe becomes three lines that call one of these and format the result. The
build calls the same function to place something, and the gate calls it to grade
what was placed, so the two cannot drift apart.

What is deliberately *not* here: anything that decides. `storey_height` says the
mesh repeats every 4.5 m; whether the build gets five floors or six is the build
script's business. Measurement and judgement are separate on purpose, because
only one of them can be checked.
"""

from __future__ import annotations

import math

# A one-cell step along a wall at angle t covers |cos t| + |sin t| metres of
# raster, which is 1.0 head-on and 1.41 at 45 degrees. Every rhythm read off a
# rasterised edge sits on top of that staircase, so it is the floor under any
# period a caller may honestly ask for.
STAIRCASE_MARGIN = 2.0


def staircase(frame) -> float:
    """The rasterisation period of an edge drawn at this frame's angle.

    `pitch_probe` was written twice. The first version counted every rise on the
    map's long edges as a notch and reported a pitch of 1.3 to 1.6 m -- which is
    not the building's rhythm, it is the staircase the 52-degree edge makes on a
    square pixel grid. Any search for a real period has to start above this
    number, and `period` takes its lower bound as a required argument so that
    the caller has to have thought about it.
    """
    a = math.radians(frame.angle)
    return abs(math.cos(a)) + abs(math.sin(a))


# -- one-dimensional signals ----------------------------------------------


def median_filter(values, half: int):
    """Median over a window of `half` samples either side.

    A median rather than a mean because the thing being removed is a staircase:
    a run of samples that sit at one of two values and jump between them. A mean
    smears the jump over the window; a median deletes it and leaves the samples
    that agree.
    """
    if half <= 0:
        return list(values)
    out = []
    n = len(values)
    for i in range(n):
        window = sorted(values[max(0, i - half):min(n, i + half + 1)])
        out.append(window[len(window) // 2])
    return out


def detrend(values, half: int):
    """Subtract a local mean, so a slow drift does not swamp the rhythm.

    An edge that wanders half a metre over a hundred metres has more energy in
    that wander than in the notches sitting on it, and an autocorrelation run
    over the raw series reports the wander.
    """
    if half <= 0:
        mean = sum(values) / len(values) if values else 0.0
        return [v - mean for v in values]
    out = []
    n = len(values)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out.append(values[i] - sum(values[lo:hi]) / (hi - lo))
    return out


class Period:
    """A repeating spacing found in a signal, and how strongly it repeats."""

    __slots__ = ("value", "score", "peaks", "step")

    def __init__(self, value: float, score: float, peaks, step: float):
        self.value = value
        self.score = score
        self.peaks = peaks          # (metres, correlation), strongest first
        self.step = step

    def __bool__(self) -> bool:
        return self.value > 0.0

    def __repr__(self) -> str:
        if not self.peaks:
            return "<no period>"
        rest = "  ".join(f"{p:.1f} m r={r:+.2f}" for p, r in self.peaks[1:4])
        return (f"<period {self.value:.2f} m r={self.score:+.2f}"
                + (f"; then {rest}>" if rest else ">"))


def period(values, step: float, lo: float, hi: float) -> Period:
    """The spacing at which `values` repeats, by autocorrelation.

    `lo` and `hi` are in the same units as `step` and neither has a default. A
    threshold has to be chosen and defended; a period does not -- but the window
    it is searched in does, and leaving it implicit is how the staircase gets
    reported as a result. See `staircase`.

    Correlation is normalised by the overlap length as well as by the energy, so
    a long lag is not rewarded for having fewer terms to disagree over.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    if not (0 < lo < hi):
        raise ValueError(f"period window {lo}..{hi} is not an interval")
    n = len(values)
    if n < 4:
        return Period(0.0, 0.0, [], step)

    mean = sum(values) / n
    y = [v - mean for v in values]
    energy = sum(v * v for v in y)
    if energy <= 0.0:
        return Period(0.0, 0.0, [], step)

    scores = []
    for lag in range(max(1, int(lo / step)), int(hi / step) + 1):
        overlap = n - lag
        if overlap < n // 2:
            break
        total = sum(y[i] * y[i + lag] for i in range(overlap))
        scores.append((lag * step, total / (energy * overlap / n)))

    peaks = [(lag, r) for (_, before), (lag, r), (_, after)
             in zip(scores, scores[1:], scores[2:])
             if r > before and r > after]
    peaks.sort(key=lambda p: -p[1])
    if not peaks:
        return Period(0.0, 0.0, [], step)
    return Period(peaks[0][0], peaks[0][1], peaks, step)


def peaks(values, step: float = 1.0, floor: float = 0.25,
          separation: float = 0.0, origin: float = 0.0):
    """Local maxima of a signal, strongest first, no two closer than `separation`.

    `floor` is a fraction of the tallest peak. Returned as (position, height)
    with position in the signal's own units, sorted by position.
    """
    if not values:
        return []
    top = max(values)
    if top <= 0:
        return []
    limit = floor * top
    n = len(values)
    found = [
        i for i in range(n)
        if values[i] >= limit
        and values[i] >= (values[i - 1] if i else -math.inf)
        and values[i] > (values[i + 1] if i + 1 < n else -math.inf)
    ]
    gap = max(0, int(separation / step))
    kept: list[int] = []
    for i in sorted(found, key=lambda j: -values[j]):
        if all(abs(i - k) >= gap for k in kept):
            kept.append(i)
    kept.sort()
    return [(origin + i * step, values[i]) for i in kept]


def histogram(samples, step: float, lo: float, hi: float):
    """Counts per bin, and the value the first bin starts at.

    Returns (counts, lo) so a caller can turn a bin index back into a height
    without having to remember which end it was measured from.
    """
    if step <= 0:
        raise ValueError("step must be positive")
    bins = max(1, int(math.ceil((hi - lo) / step)))
    counts = [0] * bins
    for value in samples:
        if lo <= value < hi:
            counts[int((value - lo) / step)] += 1
    return counts, lo


# -- the mesh --------------------------------------------------------------


class Storeys:
    """Where the floors are, read off the mesh rather than assumed."""

    __slots__ = ("levels", "spacing", "score", "counts", "step", "base")

    def __init__(self, levels, spacing: float, score: float, counts, step, base):
        self.levels = levels        # metres above the datum, ascending
        self.spacing = spacing      # the repeat, in metres
        self.score = score          # how strongly it repeats
        self.counts = counts        # the histogram it came from
        self.step = step
        self.base = base

    def __len__(self) -> int:
        return len(self.levels)

    def __repr__(self) -> str:
        heights = ", ".join(f"{v:.2f}" for v in self.levels)
        return (f"<storeys every {self.spacing:.2f} m (r={self.score:+.2f}) "
                f"at {heights}>")


# Google Earth exports carry a horizontal seam every 4.5 metres: the tiler's
# own grid, not the building's. It puts twenty to sixty times the usual number
# of vertices in a band, on every facade of every building, and autocorrelation
# reports it as a storey with a confidence no real facade reaches. Three
# buildings in a row were told their floors were 4.5 m apart -- one of them a
# 1960s block whose window rows are 2.92 m -- and each spent hours proving it
# was not so.
TILE_SEAM = 4.5
TILE_MARGIN = 0.15


def looks_like_tiling(spacing: float, seam: float = TILE_SEAM,
                      margin: float = TILE_MARGIN) -> bool:
    """Whether a spacing is the exporter's grid rather than the building's."""
    return abs(spacing - seam) <= margin


def storey_height(mesh, frame, bands, u0: float, u1: float,
                  datum: float | None = None, step: float = 0.25,
                  floor: float = 0.0, ceiling: float = 25.0,
                  lo: float = 2.0, hi: float = 6.0) -> Storeys:
    """The storey rhythm, from a histogram of vertex height over the facades.

    Balcony slabs, window heads and sill courses put far more vertices at their
    own height than the blank wall between them does, so the histogram has one
    bar per floor. The spacing of the bars is the storey height.

    `bands` are (v0, v1) strips selecting the facades that carry the rhythm --
    usually the ones facing a court, where the balconies are. Taking the whole
    building instead averages a facade with balconies against a facade without
    and the peaks disappear. `u0`/`u1` do the same along the building, keeping
    the ends and their corners out of it.
    """
    if datum is None:
        datum = mesh.ground()
    samples = []
    for i in range(len(mesh)):
        u, v = frame.to_local(mesh.x[i], mesh.z[i])
        if not (u0 <= u < u1):
            continue
        if bands and not any(a <= v < b for a, b in bands):
            continue
        h = mesh.y[i] - datum
        if floor <= h < ceiling:
            samples.append(h)

    counts, base = histogram(samples, step, floor, ceiling)
    found = period([float(c) for c in counts], step, lo, hi)
    spacing = found.value
    apart = 0.6 * spacing if spacing else 0.0
    levels = [h for h, _ in peaks([float(c) for c in counts], step,
                                  separation=apart, origin=base)]
    return Storeys(levels, spacing, found.score, counts, step, base)


def skyline(mesh, frame, u0: float, u1: float, datum: float | None = None,
            cell: float = 1.0) -> dict[int, float]:
    """Highest material at each v station inside a window along u.

    A station with nothing in it is absent from the result, not zero. That
    distinction is the whole value of the measurement: "the mesh says nothing
    stands here" and "the mesh says something flat stands here" are different
    facts, and a gate that cannot tell them apart grades a missing wing as a
    small height error.
    """
    # A window is a stretch, not a direction. Callers build one by putting two
    # plan coordinates through a registration, and a registration that has a
    # flip in it returns them in the opposite order -- at which point the window
    # is empty and the measurement silently reads nothing. Ordering it here
    # costs one comparison and removes a whole class of empty result.
    if u1 < u0:
        u0, u1 = u1, u0
    if datum is None:
        datum = mesh.ground()
    tops: dict[int, float] = {}
    for i in range(len(mesh)):
        u, v = frame.to_local(mesh.x[i], mesh.z[i])
        if not (u0 <= u < u1):
            continue
        k = int(math.floor(v / cell))
        h = mesh.y[i] - datum
        if h > tops.get(k, -math.inf):
            tops[k] = h
    return tops


class Grid:
    """Material binned by (across, up) -- a section, not a skyline."""

    __slots__ = ("cells", "cell", "v0", "v1", "h0", "h1")

    def __init__(self, cells, cell):
        self.cells = cells          # (v station, height station) -> count
        self.cell = cell
        vs = [k for k, _ in cells] or [0]
        hs = [h for _, h in cells] or [0]
        self.v0, self.v1 = min(vs), max(vs)
        self.h0, self.h1 = min(hs), max(hs)

    def count(self, k: int, h: int) -> int:
        return self.cells.get((k, h), 0)

    def column(self, k: int) -> list[int]:
        """Material at each height station over one v station."""
        return [self.cells.get((k, h), 0) for h in range(self.h0, self.h1 + 1)]

    def solid(self, v0: float, v1: float, h0: float, h1: float,
              least: int = 1) -> float:
        """Fraction of the stations in a box that hold at least `least` points."""
        ks = range(int(math.floor(v0 / self.cell)), int(math.ceil(v1 / self.cell)))
        hs = range(int(math.floor(h0 / self.cell)), int(math.ceil(h1 / self.cell)))
        total = len(ks) * len(hs)
        if not total:
            return 0.0
        hit = sum(1 for k in ks for h in hs if self.cells.get((k, h), 0) >= least)
        return hit / total


def silhouette(mesh, frame, u0: float, u1: float, datum: float | None = None,
               cell: float = 1.0) -> Grid:
    """Vertices binned across and up, keeping everything rather than the top.

    A skyline cannot see a void. A canopy with a hole under it, a court that is
    open to the sky at first floor and roofed at third, an undercroft: all of
    them have the same skyline as the solid thing they should not be. Binning by
    (v, height) keeps the middle of the building, which is where the review's
    missing pool and missing bridge were.
    """
    # Ordered, for the same reason `skyline` orders its own: a window built
    # through a registration that has a flip in it arrives backwards.
    if u1 < u0:
        u0, u1 = u1, u0
    if datum is None:
        datum = mesh.ground()
    cells: dict[tuple[int, int], int] = {}
    for i in range(len(mesh)):
        u, v = frame.to_local(mesh.x[i], mesh.z[i])
        if not (u0 <= u < u1):
            continue
        key = (int(math.floor(v / cell)),
               int(math.floor((mesh.y[i] - datum) / cell)))
        cells[key] = cells.get(key, 0) + 1
    return Grid(cells, cell)


def presence(mesh, frame, u0: float, u1: float, v0: float, v1: float,
             floor: float, ceiling: float, datum: float | None = None,
             cell: float = 1.0) -> float:
    """How much of a box the mesh fills, as a fraction of its plan cells.

    The question is "is there anything here": a bridge to the cone, a roof over
    the canopy, water in the pool. Answered in plan rather than in volume,
    because photogrammetry gives a surface and not a solid -- counting occupied
    volume would report every real object as mostly empty.
    """
    # Ordered, for the same reason `skyline` orders its own: a window built
    # through a registration that has a flip in it arrives backwards.
    if u1 < u0:
        u0, u1 = u1, u0
    if datum is None:
        datum = mesh.ground()
    hit: set[tuple[int, int]] = set()
    for i in range(len(mesh)):
        u, v = frame.to_local(mesh.x[i], mesh.z[i])
        if not (u0 <= u < u1 and v0 <= v < v1):
            continue
        h = mesh.y[i] - datum
        if floor <= h < ceiling:
            hit.add((int(u / cell), int(v / cell)))
    total = (max(1, int((u1 - u0) / cell)) * max(1, int((v1 - v0) / cell)))
    return len(hit) / total


# -- masks -----------------------------------------------------------------


class Profile:
    """The two long edges of a part, station by station along u."""

    __slots__ = ("keys", "low", "high", "bin")

    def __init__(self, keys, low, high, bin: float):
        self.keys = keys
        self.low = low
        self.high = high
        self.bin = bin

    @property
    def u0(self) -> float:
        return self.keys[0] * self.bin if self.keys else 0.0

    @property
    def u1(self) -> float:
        return self.keys[-1] * self.bin if self.keys else 0.0

    def series(self, edge: str, outward: bool = True) -> list[float]:
        """One edge as a plain list, aligned with `keys`.

        `outward` negates the low edge, so both series read "further from the
        centre is larger" and a notch is a dip in either of them.
        """
        if edge not in ("low", "high"):
            raise ValueError("edge is 'low' or 'high'")
        sign = -1.0 if (outward and edge == "low") else 1.0
        source = self.low if edge == "low" else self.high
        return [source[k] * sign for k in self.keys]

    def trim(self, metres: float) -> "Profile":
        """Drop the ends, where the strip turns its corner and the edge is not
        an edge any more."""
        n = int(metres / self.bin)
        keys = self.keys[n:len(self.keys) - n] if n else list(self.keys)
        return Profile(keys, self.low, self.high, self.bin)

    def width(self) -> list[float]:
        return [self.high[k] - self.low[k] for k in self.keys]

    def __len__(self) -> int:
        return len(self.keys)


def edge_profile(mask, frame, bin: float = 0.5) -> Profile:
    """Where a mask's two long edges sit, at each station along u.

    This is what a rhythm is read off: the notches on a facade, the steps in a
    setback, the bays of a colonnade all show as a wobble in one of these two
    series. `frame.profile` answers the same question at whole-metre stations
    and returns tuples; this keeps the sub-metre stations that a median filter
    needs in order to have anything to filter.
    """
    low: dict[int, float] = {}
    high: dict[int, float] = {}
    for x, z in mask.cells():
        u, v = frame.to_local(x + 0.5, z + 0.5)
        k = int(u / bin)
        if v < low.get(k, math.inf):
            low[k] = v
        if v > high.get(k, -math.inf):
            high[k] = v
    return Profile(sorted(set(low) & set(high)), low, high, bin)


# -- orthographic images ---------------------------------------------------


def _grey(image):
    from PIL import Image  # noqa: F401  (imported for the side of the check)

    return image.convert("L") if image.mode != "L" else image


def row_signal(image, x0: int | None = None, x1: int | None = None) -> list[float]:
    """How much each row differs from the row below it, averaged across.

    A floor slab shows in an orthographic elevation as a row that differs
    sharply from its neighbour all the way across the facade, so the sum of that
    difference is a one-dimensional signal whose peaks are the slab edges. Far
    steadier than reading storey heights off the picture by eye, and it is a
    signal rather than a judgement, so `period` and `peaks` can both be run on
    it.
    """
    image = _grey(image)
    w, h = image.size
    x0 = int(w * 0.15) if x0 is None else x0
    x1 = int(w * 0.85) if x1 is None else x1
    if x1 <= x0:
        raise ValueError(f"column window {x0}..{x1} is empty")
    px = image.load()
    span = x1 - x0
    return [sum(abs(px[x, y] - px[x, y + 1]) for x in range(x0, x1)) / span
            for y in range(h - 1)]


def column_signal(image, y0: int | None = None, y1: int | None = None) -> list[float]:
    """The same, turned ninety degrees: vertical edges, for a pier rhythm."""
    image = _grey(image)
    w, h = image.size
    y0 = int(h * 0.15) if y0 is None else y0
    y1 = int(h * 0.85) if y1 is None else y1
    if y1 <= y0:
        raise ValueError(f"row window {y0}..{y1} is empty")
    px = image.load()
    span = y1 - y0
    return [sum(abs(px[x, y] - px[x + 1, y]) for y in range(y0, y1)) / span
            for x in range(w - 1)]


def floor_lines(image, metres_per_pixel: float, x0: int | None = None,
                x1: int | None = None, floor: float = 0.12,
                separation: float = 1.5) -> list[float]:
    """Heights of the floor lines in an elevation, in metres above its bottom.

    `separation` keeps the two rows either side of one slab edge from being
    counted as two floors.
    """
    rows = row_signal(image, x0, x1)
    height = len(rows) + 1
    found = peaks(rows, metres_per_pixel, floor=floor, separation=separation)
    return sorted((height - p / metres_per_pixel) * metres_per_pixel
                  for p, _ in found)


__all__ = [
    "Grid", "Period", "Profile", "Storeys",
    "column_signal", "detrend", "edge_profile", "floor_lines", "histogram",
    "median_filter", "peaks", "period", "presence", "row_signal",
    "silhouette", "skyline", "staircase", "storey_height",
]
