"""What is on top, cell by cell, instead of one number per part.

`measure.skyline` gives a part one height. That is right for a flat roof and
wrong for everything else, and the wrongness is quiet: the section grades the
same median it was built from, so the numbers agree with each other while the
building is metres out.

Five buildings in a row hit this and five wrote their own way through it:

    a tower whose roof rises to a ridge and falls 25 m to both flanks -- one
    median put 24 stations of 34 outside tolerance, the worst by 16.9 m;

    a block with a plant room in the middle of a flat deck -- the median came
    back as the plant room and the whole building grew two metres;

    a slab stepping down along its street facade -- nine stations failed
    together at +3 m, and neither the plan nor the photographs said the step
    was there;

    two towers each a metre and a half higher at one end than the other, where
    per-station steps gave eleven flights of stairs on a flat roof;

    a rotunda standing on a pool deck, where the median over its own footprint
    was the deck it stands on and the maximum was the canopy crossing it.

All five are the same measurement: **the height of material over each cell of
the plan**, read once, then asked different questions. That is `Roof`.

Reading it costs one pass over the reference and it is done in the *plan's*
coordinates, through the registration, so nothing downstream has to convert
anything. What it deliberately does not do is voxelise: every answer here is a
level, a step or a plateau -- a number a wall can be built to -- and never a
per-cell height. A build that follows a photogrammetric surface cell by cell is
a pile of rubble, and `docs/pipeline.md` says not to.
"""

from __future__ import annotations

from .mask import Mask

# Cells this far apart in height belong to different terraces. A flat roof
# measured by photogrammetry is noisy by about a metre, so anything under that
# is the method and not the building.
STEP = 1.5

# A terrace smaller than this is noise, a lift overrun, or the corner of
# something the clip caught.
MIN_CELLS = 12

# How far above the deck a cell has to stand to be plant rather than roof.
PLANT = 1.5

# Above this share of touching cells, two levels of one lump are one surface the
# capture split rather than two things standing on each other. A checkerboard
# scores 1.0 by construction; two blobs meeting along a seam score their border
# over their area, which for anything bigger than a few cells is well under a
# third. The gap between those is wide, so the threshold is not delicate.
WOVEN = 0.6


def woven(a: Mask, b: Mask) -> float:
    """How much two masks are threaded through each other, 0 to 1.

    The question a bounding box cannot answer, and the reason one building
    quietly built a rooftop plant deck as a single blind box.

    Photogrammetry loses glass, so a glazed hall comes back with its cells split
    between two heights in a check pattern over the *same* rectangle -- one
    surface, reported as two levels. A machine room standing beside an open rack
    of air handlers is also two levels, and its two are real. Both pairs overlap
    in plan, so the rule that merged them on overlapping extents merged both, and
    the second one is a box where the reference has a box and a frame.

    Told apart by contact rather than by extent: the share of each mask's cells
    that have a cell of the other orthogonally beside them, averaged over the two
    directions. A checkerboard over one rectangle gives 1.0 -- every cell of each
    is surrounded by the other. Two solid shapes meeting along an edge give their
    shared border divided by their area, which falls as they get bigger. It is
    the same measurement either way and it does not care which is on top.
    """
    if a.width != b.width or a.length != b.length:
        raise ValueError("masks are on different grids")

    def touching(one: Mask, other: Mask) -> float:
        cells = one.cells()
        if not cells:
            return 0.0
        met = 0
        for x, z in cells:
            if (other.get(x - 1, z) or other.get(x + 1, z)
                    or other.get(x, z - 1) or other.get(x, z + 1)):
                met += 1
        return met / len(cells)

    return (touching(a, b) + touching(b, a)) / 2


def one_surface(tiers, threshold: float = WOVEN) -> bool:
    """Whether these levels are one thing the capture split, or several things.

    Every pair has to be woven for the answer to be yes. A lump of three levels
    where two are a checkerboard and the third is a box standing on them is not
    one surface, and averaging the three pairwise scores would call it one.
    """
    masks = [t.mask if isinstance(t, Terrace) else t for t in tiers]
    if len(masks) < 2:
        return False
    return all(woven(masks[i], masks[j]) >= threshold
               for i in range(len(masks))
               for j in range(i + 1, len(masks)))


class Terrace:
    """One measured level of a roof, and where it is."""

    __slots__ = ("mask", "low", "high", "cells")

    def __init__(self, mask: Mask, low: float, high: float):
        self.mask = mask
        self.low = low
        self.high = high
        self.cells = mask.count()

    @property
    def height(self) -> float:
        """The middle of the spread, not the median of it.

        A section grades the *worst* station of a terrace, not its typical one,
        so the number that minimises the worst error is the middle of the range.
        Taking the median instead builds a terrace that is right where most of
        it is and out at both ends, which is how eleven flights of stairs got
        built on a flat roof.
        """
        return (self.low + self.high) / 2

    def __repr__(self) -> str:
        return (f"<terrace {self.height:.1f} m ({self.low:.1f}..{self.high:.1f}), "
                f"{self.cells} cells>")


class Roof:
    """The height of material over each cell of the plan.

    Built through the registration, so `grid` is keyed by plan cell and its
    values are metres above the reference's datum -- the same units everything
    else in a build script speaks.
    """

    __slots__ = ("grid", "width", "length")

    def __init__(self, grid: dict[tuple[int, int], float], width: int,
                 length: int):
        self.grid = grid
        self.width = width
        self.length = length

    @classmethod
    def read(cls, mesh, link, frame, width: int, length: int) -> "Roof":
        """One pass over the reference, into the plan's own cells.

        `link` is the building's `Link` -- it carries the registration and any
        flip, so a capture standing half a turn round lands the right way up
        without the caller knowing about it.
        """
        grid: dict[tuple[int, int], float] = {}
        for i in range(len(mesh)):
            u, v = link.frame.to_local(mesh.x[i], mesh.z[i])
            x, z = frame.to_world(link.to_build_u(u), link.to_build_v(v))
            key = (int(x), int(z))
            height = mesh.y[i] - link.datum
            if height > grid.get(key, -1e9):
                grid[key] = height
        return cls(grid, width, length)

    # -- reading it ---------------------------------------------------------

    def over(self, mask: Mask) -> list[float]:
        """Every height the reference holds over a mask, in no order."""
        return [h for cell, h in self.grid.items() if mask.get(*cell)]

    def deck(self, mask: Mask, band: float = 1.0) -> float:
        """The level most of the roof actually sits at.

        The mode of the heights in bands of `band`, not the median or the
        maximum. A flat deck with a plant room on it has a median pulled up by
        the plant room and a maximum that *is* the plant room; the mode is the
        deck, which is the thing a parapet stands on.

        Cells the reference never saw are absent rather than zero, so a roof
        half in shadow still reports the half that was seen.
        """
        heights = self.over(mask)
        if not heights:
            return 0.0
        tally: dict[int, int] = {}
        for h in heights:
            k = int(h / band)
            tally[k] = tally.get(k, 0) + 1
        best = max(tally, key=lambda k: (tally[k], k))
        inside = [h for h in heights if int(h / band) == best]
        return sum(inside) / len(inside)

    def plant(self, mask: Mask, deck: float | None = None,
              above: float = PLANT, min_cells: int = 8) -> list[Mask]:
        """What stands on the deck: lift overruns, stairs, machine rooms.

        Returned as separate masks rather than one, because they are separate
        objects and a build wants to give each its own measured height. Groups
        smaller than `min_cells` are dropped -- at one metre per cell, a
        four-cell blob is photogrammetry noise, not a building.
        """
        line = (self.deck(mask) if deck is None else deck) + above
        raised = Mask(self.width, self.length)
        for cell, height in self.grid.items():
            if height > line and mask.get(*cell):
                raised.set(*cell)
        return [m for m in raised.components(min_cells=min_cells, diagonal=True)]

    def terraces(self, mask: Mask, step: float = STEP,
                 min_cells: int = MIN_CELLS,
                 floor: float | None = None) -> list[Terrace]:
        """The roof as a handful of measured levels, tallest first.

        Levels come from the *modes* of the height histogram, not from gaps in
        the sorted heights. Gaps look like the obvious way to do it and chain:
        a roof that slopes gently from 14 m to 21 m has no gap anywhere along
        it, so single-linkage swallows the lot and returns one terrace at the
        average of a roof and a pavement. Modes ask a different question --
        where do the cells *pile up* -- and a slope has no pile.

        Every cell then joins the nearest level within `step`, levels are split
        into connected areas, and whatever is left over is handed to the terrace
        that reaches it, so the result covers the mask with no holes.

        `step` is 1.5 m for a reason: photogrammetry on a flat roof is noisy by
        about a metre, and anything tighter turns one roof into a staircase --
        which is exactly what per-station slicing did on a building whose roof
        was flat to within noise.

        This is the wrong tool for a part that is *narrow* -- a drum, a turret,
        a lantern. Most of the plan inside such a part is whatever it stands on,
        so the levels come back as the deck below it. Use `plateau`, which reads
        across the part rather than over it.
        """
        from . import measure

        # Cells the reference could not see over -- under a canopy, inside a
        # court, in the shadow of a taller neighbour -- hold the ground, not the
        # roof. Left in, they drag every level down towards the pavement.
        #
        # `floor` is where the roof stops being the roof. The default is a
        # storey and a half below the deck: lower than any step a building
        # actually has, higher than any hole in a capture.
        if floor is None:
            floor = self.deck(mask) - 6.0
        here = [(h, cell) for cell, h in self.grid.items()
                if mask.get(*cell) and h >= floor]
        if not here:
            return []

        band = 0.5
        low = min(h for h, _ in here)
        counts, base = measure.histogram([h for h, _ in here], band, low,
                                         max(h for h, _ in here) + band)
        levels = [h for h, _ in measure.peaks([float(c) for c in counts], band,
                                              floor=0.05, separation=step,
                                              origin=base)]
        if not levels:
            levels = [self.deck(mask)]

        found: list[Terrace] = []
        for level in levels:
            near = Mask(self.width, self.length)
            inside: list[float] = []
            for h, cell in here:
                if abs(h - level) <= step and min(
                        abs(h - other) for other in levels) == abs(h - level):
                    near.set(*cell)
                    inside.append(h)
            if len(inside) < min_cells:
                continue
            # One terrace per level, not per connected piece. A level that
            # turns up in two places -- two wings at the same height, a canopy
            # with a hole in it -- is one measured height and one thing to
            # build; `terrace.mask.components()` is there for the rare caller
            # that needs the pieces apart.
            found.append(Terrace(near, min(inside), max(inside)))

        return self._absorb(found, mask)

    def _absorb(self, found: list[Terrace], mask: Mask) -> list[Terrace]:
        """Give every unclaimed cell to the nearest terrace.

        Grouping leaves crumbs -- a sloping edge, a parapet, every noisy cell of
        a capture -- and a hole in the roof is a hole in the building. They go
        to whichever terrace reaches them first, which is the answer a person
        gives when asked which level the edge of a roof belongs to.
        """
        if not found:
            return []
        found.sort(key=lambda t: -t.height)
        claimed = Mask(self.width, self.length)
        owner: dict[int, int] = {}
        for index, terrace in enumerate(found):
            for i, v in enumerate(terrace.mask.bits):
                if v:
                    claimed.bits[i] = 1
                    owner[i] = index

        frontier = [i for i, v in enumerate(claimed.bits) if v]
        w, h = self.width, self.length
        while frontier:
            nxt = []
            for i in frontier:
                x, z = i % w, i // w
                for nx, nz in ((x - 1, z), (x + 1, z), (x, z - 1), (x, z + 1)):
                    if not (0 <= nx < w and 0 <= nz < h):
                        continue
                    j = nz * w + nx
                    if claimed.bits[j] or not mask.bits[j]:
                        continue
                    claimed.bits[j] = 1
                    owner[j] = owner[i]
                    nxt.append(j)
            frontier = nxt

        for index, terrace in enumerate(found):
            grown = Mask(w, h)
            for i, who in owner.items():
                if who == index:
                    grown.bits[i] = 1
            terrace.mask = grown
            terrace.cells = grown.count()
        return [t for t in found if t.cells]

    def profile(self, mask: Mask, frame, along: bool = True,
                step: float = 1.0) -> list[tuple[float, float]]:
        """(station, highest) along or across the building, over a mask.

        For reading a ridge, a slope or a step by eye before deciding how to
        build it -- and for the case `skyline` gets wrong in the other
        direction: a part that is *narrow* is read across, where it separates
        from whatever crosses over it, and not along.
        """
        buckets: dict[int, float] = {}
        for cell, height in self.grid.items():
            if not mask.get(*cell):
                continue
            u, v = frame.to_local(cell[0] + 0.5, cell[1] + 0.5)
            k = int((u if along else v) / step)
            if height > buckets.get(k, -1e9):
                buckets[k] = height
        return [(k * step, h) for k, h in sorted(buckets.items())]

    def plateau(self, mask: Mask, frame, along: bool = False,
                step: float = 1.0, drop: float = STEP
                ) -> tuple[float, float, float]:
        """(height, from, to) of the flat run through the middle of a part.

        What a rotunda, a drum or a dome needs, and what a median over its own
        footprint cannot give: inside such a part most of the plan is the deck
        it stands on, so the median is the deck and the maximum is whatever
        crosses over it. Read across instead and the shell separates -- a flat
        run at the crown, falling away on both sides.

        Starts at the middle station and walks out while the profile stays
        within `drop` of where it started.
        """
        rows = self.profile(mask, frame, along, step)
        if not rows:
            return 0.0, 0.0, 0.0
        seat = len(rows) // 2
        run = [seat]
        for direction in (-1, 1):
            i = seat + direction
            while 0 <= i < len(rows) and rows[i][1] > rows[run[-1]][1] - drop:
                run.append(i)
                i += direction
        edges = [rows[i][0] for i in run]
        return (max(rows[i][1] for i in run), min(edges), max(edges))

    def lines(self, mask: Mask, name: str = "roof") -> list[str]:
        """What was read, in the form that says whether to trust one number.

        The spread is the point. A part whose deck and maximum are four metres
        apart is not a part with one height, and the report saying so is the
        difference between building it right and finding out at the section.
        """
        heights = self.over(mask)
        if not heights:
            return [f"  {name}: the reference holds nothing over it"]
        heights.sort()
        deck = self.deck(mask)
        low, high = heights[0], heights[-1]
        median = heights[len(heights) // 2]
        out = [f"  {name}: deck {deck:.1f} m, median {median:.1f}, "
               f"range {low:.1f}..{high:.1f} over {len(heights)} cells"]
        if high - deck > PLANT:
            out.append(f"  {'':{len(name)}}  {high - deck:.1f} m stands above "
                       "the deck -- plant, a ridge, or something crossing over; "
                       "one height will not describe this part")
        return out


__all__ = ["MIN_CELLS", "PLANT", "Roof", "STEP", "Terrace", "WOVEN",
           "one_surface", "woven"]
