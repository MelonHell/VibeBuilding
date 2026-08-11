"""Running a building's gate: the part that is the same on every building.

`blockwright.gate` is the apparatus -- registrations, clouds, sections,
exemptions. This is the *order* they are used in, which six buildings each wrote
out at four hundred lines and which differed between them only in comments and in
one branch that every one of them got slightly differently.

    GATE = grading.Grading(paths, derive, sys.modules[__name__])

    def main() -> int:
        return GATE.main()

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
from pathlib import Path

from . import checks, gate, measure, report, sources, style
from . import model as model3d
from .mask import Mask
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

    # -- the run -----------------------------------------------------------

    def main(self) -> int:
        paths, derive, c = self.paths, self.derive, self.c

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
        cut = self.divisions(g, model, sched)
        self.watertight(g, cut)
        self.evenness(g, model, read, sched)
        self.corners(g, model, read)
        mixes, matrix = self.facades(g, model, read, derived)
        self.elevations(g, derived)
        self.grounds(g, derived, read, sched)
        self.witnesses(g, derived)
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
                           evidence=derived.get("evidence"))

        for line in (reg.lines() if reg is not None else []):
            print(line)
        for section in sections:
            print()
            for line in section.lines():
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

    def corners(self, g, model, read):
        """Whether the parts the map drew square were built square.

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

        Asked only of the parts the *map* drew as rectangles, because only there
        is there an answer to compare against. A part whose drawn mask is
        `SQUARE_SAME` or more of the rectangle fitted to it is one of those, and
        the build should stand about as square there as the map drew.

        Read off a floor plate of the build itself rather than off anything
        declared, so it cannot be answered by naming a part. **Which** plate is
        searched for and not taken from a table: a building is hollow, so a cut
        between two floors returns the wall ring and a ring is a fifth of its
        own rectangle however square it is. `LEVELS` is no help here -- it is
        chosen for counting components, and on one building its lowest entry
        sits between the ground and the first slab. So every level is tried and
        the plate that covers most of the drawn part wins; a part that has no
        such plate is ungraded rather than failed, because the row could not
        find the thing it was going to measure.
        """
        square = self._("SQUARE_SAME", 0.90)
        budget = self._("CORNERS_LOST", 0.05)
        least = self._("CORNERS_COVER", 0.80)
        # How much of the plot round the building may stand at a course before
        # that course is the terrain rather than a floor. A quarter and not a
        # half: clipped to the drawn mask, a cut through the ground comes back
        # as an exact copy of the map, and a row that reads the map against the
        # map cannot fail. That is a worse outcome than an ungraded row.
        clear = self._("CORNERS_CLEAR", 0.25)
        reach = 2.0
        wanted = {name: found for name, found in
                  ((name, checks.rectangular(part.mask, read.frame))
                   for name, part in read.named.items())
                  if found["share"] >= square}
        if not wanted:
            g.ungraded(
                "corners",
                f"the map drew nothing here as a rectangle -- no part reaches "
                f"{square:.0%} of its own fitted box -- so there is no corner "
                "this row can hold the build to.")
            return

        # A plate is a level that is full over the part and empty *outside the
        # building*. Both halves are needed and the second one is easy to get
        # wrong. Coverage alone picks the terrain, which is laid over the whole
        # plot, covers every part perfectly, and is clipped to the drawn mask
        # here -- so it comes back as an exact copy of the map and the row can
        # never fail. Asking instead whether the ground *round the part* is full
        # rejects the terrain and rejects every storey of a part with
        # neighbours, which is most of them.
        #
        # So the ring is round the whole drawn building, once: empty sky at
        # every storey, solid ground at the courses the plot is laid on.
        u0 = min(p.u0 for p in read.named.values())
        u1 = max(p.u1 for p in read.named.values())
        v0 = min(p.v0 for p in read.named.values())
        v1 = max(p.v1 for p in read.named.values())
        mass = getattr(read, "mass", None)
        if mass is None:
            mass = Mask.union([p.mask for p in read.named.values()])
        ring = (read.frame.rect(model.width, model.length,
                                u0 - 8.0, u1 + 8.0, v0 - 8.0, v1 + 8.0)
                - mass.dilate(reach))
        ring_cells = max(1, ring.count())

        # The **lowest** plate that qualifies, not the best one. A part that
        # runs the height of a tower has a plate at every storey and they are
        # not the same shape: the top one is a roof with plant standing on it
        # and a setback under it. Its own first floor is where its plan shape
        # is, and taking the best-scoring level anywhere instead grades whatever
        # happened to be tidiest.
        step = max(1, model.height // 24)
        plates: dict[str, tuple[int, float, float]] = {}
        best: dict[str, tuple[int, float, float]] = {}
        for y in range(1, model.height, step):
            layer = solid(model, y)
            spill = (layer & ring).count() / ring_cells
            for name in wanted:
                mask = read.named[name].mask
                cover = (layer & mask).count() / max(1, mask.count())
                seen = best.get(name)
                if seen is None or cover - spill > seen[1] - seen[2]:
                    best[name] = (y, cover, spill)
                if name not in plates and cover >= least and spill <= clear:
                    plates[name] = (y, cover, spill)

        for name, drawn in wanted.items():
            at, cover, spill = plates.get(name) or best.get(name, (0, 0.0, 1.0))
            if name not in plates:
                g.ungraded(
                    f"{name} is built square",
                    f"no floor plate of this build stands over it clear of the "
                    f"ground: the best cut is {at} m, which covers {cover:.0%} "
                    f"of it with {spill:.0%} of the plot round the building "
                    f"filled as well. Under {least:.0%} covered the row is "
                    "looking at a wall ring rather than a floor, and a course "
                    "with the plot in it is the terrain")
                continue
            # Cut to the part's *own* drawn mask, the way `divisions` counts
            # inside a declared footprint and for the same reason: anything
            # crossing between two parts stands in the same layer, and a shape
            # taken over a box round one of them is that part welded to its
            # neighbour. Clipped this way a rounded corner shows as the thing it
            # is -- cells missing where the map drew material -- and nothing
            # else can leak in.
            part = read.named[name]
            mine = solid(model, at) & part.mask
            built = checks.rectangular(mine, read.frame)
            angles = checks.corners(mine, read.frame)
            g.add(f"{name} is built square",
                  built["share"] >= drawn["share"] - budget,
                  f"the map draws it {drawn['share']:.0%} of its own rectangle "
                  f"and the build stands {built['share']:.0%} of one on its "
                  f"plate at {at} m, on {angles['right']} right angle(s) of "
                  f"{angles['vertices']}; allowed to lose {budget:.0%}. Against "
                  "the plan and not against the reference: this says the "
                  "machinery kept the shape it was given, and says nothing "
                  "about whether the shape is the building's -- a corner the "
                  "map itself drew round passes here. A pad and a closing are "
                  "both discs and both bite a convex corner; `Site.squared` "
                  "draws the rectangle instead")

    def evenness(self, g, model, read, sched):
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
            mesh_frame, datum = read.massing.model_frame, read.massing.datum
        else:
            mesh = Mesh.read(reference.path)
            datum = mesh.ground()
            # The reference's own frame, fitted above `MESH_FLOOR` rather than
            # at the ground: a clip that carries a podium, a terrace or a belt
            # of trees fits its frame to those and comes back longer than the
            # building. That number is the building's own, so it is read from
            # `derive` and not typed here -- a gate with its own floor grades
            # every station in a frame the measurements were never taken in.
            mesh_frame = mesh.frame(datum=datum, floor=derive.MESH_FLOOR)

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
