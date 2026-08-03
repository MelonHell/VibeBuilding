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

from . import checks, gate, report, sources
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
        cut = self.divisions(g, model, sched)
        self.watertight(g, cut)
        self.evenness(g, model, read, sched)
        self.witnesses(g, derived)
        sections, reg = self.section(g, derived, read, reference, model, frame,
                                     parts, evidence)

        doc = report.write(paths.REPORT, c.BUILDING, g,
                           sections=sections, findings=findings, frame=frame,
                           registration=reg, schedule=audit,
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
        budget = self._("JAGGED", 0.15)
        floor = self._("JAGGED_FLOOR", 200)
        if budget is not None:
            for name, part in read.named.items():
                if part.mask.count() < floor:
                    continue
                found = checks.jaggedness(part.mask, read.frame)
                g.add(f"{name} is drawn straight", found["share"] <= budget,
                      f"{found['share']:.0%} of it is wobble -- {found['cells']}"
                      f" cells differ from its own straightened reading, which "
                      f"is {found['vertices']} straight segments; budget "
                      f"{budget:.0%}")

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
