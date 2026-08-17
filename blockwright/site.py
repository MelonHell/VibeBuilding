"""The frame, the plan, and the dimensions every build reads the same way.

Six buildings each opened `derived.json`, checked it against the plan, rounded
the levels, read the tops, worked out the pad and the staircase, and wrote the
same eight drawing helpers. The copies are what this module is: the part that was
never this building's own.

    class Site(site.Site):
        def __init__(self, derived):
            super().__init__(derived, derive.plan_of(), pad=PAD)
            self.storeys()
            self.heights()
            ...whatever this building measures that no other one does...

Nothing here decides anything. `storeys()` and `heights()` are the standard
readings of the standard blocks of `derived.json`, and a building whose section
was measured some other way -- off a texture, off a declaration, out of a probe
of its own -- simply does not call them and sets `levels`, `ground`, `storey`,
`tops` and `top` itself. Those five names are the contract with the rest of the
build; how they were arrived at is the building's business.

The one thing this class insists on is that the plan being built is the plan that
was measured. That check used to be copied by hand, and a copy that goes stale
is worse than no check at all.
"""

from __future__ import annotations

import math

from . import lattice
from .mask import Mask


class Site:
    """What a build knows before it draws anything.

    Assembled once and passed to each section of the build, so there is one place
    where a measurement turns into a coordinate and no section can quietly
    disagree with another about where a wall is.

    This is also the only place the frame's angle appears. Every mask is cut
    through `region`/`rect`/`disc` or comes straight off the plan, and all of
    them carry the rotation themselves. A section that computes its own sine and
    cosine is a section that will disagree with the others by half a degree after
    the layout is redrawn.
    """

    # How far a traced footprint is closed before it is used. A wall at this
    # frame's angle rasterises as a staircase, and the one-cell notches in it
    # each become a hole in the wall ring built from it. Half a block takes them
    # out and leaves every real opening alone.
    #
    # **It rounds every convex corner, and that is not a side effect worth
    # discovering in a render.** A closing is a dilation followed by an erosion,
    # both Euclidean, and a Euclidean dilation is a disc: it puts material into
    # the quarter-disc outside a corner and the erosion cannot take it back,
    # because there is nothing outside the corner to erode against. The pad does
    # the same thing again. So a part the map drew square comes out of
    # `footprint` with about `pad + CLOSE` metres taken off each of its corners,
    # before anything anybody wrote is involved -- and no row of the gate looks
    # at a corner. `squared` is the answer where the part really is a rectangle.
    #
    # Set it to 0.0 in a building whose parts are boxes and discs: nothing was
    # traced there, so there are no notches, and closing only rounds the corners
    # off shapes the map drew square.
    #
    # In metres, and by rights it should be in cells -- `slack`. What it closes
    # is the notch a rasterised edge leaves, and that notch is `staircase`
    # wide, so half a metre is half a notch on a building square to the world
    # and a third of one at fifty-seven degrees. It stays in metres because
    # every footprint in the corpus was drawn with this number and changing the
    # unit moves all seven of them; `docs/backlog.md` carries it.
    CLOSE = 0.5

    # Whether a part the decomposition called a disc is drawn as one. True is
    # right when the circle fit was a measurement; a building whose "discs" are
    # a wing turning a right angle and the space between two others sets it
    # False and takes the drawn masks instead.
    ROUND = True

    # How far a drawn outline may be moved to take the drawing's wobble out of
    # it, in metres. None keeps the traced edge exactly as the map drew it.
    #
    # The third idiom between `footprint` and `box`, and the one most parts
    # want. `footprint` keeps every wobble, which matters where the plan's shape
    # carries a measurement and is noise everywhere else; `box` replaces the
    # part with its extent and squares off the raked end the mapper drew on
    # purpose. This keeps the corners and drops the rest -- see
    # `Mask.straighten`.
    #
    # **On by default, at a metre.** That is the scale of a hand-drawn edge's
    # wander and half the scale of anything deliberate, so a rake, a bow and a
    # curve all survive it while a sawtooth does not. It defaulted to None for
    # the first nine buildings and not one of them turned it on, which is the
    # same way `flatmap.parcel` went: a facility nobody reaches for is a
    # facility that does not exist. A straightening costs at most a metre
    # against a reference the section grades at two, so the trade is free where
    # it is graded at all, and the thing it buys -- an edge that reads as one
    # straight line instead of a stack of little ones -- is the first thing
    # anybody notices beside a photograph.
    #
    # Set it to None on a part whose own wobble is a measurement: a shoreline,
    # a creek edge, a lawn that really does wander. `checks.jaggedness` is the
    # number that says what it moved, and the gate asks every run.
    STRAIGHT: float | None = 1.0

    def __init__(self, derived: dict, read, pad: float = 0.5):
        self.d = derived
        self.read = read
        # `site` and not `mass`: the plan is assembled, so it holds whatever the
        # reference had and the drawn source did not. `mass` is only what the
        # drawn source painted, and building off it would put those parts up
        # with no ground under them.
        self.mass, self.frame = read.site, read.frame
        self.width, self.length = self.mass.width, self.mass.length
        self.parts = read.named

        # The plan being built has to be the plan that was measured. Everything
        # below indexes `derived.json` by part name, so a layout redrawn since
        # the last measurement does not fail here -- it silently builds one
        # building's heights onto another building's shapes.
        names = [p["name"] for p in derived["parts"]]
        if names != read.order:
            raise SystemExit(
                f"the plan now reads as {', '.join(read.order)}; "
                f"derived.json was measured off {', '.join(names)}. "
                "Re-run probes/derive.py.")

        # Half a block of tolerance, and only for a plan that came off a map.
        # The map draws a wall as a line and `plan.decompose` subtracts that
        # line to find the parts, so a part read off a map stops half a block
        # inside the real face and the pad puts it back. A part measured off a
        # model or off a capture already *is* the outer face, and padding it
        # again builds the whole thing half a metre larger on every side.
        #
        # That is invisible in the build and audible at the gate: an absolute
        # pad inflates the narrow axis by a larger fraction than the long one,
        # so the registration comes back with u and v scaled differently.
        self.pad = pad if derived["plan"]["by"] == "map" else 0.0

        # The narrowest continuous member this frame can rasterise: |cos| +
        # |sin| of its angle. Anything thinner comes out as cells joined only at
        # their corners. Measured rather than typed -- see
        # `narrower_than_the_staircase`.
        self.staircase = derived["frame"]["staircase"]

        # The drawn building's own extent in its own frame. Four numbers every
        # building was computing for itself with the same one-line comprehension,
        # and one of them -- `grid` -- was already reading them off `self`
        # without anything here ever setting them.
        self.u0 = min(p.u0 for p in self.parts.values())
        self.u1 = max(p.u1 for p in self.parts.values())
        self.v0 = min(p.v0 for p in self.parts.values())
        self.v1 = max(p.v1 for p in self.parts.values())

        # Filled by `across` on first use: how far the drawn mass reaches across
        # the plot at each station along it.
        self._reach: dict[int, tuple[float, float]] | None = None

        # The lattice slope the frame was put onto, or None for a building that
        # declared UNLATTICED. Everything about repeating lives off this: see
        # `period`, `pitch` and `stamp`.
        found = derived.get("frame", {}).get("slope")
        self.slope = lattice.Slope.from_json(found) if found else None
        self.unlatticed = derived.get("frame", {}).get("unlatticed")

    # -- repeating ---------------------------------------------------------

    @property
    def period(self) -> float:
        """Metres from one cell of the wall's motif to the same cell of the next.

        The unit every repeat is counted in. A section is copied at a whole
        number of these and at nothing else: at any other pitch the copy is a
        re-rasterisation of the same shape rather than the same cells, which is
        the difference between a terrace somebody built with copy-paste and a
        terrace that was drawn eight times.
        """
        self.on_a_lattice("asking what the wall's period is")
        return self.slope.period

    def periods(self, metres: float) -> int:
        """`metres` as a whole number of periods, at least one."""
        self.on_a_lattice("turning a pitch in metres into a count of periods")
        return self.slope.multiple(metres)

    def pitch(self, metres: float) -> float:
        """`metres` rounded to a pitch this frame can actually copy at.

        Print it beside what was asked for. A measured 20.6 m becoming a built
        20.0 m is a decision, and a decision that only shows up in the geometry
        is one nobody can argue with.
        """
        self.on_a_lattice("rounding a pitch to something that can be copied")
        return self.slope.pitch(metres)

    def step(self, periods: int = 1, along: str = "u") -> tuple[int, int]:
        """The world (dx, dz) that is `periods` periods along or across.

        Both axes of a lattice frame are integer vectors -- a quarter turn maps
        the cell lattice onto itself -- so a wing copied across a court is as
        exact as a section copied along a terrace.
        """
        self.on_a_lattice("asking for the vector a copy steps by")
        if along == "u":
            return self.slope.times(int(periods))
        if along == "v":
            return self.slope.across(int(periods))
        raise ValueError(f"a copy steps along u or v, not {along!r}")

    def repeat(self, unit: Mask, periods: int, count: int,
               along: str = "u") -> Mask:
        """`unit` and `count - 1` copies of it, every `periods` periods.

        The mask side of a repeat: a run of window openings, a row of piers, a
        line of planters. Each one is the same figure translated by whole cells,
        so every one of them rasterises the same -- which a rhythm laid out by
        `Facade.bays` cannot promise, because arc length lands each opening in
        its own sub-cell phase and the raster of one is not the raster of the
        next.
        """
        if count < 1:
            raise ValueError("a repeat lays at least the first one")
        return unit.stamped(self.step(periods, along), count - 1)

    def openings(self, u0: float, u1: float, v0: float, v1: float,
                 periods: int, count: int, along: str = "u") -> Mask:
        """One opening drawn in (u, v), stamped along the building.

        The lattice answer to a run of window bays. `periods` is the pitch in
        periods of the wall's own motif -- `Site.periods` turns a measurement in
        metres into one -- and what comes back is `count` figures that are the
        same cells, not `count` rasterisations of the same rectangle.
        """
        return self.repeat(self.rect(u0, u1, v0, v1), periods, count, along)

    def on_a_lattice(self, doing: str) -> None:
        """Stop unless this building's frame sits on a lattice slope."""
        if self.slope is None:
            raise SystemExit(
                f"{doing} needs a lattice slope, and this building has none"
                + (f" -- UNLATTICED says: {self.unlatticed}"
                   if self.unlatticed else
                   ", because derived.json was measured before the frame was "
                   "put on one. Re-run probes/derive.py.")
                + "\nA repeat off the lattice is a redraw, not a copy: the "
                  "sections come out looking alike and rasterised differently.")

    def stamp(self, canvas, mask: Mask, y0: int, y1: int, periods: int,
              count: int) -> int:
        """Copy what stands over `mask` along the building, `count` more times.

        The whole idiom, in one call: draw one section, stamp the rest. See
        `Canvas.stamp` for what is copied and `Site.period` for what `periods`
        counts.
        """
        return canvas.stamp(mask, y0, y1, self.step(periods), count)

    def slack(self, cells: float = 1.0) -> float:
        """`cells` cells of this frame's grid, in metres.

        For every threshold that means "near enough to be the same thing" --
        the gap between two parts drawn separately, the reach that joins a
        floor to the wall it belongs to, the width of a seam. Written as metres
        those thresholds mean different numbers of cells on different
        buildings, and the corpus runs from square to the world to
        fifty-seven degrees off it: `dilate(2.0)` is two cells on one and 1.4
        on the other, and it is the diagonal building where a gap is hardest to
        rasterise and the threshold most needs to be generous.

        `Grading.slack` is the same function on the gate's side, and the two
        have to agree or the build closes a seam the gate then measures open.

        Not to be used for a dimension of the building. A balcony is 1.8 m deep
        because it is 1.8 m deep, at any angle.
        """
        return cells * self.staircase

    # -- what `derive` had to have written ---------------------------------

    def requires(self, *keys: str) -> None:
        """Stop unless `derive` wrote the blocks this build reads.

        A build that indexes a missing block dies on a KeyError three functions
        deep, which says nothing about what to do. This says which probe never
        ran.
        """
        missing = [key for key in keys if key not in self.d]
        if missing:
            raise SystemExit(
                f"derived.json carries no {', '.join(repr(k) for k in missing)}"
                " block. Those are written by `probes`, which needs the "
                "reference: convert and clip it and run probes/derive.py again.")

    # -- the standard readings of the section ------------------------------

    def storeys(self, scale: float = 1.0) -> None:
        """`levels`, `ground`, `roof` and `storey`, from the measured rhythm.

        Levels are rounded to whole blocks here and nowhere else. A level kept as
        5.75 all the way down to the fill turns into a different integer in two
        different sections and the floor ends up half a metre out of line with
        the wall that carries it.

        A storey rhythm nothing carried is not a measurement, and `derive` says
        so rather than returning a plausible number. This stops instead of
        building floors at whatever the autocorrelation settled on: a wrong
        storey height is not visible in the section, which grades the skyline, so
        nothing downstream would catch it.

        `scale` is for the building whose plan and whose reference are drawings
        of different sizes -- a game map that draws its block a quarter larger
        than the prototype the capture is of. The factor is measured, in that
        building's own probes, and it has to reach *every* height: applied to the
        tops and not to the rhythm it puts forty-five floors where the prototype
        has thirty-seven. Leave it at 1.0 unless something measured says
        otherwise, and never type it here.
        """
        found = self.d["storeys"]
        if not found.get("found", True):
            raise SystemExit(
                f"no storey rhythm was found (correlation {found['score']:.2f}"
                f" over the bands {found.get('bands')}).\n"
                "Either the bands are on facades the reference did not model -- "
                "move them in probes/derive.py and say why -- or this building "
                "genuinely has no repeating floor, in which case set "
                "DECLARED_STOREY there, with the source it came from.")

        # Rounded once, here. Everything downstream indexes whole blocks, and a
        # level carried as a float to two different sections becomes two
        # different integers.
        self.levels = [int(y * scale + 0.5) for y in found["levels"]]
        self.ground = self.levels[0]
        self.roof = self.levels[-1]
        self.storey = int(found["spacing"] * scale + 0.5)

    def heights(self, scale: float = 1.0, check: bool = True) -> None:
        """`tops` and `top`, from the skyline read over each part.

        Not one number for the whole building: where a wing steps down, a single
        median splits the difference and is wrong at both ends.

        `scale` is the same measured factor `storeys` takes, and for the same
        reason -- see there.

        `check=False` for a building that overrides some of what the skyline
        says before the answer is complete -- a part whose drawn mask overlaps
        its taller neighbour, so that a maximum over it returns the neighbour.
        Such a building calls `check_heights()` itself once its overrides are
        in, and skipping that call is how a part comes to be built to a height
        nothing measured.
        """
        self.tops = {
            name: int(entry["median"] * scale + 0.5)
            for name, entry in self.d["skyline"].items()
            if isinstance(entry, dict) and entry.get("median") is not None
            and name in self.parts
        }
        if check:
            self.check_heights()

    def check_heights(self) -> None:
        """Stop unless every drawn part has a height, and set `top`."""
        missing = [name for name in self.read.order if name not in self.tops]
        if missing:
            raise SystemExit(
                f"nothing states how tall {', '.join(missing)} is -- the "
                "reference has no material over that part and no height was "
                "declared for it. Add one to DECLARED_HEIGHTS in "
                "probes/derive.py, with the photograph or the sheet it was read "
                "off, and re-run it.")

        # The tallest thing measured anywhere, which is what the canvas is sized
        # from.
        self.top = max(self.tops.values())

    def ladder(self, to: int) -> list[int]:
        """The floor lines continued from the measured spacing up to `to`.

        `derive` writes only as many levels as its search ceiling reached, and a
        building taller than that ceiling has no measured line above it. The
        rhythm is still the measured one -- this repeats it rather than inventing
        a second number.
        """
        return list(range(self.ground, to + 1, self.storey))

    def terraces(self, name: str) -> list[tuple[int, Mask]]:
        """(height, where) for a part whose roof is not one level, tallest first.

        Empty for a flat roof, which is most parts: there the one number in
        `tops` is the whole answer and the footprint is the whole shape. A part
        that comes back with entries here is one the reference measured as
        stepping, and building it to `tops[name]` puts the wrong height over
        everything but the largest step -- quietly, because the section grades
        against the same median.

        Called on every run even by a building whose roofs are all flat: the day
        the clip is redrawn or the datum moves is the day a part acquires a step,
        and a build that never asks will extrude straight through it.
        """
        entry = self.d.get("roof", {}).get(name, {})
        return [(int(t["height"] + 0.5), Mask.loads(t["mask"]))
                for t in entry.get("terraces", []) if "mask" in t]

    # -- drawing helpers ---------------------------------------------------

    def region(self, predicate) -> Mask:
        return self.frame.region(self.width, self.length, predicate)

    def rect(self, u0: float, u1: float, v0: float, v1: float) -> Mask:
        return self.frame.rect(self.width, self.length, u0, u1, v0, v1)

    # Whether a circle is drawn about a point the block grid is symmetric on.
    #
    # True, and it is not a tolerance: a circle has no angle, so nothing is lost
    # by drawing it in world space, and what is gained is the only property a
    # circle has. Drawn through the frame, its centre lands at an arbitrary
    # sub-cell offset and the arc comes back longer in one octant than in its
    # mirror -- a dent, on the one shape whose whole job is not to have one. See
    # `Mask.circle`. The centre moves by a quarter of a metre at most.
    #
    # False where the centre is itself the measurement under test.
    ROUND_ON_THE_GRID = True

    def disc(self, cu: float, cv: float, radius: float,
             inner: float = 0.0) -> Mask:
        if not self.ROUND_ON_THE_GRID:
            return self.frame.disc(self.width, self.length, cu, cv, radius, inner)
        cx, cz = self.frame.to_world(cu, cv)
        return Mask.circle(self.width, self.length, cx, cz, radius, inner)

    def empty(self) -> Mask:
        return Mask(self.width, self.length)

    def at(self, u: float, v: float) -> tuple[int, int]:
        x, z = self.frame.to_world(u, v)
        return int(x), int(z)

    def stations(self, a: float, b: float, pitch: float) -> list[float]:
        """Evenly spaced positions along a run, both ends inset by half a pitch."""
        count = max(1, int((b - a) / pitch))
        step = (b - a) / count
        return [a + (i + 0.5) * step for i in range(count)]

    def grid(self, within: Mask, pitch: float, phase: float = 0.5) -> Mask:
        """Cells on a `pitch` grid in the building's own frame, inside `within`.

        For the things that really are set out on a grid -- the posts under a
        plant rack, the columns of a porte-cochere -- as opposed to planting,
        which `build.scatter` deliberately keeps off one.

        The grid is laid in `(u, v)` and rasterised by `Frame.region`, so the
        angle is handled in the one place it is ever handled. A grid stepped in
        world x and z instead would stand at the building's own angle to itself,
        and a row of posts along a 52-degree wall would walk out of the wall.

        One cell per intersection, and the nearest cell to it rather than every
        cell the predicate catches: a band of `pitch`-modulo cells is a lattice
        of stripes, which is the mistake `Mask.speckle` exists to end, and a post
        is one block.
        """
        if pitch <= 0:
            raise ValueError("a grid steps by a positive pitch")
        out = Mask(self.width, self.length)
        us = self.stations(self.u0, self.u1, pitch)
        vs = self.stations(self.v0, self.v1, pitch)
        if phase != 0.5:
            shift = (phase - 0.5) * pitch
            us = [u + shift for u in us]
            vs = [v + shift for v in vs]
        for u in us:
            for v in vs:
                x, z = self.at(u, v)
                if 0 <= x < self.width and 0 <= z < self.length \
                        and within.get(x, z):
                    out.set(x, z)
        return out

    def narrower_than_the_staircase(self, width: float, what: str) -> None:
        """Stop rather than draw something that will rasterise into corners."""
        if width < self.staircase:
            raise SystemExit(
                f"{what} is {width:.2f} m wide, under this frame's "
                f"{self.staircase:.2f} m staircase; it would rasterise into "
                "cells joined only at their corners")

    # -- parts -------------------------------------------------------------

    def footprint(self, name: str) -> Mask:
        """One part as the plan drew it, opened by the pad.

        The drawn mask and not its bounding box. A map is a drawing and its edges
        wander, so tracing inherits every wobble -- but a wobble and a shape are
        different things, and only the building can tell them apart. A tower whose
        long faces bow in by six metres is twelve overlapping rectangles if its
        strips are boxed, which is a different building; a rectangular block whose
        drawn edge wanders by a cell is better boxed. Use `box` for the second.

        A disc is drawn as a disc either way: `plan.Part` fits a circle to it and
        reports its residual, so the centre and the radius are measurements and
        the clean figure is the honest way to draw them.

        **This rounds every convex corner by about `pad + CLOSE`, whatever the
        map drew.** Both operations are Euclidean and a Euclidean dilation is a
        disc, so the corner is bitten by a quarter-disc that the erosion has
        nothing to push back against -- see `CLOSE`. On top of that a traced
        corner usually arrives chamfered already, because that is how a hand
        draws one. Neither shows up in any row of the gate: a rounded corner
        casts the same silhouette, grades the same at every station, and encloses
        the same area to within a few cells. Use `squared` for a part that really
        is a rectangle.
        """
        p = self.parts[name]
        if p.kind == "disc" and self.ROUND:
            return self.disc(p.centre[0], p.centre[1], p.radius + self.pad)
        if not self.CLOSE:
            out = p.mask.dilate(self.pad) if self.pad else p.mask
        else:
            out = p.mask.dilate(self.pad + self.CLOSE).erode(self.CLOSE)
        # Last, after the pad and the closing: both are morphological and both
        # leave their own small teeth along a diagonal, so straightening first
        # would put them back.
        if self.STRAIGHT:
            out = out.straighten(self.frame, self.STRAIGHT)
        return out

    def squared(self, name: str, keep: float | None = None,
                trim: float = 0.02, edge: str = "both") -> Mask:
        """One part rebuilt as the rectangle it is, from its own measurements.

        The middle idiom between `footprint` and `box`, on the other axis from
        `straighten`. Straightening asks "is this edge one line or a stack of
        little ones"; this asks "is this corner a right angle", and nothing else
        in the pipeline does -- a chamfer four cells deep survives any
        straightening tolerance worth using, because it is further off the chord
        than drawing noise ever is, and `Site.footprint` then rounds it further
        with its own pad and closing.

        Every number comes off the part's own mask -- see `Mask.squared` for the
        quantiles and for what `keep` protects. The pad is applied here, so a
        part squared and a part footprinted stand at the same face.

        Right where the building's corner is a right angle, which is most parts
        of most buildings. Wrong wherever the drawn shape carries a measurement:
        a bowed slab, a rake somebody drew on purpose, anything the map traced
        off a curve. Those keep `footprint`, and `keep` is how a part that is a
        rectangle with one corner cut back gets both.
        """
        p = self.parts[name]
        if p.kind == "disc" and self.ROUND:
            return self.disc(p.centre[0], p.centre[1], p.radius + self.pad)
        box = p.mask.squared(self.frame, trim=trim, keep=keep, edge=edge)
        if box is None:
            return self.footprint(name)
        u0, u1, v0, v1 = box
        return self.rect(u0 - self.pad, u1 + self.pad,
                         v0 - self.pad, v1 + self.pad)

    # How many runs a fitted face's own slope may have. The frame's slope only
    # regularises the faces along u and v; a rake, a splayed wing or a plot
    # boundary stands at whatever it was measured at and rasterises into the
    # never-repeating staircase the lattice exists to remove. Zero leaves each
    # face on its measured line -- right where the face's own direction is the
    # measurement under test and nothing may round it.
    FACE_RUNS = 3

    def faceted(self, name: str, tolerance: float = 1.0, least: int = 3,
                report: bool = True) -> Mask:
        """One part rebuilt as the polygon of its own fitted faces.

        The idiom for a part that is a polygon and not a rectangle: a slab with
        a raked end, a wing splayed off the street grid, anything cut off by a
        plot boundary. `squared` can only offer a rectangle in u and v, so on
        one of those it either squares off the rake -- which is the one thing
        the mapper drew on purpose -- or is not used, and every building that
        met the case wrote the rake by hand off a pair of extreme points. A pair
        of extreme points is the reading a single cell of tracing whisker moves,
        and on one building it put a five metre wedge down a side that is
        straight on the map.

        Here each face is a line fitted to every station of itself and the
        corners are where those lines cross, so a whisker is one station out of
        thirty and the corner is as good as the two faces that make it. See
        `Mask.edges` for how the faces are found and `Edge` for what each one
        knows about its own fit.

        The pad is applied by moving each face out along its own normal, so a
        padded corner is still a corner. `footprint` pads with a dilation, which
        is Euclidean and therefore a disc, and comes back with every convex
        corner bitten by a quarter-disc of `pad + CLOSE`.

        Stops rather than guesses when fewer than `least`-station faces are
        left, which is what a disc, a blob or a part smaller than its own noise
        comes to. Quietly handing back the drawn shape there would put the
        rounded corner into the build under the name of the fix for it. Use
        `footprint` for a shape that has no faces, and say so where it is used.
        """
        p = self.parts[name]
        if p.kind == "disc" and self.ROUND:
            return self.disc(p.centre[0], p.centre[1], p.radius + self.pad)
        measured = p.mask.edges(self.frame, tolerance=tolerance, least=least)
        faces = ([e.snapped(self.frame, self.FACE_RUNS) for e in measured]
                 if self.FACE_RUNS else measured)
        out = p.mask.faceted(self.frame, pad=self.pad, edges=faces)
        if out is None:
            raise SystemExit(
                f"part {name!r} leaves {len(faces)} straight face(s) at a "
                f"{tolerance:.1f} m tolerance, and a polygon needs three; it is "
                "a curve, a blob or smaller than its own drawing noise -- draw "
                "it with footprint or box and say why")
        if report:
            # The face reported is the one furthest off its line at a single
            # station, not the one with the worst average: a bowed face averages
            # well and is the one worth looking at.
            bent = max(faces, key=lambda e: e.worst)
            print(f"  {name}: {len(faces)} face(s), "
                  + ", ".join(f"{e.angle:.0f} deg/{e.run:.0f} m" for e in faces)
                  + f"; worst fit {bent.worst:.2f} m at a station, "
                  f"{bent.spread:.2f} m over {bent.kept}/{bent.span}")
            if self.FACE_RUNS:
                # What the snap moved, face by face. A decision that only shows
                # up in the geometry is one nobody can argue with.
                moved = [(was, now) for was, now in zip(measured, faces)
                         if abs(now.angle - was.angle) > 0.005]
                if moved:
                    print("    snapped: " + ", ".join(
                        f"{was.angle:.1f}->{now.angle:.1f} deg ("
                        f"{now.run / 2 * abs(math.sin(math.radians(now.angle - was.angle))):.2f}"
                        " m at its ends)" for was, now in moved))
        return out

    def box(self, name: str) -> Mask:
        """One part as the rectangle its extent describes, opened by the pad.

        Right where the part really is rectangular and the drawn edge is noise;
        wrong wherever the plan's own shape carries a measurement -- see
        `footprint`.

        The bounding extent, unlike `squared`, which fits the rectangle to
        trimmed quantiles and can leave a deliberate rake alone. A single stray
        cell of tracing whisker moves this and does not move that.
        """
        p = self.parts[name]
        if p.kind == "disc" and self.ROUND:
            return self.disc(p.centre[0], p.centre[1], p.radius + self.pad)
        return self.rect(p.u0 - self.pad, p.u1 + self.pad,
                         p.v0 - self.pad, p.v1 + self.pad)

    def group(self, names, boxed: bool = False) -> Mask:
        """Several parts as one mask."""
        out = self.empty()
        for name in names:
            out = out | (self.box(name) if boxed else self.footprint(name))
        return out

    # -- the ground around it ----------------------------------------------

    def across(self, u: float) -> tuple[float, float]:
        """How far the drawn mass reaches across the plot at this station.

        The two v of the building's own faces at station `u`, clamped past
        either end so a cell out beyond the ends still belongs to a side rather
        than to neither.
        """
        if self._reach is None:
            reach: dict[int, tuple[float, float]] = {}
            for x, z in self.mass.cells():
                u_, v = self.frame.to_local(x + 0.5, z + 0.5)
                lo, hi = reach.get(int(u_), (1e9, -1e9))
                reach[int(u_)] = (min(lo, v), max(hi, v))
            self._reach = reach
        if not self._reach:
            return (self.v0, self.v1)
        key = int(u)
        hit = self._reach.get(key)
        if hit is not None:
            return hit
        near = min(self._reach, key=lambda s: abs(s - key))
        return self._reach[near]

    def side(self, far: bool) -> Mask:
        """Everything on one side of the building, by its own middle line.

        A block with ground on both sides has two different grounds -- a street
        one way and a beach the other, a car park one way and a garden the other
        -- and the map usually draws neither, so the only thing that can tell
        them apart is the building standing between them. `far` picks the high-v
        side.

        **Split on the middle of the building's own depth, not on its two
        faces.** Cut on the faces, the wedge off each end of the block -- between
        the street front and the wing tips -- belongs to neither side, and
        whatever is laid last wins it: on one building that put beach sand along
        the length of the avenue. The middle line has no such gap, because every
        station is on exactly one side of it.
        """
        return self.region(
            lambda u, v: (v > sum(self.across(u)) / 2) == bool(far))

    def flipped(self, axis_u: float) -> "Site":
        """This site, drawing everything mirrored across `u = axis_u`.

        The way to build the second of a pair. Write the section once, call it
        twice -- the second time with `site.flipped(middle)` -- and the two come
        out mirror images by construction rather than by two sets of numbers
        agreeing.

        That matters more than it sounds. A mirror pair measured twice is two
        independent readings of the same building, and photogrammetry does not
        read them the same: on one hotel the two towers' roof profiles differed
        by up to five metres at stations where the real roofs are level, and the
        build extruded the difference faithfully. Built once and reflected, the
        two are identical and the disagreement becomes what it actually is -- a
        question about the reference, which `measure.twins` asks out loud.

        The plan does not move: `mass`, `parts` and `tops` are still the drawn
        building's. What is reflected is the drawing, which is the only thing
        that should be.
        """
        import copy

        out = copy.copy(self)
        out.frame = self.frame.flipped(axis_u)
        return out

    def slice_of(self, mask: Mask, u0: float, u1: float) -> Mask:
        """The part of a mask between two stations along the building."""
        return mask & self.rect(u0, u1, -1e4, 1e4)

    def cells_of(self, cells) -> Mask:
        """A mask from a list of plan cells measured off the reference."""
        out = self.empty()
        for u, v in cells:
            x, z = self.frame.to_world(u + 0.5, v + 0.5)
            x, z = int(x), int(z)
            if 0 <= x < self.width and 0 <= z < self.length:
                out.set(x, z)
        return out


__all__ = ["Site"]
