"""The slopes a wall can be drawn on and still read as a drawn line.

A wall at an angle rasterises as a staircase, and what the eye reads is not the
angle but the sequence of run lengths along it. That sequence is periodic if and
only if the slope is rational: at 52.43 degrees it runs 2,1,1,2,1,1,2,1,1,1,2,1,1,2
and never repeats, at 53.13 degrees (4:3) it runs 2,1,1 | 2,1,1 | 2,1,1 forever.
The first is what "voxelised vector" looks like; the second is what somebody
building by hand produces without thinking about it.

Periodicity is also what makes copying possible at all. A section repeated every
`k` periods lands on cells related to the first by the integer vector `k*(a, b)`,
and an integer translation is an automorphism of the cell lattice -- the copy is
the same blocks, not a re-rasterisation of the same shape. At an irrational slope
no such vector exists, so eight identical villas come out as eight different
rasters of one villa. `Frame.flipped` already explains the mirror half of this
argument; this module is the translation half.

    slope = lattice.nearest(52.43, span=165.0)
    slope.step        # (3, 4) -- three east, four south
    slope.angle       # 53.13
    slope.motif       # (1, 1, 2)
    slope.period      # 5.0 metres
    slope.error       # +0.70 degrees against what was measured
    slope.cost        # 1.01 metres at each end of a 165 m building

**What is rationed here is the motif, not the accuracy.** A slope of 13:10 is
0.001 degrees off that same building and is no use: its motif is ten runs long,
which is a period, not a rhythm. The rule is `min(|a|, |b|) <= 3`, because that
minimum *is* the number of runs in the motif.

That rule leaves the grid of available angles dense near the axes -- 1:k has a
one-run motif at every k, so 45, 26.57, 18.43, 14.04, 11.31, 9.46 and on down --
and sparse near 45 degrees, where the neighbours are 33.69 (2:3), 45 (1:1) and
53.13 (4:3). The worst a building can be caught out is about 2.9 degrees, which
is four metres at the end of a 165 m building. So the cost is real, bounded, and
has to be measured rather than assumed: `nearest` returns it and the caller
decides.
"""

from __future__ import annotations

import math

# How many runs a motif may have before it reads as noise rather than as rhythm.
# Three: 2,1,1 is a rhythm and 2,1,1,2,1,1,2,1,1,1 is not, and nothing between
# them is worth arguing about.
RUNS = 3

# How far the search reaches along the long leg. 1:96 is 0.6 degrees off the
# axis, which is finer than any frame fitted to a hand-drawn map is entitled to
# claim, and the whole candidate set is a few hundred pairs either way.
REACH = 96


def _reduce(a: int, b: int) -> tuple[int, int]:
    """Canonical integer direction: reduced, and pointing the way a frame does.

    `Frame.fit` puts +u in the eastern half-plane, which in these terms means
    the southward component is never negative and a due-east direction is
    (1, 0) rather than (-1, 0). Two spellings of one direction is how a slope
    comes to be compared against itself and found different.
    """
    if a == 0 and b == 0:
        raise ValueError("a slope needs a direction")
    g = math.gcd(abs(a), abs(b))
    a, b = a // g, b // g
    if b < 0 or (b == 0 and a < 0):
        a, b = -a, -b
    return a, b


class Slope:
    """One integer direction on the block grid, with what it costs to use it.

    `a` counts blocks east and `b` blocks south, matching the frame's angle
    convention: measured from +X towards +Z. `error` and `cost` are filled in
    by `nearest` against the angle that was actually measured; a slope built by
    hand carries None for both and says so.
    """

    __slots__ = ("a", "b", "measured", "error", "cost", "strained")

    def __init__(self, a: int, b: int, measured: float | None = None,
                 span: float | None = None):
        self.a, self.b = _reduce(int(a), int(b))
        self.measured = measured
        # Set by `choose` when nothing clean was affordable and this is only the
        # least bad. Carried on the slope so that no caller can print the number
        # without printing what it means.
        self.strained = False
        if measured is None:
            self.error = None
            self.cost = None
        else:
            self.error = self.angle - measured
            # How far each end of the building moves when the frame is turned
            # about its own centre. The whole trade, in the unit the section
            # grades in.
            self.cost = (None if span is None
                         else abs(span) / 2.0 * abs(math.sin(math.radians(self.error))))

    # -- what it is --------------------------------------------------------

    @property
    def step(self) -> tuple[int, int]:
        """The translation one period long, as a world (dx, dz)."""
        return (self.a, self.b)

    @property
    def angle(self) -> float:
        """Degrees from +X towards +Z, in [0, 180)."""
        return math.degrees(math.atan2(self.b, self.a)) % 360.0

    @property
    def period(self) -> float:
        """Metres from one cell of the motif to the same cell of the next."""
        return math.hypot(self.a, self.b)

    @property
    def runs(self) -> int:
        """How many runs the motif has. Zero means axis-aligned: one long run."""
        return min(abs(self.a), abs(self.b))

    @property
    def motif(self) -> tuple[int, ...]:
        """The run lengths of one period, in order.

        Empty for an axis-aligned slope, which has a single run of no particular
        length -- the cleanest line there is, and the one this sequence cannot
        describe.
        """
        short = self.runs
        if short == 0:
            return ()
        long = max(abs(self.a), abs(self.b))
        return tuple(long * (i + 1) // short - long * i // short
                     for i in range(short))

    def multiple(self, metres: float) -> int:
        """How many whole periods fit in `metres`, at least one.

        The arithmetic every repeat does: a pitch that is not a whole number of
        periods is not a pitch this slope can copy on.
        """
        return max(1, int(round(metres / self.period)))

    def pitch(self, metres: float) -> float:
        """`metres` rounded to the nearest whole number of periods."""
        return self.multiple(metres) * self.period

    def times(self, count: int) -> tuple[int, int]:
        """The translation `count` periods long."""
        return (self.a * count, self.b * count)

    @property
    def perpendicular(self) -> tuple[int, int]:
        """The step across the building: the same period, a quarter turn on.

        A quarter turn maps the cell lattice onto itself, so the across-axis of
        a lattice frame is an integer vector too, of the same length. That is
        what lets a wing be copied across a court as exactly as a section is
        copied along a terrace -- and it is why `across` is not canonicalised
        the way `_reduce` canonicalises a direction: which way it points is the
        caller's, and flipping it would silently copy the other way.
        """
        return (-self.b, self.a)

    def across(self, count: int) -> tuple[int, int]:
        """The translation `count` periods across."""
        return (-self.b * count, self.a * count)

    # -- saying so ---------------------------------------------------------

    def describe(self, span: float | None = None) -> str:
        motif = "-".join(str(n) for n in self.motif) or "flat"
        out = (f"{abs(self.a)}:{abs(self.b)} at {self.angle:.2f} deg, "
               f"motif {motif}, period {self.period:.2f} m")
        if self.measured is not None:
            out += f", {self.error:+.2f} deg off the measured {self.measured:.2f}"
        cost = self.cost if span is None else self.at(span)
        if cost is not None:
            out += f", {cost:.2f} m at the ends"
        return out

    def at(self, span: float) -> float | None:
        """What this slope costs on a building `span` metres long."""
        if self.error is None:
            return None
        return abs(span) / 2.0 * abs(math.sin(math.radians(self.error)))

    def to_json(self) -> dict:
        out = {
            "step": [self.a, self.b],
            "angle": round(self.angle, 3),
            "motif": list(self.motif),
            "period": round(self.period, 3),
            "runs": self.runs,
        }
        if self.measured is not None:
            out["measured"] = round(self.measured, 3)
            out["error"] = round(self.error, 3)
        if self.cost is not None:
            out["cost"] = round(self.cost, 3)
        return out

    @classmethod
    def from_json(cls, data: dict) -> "Slope":
        a, b = data["step"]
        return cls(a, b, measured=data.get("measured"))

    def __eq__(self, other) -> bool:
        return isinstance(other, Slope) and (self.a, self.b) == (other.a, other.b)

    def __hash__(self) -> int:
        return hash((self.a, self.b))

    def __repr__(self) -> str:
        return f"<Slope {self.describe()}>"


def slopes(runs: int = RUNS, reach: int = REACH) -> list[Slope]:
    """Every clean slope, once each.

    Both legs are walked to `reach` because the rule bounds the *shorter* leg:
    1:40 is as clean as 1:1 -- one run of forty -- and a building standing
    nearly square to the world needs exactly that.
    """
    seen: set[tuple[int, int]] = set()
    out: list[Slope] = []
    for short in range(0, runs + 1):
        for long in range(0, reach + 1):
            for pair in ((short, long), (long, short)):
                for sa, sb in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
                    a, b = pair[0] * sa, pair[1] * sb
                    if a == 0 and b == 0:
                        continue
                    key = _reduce(a, b)
                    if min(abs(key[0]), abs(key[1])) > runs:
                        continue
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(Slope(*key))
    return sorted(out, key=lambda s: s.angle)


def candidates(angle: float, runs: int = RUNS, reach: int = REACH,
               span: float | None = None, count: int = 6) -> list[Slope]:
    """The clean slopes nearest `angle`, closest first.

    For the message a refusal prints. A caller that has to choose by hand needs
    to see what it is choosing between, with the price of each beside it --
    which is the same table `derive` prints when the automatic choice is too
    expensive to make on its own.
    """
    near = []
    for slope in slopes(runs, reach):
        # A candidate more than a quarter turn away is the same line pointing
        # backwards, not a nearer angle. Taking one would reverse u, and
        # registration cannot express a half-turn -- see `Frame.fit`.
        if abs(slope.angle - angle) > 90.0:
            continue
        near.append(Slope(slope.a, slope.b, measured=angle, span=span))
    near.sort(key=lambda s: (abs(s.error), s.runs, s.period))
    return near[:count]


def nearest(angle: float, span: float | None = None, runs: int = RUNS,
            reach: int = REACH) -> Slope:
    """The clean slope closest to a measured angle.

    Ties break towards the shorter motif and then the shorter period, because
    between two slopes the same distance away the one that repeats sooner gives
    the build more places to put a joint.

    Closest is rarely what a build wants -- see `choose`. This is the reading
    that goes in a report and in a refusal table.
    """
    found = candidates(angle, runs=runs, reach=reach, span=span, count=1)
    if not found:
        raise ValueError(f"no clean slope within a quarter turn of {angle}")
    return found[0]


# The furthest a snap may move an end of the building, as a share of its own
# length, and as an absolute. Whichever is smaller.
#
# Two numbers because the two failures are at opposite ends of the corpus. A
# forty-metre villa turned by four degrees moves its ends by only 1.4 m -- inside
# the section's tolerance, and visibly wrong beside the drawn plan, because a
# short building's shape is nearly all corner. A five-hundred-metre slab turned
# by a quarter of a degree moves them by 1.1 m and is fine. So the budget is a
# share of the span, capped by the tolerance the section grades at.
BUDGET_SHARE = 0.01
BUDGET_CAP = 2.0


def budget_for(span: float) -> float:
    return min(BUDGET_CAP, abs(span) * BUDGET_SHARE)


def choose(angle: float, span: float, budget: float | None = None,
           pitch: float | None = None,
           runs: int = RUNS, reach: int = REACH) -> Slope:
    """The slope to actually build on: shortest period inside the budget.

    **Accuracy is the constraint here and the period is the objective**, which
    is the other way round from `nearest` and is the whole reason this function
    exists. Sorting by angle alone picks 96:1 for a building standing half a
    degree off square -- a period of ninety-six metres, one jog in the whole
    wall, and nothing that can ever be repeated. The building wanted 1:0, which
    is a tenth of a degree worse and is simply square to the world.

    A short period is what a human builder reaches for and it is what makes a
    repeat possible: a section can only be copied at a whole number of periods,
    so a long period is a building with no places to put a joint.

    `budget` is metres of movement at the ends -- see `budget_for` for why it
    scales with the building. `pitch`, if the building knows what it repeats at,
    keeps only slopes that can express that pitch to within the same budget.

    When nothing is affordable the closest slope comes back with `strained` set
    rather than an exception. The decision to draw straight anyway is the
    building's to make and the section's to grade; refusing here would only move
    it somewhere nobody measures it. `derive` prints the price either way.
    """
    if budget is None:
        budget = budget_for(span)
    near = candidates(angle, runs=runs, reach=reach, span=span, count=1 << 30)
    if not near:
        raise ValueError(f"no clean slope within a quarter turn of {angle}")
    inside = [s for s in near if s.cost is not None and s.cost <= budget]
    if pitch:
        fits = [s for s in inside if abs(s.pitch(pitch) - pitch) <= budget]
        # A pitch no affordable slope can express is a fact worth keeping rather
        # than a filter worth applying: falling back leaves the caller a slope
        # and a printed pitch that does not divide, which is visible, where an
        # empty list would only look like a bad angle.
        inside = fits or inside
    if not inside:
        out = near[0]
        out.strained = True
        return out
    return min(inside, key=lambda s: (s.period, abs(s.error)))


def table(angle: float, span: float | None = None, runs: int = RUNS,
          count: int = 6) -> str:
    """The candidate table, as printed by a refusal."""
    rows = ["    slope   angle    error   motif      period   ends"]
    for slope in candidates(angle, runs=runs, span=span, count=count):
        motif = "-".join(str(n) for n in slope.motif) or "flat"
        cost = "" if slope.cost is None else f"{slope.cost:7.2f} m"
        rows.append(f"    {abs(slope.a)}:{abs(slope.b):<5} {slope.angle:6.2f}  "
                    f"{slope.error:+6.2f}   {motif:<9} {slope.period:6.2f} m {cost}")
    return "\n".join(rows)


__all__ = ["RUNS", "REACH", "Slope", "candidates", "nearest", "slopes", "table"]
