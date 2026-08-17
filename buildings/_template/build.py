"""<name>: the recipe.

Run `probes/derive.py` first. This script refuses to start without
`out/derived.json`, and that refusal is the point of the split. Every dimension
below either comes out of that file -- where it was measured from the map and the
mesh, with the window it was measured in written down beside it -- or is a choice
this script makes and says it is making. There is no third category, and in
particular there is no number copied out of a README because it looked right
last time.

Three kinds of value appear here, and each is labelled where it is set:

    measured    read from `derived.json`, whichever input it was measured off.
    declared    stated by a person, recorded with the source named -- a sentence
                of the brief, a sheet of a drawing, a photograph. Only for what
                nothing supplied can measure: how many parts there are, how tall
                something photogrammetry rounds off.
    chosen      a decision about how to draw something the references do not fix
                -- how wide to open a recess a map draws freehand, what module
                to glaze a screen in.

    python -m buildings.<name>.probes.derive
    python -m buildings.<name>.build
    python -m buildings.<name>.gate

This is the skeleton, and it builds: a podium, the parts extruded to their
measured heights, a floor at every measured level, a roof and a parapet over
each part, and a rhythm of window bays on every facade. That is a massing study, and it will pass the gate. Everything
after it -- what the parts are made of, what carries what, what stands in the
gaps between them -- is this building's own, and goes in sections of its own
below.

The skeleton does not care where the plan came from. `derive.plan_of()` returns
the same named parts whether they were decomposed off a map, split off the roof
steps of a 3D model, or typed out of a written brief, and every dimension below
is read through it. `docs/pipeline.md` has the patterns worth stealing for a
curved shell, a surface of revolution, a colonnade and a planted court.
"""

from __future__ import annotations

import json

from blockwright import build
from blockwright.build import Canvas, Facade
from blockwright.finish import finish
from blockwright.mask import Mask
from blockwright.schedule import Item, Schedule

from . import paths
from .probes import derive

# -- palette ----------------------------------------------------------------
#
# From the photographs. Every entry must be in `blocks.COLORS` or derive from
# one, or the render comes out magenta -- which is checked and not assumed:
# `checks.palette` is one of the gate's rows.
#
# Match the *value* before the hue. A block a hundred levels too dark reads as a
# different material even when its colour is nominally right, and a photo review
# will call it out; a block of the right lightness with the cast missing reads as
# the material in a different light, which nobody notices.
WALL = "minecraft:white_concrete"
DECK = "minecraft:light_gray_concrete"
TRIM = "minecraft:smooth_quartz"
GLASS = "minecraft:light_blue_stained_glass"
PAVING = "minecraft:smooth_sandstone"

# -- chosen dimensions ------------------------------------------------------
#
# Nothing in the references fixes these. They are drawing decisions, kept
# together so that the measured numbers further down are not diluted by them.

PAD = 0.5               # half a block of tolerance around an edge a map drew
CLOSE = 1e-6            # what a half-open edge owes an inclusive extent
THICK = 1.0             # wall thickness
APRON = 3.0             # how far the podium stands out past the building
COURT = 8.0             # the widest gap between parts that is still one site
FACADE_PITCH = 6.5      # window bay module, as arc length along the wall
FACADE_WIDTH = 4.5      # how much of each bay is glass

# FACADE_PITCH is in this block because nothing used to measure it. Something
# does now: `derive` autocorrelates the vertical edges of every textured
# elevation and reports the pier rhythm with how many of the four agreed.
#
#     bay = site.d.get("facade", {}).get("bay")
#     if bay and bay["agreed"] >= 2:
#         FACADE_PITCH = bay["value"]
#
# Two elevations landing on the same figure is a measurement and belongs in the
# measured half; one elevation is a reading and belongs here, chosen, with the
# reason. Take it or reject it -- but not without looking, which is what every
# building before this one did.

RENDER_SCALE = 6.0

# -- the manifest -----------------------------------------------------------
#
# What this building is supposed to have, written before it is built. Each line
# names the part, says what it is, and names the reference that says it exists --
# and the gate then checks that something actually stands where the build said it
# put it.
#
# The manifest is the first of the three layers this pipeline grades on, and the
# only one written by hand:
#
#   the manifest      this list. What the building should have, and why.
#   the declaration   what the build says it placed, part by part, through
#                     `sched.declare(name, mask, y0, y1)`.
#   the schematic     what actually stands there when the blocks are read back.
#
# A part in the manifest that the build never declares fails. A part declared
# with nothing standing in it fails. `near=` says which other part a thing must
# be within reach of, which is what catches a balcony floating a metre off its
# wall after an edit somewhere else moved the wall.
#
# Every line names the blocks it is made of. Without that a declaration is
# satisfied by anything standing in its mask, and a window bay declared over the
# wall it is cut into is satisfied by the wall -- so "the glazing was never
# built" passes, which is the exact failure this list exists to catch.
SCHEDULE = Schedule([
    Item("podium", "the plinth the whole building stands on, one course proud "
                   "of the ground",
         "the mass sits above its surroundings in the photographs",
         blocks=PAVING),
    Item("shell", "the parts, extruded to their measured heights",
         "the plan for the footprint, the section for the height",
         blocks=WALL),
    Item("floors", "a deck at every measured storey line",
         "the storey rhythm read off the modelled facades",
         near="shell", blocks=DECK),
    Item("glazing", "the window bays in every facade",
         "photographs: <which one, and what it shows>",
         near="shell", blocks=GLASS),
    Item("parapets", "the upstand around each roof",
         "photographs: <which one>",
         near="shell", blocks=TRIM),
    # A part the building has several of, all the same one. `copies` is a count,
    # and counting is what a photograph is good for; the vector they step by is
    # a measurement and comes off `derived.json` through `Site.step`.
    #
    # Build one, stamp the rest, declare both:
    #
    #     unit = site.rect(0.0, 20.0, 0.0, 12.0)
    #     build.walls(canvas, unit, site.ground, top, WALL, THICK)
    #     periods = site.periods(20.6)          # the measured pitch, in periods
    #     site.stamp(canvas, unit, site.ground, top, periods, 7)
    #     sched.declare("villa", site.repeat(unit, periods, 8),
    #                   site.ground, top)
    #     sched.declare_repeat("villa", unit, site.ground, top,
    #                          site.step(periods), 8)
    #
    # The ends of a run are usually not copies -- a gable, a stair core, a corner
    # return -- and those are their own items rather than a copy with an edit.
    # Item("villa", "one section of the row",
    #      "photographs: eight bays, all alike",
    #      near="podium", blocks=WALL, copies=8),
])


class Site:
    """The frame, the plan, and every dimension read off the references.

    Assembled once and passed to each section of the build, so there is one place
    where a measurement turns into a coordinate and no section can quietly
    disagree with another about where a wall is.

    This is also the only place the frame's angle appears. Every mask below is
    cut through `self.region`/`self.rect`/`self.disc`, which take u and v -- along
    the building and across it -- and do the rotation themselves. A section that
    computes its own sine and cosine is a section that will disagree with the
    others by half a degree after the layout is redrawn.
    """

    def __init__(self, derived: dict):
        self.d = derived
        read = derive.plan_of()
        self.mass, self.frame = read.mass, read.frame
        self.width, self.length = self.mass.width, self.mass.length
        self.parts = read.named

        # Read the same way `probes/derive.py` read it, and checked against what
        # it wrote: an input redrawn since the last measurement decomposes into
        # different parts, and building measured heights onto them would put the
        # right numbers in the wrong places.
        names = [p["name"] for p in derived["parts"]]
        if names != read.order:
            raise SystemExit(
                f"the plan now reads as {', '.join(read.order)}; derived.json "
                f"was measured off {', '.join(names)}. Re-run "
                "probes/derive.py.")

        # -- the section --------------------------------------------------
        #
        # A storey rhythm nothing carried is not a measurement, and `derive`
        # says so rather than returning a plausible number. Stop here instead of
        # building floors at whatever the autocorrelation settled on: a wrong
        # storey height is not visible in the section, which grades the skyline,
        # so nothing downstream would catch it.
        if not derived["storeys"].get("found", True):
            raise SystemExit(
                "no storey rhythm was found (correlation "
                f"{derived['storeys']['score']:.2f} over the bands "
                f"{derived['storeys'].get('bands')}).\n"
                "Either the bands are on facades the reference did not model -- "
                "move them in probes/derive.py and say why -- or this building "
                "genuinely has no repeating floor, in which case set "
                "DECLARED_STOREY there, with the source it came from.")

        # Levels are rounded to whole blocks here and nowhere else. A level kept
        # as 5.75 all the way down to the fill turns into a different integer in
        # two different sections and the floor ends up half a metre out of line
        # with the wall that carries it.
        self.levels = [int(y + 0.5) for y in derived["storeys"]["levels"]]
        self.ground = self.levels[0]
        self.roof = self.levels[-1]
        self.storey = int(derived["storeys"]["spacing"] + 0.5)

        # The top of each part, from the skyline read over that part's own
        # footprint, or declared where nothing could read it. Not one number for
        # the whole building: where a wing steps down, a single median splits the
        # difference and is wrong at both ends.
        self.tops = {
            name: int(entry["median"] + 0.5)
            for name, entry in derived["skyline"].items()
            if isinstance(entry, dict) and entry.get("median") is not None
            and name in self.parts
        }
        missing = [name for name in read.order if name not in self.tops]
        if missing:
            raise SystemExit(
                f"nothing states how tall {', '.join(missing)} is -- the "
                "reference has no material over that part and no height was "
                "declared for it. Add one to DECLARED_HEIGHTS in "
                "probes/derive.py, with the photograph or the sheet it was read "
                "off, and re-run it.")
        self.top = max(self.tops.values())

        # Whether a measured edge needs opening out by half a block, which
        # depends entirely on what measured it. A map draws the wall as a line
        # and `plan.decompose` subtracts that line, so a part read off a map
        # stops half a block inside the real face and the pad puts it back. A
        # part read off a model or a capture is already the outer face, and
        # padding it again builds the building half a metre oversize on every
        # side. That is invisible in the build and loud at the gate: an absolute
        # pad inflates the narrow axis proportionally more than the long one, so
        # the registration comes back with its two scales disagreeing.
        self.pad = PAD if derived["plan"]["by"] == "map" else 0.0

        # The rasterisation staircase: at this frame's angle, a band narrower
        # than this comes out as cells joined only at their corners, which the
        # connectivity check reads as loose blocks rather than as a sheet.
        # Measured, never typed. Check any chosen width against it before
        # drawing, and fail loudly rather than building fifty loose blocks.
        self.staircase = derived["frame"]["staircase"]

    # -- drawing helpers ---------------------------------------------------

    def region(self, predicate) -> Mask:
        return self.frame.region(self.width, self.length, predicate)

    def rect(self, u0: float, u1: float, v0: float, v1: float) -> Mask:
        return self.frame.rect(self.width, self.length, u0, u1, v0, v1)

    def disc(self, cu: float, cv: float, radius: float,
             inner: float = 0.0) -> Mask:
        return self.frame.disc(self.width, self.length, cu, cv, radius, inner)

    def empty(self) -> Mask:
        return Mask(self.width, self.length)

    def at(self, u: float, v: float) -> tuple[int, int]:
        x, z = self.frame.to_world(u, v)
        return int(x), int(z)

    def stations(self, u0: float, u1: float, pitch: float) -> list[float]:
        """Evenly spaced positions along u, both ends inset by half a pitch."""
        count = max(1, int((u1 - u0) / pitch))
        step = (u1 - u0) / count
        return [u0 + (i + 0.5) * step for i in range(count)]

    def footprint(self, name: str) -> Mask:
        """One part's footprint, opened by `self.pad` all round.

        On a map-drawn plan the pad is what keeps a wall from landing a cell
        inside its own drawn edge, which at an angle makes every second course
        of a diagonal wall one block short. On a plan measured off geometry it
        is zero -- see `self.pad`.

        The rectangle is redrawn from the part's extent rather than taken from
        its mask, and that is deliberate: a traced mask carries the dither of
        whatever drew it, and a wing built as a polygon of its own measured
        edges is straight where a wing built from its tracing is not.

        `CLOSE` is what that redrawing owes back, and it is a nudge rather than
        a pad. `Part.u0..u1` is the extent of the part's own cell *centres* and
        includes both ends, while `Frame.rect` tests `u0 <= u < u1`, so a cell
        sitting exactly on the far edge is outside the rectangle that was drawn
        from it. On a frame square to the world that is every cell of the last
        column and the last row at once -- a connected L of 93 cells on the
        fixture, against a `PLAN_BLOB` budget of twelve. On a rotated frame it
        is two or three cells, because no two of them share a u exactly.
        Opening the far end by a micrometre includes them and adds no size;
        opening it by half a cell would add a metre and a half to a wing
        fourteen wide, which the registration reads as the two axes scaling
        differently. The defect went unseen for as long as it did because `PAD`
        covers it, and `PAD` is only laid on a map-drawn plan.
        """
        p = self.parts[name]
        if p.kind == "disc":
            # A radius read off the rim is already a distance to the outer face
            # of the last cell, not to its centre, so it owes nothing back.
            return self.disc(p.centre[0], p.centre[1], p.radius + self.pad)
        out = self.pad
        return self.rect(p.u0 - out, p.u1 + out + CLOSE,
                         p.v0 - out, p.v1 + out + CLOSE)

    def terraces(self, name: str) -> list[tuple[int, Mask]]:
        """(height, where) for a part whose roof is not one level, tallest first.

        Empty for a flat roof, which is most parts: there the one number in
        `self.tops` is the whole answer and the footprint is the whole shape.
        A part that comes back with entries here is one the reference measured
        as stepping, and building it to `self.tops[name]` puts the wrong height
        over everything but the largest step -- quietly, because the section
        grades against the same median.
        """
        entry = self.d.get("roof", {}).get(name, {})
        return [(int(t["height"] + 0.5), Mask.loads(t["mask"]))
                for t in entry.get("terraces", []) if "mask" in t]


def ground(canvas: Canvas, site: Site, sched: Schedule) -> None:
    """The plinth, and whatever the parcel around the building is.

    One course at the ground line, not below it. A schematic has no negative y,
    so a plinth drawn under the datum is written into nothing at all and the
    build comes back with its parts standing apart, connected by a floor that
    was never placed. Laying it at the datum keeps every height in the build
    equal to metres above the reference's ground, which is what the section
    compares, and a course under the whole footprint is a ground-floor slab
    rather than a lost one.
    """
    # Closed before it is grown: a building whose parts stand apart across a
    # court is one building on one site, and a pad that follows each part
    # separately leaves the wings connected by nothing. The gate then reports
    # half the building as blocks adrift from the main mass, which is true of
    # the schematic and false about the building. Closing by `COURT` bridges any
    # gap up to twice that and leaves every real opening alone.
    pad = site.mass.dilate(COURT).erode(COURT).dilate(APRON)
    build.solid(canvas, pad, site.ground, site.ground + 1, PAVING)
    sched.declare("podium", pad, site.ground, site.ground + 1)

    # Where the building came off a map, the parcel -- the ground inside the road
    # loop that belongs to it -- is read by flooding outward from the building
    # until a drawn road stops it, not by taking a box around the footprint:
    #
    #     from blockwright import flatmap
    #     lot = flatmap.parcel(paths.LAYOUT)
    #
    # It is worth doing wherever the building has grounds, and it is what puts
    # the verge, the planting and the street trees where the map actually draws
    # them rather than where a rectangle would. A building with no map has no
    # drawn boundary to read, so its grounds are a chosen dimension like any
    # other -- `site.mass.dilate(APRON)` above is the whole of it.
    #
    # `parcel` needs the road loop to close. Four buildings found it returning
    # either the footprint or the whole tile, and in every case the loop was
    # open: the map's road ran off the edge, or the building sat on a corner
    # with water on two sides. When it does that, the grounds come off the
    # capture instead -- `flatmap.surrounds(paths.LAYOUT, site.mass)` reads what
    # the map draws within reach of the building without needing a boundary --
    # and the fallback is recorded as chosen rather than measured.


    # -- the gap between the wings, which is usually not a gap ---------------
    #
    # A plan decomposed off a map draws two wings and the air between them, and
    # the obvious reading is a courtyard: pave it, plant it, done. On four
    # buildings out of six that reading was wrong, and the reference said so
    # the whole time -- the roof grid holds material three to eight metres up
    # over the whole gap. A pool deck, a lobby roof, a porte-cochere, a garage
    # podium.
    #
    # The question is asked in `probes/derive.py`, where the reference is open,
    # by naming the gap as a part of the plan and letting `roof` measure it like
    # any other. A deck of 4.2 m over 900 cells is a building, not a court.
    # Built as open ground it costs twice: the section grades those stations
    # against material that is not there, and the render shows daylight through
    # the middle of a building that has none.


def shell(canvas: Canvas, site: Site, sched: Schedule) -> None:
    """Each drawn part, extruded to its own measured height.

    Hollow, not solid. A filled volume is a massing study: it passes the section,
    reads as a block of stone in every render, and cannot be walked into.
    """
    # Each part is declared with the mask the blocks actually went into, not
    # with the whole footprint. A declaration wider than what it describes is
    # satisfied by its neighbours: floors declared over the footprint are held
    # up by the wall ring standing in the same cells, and the day the slabs stop
    # being placed nothing says so.
    for name in site.tops:
        foot = site.footprint(name)
        top = site.tops[name]
        floors = [y for y in site.levels if site.ground < y < top]
        ring = foot.outline(THICK)
        deck = foot.erode(THICK)

        build.walls(canvas, foot, site.ground, top, WALL, THICK)
        sched.declare("shell", ring, site.ground, top)

        for y in floors:
            build.slab(canvas, foot, y, DECK, inset=THICK)
            sched.declare("floors", deck, y, y + 1)

        # The roof is a floor like any other, and this skeleton did without one
        # for a long time: a ring of wall with storeys inside it and open sky
        # over the middle. Almost nothing here says so. The section reads the
        # tallest thing over each station and the parapet ring stands a metre
        # above the roof line, so a hole in the middle of a part is answered by
        # its own edge; the first render is what shows it.
        build.slab(canvas, foot, top - 1, DECK, inset=THICK)
        sched.declare("floors", deck, top - 1, top)

        # The parapet: a course above the roof line, which is what stops a roof
        # reading as an unfinished floor.
        build.walls(canvas, foot, top, top + 1, TRIM, THICK)
        sched.declare("parapets", ring, top, top + 1)

        # Window bays, spaced by arc length along the wall and not by u. Spacing
        # by u puts the bays closer together on any wall that is not square to
        # the frame, which on a rotated building is every wall that turns a
        # corner, and the rhythm visibly tightens at the corners.
        face = Facade(foot)
        build.windows(canvas, foot, face, site.ground + 1, top - 1,
                      period=FACADE_PITCH, width=FACADE_WIDTH,
                      thickness=THICK, block=GLASS)
        sched.declare("glazing",
                      build.openings(foot, face, FACADE_PITCH, FACADE_WIDTH,
                                     THICK),
                      site.ground + 1, top - 1)

    # -- when one height does not describe a part ----------------------------
    #
    # `site.tops[name]` is one number, and the loop above extrudes to it. That
    # is right for a flat deck and wrong for a roof that steps, ridges or
    # carries a plant room -- and the wrongness is quiet, because the section
    # grades the same median the build was made from. `derive` prints the
    # spread over every part and says so out loud when the deck and the maximum
    # are more than a storey apart. Where it does, the steps come measured, with
    # their shapes, and replace the single extrusion for that part:
    #
    #     for top, where in site.terraces("slab"):
    #         build.walls(canvas, where, site.ground, top, WALL, THICK)
    #         build.slab(canvas, where, top - 1, DECK, inset=THICK)
    #
    # `roof.plant` for what stands on the deck and `roof.plateau` for a part too
    # narrow to read from above (a drum, a turret: read across it, where its
    # shell separates from whatever crosses over) are measured the same way, in
    # `probes/derive.py`, where the reference is open. What none of them do is
    # follow the surface cell by cell -- every answer is a level a wall can be
    # built to, and a build that voxelises a capture is a pile of rubble.
    #
    # -- a balcony is a recess, not a shelf ---------------------------------
    #
    # Balconies drawn as slabs sticking out of the wall plane fail the section
    # at every station they touch: the reference reads them as part of the
    # facade, the build puts them a metre and a half proud of it. Cut them into
    # the wall instead -- `build.recess(canvas, foot, face, ...)` -- and the
    # facade keeps its measured line while the rhythm reads from every camera
    # the review uses. The exception is a balcony the capture itself holds as
    # an overhang, which is a measured fact and is built where it was measured.

    # -- this building's own sections go here -------------------------------
    #
    # One function per part, each taking `(canvas, site, sched)` and declaring
    # what it placed. Keeping them separate is what lets a review finding be
    # answered by rewriting one of them.
    #
    # Before drawing anything narrow, check it against `site.staircase` and stop
    # rather than draw it:
    #
    #     if width < site.staircase:
    #         raise SystemExit(
    #             f"a member {width:.1f} m wide is under this frame's "
    #             f"{site.staircase:.2f} m staircase; it would rasterise into "
    #             "cells joined only at their corners")


def main() -> None:
    if not paths.DERIVED.exists():
        raise SystemExit(
            f"{paths.DERIVED} is missing. Every dimension in this build is "
            "measured rather than typed, so there is nothing to build from "
            "until the references have been read:\n"
            "    python -m buildings.<name>.probes.derive")

    site = Site(json.loads(paths.DERIVED.read_text(encoding="utf-8")))
    canvas = Canvas(site.width, site.top + 4, site.length)

    ground(canvas, site, SCHEDULE)
    shell(canvas, site, SCHEDULE)

    print(site.frame)
    print(f"  levels  {', '.join(str(y) for y in site.levels)}, "
          f"storey {site.storey} m")
    for name, top in site.tops.items():
        p = site.parts[name]
        print(f"  {name:12s} u {p.u0:6.1f}..{p.u1:6.1f}  "
              f"v {p.v0:5.1f}..{p.v1:5.1f}  to {top} m")

    done = finish(canvas, paths.OUT, site.frame,
                  schedule=SCHEDULE, orthos=paths.ORTHOS, scale=RENDER_SCALE,
                  plans=(("ground", site.ground),
                         ("upper", site.levels[len(site.levels) // 2])))
    for line in done.lines():
        print(line)


if __name__ == "__main__":
    main()
