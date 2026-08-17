"""What the building is supposed to be made of, and whether it is.

Everything else in the pipeline grades the shape of what was built. Nothing
records what was *meant* to be there, so the one failure none of it can see is
the part nobody wrote any code for. A canopy with no glass, a cone with no
bridge to it, a pool that was never dug: the massing registers, the section
passes, the structure is in one piece, and the answer comes back green because
every question asked was about material that exists.

The schedule is the missing question. It has three layers, and each gap between
two of them is a different bug:

    manifest        what the building has, written by hand from the photographs
    declaration     what the build claims it put there -- a name, a mask, a
                    y-range, written next to the code that places the blocks
    schematic       what actually stands

A manifest entry with no declaration is a part **nobody built**. A declaration
with no blocks under it is a part **built to nothing** -- a mask eroded away, a
loop whose range came out empty, geometry pushed off the canvas. A declaration
whose neighbour is out of reach is a part **built in the wrong place**, which is
the bridge that stops three blocks short of the cone.

So an entry with no implementation makes the gate fail red. That is deliberate
and it is the whole point: this is a specification, and a specification that
goes quiet when it is unmet is a wish list. Write the manifest first, watch it
fail, then build until it stops.

Reading the photographs is not something that can be computed. Writing down what
you saw in them is, and that is the honest artefact -- which is why every item
carries a `source` naming where the author saw the thing.

The build and the gate are separate processes, so declarations travel between
them in a sidecar next to the schematic. The gate does the counting itself,
against the schematic, so the build cannot mark its own homework: all a
declaration says is *where to look*.
"""

from __future__ import annotations

import base64
import json
import math
import zlib
from pathlib import Path

from .mask import Mask
from .schem import AIR

# Two things that touch along a 52-degree wall are up to |cos t| + |sin t| =
# 1.41 cells apart, centre to centre, because the staircase steps diagonally
# between them. Anything tighter would report a genuine join as a gap.
REACH = 1.5


def _pack(mask: Mask) -> str:
    return base64.b64encode(zlib.compress(bytes(mask.bits), 9)).decode("ascii")


def _unpack(text: str, width: int, length: int) -> Mask:
    bits = bytearray(zlib.decompress(base64.b64decode(text)))
    if len(bits) != width * length:
        raise ValueError(
            f"packed mask is {len(bits)} cells, expected {width * length}"
        )
    return Mask(width, length, bits)


class Item:
    """One line of the schedule: a part the building is supposed to have.

    `placed` is for the part a photograph proves exists and nothing measures the
    position of: the stepped corner of a bowl, the glazed wall behind an
    outfield, a lift core somewhere along a street. The pipeline's rule is that
    a photograph is authoritative for material and rhythm and never for a
    dimension, and that rule is right; the failure it leaves behind is that such
    a part is simply never built, and "not built" and "not there" then look the
    same in every render.

    So the position is written as a **count against something measured** -- "the
    third arch from the corner", "behind sections 34 to 38", "the two bays east
    of the seam" -- and never as a distance. That distinction is the whole of it.
    A photograph cannot say 37 metres, and it can say *third*: counting is
    exactly what a photograph is good for, which is why `docs/sources.md` already
    scores it 2 for parts. A part positioned by index stays true when the map is
    redrawn or the capture is re-clipped, and a part positioned by a number
    somebody read off a picture goes quietly wrong the first time either moves.

    The build resolves the index against its own measured anchors -- the bays
    `Frame.bays` sets out, the spans `measure.apart` grouped, the stations the
    section cuts -- so the arithmetic is code and not a comment. What this field
    carries is the sentence a reader needs, and the gate prints it: a build with
    placed parts finishes `ungraded` on that row, because the position was
    argued for rather than checked.

    `blocks` names what this part is made of, and it is the difference between
    a check and a formality. Without it a declaration is satisfied by anything
    non-air standing in its mask -- and a window bay declared over the wall it
    is cut into is satisfied by the wall, whether or not a single pane was
    placed. Naming the glass makes "the glazing was never built" a failure
    instead of a thing somebody notices in a render three rounds later.

    Names are matched on the part before the state, so `minecraft:oak_fence`
    covers `minecraft:oak_fence[north=true]`.

    `copies` is how many of this part the building has, when they are all the
    same one -- a row of villas, a terrace of houses, a court of identical
    cottages. **A count and not a step.** Counting is what a photograph is good
    for, by the same argument `placed` rests on; the vector the copies step by
    is a measurement and lives in `derived.json` beside `frame.staircase`, where
    it is worked out rather than typed.

    What it buys is a row nothing else can supply. The copies of a section are
    the same silhouette, the same section, the same plan, the same materials and
    the same schedule whether they are identical or merely alike, so a build
    that stopped copying and started redrawing would read as correct
    everywhere -- and read as wrong from the ground, immediately. Declaring the
    count lets the gate compare the blocks themselves; see `checks.copies`.

    The two ends of a run are usually not copies -- a gable, a stair core, a
    corner return -- and they are their own items rather than a copy with an
    edit. A stamp that gets edited afterwards is not a copy of anything, and the
    row would need per-copy exceptions to say so, which is the shape of a
    measure about to stop measuring.
    """

    __slots__ = ("name", "what", "source", "near", "reach", "blocks", "placed",
                 "copies")

    def __init__(self, name: str, what: str, source: str,
                 near: str | tuple[str, ...] = (), reach: float = REACH,
                 blocks: str | tuple[str, ...] = (), placed: str = "",
                 copies: int = 0):
        self.name = name
        self.what = what
        self.source = source
        self.near = (near,) if isinstance(near, str) else tuple(near)
        self.reach = reach
        self.blocks = (blocks,) if isinstance(blocks, str) else tuple(blocks)
        if placed and not placed.strip():
            raise ValueError(
                f"{name!r} is placed by judgement and says nothing about how; "
                "name the anchor it was counted against")
        self.placed = placed.strip()
        if copies and int(copies) < 2:
            raise ValueError(
                f"{name!r} declares {copies} copies; a repeat is two or more. "
                "A part there is one of is an ordinary item.")
        self.copies = int(copies)

    def __repr__(self) -> str:
        near = f", near {'+'.join(self.near)}" if self.near else ""
        return f"<item {self.name}: {self.what}{near}>"


class Declaration:
    """Where a build says it put a part. Half-open in y, like `Canvas.fill`."""

    __slots__ = ("mask", "y0", "y1")

    def __init__(self, mask: Mask, y0: float, y1: float):
        self.mask = mask
        self.y0 = y0
        self.y1 = y1


class Repeat:
    """Where a build says it stamped one section and copied it.

    The motif is the mask of the *first* copy and the y range is the volume that
    copy owns -- not the band the whole run occupies. The gate reads the blocks
    under the motif, then under the motif shifted by one step, and so on, and
    compares them; handing it the run's outline instead would compare the run
    with itself and pass whatever it was given.
    """

    __slots__ = ("motif", "y0", "y1", "step", "count")

    def __init__(self, motif: Mask, y0: int, y1: int,
                 step: tuple[int, int], count: int):
        self.motif = motif
        self.y0 = int(y0)
        self.y1 = int(y1)
        self.step = (int(step[0]), int(step[1]))
        self.count = int(count)


class Standing:
    """What was found under a declaration: the cells, and how tall they run.

    `foreign` is what stands in the declared mask that the item's `blocks` do
    not name, counted by block. It is the answer to the question the audit used
    to leave open: a part reported as five per cent built is either five per
    cent built or built out of something else, and those are different bugs in
    different files.
    """

    __slots__ = ("mask", "y0", "y1", "foreign")

    def __init__(self, mask: Mask, y0: int, y1: int, foreign=None):
        self.mask = mask
        self.y0 = y0
        self.y1 = y1
        self.foreign = foreign or {}


class Schedule:
    """A manifest of parts, and the declarations a build made against it."""

    def __init__(self, items):
        self.items = list(items)
        self.by_name: dict[str, Item] = {}
        for item in self.items:
            if item.name in self.by_name:
                raise ValueError(f"{item.name!r} is in the schedule twice")
            self.by_name[item.name] = item
        for item in self.items:
            for other in item.near:
                if other == item.name:
                    raise ValueError(f"{item.name!r} cannot be near itself")
                if other not in self.by_name:
                    raise ValueError(
                        f"{item.name!r} is near {other!r}, which is not in the "
                        "schedule"
                    )
        self.width = 0
        self.length = 0
        self.built: dict[str, Declaration] = {}
        self.repeated: dict[str, Repeat] = {}

    # -- the build's side --------------------------------------------------

    def declare(self, name: str, mask: Mask, y0: float, y1: float) -> None:
        """Record that this part was built here.

        Called more than once for the same name, the claims merge -- a part
        placed in a loop over its courses is still one part.
        """
        if name not in self.by_name:
            raise KeyError(f"{name!r} is not in the schedule")
        if y1 <= y0:
            raise ValueError(f"{name!r} declared an empty y range {y0}..{y1}")
        if self.width and (mask.width, mask.length) != (self.width, self.length):
            raise ValueError(
                f"{name!r} declared a {mask.width}x{mask.length} mask; the rest "
                f"of the schedule is {self.width}x{self.length}"
            )
        self.width, self.length = mask.width, mask.length

        held = self.built.get(name)
        if held is None:
            self.built[name] = Declaration(mask.copy(), y0, y1)
        else:
            held.mask = held.mask | mask
            held.y0 = min(held.y0, y0)
            held.y1 = max(held.y1, y1)

    def declare_repeat(self, name: str, motif: Mask, y0: int, y1: int,
                       step: tuple[int, int], count: int) -> None:
        """Record that this part is one section stamped `count` times.

        Called beside `Canvas.stamp`, with the same four arguments, so that the
        thing declared and the thing built cannot drift apart -- and once per
        part: a second call replaces the first rather than merging, because two
        different repeats of one name is an authoring mistake and merging them
        would make it look like an answer.
        """
        if name not in self.by_name:
            raise KeyError(f"{name!r} is not in the schedule")
        item = self.by_name[name]
        if not item.copies:
            raise ValueError(
                f"{name!r} declares a repeat and the manifest says nothing "
                "about copies. Say how many the building has, with the "
                "photograph they were counted on: Item(..., copies=N).")
        if count != item.copies:
            raise ValueError(
                f"{name!r} was stamped {count} times and the manifest says "
                f"{item.copies}. One of the two is wrong, and the gate cannot "
                "tell which -- fix it here.")
        if count < 2:
            raise ValueError(f"{name!r} repeats {count} time(s); a repeat is two "
                             "or more")
        if not (step[0] or step[1]):
            raise ValueError(f"{name!r} repeats on a step of (0, 0)")
        if y1 <= y0:
            raise ValueError(f"{name!r} repeats over an empty y range {y0}..{y1}")
        if not motif.count():
            raise ValueError(f"{name!r} repeats an empty motif")
        self.repeated[name] = Repeat(motif.copy(), y0, y1, step, count)

    @property
    def undeclared(self) -> list[str]:
        """Manifest entries this build never claimed. Empty is the goal."""
        return [i.name for i in self.items if i.name not in self.built]

    @property
    def unrepeated(self) -> list[str]:
        """Items the manifest says are copies and the build never stamped."""
        return [i.name for i in self.items
                if i.copies and i.name not in self.repeated]

    @property
    def placed(self) -> list[Item]:
        """Parts whose position was argued for rather than measured.

        Empty is not the goal here, and that is the difference between this and
        `undeclared`. A building with nothing placed either has nothing that
        only a photograph shows, or it left all of that unbuilt -- and the
        second is where the corpus has been sitting.
        """
        return [i for i in self.items if i.placed]

    def extent(self, frame=None) -> tuple[float, float, float, float]:
        """The bounding box of every declaration.

        What the build actually occupies, as opposed to what the plan drew: the
        two differ exactly where the build put something the plan never had, and
        that difference is what `site_covered` grades. World cells if no frame
        is given -- the schedule does not carry one -- and plan (u, v) when it
        is, because that is the space the reference is recorded in. A world
        AABB compared to a plan AABB is a false red on any building that is
        not already axis-aligned.
        """
        cells = [i for d in self.built.values()
                 for i, v in enumerate(d.mask.bits) if v]
        if not cells:
            return (0.0, 0.0, 0.0, 0.0)
        width = self.width
        if frame is None:
            xs = [i % width for i in cells]
            zs = [i // width for i in cells]
            return (float(min(xs)), float(max(xs)), float(min(zs)), float(max(zs)))
        us: list[float] = []
        vs: list[float] = []
        for i in cells:
            u, v = frame.to_local(i % width + 0.5, i // width + 0.5)
            us.append(u)
            vs.append(v)
        return (min(us), max(us), min(vs), max(vs))

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({
            "width": self.width,
            "length": self.length,
            "items": [{"name": i.name, "what": i.what, "source": i.source,
                       "near": list(i.near), "reach": i.reach,
                       "blocks": list(i.blocks), "placed": i.placed,
                       "copies": i.copies}
                      for i in self.items],
            "built": {name: {"y0": d.y0, "y1": d.y1,
                             "cells": d.mask.count(), "mask": _pack(d.mask)}
                      for name, d in self.built.items()},
            "repeated": {name: {"y0": r.y0, "y1": r.y1, "step": list(r.step),
                                "count": r.count, "cells": r.motif.count(),
                                "motif": _pack(r.motif)}
                         for name, r in self.repeated.items()},
        }, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> "Schedule":
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
        out = cls(Item(i["name"], i["what"], i["source"],
                       tuple(i["near"]), i["reach"],
                       tuple(i.get("blocks", ())),
                       i.get("placed", ""),
                       i.get("copies", 0)) for i in doc["items"])
        out.width, out.length = doc["width"], doc["length"]
        for name, d in doc["built"].items():
            out.built[name] = Declaration(
                _unpack(d["mask"], out.width, out.length), d["y0"], d["y1"])
        for name, r in doc.get("repeated", {}).items():
            out.repeated[name] = Repeat(
                _unpack(r["motif"], out.width, out.length),
                r["y0"], r["y1"], tuple(r["step"]), r["count"])
        return out

    # -- the gate's side ---------------------------------------------------

    def standing(self, model) -> dict[str, Standing]:
        """What actually stands inside each declaration, read off the schematic.

        Columns are reduced to one integer of occupancy bits first, so a part
        costs one bitwise AND per cell instead of a walk up its whole y-range.
        Without that the parcel -- tens of thousands of cells over thirty
        courses -- would dominate the gate's runtime on its own.
        """
        if self.built and (model.width, model.length) != (self.width, self.length):
            raise ValueError(
                f"the schedule is {self.width}x{self.length}, the schematic is "
                f"{model.width}x{model.length}"
            )

        area = model.width * model.length
        air = {i for i, block in enumerate(model.palette) if block == AIR}

        def occupancy(wanted: tuple[str, ...]) -> list[int]:
            """One integer of set bits per cell: which layers hold material.

            `wanted` narrows it to the blocks a part is made of. One column per
            distinct filter and not one per part: a building declares a dozen
            parts out of two or three materials, and the walk is over the whole
            schematic.
            """
            # Both sides stripped of state, not just the one that was found.
            # `Item.blocks` promises that `minecraft:oak_fence` covers
            # `minecraft:oak_fence[north=true]`, and it did -- but a manifest
            # that named the state itself, as every building with
            # `jungle_leaves[persistent=true]` in it does, could never match
            # anything: the palette entry was stripped and the wanted name was
            # not, so the two never met. That line then reported nothing
            # standing under a part that was fully built, or -- before the
            # audit compared declared against found -- reported a handful of
            # cells and passed.
            names = {name.split("[", 1)[0] for name in wanted}
            keep = {i for i, block in enumerate(model.palette)
                    if i not in air
                    and (not names or block.split("[", 1)[0] in names)}
            column = [0] * area
            for y in range(model.height):
                base = y * area
                bit = 1 << y
                for i in range(area):
                    if model.blocks[base + i] in keep:
                        column[i] |= bit
            return column

        columns: dict[tuple[str, ...], list[int]] = {}
        for name in self.built:
            wanted = tuple(sorted(self.by_name[name].blocks))
            if wanted not in columns:
                columns[wanted] = occupancy(wanted)

        out: dict[str, Standing] = {}
        for name, d in self.built.items():
            item = self.by_name[name]
            column = columns[tuple(sorted(item.blocks))]
            lo = max(0, int(math.floor(d.y0)))
            hi = min(model.height, int(math.ceil(d.y1)))
            window = ((1 << hi) - 1) ^ ((1 << lo) - 1) if hi > lo else 0
            found = Mask(model.width, model.length)
            y0, y1 = model.height, -1
            for i, v in enumerate(d.mask.bits):
                if not v:
                    continue
                bits = column[i] & window
                if not bits:
                    continue
                found.bits[i] = 1
                y0 = min(y0, (bits & -bits).bit_length() - 1)
                y1 = max(y1, bits.bit_length() - 1)

            # What else is standing there. Only computed where the answer might
            # matter -- a part that filled its whole declaration out of the
            # blocks it named needs no explaining.
            foreign: dict[str, int] = {}
            if item.blocks and found.count() < d.mask.count():
                wanted = set(item.blocks)
                for i, v in enumerate(d.mask.bits):
                    if not v or found.bits[i]:
                        continue
                    for y in range(lo, hi):
                        value = model.blocks[y * area + i]
                        if value in air:
                            continue
                        block = model.palette[value].split("[", 1)[0]
                        if block not in wanted:
                            foreign[block] = foreign.get(block, 0) + 1
            out[name] = Standing(found, y0, y1, foreign)
        return out

    def audit(self, model):
        """(name, ok, detail) for every line of the schedule, in manifest order.

        Yields the gate's own verdict tuples rather than printing, so the
        reporting and the exit code stay in one place there.
        """
        found = self.standing(model)
        gaps: dict[str, list[float]] = {}

        for item in self.items:
            here = found.get(item.name)
            if here is None:
                yield (f"part {item.name}", False,
                       f"not built; nothing declares it ({item.what})")
                continue
            claim = self.built[item.name]
            made = (" of " + ", ".join(item.blocks)) if item.blocks else ""
            instead = ""
            if here.foreign:
                worst = sorted(here.foreign.items(), key=lambda kv: -kv[1])[:3]
                instead = ("; what stands in the rest is "
                           + ", ".join(f"{b} ({n})" for b, n in worst))

            if here.mask.count() == 0:
                yield (f"part {item.name}", False,
                       f"declared {claim.mask.count()} cells over y "
                       f"{claim.y0:g}..{claim.y1:g}, nothing{made} stands "
                       f"there{instead}")
                continue

            # Declared against found, both numbers, every run.
            #
            # A part that is mostly missing used to pass with one cheerful
            # number: a hotel declared its glazing over 297 cells and twenty-
            # seven courses, 158 cells of it stood in the bottom three, and the
            # audit printed "158 cells, y 1..3" and called it built. The tower
            # windows were cut in a different block from the one the manifest
            # named and were invisible to this check; the two blank facades
            # survived three rounds.
            #
            # The thresholds are blunt on purpose. A quarter of the cells and
            # half the courses is far below anything a rasteriser loses and far
            # above nothing, and a part that trips them is a part to look at
            # rather than a tolerance to widen.
            covered = (here.y1 - here.y0 + 1) / max(1.0, claim.y1 - claim.y0)
            share = here.mask.count() / max(1, claim.mask.count())
            detail = (f"{here.mask.count()} of {claim.mask.count()} cells, "
                      f"y {here.y0}..{here.y1} of {claim.y0:g}..{claim.y1:g}")
            if share < 0.25 or covered < 0.5:
                yield (f"part {item.name}", False,
                       detail + f" -- most of what was declared{made} is not "
                                f"there{instead}")
                continue
            yield (f"part {item.name}", True, detail + instead)

            for other in item.near:
                there = found.get(other)
                if there is None or there.mask.count() == 0:
                    yield (f"{item.name} meets {other}", False,
                           f"{other} was not built")
                    continue
                if other not in gaps:
                    gaps[other] = there.mask.distance_field(inside=False)

                # Copy by copy where the part is a repeat, and the *worst* of
                # them. A merged mask answers with its nearest cell, so a row of
                # eight villas where seven sit on the deck and the eighth hangs
                # five metres off the end of it reads as touching -- the seven
                # answer for the one. A translate is cheap to take apart again,
                # so there is no reason to leave that hole open.
                repeat = self.repeated.get(item.name)
                pieces = [(None, here.mask)]
                if repeat is not None:
                    apart = []
                    for n in range(repeat.count):
                        moved, _ = repeat.motif.shifted(repeat.step[0] * n,
                                                        repeat.step[1] * n)
                        piece = moved & here.mask
                        if piece.count():
                            apart.append((n, piece))
                    pieces = apart or pieces

                which, flat = None, -1.0
                for n, piece in pieces:
                    reach = math.sqrt(min(gaps[other][i]
                                          for i, v in enumerate(piece.bits) if v))
                    if reach > flat:
                        which, flat = n, reach

                # Vertically they only have to overlap within the same reach:
                # a bridge meets a tower it runs into at one level, not at all
                # of them.
                lift = max(here.y0 - there.y1, there.y0 - here.y1, 0)
                ok = flat <= item.reach and lift <= item.reach
                whose = "" if which is None else f"copy {which} is the furthest, "
                yield (f"{item.name} meets {other}", ok,
                       f"{whose}{flat:.1f} m apart, {lift} m of daylight in y; "
                       f"reach {item.reach:g} m")


__all__ = ["Declaration", "Item", "Repeat", "Schedule", "Standing", "REACH"]
