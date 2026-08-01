"""What counts as right for this building.

    python -m buildings.<name>.gate

The grading is `blockwright.gate`'s. What belongs here is the part only this
building can say: which stretches of it to cut a section through, what the
reference is not a witness for, how many parts a plan cut through a storey should
show, and how much clutter a finished build of this size is allowed.

The section is the one check in this pipeline that can come back wrong. Every
other row grades the build against something it was drawn from, which is a
tautology -- the build is the plan, extruded, so of course they agree. The
reference is read only for numbers, never for geometry, so a section cut through
both is a comparison against something the build was never given.

That argument holds exactly as far as the evidence does, and the gate says how
far that is. Where the plan came from a map and the section is cut against a
capture, the two are independent and the section is a real outside check. Where
the same model supplied both, the section grades the simplification and cannot
catch the model being wrong. Where there is no model and no capture at all, there
is nothing to cut a section against, and the run is reported `ungraded` -- not
`pass`, which would be this file claiming a building was checked because nothing
contradicted it.

Everything below the constants is generic and usually needs no editing. The
constants are the whole of the building-specific part, and every one of them
should be set by lowering it onto a build that already passed rather than typed
in advance: a budget with an order of magnitude of slack has stopped being a
check and become a comment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from blockwright import checks, gate, report, sources
from blockwright import model as model3d
from blockwright.mask import Mask
from blockwright.mesh import Mesh
from blockwright.schedule import Schedule
from blockwright.schem import AIR, Schematic

from . import paths
from .probes import derive

BUILDING = "<name>"

# How far a station of the build may stand from the mesh's reading of the same
# station before the section fails it. Two metres is half a storey: tight enough
# that a floor gained or lost fails, loose enough that photogrammetry's rounding
# of a parapet does not.
TOLERANCE = 2.0

# The library registers on material above half the build's height. Raise it when
# the clip carries tall trees or a neighbour that photogrammetry cannot tell from
# building: fitting an extent against a belt of trees stretches that axis, and
# what is then graded is the landscaping. `derive` reports the disagreement
# between the two axes, which is how this number gets chosen.
REGISTER_AT = 0.5

# How far the build may be from the reference in overall size. Widen it only for
# a building whose reference is knowingly a different one -- another block of the
# same design, an earlier phase -- and write the reason beside the number.
SCALE_TRUE = gate.SCALE_TRUE

# Planting and water stand on the building rather than being it. Left in, a palm
# sets the skyline and the section grades a frond against a parapet.
SOFT = {
    "minecraft:moss_block",
    "minecraft:jungle_leaves",
    "minecraft:jungle_log",
    "minecraft:short_grass",
    "minecraft:grass_block",
    "minecraft:water",
}

# Watermarks. Nothing may hang unsupported, ever. The other two are what this
# building's fixtures actually cost -- planter feet standing clear on a podium,
# rail posts and truss webs ending in air -- and are set from a run that passed.
FLOATING_BLOCKS = 0
GROUNDED_STRAYS = 200
FREE_ENDS = 1200

# What a plan cut through a storey should show, part by part: the name the build
# declared in its schedule, and how many separate components a cut through it
# ought to find. Counted rather than looked at, so a floor that lost its division
# between houses fails as a number instead of being noticed later in a render.
#
# Counted inside each part's own declared footprint and nowhere else. Everything
# that crosses between parts -- galleries, bridges, canopy piers -- stands at some
# height in the same layer, and a count taken over the whole layer either grades
# a pier as a house or merges two houses through a bridge and grades the pair as
# one.
#
# At several heights and not at one, because a division can survive at 3 m and be
# gone at 17 m. Choose heights that fall between floor lines, not on them: a cut
# taken exactly at a deck reads the deck.
COUNTS: dict[str, int] = {
    # "villas": 13,
}
LEVELS: tuple[int, ...] = (3, 8, 12, 17, 20)

# Parts that should read as one closed ring at one height -- a drum, a shell, a
# tower shaft. The height is chosen above whatever stands under the part and
# below whatever caps it.
RINGS: dict[str, int] = {
    # "cone": 20,
}

# Whether an empty `COUNTS` is a decision or an omission. Left False, the gate
# prints an ungraded row saying nobody has said how this building divides --
# because an empty table and a satisfied one produce exactly the same output
# otherwise, and "no rows failed" then means "no rows were asked".
#
# Set it True for a building that genuinely has no repeated division: one shed,
# one tower, one hall. Then the row says so, by name, and the reader can
# disagree with a decision instead of guessing at a silence.
UNDIVIDED = False

# A part smaller than this is a fixture, not a floor plate: a column, a rail
# post, the corner of a planter clipped by the layer.
MIN_PART = 8

# Where to test that a wall ring actually holds what it encloses, and for which
# parts. A band of footprint-minus-erosion leaks at every diagonal, and on a
# rotated frame every wall in the building is a diagonal.
WATERTIGHT_AT = 8
WATERTIGHT: tuple[str, ...] = tuple(COUNTS)


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

    Each window is offered only the exemptions it can actually use. An exemption
    offered to a window that cannot use it excuses nothing and reports itself as
    stale geometry, which trains the reader to skim past the one row that
    matters. An exemption withheld is also the safer error -- if a window ever
    does need one, it fails and gets looked at.

    Exemptions available from `blockwright.gate`:

      `taller_than_the_mesh(part, note)` -- this part stands above what the mesh
      records, and the note says why the capture is wrong rather than the build.
      Photogrammetry rounding off a tapering tip is the usual reason.

      `next_lot()` -- material in the clip past the end of this building's plot,
      which is a neighbour and not this building.

    Returns `(name, u0, u1, exemptions)` tuples in u order.

    The default splits the building wherever its parts stop overlapping along u.
    Two parallel wings share one window, because a cut through them is one cut
    through both; a wing and a tower beyond its end get one each. That is the
    right shape of answer far more often than a single window for the whole
    length: a section through a uniform extrusion is the same section wherever
    it is cut, so one wide window grades the middle repeatedly and the ends not
    at all -- and the ends are where things go wrong.

    Split further where a single part steps, and merge by hand where two
    stretches really are one continuous thing.
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


def main() -> int:
    if not paths.DERIVED.exists():
        raise SystemExit(
            f"{paths.DERIVED} is missing; run probes/derive.py first")

    derived = json.loads(paths.DERIVED.read_text(encoding="utf-8"))
    evidence = sources.survey(paths)
    read = derive.plan_of()
    reference = evidence.reference
    g = gate.Gate(BUILDING)

    # Freshness first, and fatally. A stale schematic registers perfectly -- it
    # was a real build of the same building -- so every row after it goes on
    # printing a grade that was true yesterday and is being reported today.
    #
    # The inputs it is checked against are the ones the geometry came from, not
    # everything in `input/`: a photograph added this morning does not make
    # yesterday's massing stale, and a gate that says it does teaches people to
    # rebuild without reading why.
    # `build.py` is in the list, and that is not pedantry: a recipe edited and
    # not re-run is the commonest way a gate comes to grade last week's build,
    # and it is exactly the case a mtime check is for.
    g.fresh(made=[paths.SCHEM, paths.SCHEDULE],
            sources=[read.source.path, paths.DERIVED,
                     Path(__file__).with_name("build.py")]
                    + ([reference.path] if reference else []))
    if not g.ok:
        for line in g.lines():
            print(line)
        return 1

    model = Schematic.read(paths.SCHEM)
    frame, parts = read.frame, read.parts
    sched = Schedule.load(paths.SCHEDULE)

    # -- did the build place what the manifest asked for --------------------
    audit = list(sched.audit(model))
    g.extend(audit)

    # -- is what it placed sound --------------------------------------------
    findings = checks.inspect(model, layers=list(LEVELS), soft=SOFT)
    g.add("palette", not findings.unknown,
          "every block has a colour" if not findings.unknown
          else "unknown: " + ", ".join(sorted(findings.unknown)))
    g.budget("floating", sum(p.count for p in findings.adrift),
             FLOATING_BLOCKS, "blocks with nothing under them")
    g.budget("strays", sum(p.count for p in findings.strays),
             GROUNDED_STRAYS, "blocks adrift from the main mass")
    g.budget("free ends", len(findings.ends), FREE_ENDS,
             "blocks with one neighbour or none")

    # -- does it still divide the way it is supposed to ----------------------
    #
    # An empty table is a row and not a silence. `COUNTS` unfilled and `COUNTS`
    # satisfied print the same thing otherwise -- nothing -- and then "no rows
    # failed" quietly comes to mean "no rows were asked", which is the exact
    # confusion the three-state verdict exists to prevent.
    if not COUNTS and not UNDIVIDED:
        g.ungraded(
            "division",
            "COUNTS is empty, so nothing checks that this building still reads "
            "as separate parts. A row of houses and one long block cast the "
            "same silhouette and pass every other row here. Fill it in, or set "
            "UNDIVIDED = True to say this building genuinely has no repeated "
            "division.")

    cut: dict[str, dict[int, list[Mask]]] = {}
    for name, expected in COUNTS.items():
        footprint = sched.built[name].mask
        cut[name] = {y: (solid(model, y) & footprint).components(min_cells=MIN_PART)
                     for y in LEVELS}
        for y, found in cut[name].items():
            g.add(f"{name} at {y} m", len(found) == expected,
                  f"{len(found)} parts, expected {expected}")

    for name, y in RINGS.items():
        found = (solid(model, y) & sched.built[name].mask).components(
            min_cells=MIN_PART)
        g.add(f"{name} at {y} m", len(found) == 1,
              f"{len(found)} parts, expected one closed ring" if len(found) != 1
              else f"one ring of {found[0].count()} blocks")

    if WATERTIGHT and WATERTIGHT_AT not in LEVELS:
        raise SystemExit(
            f"WATERTIGHT_AT is {WATERTIGHT_AT} m and LEVELS are {LEVELS}: the "
            "watertightness test reads the cut this gate already took, and "
            "there is no cut at that height. Add it to LEVELS or move it onto "
            "one.")

    for name in WATERTIGHT:
        rings = cut[name][WATERTIGHT_AT]
        leaking = [r for r in rings if holds(r) == 0]
        g.add(f"{name} watertight", not leaking,
              f"all {len(rings)} hold what they enclose" if not leaking
              else f"{len(leaking)} of {len(rings)} at {WATERTIGHT_AT} m are "
                   "open to the outside")

    # -- do the references agree with each other -----------------------------
    #
    # Rows that can fail for a reason no section can produce. A section compares
    # the build against one reference; these compare two references against each
    # other, and catch what is wrong with the reference itself: a rescaled crop,
    # a capture of the building next door, a model of a later revision, a
    # drawing read at the wrong scale.
    #
    # The numbers are `derive`'s -- measured once, written to derived.json, read
    # here. The gate grades; it does not re-measure.
    for one in derived.get("witnesses", []):
        name = f"{one['question']} agree"
        if one["ok"] is None:
            g.ungraded(name, one.get("expected")
                       or "only one source can answer this")
            continue
        worst = one["worst"]
        where = max(one["rows"], key=lambda k: abs(one["rows"][k][0]
                                                   - one["rows"][k][1]),
                    default="")
        g.add(name, one["ok"],
              f"{one['pair']}: worst {worst:.2f} {one['unit']}"
              + (f" at {where}" if where else "")
              + f", tolerance {one['tolerance']:.2f} {one['unit']}")

    # -- where it stands, against the reference ------------------------------
    sections = []
    reg = None
    if reference is None:
        # No model and no capture: nothing holds a height at a station, so there
        # is no section to cut. Said out loud, as a row, because a check that is
        # skipped in silence reads exactly like a check that passed.
        g.ungraded(
            "section",
            "no model and no capture, so nothing states where material actually "
            "stands. Every height in this build is declared, and nothing here "
            "can agree or disagree with it.")
    else:
        if read.massing is not None and reference.name == read.source.name:
            # One fit, not two: the plan came out of this same file, so the
            # frame it was measured in addresses the mesh exactly.
            mesh = model3d.load(reference.path, up=derive.MODEL_UP,
                                scale=derive.MODEL_SCALE)
            mesh_frame, datum = read.massing.model_frame, read.massing.datum
        else:
            mesh = Mesh.read(reference.path)
            datum = mesh.ground()
            mesh_frame = mesh.frame(datum=datum, floor=6.0)

        cloud_mesh = gate.Cloud.from_mesh(mesh, mesh_frame, datum=datum)
        cloud_build = gate.Cloud.from_model(model, frame, skip=SOFT)
        # The plan goes in with the clouds: an extent is the same end for end,
        # so without it the fit cannot tell which way round the two frames
        # stand. Where they differ -- a game map on an invented street grid
        # against a capture of the real prototype -- everything registers
        # perfectly and every station is graded against the opposite end of the
        # building.
        drawn = gate.Plan.from_parts(parts, frame)
        reg = gate.Registration.fit(cloud_mesh, cloud_build, at=REGISTER_AT,
                                    plan=drawn)
        g.add("registration", reg.agrees(), reg.detail)

        # Two different questions, and only the first used to be asked. The row
        # above asks whether the two axes tell the same story; this one asks
        # whether that story is 1:1. A build a quarter smaller than the
        # reference on both axes registers perfectly and sections perfectly,
        # because the section compares heights through a normalised u and v --
        # and it is a quarter smaller.
        g.add("scale", reg.scaled(SCALE_TRUE),
              f"the build is {1 / reg.u_scale:.2f}x by {1 / reg.v_scale:.2f}x of "
              f"the {reference.name}, tolerance {SCALE_TRUE:.0%}"
              + ("" if reg.scaled(SCALE_TRUE) else
                 ". A rescaled map crop and a reference of a different building "
                 "both look exactly like this; if it is the second, say so here "
                 "by widening SCALE_TRUE with the reason."))

        if reg.agrees():
            cuts = windows(parts, frame, derived, read.named)
            seams = tuple(u1 for _, _, u1, _ in cuts[:-1])
            sections = [
                gate.section(name, u0, u1, mesh=cloud_mesh, build=cloud_build,
                             plan=drawn, registration=reg, tolerance=TOLERANCE,
                             exemptions=allowed, seams=seams)
                for name, u0, u1, allowed in cuts
            ]
            for section in sections:
                g.take(section)
        # A section cut against a registration that does not agree with itself
        # is noise dressed up as a grade, so none is cut and the row above is
        # the failure. Everything already measured is still reported.

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
                   if others else
                   "A second source -- a map, a survey, photographs -- is what "
                   "would make it an outside check."))

    doc = report.write(paths.REPORT, BUILDING, g,
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


if __name__ == "__main__":
    sys.exit(main())
