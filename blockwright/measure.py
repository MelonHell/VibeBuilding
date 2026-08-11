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


def flatten(profile, rise: float = 1.5, run: float = 4.0) -> list[dict]:
    """A measured profile as the runs it actually has: flat unless proven.

    The default reading of a skyline is one number per station, and built that
    way a flat roof comes out a staircase. Photogrammetry's noise on a plain
    surface is a metre and more; every wobble across a block boundary becomes a
    step, and the section is perfectly happy with all of them because the
    staircase is exactly as tall as what it was read from. One hotel built nine
    treads on a roof its photographs show as one plane with one step in it.

    The other default is as bad and is what a median does: one number over a
    part that really steps is wrong at both ends, and neither error shows in a
    silhouette.

    So, runs. A station joins the run beside it while the **whole run** stays
    within `rise` of its own mid-range -- mid-range and not mean, because the
    section grades the worst station of a run rather than the typical one, so a
    run has to be centred on its extremes. A run shorter than `run` stations is
    not a step, it is a noisy station, and is folded into whichever neighbour it
    is closer to in height. The fold takes the stations, never the height: a
    three-station stub at 17 m folded into a forty-station run at 67 m by
    averaging moved that whole roof to 54.

    Three buildings each wrote a version of this -- `bands`, `_steps`,
    `roof_step` -- with three different statistics and three different answers.

    `profile` is `[(station, height), ...]` or `{station: height}`. Returns
    `[{"from": k0, "to": k1, "top": h, "stations": n}]`, in station order and
    with no gaps between neighbours.
    """
    rows = sorted(profile.items() if isinstance(profile, dict) else profile)
    if not rows:
        return []

    runs: list[list] = []
    for station, height in rows:
        if runs:
            held = runs[-1]
            lo = min(held[2], height)
            hi = max(held[3], height)
            if station == held[1] + 1 and hi - lo <= 2 * rise:
                held[1], held[2], held[3] = station, lo, hi
                held[4] += 1
                continue
        runs.append([station, station, height, height, 1])

    while len(runs) > 1:
        short = [i for i, r in enumerate(runs) if r[4] < run]
        if not short:
            break
        i = min(short, key=lambda k: runs[k][4])
        mine = 0.5 * (runs[i][2] + runs[i][3])
        left = runs[i - 1] if i else None
        right = runs[i + 1] if i + 1 < len(runs) else None
        pick = left
        if right is not None and (
                left is None
                or abs(0.5 * (right[2] + right[3]) - mine)
                < abs(0.5 * (left[2] + left[3]) - mine)):
            pick = right
        pick[0] = min(pick[0], runs[i][0])
        pick[1] = max(pick[1], runs[i][1])
        pick[4] += runs[i][4]
        runs.pop(i)

    # Each run reaches the start of the next, so a station the reference dropped
    # is spanned by its neighbour instead of coming out as a hole in the roof.
    out = []
    for i, r in enumerate(runs):
        end = runs[i + 1][0] if i + 1 < len(runs) else r[1] + 1
        out.append({"from": r[0], "to": end,
                    # The middle of the range, for the reason above.
                    "top": 0.5 * (r[2] + r[3]), "stations": r[4]})
    return out


def twins(profiles: dict, tolerance: float = 1.5, mirror: bool = False) -> dict:
    """Whether two parts the building repeats were measured the same.

    A mirror pair, a row of identical villas, a stack of identical floors: the
    building says they are the same and the reference is measured once per copy.
    Nothing downstream compares those measurements, and the difference between
    them is pure noise -- so it gets built, faithfully, as if it were the
    building.

    That is not a small effect. On one hotel the two towers' measured roofs
    differed by up to five metres at stations where the real roofs are level;
    the build stepped one of them nine times and the other five, and every check
    passed: two towers of the same footprint cast the same silhouette and the
    same profile, and the section grades each against its own half of the
    reference. The only thing that could have caught it was somebody looking at
    a render.

    `profiles` is `{name: {station: height}}`, one entry per copy, stations
    counted from each copy's own start so that they line up. `mirror` reverses
    every profile but the first, for a pair that faces the other way.

    Returns the reconciled profile -- the per-station median, which is the right
    estimator for three or more and the mean for two -- and, more importantly,
    where and by how much the copies disagree. **It does not decide.** A caller
    that merges a pair disagreeing by five metres has replaced a visible defect
    with an invisible one: the section will fail runs of stations on both copies,
    correctly, because neither of them is what was measured. What a disagreement
    means is that one copy is modelled worse than the other, and that is a
    sentence for the report, not a number to average away.
    """
    names = list(profiles)
    if len(names) < 2:
        return {"agree": None, "why": "one copy is not a pair",
                "members": names, "level": {}, "worst": 0.0, "at": None,
                "rows": []}

    ordered = {}
    for i, name in enumerate(names):
        entry = dict(profiles[name])
        if mirror and i:
            keys = sorted(entry)
            entry = {k: entry[j] for k, j in zip(keys, reversed(keys))}
        ordered[name] = entry

    shared = set.intersection(*(set(p) for p in ordered.values()))
    rows, level = [], {}
    worst, at = 0.0, None
    for k in sorted(shared):
        heights = [ordered[name][k] for name in names]
        got = sorted(heights)
        level[k] = (got[len(got) // 2] if len(got) % 2
                    else 0.5 * (got[len(got) // 2 - 1] + got[len(got) // 2]))
        spread = max(heights) - min(heights)
        rows.append((k, heights, spread))
        if spread > worst:
            worst, at = spread, k

    only = {name: sorted(set(ordered[name]) - shared) for name in names}
    return {
        "agree": worst <= tolerance,
        "members": names,
        "level": level,
        "worst": worst,
        "at": at,
        "tolerance": tolerance,
        "rows": rows,
        # Stations one copy has and another does not. A hole in the capture
        # over one of a pair reads as a station the other simply lacks, and
        # that is worth naming rather than dropping.
        "only": {name: got for name, got in only.items() if got},
    }


def mode(values, step: float = 0.5) -> dict:
    """The densest band of `values`, and how much of the population it holds.

    The answer to "what level is this surface at" when the surface is measured
    by a cloud of vertices rather than by a ruler. The median is the obvious
    reading and is wrong on a flat roof: photogrammetry puts no vertex at all on
    a third of a plain surface, and the highest thing it holds in those cells is
    whatever it saw underneath -- a pavement under an overhang, the deck under a
    glazed canopy -- so those cells vote in the median as if the roof were at
    four metres. The mode reads the plane the vertices actually lie on.

    `share` is what makes it usable rather than merely plausible: a flat roof
    comes back with most of its population in one band, and a stepped or
    cluttered one does not. A caller that ignores `share` is treating a
    two-level roof's busiest level as its only one.

    Returns `samples`, `level`, `share` and `median`; `samples` is zero and the
    rest are None when there is nothing to read, so the caller says what it
    wants to do about that rather than getting a confident zero.

    Three buildings each wrote a private copy of this -- `_mode`, `_deck`,
    `roof_step`'s inner helper -- and each rounded and reported it differently.
    """
    values = [v for v in values if v is not None]
    if not values:
        return {"samples": 0, "level": None, "share": 0.0, "median": None}

    lo, hi = min(values), max(values) + step
    counts, base = histogram(values, step, lo, hi)
    best = max(range(len(counts)), key=lambda i: counts[i])
    band = [v for v in values
            if base + best * step <= v < base + (best + 1) * step]

    got = sorted(values)
    return {
        "samples": len(values),
        # The mean of the band rather than the band's centre: the bin is an
        # accident of where `lo` fell, and half a bin is half the tolerance the
        # section has to spend before anything is actually wrong.
        "level": sum(band) / len(band) if band else base + best * step,
        "share": len(band) / len(values),
        "median": got[len(got) // 2],
    }


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


# -- the ground around a building -------------------------------------------
#
# Every check and every probe above this line is about the building. Three
# buildings so far have also needed the ground it stands on -- a pool deck over
# a garage, a road, a beach, a car park a metre down from the entrance -- and
# every one of them wrote the same forty lines into its own `probes/derive.py`
# because the library had nothing to offer.
#
# It is a measurement of *height*, which is what `docs/sources.md` allows a
# capture to be read for. A plot at two levels is two surfaces, and where each
# of them is is simply where its own level is: the boundary between them is a
# step of three metres rather than an edge somebody traced.

# The cell the ground is read on, in metres.
#
# Three, and not the build's own metre. At one metre the answer is a chequer:
# photogrammetry lays a metre of paving with far fewer than one vertex per
# square metre, so most cells hold no reading of the surface at all and take
# whatever else passed through them. Three is coarse enough that every cell
# holds a handful of vertices and fine enough that a step from a road up to a
# deck stays one cell wide.
GROUND_CELL = 3.0

# Which vertex in a cell is its ground: a low quantile rather than the minimum.
# The minimum is the skirt of unclosed polygons a Google Earth export hangs
# under its paving; the median is whatever is standing on the paving.
GROUND_QUANTILE = 0.2

# How few vertices a cell may hold and still be read. Under four, a quantile is
# the minimum by another name.
GROUND_LEAST = 4


class Ground:
    """What the reference stands at, cell by cell, over one plot.

    A height map and nothing else. What each surface *is* -- road, deck, beach,
    lawn -- is not in here and cannot be: two surfaces a metre apart in height
    and on opposite sides of the building read identically, and only the
    building between them tells them apart. `Site.side` is that split.
    """

    __slots__ = ("cells", "cell", "quantile", "floor", "under")

    def __init__(self, cells, cell, quantile, floor, under):
        self.cells = cells          # (a, b) cell -> height over the datum
        self.cell = cell
        self.quantile = quantile
        self.floor = floor
        self.under = under

    def at(self, u: float, v: float) -> float | None:
        """What the ground stands at here, in metres over the datum."""
        return self.cells.get((int(u // self.cell), int(v // self.cell)))

    def band(self, low: float, high: float | None = None) -> list[float]:
        """Every cell reading between two levels, sorted."""
        return sorted(h for h in self.cells.values()
                      if h >= low and (high is None or h < high))

    def level(self, low: float, high: float | None = None,
              share: float = 0.5) -> float | None:
        """One number for a surface: a quantile of the cells inside its band.

        The quantile matters and is worth choosing rather than taking the
        middle. A paved deck that steps down over its last ten metres is not one
        plane, and the middle of its readings is halfway down that step; what a
        build lays its paving at is the level of the paving proper, which is a
        three-quarter point.
        """
        found = self.band(low, high)
        if not found:
            return None
        return found[max(0, min(len(found) - 1, int(share * (len(found) - 1))))]

    def report(self) -> dict:
        return {
            "cell": self.cell,
            "cells": [[a, b, round(h, 2)] for (a, b), h in sorted(self.cells.items())],
            "note": f"the {self.quantile:.0%} quantile of what the reference "
                    f"holds between {self.floor} and {self.under} m of the "
                    f"datum, per {self.cell:.0f} m cell, median-smoothed over "
                    "each cell's nine neighbours",
        }

    @classmethod
    def loads(cls, found: dict) -> "Ground":
        """The same map back out of `derived.json`."""
        return cls({(a, b): h for a, b, h in found["cells"]},
                   float(found["cell"]), GROUND_QUANTILE, 0.0, 0.0)


def ground(mesh, frame, datum: float, floor: float, under: float,
           cell: float = GROUND_CELL, quantile: float = GROUND_QUANTILE,
           least: int = GROUND_LEAST, to_local=None) -> Ground:
    """The ground the reference holds, as a smoothed map of heights.

    `floor` and `under` are the band a *ground* vertex may be in, relative to
    the datum, and both are the building's own decision because both are about
    that capture.

    The floor is the part that matters and the part that is always forgotten. A
    Google Earth export hangs a skirt of unclosed polygons under its paving,
    outside whatever clip box was cut; left in, the lowest thing in every cell
    is a skirt vertex some metres under the road and every surface reads low.
    The ceiling drops the palms, the sea wall's coping and the building itself.

    Read off the *whole* export rather than the building clip, normally: the
    clip that makes a section honest cuts the grounds away by construction. Pass
    the site mesh, not the clipped one.

    `to_local` converts a point in the reference's frame into the plan's, for a
    survey that registered the two -- the map is indexed in the plan's metres so
    that a build can look a cell up by the coordinates it draws in. Without it
    the cells are in the reference's own frame and a build has to register them
    itself, which is a second answer to a question that already has one.
    """
    bag: dict[tuple[int, int], list[float]] = {}
    for i in range(len(mesh)):
        h = mesh.y[i] - datum
        if not (floor <= h <= under):
            continue
        u, v = frame.to_local(mesh.x[i], mesh.z[i])
        if to_local is not None:
            u, v = to_local(u, v)
        bag.setdefault((int(u // cell), int(v // cell)), []).append(h)

    coarse = {key: sorted(vals)[int(quantile * (len(vals) - 1))]
              for key, vals in bag.items() if len(vals) >= least}

    # Median-smoothed over each cell's nine neighbours, for the same reason the
    # quantile is low rather than minimum: one cell that caught a parked car or
    # the lip of a planter is a hole in an otherwise flat surface, and a hole in
    # a height map becomes a hole in whatever is laid from it.
    smooth: dict[tuple[int, int], float] = {}
    for (a, b) in coarse:
        near = sorted(coarse[(a + da, b + db)]
                      for da in (-1, 0, 1) for db in (-1, 0, 1)
                      if (a + da, b + db) in coarse)
        smooth[(a, b)] = near[len(near) // 2]
    return Ground(smooth, cell, quantile, floor, under)


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


def rhythm_by_span(image, metres_per_pixel: float, spans, lo: float, hi: float,
                   y0: int | None = None, y1: int | None = None,
                   least: float = 3.0) -> list[dict]:
    """The pier rhythm read separately over each stretch of one elevation.

    The question `period(column_signal(image))` cannot answer, and the reason it
    cannot is the shape of the answer rather than its accuracy: it returns one
    number for a whole elevation, so a building with a balconied wing and a blank
    one comes back with a single rhythm and no hint that half of it was outvoted.
    That is not hypothetical. One building read 10.99, 8.48, 14.50 and 11.01 off
    its four elevations, was recorded as "11.00 m, 2 of 4 agree", and was built
    with loggias along all three of its wings -- one of which has none. The two
    dissenting numbers were the blank wing, and they were in the file.

    Four elevations are the wrong axis for this in the first place. They are the
    capture's north, south, east and west; wings run along the building's own u,
    and no one of the four is one wing. So the window is given here in metres
    along the elevation, and the caller -- which knows where its wings start and
    stop, because `wing_cuts` or `plan.decompose` already told it -- asks per
    wing instead of per compass point.

    `spans` are `(from, to)` in metres from the left edge of the image. A span
    narrower than `least` times the longest period looked for gets a reading of
    zero and a reason: autocorrelation over two repeats is not a measurement, and
    a confident number from a span too short to hold one is worse than none.

    Nothing here decides anything. It returns what each stretch read, and a
    caller that finds them disagreeing has learned a fact about the building
    rather than hit a fault in the measurement.
    """
    if metres_per_pixel <= 0:
        raise ValueError("metres per pixel must be positive")
    signal = column_signal(image, y0, y1)
    width = len(signal)
    out = []
    for a, b in spans:
        x0 = max(0, int(round(min(a, b) / metres_per_pixel)))
        x1 = min(width, int(round(max(a, b) / metres_per_pixel)))
        row = {"span": [round(min(a, b), 1), round(max(a, b), 1)],
               "value": 0.0, "score": 0.0, "why": ""}
        if (x1 - x0) * metres_per_pixel < least * hi:
            row["why"] = (f"{(x1 - x0) * metres_per_pixel:.1f} m holds fewer "
                          f"than {least:g} of the longest rhythm looked for "
                          f"({hi:g} m), so there is nothing to correlate")
            out.append(row)
            continue
        found = period(signal[x0:x1], metres_per_pixel, lo, hi)
        row["value"] = round(found.value, 2)
        row["score"] = round(found.score, 3)
        if not found:
            row["why"] = "no rhythm in the window"
        out.append(row)
    return out


def apart(readings, tolerance: float = 0.12) -> list[list[int]]:
    """Group readings that agree, largest group first.

    Two agree within `tolerance` of the larger. Readings of zero -- a span that
    could not be measured -- join no group and are reported as their own, so a
    span nobody could read never counts as agreement.

    Returned as indices, not values, because what the caller has to say is
    *which* stretch disagreed, and the value alone cannot point at a wing.
    """
    groups: list[list[int]] = []
    for i, value in enumerate(readings):
        if value <= 0:
            groups.append([i])
            continue
        for group in groups:
            head = readings[group[0]]
            if head > 0 and abs(value - head) <= tolerance * max(value, head):
                group.append(i)
                break
        else:
            groups.append([i])
    groups.sort(key=lambda g: -len(g))
    return groups


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
    "Grid", "Ground", "Period", "Profile", "Storeys",
    "apart", "column_signal", "detrend", "edge_profile", "floor_lines",
    "ground", "histogram", "median_filter", "peaks", "period", "presence",
    "rhythm_by_span", "row_signal", "silhouette", "skyline", "staircase",
    "storey_height",
]
