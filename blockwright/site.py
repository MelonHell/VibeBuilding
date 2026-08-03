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
    # Set it to 0.0 in a building whose parts are boxes and discs: nothing was
    # traced there, so there are no notches, and closing only rounds the corners
    # off shapes the map drew square.
    CLOSE = 0.5

    # Whether a part the decomposition called a disc is drawn as one. True is
    # right when the circle fit was a measurement; a building whose "discs" are
    # a wing turning a right angle and the space between two others sets it
    # False and takes the drawn masks instead.
    ROUND = True

    def __init__(self, derived: dict, read, pad: float = 0.5):
        self.d = derived
        self.read = read
        self.mass, self.frame = read.mass, read.frame
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

    def disc(self, cu: float, cv: float, radius: float,
             inner: float = 0.0) -> Mask:
        return self.frame.disc(self.width, self.length, cu, cv, radius, inner)

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
        """
        p = self.parts[name]
        if p.kind == "disc" and self.ROUND:
            return self.disc(p.centre[0], p.centre[1], p.radius + self.pad)
        if not self.CLOSE:
            return p.mask.dilate(self.pad) if self.pad else p.mask
        return p.mask.dilate(self.pad + self.CLOSE).erode(self.CLOSE)

    def box(self, name: str) -> Mask:
        """One part as the rectangle its extent describes, opened by the pad.

        Right where the part really is rectangular and the drawn edge is noise;
        wrong wherever the plan's own shape carries a measurement -- see
        `footprint`.
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
