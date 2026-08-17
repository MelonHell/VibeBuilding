"""Running a building's gate: the part that is the same on every building.

`blockwright.gate` is the apparatus -- registrations, clouds, sections,
exemptions. This is the *order* they are used in, which six buildings each wrote
out at four hundred lines and which differed between them only in comments and in
one branch that every one of them got slightly differently.

    GATE = grading.Grading(paths, derive, sys.modules[__name__])

    def main() -> int:
        return GATE.main()

`main` takes the command line, so every building's gate answers `--profile`:
both skylines at every station of every window, graded and dropped alike, in one
unit. That is the table to reach for when two runs in a row have not moved the
report, because the ordinary output shows only the stations that were graded and
cannot say whether the window is in the wrong place.

The building's `gate.py` is then its constants and, where it needs one, its own
`windows()` -- which stretches to cut a section through, and which exemptions
each may use. Everything else here is fixed, and fixed on purpose: the order
these rows are asked in is the difference between a gate that says what is wrong
and one that reports the second symptom of the first fault.

What a building may override, by naming it at module level:

    REGISTRATION    "measured" (the default) or "fit". The default grades the
                    section through the registration `derive` already made, which
                    is the one every number in the build came through. "fit" is
                    for a building that deliberately wants an independent one,
                    and is worth an argument in the file where it is set.
    windows()       the stretches to cut through, and their exemptions.
    build_cloud()   the build's own cloud, for a building that has to bring its
                    heights back to the reference's scale before the comparison.

Every one of those is a decision, so none of them has a silent default beyond
what is written above.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path

from . import checks, gate, measure, report, sources, style, survey
from . import model as model3d
from .mask import Mask, iou
from .mesh import Mesh
from .schedule import Schedule
from .schem import AIR, Schematic


def solid(model, y: int) -> Mask:
    """Everything that is not air on one layer."""
    area = model.width * model.length
    air = {i for i, block in enumerate(model.palette) if block == AIR}
    layer = Mask(model.width, model.length)
    base = y * area
    for i in range(area):
        if model.blocks[base + i] not in air:
            layer.bits[i] = 1
    return layer


def holds(ring: Mask) -> int:
    """How many cells a ring encloses that the outside cannot reach.

    Zero means the ring leaks -- either a wall met itself corner to corner and
    left a diagonal gap, or what looked like a room is open on one side.
    """
    bounds = ring.bounds()
    if bounds is None:
        return 0
    x0, z0, x1, z1 = bounds
    box = Mask(ring.width, ring.length)
    for z in range(z0, z1 + 1):
        for x in range(x0, x1 + 1):
            box.set(x, z)
    inside = box - ring
    return inside.count() - len(checks.watertight(inside, ring))


def windows(parts, frame, derived: dict, names: dict | None = None):
    """The stretches along the building to cut a section through.

    One window is right only where the building is one thing for its whole
    length. Split it where a wing stops or a tower sets back: one window would
    average a wing and a courtyard together and grade neither.

    The default splits the building wherever its parts stop overlapping along u.
    Two parallel wings share one window, because a cut through them is one cut
    through both; a wing and a tower beyond its end get one each. That is the
    right shape of answer far more often than a single window for the whole
    length: a section through a uniform extrusion is the same section wherever it
    is cut, so one wide window grades the middle repeatedly and the ends not at
    all -- and the ends are where things go wrong.

    A building overrides this to split further where a single part steps, to
    merge two stretches that really are one continuous thing, or to offer a
    window an exemption. Returns `(name, u0, u1, exemptions)` in u order.
    """
    ordered = sorted((names or {}).items(), key=lambda kv: kv[1].u0)
    if not ordered:
        u0 = min(p.u0 for p in parts)
        u1 = max(p.u1 for p in parts)
        return (("body", u0, u1, ()),)

    runs: list[list] = []
    for name, part in ordered:
        if runs and part.u0 < runs[-1][2]:
            runs[-1][0].append(name)
            runs[-1][2] = max(runs[-1][2], part.u1)
        else:
            runs.append([[name], part.u0, part.u1])
    return tuple(("+".join(held), u0, u1, ()) for held, u0, u1 in runs)


# What a building gets if it says nothing.
#
# Six constants were byte for byte the same in all six buildings, with the same
# paragraph of explanation copied above each -- which is not six decisions, it is
# one decision and five copies. A building's `gate.py` is supposed to be the part
# only that building can say, so these move here and a building writes them only
# where it differs.
#
# The two budgets are the awkward ones and they are handled rather than ducked.
# `FREE_ENDS` and `GROUNDED_STRAYS` are meant to be lowered onto a build that
# already passed -- a budget with an order of magnitude of slack has stopped
# being a check -- so a default silently satisfying them would be exactly the
# rot they exist to prevent. The row therefore says `(default)` when the number
# came from here, and the reader can tell a measured budget from an inherited
# one at a glance.
DEFAULTS = {
    # Half a storey: tight enough that a floor gained or lost fails, loose
    # enough that photogrammetry's rounding of a parapet does not.
    "TOLERANCE": 2.0,
    # Below this a layer component is a fixture -- a column, a rail post, the
    # corner of a planter clipped by the cut -- and not a floor plate.
    "MIN_PART": 8,
    "SCALE_TRUE": gate.SCALE_TRUE,
    # Nothing may hang unsupported, ever. This one is not a budget.
    "FLOATING_BLOCKS": 0,
    "FREE_ENDS": 1200,
}

# A declaration taller than this many measured storeys is a building, not a
# surface. One course is grounds, a deck, a pool; one storey is a floor plate
# or a parapet course stacked in the merge. A clubhouse at four metres on a
# three-metre storey is the thing this row exists to see.
COVERAGE_STOREYS = 1.0

# Share of a declaration that must sit on the surveyed plan -- dilated by
# `slack`, so a wall ring on the padded edge still counts as on the plan.
# Half is "mostly": a balcony that is one cell of wall and one cell of
# cantilever stays with the plan; a villa whose whole footprint misses
# every part does not.
COVERAGE_SHARE = 0.5

# How far the build may reach past the reference before the clip is too
# small. One metre is one cell, and it is the noise of a lattice snap and
# a staircase at the end of an axis -- not the tens of metres a clip cut
# around the tower leaves of the clubhouse. A bare 1.0 in the comparison
# would be the same number with none of that.
CLIP_HOLDS_WITHIN = 1.0


class Grading:
    """One building's gate, run in the order that makes its answers readable."""

    def __init__(self, paths, derive, config):
        self.paths = paths
        self.derive = derive
        self.c = config

    def _(self, name: str, default=None):
        if default is None and name in DEFAULTS:
            default = DEFAULTS[name]
        return getattr(self.c, name, default)

    def _own(self, name: str) -> bool:
        """Whether the building set this itself or inherited the default."""
        return hasattr(self.c, name)

    def slack(self, read, cells: float = 2.0) -> float:
        """`cells` cells of the build's own grid, in metres.

        Every "is this the same piece or the next one" threshold in this file
        means a number of cells and was written as a number of metres, which is
        the same thing only on a building that stands square to the world. It
        does not: a one-cell step along an edge at angle t covers
        `|cos t| + |sin t|` metres -- 1.0 head-on, 1.41 at forty-five degrees --
        so `dilate(2.0)` reaches two cells across a north-south building and
        1.4 across a diagonal one. The corpus runs from 0 to 57 degrees, so the
        same constant has meant two different things the whole time, and the
        one it means least is on the buildings whose edges are hardest to
        rasterise.

        Named `slack` rather than `reach` because it is never a dimension of
        the building: it is how far apart two cells may be and still be one
        thing -- the pad's half-metre, the gap a diagonal wall leaves between a
        floor and the wall it belongs to, the width of the seam between two
        parts drawn separately.

        Measured off the frame rather than read from `derived.json`, so a
        caller with a frame and no survey gets the same answer.
        """
        return cells * measure.staircase(read.frame)

    # -- the run -----------------------------------------------------------

    def main(self, argv=None) -> int:
        paths, derive, c = self.paths, self.derive, self.c

        # One flag, and an unknown one is refused rather than ignored: somebody
        # typing `--profiles` is asking for a table, and a run that prints the
        # ordinary report and exits zero answers them with the wrong thing.
        argv = list(sys.argv[1:] if argv is None else argv)
        profile = "--profile" in argv
        rest = [a for a in argv if a != "--profile"]
        if rest:
            raise SystemExit(f"unknown argument(s) {' '.join(rest)}; "
                             "the gate takes --profile and nothing else")

        if not paths.DERIVED.exists():
            raise SystemExit(
                f"{paths.DERIVED} is missing; run probes/derive.py first")

        derived = json.loads(paths.DERIVED.read_text(encoding="utf-8"))
        evidence = sources.survey(paths)
        read = derive.plan_of()
        reference = evidence.reference
        g = gate.Gate(c.BUILDING)

        # Freshness first, and nothing else runs if it fails. Every row below
        # reads the schematic and the schedule; grading yesterday's build
        # against today's plan produces a page of confident numbers about a
        # building that no longer exists, and the reader has no way to tell.
        #
        # The recipe counts as a source. A build.py edited since the schematic
        # was written means the schematic is not what the recipe says, which is
        # the case that actually happens: the fix is made, the run is
        # forgotten, and the gate reports the defect as still present -- or,
        # worse, as gone.
        g.fresh(made=[paths.SCHEM, paths.SCHEDULE],
                sources=[read.source.path, paths.DERIVED,
                         Path(c.__file__).with_name("build.py")]
                        + ([reference.path] if reference else []))
        if not g.ok:
            for line in g.lines():
                print(line)
            return 1

        model = Schematic.read(paths.SCHEM)
        frame, parts = read.frame, read.parts
        sched = Schedule.load(paths.SCHEDULE)

        audit = list(sched.audit(model))
        g.extend(audit)

        findings = self.soundness(g, model)
        self.placement(g, sched)
        self.repetition(g, model, sched, derived)
        cut = self.divisions(g, model, sched)
        self.watertight(g, cut)
        self.evenness(g, model, read, sched, derived)
        plan = self.plan_shape(g, model, read)
        self.corners(g, model, read, derived, sched)
        mixes, matrix = self.facades(g, model, read, derived)
        self.elevations(g, derived)
        self.plateaus(g, derived)
        self.grounds(g, derived, read, sched)
        self.witnesses(g, derived)
        self.coverage(g, derived, sched, read)
        self.site_covered(g, derived, sched, read)
        sections, reg = self.section(g, derived, read, reference, model, frame,
                                     parts, evidence)

        # Printed, never graded. There is no correct value for how varied a
        # surface should be -- the building's own photographs outrank any figure
        # taken off another city -- so this is a column of numbers beside a
        # column of numbers and nothing turns red. What it buys is that a review
        # finding about texture can be answered with a measurement instead of
        # another opinion, and that a deck laid as one flat rectangle says so on
        # every run rather than waiting for somebody to notice in a render.
        texture = style.texture(model)

        doc = report.write(paths.REPORT, c.BUILDING, g,
                           sections=sections, findings=findings, frame=frame,
                           registration=reg, schedule=audit,
                           texture=texture.report(),
                           facades={name: {"cells": found["cells"],
                                           "mix": {b: round(s, 3) for b, s
                                                   in sorted(found["mix"].items(),
                                                             key=lambda kv: -kv[1])}}
                                    for name, found in mixes.items()},
                           facade_matrix=[[a, b, round(far, 3)]
                                          for a, b, far in matrix],
                           plan=plan,
                           evidence=derived.get("evidence"))

        for line in (reg.lines() if reg is not None else []):
            print(line)
        for section in sections:
            print()
            for line in (section.profile() if profile else section.lines()):
                print(line)
        print()
        for line in g.lines():
            print(line)
        print()
        for line in texture.lines():
            print(line)
        # Printed on every run, whether or not a row asked about it. The whole
        # point of the matrix is the pair nobody thought to ask about.
        for line in self.facade_lines(matrix):
            print(line)
        print()
        for line in report.lines(doc):
            print(line)
        print(f"\nwrote {paths.REPORT}")
        return 0 if g.ok else 1

    # -- the rows ----------------------------------------------------------

    def soundness(self, g, model):
        c = self.c
        findings = checks.inspect(model, layers=list(self._("LEVELS")), soft=self._("SOFT", set()))
        g.add("palette", not findings.unknown,
              "every block has a colour" if not findings.unknown
              else "unknown: " + ", ".join(sorted(findings.unknown)))
        # A budget the building did not set is named as inherited. These are
        # meant to be lowered onto a build that already passed, and a default
        # quietly satisfying one is the rot they exist to prevent.
        def whose(name: str) -> str:
            return "" if self._own(name) else " (default, not measured here)"

        g.budget("floating", sum(p.count for p in findings.adrift),
                 self._("FLOATING_BLOCKS"),
                 "blocks with nothing under them" + whose("FLOATING_BLOCKS"))
        g.budget("strays", sum(p.count for p in findings.strays),
                 self._("GROUNDED_STRAYS", 0),
                 "blocks adrift from the main mass" + whose("GROUNDED_STRAYS"))
        g.budget("free ends", len(findings.ends), self._("FREE_ENDS"),
                 "blocks with one neighbour or none" + whose("FREE_ENDS"))
        return findings

    def placement(self, g, sched):
        """Which parts stand where a photograph said, rather than where a
        measurement put them.

        This row never fails and it never passes quietly either. A part with a
        `placed` note exists because a photograph shows it and stands where it
        stands because somebody counted arches -- so its position was argued
        for, not checked, and `ungraded` is the honest verdict for exactly that.
        The alternative the corpus has been living with is worse: the part is
        left out, the render is missing the most recognisable thing about the
        building, and nothing anywhere says why.

        With nothing placed the row passes and says so. That is a real answer
        rather than a silence -- unlike `COUNTS`, an empty list here means "every
        part in this building was positioned by something measured", which is
        the better state and worth reading.
        """
        placed = sched.placed
        if not placed:
            g.add("placement", True,
                  "every part is positioned by something measured")
            return
        g.ungraded(
            "placement",
            f"{len(placed)} part(s) stand where a photograph says, counted "
            "against a measured anchor rather than measured: "
            + "; ".join(f"{i.name} -- {i.placed}" for i in placed))

    def repetition(self, g, model, sched, derived):
        """Are the parts this building says it copied actually the same blocks?

        The question `division` asks about shape, asked about material: a row of
        eight sections and a row of eight sections that were each drawn from the
        same numbers cast the same silhouette, cut the same section, cover the
        same plan and satisfy the same audit. The second reads as wrong from the
        ground and as right to every other row here.

        Empty is `[----]` and not silence, for the reason `COUNTS` is: a
        building that declares no copies has either nothing that repeats or a
        mechanism nobody reached for, and those look identical from the outside.
        `UNREPEATED` says which, in a sentence -- not a flag, because the flag
        version of this is `UNIFORM = True`, which stood on two buildings and
        turned off the only row that looked at a wall.
        """
        wanted = [i for i in sched.items if i.copies]
        why = self._("UNREPEATED", None)
        if not wanted:
            if why is True:
                g.ungraded(
                    "repetition",
                    "UNREPEATED is a reason, not a flag: say what about this "
                    "building has nothing in it twice.")
            elif why:
                g.ungraded("repetition",
                           f"nothing here repeats -- {why}")
            else:
                g.ungraded(
                    "repetition",
                    "no item declares copies and UNREPEATED is not set, so "
                    "nothing checks that the sections this building repeats are "
                    "the same section. Eight identical villas and eight villas "
                    "drawn eight times pass every other row here -- same "
                    "silhouette, same section, same plan, same materials, same "
                    "schedule -- and differ from the ground. Say "
                    "Item(..., copies=N) beside the part and call "
                    "Schedule.declare_repeat where it is stamped, or set "
                    "UNREPEATED to the reason this building has nothing in it "
                    "twice.")
            return

        slope = (derived.get("frame", {}) or {}).get("slope")
        for item in wanted:
            found = sched.repeated.get(item.name)
            if found is None:
                g.add(f"{item.name} repeats", False,
                      f"the manifest says {item.copies} copies ({item.source}) "
                      "and the build declares no repetition. Either the "
                      "sections are being drawn one by one -- in which case "
                      "they are not copies -- or the stamp is there and "
                      "Schedule.declare_repeat beside it is not.")
                continue

            same = checks.copies(model, found.motif, found.y0, found.y1,
                                 found.step, found.count)
            step = tuple(found.step)
            reach = math.hypot(*step)
            where = (f"step {step} = {reach:.2f} m"
                     + (f" = {reach / slope['period']:.0f} period(s) of "
                        f"{abs(slope['step'][0])}:{abs(slope['step'][1])}"
                        if slope and slope.get("period") else ""))
            excused = (f"; {same['excused']} rim cell(s) excused ("
                       + ", ".join(f"{k} states" for k in same["families"])
                       + ")" if same["excused"] else "")

            if same["outside"]:
                g.add(f"{item.name} repeats", False,
                      f"{same['outside']} cell(s) of the run stand outside the "
                      f"schematic: {found.count} copies on {where} do not fit "
                      "what was written.")
            elif same["ok"]:
                g.add(f"{item.name} repeats", True,
                      f"{found.count} copies on {where}, byte-identical over "
                      f"{same['cells']} cells x {same['courses']} "
                      f"course(s){excused}")
            else:
                n, x, y, z, was, now = same["first"]
                g.add(f"{item.name} repeats", False,
                      f"{found.count} copies on {where} came out "
                      f"{same['distinct']} distinct pattern(s); copy {n} "
                      f"differs from copy 0 in {same['differ'][n]} of "
                      f"{same['cells'] * same['courses']} cells, first at "
                      f"({x}, {y}, {z}): {now} where copy 0 has {was}{excused}")

    def divisions(self, g, model, sched):
        """Counts of components inside each declared part, at several heights.

        An empty table is a row and not a silence. `COUNTS` unfilled and `COUNTS`
        satisfied print the same thing otherwise -- nothing -- and then "no rows
        failed" quietly comes to mean "no rows were asked", which is the exact
        confusion the three-state verdict exists to prevent.
        """
        c = self.c
        if not self._("COUNTS", {}) and not self._("UNDIVIDED", False):
            g.ungraded(
                "division",
                "COUNTS is empty, so nothing checks that this building still "
                "reads as separate parts. A row of houses and one long block "
                "cast the same silhouette and pass every other row here. Fill "
                "it in, or set UNDIVIDED = True to say this building genuinely "
                "has no repeated division.")

        # Counted inside each part's own declared footprint. Anything crossing
        # between parts -- a gallery, a bridge, a canopy pier -- stands in the
        # same layer, and a count taken over the whole layer either grades a
        # pier as a house or merges two houses through a bridge.
        cut = {}
        for name, expected in self._("COUNTS", {}).items():
            footprint = sched.built[name].mask
            cut[name] = {
                y: (solid(model, y) & footprint).components(
                    min_cells=self._("MIN_PART"))
                for y in self._("LEVELS")}
            for y, found in cut[name].items():
                g.add(f"{name} at {y} m", len(found) == expected,
                      f"{len(found)} parts, expected {expected}")

        for name, y in self._("RINGS", {}).items():
            found = (solid(model, y) & sched.built[name].mask).components(
                min_cells=self._("MIN_PART"))
            g.add(f"{name} at {y} m", len(found) == 1,
                  f"{len(found)} parts, expected one closed ring"
                  if len(found) != 1
                  else f"one ring of {found[0].count()} blocks")
        return cut

    def facade_spans(self, read, derived) -> tuple[dict, str]:
        """The stretches of wall to tally, declared or cut automatically.

        `FACADES` when the building filled it in. Otherwise the building is cut
        up anyway, because the alternative is what happened: a row that is only
        ever asked when somebody already suspects the answer.

        Two automatic cuts, in order of how much they know. The runs `probes`
        found -- the arms of an E, the wings of a U -- are the right stretches
        when they exist, being the building's own divisions along its length.
        Failing those, the plan's parts. Near-duplicate spans are dropped: a
        plan whose parts all run the whole length would otherwise report every
        pair as identical walls, which is true and says nothing.
        """
        spans = dict(self._("FACADES", {}))
        if spans:
            return spans, "declared in FACADES"

        found = (derived.get("wings") or {}).get("found") or []
        if found:
            spans = {f"run {i}": (float(w["u"][0]), float(w["u"][1]))
                     for i, w in enumerate(found, 1)}
            source = "the runs probes measured along this building"
        else:
            spans = {name: (float(p.u0), float(p.u1))
                     for name, p in read.named.items()}
            source = "the plan's own parts"

        kept: dict[str, tuple[float, float]] = {}
        for name, (u0, u1) in sorted(spans.items(), key=lambda kv: kv[1][0]):
            reach = u1 - u0
            twin = any(min(u1, b1) - max(u0, b0) > 0.5 * min(reach, b1 - b0)
                       for b0, b1 in kept.values())
            if not twin and reach > 0:
                kept[name] = (u0, u1)
        return kept, source

    def facade_lines(self, matrix) -> list[str]:
        """The matrix, as a column of numbers under the texture's column."""
        if not matrix:
            return []
        out = ["", "facade material, stretch against stretch "
                   "(0 is the same wall, 1 shares nothing)"]
        for a, b, far in matrix:
            out.append(f"  {far:.2f}  {a} / {b}")
        return out

    def facades(self, g, model, read, derived):
        """Whether the stretches of wall the reference draws differently are.

        The hole `evenness` does not cover, and the two are easy to confuse.
        `TWINS` asks "you said these parts are alike -- are they?" and compares
        the build against itself. This asks the opposite question, and only the
        reference can raise it: **is anything the build made alike actually
        alike out there?** A building whose right-hand wing has no balconies is
        built with balconies along all of it, and every row here passes. Three
        wings of one footprint cast one silhouette. The section grades a skyline
        and a facade has none. The schedule audit sees loggias declared and
        loggias standing, in the right cells, in the right block. Nothing in the
        pipeline is looking at the wall.

        It happened, and the measurement had already said so: the run recorded
        `texture bay: 11.00 m, 2 of 4 elevations agree`, the two that dissented
        read 8.48 and 14.50, and they were the blank wing. Two of four is not a
        weak reading of one rhythm; it is a firm reading of two.

            FACADES        {name: (u0, u1)} -- stretches along the building
            FACADES_DIFFER ((a, b), ...)    -- pairs the reference draws apart
            FACADES_ALIKE  ((a, b), ...)    -- pairs it draws the same
            FACADES_SAME   how close two mixes may be and still count as apart

        **The tally runs whether or not the building filled anything in.** That
        is the change this row needed most. `FACADES_DIFFER` can only ask what
        somebody already suspected -- "these two should differ" -- and the
        failure that ships is the other one: two stretches the build made
        identical, which nobody suspected, so nobody went to look at whether the
        reference agrees. So the spans are cut automatically when they are not
        declared (see `facade_spans`), the whole matrix is measured and printed,
        and every pair the build made alike is named in a row of its own.

        `UNIFORM` says this building really is one facade all the way round.
        It takes the *reason* -- the photographs somebody went and looked at --
        and not a bare `True`. The bare boolean is what this row cost before,
        and one line was too cheap for what it switches off: on one building it
        was set together with a paragraph of justification naming photographs
        that had been glanced at rather than read, and the wing with no
        balconies on it shipped with balconies. Filling in `FACADES` is work;
        `UNIFORM = True` was one word. The two now cost about the same to write
        and only one of them is checkable, which is the right way round.
        """
        # The drawn mass, and this is the one row here that wants it. A facade
        # mix is tallied over the outline of this mask inside a u span, so an
        # assembled part standing anywhere inside another part's u span puts its
        # own wall columns into that part's facade -- and a pool house beside a
        # block is inside the block's span at both ends of it. The same reason
        # `storeys_of` reads its bands off the drawn building.
        mask = read.mass if hasattr(read, "mass") else None
        spans, source = self.facade_spans(read, derived)
        mixes = ({name: checks.facade_mix(model, mask, read.frame, u0, u1)
                  for name, (u0, u1) in spans.items()}
                 if mask is not None else {})
        matrix = checks.mix_matrix(mixes)
        budget = self._("FACADES_SAME", 0.10)

        declared = self._("FACADES", {})
        uniform = self._("UNIFORM", None)
        if not declared:
            if uniform is True:
                g.ungraded(
                    "facades",
                    "UNIFORM is a bare True, which says this building is one "
                    "facade all the way round and says nothing about how "
                    "anybody knows. Give it the reason instead -- the "
                    "photographs that were looked at and what they show -- and "
                    "it becomes a claim that can be read and argued with. One "
                    "building set this flag with a paragraph of justification "
                    "in a comment beside it; the photographs named in that "
                    "paragraph had been glanced at, one wing of the real "
                    "building has no balconies, and the build gave it some.")
            elif isinstance(uniform, str) and uniform.strip():
                g.add("facades", True,
                      "one facade all the way round, and the reference says so: "
                      + uniform.strip())
            else:
                g.ungraded(
                    "facades",
                    "FACADES is empty, so nothing checks that the parts of this "
                    "building the reference draws differently were built "
                    "differently. A wing built with the balconies its neighbour "
                    "has and the reference does not passes every other row "
                    "here: same silhouette, same skyline, same section, same "
                    "schedule. Fill it in, or set UNIFORM to the reason the "
                    "reference really does draw one facade all the way round.")

        for a_name, b_name in self._("FACADES_DIFFER", ()):
            a, b = mixes.get(a_name), mixes.get(b_name)
            if a is None or b is None:
                g.ungraded(f"{a_name} differs from {b_name}",
                           "one of them is not in FACADES")
                continue
            if not a["cells"] or not b["cells"]:
                g.ungraded(f"{a_name} differs from {b_name}",
                           f"{a_name} has {a['cells']} facade cells and "
                           f"{b_name} has {b['cells']}; one of the spans is "
                           "off the building")
                continue
            far = checks.mix_apart(a["mix"], b["mix"])
            g.add(f"{a_name} differs from {b_name}", far >= budget,
                  f"the two walls are {far:.2f} apart by material "
                  f"(0 is the same wall, 1 shares nothing); the reference draws "
                  f"them apart, so anything under {budget:g} means the build "
                  "made them the same")

        self.alike(g, matrix, source, budget, uniform)
        return mixes, matrix

    def alike(self, g, matrix, source, budget, uniform) -> None:
        """The stretches the build made the same, and whether anybody asked.

        The other direction, and the one nothing in this pipeline had. Every
        check that looks at a facade at all takes a question from the building's
        own tables, so the wall that ships wrong is the one the tables do not
        mention: a run built exactly like its neighbour because the section
        drawing it was called twice, on a building where the reference draws
        them apart.

        `ungraded` rather than a failure, and that is the honest verdict: two
        stretches being identical is not a fault -- most buildings really are
        uniform over most of their length -- it is an unasked question. A
        building answers it by naming the pair in `FACADES_ALIKE`, by declaring
        `UNIFORM` with its reason, or by naming it in `FACADES_DIFFER`, which
        turns it into a row that can fail.
        """
        if not matrix:
            return
        known = {frozenset(pair) for pair in self._("FACADES_ALIKE", ())}
        known |= {frozenset(pair) for pair in self._("FACADES_DIFFER", ())}
        if isinstance(uniform, str) and uniform.strip():
            known |= {frozenset((a, b)) for a, b, _ in matrix}

        same = [(a, b, far) for a, b, far in matrix
                if far < budget and frozenset((a, b)) not in known]
        if not same:
            g.add("facades built alike", True,
                  f"every pair of the {len(matrix)} compared is either "
                  f"{budget:g} or more apart by material, or was declared")
            return
        g.ungraded(
            "facades built alike",
            f"the build made {len(same)} pair(s) of stretches the same wall, "
            f"cut from {source}, and nothing says the reference agrees: "
            + "; ".join(f"{a} / {b} at {far:.2f}" for a, b, far in same[:6])
            + ". Go and look at a photograph of each: if they really are the "
            "same wall say so in FACADES_ALIKE, and if they are not, name the "
            "pair in FACADES_DIFFER and this becomes a row that fails.")

    def elevations(self, g, derived):
        """Whether the reference's own elevations agree about the facade.

        The measurement was already there and was printed as a note. `derive`
        reads the storey and the pier rhythm off all four orthographic
        elevations and reports how many of them agreed -- and its own docstring
        says what a minority means: *two of four is not a weak reading of one
        rhythm, it is a firm reading of two.*

        It happened exactly that way. A run recorded `texture bay: 11.00 m, 2 of
        4 elevations agree`, the two that dissented read 8.48 and 14.50, and
        they were the wing with no balconies on it. Everything needed to catch
        it was on the screen, in a paragraph under a table, in a survey that
        prints two hundred lines. A paragraph is scrollable; a row is not.

        Never a failure: a dissenting elevation is evidence about the reference
        and not about the build. It closes when the building has said what the
        disagreement is -- `FACADES` splitting it into stretches, or `UNIFORM`
        with the reason -- which is the whole of what this row is asking for.
        """
        storeys = derived.get("storeys") or {}
        answered = bool(self._("FACADES", {})) or isinstance(
            self._("UNIFORM", None), str)
        readings = (
            ("bay", (derived.get("facade") or {}).get("bay")),
            ("storey", storeys.get("from_texture")
             or (storeys.get("measured") or {}).get("from_texture")),
        )
        for what, found in readings:
            if not found:
                continue
            name = f"the elevations agree about the {what}"
            sides = ", ".join(f"{r['side']} {r['value']:.2f}"
                              for r in found.get("readings", []))
            against = found.get("against") or []
            if not against:
                g.add(name, True,
                      f"{found['agreed']} of {found['of']} elevations read "
                      f"{found['value']:.2f} m -- {sides}")
                continue
            dissent = ", ".join(f"{r['side']} at {r['value']:.2f} m"
                                for r in against)
            if answered:
                g.add(name, True,
                      f"{found['agreed']} of {found['of']} read "
                      f"{found['value']:.2f} m and {dissent}; the build says "
                      "what the difference is -- see the facade rows")
                continue
            g.ungraded(
                name,
                f"only {found['agreed']} of {found['of']} elevations read "
                f"{found['value']:.2f} m, and {dissent}. If those elevations "
                "are different parts of this building, that is two facades and "
                "not one weak measurement -- which is a thing no other row here "
                "can see, because two wings of one footprint have the same "
                "silhouette, skyline, section and schedule. Fill in FACADES, or "
                "set UNIFORM to the reason there is only one.")

    def plateaus(self, g, derived):
        """Whether the height each part hands downstream describes the part.

        `skyline_of` reads one number per part and every row below uses it:
        `Site.tops` builds to it, the section grades against it, the silhouette
        is cut at it. It is a median, and a median is only a height when the
        readings it came from are one plateau.

        They often are not, and the way they fail is a neighbour. A tower that
        overhangs the link between two towers stands over the link's footprint,
        so the link's stations read the tower; the part is two storeys and the
        number handed on is twenty-eight metres. That happened, and the sheet
        printed `low 5.2, median 27.6, high 29.8` -- twenty-four metres of
        spread on a flat roof, visible at a glance and in no row at all. On the
        same building a wing came back with eighteen per cent of its stations at
        its own median, which is the case `skyline_of`'s own docstring warns
        about: two levels, and the median between them, wrong at both ends.

        The share is the measurement and the spread is not. A parapet, a plant
        enclosure and a lift overrun all widen `low..high` on a roof that is
        genuinely one height; only the share of stations *at* the median tells
        those apart from a second building standing on the part. `PLATEAU` is
        where the line sits -- one storey, the smallest step that is another
        floor rather than roof furniture.

        Read as a share of *stations*, and a station is the highest thing over
        a line across the part, so a little tall material goes a long way: the
        link above reads 7.6 m as a median over its cells and 27.6 m as a
        median over its stations, because most lines across it clip something
        of the tower. That is the honest reading for this question -- the
        height handed downstream is the station reading, and it is the station
        reading that is wrong.

        Never a failure. This is evidence about the reference and the plan
        together -- the capture is what it is, and which cells belong to which
        part is the plan's business -- so the build cannot be at fault for it
        and cannot fix it either. It closes the way `UNIFORM` closes: the
        building says what the second level is, in `PLATEAUS`, keyed by part.
        The measurement is printed on every run regardless, so the answer stays
        checkable against the number it explains.
        """
        skyline = derived.get("skyline") or {}
        said = self._("PLATEAUS", {}) or {}
        # One row for a stale sheet and not one per part. A survey written
        # before this measurement kept the range and threw the shape away, and
        # the buildings in that state are the ones whose `derive` currently
        # refuses on the datum -- they cannot act on the row, so saying it once
        # is the whole of what it can usefully say.
        stale = [name for name, p in skyline.items()
                 if ".." not in name and p.get("stations") and "share" not in p]
        if stale:
            g.ungraded(
                "parts are one height",
                f"{len(stale)} part(s) were read by a survey that predates "
                f"this measurement -- {', '.join(stale)}. It recorded the "
                "range over each part and not the share of it at the median, "
                "and only the share can tell a parapet from a neighbour "
                "standing on the roof. Re-run probes/derive.py.")
        for name, p in skyline.items():
            # The gaps between parts are keyed `near..far` and report whatever
            # stands over a court. A court reading at two levels is the court
            # doing its job, not a part with a neighbour on it.
            if ".." in name or p.get("declared") or not p.get("stations"):
                continue
            row = f"{name} is one height"
            if "share" not in p:
                continue
            share = p["share"]
            if not p.get("elsewhere"):
                g.add(row, True,
                      f"{share:.0%} of {p['stations']} station(s) stand within "
                      f"{p['band']:.1f} m of {p['median']:.1f} m; the range is "
                      f"{p['low']:.1f} to {p['high']:.1f} m, which is a parapet "
                      "and plant on one roof")
                continue
            told = f"{p['elsewhere']} of {p['stations']} station(s) stand " \
                   f"{p['apart']:+.1f} m from the median of {p['median']:.1f} " \
                   f"m, leaving {share:.0%} of the part at the height " \
                   "everything downstream builds to"
            if name in said:
                g.add(row, True, f"{told}. {said[name]}")
                continue
            g.ungraded(
                row,
                f"{told}. That is another level of building over this "
                "footprint, not roof furniture -- a neighbour overhanging it, "
                "a part the plan drew as one piece that steps, or a capture "
                "that fused the two. Whichever it is, the single height read "
                "here is wrong for one of the two levels. Split the part in "
                "the plan, or set PLATEAUS[name] to what the second level is.")

    def grounds(self, g, derived, read, sched):
        """The ground this building stands on, which nothing else grades.

        The clip that makes a section honest cuts the grounds away by
        construction. That is written down and accepted, and its consequence is
        not: the deck, the road and the beach can be any shape and any size at
        all, and not one row of this gate turns red. On one building the pool
        deck was built from a height map's ragged outline and the courtyard came
        out a blob; on another the beach sand ran the length of the avenue.
        Both were found by a person looking at a render.

        Two numbers per surface and not a shape, because a capture is no
        authority on the shape of a ground (`docs/sources.md`) and every
        authority on it -- the photographs -- is outside this file. Height it
        is an authority on, and a height band gives an area and an extent.

            GROUNDS      {declared part: (low, high)} -- the band, over the
                         reference's datum, that this surface stands in
            GROUNDS_SAME how far the two areas may differ, as a fraction
            RECTANGULAR  (name, ...) -- surfaces that are one poured rectangle
            GROUNDLESS   True where this build lays no ground at all
        """
        table = self._("GROUNDS", {})
        square = self._("RECTANGULAR", ())
        if not table and not square:
            if not self._("GROUNDLESS", False):
                g.ungraded(
                    "grounds",
                    "GROUNDS is empty, so nothing grades the ground this "
                    "building stands on. The clip the section is cut against "
                    "throws the grounds away by construction, which means the "
                    "deck, the road and the beach can be any size and any shape "
                    "and every row here stays green -- it has happened twice. "
                    "Name each surface and the height band it stands in, or set "
                    "GROUNDLESS = True if this build lays no ground.")
            return

        found = derived.get("grounds")
        if table and not found:
            g.ungraded(
                "grounds",
                "GROUNDS names surfaces to grade and derived.json carries no "
                "`grounds` block to grade them against. It is written by "
                "`measure.ground` off the whole export rather than the building "
                "clip -- the clip has none of it -- so the probe needs the "
                "unclipped capture.")
            table = {}

        budget = self._("GROUNDS_SAME", 0.25)
        reading = measure.Ground.loads(found) if found else None
        for name, band in table.items():
            held = sched.built.get(name)
            if held is None:
                g.ungraded(f"{name} covers its ground",
                           "nothing in the build declares a part by that name")
                continue
            low, high = band if isinstance(band, (list, tuple)) else (band, None)
            s = checks.surface(held.mask, read.frame, reading, low, high)
            if not s["read_area"]:
                g.ungraded(f"{name} covers its ground",
                           f"the reference holds no ground between {low} and "
                           f"{high} m of its datum, so there is nothing to "
                           "compare this surface against")
                continue
            far = abs(s["built_area"] - s["read_area"]) / s["read_area"]
            g.add(f"{name} covers its ground", far <= budget,
                  f"{s['built_area']:.0f} m2 built against "
                  f"{s['read_area']:.0f} m2 standing between {low} and {high} m "
                  f"in the reference, {far:.0%} apart; budget {budget:.0%}. "
                  f"Built u {s['built_u'][0]:.0f}..{s['built_u'][1]:.0f}, "
                  f"v {s['built_v'][0]:.0f}..{s['built_v'][1]:.0f}; read "
                  f"u {s['read_u'][0]:.0f}..{s['read_u'][1]:.0f}, "
                  f"v {s['read_v'][0]:.0f}..{s['read_v'][1]:.0f}")

        allowed = self._("RECTANGULAR_SAME", 0.85)
        for name in square:
            held = sched.built.get(name)
            if held is None:
                g.ungraded(f"{name} is a rectangle",
                           "nothing in the build declares a part by that name")
                continue
            shape = checks.rectangular(held.mask, read.frame,
                                       around=getattr(read, "mass", None))
            g.add(f"{name} is a rectangle", shape["share"] >= allowed,
                  f"{shape['share']:.0%} of it and of the rectangle fitted to "
                  f"it are the same cells; allowed {allowed:.0%}. A poured slab "
                  "read off a height map comes back ragged at the edge and "
                  "bitten where the pool is, and every one of those is the "
                  "reading rather than the thing")

    def site_mass(self, read) -> Mask:
        """Every cell the plan holds, however this plan came to be read.

        `Read.site` and not `Read.mass`: the two differ by whatever
        `Survey.assemble` put on the plan that the drawn source never drew. A
        floor plate is clipped to this before it is compared with a part, so a
        pool house left out of it comes back as a hundred per cent of its own
        drawing unbuilt -- the loudest possible failure, about a part standing
        there the whole time.

        Named for the site and not for the drawing on purpose. It was
        `drawn_mass` while the two were the same thing, and a helper called
        `drawn` that answers with more than what was drawn is how the next
        reader puts the wrong mask into the next row -- see `facades`, which
        wants the drawn mass and says so.
        """
        # `is None` and not `or`: an empty Mask is falsy, so `or` would step
        # over a real answer of "the plan holds nothing here" and hand back a
        # different mask instead.
        mass = getattr(read, "site", None)
        if mass is None:
            mass = getattr(read, "mass", None)
        if mass is None:
            mass = Mask.union([p.mask for p in read.named.values()])
        return mass

    def outside_ring(self, model, read) -> tuple[Mask, int]:
        """The plot round the building: empty at a storey, solid at the ground.

        The one test that tells a floor plate from a course of terrain, and it
        has to look *outside* the building to do it. Inside, the two are
        indistinguishable -- paving under a court and a floor slab over it are
        both a filled level -- and outside, the ground is laid to the edge of
        the plot and a storey is sky.
        """
        mass = self.site_mass(read)
        u0 = min(p.u0 for p in read.named.values())
        u1 = max(p.u1 for p in read.named.values())
        v0 = min(p.v0 for p in read.named.values())
        v1 = max(p.v1 for p in read.named.values())
        ring = (read.frame.rect(model.width, model.length,
                                u0 - 8.0, u1 + 8.0, v0 - 8.0, v1 + 8.0)
                - mass.dilate(self.slack(read)))
        return ring, max(1, ring.count())

    def plates(self, model, read, figures, windows=None, aloft: float = 0.0):
        """The floor plate of each named figure: (level, cover, spill, aloft).

        `figures` is `{name: Mask}` -- a plan part, a run measured along the
        building, a footprint a manifest part declared. Masks and not names,
        because the interesting figures on an E- or an H-plan are not parts:
        see `Grading.figures`.

        A plate is a level that is full over the part and empty *outside the
        building*. Both halves are needed and the second is easy to get wrong.
        Coverage alone picks the terrain, which is laid over the whole plot and
        therefore covers every part perfectly -- and clipped to the drawn mask,
        a cut through the ground comes back as an exact copy of the map, so a
        row read off it can never fail. Asking instead whether the ground
        *round the part* is full would reject the terrain and also reject every
        storey of a part that has neighbours, which is most of them. So the
        ring is round the whole drawn building, taken once: empty sky at every
        storey, solid ground at the courses the plot is laid on.

        **The ring is a building-wide answer to a question asked of one part,
        and on a low building that is not enough.** A villa that lays its paving
        at the same height as its own ground floor fills a fifth of the ring
        there and passes a test set at a quarter, so the plate chosen for it is
        the terrain -- and a rectangle read off the terrain is the map, so the
        row cannot fail. `aloft` is the same question asked where it belongs:
        how much of the covered figure has **air directly under it**. Measured
        across the corpus the two populations do not overlap and are not close:
        a course of ground reads 0.00, 0.02, 0.06, and a real floor plate reads
        0.60 to 0.93. Zero, the default, turns the test off.

        Only `corners` asks for it, and that is a measurement rather than
        caution. What that row grades is the *share of its own rectangle* the
        build stands, which the terrain satisfies exactly and by construction.
        `plan_shape` grades the largest piece the build is missing, which the
        terrain does not satisfy -- a ground course is where a ground-laid part
        genuinely lives, and on one building reading it is what found an apron
        seventeen per cent off its own drawing. Requiring air under the plate
        there would have taken the plate away from seven parts across the
        corpus, that apron among them.

        Searched rather than taken from a table. A building is hollow, so a cut
        between two floors returns the wall ring, and a ring is a fifth of its
        own rectangle however square it is. `LEVELS` is no help -- it is chosen
        for counting components, and on one building its lowest entry sits
        between the ground and the first slab.

        The **lowest** plate that qualifies, not the best-scoring one. A part
        running the height of a tower has a plate at every storey and they are
        not the same shape: the top one is a roof with plant on it and a setback
        under it. Its own first floor is where its plan shape is.

        `windows` narrows the search for a figure that already knows where it
        lives: `{name: (y0, y1)}`, half-open, as a manifest declaration gives
        it. Without one the search caught a garden deck a metre under itself --
        the plinth it is laid on covers the same cells, qualifies as a plate,
        and is a different shape -- and reported the difference as the garden
        having been built wrong. A figure that declares its own height is not
        asking about any other.

        Returns the qualifying plates and, separately, the best cut found for
        every part, so a caller can say what it looked at when nothing
        qualified.
        """
        windows = windows or {}
        least = self._("CORNERS_COVER", 0.80)
        # How much of the plot round the building may stand at a course before
        # that course is the terrain rather than a floor. A quarter and not a
        # half: a row that reads the map against the map cannot fail, which is a
        # worse outcome than an ungraded row.
        clear = self._("CORNERS_CLEAR", 0.25)

        ring, ring_cells = self.outside_ring(model, read)
        step = max(1, model.height // 24)
        # Anchored at each window's own floor as well as at the building's, or
        # a part one course deep falls between two steps of the sweep and is
        # ungraded for want of ever having been looked at.
        levels = set(range(1, model.height, step))
        for low, high in windows.values():
            levels.update(range(max(1, int(low)),
                                min(model.height, int(high)), step))
        plates: dict[str, tuple[int, float, float, float]] = {}
        best: dict[str, tuple[int, float, float, float]] = {}
        for y in sorted(levels):
            layer = solid(model, y)
            # A second read of the model per level, so it is done only for a
            # caller that asked. `levels` starts at 1, so there is always a
            # course below.
            under = solid(model, y - 1) if aloft else None
            spill = (layer & ring).count() / ring_cells
            for name, mask in figures.items():
                low, high = windows.get(name, (0, model.height))
                if not low <= y < high:
                    continue
                cells = max(1, mask.count())
                over = layer & mask
                cover = over.count() / cells
                free = (over - under).count() / cells if under is not None else 1.0
                seen = best.get(name)
                if seen is None or cover - spill > seen[1] - seen[2]:
                    best[name] = (y, cover, spill, free)
                if (name not in plates and cover >= least and spill <= clear
                        and free >= aloft):
                    plates[name] = (y, cover, spill, free)
        return plates, best

    def plan_shape(self, g, model, read):
        """Whether the build stands where its own plan says, part by part.

        **Conformance, not resemblance.** The plan is an input, so a green row
        here says nothing about whether the building looks like the real one --
        `docs/sources.md`, "Сходство и соответствие". What it says is that the
        machinery between `layout.png` and the schematic kept the shape it was
        handed, which is an ordinary verification and not a tautology: the
        palette, the decomposition, the frame fit, the tracing, the pad, the
        closing, the straightening and the whole recipe sit between those two
        files, and when one of them distorts the plan it does so silently.

        Nothing did this before. `corners` asks a sharper question of a smaller
        set -- only the parts the map drew as rectangles -- and on two buildings
        of seven that set is empty and the row grades nothing at all. `COUNTS`
        counts components and cannot see a shape. The comparison sheets pair the
        build against the *capture*, never against the plan. So a wing built
        eight metres short of its own drawing, or a court the recipe filled in,
        had nothing anywhere to fail against.

        **Graded on the largest connected piece of the disagreement, not on the
        share.** The share cannot tell the two states apart: an outline that
        moved a cell all the way round -- the pad and the straightening doing
        what they are for -- and a wedge of forty-four cells past one corner
        came out at 0.981 and 0.974 on one real pair, six thousandths apart. The
        blob separates them at one to three cells against forty-four. So
        `PLAN_BLOB` is the budget and the shares are printed beside it.

        Material *outside* the plan is printed and graded only where a building
        sets `PLAN_OUTSIDE`, and that asymmetry is measured rather than timid: a
        balcony, a cornice, a canopy and a roof track all legitimately overhang
        a plan the map drew as walls, and across the corpus the largest such
        piece runs from nothing to a hundred and sixty cells with nothing wrong
        anywhere. There is no default that separates a wedge from a cornice.
        What separates them is that a wedge *appeared*, so the number is
        written into the report and `report.moved` is what raises it.

        Also draws the answer: `out/compare/plan-vs-build.png` lays the drawn
        plan and the build's own plates over each other in three colours. Four
        per cent unbuilt says nothing about *which* four per cent, and the
        difference between a cell all round the edge and the whole of one arm
        is the difference between rounding and a defect.
        """
        allowed = self._("PLAN_MISSING", 0.12)
        # A dozen cells. Under it the disagreement is an edge that moved, which
        # is what the pad and the straightening are for; over it it is a piece
        # of building in the wrong place. Measured rather than chosen: a clean
        # plate disagrees in ones and twos and the wedge that shipped was 44.
        blob = self._("PLAN_BLOB", 12)
        beyond = self._("PLAN_OUTSIDE", None)
        mass = self.site_mass(read)

        plates, best = self.plates(
            model, read, {n: p.mask for n, p in read.named.items()})
        measured: dict[str, dict] = {}
        # The picture is drawn against the whole **mass** and not against the
        # union of the parts, so that it says the same thing the numbers do.
        # `plan.decompose` subtracts the mapper's drawn line to find the parts,
        # so a cell on that line belongs to no part -- and against the union it
        # came out as "the build went outside the plan", a cool stripe down the
        # middle of a slab that is nothing of the sort.
        drawn_all = Mask(model.width, model.length)
        built_all = Mask(model.width, model.length)
        for name, part in read.named.items():
            found = plates.get(name)
            if found is None:
                at, cover, spill, _ = best.get(name, (0, 0.0, 1.0, 0.0))
                g.ungraded(
                    f"{name} stands where the plan says",
                    f"no floor plate of this build stands over it clear of the "
                    f"ground: the best cut is {at} m, covering {cover:.0%} of "
                    f"it with {spill:.0%} of the plot round the building filled "
                    "as well. Nothing here can be compared against the drawing")
                continue
            at = found[0]
            # Filled before it is compared, and filled at the building's own
            # reach rather than the part's. A storey is a ring of walls round
            # rooms and every level of a real building has light wells, shafts
            # and stairs in it, each of which would read as material the build
            # failed to place -- so the holes are closed. But a court is not a
            # hole: it is open to the sky at one end, and `holes` knows that
            # only while the mask has not been dilated far enough to bridge its
            # mouth. Dilated by six cells the E's courts closed over, filled,
            # and every part came back at nought per cent missing -- a row that
            # had stopped being able to fail.
            near = (self.plate_of(model, read, mass, at)
                    & part.mask.dilate(self.slack(read, 4.0)))
            shape = checks.conformance(part.mask, near, within=mass)
            measured[name] = {
                "at": at,
                "iou": round(shape["iou"], 4),
                "missing": round(shape["missing"], 4),
                "outside": round(shape["outside"], 4),
                "worst_missing": shape["worst_missing"],
                "worst_outside": shape["worst_outside"],
            }
            drawn_all = (drawn_all | part.mask
                         | (mass & part.mask.dilate(self.slack(read))))
            built_all = built_all | near
            ok = shape["worst_missing"] <= blob
            if beyond is not None:
                ok = ok and shape["outside"] <= beyond
            g.add(f"{name} stands where the plan says", ok,
                  f"the largest piece of the drawn part with nothing on it at "
                  f"{at} m is {shape['worst_missing']} cell(s), budget {blob}; "
                  f"{shape['missing']:.0%} of it is unbuilt in all and the two "
                  f"overlap {shape['iou']:.3f}. Material equal to "
                  f"{shape['outside']:.0%} of the part stands outside the whole "
                  f"drawn plan, worst piece {shape['worst_outside']} cell(s)"
                  + (f", allowed {beyond:.0%}" if beyond is not None
                     else " -- printed rather than graded, because a balcony, a "
                          "cornice and a canopy all overhang a plan drawn as "
                          "walls; set PLAN_OUTSIDE to hold this building to a "
                          "figure")
                  + ". Against the plan and not the reference: this says the "
                    "machinery kept the shape it was given")

        self.built_straight(g, model, read, mass, plates)
        self.pieces(g, model, read, mass, plates)

        if drawn_all.count():
            from . import compare

            path = Path(self.paths.OUT) / "compare" / "plan-vs-build.png"
            compare.overlay(drawn_all, built_all, path)
            print(f"[gate] {path}: grey where the plan and the build agree, "
                  "warm where the plan drew material the build did not stand "
                  "on, cool where the build went outside the plan")
        return measured

    def plate_of(self, model, read, mass, at: int) -> Mask:
        """The build's own floor plate at one level, as a solid figure.

        The level inside the building's own reach, with each piece's holes
        closed. Two cells of reach and not more -- `Grading.slack`, which is
        metres on a building square to the world and 2.8 m on one at
        forty-five degrees, because that is what two cells are there. The pad
        puts the built face half a metre outside the drawn one and a wall leans
        no further than that, while six cells bridges the mouth of a court and
        turns the court into a hole to be filled.

        **Every piece and not the largest one.** Taking the largest was the
        obvious reading of "the plate" and it is wrong on any building whose
        plan has more than one thing in it: a roof track standing over a bowl,
        a pavilion in a court, the second of two blocks. Dropped, those parts
        came back as a hundred per cent of the drawing unbuilt -- the loudest
        possible failure, about a part that was standing there the whole time.
        """
        found = solid(model, at) & mass.dilate(self.slack(read))
        out = Mask(found.width, found.length)
        for piece in found.components(min_cells=self._("MIN_PART")):
            out = out | piece | piece.holes()
        return out

    def built_straight(self, g, model, read, mass, plates):
        """Whether the outline the build stands on is one line or a staircase.

        `checks.jaggedness` is already asked of every part, and of the **map**:
        it reads the drawn mask and answers "how wobbly a line did the mapper
        draw". That is a real question and it is not this one. A plan can come
        back at 2 per cent -- a clean drawing -- while the build standing on it
        has an edge that steps 1, 2, 3, 2 cells at a time, because the pad, the
        closing, a hand-written rectangle and a mask union each put their own
        teeth in after the drawing was read. Nothing measured the result.

        The same measure, on the built outline. A perfectly straight edge at
        fifty degrees to the world grid is a staircase, and that is the point of
        defining jaggedness against `Mask.straighten` rather than against local
        roughness: an even staircase simplifies to one segment and scores near
        zero, and a staircase with uneven treads does not. It is the only
        measure in the pipeline that can say "a straight line stopped being
        straight", and until now it only ever asked it of the drawing.

        Taken over the whole plate rather than per part, because a part's own
        clipped slice inherits the drawn mask's boundary and would report the
        map's number back.
        """
        if not plates:
            return
        at = min(found[0] for found in plates.values())
        built = self.plate_of(model, read, mass, at)
        if not built.count():
            return

        budget = self._("BUILT_JAGGED", 0.15)
        found = checks.jaggedness(built, read.frame)
        drew = checks.jaggedness(mass, read.frame)
        g.add("the built outline is straight", found["share"] <= budget,
              f"{found['share']:.0%} of the plate at {at} m differs from its "
              f"own straightened reading, over {found['vertices']} straight "
              f"segments; budget {budget:.0%}. The map's own outline reads "
              f"{drew['share']:.0%} over {drew['vertices']} -- this row is the "
              "build's edge and that one is the drawing's, and a clean drawing "
              "does not stop the build from stepping")

    def pieces(self, g, model, read, mass, plates):
        """One drawn part, one plate: the floor over it does not arrive in two.

        `COUNTS` answers a version of this and answers it better -- how many
        things stand inside one declared part -- and it is a table somebody has
        to fill in with the right number before the run. It caught a floor
        coming apart twice on one building, and it is optional, so the case it
        cannot cover is the building where nobody thought to fill it in.

        This needs nothing filled in, because the drawing already carries the
        answer: what the map draws as one continuous part has one floor over
        it, and a plate that comes back in two pieces has come apart -- at the
        joint between two kinds of wall, at a court built closed, where an arm
        parted from its slab. None of those moves a silhouette, a section or
        the schedule.

        **Per part at its own plate, and not per level over the whole
        building.** The obvious form -- as many pieces standing as the map drew,
        at every level in `LEVELS` -- was tried and is simply false about real
        buildings: a stadium whose plan is one connected drum stands as six
        separate things at twenty metres and twenty at twenty-eight, all of them
        correct, because a ring, a roof track and a wall panel end at different
        heights. Connectivity in plan is not connectivity at a height, and only
        the second one has a floor to be measured on.

        Counted at the building's own reach rather than at the drawn edge. The
        pad puts a built face half a metre outside the line the map drew, so a
        cut taken at that line shaves the cells joining one part to the next: on
        one building it reported an eight-metre strip of wall as a second piece
        at four levels of five, and the building was sound. The reach is two
        cells, not two metres -- `Grading.slack`.
        """
        least = self._("MIN_PART")
        reach = mass.dilate(self.slack(read))
        for name, found in plates.items():
            at = found[0]
            part = read.named[name]
            over = solid(model, at) & reach & part.mask.dilate(self.slack(read))
            standing = over.components(min_cells=least)
            g.add(f"{name} is one plate at {at} m", len(standing) <= 1,
                  f"{len(standing)} piece(s) of floor over a part the map draws "
                  "as one"
                  + ("" if len(standing) <= 1 else
                     ", sizes " + ", ".join(str(p.count())
                                            for p in standing[:5])
                     + " -- a floor that came apart at the joint between two "
                       "kinds of wall, a court built closed, or an arm off its "
                       "slab"))

    def figures(self, model, read, derived, sched) -> dict[str, tuple]:
        """Every measured figure a squareness row could be asked about.

        A figure is a name, a mask saying what shape something is supposed to
        be, a note about who said so, and the courses it lives in where that is
        known. Three sources, in falling order of authority:

        *The plan's own parts.* The map is the authority for the plan, and a
        part is what it drew. This was the only source, and on the two most
        interesting plans in the corpus it yields nothing usable: the mapper
        draws no line between an arm of an E and the court beside it, so
        `plan.decompose` returns the whole E as one region, and one region
        shaped like an E is nowhere near its own bounding rectangle. The row
        went ungraded on exactly the buildings whose corners are worth asking
        about.

        *The runs `probes` measured along the building.* Cut the drawn mass at
        the u range of a run and the arm falls out of the E as its own figure --
        still the map's drawing, still the map's authority, just divided at a
        line the mapper did not draw and the geometry does. `facade_spans`
        already reads these for the same reason.

        *The footprints the manifest parts declared.* Not a drawing at all but
        `build.py`'s own claim, and worth a row for a reason the other two do
        not cover: `Canvas.fill` is last-write-wins, so a part declared square
        can be eaten into at a corner by a part drawn after it, and the
        declaration keeps saying square. Skipped where a plan part already has
        the name, and only ever the shape at a floor plate, so a declaration
        that spans forty metres of tower is compared against a storey of it --
        a storey **of its own**, which is what the y range is carried for. A
        garden deck one course deep, searched over the whole building, was
        graded against the plinth a metre under it and failed for being a
        different shape from something it is not.

        Figures too close to one already taken are dropped -- a run that is a
        part is one figure, not two -- and so are figures too small to have
        corners worth measuring.

        Near-*equal* and not contained, which is the difference between a
        working row and a silent one. Dropping a figure because a bigger one
        already covers it throws away every arm of the E, since an arm is by
        construction a subset of the E, and leaves the row with nothing but the
        letter it could not grade in the first place.
        """
        out: dict[str, tuple] = {}
        least = max(self._("MIN_PART"), 64)

        def keep(name: str, mask: Mask, whose: str, window=None) -> None:
            if name in out or mask.count() < least:
                return
            for held, _, _ in out.values():
                if iou(held, mask) >= 0.8:
                    return
            out[name] = (mask, whose, window)

        for name, part in read.named.items():
            keep(name, part.mask, "the map's own part")

        mass = self.site_mass(read)
        runs = (derived.get("wings") or {}).get("found") or []
        if runs:
            v0 = min(p.v0 for p in read.named.values()) - 8.0
            v1 = max(p.v1 for p in read.named.values()) + 8.0
            for i, run in enumerate(runs, 1):
                u0, u1 = float(run["u"][0]), float(run["u"][1])
                band = read.frame.rect(model.width, model.length,
                                       u0, u1, v0, v1)
                keep(f"run {i} (u {u0:.0f}..{u1:.0f})", mass & band,
                     "a run probes measured along this building")

        if sched is not None:
            for name, held in sched.built.items():
                keep(name, held.mask, "a footprint this build declared",
                     (int(held.y0), max(int(held.y0) + 1, int(held.y1))))
        return out

    def corners(self, g, model, read, derived=None, sched=None):
        """Whether the figures drawn square were built square.

        **This grades conformance and not resemblance, and the difference is
        the whole of how to read it.** The witness here is the plan, which is
        also what the build was drawn from, so nothing this row says bears on
        whether the building looks like the real one -- see `docs/sources.md`,
        which used to forbid the comparison outright and now separates the two
        questions instead. What it does say is whether the machinery between
        `layout.png` and the schematic kept the shape it was given, and that is
        an ordinary verification rather than a tautology: between those two
        files sit the palette, the decomposition, the frame fit, the tracing,
        the pad, the closing, the straightening and the whole of `build.py`.

        The failure it exists for is one the pipeline puts in by itself.
        `Site.footprint` closes a traced mask and pads it; both are Euclidean, a
        Euclidean dilation is a disc, and a disc takes about `pad + CLOSE` off
        every convex corner. Nothing else here can see it: a rounded corner
        casts the same silhouette, grades the same at every station, encloses
        the same area, and is as straight along both its edges as the map was --
        `jaggedness` compares a shape against its own straightened reading, and
        a chamfer survives that at any tolerance worth using.

        **Graded in metres off the corner and not in share of the box**, which
        is a correction. A pad and a closing take a quarter-disc of a metre and
        a half off a convex corner; four of those are seven square metres, and
        on a part of twelve hundred cells that is six tenths of one per cent
        against a budget of five. The share was thirty times too coarse to see
        the defect the row exists for, while being fully sensitive to things the
        plan never had: one villa lost eight per cent of its share to a facade
        rhythm of two-cell recesses -- ninety of its ninety-six missing cells
        further than four metres from any corner -- and failed for it twice.
        `checks.corner_reach` asks the question the row is named after instead:
        how far from each corner of the drawn box the material starts. Both
        readings are taken against the *drawn* box, so a corner the mapper
        rounded himself subtracts out and only what the machinery took is left.
        Across the corpus that difference is 0.00 m on nine graded figures of
        ten and 0.73 m on the tenth, against a `CORNERS_BITE` of one metre --
        a block. The share is still printed, because it is the number that says
        how much of the part is there at all.

        Two things it cannot do, and both matter because a green row here is
        easy to over-read:

        *It cannot see a corner the map itself drew wrong.* Where the mapper's
        own hand chamfered an arm and the build faithfully reproduced the
        chamfer, both sides of this comparison agree and the row passes. That
        case is `build-like-an-artist-not-a-mask`, and it is settled by a
        photograph and by `Site.squared`, not here.

        *It is one-directional.* The build is clipped to the drawn mask, so the
        row sees material the build failed to place and never sees material it
        placed outside the plan. The section and the silhouette sheets cover
        the other direction.

        Asked only of the figures drawn as rectangles, because only there is
        there an answer to compare against: a figure whose mask is `SQUARE_SAME`
        or more of the rectangle fitted to it is one of those, and the build
        should stand about as square there as it was drawn.

        **A figure is not only a plan part**, and that was the row's largest
        hole. On an E- and on an H-plan the mapper draws no line between an arm
        and the court beside it, `plan.decompose` hands back one region shaped
        like the whole letter, and a letter is nowhere near its own bounding
        box -- so the row printed "the map drew nothing here as a rectangle" on
        precisely the two buildings with the most corners in them. `figures`
        adds the runs `probes` measured along the building, which cut the arms
        out of the E at lines the geometry draws, and the footprints the
        manifest parts declared, which catch a square part eaten into at a
        corner by a part `Canvas.fill` drew after it.

        Read off a floor plate of the build itself rather than off anything
        declared, so it cannot be answered by naming a part. **Which** plate is
        searched for and not taken from a table: a building is hollow, so a cut
        between two floors returns the wall ring and a ring is a fifth of its
        own rectangle however square it is. `LEVELS` is no help here -- it is
        chosen for counting components, and on one building its lowest entry
        sits between the ground and the first slab. So every level is tried and
        the plate that covers most of the drawn figure wins; a figure that has
        no such plate is ungraded rather than failed, because the row could not
        find the thing it was going to measure.

        **And the plate has to have air under it**, which is the part that was
        missing. The ring round the building is a building-wide answer, and a
        villa one storey high that paves its terrace at the height of its own
        ground floor fills a fifth of that ring -- under the quarter allowed --
        so the plate chosen for it was the terrain. A rectangle read off the
        terrain is the map, the map is what the figure was drawn from, and the
        row came back 100% square on every run because it could not come back
        anything else. Two buildings of seven were in that state, and a third
        graded one of a pair of roof tracks off the stands twenty metres under
        it. Asking how much of the covered figure stands over air separates the
        two cleanly: 0.00, 0.02 and 0.06 for a course of ground against 0.60 to
        0.93 for a floor plate, with nothing anywhere near `CORNERS_ALOFT`.
        """
        square = self._("SQUARE_SAME", 0.90)
        bite = self._("CORNERS_BITE", 1.0)
        least = self._("CORNERS_COVER", 0.80)
        offered = self.figures(model, read, derived or {}, sched)
        wanted = {}
        for name, (mask, whose, window) in offered.items():
            found = checks.rectangular(mask, read.frame)
            if found["share"] >= square:
                wanted[name] = (mask, whose, window, found)
        if not wanted:
            g.ungraded(
                "corners",
                f"nothing here is drawn as a rectangle -- of "
                f"{len(offered)} figure(s) offered (the map's parts, the runs "
                f"probes measured, the footprints the build declared) none "
                f"reaches {square:.0%} of its own fitted box -- so there is no "
                "corner this row can hold the build to.")
            return

        # A plate this row reads has to have air under it -- see `plates`.
        # Without that the villa and the hotel both graded their own terrain,
        # which is the map, against the map: 100% covered, 100% square, green
        # on every run and unable to be anything else.
        alofted = self._("CORNERS_ALOFT", 0.50)
        plates, best = self.plates(
            model, read, {n: f[0] for n, f in wanted.items()},
            {n: f[2] for n, f in wanted.items() if f[2] is not None},
            aloft=alofted)
        for name, (mask, whose, _, drawn) in wanted.items():
            at, cover, spill, free = (plates.get(name)
                                      or best.get(name, (0, 0.0, 1.0, 0.0)))
            if name not in plates:
                # Three different nothings, and saying which is the difference
                # between a gap and an expected silence. A figure that covers
                # itself and the plot around it is a ground, and a ground has
                # no floor plate by construction -- `RECTANGULAR` is where its
                # shape is graded. A figure that is covered but has material
                # under all of it is the same ground seen from inside, on a
                # building too low for the ring to notice. A figure nothing
                # covers is a wall, a rail or a set of panels, which has no
                # floor either.
                if spill > self._("CORNERS_CLEAR", 0.25):
                    why = ("this is a surface laid over the plot -- a ground "
                           "has no floor plate to read, and its shape is "
                           "RECTANGULAR's row")
                elif cover >= least:
                    why = (f"only {free:.0%} of that stands over air, under "
                           f"{alofted:.0%} -- the cut is the ground the figure "
                           "is laid on, and a rectangle read off the ground is "
                           "the map graded against itself")
                else:
                    why = (f"under {least:.0%} covered the row is looking at a "
                           "wall, a rail or a ring rather than a floor")
                g.ungraded(
                    f"{name} is built square",
                    f"no floor plate of this build stands over it clear of the "
                    f"ground: the best cut is {at} m, which covers {cover:.0%} "
                    f"of it with {spill:.0%} of the plot round the building "
                    f"filled as well -- {why}")
                continue
            # Cut to the figure's *own* mask, the way `divisions` counts inside
            # a declared footprint and for the same reason: anything crossing
            # between two parts stands in the same layer, and a shape taken over
            # a box round one of them is that part welded to its neighbour.
            # Clipped this way a rounded corner shows as the thing it is --
            # cells missing where material was drawn -- and nothing else can
            # leak in.
            mine = solid(model, at) & mask
            built = checks.rectangular(mine, read.frame)
            angles = checks.corners(mine, read.frame)
            # Both readings against the *drawn* box, so a corner the mapper
            # rounded himself is subtracted out and only what the machinery
            # took is left. A box refitted to the build travels with the
            # defect and would report nothing.
            was = checks.corner_reach(mask, read.frame, drawn["box"])
            now = checks.corner_reach(mine, read.frame, drawn["box"])
            lost = max(b - a for a, b in zip(was["reach"], now["reach"]))
            g.add(f"{name} is built square",
                  lost <= bite,
                  f"{whose}, on its plate at {at} m ({free:.0%} of it over "
                  f"air): the corners of its own box stand {now['reach']} m "
                  f"from material, drawn {was['reach']} m, so the worst lost "
                  f"{lost:.2f} m of {bite:.2f}. It fills {built['share']:.0%} "
                  f"of that box against {drawn['share']:.0%} drawn, on "
                  f"{angles['right']} right angle(s) of {angles['vertices']} "
                  "-- printed and not graded: four bitten corners are six "
                  "tenths of a per cent of a part this size, and a facade "
                  "rhythm the plan never had is eight. Against the plan and "
                  "not against the reference: this says the machinery kept "
                  "the shape it was given, and says nothing about whether the "
                  "shape is the building's. A pad and a closing are both discs "
                  "and both bite a convex corner; `Site.squared` draws the "
                  "rectangle instead")

    def evenness(self, g, model, read, sched, derived):
        """Whether the building agrees with itself.

        Every other row here compares the build against something outside it,
        and that is exactly why none of them can see this. Two towers of the
        same footprint cast the same silhouette and the same profile whatever
        their facades do; a parapet built at nine heights along one roof passes
        every station it is graded at, because it was read from nine stations.
        Both are the first thing anybody notices in a render and neither is a
        number anywhere else in this pipeline.

        Driven by two tables in the building's own gate, because only the
        building knows what it repeats:

            TWINS   ((a, b), ...) or ((a, b, axis_u), ...) -- **plan** parts
                    that are mirror images. Plan parts and not declared ones,
                    because the mirror plane is read off their extents and only
                    the plan has those; the axis defaults to halfway between.
            LEVEL   {part: how many distinct top heights it may have} -- one for
                    a flat roof, two or three for a real step. A plan part is
                    looked up first and a declared one second, and the plan part
                    is usually what is wanted: a declaration merges every call
                    that made it, so "parapets" on a building with a podium and
                    two towers is three different horizontals in one mask and
                    the count of them means nothing.

        An empty `TWINS` is a row and not a silence, for the reason `COUNTS` is:
        an unasked question and a satisfied one print the same nothing. A
        building with genuinely nothing repeated says `SINGULAR = True`.

        **And one question is asked without any table at all**, because five
        buildings of six answered `SINGULAR = True` and every one of them was
        telling the truth. `TWINS` asks about a *mirror pair*, and a mirror pair
        is a rare thing: what these buildings actually repeat is storeys, rows of
        villas, bays of a facade -- copies by translation, for which there is no
        reflection to grade. A row that everybody steps over the same way has
        stopped being a question, and the fix is the one `facades` got: not to
        forbid the exit, but to ask something the exit does not cover.
        `self.cadence` is that something. It needs no table to be asked, and the
        one table it does take only ever takes parts *out*:

            NOT_WALLS  (part, ...) -- **plan** parts that have no storeys to
                       stand at, because they are not walls. A retractable roof
                       vault, a girder track, a plaza apron. Named parts are
                       printed in a row of their own; a name the plan does not
                       draw stops the gate, so that renaming a part cannot
                       quietly hand it back.
        """
        c = self.c
        pairs = self._("TWINS", ())
        if not pairs and not self._("SINGULAR", False):
            g.ungraded(
                "evenness",
                "TWINS is empty, so nothing checks that the parts this building "
                "repeats are actually the same. A mirror pair is measured once "
                "per copy and photogrammetry does not read the two alike, so "
                "the difference between the readings gets built as if it were "
                "the building -- and every other row here passes, because two "
                "parts of the same footprint have the same silhouette and the "
                "same profile. Fill it in, or set SINGULAR = True.")

        budget = self._("TWINS_SAME", 0.95)
        for pair in pairs:
            a_name, b_name = pair[0], pair[1]
            a, b = read.named[a_name], read.named[b_name]
            # Which way the pair stands is read off the pair rather than
            # configured: whichever axis separates their centres more is the one
            # the mirror plane cuts. Two towers end to end mirror across u, two
            # bars facing each other over a court mirror across v, and nobody
            # has to say which.
            along = ("u" if abs((a.u0 + a.u1) - (b.u0 + b.u1))
                     >= abs((a.v0 + a.v1) - (b.v0 + b.v1)) else "v")
            gap = ((min(a.u1, b.u1), max(a.u0, b.u0)) if along == "u"
                   else (min(a.v1, b.v1), max(a.v0, b.v0)))
            axis = pair[2] if len(pair) > 2 else 0.5 * (gap[0] + gap[1])
            found = checks.twins(model, a.mask, b.mask, read.frame, axis, along)
            # One decimal, because the interesting cases sit on the budget and
            # "95% against a budget of 95%" reads as a bug in the check.
            g.add(f"{a_name} mirrors {b_name}", found["same"] >= budget,
                  f"{found['same']:.1%} of {found['cells']} paired columns "
                  f"agree, worst course y {found['worst_course']} with "
                  f"{found['worst']} cells; budget {budget:.1%}")

        # How much of each drawn part is the map's hand rather than the
        # building. Always asked, with a default rather than a table, because a
        # sawtoothed edge is a thing every map-read building can have and an
        # opt-in check for it would be filled in by nobody -- the whole point is
        # that nothing else can see it.
        #
        # **Per part, and switched off per part.** `JAGGED` takes a dict as well
        # as a number, keyed on the plan's part names with `"*"` for the rest.
        # It used to be one number for the building, and one number for the
        # building is one switch for the building: a plan with a single curved
        # front and three rectangular wings sets `JAGGED = None` for the curve
        # -- correctly, because an arc is a run of corners and not a wobble --
        # and the three wings stop being checked at the same moment, silently.
        # That happened, and the wings shipped sawtoothed.
        #
        # A part whose check is off is named in a row of its own. Switching a
        # check off is a decision and it should cost a line in the report, the
        # same way an unfilled table does.
        budget = self._("JAGGED", 0.15)
        floor = self._("JAGGED_FLOOR", 200)
        per = budget if isinstance(budget, dict) else {}
        fallback = per.get("*") if per else budget
        off = []
        for name, part in read.named.items():
            allowed = per.get(name, fallback) if per else fallback
            if allowed is None:
                off.append(name)
                continue
            if part.mask.count() < floor:
                continue
            found = checks.jaggedness(part.mask, read.frame)
            g.add(f"{name} is drawn straight", found["share"] <= allowed,
                  f"{found['share']:.0%} of it is wobble -- {found['cells']}"
                  f" cells differ from its own straightened reading, which "
                  f"is {found['vertices']} straight segments; budget "
                  f"{allowed:.0%}")
        if off:
            g.ungraded(
                "drawn straight",
                "straightness is not graded on " + ", ".join(sorted(off))
                + ". That is right for a part whose own wobble is a measurement "
                "-- an arc, a rake, a shoreline -- and it is the only thing in "
                "this pipeline that can see a sawtoothed edge, so it is worth "
                "being sure the part named is the curved one. JAGGED takes a "
                "dict keyed on part names, with \"*\" for the rest.")

        for name, allowed in self._("LEVEL", {}).items():
            where = (read.named[name].mask if name in read.named
                     else sched.built[name].mask)
            found = checks.level_runs(model, where)
            g.add(f"{name} is level", found["count"] <= allowed,
                  f"{found['count']} distinct top height(s), "
                  f"{found['share']:.0%} of {found['cells']} cells at the "
                  f"commonest; allowed {allowed}")

        self.cadence(g, model, read, derived)

    def cadence(self, g, model, read, derived):
        """Whether the build stands at the storey somebody measured for it.

        Every storey figure in this pipeline is a figure about the building and
        none of them is a figure about the build: the capture's vertex histogram,
        a count off a photograph, a spacing declared in words. `storey height
        agree` compares two of those to each other and never asks what got laid.
        So the schedule's own warning -- stamp one step and give up a floor,
        never distribute the remainder -- is a rule with nothing behind it. A
        build that spent its remainder as 3-2-3-3-2 has the same total height,
        the same silhouette, the same section at every station and the same one
        storey number as a build that is right, and looks like a stack of shelves
        in the first render anybody opens.

        `checks.cadence` reads the period off the outside wall of each plan part,
        the same signal and the same autocorrelation the capture is read with,
        and this compares it to the storey `Site.storeys` laid the levels at --
        the measured or declared spacing, times whatever vertical stretch that
        building's `derived.json` carries, which is the one measured factor there
        is and the same one `Site` is handed. Reading it from there rather than
        taking it as a setting is deliberate: a gate that asked the building to
        restate its own scale would be graded against a number typed twice.

        The slack is **six tenths of a block**: half of it is the rounding the
        build is required to do, since a storey is a whole number of blocks and a
        storey of 4.5 may land on either side, and the tenth on top of it keeps a
        storey of exactly one half off a knife edge. Wider than that stops
        distinguishing a storey of three from a storey of four, which is the
        whole of what this asks.

        A remainder spread over the levels is what this catches, and it does not
        catch it by scoring badly. A wall stepping 3-2-3-3-2 correlates *well* --
        r=0.69 over sixty courses, because the five-course group repeats exactly
        -- and reports a period of five against a storey of three. It fails here
        by being a different number, which is the only place in this pipeline
        that number is looked at.

        Only parts whose wall carries a readable rhythm are graded, and that is
        not a loophole: a blank wall has nothing to correlate, exactly as a
        smooth model has nothing for `measure.storey_height`, and a low score is
        that fact rather than a small period. It *is* a loophole if the reference
        found a rhythm and the build shows none -- the facade was measured
        ribbed and built flat -- so that case is named rather than passed over,
        as `ungraded` and not as a failure: the capture reads its rhythm off the
        bands it was pointed at, and a part can honestly carry one only on the
        face those bands were on.
        """
        storeys = derived.get("storeys") or {}
        clear = self._("CADENCE_CLEAR", 0.5)
        slack = self._("CADENCE_SLACK", 0.6)
        name = "the storeys are the storey that was measured"

        # Named first, so that it prints on every path out of here. A building
        # that takes eight of its ten parts out of this check and then goes
        # ungraded for want of a storey figure would otherwise print the second
        # fact and not the first, which is the wrong way round: the list is why
        # the rest of the row is as short as it is.
        #
        # `NOT_WALLS` is not a way of quietening a row that failed: a retractable
        # roof vault, a girder track and a plaza apron are all drawn as plan parts
        # and none of them has a storey, so asking one where its floors are is a
        # question with no right answer -- the arch on this corpus reports a
        # period of four because that is the pitch of its curvature. Naming them
        # costs a printed row, the same way switching off `JAGGED` for a part
        # does.
        not_walls = set(self._("NOT_WALLS", ()))
        strangers = not_walls - set(read.named)
        if strangers:
            raise SystemExit(
                f"NOT_WALLS names {', '.join(sorted(strangers))}, which the plan "
                "does not draw. A part renamed out from under this list would "
                "otherwise go back to being graded silently.")
        if not_walls:
            g.ungraded(
                "the parts that are not walls",
                "no storey is asked of " + ", ".join(sorted(not_walls))
                + ", because NOT_WALLS says they are not walls. Right for a roof "
                "vault, a girder track and a plaza apron, and wrong for anything "
                "with a facade on it -- so it is worth reading the list back "
                "rather than the reason it was written.")

        if not storeys.get("found") or not storeys.get("spacing"):
            g.ungraded(
                name,
                "nothing in derived.json says what a storey of this building is "
                f"({storeys.get('why', 'no `storeys` block at all')}), so the "
                "period the build stands at has nothing to be compared against. "
                "`derive` writes it from the capture or from DECLARED_STOREY.")
            return
        # The stretch is applied the same way and in the same order `Site.storeys`
        # applies it, so that the number here is the number the levels were laid
        # at and not a second opinion about it.
        stretch = (derived.get("stretch") or {}).get("vertical", 1.0)
        storey = storeys["spacing"] * stretch

        # Only the parts the correlation actually ran on. A part too short to
        # show the period twice is not a wall that read blank, and folding the
        # two together would print `r=0.00` against a canopy as though somebody
        # had looked.
        read_off = {}
        for part_name, part in read.named.items():
            if part_name in not_walls:
                continue
            found = checks.cadence(model, part.mask)
            if found["read"]:
                read_off[part_name] = found
        ribbed = {n: f for n, f in read_off.items() if f["score"] >= clear}
        best = max(read_off.values(), key=lambda f: f["score"], default=None)

        told = (f"a storey here is {storeys['spacing']:.2f} m "
                f"{storeys.get('by', 'measured')}"
                + (f" and the build stretches it by {stretch:.4f}"
                   if stretch != 1.0 else "")
                + f", so its walls owe a period of {storey:.2f} block(s)")
        if not ribbed:
            found = ("the strongest reading is "
                     f"r={best['score']:+.2f} over {best['courses']} course(s)"
                     if best else "no part of it is tall enough to show a period")
            if storeys.get("score", 0.0) >= clear:
                g.ungraded(
                    name,
                    f"{told}, and no wall of this build shows one: {found}, "
                    f"against a floor of r={clear:+.2f}. The capture did find a "
                    f"rhythm here (r={storeys['score']:+.2f}), which leaves two "
                    "readings: the facade was measured ribbed and built flat, or "
                    "the capture read its rhythm off bands on a face this build "
                    "carries plainly. Look at an elevation before believing "
                    "either.")
                return
            g.ungraded(
                name,
                f"{told}, and no wall of this build carries a readable rhythm: "
                f"{found}, against a floor of r={clear:+.2f}. That is the honest "
                "answer for a blank wall and for a building of two storeys -- "
                "there is nothing for a period to repeat in -- and it is also "
                "what a facade whose rhythm was flattened looks like. The "
                "reference cannot break the tie: it did not find a rhythm either "
                f"(r={storeys.get('score', 0.0):+.2f}).")
            return

        for part_name, found in sorted(ribbed.items()):
            off = abs(found["period"] - storey)
            g.add(f"{part_name} stands at that storey", off <= slack,
                  f"{told}; its wall repeats every {found['period']:.0f} block(s)"
                  f" in {found['by']} (r={found['score']:+.2f} over "
                  f"{found['courses']} course(s), {found['cells']} cells of "
                  f"wall), which is {off:.2f} off a slack of {slack:.2f}")
        quiet = sorted(set(read_off) - set(ribbed))
        if quiet:
            g.ungraded(
                "the walls without a rhythm",
                "no period is read off " + ", ".join(quiet)
                + f", all under r={clear:+.2f} though each is tall enough to be "
                "asked. Not a failure and not a small period: a blank wall and a "
                "wall of one material have nothing to correlate. Printed because "
                "the graded rows above are silent about them, and a facade built "
                "flat that should be ribbed lands in exactly this list.")

    def watertight(self, g, cut):
        c = self.c
        names = self._("WATERTIGHT", ())
        if names and self._("WATERTIGHT_AT", 0) not in self._("LEVELS"):
            raise SystemExit(
                f"WATERTIGHT_AT is {self._("WATERTIGHT_AT", 0)} m and LEVELS are "
                f"{self._("LEVELS")}: the watertightness test reads the cut this gate "
                "already took, and there is no cut at that height. Add it to "
                "LEVELS or move it onto one.")

        for name in names:
            rings = cut[name][self._("WATERTIGHT_AT", 0)]
            leaking = [r for r in rings if holds(r) == 0]
            g.add(f"{name} watertight", not leaking,
                  f"all {len(rings)} hold what they enclose" if not leaking
                  else f"{len(leaking)} of {len(rings)} at {self._("WATERTIGHT_AT", 0)} m "
                       "are open to the outside")

    def witnesses(self, g, derived):
        """Rows that can fail for a reason no section can produce.

        A section compares the build against one reference; these compare two
        references against each other, and catch what is wrong with the reference
        itself: a rescaled crop, a capture of the building next door, a model of
        a later revision, a drawing read at the wrong scale.

        The numbers are `derive`'s -- measured once, written to derived.json,
        read here. The gate grades; it does not re-measure.
        """
        for one in derived.get("witnesses", []):
            name = f"{one['question']} agree"
            if one["ok"] is None:
                g.ungraded(name, one.get("expected")
                           or "only one source can answer this")
                continue
            worst = one["worst"]
            where = max(one["rows"],
                        key=lambda k: abs(one["rows"][k][0] - one["rows"][k][1]),
                        default="")
            g.add(name, one["ok"],
                  f"{one['pair']}: worst {worst:.2f} {one['unit']}"
                  + (f" at {where}" if where else "")
                  + f", tolerance {one['tolerance']:.2f} {one['unit']}")

    def coverage(self, g, derived, sched, read):
        """Parts the build put up that nothing measured.

        The row this pipeline did not have. A declaration check asks whether
        blocks stand inside a mask the build itself drew, which is the build
        marking its own homework; this asks whether anything outside the build
        ever stated where that part goes or how tall it is. On the building this
        was written after, nine parts of fourteen would have failed here, and
        every one of them printed green.

        Two ways in, because the names do not line up. A schedule item that
        shares a name with a derived part and has dashes in every provenance
        column is the first: the build put that surveyed part up and nothing
        measured it. A schedule item that shares no name -- `villas` against
        `north_tower` -- is the second: if it stands taller than one storey
        and its footprint lies mostly outside the surveyed mass, it is a
        building the plan never drew. A floor, a run of glazing, a one-course
        podium are neither, and are not accused of lacking a footprint.

        The three provenance columns are read independently. Today `witness`
        is "section" exactly when `height` was read off the reference, so the
        third test adds nothing; when that changes, a part whose only evidence
        is a section still counts as covered, and a part whose height was
        measured still counts if the witness column later says none.

        `UNMEASURED` is the building's own list of parts it knows nothing
        measured -- a phrase per part, not a flag. Named there, the row goes
        ungraded with the reasons printed rather than failing: a part standing
        on a photograph alone is a legitimate way to build and an illegitimate
        thing to report as checked. A name in the table that this row does
        not treat as unmeasured stops the run, the same way `NOT_WALLS` does.
        """
        stated = {r["name"]: r.get("provenance", {})
                  for r in derived.get("parts", [])}
        excused = self._("UNMEASURED", {}) or {}
        if not isinstance(excused, dict):
            raise SystemExit(
                "UNMEASURED is a dict of part to reason, not a flag. A part "
                "standing on a photograph is named here with why nothing "
                "measured it; a bare True would turn the row off.")

        storey = float((derived.get("storeys") or {}).get("spacing") or 0.0)
        mass = self.site_mass(read)
        if mass and (mass.width, mass.length) != (0, 0):
            covered = mass.dilate(self.slack(read))
        else:
            covered = None

        # `notes` is what the row prints next to a name; `measured` / `inside`
        # are what the pass text is allowed to claim -- only the names this
        # loop actually classified, never every key in derived["parts"].
        blind, notes = [], {}
        measured, inside = [], []
        for name in sorted(sched.by_name):
            p = stated.get(name)
            if p is not None:
                if (p.get("plan") in ("map", "vector", "model", "capture")
                        or p.get("height") in ("capture", "model")
                        or p.get("witness") == "section"):
                    measured.append(name)
                    continue
                notes[name] = "named on the plan, nothing in any column"
                blind.append(name)
                continue

            held = sched.built.get(name)
            if held is None:
                continue
            rise = float(held.y1) - float(held.y0)
            cells = held.mask.count()
            if cells == 0 or rise <= storey * COVERAGE_STOREYS:
                inside.append(name)
                continue
            if (covered is None
                    or (held.mask.width, held.mask.length)
                    != (covered.width, covered.length)):
                share = 0.0
            else:
                share = (held.mask & covered).count() / cells
            if share >= COVERAGE_SHARE:
                inside.append(name)
                continue
            notes[name] = f"{rise:.0f} m, {share:.0%} on the plan"
            blind.append(name)

        strangers = sorted(set(excused) - set(blind))
        if strangers:
            raise SystemExit(
                f"UNMEASURED names {', '.join(strangers)}, which this row "
                "does not treat as unmeasured. A renamed part, a typo, or a "
                "surface named here would otherwise go back to being silent.")

        named = [n for n in blind if n in excused]
        red = [n for n in blind if n not in excused]
        limit = storey * COVERAGE_STOREYS
        rule = (f"taller than {COVERAGE_STOREYS:g} storey "
                f"({limit:.2f} m here) with less than {COVERAGE_SHARE:.0%} "
                "of the footprint on the plan, or named on the plan with "
                "nothing in any provenance column")

        def listed(names):
            return ", ".join(
                f"{n} ({notes[n]})" if n in notes else n for n in names)

        if red:
            g.add("every part is measured by something", False,
                  f"{len(red)} part(s) have no measured footprint, no "
                  f"measured height and no witness: {listed(red)}. "
                  f"Unmeasured is {rule}. Measure the footprint, measure "
                  "the height, widen the clip so the reference reaches "
                  "them, or name them in UNMEASURED with the reason they "
                  "cannot be measured.")
            return
        if named:
            g.ungraded("every part is measured by something",
                       "; ".join(f"{n}: {excused[n]}" for n in named)
                       + f". Unmeasured is {rule}")
            return

        bits = []
        if measured:
            bits.append(f"{len(measured)} plan-named part(s) are measured")
        if inside:
            bits.append(
                f"{len(inside)} attachment(s) sit on the plan or stand "
                f"{COVERAGE_STOREYS:g} storey or less")
        if not bits:
            bits.append("the schedule names no part this row examines")
        g.add("every part is measured by something", True,
              "; ".join(bits) + "; nothing stands unmeasured")

    def section(self, g, derived, read, reference, model, frame, parts,
                evidence):
        c, derive = self.c, self.derive

        if reference is None:
            # Not a failure. Nothing supplied says where material stands, so
            # there is nothing for the section to disagree with -- and saying
            # `pass` here would mean "checked" when it means "unchecked".
            g.ungraded(
                "section",
                "no model and no capture, so nothing states where material "
                "actually stands. Every height in this build is declared, and "
                "nothing here can agree or disagree with it.")
            return [], None

        if read.massing is not None and reference.name == read.source.name:
            # The model that supplied the plan also supplies the heights, so
            # there is one fit and no registration between two frames.
            mesh = model3d.load(reference.path, up=derive.MODEL_UP,
                                scale=derive.MODEL_SCALE)
            datum = read.massing.datum
            mesh_frame = survey.with_the_plan(read, read.massing.model_frame)
        else:
            mesh = Mesh.read(reference.path)
            datum = mesh.ground()
            # The reference's own frame, fitted above `MESH_FLOOR` rather than
            # at the ground: a clip that carries a podium, a terrace or a belt
            # of trees fits its frame to those and comes back longer than the
            # building. That number is the building's own, so it is read from
            # `derive` and not typed here -- a gate with its own floor grades
            # every station in a frame the measurements were never taken in.
            #
            # Turned with the plan, for the same reason the survey turns it: a
            # plan on a lattice slope and a reference on its own fit are two
            # coordinate systems, and a registration can only express one.
            mesh_frame = survey.with_the_plan(
                read, mesh.frame(datum=datum, floor=derive.MESH_FLOOR))

        cloud_mesh = gate.Cloud.from_mesh(mesh, mesh_frame, datum=datum)
        cloud_build = gate.Cloud.from_model(model, frame, skip=self._("SOFT", set()))
        shape = self._("build_cloud")
        if shape is not None:
            cloud_build = shape(cloud_build, derived)

        # The plan, as the thing that says which stations belong to this
        # building at all. Material in the clip outside it is a neighbour, and
        # a section that grades it is grading the next lot.
        drawn = gate.Plan.from_parts(parts, frame)

        self.clip_box(g, derived)

        reg = self.registration(cloud_mesh, cloud_build, drawn, derived)

        # A building may declare a disagreement it cannot make agree -- two
        # sources of knowingly different proportion, a game map against a real
        # capture. The declaration is in `derive.EXPECTED`, with its reason.
        expect = dict(getattr(derive, "EXPECTED", {}))
        g.witness("registration", reg.agrees(), reg.detail,
                  expect.get("registration", ""))

        # Size, separately from position. A build the right shape in the wrong
        # place fails the registration; a build the right place and the wrong
        # size fails this, and the two are fixed in different files.
        g.witness("scale", reg.scaled(self._("SCALE_TRUE")),
                  f"the build is {1 / reg.u_scale:.2f}x by "
                  f"{1 / reg.v_scale:.2f}x of the {reference.name}, tolerance "
                  f"{self._("SCALE_TRUE"):.0%}",
                  expect.get("scale", ""))

        # Sections only if the registration holds, or if the building declared
        # that it cannot. Stations cut through a fit that does not agree are
        # not measurements of the building -- they are measurements of the
        # disagreement, printed once per station, and they bury the one row
        # that says what is actually wrong.
        sections = []
        if reg.agrees() or expect.get("registration"):
            cuts = (self._("windows") or windows)(parts, frame, derived,
                                                  read.named)
            seams = tuple(u1 for _, _, u1, _ in cuts[:-1])
            sections = [
                gate.section(name, u0, u1, mesh=cloud_mesh, build=cloud_build,
                             plan=drawn, registration=reg,
                             tolerance=self._("TOLERANCE"), exemptions=allowed,
                             seams=seams)
                for name, u0, u1, allowed in cuts]
            for section in sections:
                g.take(section)

        # And whether any of that was an outside check at all. A section cut
        # against the same file the plan was read off grades the simplification.
        if not evidence.independent:
            others = [w.name for w in evidence.witnesses_for("plan")]
            g.ungraded(
                "independent witness",
                f"the {reference.name} the section is cut against is also what "
                "the plan was read off, so the section grades the "
                "simplification and cannot catch the reference itself being "
                "wrong. "
                + (f"The plan alone is witnessed by {', '.join(others)}, which "
                   "the comparison sheets score by silhouette; the heights are "
                   "not witnessed by anything."
                   if others
                   else "A second source -- a map, a survey, photographs -- is "
                        "what would make it an outside check."))

        return sections, reg

    def site_covered(self, g, derived, sched, read):
        """Whether the reference reaches as far as the build does.

        The clip is a decision made at stage one, when the only thing anybody
        has looked at is an ortho of the capture, and it is the decision that
        quietly bounded everything after it. Clipped around the building, the
        reference holds nothing of the grounds, so every part standing on them
        is graded by nothing and no row says so.

        This is that row. It compares the extent of what the build declared
        against the extent of the reference, in the plan's (u, v), and prints
        the box that would have covered both, so that a re-clip is a copy of
        two numbers rather than a second look at an ortho. World cells are
        the wrong space: the fixture sits thirty degrees off the axes, and
        a world AABB compared to a plan AABB is a false red.
        """
        rec = derived.get("mesh") or {}
        mesh = rec.get("bounds") if rec.get("read") else None
        if not mesh:
            g.ungraded("the clip holds the site",
                       "no reference: nothing states how far the site reaches")
            return
        built = sched.extent(read.frame)
        over = [
            ("west", mesh["u0"] - built[0]),
            ("east", built[1] - mesh["u1"]),
            ("north", mesh["v0"] - built[2]),
            ("south", built[3] - mesh["v1"]),
        ]
        worst = [(side, gap) for side, gap in over if gap > CLIP_HOLDS_WITHIN]
        if not worst:
            g.add("the clip holds the site", True,
                  f"the build stands u {built[0]:.0f}..{built[1]:.0f}, "
                  f"v {built[2]:.0f}..{built[3]:.0f}, all of it inside the "
                  "reference")
            return
        g.add("the clip holds the site", False,
              "the build reaches past the reference by "
              + ", ".join(f"{gap:.0f} m {side}" for side, gap in worst)
              + f". Re-clip to hold u {min(built[0], mesh['u0']):.0f}.."
                f"{max(built[1], mesh['u1']):.0f}, "
                f"v {min(built[2], mesh['v0']):.0f}.."
                f"{max(built[3], mesh['v1']):.0f} and re-measure: everything "
                "out there is graded by nothing. The plan canvas is sized to "
                "the drawn building, not the site -- that is a different "
                "decision, and this row does not widen it.")

    def clip_box(self, g, derived):
        """Whether the frame the section is cut in is the building or the box.

        `survey.fits_clip_box` measures it and writes the answer; this reports
        it, because a measurement nobody reads is not a check. Ungraded rather
        than failed: what it says is "this run cannot tell you whether the frame
        is real", and the fix is a re-clip or a datum, neither of which the gate
        can make.
        """
        found = derived.get("mesh", {}).get("frame", {}).get("clip_box")
        if not found or not found.get("checked"):
            return
        if not found.get("is_the_box"):
            return
        g.ungraded(
            "frame",
            f"the frame fitted to the reference is {found['frame'][0]:.1f} x "
            f"{found['frame'][1]:.1f} m and the clip it was cut with is "
            f"{found['clip'][0]:.1f} x {found['clip'][1]:.1f} m -- the same to "
            f"within {found['worst']:.2f} m on both axes. A fit that lands on "
            "the clip box has measured the box: either the datum is under the "
            "ground, or MESH_FLOOR is below the terrain skirt, or the clip is "
            "loose enough that its own ground plane survived the cut. Every "
            "station below is read through that frame, so they are all "
            "consistent with each other and none of them is evidence.")

    def registration(self, cloud_mesh, cloud_build, drawn, derived):
        """One registration, not two.

        `derive` already fitted the plan to the reference, and every number in
        the build came through that fit; fitting a second one here -- on a
        fraction of the build's own height, which moves whenever the build does
        -- grades those numbers through a different map. Where the two differed
        by five metres, the section reported the gap between them as the
        building's error.

        `fit` stays as the fallback for a survey that never registered anything,
        and it is the only path that can still choose the orientation, so the
        measured one carries it across.
        """
        which = self._("REGISTRATION", "measured")
        if which not in ("measured", "fit"):
            raise SystemExit(
                f"REGISTRATION is {which!r}; it is 'measured' or 'fit'")
        if which == "measured":
            reg = gate.Registration.measured(derived)
            if reg is not None:
                return reg
        return gate.Registration.fit(cloud_mesh, cloud_build,
                                     at=self._("REGISTER_AT", 0.5), plan=drawn)


__all__ = ["Grading", "holds", "solid", "windows"]
