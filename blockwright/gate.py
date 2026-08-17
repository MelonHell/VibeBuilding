"""Grading a build against the reference it was never given.

Scoring a build's *resemblance to the real building* against the map it was
drawn from is a tautology: the build is the map, extruded, so of course they
agree, and an upside-down building would score the same. The Google Earth mesh
is the independent witness, because it is read for numbers -- how tall, how far
along, where does it step -- and never for geometry. A section cut through both
is the one comparison in this pipeline that can come back wrong about the
building.

Scoring *conformance* against the map is a different question and is not a
tautology: whether the machinery between `layout.png` and the schematic kept the
shape it was given is an ordinary verification, and `COUNTS`, `RINGS` and
`<part> is built square` in `grading` are it. The rule is not "never compare
against the map" -- it is that a row must say which of the two it answered. See
`docs/sources.md`, "Сходство и соответствие".

Everything here was a per-building script first. Terra's gate ran to 592 lines
and about sixty of them were about Terra; the rest was apparatus -- registration,
exemptions, station grading, verdict bookkeeping -- that every building needs and
that would otherwise be copied. A copied gate rots differently in each copy,
which is worse than having no gate at all, because the copies go on printing
green.

    g = Gate("terra beachside villas")
    g.fresh(made=[SCHEM], sources=[LAYOUT, MESH])
    reg = Registration.fit(mesh_cloud, build_cloud)
    g.add("registration", reg.agrees(), reg.detail)
    for name, u0, u1 in WINDOWS:
        g.take(section(name, u0, u1, mesh=..., build=..., plan=..., reg=reg))
    sys.exit(0 if g.ok else 1)
"""

from __future__ import annotations

import math
from pathlib import Path

# Two things measured by different means, one of them photogrammetry, agreeing
# to within two metres is agreement. Tighter than this and the check reports the
# capture's noise; looser and a whole storey hides inside the tolerance.
TOLERANCE = 2.0

# Registration keeps only what stands above this fraction of the build's height.
# At ground level the mesh includes road, planting, parked cars and the
# neighbours; fitting against those measures the capture, not the building.
REGISTER_AT = 0.5

# Quantile trimmed off each end before an extent is taken. Photogrammetry sprays
# a few vertices well outside anything real, and one of them sets the extent.
TRIM = 0.005

# How far the two axes of the registration may disagree before the registration
# is not one. If the building is 1.15x in u and 0.98x in v, the fit has latched
# onto something that is not the building and every section after it is noise.
SCALE_AGREEMENT = 0.03

# How far the whole build may be from the reference in size before it stops
# being the same building. Wider than SCALE_AGREEMENT on purpose: the two axes
# of one fit should agree closely, while the build and its reference are allowed
# to differ -- a capture is of the real thing and the build is of a design, and
# a few per cent between them is ordinary. Ten is not ordinary; ten is a rescaled
# crop or a reference of the wrong building.
SCALE_TRUE = 0.10

# A station the plan barely touches is the corner of a rasterised diagonal, not
# a claim that something stands there.
EDGE_CELLS = 2

# How far either side of a window's edge material stops being that window's to
# grade. A wall at 52 degrees rasterises with |cos t| + |sin t| of ambiguity and
# the registration leaves a residual of about the same size, so a metre or so of
# disagreement at a cut is expected rather than remarkable.
SEAM = float(EDGE_CELLS)

# Below this, mesh material is ground clutter rather than building.
CLUTTER = 2.0

# How many stations the length of the building is cut into when two plans are
# compared end for end. Enough that a short wing and a rotunda are separate
# features; few enough that photogrammetry noise averages out inside one.
PROFILE_BINS = 24

# How far a width profile has to differ **from its own reverse** before a flip
# can be read off it. Not how much it varies: the question a flip asks is
# whether this building can be told from itself end for end, and an H, a U, two
# equal wings or a court in the middle all vary enormously and answer *no*. A
# profile compared against its own reverse answers exactly the question asked.
#
# One cell is the scale, for the same reason everywhere else here: a profile is
# the span between two traced faces, each landing within a cell of where the
# drawing put it, so two stations that mirror each other to within a cell are
# the same station read twice. Pearson is scale-free, so the floor has to sit on
# the raw profile in metres and not on the correlation.
#
# Measured on the fixture: the profile that decides the u flip there differs
# from its reverse by 0.5 m against this 1.0, and its correlation still swings
# by 0.54 between the two ways round -- which is the whole of the margin that
# used to be reported as confidence.
ORIENT_SHAPE = 1.0

# How far either way to turn the reference when checking that the two frames
# agree about which way the building points, and how finely. Five degrees is
# past anything a good fit produces and short of the half-turn `orient` handles;
# half a degree is finer than the answer is meaningful to, which is the point --
# a maximum that wanders inside a degree is a plateau and reads as one.
SWEEP = 5.0
SWEEP_STEP = 0.5

# The grid the two footprints are compared on. Coarse deliberately: this is a
# question about a couple of degrees of rotation, not about a wall, and a fine
# grid would spend its time on photogrammetry speckle.
SWEEP_GRID = 120

# How many of the reference's points the sweep carries. A footprint's shape is
# fully drawn by this many on a 120-cell grid, and a capture has twenty times
# as many.
SWEEP_POINTS = 60000

# Fixed, because a measurement that changes between two runs of the same inputs
# is not a measurement.
SWEEP_SEED = 20260801


# -- verdicts --------------------------------------------------------------


class Check:
    """One thing that was asked, and the answer.

    `ok` has three values, not two. `True` and `False` are the answers; `None`
    means the question was asked and nothing here could answer it -- no witness
    was supplied, so there is no evidence either way.

    The third state exists because the second-worst outcome of a grading harness
    is a wrong answer and the worst is a confident one. A build set out on a
    verbal description and graded against nothing at all can be internally
    perfect, and a row that printed PASS for it would be reporting that the
    rasteriser works.
    """

    __slots__ = ("name", "ok", "detail")

    def __init__(self, name: str, ok: bool | None, detail: str = ""):
        self.name = name
        self.ok = None if ok is None else bool(ok)
        self.detail = detail

    def line(self) -> str:
        mark = "----" if self.ok is None else ("PASS" if self.ok else "FAIL")
        return f"[{mark}] {self.name}" + (f": {self.detail}" if self.detail else "")

    def __repr__(self) -> str:
        return f"<{self.line()}>"


class Gate:
    """Every verdict a run produced, and the exit code they add up to."""

    def __init__(self, title: str = ""):
        self.title = title
        self.checks: list[Check] = []

    def add(self, name: str, ok, detail: str = "") -> Check:
        check = Check(name, ok, detail)
        self.checks.append(check)
        return check

    def ungraded(self, name: str, why: str) -> Check:
        """A question nothing supplied could answer.

        Use it wherever a check would otherwise be skipped in silence. A skipped
        row and a passing row look identical in a summary that only counts
        failures, and the difference between them is the difference between a
        graded build and an unexamined one.
        """
        return self.add(name, None, why)

    def extend(self, rows) -> None:
        """Take (name, ok, detail) triples -- what `Schedule.audit` yields."""
        for name, ok, detail in rows:
            self.add(name, ok, detail)

    def take(self, result) -> Check:
        """Take anything with `.name`, `.ok` and `.detail` -- a Section, say."""
        return self.add(result.name, result.ok, result.detail)

    def budget(self, name: str, actual: int, allowed: int, what: str) -> Check:
        """A count that must stay under a watermark.

        A budget with an order of magnitude of slack stops being a check and
        becomes a comment, so these are meant to be lowered onto a finished
        build rather than set once and left.
        """
        return self.add(name, actual <= allowed,
                        f"{actual} {what}, budget {allowed}")

    def witness(self, name: str, ok, detail: str, expected: str = "") -> Check:
        """A row that a building may declare it cannot make agree.

        Some disagreements are facts about the inputs rather than faults. A crop
        from a game map draws a building of different proportions from the
        capture of the real prototype it was modelled on; the registration will
        never agree, and no clip, floor or tolerance will change it.

        Declared, the row goes ungraded with the reason printed. That is not a
        widened tolerance -- every other building still judges the same check on
        the same number, and the verdict for this one is `ungraded` rather than
        `pass`, because the disagreement was explained and not checked.

        Four buildings wrote this same branch by hand in their own `gate.py`,
        thirty lines each including the argument for it. One of them copied it
        from another, which is how a copy comes to rot differently in each copy.
        """
        if expected:
            return self.ungraded(name, f"{detail}. Declared expected: {expected}")
        return self.add(name, ok, detail)

    def fresh(self, made, sources) -> Check:
        """Everything the gate reads must be newer than everything it grades.

        Not a nicety. A stale schematic registers perfectly against the mesh --
        it was a real build of the same building -- and the section goes on
        passing while the code that produced it has changed underneath. The
        grade was true yesterday and is being reported today.
        """
        made = [Path(p) for p in made]
        sources = [Path(p) for p in sources]
        missing = [p.name for p in made if not p.exists()]
        if missing:
            return self.add("freshness", False,
                            "not built yet: " + ", ".join(missing))
        newest = max((p.stat().st_mtime for p in sources if p.exists()),
                     default=0.0)
        stale = [p.name for p in made if p.stat().st_mtime < newest]
        return self.add(
            "freshness", not stale,
            "stale, rebuild first: " + ", ".join(stale) if stale
            else f"{len(made)} artefact(s) newer than every input")

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if c.ok is False]

    @property
    def unanswered(self) -> list[Check]:
        return [c for c in self.checks if c.ok is None]

    @property
    def ok(self) -> bool:
        return not self.failures

    @property
    def verdict(self) -> str:
        """fail | ungraded | pass -- what this run is entitled to claim.

        `ungraded` is not a soft pass. It says every question that could be
        answered was answered right, and that some could not be answered at all,
        which is a different building from one that was checked and stood up.
        """
        if self.failures:
            return "fail"
        return "ungraded" if self.unanswered else "pass"

    def lines(self) -> list[str]:
        out = [c.line() for c in self.checks]
        answered = len(self.checks) - len(self.unanswered)
        if self.failures:
            out.append(f"{len(self.failures)} of {len(self.checks)} checks fail")
            out.extend("  " + c.name for c in self.failures)
        elif self.unanswered:
            out.append(f"all {answered} answerable checks pass, "
                       f"{len(self.unanswered)} could not be answered")
            out.extend("  " + c.name + ": " + c.detail for c in self.unanswered)
        else:
            out.append(f"all {len(self.checks)} checks pass")
        return out

    def report(self) -> list[dict]:
        return [{"name": c.name, "ok": c.ok, "detail": c.detail}
                for c in self.checks]


# -- point clouds ----------------------------------------------------------


class Cloud:
    """(u, v, h) points from one source, in that source's own frame.

    The mesh and the build are the same building measured twice, by different
    means, into different coordinate systems. Putting both into the same shape
    is what lets one function register them and one function compare them.
    """

    __slots__ = ("u", "v", "h")

    def __init__(self, u, v, h):
        self.u = u
        self.v = v
        self.h = h

    def __len__(self) -> int:
        return len(self.u)

    @classmethod
    def from_mesh(cls, mesh, frame, datum: float | None = None,
                  floor: float = CLUTTER) -> "Cloud":
        if datum is None:
            datum = mesh.ground()
        u, v, h = [], [], []
        for i in range(len(mesh)):
            height = mesh.y[i] - datum
            if height <= floor:
                continue
            a, b = frame.to_local(mesh.x[i], mesh.z[i])
            u.append(a)
            v.append(b)
            h.append(height)
        return cls(u, v, h)

    @classmethod
    def from_model(cls, model, frame, skip=()) -> "Cloud":
        """Every non-air cell of a canvas or schematic, at its own height.

        `skip` names blocks that are planting, water or scenery -- things that
        stand on the building rather than being it, and that would otherwise set
        its skyline from the top of a palm.
        """
        from .schem import AIR

        data = getattr(model, "data", None)
        if data is None:
            data = model.blocks
        ignore = {i for i, block in enumerate(model.palette)
                  if block == AIR or block.split("[", 1)[0] in set(skip)}
        area = model.width * model.length
        local = [frame.to_local(i % model.width + 0.5, i // model.width + 0.5)
                 for i in range(area)]
        u, v, h = [], [], []
        for y in range(model.height):
            base = y * area
            for i in range(area):
                if data[base + i] in ignore:
                    continue
                a, b = local[i]
                u.append(a)
                v.append(b)
                # The *top* of the block, not its index. A block in layer y
                # occupies [y, y + 1), so its upper surface is at y + 1, and
                # that is what a capture of the same building measures: the
                # outside of the roof, not the level the roof sits on. Reporting
                # the index costs a metre on every station, in the same
                # direction, which is half the tolerance spent before anything
                # has gone wrong -- and a build that is genuinely a metre short
                # then reads as two and fails for the wrong reason.
                h.append(float(y) + 1.0)
        return cls(u, v, h)

    def top(self) -> float:
        return max(self.h) if self.h else 0.0

    def above(self, height: float) -> list[tuple[float, float]]:
        return [(u, v) for u, v, h in zip(self.u, self.v, self.h) if h > height]


def _profile(points, span: tuple[float, float], bins: int) -> list[float]:
    """How far the thing spreads across, at each station along it.

    The signature of a building's own asymmetry: wide where the wings are, one
    end shorter than the other, a bulge where anything round is. Taken as the
    span in v rather than the count, so a dense patch of photogrammetry does not
    read as a wide one.
    """
    lo, hi = span
    reach = (hi - lo) or 1.0
    low = [None] * bins
    high = [None] * bins
    for u, v in points:
        k = int((u - lo) / reach * bins)
        k = 0 if k < 0 else (bins - 1 if k >= bins else k)
        if low[k] is None or v < low[k]:
            low[k] = v
        if high[k] is None or v > high[k]:
            high[k] = v
    return [0.0 if low[k] is None else high[k] - low[k] for k in range(bins)]


def _agreement(a: list[float], b: list[float]) -> float:
    """Pearson correlation of two profiles, on the same stations.

    A correlation and not a difference, because the two are in different units
    of the same thing: one is a drawing's width in metres and the other is a
    capture's, and they differ by a few per cent everywhere without differing in
    shape anywhere.
    """
    n = min(len(a), len(b))
    if n < 2:
        return 0.0
    mean_a = sum(a[:n]) / n
    mean_b = sum(b[:n]) / n
    top = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    left = sum((a[i] - mean_a) ** 2 for i in range(n))
    right = sum((b[i] - mean_b) ** 2 for i in range(n))
    if left <= 0 or right <= 0:
        return 0.0
    return top / (left * right) ** 0.5


def _asymmetry(profile: list[float]) -> float:
    """How far a profile differs from itself read end for end, in metres.

    The measurement a flip actually rests on. Spread answers "does this
    building vary along its length", which an H, a U and two equal wings all
    answer loudly while being identical either way round -- and a flip read off
    one of those is read off whatever noise breaks the tie. This answers "can
    this building be told from itself reversed", which is the question.

    The worst station pair and not the mean of them: one wing longer than the
    other at one end is a real difference, and averaging it over twenty-three
    stations that match perfectly buries it.
    """
    n = len(profile)
    return max((abs(profile[k] - profile[n - 1 - k]) for k in range(n // 2)),
               default=0.0)


def extent(values, trim: float = TRIM) -> tuple[float, float]:
    """The span of a set of numbers, with the outermost `trim` dropped."""
    values = sorted(values)
    if not values:
        raise ValueError("no points to measure")
    last = len(values) - 1
    return values[int(trim * last)], values[int((1.0 - trim) * last)]


class Registration:
    """The affine that puts the mesh's frame and the build's frame together.

    Both frames were fitted to their own source, so neither their origins nor
    their scales match, and until they are registered a comparison between them
    compares two arbitrary coordinate systems.

    This was four hand-fitted constants once, and they rotted silently: the
    layout was redrawn a little wider, the template's frame origin moved with it,
    and the section carried on reporting a pass while comparing the front row of
    villas against a stretch of car park thirty-five metres away. So it is
    measured from the data on every run.

    `flip_u` and `flip_v` turn the correspondence end for end on that axis.
    They exist because two independent fits of one building can disagree about
    which way round it is, and nothing in the extents says so.

    `Frame.fit` points +u into the eastern half-plane, which settles the
    ambiguity whenever the two bearings are close. It does not settle it when
    they are not: a game map that stands its building on an invented street grid
    can be forty degrees off the real one, both fits are canonical, and they
    land half a turn apart in (u, v). The extents then match perfectly, both
    scales agree, every row passes -- and the section grades each part against
    the part at the opposite corner: the long wing against the short one, the
    rotunda against a stretch of the spine.

    That was found by hand once, from the asymmetry of a plan (short wing at low
    u on one side and at high u on the other), and fixed by rewriting the mesh.
    Rewriting the measuring stick is the wrong place: `orient` below reads the
    flips off the same asymmetry, from the data, on every run.
    """

    __slots__ = ("mesh_u", "mesh_v", "build_u", "build_v",
                 "u_scale", "v_scale", "ceiling", "flip_u", "flip_v")

    def __init__(self, mesh_u, mesh_v, build_u, build_v, ceiling: float,
                 flip_u: bool = False, flip_v: bool = False):
        self.mesh_u = mesh_u
        self.mesh_v = mesh_v
        self.build_u = build_u
        self.build_v = build_v
        self.ceiling = ceiling
        self.flip_u = flip_u
        self.flip_v = flip_v
        self.u_scale = (mesh_u[1] - mesh_u[0]) / (build_u[1] - build_u[0])
        self.v_scale = (mesh_v[1] - mesh_v[0]) / (build_v[1] - build_v[0])

    @classmethod
    def measured(cls, derived: dict) -> "Registration | None":
        """The registration the survey already fitted, out of `derived.json`.

        Two fits of the same pair is one fit too many. `derive` registers the
        plan against the reference on material above a stated height and every
        number in the build comes through it; a gate that fits its own -- on a
        fraction of the *build's* height, which changes whenever the build does
        -- grades those numbers through a different map. On one building the two
        were five metres apart across the width, which is eight per cent of the
        depth, and the section reported that gap as the building's error.

        Three buildings wrote this by hand with the same reasoning before it
        moved here. Prefer it; `fit` is the fallback for a gate whose survey
        never registered anything.
        """
        r = derived.get("registration") or {}
        if not r.get("needed"):
            return None
        found = cls(tuple(r["u"]["mesh"]), tuple(r["v"]["mesh"]),
                    tuple(r["u"]["plan"]), tuple(r["v"]["plan"]),
                    float(r.get("floor", 0.0)))
        found.flip_u = "u" in (r.get("turned") or "") or bool(r.get("flip_u"))
        found.flip_v = "v" in (r.get("turned") or "") or bool(r.get("flip_v"))
        if r.get("turned") == "half a turn":
            found.flip_u = found.flip_v = True
        elif r.get("turned") == "mirrored along u":
            found.flip_u, found.flip_v = True, False
        elif r.get("turned") == "mirrored across v":
            found.flip_u, found.flip_v = False, True
        elif r.get("turned") == "the same way round":
            found.flip_u = found.flip_v = False
        return found

    @classmethod
    def fit(cls, mesh: Cloud, build: Cloud, at: float = REGISTER_AT,
            trim: float = TRIM, plan: "Plan | None" = None) -> "Registration":
        """Fit the two extents, and work out which way round they go.

        `plan` is what makes the second half possible. Without it the fit has
        only the extents to go on, and an extent is the same end for end.
        """
        if not len(build):
            raise ValueError("nothing built")
        ceiling = at * build.top()
        high_mesh = mesh.above(ceiling)
        high_build = build.above(ceiling)
        if not high_mesh:
            raise ValueError(
                f"the mesh has nothing above {ceiling:.1f} m; either the datum "
                "is wrong or the clip is of the wrong building")
        found = cls(
            extent([p[0] for p in high_mesh], trim),
            extent([p[1] for p in high_mesh], trim),
            extent([p[0] for p in high_build], trim),
            extent([p[1] for p in high_build], trim),
            ceiling,
        )
        if plan is not None:
            found.flip_u, found.flip_v = found.orient(high_mesh, plan)[:2]
        return found

    def orient(self, points, plan: "Plan") -> tuple[bool, bool, dict]:
        """Which way round the mesh goes, read off the asymmetry of the plan.

        Every one of the four ways to lay one rectangle on another is tried, and
        the winner is the one that puts the most of the reference's material
        where the plan claims a building. A symmetric building scores the same
        four times and the answer does not matter; an asymmetric one -- which is
        every building with a wing shorter than the other, or anything round at
        one end -- separates them clearly.

        **A flip is only taken from a profile that can be told from its own
        reverse.** The two terms are independent -- mirroring u reverses the
        order of the `along` bins and leaves every `across` span untouched, and
        the other way round -- so each axis is decided by exactly one profile,
        and a profile that reads the same backwards is deciding nothing. On a
        plan whose two strips both run the whole length, `along` is the same
        width at every station to within the rasterisation of its own edges, and
        correlating that jitter against the reference's jitter returns a number
        with a sign and no meaning. It came back at +0.402 one way round and
        -0.136 the other on the fixture, which reads in the table as a margin of
        0.537 and is not a margin at all.

        The test is `_asymmetry` against `ORIENT_SHAPE`, and it is deliberately
        not "does the profile vary": an H, a U, two equal wings and a court in
        the middle all vary by tens of metres and are all identical end for end,
        so a spread test would pass them straight through to the same coin toss
        with a confident-looking margin on top. Below the floor the flip stays
        where the two canonical fits put it -- `Frame.fit` points +u into the
        eastern half-plane, which settles the ambiguity whenever the two
        bearings are close -- and the table says which axis was left
        undetermined.

        That mattered nothing while a symmetric building was read the same
        building either way round. It stopped being nothing when the plan grew
        parts the drawn source never drew: `Survey.assemble` places those
        through this correspondence, and a flip taken off jitter puts a pool
        house at the far end of the site with every row downstream agreeing,
        because every row reads the reference through this same fit.

        Returns the flips and the whole score table, so that a close call is
        visible rather than decided in silence.
        """
        # Scored on the *shape* of the building along its length, not on how
        # many points land inside it. A long building is mostly the same all the
        # way down, so counting hits separates the four barely -- on one real
        # case it came out 0.670 against 0.647, which is a decision taken on
        # noise. What actually distinguishes one end from the other is where the
        # building is wide and where it is narrow: a short wing, a rotunda, a
        # court that stops. That profile is what is compared here.
        # Both ways round, because one profile can only see one axis. How wide
        # the building is at each station along it says nothing about which side
        # is which -- a mirror across the axis leaves every width untouched --
        # so the same measurement is taken along the other axis as well, and the
        # two are added. Each flip is then decided by the profile that can see
        # it, and neither is decided by a coin.
        along = _profile(plan.points, self.build_u, PROFILE_BINS)
        across = _profile([(v, u) for u, v in plan.points], self.build_v,
                          PROFILE_BINS)
        scores: dict[tuple[bool, bool], float] = {}
        for fu in (False, True):
            for fv in (False, True):
                trial = Registration(self.mesh_u, self.mesh_v, self.build_u,
                                     self.build_v, self.ceiling, fu, fv)
                put = [(trial.to_build_u(u), trial.to_build_v(v))
                       for u, v in points]
                scores[(fu, fv)] = (
                    _agreement(along, _profile(put, self.build_u, PROFILE_BINS))
                    + _agreement(across, _profile([(v, u) for u, v in put],
                                                  self.build_v, PROFILE_BINS)))
        best = max(scores, key=scores.get)
        # Each axis kept only where its own profile can be told from its own
        # reverse, which is the question a flip asks and the only one. Compared
        # station by station against its mirror, zeros included: a station the
        # plan does not reach is the shape of a court and not a gap in the
        # reading, and a court off the middle is exactly what separates one end
        # of a building from the other.
        shape = (_asymmetry(along), _asymmetry(across))
        blind = ("u" if shape[0] < ORIENT_SHAPE else "") + \
                ("v" if shape[1] < ORIENT_SHAPE else "")
        best = (best[0] and "u" not in blind, best[1] and "v" not in blind)
        ranked = sorted(scores.values(), reverse=True)
        table = {f"{'u' if k[0] else '-'}{'v' if k[1] else '-'}": round(s, 3)
                 for k, s in scores.items()}
        # Which axes the profiles could not decide, and by how much they missed
        # -- carried into `derived.json` because `Survey.assemble` refuses to
        # place a part on an axis nothing settled.
        table["undetermined"] = blind
        table["shape"] = [round(shape[0], 2), round(shape[1], 2)]
        table["shape_floor"] = ORIENT_SHAPE
        # How far ahead the winner is, over all four. Read it beside
        # `undetermined` and not on its own: a margin is only evidence about an
        # axis whose profile carries shape, and the one this used to be trusted
        # for is the one it cannot speak to. A wide margin between two ways
        # round that differ only on a flat axis is jitter with a decimal point.
        table["margin"] = round(ranked[0] - ranked[1], 3)
        return best[0], best[1], table

    def square(self, points, plan: "Plan", sweep: float = SWEEP,
               step: float = SWEEP_STEP, grid: int = SWEEP_GRID) -> dict:
        """Whether the two frames are turned the same way, to within a degree.

        The registration scales and shifts each axis and never rotates. That is
        right, because the rotation is already in the two frames -- each was
        fitted to its own source -- and it is right only if those two fits agree
        about which way the building points. When they do not, nothing says so:
        the extents simply stretch to cover a footprint that is lying across
        them, both scales stay plausible, and the section reports the error as
        noise spread evenly over every station. That is the most expensive kind
        of wrong this pipeline can be, because it looks like photogrammetry.

        So: turn the reference through a few degrees either way, re-fit, and
        measure how much of the plan it covers each time. Reported, never
        applied. If the answer is that the fit would be better a few degrees
        round, the fix is the clip or the capture's heading, not a quiet
        rotation inside the measuring stick.

        Scored by mask overlap and not by the width profile `orient` uses.
        Profile agreement rises as a shape is smeared across its own bins, so it
        is maximised at both ends of the sweep and is useless for an angle;
        overlap falls off on both sides of the truth, which is what a metric for
        this has to do.
        """
        from .mask import Mask, iou

        u0, u1 = self.build_u
        v0, v1 = self.build_v
        du = (u1 - u0) or 1.0
        dv = (v1 - v0) or 1.0

        def rasterise(pairs, close: float = 2.0) -> Mask:
            mask = Mask(grid, grid)
            for u, v in pairs:
                x = int((u - u0) / du * (grid - 1))
                z = int((v - v0) / dv * (grid - 1))
                if 0 <= x < grid and 0 <= z < grid:
                    mask.set(x, z)
            return mask.close(close) if close else mask

        # Thinned before the sweep. This is a question about a couple of degrees
        # of rotation of a whole footprint, and a footprint's shape is drawn
        # well enough by a fraction of the points on a 120-cell grid; carrying
        # half a million through twenty-one rotations costs more than the rest
        # of the survey.
        #
        # Sampled at random, from a fixed seed, and not by taking every nth. An
        # OBJ's vertices arrive in the order the exporter wrote them, which is
        # tile by tile, so a stride is a comb through the building and drops
        # whole pieces of it -- on one capture that moved the answer by four
        # degrees. The seed keeps the run reproducible, which every other number
        # here already is.
        if len(points) > SWEEP_POINTS:
            import random

            points = random.Random(SWEEP_SEED).sample(list(points),
                                                      SWEEP_POINTS)

        want = rasterise(plan.points)
        cu = sum(p[0] for p in points) / len(points)
        cv = sum(p[1] for p in points) / len(points)

        rows = []
        for i in range(int(-sweep / step), int(sweep / step) + 1):
            angle = i * step
            rad = math.radians(angle)
            cos, sin = math.cos(rad), math.sin(rad)
            turned = [((u - cu) * cos - (v - cv) * sin + cu,
                       (u - cu) * sin + (v - cv) * cos + cv)
                      for u, v in points]
            trial = Registration(extent([p[0] for p in turned]),
                                 extent([p[1] for p in turned]),
                                 (u0, u1), (v0, v1), self.ceiling,
                                 self.flip_u, self.flip_v)
            got = rasterise([(trial.to_build_u(u), trial.to_build_v(v))
                             for u, v in turned])
            rows.append((angle, iou(want, got)))

        best = max(rows, key=lambda r: r[1])
        here = dict(rows)[0.0]
        return {
            "best": round(best[0], 2),
            "overlap": round(best[1], 3),
            "as_fitted": round(here, 3),
            "gain": round(best[1] - here, 3),
            "sweep": [[a, round(s, 3)] for a, s in rows],
        }

    def to_mesh_u(self, u: float) -> float:
        out = self.mesh_u[0] + (u - self.build_u[0]) * self.u_scale
        return self.mesh_u[0] + self.mesh_u[1] - out if self.flip_u else out

    def to_build_u(self, u: float) -> float:
        if self.flip_u:
            u = self.mesh_u[0] + self.mesh_u[1] - u
        return self.build_u[0] + (u - self.mesh_u[0]) / self.u_scale

    def to_build_v(self, v: float) -> float:
        if self.flip_v:
            v = self.mesh_v[0] + self.mesh_v[1] - v
        return self.build_v[0] + (v - self.mesh_v[0]) / self.v_scale

    def to_mesh_v(self, v: float) -> float:
        out = self.mesh_v[0] + (v - self.build_v[0]) * self.v_scale
        return self.mesh_v[0] + self.mesh_v[1] - out if self.flip_v else out

    @property
    def turned(self) -> str:
        """How the two frames stand to each other, in words."""
        if self.flip_u and self.flip_v:
            return "half a turn"
        if self.flip_u:
            return "mirrored along u"
        if self.flip_v:
            return "mirrored across v"
        return "the same way round"

    @property
    def disagreement(self) -> float:
        return abs(self.u_scale - self.v_scale)

    def agrees(self, within: float = SCALE_AGREEMENT) -> bool:
        return self.disagreement <= within

    @property
    def stretch(self) -> float:
        """How far the build is from the reference in size, as a fraction.

        `agrees` asks whether the two axes tell the same story; this asks
        whether that story is 1:1. They are different questions and only the
        first was ever asked: a build 25 per cent smaller than the reference on
        *both* axes registers perfectly -- 1.25 and 1.25, no disagreement at all
        -- the section then compares heights through a normalised u and v, and
        the gate prints a pass.

        A whole-building scale error has exactly two causes, and both are
        silent. A map crop rescaled before it was cropped: the metre-per-pixel
        everything downstream assumes is no longer one, and nothing else in the
        pipeline can see that. And a reference that is not this building --
        another block of the same design, a different phase of it -- which is a
        real and legitimate case, and one that has to be *declared* rather than
        absorbed.
        """
        return max(abs(self.u_scale - 1.0), abs(self.v_scale - 1.0))

    def scaled(self, within: float) -> bool:
        return self.stretch <= within

    @property
    def detail(self) -> str:
        return (f"u {self.u_scale:.3f} vs v {self.v_scale:.3f}, differ by "
                f"{self.disagreement:.3f}")

    def lines(self) -> list[str]:
        return [
            f"on material above {self.ceiling:.0f} m"
            + ("" if not (self.flip_u or self.flip_v)
               else f"; the reference stands {self.turned} to the plan")
            + (f"; the build is {1 / self.u_scale:.2f}x by {1 / self.v_scale:.2f}x "
               "of the reference" if self.stretch > 0.02 else ""),
            f"  u: mesh {self.mesh_u[0]:6.1f}..{self.mesh_u[1]:6.1f} -> "
            f"build {self.build_u[0]:6.1f}..{self.build_u[1]:6.1f}  "
            f"scale {self.u_scale:.3f}",
            f"  v: mesh {self.mesh_v[0]:6.1f}..{self.mesh_v[1]:6.1f} -> "
            f"build {self.build_v[0]:6.1f}..{self.build_v[1]:6.1f}  "
            f"scale {self.v_scale:.3f}",
        ]


# -- what the plan claims ---------------------------------------------------


class Plan:
    """The drawn parts as points, for asking "does the map claim anything here".

    Built from the *parts* -- the components of mass minus lines -- and not from
    the whole drawing. A drawn line asserts only that something ends there, so
    grading against a one-pixel outline has the mesh answering with
    photogrammetry over an open court. `plan.decompose` has already made the
    distinction; nothing real is lost by using it, because a line that runs over
    a genuine footprint lies inside a part anyway.
    """

    __slots__ = ("points", "cells")

    def __init__(self, points):
        self.points = list(points)
        self.cells = {(int(round(u)), int(round(v))) for u, v in self.points}

    @classmethod
    def from_parts(cls, parts, frame) -> "Plan":
        return cls([frame.to_local(x + 0.5, z + 0.5)
                    for p in parts for x, z in p.mask.cells()])

    def claims(self, u0: float, u1: float,
               edge_cells: int = EDGE_CELLS) -> set[int]:
        """The v stations the plan really claims between u0 and u1."""
        tally: dict[int, int] = {}
        for u, v in self.points:
            if u0 <= u < u1:
                k = int(round(v))
                tally[k] = tally.get(k, 0) + 1
        return {k for k, n in tally.items() if n > edge_cells}

    def covers(self, u: float, v: float) -> bool:
        return (int(round(u)), int(round(v))) in self.cells


# -- exemptions -------------------------------------------------------------


class Exemption:
    """Stations a section will not grade, and why not.

    A station can disagree with the mesh because the build is wrong, or because
    the mesh is not a witness there. The second case is real and permanent, so
    it is named and printed every run whether it fires or not: an exemption that
    stops covering anything is as much a signal as one that fires -- it means
    the thing it was written about has moved.

    `sign` keeps an exemption from doing more than it was written for. An
    exemption for a part we deliberately build taller than the mesh must not
    also excuse that part coming out short.
    """

    __slots__ = ("name", "why", "stations", "sign")

    def __init__(self, name: str, why: str, stations, sign: int = 0):
        self.name = name
        self.why = why
        self.stations = set(stations)
        self.sign = sign        # +1 excuses only taller, -1 only shorter

    def covers(self, k: int, d: float) -> bool:
        return k in self.stations and (self.sign == 0 or self.sign * d > 0)

    def line(self, hits: int) -> str:
        return (f"  ~ {self.name}: {hits} station(s) not graded -- {self.why}"
                + ("" if hits else "  [covers nothing]"))


class Band:
    """What an exemption factory gets to look at: one window, already measured."""

    __slots__ = ("name", "u0", "u1", "mesh_top", "build_top", "registration")

    def __init__(self, name, u0, u1, mesh_top, build_top, registration):
        self.name = name
        self.u0 = u0
        self.u1 = u1
        self.mesh_top = mesh_top
        self.build_top = build_top
        self.registration = registration


def taller_than_the_mesh(part, why: str, name: str = "cone"):
    """A drawn disc built taller than photogrammetry saw it -- a cone, a spire.

    Photogrammetry drops thin tapering tips: there is nothing for the matcher to
    correlate on a surface that narrows to a point, so the capture rounds it off
    or loses it. Where the height was chosen from the drawn radius and the
    photographs rather than measured off the mesh, the mesh is not the witness,
    and the section should say so by name instead of by a wider tolerance.

    The range is the drawn circle -- centre and fitted radius, not the bounding
    box, which a clipped disc drags outwards.
    """
    def make(band: Band) -> Exemption:
        if part is None:
            return Exemption(name, "nothing drawn for it", set(), +1)
        if not (band.u0 <= part.centre[0] < band.u1):
            return Exemption(name, "not drawn in this window", set(), +1)
        cv, r = part.centre[1], part.radius
        reg = band.registration
        covered = {k for k in band.mesh_top
                   if cv - r <= reg.to_build_v(k + 0.5) <= cv + r}
        return Exemption(name,
                         f"drawn disc, build v {cv - r:.1f}..{cv + r:.1f}; "
                         + why, covered, +1)
    return make


def next_lot(name: str = "next lot"):
    """Stations past the far end of the mesh's own copy of this building.

    A crop of a photogrammetry capture does not stop where the building does.
    Past the last station with material above the registration ceiling, what the
    mesh is describing is the neighbour's roof, and a roof on another lot is not
    evidence about this one.

    Only the outer run is covered, and it is the outer run by construction: the
    edge is the *last* station standing above the ceiling, so everything past it
    is contiguous with the crop boundary. A low station short of the edge is our
    building being low, which is exactly what the section exists to notice.
    """
    def make(band: Band) -> Exemption:
        reg = band.registration
        if not band.mesh_top:
            return Exemption(name, "no mesh material in this window", set())
        ours = [k for k, h in band.mesh_top.items() if h > reg.ceiling]
        if not ours:
            return Exemption(
                name, f"no material above the registration ceiling of "
                      f"{reg.ceiling:.0f} m", set())
        edge = max(ours)
        return Exemption(
            name, f"the mesh's building ends at build v "
                  f"{reg.to_build_v(edge + 0.5):.1f}; past it is another lot's "
                  f"roof, below the {reg.ceiling:.0f} m registration ceiling",
            {k for k in band.mesh_top if k > edge})
    return make


def station_of(reg, mesh_v: float) -> int:
    """Which station a v in the reference's own coordinates belongs to.

    The section bins the reference by whole metres **of the mesh** -- `int(v)`
    -- and nothing else in the pipeline does. A probe that measures the same
    building in plan metres and reports a run as `v 34..40` is speaking a
    different language: the two frames differ by a few per cent, and over fifty
    metres that is a whole station. It does not show up as a wrong number
    anywhere, because both numbers are right; it shows up as a station failing
    at every edge of every step, which is where two buildings spent a day.

    So the convention lives here and both sides call it. A probe handing the
    build a step boundary hands it a station, not a metre.
    """
    return int(mesh_v)


def build_bin(reg, station: int) -> int:
    """The build's v that a reference station is compared against.

    The other half of the same convention. A mesh band runs `[k, k+1)` and is
    read at its centre, so the build is looked for at `round(to_build_v(k +
    0.5))` -- binning it at `to_build_v(k)` puts the two skylines half a metre
    apart in v, which on a sloping roof is most of a station's worth of height
    and fails one that agreed.
    """
    return int(round(reg.to_build_v(station + 0.5)))


def one_station_off(why: str, name: str = "one station off",
                    drop: float | None = None,
                    tolerance: float = TOLERANCE):
    """A station the reference disagrees with **both** its neighbours about.

    Two failures of the method look identical in the numbers and neither is the
    build being wrong.

    A hole in the capture: photogrammetry loses a facade it had too few views
    of and reads 13.7 m at one station with 29 to 36 m at every station beside
    it. The building has no such notch -- nothing does -- and the give-away is
    that the two neighbours agree with each other.

    A step on the wrong side of a line: the section bins the reference by whole
    metres of its own frame, and where the plan and the reference differ by a
    few per cent a measured tread lands one station over. The build is right and
    is being compared against the station next door, which is why this also
    covers a station whose build height matches one of its neighbours' mesh.

    Both are properties of the method, not of the building, so this belongs
    here and not in a building's own gate. What it deliberately does not cover
    is a run: three stations in a row out of tolerance is a shape a building can
    have, and the section should say so.

    `drop` defaults to twice the section's tolerance rather than to a typed
    figure. It was 4.0 m for as long as every building graded at 2.0, which made
    the relationship invisible: a building that tightens its section to a metre
    inherited an exemption written for a section twice as loose, and one that
    widened to three inherited one that fires on ordinary noise. The pair of
    them is the same decision written once.
    """
    if drop is None:
        drop = 2.0 * tolerance

    def make(band: Band) -> Exemption:
        tops = band.mesh_top
        if not tops:
            return Exemption(name, "no mesh material in this window", set())
        covered = set()
        for k, here in tops.items():
            before, after = tops.get(k - 1), tops.get(k + 1)
            if before is None or after is None:
                continue
            # Isolated: this station argues with both sides and they agree.
            if (abs(here - before) > drop and abs(here - after) > drop
                    and abs(before - after) <= drop):
                covered.add(k)
                continue
            # Or the build agrees with a neighbouring station instead of this
            # one, which is a tread that fell across a station line.
            mine = band.build_top.get(k)
            if mine is None:
                continue
            if (abs(mine - here) > drop
                    and min(abs(mine - before), abs(mine - after)) <= drop):
                covered.add(k)
        return Exemption(name, why, covered)
    return make


def on_a_step(why: str = "", name: str = "on a step in the reference",
              reach: int = 2, drop: float | None = None,
              tolerance: float = TOLERANCE):
    """Stations sitting on a step in the reference's own reading.

    The other half of `one_station_off`, and the same arithmetic seen from the
    other end. That one covers a station the reference is alone about; this
    covers the *seam* between two flat runs the reference is perfectly sure
    about -- a roof at two levels, the edge of a plant house, a parapet that
    steps.

    The cause is not photogrammetry. The section bins the reference in the
    reference's own metres and the build in the build's, and the affine between
    them carries a scale of a per cent or so and an origin a metre or two off,
    so a station's window on one side is about a metre out of step with the same
    station's window on the other. On a flat run that costs nothing: both
    windows read the same plane. Across a three-metre step it costs the whole
    step, in whichever direction the offset happens to fall, and the number the
    row prints is the offset rather than the build.

    Measured off the reference alone, so it cannot be tuned by what the build
    did: a station within `reach` of a place where the reference's own reading
    jumps by more than `drop` is on a step. It covers nothing on a flat run,
    however wrong the build is there -- which is the point. The roof levels
    themselves, the parapet and every box standing on the roof are still graded
    at every station between the steps.

    `reach` is two stations and not one because that is the size of the offset:
    about a metre of registration residual on top of a one-metre station, so a
    step can reach the station next but one. Wider starts covering flat runs,
    which is where this row does its work.

    Written twice in one building's own gate before it moved here, and it was
    never that building's: any building whose roof steps by more than the
    tolerance has it, and every one of them would otherwise reach for a wider
    tolerance instead -- which excuses the flat runs too, silently.
    """
    if drop is None:
        drop = tolerance
    why = why or (
        f"the reference's own reading steps by more than {drop:.0f} m between "
        "this station and one beside it. The two sources bin this building "
        "about a metre out of step with each other, so across a step of that "
        "size the row reads the offset rather than the build. Every station on "
        "a flat run is graded")

    def make(band: Band) -> Exemption:
        tops = band.mesh_top
        if not tops:
            return Exemption(name, "no mesh material in this window", set())
        steps = {k for k in tops
                 if any(abs(tops[k] - tops[j]) > drop
                        for j in (k - 1, k + 1) if j in tops)}
        covered = {k for k in tops
                   if any(j in steps for j in range(k - reach, k + reach + 1))}
        return Exemption(name, why, covered)
    return make


def ribbed(why: str = "", name: str = "the capture's own ribbing",
           same: float | None = None, tolerance: float = TOLERANCE):
    """A station the reference dips at that both its neighbours agree about.

    `one_station_off` is this shape at twice the tolerance and its docstring
    makes the argument: a station the reference argues with both its neighbours
    about, where the two neighbours agree with each other, is a property of the
    method rather than of the building. Nothing has a one-metre-wide notch in
    it.

    The same shape turns up smaller, and then that check will not see it. A
    photogrammetric surface over a flat equipment roof saws: one capture reads
    52.5, 50.7, 52.5, 50.6, 52.5 on consecutive stations -- a 1.9 m saw with a
    one-metre period, under the four metres `one_station_off` asks for and over
    the two the section grades at. The build lays a flat top, correctly, and
    comes out half a metre under the tall rows and two metres over the dips.

    Deliberately narrower than `one_station_off`: the two neighbours have to
    agree with each other to within `same`, the station has to be a *dip* rather
    than a spike, and one station is covered at a time. Two dips in a row is a
    shape and stays graded.

    `same` defaults to a bit over half the tolerance -- under the amplitude of
    the saw and well under a course of any building's storey.
    """
    if same is None:
        same = 0.6 * tolerance
    why = why or (
        "the reference reads this station lower than the two beside it and "
        f"those two agree with each other to {same:.1f} m. That is the ribbing "
        "of a photogrammetric surface over a flat roof rather than a notch in "
        "the building -- nothing is one station wide. Two such stations in a "
        "row would be a shape and are not covered")

    def make(band: Band) -> Exemption:
        tops = band.mesh_top
        if not tops:
            return Exemption(name, "no mesh material in this window", set())
        covered = set()
        for k, here in tops.items():
            before, after = tops.get(k - 1), tops.get(k + 1)
            if before is None or after is None:
                continue
            if (abs(before - after) <= same
                    and here < min(before, after) - same):
                covered.add(k)
        return Exemption(name, why, covered)
    return make


def capture_hole(name: str = "capture hole", under: float = 2.0):
    """Stations where the reference holds ground and nothing else.

    A capture is flown, and an aircraft cannot see into a courtyard, a light
    well, or the space under a canopy. What photogrammetry reconstructs there is
    the surrounding roofs and then a smear at the ground, because it never had a
    view of what is between them. The section then compares a deck the build
    lays at three metres against a station the reference reads at nothing, and
    reports it as three metres of error.

    That is not a disagreement about a height, it is the absence of a witness,
    and the two want saying differently -- which is the whole reason the verdict
    has three states rather than two.

    Deliberately narrow: only where the reference has *nothing* above `under`
    metres. A court the capture did see, however roughly, is graded.
    """
    def make(band: Band) -> Exemption:
        tops = band.mesh_top
        if not tops:
            return Exemption(name, "no mesh material in this window", set())
        covered = {k for k, h in tops.items() if h <= under}
        return Exemption(
            name,
            f"the capture holds nothing above {under:.1f} m at these stations, "
            "which is a court or a well it was never flown into rather than a "
            "height it disagrees about",
            covered)
    return make


# -- the section ------------------------------------------------------------


class Station:
    """One v station of a section: what each reference says stands there."""

    __slots__ = ("k", "v", "mesh", "build", "state", "excuse")

    def __init__(self, k, v, mesh, build, state, excuse=None):
        self.k = k
        self.v = v
        self.mesh = mesh
        self.build = build
        self.state = state          # ok | over | under | empty | exempt
        self.excuse = excuse

    @property
    def diff(self) -> float | None:
        return None if self.build is None else self.build - self.mesh

    def line(self) -> str:
        if self.build is None:
            return f"{self.v:6.1f} {self.mesh:6.1f} {'--':>6} {'EMPTY':>6}  <--"
        tail = {"ok": "", "exempt": f"   ~ {self.excuse}"}.get(self.state, "  <--")
        return (f"{self.v:6.1f} {self.mesh:6.1f} {self.build:6.1f} "
                f"{self.diff:+6.1f}{tail}")


class Section:
    """The verdict on one window, and every station that went into it."""

    __slots__ = ("name", "u0", "u1", "mesh_u0", "mesh_u1", "stations",
                 "skipped", "tolerance", "exemptions", "hits", "dropped")

    def __init__(self, name, u0, u1, mesh_u0, mesh_u1, stations, skipped,
                 tolerance, exemptions, hits, dropped=()):
        self.name = name
        self.u0 = u0
        self.u1 = u1
        self.mesh_u0 = mesh_u0
        self.mesh_u1 = mesh_u1
        self.stations = stations
        self.skipped = skipped
        self.tolerance = tolerance
        self.exemptions = exemptions
        self.hits = hits
        self.dropped = list(dropped)

    @property
    def graded(self) -> int:
        return len(self.stations)

    @property
    def misses(self) -> int:
        return sum(1 for s in self.stations if s.state in ("over", "under", "empty"))

    @property
    def empty(self) -> int:
        return sum(1 for s in self.stations if s.state == "empty")

    @property
    def worst(self) -> float:
        return max((abs(s.diff) for s in self.stations
                    if s.diff is not None and s.state != "exempt"), default=0.0)

    @property
    def ok(self) -> bool | None:
        """True, False, or None when the window graded nothing.

        None rather than False, for the same reason `Check` has three states: a
        window where the reference offered no station and the plan claims
        nothing is a question that could not be asked, and calling that a
        failure sends somebody looking for a fault in the build. It is a fault
        in the window, or in the clip, and the detail below says which.
        """
        if self.graded == 0:
            return None
        return self.misses == 0

    @property
    def detail(self) -> str:
        if self.graded == 0:
            return (f"nothing to grade here: the reference offered "
                    f"{self.skipped} station(s) and the plan claims none of "
                    "them. Either this window is past the end of the building, "
                    "or the clip has no material over it. Run the gate with "
                    "--profile: it prints both skylines at every station here, "
                    "with the reason against each one that was dropped.")
        return (f"{self.graded} stations, {self.misses} outside "
                f"{self.tolerance:.0f} m ({self.empty} with nothing built), "
                f"{sum(self.hits.values())} exempt, worst {self.worst:.1f} m "
                "of those graded")

    def lines(self) -> list[str]:
        out = [
            f"{self.name}: build u {self.u0:.0f}..{self.u1:.0f} "
            f"(mesh u {self.mesh_u0:.0f}..{self.mesh_u1:.0f}), highest point at "
            f"each v; {self.graded} stations graded, {self.skipped} skipped "
            "where the plan draws nothing",
            f"{'v':>6} {'mesh':>6} {'build':>6} {'diff':>6}",
        ]
        out.extend(s.line() for s in self.stations)
        out.extend(e.line(self.hits[e.name]) for e in self.exemptions)
        return out

    def profile(self) -> list[str]:
        """Both skylines over this window, every station, in one unit.

        `lines` is the verdict and shows only what was graded. This is the
        measurement the verdict was made from, and the difference between the
        two is the question a person asks after two runs that did not move the
        report: *is the window in the wrong place, or is the building right?* A
        section that grades forty stations of ninety-six says so in one number
        and cannot say which forty; the reader moves the window, gets another
        number, and cycles. Both profiles side by side, with a reason against
        every station that did not reach the verdict, answers it by being read.
        The rows are sorted by v and not by which side offered them, because a
        run of `--` down one column at one end of the building is the shape the
        answer usually has, and it is only a shape if the table is in order.

        Printed under `--profile` and never otherwise: it is a line per station
        on both sides, the right length for the person holding this problem and
        far too long for anybody else.
        """
        rows: list[tuple[float, str]] = []
        for s in self.stations:
            mark = "" if s.state == "ok" else s.state
            if s.excuse:
                mark = f"{s.state} ~ {s.excuse}"
            rows.append((s.v, self._row(s.v, s.mesh, s.build, mark)))
        for v, mesh, built, why in self.dropped:
            rows.append((v, self._row(v, mesh, built, f"dropped: {why}")))
        rows.sort(key=lambda r: r[0])
        out = [
            f"{self.name}: every station over build u {self.u0:.0f}..{self.u1:.0f}"
            f" (mesh u {self.mesh_u0:.0f}..{self.mesh_u1:.0f}), "
            f"{self.graded} graded and {len(self.dropped)} dropped",
            f"{'v':>6} {'mesh':>6} {'build':>6} {'diff':>6}  state",
        ]
        out.extend(line for _, line in rows)
        # A superset of `lines`, exemptions included, so the flag swaps the table
        # out rather than printing every graded station twice.
        out.extend(e.line(self.hits[e.name]) for e in self.exemptions)
        return out

    @staticmethod
    def _row(v: float, mesh: float | None, built: float | None,
             mark: str) -> str:
        # A station only one side offered prints `--` rather than a zero: the
        # whole use of this table is telling "nothing here" from "nothing tall
        # here", and a zero reads as a measurement.
        m = "--" if mesh is None else f"{mesh:.1f}"
        b = "--" if built is None else f"{built:.1f}"
        d = "--" if mesh is None or built is None else f"{built - mesh:+.1f}"
        return f"{v:6.1f} {m:>6} {b:>6} {d:>6}  {mark}".rstrip()

    def report(self) -> dict:
        return {
            "name": self.name,
            "ok": self.ok,
            "detail": self.detail,
            "window": [self.u0, self.u1],
            "graded": self.graded,
            "skipped": self.skipped,
            "misses": self.misses,
            # Counted by reason and not listed: the list is the `--profile`
            # table and belongs on a terminal, but the counts are what tell a
            # reader of the report that there is a table worth asking for.
            "dropped": {why: sum(1 for d in self.dropped if d[3] == why)
                        for why in sorted({d[3] for d in self.dropped})},
            "empty": self.empty,
            "worst": round(self.worst, 2),
            "tolerance": self.tolerance,
            "exemptions": [{"name": e.name, "why": e.why,
                            "stations": len(e.stations),
                            "fired": self.hits[e.name]}
                           for e in self.exemptions],
            "stations": [
                {"v": round(s.v, 1), "mesh": round(s.mesh, 1),
                 "build": None if s.build is None else round(s.build, 1),
                 "state": s.state}
                for s in self.stations if s.state != "ok"
            ],
        }


def section(name: str, u0: float, u1: float, *, mesh: Cloud, build: Cloud,
            plan: Plan, registration: Registration,
            tolerance: float = TOLERANCE, exemptions=(), seams=(),
            seam: float = SEAM, edge_cells: int = EDGE_CELLS,
            floor: float = CLUTTER) -> Section:
    """Compare the two skylines over one stretch of the building.

    Heights are compared as they stand, in metres, without the plan scale
    applied to them. The mesh building is usually a few per cent smaller because
    it is a different building of the same design; the height of ours was chosen
    deliberately from its floor height, and is not a thing to be corrected
    towards the reference. Registration is for saying *where*, never how tall.

    `seams` are the u values where this window meets the next one. The drawing
    and the mesh do not put a junction in quite the same place, and material
    from the far side of one arrives inside this window carrying the wrong
    building's heights.
    """
    reg = registration
    # Ordered: a window put through a registration that carries a flip comes
    # back with its ends swapped, and every test below is `m0 <= u < m1`. The
    # section then grades nothing at all and says so, which is better than
    # grading the wrong thing and much worse than just working.
    m0, m1 = sorted((reg.to_mesh_u(u0), reg.to_mesh_u(u1)))
    expected = plan.claims(u0, u1, edge_cells)

    mesh_top: dict[int, float] = {}
    for u, v, h in zip(mesh.u, mesh.v, mesh.h):
        if not (m0 <= u < m1 and reg.mesh_v[0] <= v <= reg.mesh_v[1]):
            continue
        bu, bv = reg.to_build_u(u), reg.to_build_v(v)
        if not plan.covers(bu, bv):
            continue
        if any(abs(bu - s) < seam for s in seams):
            continue
        k = station_of(reg, v)
        if h > mesh_top.get(k, -math.inf):
            mesh_top[k] = h

    build_top: dict[int, float] = {}
    for u, v, y in zip(build.u, build.v, build.h):
        if u0 <= u < u1:
            k = int(round(v))
            if y > build_top.get(k, -math.inf):
                build_top[k] = y

    # Stations the witness offers, split into the ones the plan claims and the
    # ones it does not. Counted before the table, so a filter that quietly stops
    # firing is visible rather than assumed.
    offered = [k for k, h in mesh_top.items() if h >= floor]
    graded = sorted(k for k in offered if build_bin(reg, k) in expected)

    # Every station this window saw and did not grade, with the reason. Counted
    # here and printed only under `--profile`, because the question it answers
    # is the one asked after two runs that did not move the report: a section
    # that grades forty stations of ninety-six is silent about the other
    # fifty-six, and the reader cannot tell a window in the wrong place from a
    # building that is right. The build column comes along because a station
    # the reference drops is exactly where the build is graded by nothing.
    # Carried in build v, the same unit and the same half-station offset the
    # graded rows use, so the two can be printed as one table.
    kept = set(graded)
    dropped: list[tuple[float, float | None, float | None, str]] = []
    for k in sorted(mesh_top):
        if k in kept:
            continue
        b = build_top.get(build_bin(reg, k))
        why = ("under the clutter floor" if mesh_top[k] < floor
               else "the plan claims nothing here")
        dropped.append((reg.to_build_v(k + 0.5), mesh_top[k], b, why))
    # And the other direction: build material at a station the reference never
    # offered at all. The section is one-directional by construction and this is
    # the whole of what it cannot see. Two reasons and not one, because the two
    # want opposite things done about them: a run of stations past the end of the
    # clip is the site being larger than the capture and is expected, while a
    # station inside the clip with nothing over it is either a hole in the
    # reference or a piece of building nobody asked for.
    seen = {build_bin(reg, k) for k in mesh_top}
    inside = sorted((reg.to_build_v(reg.mesh_v[0]), reg.to_build_v(reg.mesh_v[1])))
    for bv in sorted(build_top):
        if bv in seen:
            continue
        why = ("nothing in the reference at this station"
               if inside[0] <= bv <= inside[1]
               else "past the end of the reference clip")
        dropped.append((float(bv), None, build_top[bv], why))

    band = Band(name, u0, u1, mesh_top, build_top, reg)
    excused = [make(band) for make in exemptions]
    hits = {e.name: 0 for e in excused}

    stations: list[Station] = []
    for k in graded:
        m = mesh_top[k]
        # A mesh band runs [k, k+1) and is read at its centre. Binning the build
        # on to_build_v(k) instead put the two skylines half a metre apart in v,
        # which on a sloping roof is most of a station's worth of height, and
        # failed one that agreed.
        v = reg.to_build_v(k + 0.5)
        b = build_top.get(int(round(v)))
        if b is None:
            # The mesh sees a building here and we built nothing. That is the
            # loudest thing this check can find, and it used to be a `continue`.
            stations.append(Station(k, v, m, None, "empty"))
            continue
        d = b - m
        if abs(d) <= tolerance:
            stations.append(Station(k, v, m, b, "ok"))
            continue
        let_off = next((e for e in excused if e.covers(k, d)), None)
        if let_off is not None:
            hits[let_off.name] += 1
            stations.append(Station(k, v, m, b, "exempt", let_off.name))
            continue
        stations.append(Station(k, v, m, b, "over" if d > 0 else "under"))

    return Section(name, u0, u1, m0, m1, stations, len(offered) - len(graded),
                   tolerance, excused, hits, dropped)


__all__ = [
    "Band", "Check", "Cloud", "Exemption", "Gate", "Plan", "Registration",
    "Section", "Station", "capture_hole", "extent", "next_lot", "on_a_step",
    "one_station_off", "ribbed", "section", "station_of", "build_bin",
    "taller_than_the_mesh",
    "CLUTTER", "EDGE_CELLS", "REGISTER_AT", "SCALE_AGREEMENT", "SCALE_TRUE",
    "SEAM", "TOLERANCE", "TRIM",
]
