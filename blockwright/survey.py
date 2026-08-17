"""Measuring a building from whatever was supplied, as apparatus.

`buildings/<name>/probes/derive.py` used to be a thousand lines, of which about
seven hundred were the same in every building: work out what is in `input/`,
pick the branch that reads the plan, load the reference, autocorrelate the
storey rhythm, read the skyline, ask the held-out sources what they say, and
write `derived.json`. Copied per building, that is seven hundred lines to keep
in step by hand across every copy -- the failure `gate.py` names in its own
docstring, applied to the one file every number in a build comes out of.

So the apparatus is here and the *tables* are there. A building's `derive.py` is
now its declarations, its measuring windows, its part names, and whatever probes
only it needs; the reading of them is `Survey`.

    survey = Survey(paths, sys.modules[__name__])
    survey.main()                    # measure everything, write derived.json
    read = survey.plan_of()          # what build.py and gate.py call

`tables` is the building's own module, passed in whole rather than as thirty
arguments. Anything it does not define falls back to the default beside it in
`DEFAULTS` -- a building that never opens a void does not have to name
`SECTION_CELL` to be allowed to run.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from . import declared, flatmap, gate, lattice, measure, roof, sources, witnesses
from . import model as model3d
from .flatmap import guides
from .frame import Frame
from .mask import iou
from .mesh import Mesh
from .plan import Part, decompose
from . import vectorplan

# What a building gets if it does not say. Every one of these is a window --
# where to look -- or a search bound, and none of them is a result.
DEFAULTS = {
    "PLAN_ANGLE": 0.0,
    "DECLARED_PLAN": (),
    "DECLARED_HEIGHTS": {},
    "DECLARED_STOREY": {},
    "DECLARED": {},
    "BODY": (0.05, 0.60),
    "FACADE_INSET": 1.0,
    "STOREY_RANGE": (2.0, 6.0),
    # Where to look for the pier rhythm on a textured elevation. Wider than the
    # storey range because a bay is a composition rather than a constraint: a
    # residential slab repeats every three metres and a hotel every twelve, and
    # both are ordinary. The floor is above the rasterisation staircase so that
    # what comes back is a building and not the pixel grid.
    "BAY_RANGE": (2.5, 14.0),
    "STOREY_STEP": 0.25,
    "STOREY_CEILING": 26.0,
    "STOREY_SCORE": 0.15,
    "NOTCH_RANGE_HI": 25.0,
    "PROFILE_BIN": 0.5,
    "PROFILE_TRIM": 8.0,
    # How far a station may stand from a part's median before it is a second
    # level of building rather than the roof furniture on one. Three metres: a
    # parapet, a plant enclosure and a lift overrun are all under a storey, and
    # a storey is the smallest step that is another floor. See `skyline_of`.
    "PLATEAU": 3.0,
    "MESH_FLOOR": 6.0,
    "REGISTER_FLOOR": 6.0,
    "SECTION_CELL": 1.0,
    "STRIPS": (),
    "DISCS": (),
    "MODEL_PARTS": (),
    # Names for the parts the reference holds and the drawn plan does not --
    # see `Survey.assemble` -- and how much two footprints must overlap before
    # they are taken to be the same part.
    "CAPTURE_PARTS": (),
    "MATCH_FLOOR": 0.30,
    # What is too small or too low to be a part of the building rather than
    # a thing standing on it. See `Survey.assemble`.
    "PART_LEAST_AREA": 40.0,
    "PART_LEAST_HEIGHT": 2.5,
    "MODEL_UP": "y",
    "MODEL_SCALE": 1.0,
    "MAP_PALETTE": None,
    # How far the cheap datum may sit from the height the ground actually piles
    # up at before the capture is carrying geometry below its own grade. One
    # metre: a real site's pavement, car park and pool deck come out within that
    # of each other, so anything wider is not surface variation.
    "DATUM_AGREEMENT": 1.0,
    # Witness disagreements that are facts about the inputs rather than faults,
    # keyed by question, each with the reason. See `witnesses.declare`.
    # `plan overlap` is not a legal key: two silhouettes under the floor are
    # two buildings, and that choice is `WITNESS`, not a declared disagreement.
    "EXPECTED": {},
    # Why the reference is not a witness to this building, as a sentence.
    # None means it is. Set, the capture or the model is not evidence --
    # `sources.survey` does not report it -- so there is no reference to
    # cut a section against, and heights and the storey must be declared.
    # See `Survey.witnesses_of`.
    "WITNESS": None,
    # Metres per unit of an SVG plan. A GeoJSON in lon/lat needs none -- it is
    # projected -- and one already in metres needs none either; an SVG has no
    # units at all and cannot be read without this.
    "VECTOR_SCALE": 0.0,
    # Which lattice slope the frame is put onto: "auto" for the shortest period
    # the budget affords, or a pair like (3, 4) chosen by hand. See
    # `Survey.on_a_lattice` and `blockwright.lattice`.
    "LATTICE": "auto",
    # Why this building is not on a lattice, as a sentence. A building whose
    # shape is a curve or whose azimuth carries a measurement nothing may round
    # sets this and says so; the gate then prints the reason rather than a
    # verdict. A bare True would be the `UNIFORM` mistake again.
    "UNLATTICED": None,
    # How far a snap may move an end of the building, in metres. None takes
    # `lattice.budget_for`, which scales with the span and caps at the
    # section's own tolerance.
    "LATTICE_BUDGET": None,
    # The pitch this building repeats at, in metres, if it knows. Slopes whose
    # period cannot express it are passed over -- a period that does not divide
    # the pitch is a building that cannot copy its own sections.
    "LATTICE_PITCH": None,
}


class Tables:
    """A building's constants, with the defaults behind them.

    Reads attributes off the building's module and falls back to `DEFAULTS`, so
    that the apparatus can ask for anything and a building need only state what
    it actually decided.
    """

    def __init__(self, source):
        self.source = source

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        value = getattr(self.source, name, None)
        if value is None and name in DEFAULTS:
            return DEFAULTS[name]
        if value is None and name not in DEFAULTS:
            raise AttributeError(
                f"{name} is not defined in {getattr(self.source, '__name__', self.source)} "
                "and has no default")
        return value


def with_the_plan(read, frame):
    """A reference's frame, turned by however far the plan's frame was.

    **Both sides of a registration have to be measured along the same
    directions.** Turn the plan onto a lattice slope and leave the reference on
    its own fit, and the two stand half a degree apart -- which a registration
    cannot express, because it is scale and shift per axis. It comes out as a
    scale disagreement instead: on the fixture, u fitted at 0.994 and v at
    0.945, and the run stopped, saying the fit had latched onto something that
    was not the building. It had not. It had been handed two coordinate systems,
    which is the fault `Registration.measured` exists to end -- and the reason
    this is a function both the survey and the gate call rather than a line in
    either.

    By the *delta* and not onto the plan's own angle. Where the two really do
    stand at different azimuths -- a game map's invented street grid against a
    capture of the real prototype -- that difference is a fact about the inputs,
    declared in `EXPECTED`, and turning one frame onto the other would swallow
    it silently.

    What this does mean is that the section cannot see the snap: both sides move
    together, so a station cuts both in the same place. That is the right place
    for the blindness. What a snap turns is the drawing against the drawn plan,
    and the rows that compare a build with its plan -- `stands where the plan
    says`, `is built square` -- are the ones that can see a rotation, and do.
    The section grades heights, and heights are what it still grades.
    """
    slope = getattr(read, "slope", None)
    if slope is None or not slope.error:
        return frame
    return frame.turned(frame.angle + slope.error)


def _resat(frame, was, cells):
    """`frame` given the extent and the datum corner its predecessor had.

    `Frame.turned` keeps the extent it was handed, which was measured at the old
    angle: after a turn the minimum-area box is a little different, and the
    corner u = 0 sits somewhere else. Left alone, that shows up twice -- the
    registration compares extents with the reference, and every part's `u0`
    moves by a metre for no reason a reader could name.

    So the extent is re-read off the same cells at the new angle, and the origin
    is put back where it was relative to the mass: whatever gap the old frame
    left between u = 0 and the first cell, the new one leaves the same. That is
    the pad the fitting branch chose, and it belongs to the branch rather than
    to this.
    """
    def span(f):
        us, vs = [], []
        for x, z in cells:
            u, v = f.to_local(x + 0.5, z + 0.5)
            us.append(u)
            vs.append(v)
        return min(us), max(us), min(vs), max(vs)

    old_u0, _, old_v0, _ = span(was)
    u0, u1, v0, v1 = span(frame)
    pad_u, pad_v = -old_u0, -old_v0
    origin = frame.to_world(u0 - pad_u, v0 - pad_v)
    return Frame(origin, frame.angle,
                 (u1 - u0) + 2 * pad_u, (v1 - v0) + 2 * pad_v)


class Read:
    """What a plan branch produced, whichever branch it was.

    Every branch ends up with the same three things -- a frame, named parts, and
    the order to report them in -- so that nothing after this point has to ask
    where the plan came from. `massing` is set only on the model branch, where
    the same file also holds the heights. `provenance` is per part because an
    assembled plan mixes sources; a single branch fills it with its own kind.
    """

    __slots__ = ("source", "frame", "named", "order", "massing", "mass",
                 "slope", "unlatticed", "provenance", "drawn", "link", "site")

    def __init__(self, source, frame, named, order, mass=None, massing=None,
                 provenance=None):
        self.source = source
        self.frame = frame
        self.named = named
        self.order = order
        self.mass = mass
        self.massing = massing
        # Every cell the plan holds, which is `mass` until `Survey.assemble`
        # puts something beside it. The two are kept apart because they answer
        # different questions and both are asked: `mass` is what the drawn
        # source painted, and the one witness that compares this plan with a
        # held-out map has to compare like with like; `site` is what the build
        # stands on and what the gate clips its floor plates to, and a pool
        # house missing from that reads as a hundred per cent unbuilt.
        self.site = mass
        # Which of the parts the drawn source actually drew. Every branch draws
        # all of them; `Survey.assemble` appends the ones only the reference
        # holds, and leaves this alone, so that the three measurements taken
        # *against* the reference keep asking about the building the drawn
        # source drew. See `drawn_bounds`.
        self.drawn = tuple(order)
        # How to speak about the reference in this plan's coordinates, fitted
        # once in `plan_of` and carried rather than re-fitted. Two fits of one
        # pair is one fit too many -- `gate.Registration.measured` says why at
        # length -- and here it is also the difference between a part that
        # `skyline_of` finds material over and one it does not.
        self.link = None
        # The lattice slope the frame was put onto, if it was -- see
        # `Survey.on_a_lattice`. `unlatticed` carries the building's reason for
        # not being on one, which is a decision and therefore has to be a
        # sentence rather than a flag.
        self.slope = None
        self.unlatticed = None
        # Where each part's footprint came from, by name. One branch fills this
        # with its own kind for every part; the assembled plan (see
        # `plan_of`) fills it per part, which is the whole reason it exists.
        self.provenance = provenance or {}

    @property
    def parts(self) -> list:
        return [self.named[name] for name in self.order]

    def bounds(self) -> tuple[float, float, float, float]:
        parts = self.parts
        return (min(p.u0 for p in parts), max(p.u1 for p in parts),
                min(p.v0 for p in parts), max(p.v1 for p in parts))

    def drawn_parts(self) -> list:
        """The parts the drawn source stated, and no others.

        `parts` is every part of the plan, which after `Survey.assemble` is the
        whole site. Everything measured *against* the reference is about the
        building the drawn source drew -- the registration and the flip it is
        read with, the facade bands the storey rhythm is taken on, the size
        witness, the longest edge the notch pitch is read off -- because the
        reference answers those on material above `REGISTER_FLOOR` and a
        single-storey pool house across the lawn is not in that.

        One method rather than the same comprehension in five places: the two
        that were written separately are exactly the pair that drifted apart,
        with `link_to` taking `drawn_bounds()` for the extents and every part
        for the profile the flip is read off.
        """
        return [self.named[name] for name in self.drawn] or self.parts

    def drawn_bounds(self) -> tuple[float, float, float, float]:
        """The extent of `drawn_parts`.

        Putting an assembled part in the plan's half of the registration would
        stretch one axis by the width of the site and stop the run with a
        message about a clip that is not the problem.
        """
        parts = self.drawn_parts()
        return (min(p.u0 for p in parts), max(p.u1 for p in parts),
                min(p.v0 for p in parts), max(p.v1 for p in parts))


class Link:
    """How to speak about an OBJ in the plan's coordinates.

    A map and a capture are two independent fits of the same building, so their
    frames differ by a metre or two and everything that crosses between them has
    to be registered. A model that supplied the plan as well needs no such thing:
    there is one fit and a translation, and `registration` is None to say so.
    """

    __slots__ = ("mesh", "frame", "datum", "kind", "registration")

    def __init__(self, mesh, frame, datum, kind, registration=None):
        self.mesh = mesh
        self.frame = frame
        self.datum = datum
        self.kind = kind            # "model" or "capture"
        self.registration = registration

    def mu(self, u: float) -> float:
        return self.registration.to_mesh_u(u) if self.registration else u

    def mv(self, v: float) -> float:
        return self.registration.to_mesh_v(v) if self.registration else v

    def to_build_u(self, u: float) -> float:
        return self.registration.to_build_u(u) if self.registration else u

    def window(self, u0: float, u1: float) -> tuple[float, float]:
        """A stretch of the plan, in the reference's coordinates, in order.

        `mu(a), mu(b)` is the obvious way to write this and is wrong the moment
        the registration carries a flip: the pair comes back reversed, the
        window is empty, and whatever was being measured reads nothing at all.
        """
        a, b = self.mu(u0), self.mu(u1)
        return (a, b) if a <= b else (b, a)

    def across(self, v0: float, v1: float) -> tuple[float, float]:
        """The same, across the building."""
        a, b = self.mv(v0), self.mv(v1)
        return (a, b) if a <= b else (b, a)

    def to_build_v(self, v: float) -> float:
        return self.registration.to_build_v(v) if self.registration else v


def _texture_lines(what: str, found: dict, against: float | None) -> list[str]:
    """What the elevations' texture read, and whether they agreed with it.

    The agreement count is the load-bearing part. One facade's autocorrelation
    is a reading; three facades landing on the same figure is a measurement, and
    the difference between them is the difference between a number worth
    building to and a number worth printing.
    """
    sides = ", ".join(f"{r['side']} {r['value']:.2f}" for r in found["readings"])
    out = [f"  texture {what}: {found['value']:.2f} m, "
           f"{found['agreed']} of {found['of']} elevations agree "
           f"(r={found['score']:+.2f}) -- {sides}"]
    if found["doubled"]:
        out.append(f"  {'':{len(what)}}          "
                   f"{', '.join(found['doubled'])} read half of it -- the same "
                   "rhythm at twice the frequency, which on a storey is the "
                   "balcony rail between the floors and on a bay is the "
                   "mullion between the piers")
    if found["agreed"] < 2:
        out.append(f"  {'':{len(what)}}          one elevation only, so this is "
                   "a reading and not a measurement")
    # A minority that disagrees is a finding, not noise, and it has to be said
    # here because nothing downstream can say it. The gate grades a build
    # against a section, and a wing built to the wrong rhythm keeps the same
    # silhouette, the same skyline and the same section as one built right; the
    # only trace it leaves anywhere is these two numbers.
    for other in found.get("against") or ():
        out.append(f"  {'':{len(what)}}          {other['side']} disagrees at "
                   f"{other['value']:.2f} m (r={other['score']:+.2f}) -- if that "
                   "elevation is a different part of the building, this is two "
                   "rhythms and not one weak reading. Measure the parts "
                   "separately with `measure.rhythm_by_span` before averaging "
                   "them away.")
    if what == "storey" and measure.looks_like_tiling(found["value"]):
        out.append(f"  {'':{len(what)}}          and it lands on the "
                   f"{measure.TILE_SEAM} m tile seam, so the texture agrees "
                   "with the exporter rather than with the building. Count "
                   "rows on a photograph.")
    if against is not None and abs(against - found["value"]) > 0.5:
        out.append(f"  {'':{len(what)}}          the geometry says "
                   f"{against:.2f} m and the photograph says "
                   f"{found['value']:.2f}; they are the same capture, so one of "
                   "them is the exporter. Count rows on a photograph.")
    return out


CLIP_BOX_SAME = 1.0

# How much of a reference part has to land on the plan's grid before what
# landed is that part. The grid is the drawn source's own canvas -- a map crop,
# a vector plan's extent -- and a capture of a site regularly reaches past it.
#
# Half, and the reason is not the missing half: it is what the surviving half
# claims to be. A build redraws a part as a rectangle on its measured
# `u0..u1, v0..v1`, so a footprint cut down to a fifteen-cell sliver at the edge
# of the crop arrives as a fifteen-cell *building*, correctly named, with a
# provenance saying the capture measured it. Every row then grades that. The
# part is better refused and named, which is what `off_plan` is for.
ON_THE_PLAN = 0.5

# How far the two frames' bearings may stand apart before the canonical fits
# stop settling which way round they are. `gate.Registration` says it in its own
# docstring: `Frame.fit` points +u into the eastern half-plane, "which settles
# the ambiguity whenever the two bearings are close. It does not settle it when
# they are not." Two degrees is the bar the squareness row already prints its
# warning at, and it is what `Survey.assemble` leans on when a width profile is
# too flat for `orient` to read a flip off.
ORIENT_SQUARE = 2.0


def fits_clip_box(mesh, frame, within: float = CLIP_BOX_SAME) -> dict:
    """Whether the frame fitted to the reference is the building or the box.

    A clip is a rectangle cut out of a larger capture, and its ground plane runs
    all the way to the corners of that rectangle. `Mesh.frame` fits above
    `MESH_FLOOR` precisely so that plane is excluded -- but when the datum lands
    under the real ground, everything is above the floor, and what gets fitted
    is the cut itself. The frame then comes back the size of the clip.

    Nothing downstream can tell. The section reads both sides through that
    frame, so every station is consistent with every other and none of them is
    evidence; the registration prints plausible numbers; the whole run is
    self-consistent and about nothing. One building spent an hour re-clipping
    horizontally because the symptom -- `u 1.077 vs v 1.144` -- reads exactly
    like a clip that is too wide, and the fault was vertical.

    The test is a subtraction. A frame the same size as its clip to within a
    metre on both axes has measured the clip; a frame fitted to a building
    inside one is smaller, because a building is not a rectangle filling its
    own crop. Reported, never failed: what it says is "this run cannot tell you
    whether the frame is real", and the fix is a re-clip or a datum, neither of
    which the gate can make.
    """
    (x0, y0, z0), (x1, y1, z1) = mesh.bounds()
    clip = sorted((x1 - x0, z1 - z0))
    got = sorted((frame.extent_u, frame.extent_v))
    worst = max(abs(a - b) for a, b in zip(clip, got))
    return {
        "checked": True,
        "frame": [round(got[1], 1), round(got[0], 1)],
        "clip": [round(clip[1], 1), round(clip[0], 1)],
        "worst": round(worst, 2),
        "is_the_box": worst <= within,
    }


def mesh_clip_bounds(mesh) -> dict:
    """The mesh's reach in the converter's frame: east and north.

    The clip is axis-aligned here. Recording it in plan (u, v) and taking
    the AABB of the four corners inflates the box by |cos θ| + |sin θ| on
    each side -- tens of metres of empty corner on a rotated clip -- and a
    clubhouse sitting in that corner stays green. World space has no such
    AABB, and it is the frame `--clip-east` / `--clip-north` argue in.

    Registration's high-point extents are the wrong box for a different
    reason: they drop everything at or below REGISTER_FLOOR.
    """
    (x0, _y0, z0), (x1, _y1, z1) = mesh.bounds()
    # OBJ: x=east, z=south, so north = -z. Sorted so north0 < north1.
    north0, north1 = sorted((-z1, -z0))
    return {
        "east0": round(x0, 1),
        "east1": round(x1, 1),
        "north0": round(north0, 1),
        "north1": round(north1, 1),
    }


def levels_from(spacing: float, top: float, base: float = 0.0) -> list[float]:
    """Floor lines at a declared spacing, from the ground to the highest part."""
    if spacing <= 0:
        return [base]
    count = max(1, int((top - base) // spacing))
    return [round(base + i * spacing, 2) for i in range(count + 1)]


class Survey:
    """One building's measurements: the branch, the reference, and the numbers.

    Holds no state between runs -- `main` writes `derived.json` and that file is
    the state. What it holds is where the building's files are and what it has
    decided, which is exactly the pair that differs between buildings.
    """

    def __init__(self, paths, tables, probes=None):
        self.paths = paths
        self.t = tables if isinstance(tables, Tables) else Tables(tables)
        # A building's own windows into the reference, run after everything
        # generic has been measured and given the same `(out, read, link)` the
        # rest of this class works with.
        self.probes = probes

    def require(self, *wanted: Path) -> None:
        """Stop with the missing inputs named, rather than a traceback from deep
        inside a reader. Every one of these is something a person has to supply;
        none of them can be derived, defaulted, or usefully stubbed."""
        missing = [p for p in wanted if not p.exists()]
        if missing:
            raise SystemExit(
                "missing input:\n"
                + "".join(f"    {p}\n" for p in missing)
                + "see docs/data-contract.md for what each one is")

    def unconverted(self) -> None:
        """A capture that is still a folder of tiles is not yet a reference.

        Worth its own message: the survey reports "no capture" for a building whose
        whole `ge-export/` is sitting right there, and the difference between "you
        did not supply one" and "you did not convert it" is two commands.
        """
        if self.paths.MESH.exists() or not self.paths.GE_EXPORT.is_dir():
            return
        if not any(self.paths.GE_EXPORT.iterdir()):
            return
        raise SystemExit(
            f"{self.paths.GE_EXPORT} is here but {self.paths.MESH} is not: the capture has "
            "not been converted yet, so nothing can be measured off it.\n"
            f"    python tools/ge_convert.py {self.paths.GE_EXPORT} "
            f"-o {self.paths.MESH_FULL.parent}\n"
            f"    python tools/ge_convert.py {self.paths.GE_EXPORT} "
            f"-o {self.paths.MESH.parent} --clip-east <a> <b> --clip-north <c> <d>\n"
            "The first pass is the whole capture, which is what the clip box is "
            "measured off; the second cuts this building out of it. See the capture "
            "skill for how to read the box off the orthographic views.")

    def from_vector(self, out: dict, source) -> Read:
        """An outline that arrived as an outline.

        The one branch that needs no naming table: a FeatureCollection has one
        feature per part, and a feature that carries a name gives its part that
        name. Where it does not, they come back as part-1 and so on, the same as
        everywhere else.
        """
        layout = vectorplan.read(source.path, scale=self.t.VECTOR_SCALE)
        out["guides"] = []
        out["vector"] = {"shapes": len(layout.shapes),
                         "origin": [round(v, 1) for v in layout.origin]}
        for name in layout.order:
            part = layout.parts[name]
            if part.kind == "disc":
                out.setdefault("discs", {})[name] = {
                    "centre": [round(part.centre[0], 1), round(part.centre[1], 1)],
                    "radius": round(part.radius, 1),
                    "residual": round(part.residual, 2),
                }
        return Read(source, layout.frame, layout.parts, layout.order,
                    mass=layout.mass,
                    provenance={name: source.name for name in layout.order})

    def from_map(self, out: dict, source) -> Read:
        """A flat map, decomposed along the lines somebody drew inside it."""
        template, frame, parts = decompose(self.paths.LAYOUT, palette=self.t.MAP_PALETTE)
        strips = sorted((p for p in parts if p.kind == "strip"),
                        key=lambda p: p.v0 + p.v1)
        discs = sorted((p for p in parts if p.kind == "disc"),
                       key=lambda p: p.centre[0])

        # Names are optional and checked, in that order. An empty table means "call
        # them strip-1, disc-1 and get on with it", which is what makes the first
        # run on a new map work: nobody knows how many parts a map has until it has
        # been decomposed once, and a run that dies before printing them leaves the
        # reader with nothing to name them from.
        if not self.t.STRIPS and not self.t.DISCS:
            names = ([f"strip-{i + 1}" for i in range(len(strips))]
                     + [f"disc-{i + 1}" for i in range(len(discs))])
        elif len(strips) != len(self.t.STRIPS) or len(discs) != len(self.t.DISCS):
            raise SystemExit(
                f"{self.paths.LAYOUT.name} decomposed into {len(strips)} strip(s) and "
                f"{len(discs)} disc(s); self.t.STRIPS names {len(self.t.STRIPS)} and self.t.DISCS names "
                f"{len(self.t.DISCS)}.\n"
                + "".join(f"    {p!r}\n" for p in strips + discs)
                + "Name them all in v order (strips) and u order (discs), or empty "
                  "both tables to have them called strip-1, disc-1 and so on.")
        else:
            names = list(self.t.STRIPS) + list(self.t.DISCS)

        named = dict(zip(names, strips + discs))
        order = names

        for name in [n for n in order if named[n].kind == "disc"]:
            disc = named[name]
            out.setdefault("discs", {})[name] = {
                "centre": [round(disc.centre[0], 1), round(disc.centre[1], 1)],
                "radius": round(disc.radius, 1),
                "residual": round(disc.residual, 2),
            }

        # The drawn interior lines. `decompose` has already used them -- the parts
        # are what is left when they are subtracted -- so these are recorded as
        # corroboration and not as news, and to catch the case where the mapper drew
        # a division the decomposition then failed to close into a part.
        out["guides"] = [
            {"along": g.along,
             "u": [round(g.u0, 1), round(g.u1, 1)],
             "v": [round(g.v0, 1), round(g.v1, 1)],
             "mid_v": round((g.v0 + g.v1) / 2, 1),
             "cells": g.mask.count()}
            for g in guides(self.paths.LAYOUT, frame, palette=self.t.MAP_PALETTE)
        ]
        return Read(source, frame, named, order, mass=template,
                    provenance={name: source.name for name in order})

    def from_model(self, out: dict, source) -> Read:
        """A 3D model or a capture, split at its roof steps.

        The same reader for both, and `sources.py` is what remembers which one it
        was. A model is authoritative for shape and a capture is not, and nothing in
        the geometry says which arrived: that is a fact about where the file came
        from, not about what is in it.
        """
        massing = model3d.read(source.path, up=self.t.MODEL_UP, scale=self.t.MODEL_SCALE)
        names = list(self.t.MODEL_PARTS) or [f"part-{i + 1}"
                                      for i in range(len(massing.parts))]
        if len(names) != len(massing.parts):
            raise SystemExit(
                f"{source.path.name} split into {len(massing.parts)} parts and "
                f"self.t.MODEL_PARTS names {len(names)}. Either name them all or leave the "
                "table empty; a partial list would attach the wrong name to the "
                "wrong part, which is worse than no name at all.\n  "
                + "\n  ".join(massing.lines()))
        out["guides"] = []
        return Read(source, massing.frame, dict(zip(names, massing.parts)), names,
                    mass=massing.mass, massing=massing,
                    provenance={name: source.name for name in names})

    def from_declared(self, out: dict, source) -> Read:
        """A table somebody typed after reading the brief or an unscaled drawing."""
        if not self.t.DECLARED_PLAN:
            raise SystemExit(
                f"the plan has to come from {source.kind.label}, and self.t.DECLARED_PLAN "
                "in this file is empty.\n"
                "Read the brief and the drawings, and write the parts down as "
                "declared.Rect and declared.Round entries with the sentence or the "
                "sheet each one came from. That is not a formality: a declared plan "
                "is the only kind whose numbers cannot be re-derived later, so the "
                "source is the only thing that makes them checkable at all.")
        layout = declared.layout(self.t.DECLARED_PLAN, angle=self.t.PLAN_ANGLE)
        out["guides"] = []
        out["declared_plan"] = [
            {"name": s.name, "source": s.source} for s in layout.shapes]
        return Read(source, layout.frame, layout.parts, layout.order,
                    mass=layout.mass,
                    provenance={name: "declared" for name in layout.order})

    def plan_of(self, out: dict | None = None) -> Read:
        """The plan: what the drawn source states, and what only the reference
        holds.

        `build.py`, `gate.py` and `review.py` all call this rather than reading a
        plan of their own, because the two have to agree cell for cell. They used
        to each open the map, which worked until the day one of them was given a
        model instead and the other went on reading a map that was no longer the
        authority.

        The branch below still decides who states the *drawn* plan -- a vector
        beats a map beats a model beats a declaration, and that ranking is
        `sources.PREFER`. What changed is that its answer is no longer the whole
        plan: `assemble` adds the parts of the site the drawn source never drew.

        The order of the three steps below is the argument rather than a
        sequence. `on_a_lattice` settles the frame, because a frame that turned
        during the survey and not during the build is two buildings. `link_to`
        then fits the one registration everything downstream reads the reference
        through -- against the drawn plan, in that settled frame. Only then does
        `assemble` speak about parts that exist in the reference alone, in
        coordinates that already mean something. Fitting the link afterwards
        would fit it against a plan that already holds the reference's own
        contribution, and against material the register floor deliberately
        leaves out, so the two axes would come back scaled differently and the
        run would stop with a message about a clip that is not the problem.
        """
        out = {} if out is None else out
        evidence = sources.survey(self.paths, witness=self.t.WITNESS)
        by = evidence.answers("plan")
        if by is None:
            raise SystemExit(sources.refuse(evidence) or "nothing states the plan")
        if by.name == "vector":
            read = self.from_vector(out, by)
        elif by.name == "map":
            read = self.from_map(out, by)
        elif by.name in ("model", "capture"):
            read = self.from_model(out, by)
        else:
            read = self.from_declared(out, by)

        read = self.on_a_lattice(read)
        reference = evidence.reference
        if reference is None:
            out["mesh"] = {"read": False,
                           "why": "no model and no capture; the section is "
                                  "declared"}
        else:
            read.link = self.link_to(read, reference, out)
        return self.assemble(out, read, evidence)

    def assemble(self, out: dict, read: Read, evidence) -> Read:
        """The plan the drawn source states, plus what only the reference holds.

        Four branches used to be four answers, one of which won. That is right
        for the question "which source states the plan" and wrong for the site:
        a crop shows the building somebody cropped it around, and the clubhouse,
        the pool house and the row of villas beside it are on no crop at all.
        Built anyway, they arrived with no footprint, no height and no witness,
        and the report could not tell them from the tower.

        So the drawn source keeps every part it draws -- it is a drawing made to
        be measured, and cleaner than photogrammetry -- and the reference
        contributes the parts it does not. Overlap decides which is which, at
        `MATCH_FLOOR`, and an unmatched piece is named in the run's own output
        rather than quietly becoming a new building.

        An unmatched piece still has to be a part of the building. Decomposing
        a whole site brings the trees, the cars, the awnings and the street
        furniture along with it, so anything under `PART_LEAST_AREA` or
        `PART_LEAST_HEIGHT` is dropped -- and counted: how many pieces went,
        how much area in all, how tall the tallest one was. A threshold that
        quietly ate a wing reads exactly like one that quietly ate a hedge.

        **Both sides have to be in one coordinate system before an overlap means
        anything**, and the reference's is its own: `model3d.read` grids the OBJ
        from wherever the exporter left the origin. So every reference footprint
        is redrawn on the plan's grid through `read.link` -- the same
        registration `skyline_of`, `roof_of` and the gate's sections read the
        reference with. Fitting a second one here would put the parts this adds
        a metre or two from the material that is then read over them, and a part
        with nothing over it has no height at all.

        What that inherits is the fit's own answer about which way round the two
        frames stand, and **that answer has to have been decided on something**.
        A drawn plan whose width does not vary along its length -- two strips
        running the whole way, a plain slab, a square tower -- gives
        `Registration.orient` a flat profile on that axis, and a flip read off a
        flat profile is read off the rasterisation. It costs nothing while both
        ways round draw the same building, and it costs sixty metres the moment
        the plan grows a part the drawn source never drew: the part lands at the
        wrong end, and every row downstream agrees with it, because every row
        reads the reference through this same fit.

        `orient` now leaves such an axis alone and says so, which leaves the
        two canonical fits to settle it -- both frames point +u into the eastern
        half-plane, so two fits of one building at one bearing agree. That
        reasoning holds while the bearings agree and not otherwise, so this
        refuses rather than places a part when an axis is undetermined *and* the
        two frames stand more than `ORIENT_SQUARE` apart. Refuses, and does not
        place it marked: a marked part is still a real building put up sixty
        metres from where it stands, and every check would still agree with it.
        The thing a reader could act on is the stop.
        """
        reference = evidence.reference
        drawn = read.drawn_parts()
        floor = float(self.t.MATCH_FLOOR)
        record = {"drawn": len(drawn), "reference_parts": 0, "matched": 0,
                  "extra": 0, "floor": floor,
                  "dropped": {"count": 0, "area": 0.0, "tallest": 0.0}}
        out["assembly"] = record

        # Written whichever way it goes, and with the reason when it goes
        # nowhere. A run that assembled nothing and a run that never asked read
        # identically from the file otherwise, and they are not the same fact.
        if reference is None or read.link is None:
            record["why"] = ("no model and no capture, so nothing here holds a "
                             "part the plan does not draw")
            return read
        if reference.name == read.source.name:
            record["why"] = (f"the plan and the reference are the same "
                             f"{reference.name}, so every part it holds is "
                             "already drawn")
            return read

        # Which way round the two frames stand, and whether anything decided it.
        # `orient` names the axes its profiles were too flat to read; where the
        # bearings also disagree, nothing at all places a part and this stops.
        turn = (out.get("registration") or {}).get("orientation") or {}
        blind = turn.get("undetermined") or ""
        bearing = abs(read.frame.angle - read.link.frame.angle)
        record["orientation"] = {"margin": turn.get("margin"),
                                 "undetermined": blind,
                                 "shape": turn.get("shape"),
                                 "bearing": round(bearing, 2)}
        if blind and bearing > ORIENT_SQUARE:
            raise SystemExit(
                f"nothing states which way round the reference and the plan "
                f"stand on {' and '.join(blind)}.\n"
                f"    the plan's width profile differs from its own reverse by "
                f"{turn.get('shape')} m along u and across v, against a floor "
                f"of {turn.get('shape_floor')} m, so this building cannot be "
                f"told from itself end for end there\n"
                f"    and the two frames stand {bearing:.1f} deg apart, over "
                f"the {ORIENT_SQUARE:.1f} deg within which two canonical fits "
                f"of one building settle it between them\n"
                "So a part the reference holds and the plan does not could be "
                "placed at either end, and every row below would agree with "
                "whichever was picked. Draw the part on the plan and it is "
                "drawn rather than assembled; or lower REGISTER_FLOOR until "
                "the reference's own asymmetry is inside the fit, knowing that "
                "it enters the scale fit with it.")

        # Read the reference exactly the way `link_to` read it, which on this
        # branch is raw. `MODEL_UP` and `MODEL_SCALE` describe the file the
        # *plan* came from, and this is the other file: transforming it here
        # while the link reads it flat would put `extra.grid` and `link.frame`
        # in different spaces, and every footprint would map to nowhere --
        # reported as a canvas too small, about a canvas that is fine.
        #
        # At `model3d`'s own floor and not at `MESH_FLOOR`. The register floor
        # is chosen to keep a road, parked cars and a hedge out of a *fit*, and
        # it leaves a single-storey wing out with them; what is wanted here is
        # everything standing on the site.
        extra = model3d.read(reference.path)
        record["reference_parts"] = len(extra.parts)
        here = self.on_the_plan(extra, read.link, read)

        loose, gone = [], []
        for piece, top, (mask, off) in zip(extra.parts, extra.tops, here):
            here_cells = mask.count()
            # A piece the drawn source's canvas has eaten. Not only one that
            # missed it entirely: a piece reduced to a sliver at the edge of the
            # crop would be named, built and graded at the size of the sliver.
            # See `ON_THE_PLAN`.
            if here_cells < ON_THE_PLAN * (here_cells + off):
                gone.append((piece, top, here_cells, off))
                continue
            best = max((iou(mask, part.mask) for part in drawn), default=0.0)
            if best >= floor:
                record["matched"] += 1
            else:
                loose.append((mask, top, best, off))

        # Named rather than dropped. There is nowhere on this plan to put one,
        # and the canvas is what the schematic is cut to, so saying how much of
        # it the plan could not reach is the only thing left to do with it.
        if gone:
            record["off_plan"] = [
                {"cells": p.mask.count(), "top": round(t, 1),
                 "on_the_plan": on, "past": off} for p, t, on, off in gone]

        # The height is the one this already recorded -- the same number
        # that later lands in `assembly.added[].top` -- and the area is the
        # part's cells on the plan. One cell is one metre.
        kept, dropped = [], []
        for item in loose:
            mask, top, _, _ = item
            area = mask.count()
            if area < self.t.PART_LEAST_AREA or top < self.t.PART_LEAST_HEIGHT:
                dropped.append((area, top))
            else:
                kept.append(item)
        loose = kept
        record["dropped"] = {
            "count": len(dropped),
            "area": round(sum(a for a, _ in dropped), 1),
            "tallest": round(max((t for _, t in dropped), default=0.0), 1),
        }
        record["extra"] = len(loose)
        if not loose:
            return read

        names = list(self.t.CAPTURE_PARTS) or [
            f"capture-{i + 1}" for i in range(len(loose))]
        if len(names) != len(loose):
            raise SystemExit(
                f"the reference holds {len(loose)} part(s) the plan does not "
                f"draw, and CAPTURE_PARTS names {len(names)}.\n"
                + "".join(f"    {m.count()} cells, {t:.1f} m tall, best overlap "
                          f"with anything drawn {b:.2f}\n"
                          for m, t, b, _ in loose)
                + "Name them all in the order printed, largest first, or empty "
                  "the table to have them called capture-1 and so on. A partial "
                  "list would put the wrong name on the wrong part.")
        taken = [name for name in names if name in read.named]
        if taken:
            raise SystemExit(
                f"CAPTURE_PARTS names {', '.join(taken)}, which the plan "
                "already draws. A part the reference holds and the plan does "
                "not is a different part, and naming it after a drawn one would "
                "replace a drawn footprint with a photogrammetric one without "
                "saying so.")

        named = dict(read.named)
        order = list(read.order)
        provenance = dict(read.provenance)
        site = read.site
        added = []
        for name, (mask, top, best, off) in zip(names, loose):
            named[name] = Part(mask, read.frame)
            order.append(name)
            provenance[name] = reference.name
            site = mask if site is None else site | mask
            added.append({"name": name, "cells": mask.count(),
                          "top": round(top, 1), "overlap": round(best, 2),
                          "clipped": off})
        record["added"] = added

        joined = Read(read.source, read.frame, named, order,
                      mass=read.mass, massing=read.massing,
                      provenance=provenance)
        joined.slope, joined.unlatticed = read.slope, read.unlatticed
        joined.drawn, joined.link = read.drawn, read.link
        # The drawn mass is left exactly as the branch measured it and the new
        # parts go into `site` instead -- see `Read`. A union and not a closing:
        # the fifteen metres between a block and its pool house are a fact about
        # the site, and any closing wide enough to bridge a court would swallow
        # them without a word.
        joined.site = site
        return joined

    @staticmethod
    def on_the_plan(extra, link: Link, read: Read) -> list[tuple]:
        """Every footprint the reference holds, redrawn on the plan's own grid.

        Sampled backwards -- for each cell of the plan, what does the reference
        have there -- rather than splatted forwards. `Mask.mirrored` gives the
        argument in full: a change of coordinates is an isometry of the plane and
        not of the cell lattice, so a forward splat leaves pinholes wherever the
        two grids fall out of step. On the fixture that is a seventh of the
        footprint, scattered through it in a pattern nobody would read as
        anything but noise.

        The scan is bounded by where the reference actually lands, which is what
        the forward pass is for. That pass also counts the cells that land
        nowhere: the plan's grid is the drawn source's own canvas, and a capture
        of a site regularly reaches past a crop made of one building on it. A
        footprint handed back with its far half missing and nothing said is
        worse than one that is said to be short.
        """
        like = (read.mass if read.mass is not None
                else read.named[read.order[0]].mask)
        width, length = like.width, like.length

        def to_plan(mx: float, mz: float) -> tuple[int, int]:
            u, v = link.frame.to_local(mx, mz)
            x, z = read.frame.to_world(link.to_build_u(u), link.to_build_v(v))
            return int(math.floor(x)), int(math.floor(z))

        off = [0] * len(extra.parts)
        reach = None
        for i, piece in enumerate(extra.parts):
            for cell in piece.mask.cells():
                x, z = to_plan(*extra.grid.to_model(*cell))
                if not (0 <= x < width and 0 <= z < length):
                    off[i] += 1
                    continue
                reach = ((min(reach[0], x), max(reach[1], x),
                          min(reach[2], z), max(reach[3], z))
                         if reach else (x, x, z, z))

        out = [like.empty_like() for _ in extra.parts]
        if reach is None:
            return list(zip(out, off))

        owner: dict[tuple[int, int], int] = {}
        for i, piece in enumerate(extra.parts):
            for cell in piece.mask.cells():
                owner[cell] = i

        x0, x1, z0, z1 = reach
        for z in range(z0, z1 + 1):
            for x in range(x0, x1 + 1):
                u, v = read.frame.to_local(x + 0.5, z + 0.5)
                mx, mz = link.frame.to_world(link.mu(u), link.mv(v))
                i = owner.get(extra.grid.to_cell(mx, mz))
                if i is not None:
                    out[i].bits[z * width + x] = 1
        return list(zip(out, off))

    def on_a_lattice(self, read: Read) -> Read:
        """The same plan, drawn on a slope the block grid can repeat on.

        One place for all four branches, and it has to be here rather than in
        each of them: `build.py` and `gate.py` call `plan_of` too, and a frame
        that turned during the survey and not during the build is two buildings.

        **What turns is the drawing board, not the drawing.** The parts keep the
        masks the map drew; they are re-measured in the new frame because their
        `u0..u1` are readings *through* it. Everything authored in `(u, v)` --
        every `rect`, every `disc`, every bay -- lands on the new slope, and
        that is the whole effect: an authored wall becomes a staircase whose run
        lengths repeat, and a section repeated at a whole number of periods
        becomes a translation of the first rather than a second rasterisation of
        the same shape. `tools/lattice_selftest.py` asserts both, and asserts
        that both stop being true off the lattice.

        The price is an angle. It is measured here, in metres at the ends of the
        building, and it is carried into `derived.json` so that no row downstream
        has to guess whether a disagreement is the building or the snap. Where
        the price is over budget the slope still comes back -- marked -- because
        the section is the thing entitled to grade it, and refusing here would
        move the decision somewhere nothing measures it.
        """
        why = self.t.UNLATTICED
        if why:
            if why is True:
                raise SystemExit(
                    "UNLATTICED is a reason, not a flag: say what about this "
                    "building makes a lattice slope wrong -- a shoreline that "
                    "really does wander, an azimuth that carries a measurement "
                    "-- so the gate can print it.")
            read.unlatticed = str(why)
            return read

        cells = (read.mass.cells() if read.mass is not None
                 else [c for p in read.parts for c in p.mask.cells()])
        if not cells:
            read.unlatticed = "the plan has no cells to fit a slope to"
            return read

        span = max(read.frame.extent_u, read.frame.extent_v) or 1.0
        setting = self.t.LATTICE
        if isinstance(setting, (tuple, list)):
            slope = lattice.Slope(setting[0], setting[1],
                                  measured=read.frame.angle, span=span)
            if slope.runs > lattice.RUNS:
                raise SystemExit(
                    f"LATTICE = {tuple(setting)} has a motif of {slope.runs} "
                    f"runs ({'-'.join(str(n) for n in slope.motif)}), and a "
                    f"motif over {lattice.RUNS} reads as noise rather than as "
                    "rhythm. Pick a coarser slope, or say why in UNLATTICED.\n"
                    + lattice.table(read.frame.angle, span=span))
        elif setting == "auto":
            slope = lattice.choose(read.frame.angle, span,
                                   budget=self.t.LATTICE_BUDGET,
                                   pitch=self.t.LATTICE_PITCH)
        else:
            raise SystemExit(
                f"LATTICE is {setting!r}; it is \"auto\" or a pair like (3, 4). "
                "A building that should not be on a lattice sets UNLATTICED "
                "with its reason instead.")

        read.slope = slope
        if slope.step == (1, 0) and abs(slope.error) < 1e-9:
            return read              # already square to the world; nothing to turn

        frame = _resat(read.frame.turned(slope.angle), read.frame, cells)
        out = Read(read.source, frame,
                   {name: Part(part.mask, frame)
                    for name, part in read.named.items()},
                   read.order, mass=read.mass, massing=read.massing,
                   provenance=read.provenance)
        out.slope = slope
        out.drawn, out.link, out.site = read.drawn, read.link, read.site
        return out

    def datum_of(self, mesh, path, note: dict) -> float:
        """Where the ground is, having first checked that it is the ground.

        `Mesh.ground` takes a low quantile of the height, which assumes the
        lowest geometry in a capture is its own grade. Photogrammetry regularly
        hangs skirts of unclosed polygons below the surface it could not seal --
        seven per cent of the vertices on one export -- and then the quantile
        lands metres underground.

        Everything after that is quietly wrong and internally consistent. Every
        threshold is measured off the datum, so "material above six metres" is
        the whole clip box including its ground plane; the registration fits the
        box rather than the building, and reports plausible numbers while doing
        it. Two of the six buildings built with this pipeline lost the better
        part of a day to it, and both times the search went sideways -- re-clip
        tighter, raise the floor -- because the symptom points along the axis
        the fault is not on.

        The check is one comparison: the height at which vertices actually pile
        up, against the quantile. On a real site the pavement, the car park, the
        pool deck and the road agree to within a metre; a disagreement wider
        than that is not surface variation.
        """
        cheap, real, gap = mesh.datum()
        note["datum"] = {"quantile": round(cheap, 2),
                         "ground_band": round(real, 2),
                         "gap": round(gap, 2),
                         "agrees": gap <= self.t.DATUM_AGREEMENT}
        if gap <= self.t.DATUM_AGREEMENT:
            return cheap

        raise SystemExit(
            f"the datum of {Path(path).name} is not the ground.\n"
            f"    a low quantile of the height says {cheap:.2f} m\n"
            f"    vertices actually pile up at {real:.2f} m\n"
            f"    they differ by {gap:.2f} m\n"
            "This capture carries geometry below its own grade -- skirts "
            "of unclosed polygons, which photogrammetry hangs under any "
            "surface it could not seal. Every threshold below is measured "
            "from the datum, so leaving it there puts 'above the ground' "
            "inside the ground, and fits the registration to the clip box "
            "rather than to the building.\n"
            "Do not clip the floor off the capture: that deletes every low "
            "building on the site and deletes it silently. If the terrain "
            "skirt is the problem, raise MESH_FLOOR in the building's "
            "probe -- that drops terrain from the measurement and keeps "
            "it in the file, so the loss is visible. If this capture "
            "really does have that much below grade -- a basement the "
            "export modelled, a site that steps down -- raise "
            "DATUM_AGREEMENT and say why.")

    def link_to(self, read: Read, reference, out: dict, record: bool = True) -> Link:
        """Load the reference OBJ and work out how to address it.

        Two cases, and the difference is whether the plan came out of this same
        file. If it did, the plan's frame shifted into model coordinates addresses
        the mesh exactly. If it did not, the two are independent fits and have to be
        registered -- and a registration whose axes disagree is a fit that has
        latched onto something that is not this building, which stops the run.

        `record` off is for the second reference, when a project supplied both a
        model and a capture: it is loaded to be asked the same questions, not to
        take over the file's account of what the section was cut against.
        """
        note = out if record else {}
        if read.massing is not None and reference.name == read.source.name:
            mesh = model3d.load(reference.path, up=self.t.MODEL_UP, scale=self.t.MODEL_SCALE)
            datum = read.massing.datum
            model_frame = with_the_plan(read, read.massing.model_frame)
            note["mesh"] = {
                "read": True,
                "kind": reference.name,
                "vertices": len(mesh),
                "datum": round(datum, 2),
                "top": round(max(mesh.y) - datum, 1),
                "frame": {"origin": [round(v, 2) for v in model_frame.origin],
                          "angle": round(model_frame.angle, 2),
                          "extent": [round(read.frame.extent_u, 1),
                                     round(read.frame.extent_v, 1)]},
                "bounds": mesh_clip_bounds(mesh),
            }
            note["registration"] = {"needed": False,
                                   "why": "the plan and the section come from the "
                                          "same file, so they share one fit"}
            return Link(mesh, model_frame, datum, reference.name)

        mesh = Mesh.read(reference.path)
        datum = self.datum_of(mesh, reference.path, note)
        mesh_frame = with_the_plan(
            read, mesh.frame(datum=datum, floor=self.t.MESH_FLOOR))
        cloud = gate.Cloud.from_mesh(mesh, mesh_frame, datum=datum)

        # Both sides of this registration have to describe the same thing, and the
        # plan describes the *whole* building -- every part, however low. So the
        # mesh is taken from a fixed height above the ground rather than from a
        # fraction of its own top: a fraction throws away every part shorter than
        # half the tallest one, and then a podium-and-tower registers its podium
        # against nothing and reports scales that disagree by tens of per cent. The
        # run then stops with a message about re-clipping, and re-clipping does not
        # help, because the clip was never the problem.
        #
        # The floor still has to be above the ground: at grade the capture holds
        # road, planting, parked cars and the neighbours. `self.t.REGISTER_FLOOR` is that
        # height in metres, and it is a height and not a fraction on purpose.
        #
        # The drawn extent, not the whole plan's. `Survey.assemble` puts the
        # parts only the reference holds into the plan, and those are exactly
        # the ones this floor was chosen to leave out of the mesh's half -- a
        # pool house across the lawn is under it. Fitting the two against each
        # other would stretch one axis by the width of the site.
        u0, u1, v0, v1 = read.drawn_bounds()
        high = cloud.above(self.t.REGISTER_FLOOR)
        if not high:
            raise SystemExit(
                f"the reference has nothing above {self.t.REGISTER_FLOOR:.1f} m. Either "
                "the datum is wrong or this clip is not of a building.")
        reg = gate.Registration(
            gate.extent([p[0] for p in high]), gate.extent([p[1] for p in high]),
            (u0, u1), (v0, v1), self.t.REGISTER_FLOOR)

        # Which way round the two frames stand. An extent is the same end for
        # end, so the fit above cannot tell -- and when the plan comes off a map
        # that stands its building on an invented street grid, the two
        # canonical fits can land half a turn apart. Everything then registers
        # perfectly and every window is measured at the opposite end of the
        # building. Read off the plan's own asymmetry, on every run.
        #
        # The drawn parts and not every part, for the same reason the extents
        # three lines up are `drawn_bounds()`. Harmless on the first call, where
        # nothing has been assembled yet; not harmless on the second, which
        # `witnesses_of` makes against the already-assembled plan to ask a
        # second OBJ the same questions. There the assembled parts lie outside
        # `build_u`, `_profile` clamps everything out of span into the end bins,
        # and the flip would be decided on a profile with two invented spikes at
        # its ends. A wrong flip puts every `part heights agree` reading at the
        # other end of the building.
        drawn = gate.Plan.from_parts(read.drawn_parts(), read.frame)
        flip_u, flip_v, scores = reg.orient(high, drawn)
        reg.flip_u, reg.flip_v = flip_u, flip_v

        # Which way round is settled; whether the two frames are turned the
        # *same* way is a different question, and until now nothing asked it.
        # The registration does not rotate -- it does not have to, because each
        # frame was fitted to its own source -- and that reasoning holds only
        # while the two fits agree about which way the building points. When
        # they disagree by a couple of degrees the extents stretch to cover a
        # footprint lying across them, both scales stay plausible, and the
        # section reports the error as noise on every station at once.
        square = reg.square(high, drawn)

        note["mesh"] = {
            "read": True,
            "kind": reference.name,
            "vertices": len(mesh),
            "datum": round(datum, 2),
            "top": round(cloud.top(), 1),
            "frame": {"origin": [round(v, 2) for v in mesh_frame.origin],
                      "angle": round(mesh_frame.angle, 2),
                      "extent": [round(mesh_frame.extent_u, 1),
                                 round(mesh_frame.extent_v, 1)],
                      "clip_box": fits_clip_box(mesh, mesh_frame)},
            "bounds": mesh_clip_bounds(mesh),
        }
        note["registration"] = {
            "needed": True,
            "floor": self.t.REGISTER_FLOOR,
            "u": {"mesh": [round(v, 1) for v in reg.mesh_u],
                  "plan": [round(v, 1) for v in reg.build_u],
                  "scale": round(reg.u_scale, 3)},
            "v": {"mesh": [round(v, 1) for v in reg.mesh_v],
                  "plan": [round(v, 1) for v in reg.build_v],
                  "scale": round(reg.v_scale, 3)},
            "disagreement": round(reg.disagreement, 3),
            "agrees": reg.agrees(),
            "turned": reg.turned,
            # The flips as booleans and not only as the sentence `turned`
            # renders them. A gate that wants to grade through *this* fit rather
            # than re-fitting its own has to rebuild the Registration, and
            # parsing "mirrored across v" back into a pair of flags is the kind
            # of thing that works until somebody improves the wording.
            "flip": [reg.flip_u, reg.flip_v],
            "orientation": scores,
            "square": square,
        }
        note["registration"]["graded"] = True
        if not reg.agrees():
            # The same escape the witness rows have, and for the same argument.
            # Two axes fitting differently is usually a fit that has latched
            # onto something that is not this building, and then stopping is
            # right. It is not always that: a crop from a game map is a drawing
            # of the building, and a mapper who keeps the street frontage and
            # squeezes the depth produces a plan whose aspect is a few per cent
            # off the real one forever. No clip and no floor will change it.
            #
            # Naming that here makes the fit usable and the row ungraded, with
            # the reason carried into the report. It is not a widened tolerance:
            # widening would excuse every other disagreement on this axis too,
            # including the ones that are faults, and would do it silently. The
            # affine is still fitted from the data and still says where a
            # station is; what it stops claiming is that the two sources are of
            # the same size.
            why = dict(self.t.EXPECTED).get("registration")
            if not why:
                raise SystemExit(
                    "the reference and the plan do not register: " + reg.detail + ".\n"
                    "One axis fitting several per cent differently from the other means "
                    "the fit has latched onto something that is not this building -- a "
                    "neighbour inside the clip, a belt of trees, a datum from the wrong "
                    "capture. Every number below it would be measured in the wrong "
                    f"place, so nothing was written to {self.paths.DERIVED.name}.\n"
                    f"Raise REGISTER_FLOOR and MESH_FLOOR above the accessory "
                    f"volumes and below the lowest part of the drawn building "
                    f"-- they are at {self.t.REGISTER_FLOOR:.1f} m and "
                    f"{self.t.MESH_FLOOR:.1f} m. Under a clip of the site this "
                    "is the ordinary answer rather than the last one: the clip "
                    "holds the villas, the pool house and the palms on purpose, "
                    "the fit takes every vertex above the floor, and the plan it "
                    "is fitted against is the drawn building alone. The floor is "
                    "what states which of the volumes on this parcel that "
                    "building is. It bounds the fit and not the reading -- every "
                    "height is still read over every part, so nothing below the "
                    "new floor stops being measured; it stops being registered.\n"
                    "Or re-clip the capture tighter, which throws the rest of the "
                    "site away and is the decision the site clip exists to undo.\n"
                    "If the two really are of differently proportioned drawings of "
                    "one building -- a game map against a capture of the real "
                    "prototype -- say so in EXPECTED['registration'] with the "
                    "reason, and the run continues with the row ungraded.")
            note["registration"]["graded"] = False
            note["registration"]["expected"] = why
        return Link(mesh, mesh_frame, datum, reference.name, reg)

    def from_texture(self, out: dict) -> dict:
        """Two rhythms read off the orthographic elevations' own texture.

        The *geometry* of a capture cannot answer either of them. The exporter
        lays a seam of vertices every 4.5 m on every facade of every building,
        and autocorrelation reports that seam as a storey with a confidence no
        real facade reaches; nothing in the geometry knows where a pier is at
        all. The texture of the same capture is a photograph of the wall, and
        window heads, sill courses and piers are in it where they actually are.

        Read four elevations, not one, and report **how many of them agreed**.
        A single facade's autocorrelation on a balconied elevation comes back
        with the slab-and-rail pair rather than the storey -- half the real
        number, at a score that looks respectable. Two independent facades
        landing on the same figure is evidence; one facade landing on a figure
        is a reading.

        Nothing here decides anything. Both rhythms travel as candidates, with
        their agreement, for the build to take or for a person to reject with a
        reason -- and the piers in particular are a number that has been chosen
        by hand on every building so far while being perfectly measurable.
        """
        orthos = getattr(self.paths, "ORTHOS", None)
        meta = Path(orthos) / "render_meta.json" if orthos else None
        if meta is None or not meta.exists():
            return {}
        views = json.loads(meta.read_text(encoding="utf-8")).get("views", {})

        storeys: list[dict] = []
        bays: list[dict] = []
        for side in ("north", "south", "east", "west"):
            view = views.get(side) or {}
            picture = Path(orthos) / f"{side}_tex.png"
            scale = view.get("metres_per_pixel")
            if not scale or not picture.exists():
                continue
            try:
                from PIL import Image

                image = Image.open(picture)
                down = measure.period(measure.row_signal(image), scale,
                                      *self.t.STOREY_RANGE)
                across = measure.period(measure.column_signal(image), scale,
                                        *self.t.BAY_RANGE)
            except Exception:                              # noqa: BLE001
                continue
            # Not filtered against the tile seam, unlike the geometry. The
            # seam is a band of *vertices*; this is the photograph wrapped
            # around them, and dropping a reading for landing near 4.5 m would
            # throw away the one measurement that can tell a real 4.5 m storey
            # from the exporter's. If the answer comes back on the seam anyway,
            # `_texture_lines` says so and leaves the judgement outside.
            if down.value > 0:
                storeys.append({"side": side, "value": round(down.value, 2),
                                "score": round(down.score, 3)})
            if across.value > 0:
                bays.append({"side": side, "value": round(across.value, 2),
                             "score": round(across.score, 3)})

        found = {}
        for name, readings in (("storey", storeys), ("bay", bays)):
            agreed = self._agreed(readings)
            if agreed:
                found[name] = agreed
        return found

    @staticmethod
    def _agreed(readings: list[dict], tolerance: float = 0.12) -> dict | None:
        """The largest group of facades that read the same rhythm.

        Two readings agree within `tolerance` of the larger, and a reading also
        agrees with twice itself: autocorrelation on a facade with a band
        halfway up each storey returns the half, and a half that matches
        somebody else's whole is the same fact seen at a different multiple, not
        a disagreement. The doubled reading is what is reported, because the
        whole is the storey and the half is the balcony.
        """
        if not readings:
            return None

        def near(a: float, b: float) -> bool:
            return abs(a - b) <= tolerance * max(a, b)

        best = None
        for anchor in readings:
            value = anchor["value"]
            with_it = []
            for other in readings:
                if near(other["value"], value):
                    with_it.append((other, other["value"]))
                elif near(other["value"] * 2, value):
                    with_it.append((other, other["value"] * 2))
            if best is None or len(with_it) > len(best[1]) or (
                    len(with_it) == len(best[1])
                    and anchor["score"] > best[0]["score"]):
                best = (anchor, with_it)

        anchor, group = best
        weight = sum(max(r["score"], 0.0) for r, _ in group)
        if weight <= 0:
            value = sum(v for _, v in group) / len(group)
        else:
            value = sum(max(r["score"], 0.0) * v
                        for r, v in group) / weight
        # Which sides were outvoted, by name and with their figures. The count
        # alone was here from the start and the names were not, and the gap
        # between those two is a building that shipped wrong: a run recorded
        # "bay 11.00 m, 2 of 4 elevations agree", the two that disagreed read
        # 8.48 and 14.50, and they were the wing with no balconies on it. Two
        # out of four is not a weak measurement of one rhythm -- it is a firm
        # measurement of two, and only the names say which is which.
        inside = {id(r) for r, _ in group}
        against = [r for r in readings if id(r) not in inside]
        return {
            "value": round(value, 2),
            "agreed": len(group),
            "of": len(readings),
            "score": round(max(r["score"] for r, _ in group), 3),
            "readings": readings,
            "against": [{"side": r["side"], "value": r["value"],
                         "score": r["score"]} for r in against],
            "doubled": sorted(r["side"] for r, v in group
                              if abs(v - r["value"]) > 1e-9),
        }

    def storeys_of(self, out: dict, read: Read, link: Link | None) -> None:
        """The storey rhythm: measured off the reference, or declared.

        Measured first wherever there is anything to measure. A declaration that
        overrides a real measurement is how a building ends up with the floor heights
        somebody expected rather than the ones it has -- so the declaration is the
        fallback, the measurement is reported either way, and the file says which one
        the build will use.
        """
        # The drawn building, not the whole assembled site. The bands below are
        # its two long facades, and a pool house fifteen metres clear of it
        # would put one of them on the pool house's back wall -- where the
        # rhythm read would be a real rhythm, of the wrong building.
        parts = read.drawn_parts()
        u0, u1, v0, v1 = read.drawn_bounds()
        body = (u0 + self.t.BODY[0] * (u1 - u0), u0 + self.t.BODY[1] * (u1 - u0))

        measured = None
        if link is not None:
            # The two long facades, in v, and which parts carry them.
            #
            # This used to take the outermost parts in v and read the inner face
            # of each -- which assumes the building's parts are stacked across
            # v. On a building whose parts are stacked along u instead, both
            # bands landed within a metre of each other at one end and the
            # rhythm was measured on the end wall. It found one, with a
            # respectable correlation, off a facade eight metres wide.
            #
            # So the bands are the two long faces of the whole drawn building,
            # inset from its own extent, and they do not depend on how the plan
            # happens to divide. `FACADE_INSET` is how thick a band to read.
            reach = self.t.FACADE_INSET
            edges = ((v0 - reach, v0 + reach), (v1 - reach, v1 + reach))
            if v1 - v0 < 4 * reach and parts:
                # A building narrower than the two bands are thick: the bands
                # would overlap and both would read the same wall. Fall back to
                # the parts' own division, which is what a stack of narrow
                # strips has instead of two long faces.
                strips = sorted(parts, key=lambda p: p.v0 + p.v1)
                edges = ((strips[0].v0 - reach, strips[0].v0 + reach),
                         (strips[-1].v1 - reach, strips[-1].v1 + reach))
            found = measure.storey_height(
                link.mesh, link.frame,
                tuple(link.across(a, b) for a, b in edges),
                *link.window(body[0], body[1]), datum=link.datum,
                step=self.t.STOREY_STEP, ceiling=self.t.STOREY_CEILING,
                lo=self.t.STOREY_RANGE[0], hi=self.t.STOREY_RANGE[1])
            measured = {
                "by": link.kind,
                "spacing": round(found.spacing, 2),
                "score": round(found.score, 3),
                "found": found.score >= self.t.STOREY_SCORE,
                "levels": [round(v, 2) for v in found.levels],
                "bands": [[round(a, 1), round(b, 1)] for a, b in edges],
                "window": [round(body[0], 1), round(body[1], 1)],
            }

        # A spacing that is the exporter's tile seam is not a storey, however
        # confidently the autocorrelation reports it. Three buildings were told
        # 4.5 m -- one of them a block whose window rows are 2.92 -- and each
        # proved it wrong by hand, twice from a histogram of vertex heights and
        # once by counting rows in a photograph.
        if measured and measured["found"] and measure.looks_like_tiling(
                measured["spacing"]):
            measured["found"] = False
            measured["tiling"] = True
            measured["why"] = (
                f"{measured['spacing']:.2f} m is the Google Earth tile seam, "
                "not a storey: the exporter puts a band of vertices every "
                f"{measure.TILE_SEAM} m on every facade of every building. "
                "Read the rhythm off the texture instead -- "
                "`measure.floor_lines` on an orthographic elevation -- or "
                "declare it from a photograph with the row count named.")

        # Before giving up on the geometry, ask the texture -- and offer the
        # answer rather than take it. The texture is the same capture
        # photographed instead of triangulated, so the tile seam is not in it;
        # but it holds every other horizontal line as well, and on a facade with
        # balconies it finds the slab *and* the rail and reports half a storey.
        #
        # Two candidates, neither trustworthy alone, is exactly the situation a
        # declaration exists for. So both are printed with what they came from,
        # and the run stops rather than picking.
        #
        # Read whether or not the geometry found something. When the geometry
        # is confident and the texture disagrees, that disagreement is the news:
        # a capture whose vertices say 4.5 m and whose photograph says 3.1 m has
        # told you which one is the exporter.
        # The texture is of the same capture the geometry came from. No
        # link means that file is not evidence -- described, or WITNESS
        # dropped it -- so this does not read a rhythm off it either.
        skin = self.from_texture(out) if link is not None else {}
        if skin.get("storey") and measured is not None:
            measured["from_texture"] = skin["storey"]
        if skin.get("bay"):
            out["facade"] = {"bay": skin["bay"]}

        # A weak geometry reading loses to a texture that four facades agree on.
        #
        # The autocorrelation returns a number and a score, and the score is a
        # threshold rather than a confidence: just over it means "found", and
        # just over it is where the wrong answers live. One hotel came back at
        # 2.25 m with a score of 0.167 against a bar of 0.15, while the texture
        # read 3.04, 3.09, 3.05 and 3.00 on its four elevations -- four
        # independent photographs of the same building agreeing to five
        # centimetres, losing to one marginal correlation over a cloud of
        # vertices.
        #
        # So: where the geometry is inside `STOREY_SURE` of its own bar and the
        # texture agrees across at least `TEXTURE_SIDES` elevations, the texture
        # wins and says so. Both numbers stay in the file either way -- this
        # picks which the build uses, it does not throw the other away.
        sure = getattr(self.t, "STOREY_SURE", 2.0) * self.t.STOREY_SCORE
        sides = getattr(self.t, "TEXTURE_SIDES", 3)
        read_off = (measured or {}).get("from_texture") or {}
        if (measured and measured.get("found")
                and measured["score"] < sure
                and read_off.get("agreed", 0) >= sides
                and read_off.get("value")):
            measured["geometry"] = {"spacing": measured["spacing"],
                                    "score": measured["score"]}
            measured["spacing"] = round(float(read_off["value"]), 2)
            measured["by"] = "texture"
            measured["why"] = (
                f"the geometry read {measured['geometry']['spacing']:.2f} m at "
                f"r={measured['geometry']['score']:.3f}, inside {sure:.2f} of "
                f"the bar it had to clear; the texture read "
                f"{read_off['value']:.2f} m and {read_off['agreed']} of "
                f"{read_off['of']} elevations agree with it")
            top = max((h["top"] for h in self.t.DECLARED_HEIGHTS.values()),
                      default=0.0)
            top = max(top, out.get("mesh", {}).get("top", 0.0))
            measured["levels"] = levels_from(measured["spacing"], top)

        if measured and measured["found"]:
            out["storeys"] = measured
        elif self.t.DECLARED_STOREY.get("spacing"):
            if not self.t.DECLARED_STOREY.get("source"):
                raise SystemExit("self.t.DECLARED_STOREY names no source")
            spacing = float(self.t.DECLARED_STOREY["spacing"])
            top = max((h["top"] for h in self.t.DECLARED_HEIGHTS.values()), default=0.0)
            if link is not None:
                top = max(top, out.get("mesh", {}).get("top", 0.0))
            out["storeys"] = {
                "by": "declared",
                "spacing": spacing,
                "score": 0.0,
                "found": True,
                "declared": True,
                "source": self.t.DECLARED_STOREY["source"],
                "levels": levels_from(spacing, top,
                                      float(self.t.DECLARED_STOREY.get("base",
                                                                       0.0))),
                "measured": measured,
            }
        elif measured:
            out["storeys"] = measured
        elif self.t.WITNESS is not None:
            raise SystemExit(
                "nothing states the storey height. WITNESS dropped the "
                "reference, so no rhythm can be measured, and "
                "self.t.DECLARED_STOREY names no spacing.\n"
                "Add one, with the sentence or the sheet it came from.")
        else:
            out["storeys"] = {
                "by": "nothing", "spacing": 0.0, "score": 0.0, "found": False,
                "levels": [], "why": "no reference to measure a rhythm off and no "
                                     "self.t.DECLARED_STOREY to fall back on",
            }
        out["storeys"]["window"] = [round(body[0], 1), round(body[1], 1)]

    def roof_of(self, out: dict, read: Read, link: Link | None) -> None:
        """How level each part's roof actually is, before anything is built on it.

        One number per part is the pipeline's default and it is right only for a
        flat roof. Where it is wrong it is wrong quietly: the section grades the
        same median the build was extruded to, so every number agrees with every
        other while the building is metres out. Five buildings in a row lost
        hours to it and every one of them had the evidence in `derived.json`
        from the first run -- a part whose median and maximum were four metres
        apart -- with nothing suggesting anybody look.

        This is the suggestion. It measures nothing the build must use; it says
        how much the one number can be trusted, and names the tool for the case
        where it cannot.
        """
        if link is None:
            return
        surface = roof.Roof.read(link.mesh, link, read.frame,
                                 read.mass.width, read.mass.length)
        out["roof"] = {}
        for name in read.order:
            mask = read.named[name].mask
            heights = sorted(surface.over(mask))
            if not heights:
                out["roof"][name] = {"cells": 0}
                continue
            deck = surface.deck(mask)
            levels = surface.terraces(mask)
            out["roof"][name] = {
                "cells": len(heights),
                "deck": round(deck, 2),
                "median": round(heights[len(heights) // 2], 2),
                "low": round(heights[0], 2),
                "high": round(heights[-1], 2),
                "above_deck": round(heights[-1] - deck, 2),
                "terraces": [self._terrace(t, len(levels)) for t in levels],
            }
        out["roof_lines"] = [line for name in read.order
                             for line in surface.lines(read.named[name].mask,
                                                       name)]

    @staticmethod
    def _terrace(terrace, levels: int) -> dict:
        """One measured level, with its shape when the shape is needed.

        A part that came back as a single terrace is a flat roof and the build
        already has its footprint, so the mask would be a copy of something it
        holds. A part that came back as several is the case this measurement
        exists for, and there the shapes are the answer: without them a build
        can only put the steps in a bounding box, which is the mistake in a
        different costume.
        """
        entry = {"height": round(terrace.height, 2),
                 "low": round(terrace.low, 2),
                 "high": round(terrace.high, 2),
                 "cells": terrace.cells}
        if levels > 1:
            entry["mask"] = terrace.mask.dumps()
        return entry

    def skyline_of(self, out: dict, read: Read, link: Link | None) -> None:
        """How tall each part is: read over its own footprint, or declared.

        Over its own footprint and not over one window for the whole building: where
        a wing steps down, a single median splits the difference and is wrong at both
        ends.
        """
        out["skyline"] = {}
        if link is None:
            for name in read.order:
                entry = self.t.DECLARED_HEIGHTS.get(name)
                if not entry:
                    raise SystemExit(
                        f"nothing states how tall {name!r} is. There is no model "
                        "and no capture, so no height can be measured, and "
                        f"self.t.DECLARED_HEIGHTS has no entry for {name!r}.\n"
                        "Add one, with the sentence or the sheet it came from.")
                if not entry.get("source"):
                    raise SystemExit(f"the declared height of {name!r} names no "
                                     "source")
                out["skyline"][name] = {"stations": 0, "declared": True,
                                        "median": float(entry["top"]),
                                        "source": entry["source"]}
            return

        # Each part is read over its own *mask*, not over its bounding box. A box is
        # right for two parallel strips and wrong for everything else: an L-shaped
        # wing's box covers the court it wraps around, and a part whose box overlaps
        # its neighbour's reads the neighbour's roof. The height of a part is the
        # number the whole build stands on, and nothing downstream can catch it
        # being wrong -- the section compares the same thing.
        #
        # One pass over the reference and not one per part. Mapping a vertex to the
        # part it stands over is the expensive step, and on a capture with a million
        # vertices doing it five times is five times the wait.
        owner: dict[tuple[int, int], str] = {}
        for name in read.order:
            for cell in read.named[name].mask.cells():
                owner[cell] = name

        tops: dict[str, dict[int, float]] = {name: {} for name in read.order}
        between: dict[int, float] = {}
        mesh = link.mesh
        for i in range(len(mesh)):
            u, v = link.frame.to_local(mesh.x[i], mesh.z[i])
            bv = link.to_build_v(v)
            x, z = read.frame.to_world(link.to_build_u(u), bv)
            height = mesh.y[i] - link.datum
            name = owner.get((int(x), int(z)))
            station = int(bv)
            if name is None:
                if height > between.get(station, -1e9):
                    between[station] = height
                continue
            if height > tops[name].get(station, -1e9):
                tops[name][station] = height

        # A part is one height only when its stations say so, and `low .. median
        # .. high` cannot tell "a parapet and a lift overrun" from "a tower
        # standing on this roof". Both read as a spread; only the *share* at the
        # median separates them. On one hotel the two-storey link between the
        # towers reported 5.2 .. 27.6 .. 29.8, which is the flat roof at one end
        # of the range and the neighbour at the other, and the number handed
        # downstream was the neighbour's. Nothing caught it: the median is the
        # height the whole build stands on, and every row below reads it from
        # here.
        band = float(self.t.PLATEAU)

        def plateau(reading: dict[int, float]) -> dict:
            got = sorted(reading.values())
            if not got:
                return {"stations": 0}
            median = got[len(got) // 2]
            near = [h for h in got if abs(h - median) <= band]
            far = [h for h in got if abs(h - median) > band]
            out = {"stations": len(got),
                   "low": round(got[0], 1),
                   "median": round(median, 1),
                   "high": round(got[-1], 1),
                   "band": band,
                   "share": round(len(near) / len(got), 3)}
            if far:
                # The other level, named rather than averaged into the spread:
                # the question a reader asks next is "how far away, and how much
                # of the part", and a range answers neither.
                out["apart"] = round(sorted(far)[len(far) // 2] - median, 1)
                out["elsewhere"] = len(far)
            return out

        for name in read.order:
            out["skyline"][name] = plateau(tops[name])
            # A part the reference has nothing over is a fact worth stopping on when
            # it is the whole building and worth recording when it is one wing: a
            # capture that never modelled a wing cannot grade it either.
            declared_top = self.t.DECLARED_HEIGHTS.get(name)
            if not out["skyline"][name]["stations"] and declared_top:
                out["skyline"][name] = {"stations": 0, "declared": True,
                                        "median": float(declared_top["top"]),
                                        "source": declared_top["source"]}

        # The gaps between parts, which are courts, streets or light wells, and
        # which a skyline reports as whatever stands over them. A court that reads
        # as tall as its wings is a capture that fused a tree to a facade, or a wing
        # that is not where the plan says it is.
        across = sorted(read.order, key=lambda n: read.named[n].v0)
        for near, far in zip(across, across[1:]):
            a, b = read.named[near].v1, read.named[far].v0
            if b - a < 1.0:
                continue
            out["skyline"][f"{near}..{far}"] = plateau(
                {k: h for k, h in between.items() if a <= k + 0.5 < b})

    def tops_from(self, read: Read, link: Link) -> dict[str, float]:
        """The median height over each part, read off one reference.

        The same measurement `skyline_of` makes, factored out so that a second
        reference can be asked the same question and the two answers compared.
        """
        owner: dict[tuple[int, int], str] = {}
        for name in read.order:
            for cell in read.named[name].mask.cells():
                owner[cell] = name

        tops: dict[str, dict[int, float]] = {name: {} for name in read.order}
        mesh = link.mesh
        for i in range(len(mesh)):
            u, v = link.frame.to_local(mesh.x[i], mesh.z[i])
            bv = link.to_build_v(v)
            x, z = read.frame.to_world(link.to_build_u(u), bv)
            name = owner.get((int(x), int(z)))
            if name is None:
                continue
            height = mesh.y[i] - link.datum
            station = int(bv)
            if height > tops[name].get(station, -1e9):
                tops[name][station] = height

        out = {}
        for name, reading in tops.items():
            got = sorted(reading.values())
            if got:
                out[name] = got[len(got) // 2]
        return out

    def witnesses_of(self, out: dict, evidence, read: Read, link: Link | None) -> None:
        """Ask every held-out source the questions it could have answered.

        This is the rule of the whole pipeline turned into rows that can fail. Where
        two supplied inputs can both answer the same question, both answer it, and
        the difference is written down with a tolerance beside it.

        What it catches is a class of fault no section can reach, because a section
        compares the build to *one* reference and these faults are in the reference:
        a map crop rescaled before it was cropped, a capture of the building next
        door, a model of a later revision than the photographs, a drawing read at
        the wrong scale.

        A silhouette overlap under the floor is not in that list. It is two
        buildings, not two views of one, and declaring it expected is what let a
        game-map crop of a dogbone grade as a capture of a different tower: the
        only row that said so became a dash, the verdict was ungraded, and the
        build reproduced the map of somewhere else. `EXPECTED` cannot name it.
        `WITNESS` is the way to say the reference is of something else, and it
        takes a sentence -- the flag version of this already let a real
        disagreement pass as expected.
        """
        banned = set(self.t.EXPECTED) & {"plan overlap"}
        if banned:
            raise SystemExit(
                "EXPECTED cannot declare 'plan overlap'. Two silhouettes that "
                "share less than the floor are not one building seen twice, "
                "they are two buildings, and calling the difference expected "
                "hides the only row that says so.\n"
                "Decide instead: build what the drawn plan shows and set "
                "WITNESS to the sentence saying the reference is of something "
                "else, or build what the reference shows and let the plan "
                "answer only for the parts that matched.")
        sources.witness_sentence(self.t.WITNESS)
        found: list[witnesses.Agreement] = []
        by = read.source.name

        # The plan against the reference: how big, and which way round. Both come
        # free with the registration that has already been fitted, and both were
        # being thrown away.
        reg = out.get("registration", {})
        if reg.get("needed") and link is not None:
            # The same extent the registration was fitted from, or this row
            # measures the site against a reading of the building.
            u0, u1, v0, v1 = read.drawn_bounds()
            found.append(witnesses.size(
                u1 - u0, v1 - v0,
                reg["u"]["mesh"][1] - reg["u"]["mesh"][0],
                reg["v"]["mesh"][1] - reg["v"]["mesh"][0],
                by, link.kind))
            found.append(witnesses.bearing(
                read.frame.angle, link.frame.angle, by, link.kind))

        # A second OBJ, if one was supplied: the same parts, read off both.
        others = [s for s in evidence.sources
                  if s.name in ("model", "capture")
                  and (link is None or s.name != link.kind)]
        if link is not None and others:
            second = self.link_to(read, others[0], out, record=False)
            found.append(witnesses.heights(
                self.tops_from(read, link), self.tops_from(read, second),
                link.kind, second.kind))

        # A storey height that was both measured and declared. `storeys_of` keeps
        # the measurement beside the declaration precisely so this can be asked.
        section = out.get("storeys", {})
        if section.get("by") == "declared" and section.get("measured"):
            measured = section["measured"]
            if measured.get("found"):
                found.append(witnesses.storey(
                    measured["spacing"], section["spacing"],
                    measured["by"], "declared"))

        # Two plans of the same building: the one that was used, and one held back.
        # Compared after both are put in their own frames' (u, v), so this measures
        # shape and proportion and not where either of them thinks the building is.
        held = evidence.witnesses_for("plan")
        if held and any(s.name == "map" for s in held) and read.source.name != "map":
            try:
                other_mass, other_frame, _ = decompose(self.paths.LAYOUT,
                                                       palette=self.t.MAP_PALETTE)
            except SystemExit:
                other_mass = None
            if other_mass is not None:
                same = read.mass.empty_like()
                for x, z in other_mass.cells():
                    u, v = other_frame.to_local(x + 0.5, z + 0.5)
                    wx, wz = read.frame.to_world(u, v)
                    same.set(int(wx), int(wz))
                found.append(witnesses.overlap(
                    iou(read.mass, same), by, "map"))

        # The plan against the reference, which is the pair every building has
        # and none of them was comparing. The number falls out of the squareness
        # sweep -- it is the overlap at zero -- so it costs nothing extra, and it
        # is the one shape check on the registration itself: two footprints that
        # register perfectly by extent and overlap six tenths are not the same
        # building seen twice.
        square = (out.get("registration") or {}).get("square")
        kind = (out.get("mesh") or {}).get("kind")
        if square and kind and read.source.name != kind:
            found.append(witnesses.overlap(square["as_fitted"], by, kind))

        # Two silhouettes that share less than the floor are not one building
        # seen twice. Stop rather than write a failing row that EXPECTED used
        # to be able to grey out: the grey dash read exactly like "nobody
        # could answer this", and a real pair of different buildings passed.
        floor = witnesses.OVERLAP
        for one in found:
            if one.question != "plan overlap" or self.t.WITNESS:
                continue
            value = one.rows["silhouette"][1].value
            if value < floor:
                raise SystemExit(
                    f"the drawn plan and the reference share {value:.2f} of their "
                    f"silhouettes against a floor of {floor:.2f}. Two drawings of "
                    "one building do not do that.\n"
                    "Look at input/layout.png and the capture's ortho "
                    "(out/mesh-clip/orthos/top.png) before anything else, and "
                    "there are three ways out.\n"
                    f"    The commonest under a clip of the site: the two are of "
                    f"one building and the fit is of the whole parcel. "
                    f"REGISTER_FLOOR and MESH_FLOOR are at "
                    f"{self.t.REGISTER_FLOOR:.1f} m and {self.t.MESH_FLOOR:.1f} "
                    f"m, and every vertex above them enters the fit -- the "
                    "villas, the palms, the neighbour over the parcel line -- "
                    "while the plan half is the drawn building alone. Raise both "
                    "above the accessory volumes and below the lowest part of the "
                    "drawn building. That is what states which volume on this "
                    "parcel the plan drew, and it bounds the fit only: every "
                    "height is still read over every part.\n"
                    "    They really are of different buildings -- a game map "
                    "beside a capture of the real prototype is the usual way. Set "
                    "WITNESS in this file to the sentence that says so, and the "
                    "reference is dropped from the evidence: every row it would "
                    "have graded reads ungraded with that sentence, and heights "
                    "and the storey must be declared.\n"
                    "    They are the same building at the same floor, and then "
                    "the clip or the crop is wrong.")

        # Disagreements the building has said to expect. Not a widened
        # tolerance: the row goes ungraded with the reason printed beside it,
        # so it stays legible and every other disagreement on that axis is
        # still graded.
        witnesses.declare(found, dict(self.t.EXPECTED))
        if self.t.WITNESS is not None:
            if not found:
                # Nothing was compared: the reference is not evidence. The
                # questions it would have answered still appear, or the
                # report looks like nobody asked -- the JAGGED = None shape
                # the other way.
                found.extend([
                    witnesses.Agreement("extent", "x", witnesses.SIZE),
                    witnesses.Agreement("bearing", "deg", witnesses.ANGLE),
                    witnesses.Agreement("plan overlap", "IoU",
                                        1.0 - witnesses.OVERLAP),
                ])
            for one in found:
                one.declare(self.t.WITNESS)
        out["witnesses"] = [one.report() for one in found]
        out["witness_lines"] = witnesses.lines(found)

    def notches_of(self, out: dict, read: Read) -> None:
        """The notch pitch on the long edge, where there is a measured edge at all.

        Skipped on a declared plan, and not because it would crash: it would return
        a confident number measured off a rectangle this file drew itself. The map
        and the model have edges somebody else made; a declared plan has only the
        edges it was given.
        """
        if read.source.name not in ("map", "model", "capture"):
            out["notch"] = {"measured": False,
                            "why": f"the plan is declared from "
                                   f"{read.source.kind.name}, so its edges are as "
                                   "straight as they were typed"}
            return

        step = out["frame"]["staircase"]
        # The longest *drawn* edge. The guard above is about the plan's source
        # and the selection was not: an assembled part's outline is a
        # photogrammetric silhouette, which is exactly the kind of edge this row
        # exists to keep out -- "the map and the model have edges somebody else
        # made", and a capture's are edges nobody made.
        edge = max(read.drawn_parts(), key=lambda p: p.u1 - p.u0)
        profile = measure.edge_profile(edge.mask, read.frame, self.t.PROFILE_BIN)
        series = measure.detrend(
            measure.median_filter(profile.trim(self.t.PROFILE_TRIM).series("high"),
                                  half=int(step / self.t.PROFILE_BIN) + 1),
            half=int(20.0 / self.t.PROFILE_BIN))
        notch = measure.period(series, self.t.PROFILE_BIN,
                               lo=step * measure.STAIRCASE_MARGIN,
                               hi=self.t.NOTCH_RANGE_HI)
        out["notch"] = {
            "measured": True,
            "pitch": round(notch.value, 1),
            "score": round(notch.score, 3),
            "next": [[round(p, 1), round(r, 3)] for p, r in notch.peaks[1:4]],
            "searched": [round(step * measure.STAIRCASE_MARGIN, 2), self.t.NOTCH_RANGE_HI],
            "read_off": read.order[read.parts.index(edge)],
        }

    def provenance_of(self, out: dict, read: Read) -> None:
        """Three words per part: what measured its footprint, its height, and
        what can contradict either.

        Written for the reader rather than for the code: everything here is
        already knowable from other keys in the file, and nobody ever worked it
        out, so a part built freehand read exactly like a part set out on a
        survey. Stating it costs a dictionary and removes the whole class.
        """
        skyline = out.get("skyline", {})
        # Sections are the gate's, computed later -- there is no `sections` key
        # here. What derive can know is whether this part's height was read off
        # the reference rather than declared. A part whose skyline came from the
        # mesh has something that can contradict it; a declared height does not.
        measured_by = (out.get("mesh") or {}).get("kind")
        for record in out["parts"]:
            name = record["name"]
            entry = skyline.get(name)
            if not isinstance(entry, dict):
                entry = {}
            if entry.get("declared"):
                height = "declared"
            elif entry.get("median") is not None and entry.get("stations"):
                height = (measured_by if measured_by in ("capture", "model")
                          else "capture")
            elif name in (self.t.DECLARED_HEIGHTS or {}):
                height = "declared"
            else:
                height = "none"
            if self.t.WITNESS is not None:
                # The reference is not a witness. A leftover measurement
                # must not claim it covered the part.
                if height in ("capture", "model"):
                    height = ("declared"
                              if name in (self.t.DECLARED_HEIGHTS or {})
                              else "none")
                witness = "none"
            else:
                witness = ("section" if height in ("capture", "model")
                           else "none")
            record["provenance"] = {
                "plan": read.provenance.get(name, "declared"),
                "height": height,
                "witness": witness,
            }

    def declarations_of(self, out: dict, read: Read) -> None:
        """The photograph-read facts, and what follows from them.

        The count is declared; the pitch it implies is measured, and the plan's own
        notches are asked whether they land on the same pitch. A declaration that
        nothing corroborates is still used -- photographs are the reference for which
        parts exist -- but the report says so, which is the point.
        """
        out["declared"] = {}
        notch = out.get("notch", {})
        for key, entry in self.t.DECLARED.items():
            record = dict(entry)
            if "source" not in record:
                raise SystemExit(
                    f"declaration {key!r} names no photograph. A declared number is "
                    "only as good as the reference it was read off, and one with no "
                    "source cannot be checked, argued with, or re-read later.")
            pitches = {}
            for name in read.order:
                count = record.get(name)
                if isinstance(count, int) and count > 0:
                    part = read.named[name]
                    pitches[name] = round((part.u1 - part.u0) / count, 1)
            if pitches:
                record["pitch"] = pitches
                spread = max(pitches.values()) - min(pitches.values())
                record["agree"] = spread < 1.0
                record["corroborated"] = [
                    name for name, pitch in pitches.items()
                    if notch.get("pitch")
                    and abs(pitch - notch["pitch"]) <= 2 * self.t.PROFILE_BIN
                ]
            out["declared"][key] = record

    def main(self) -> dict:
        out: dict = {}

        self.unconverted()
        evidence = sources.survey(self.paths, witness=self.t.WITNESS)
        stop = sources.refuse(evidence)
        if stop:
            raise SystemExit("nothing to measure: " + stop)
        out["evidence"] = evidence.to_json()
        if self.t.WITNESS is not None:
            out["witness"] = self.t.WITNESS

        # -- the plan -----------------------------------------------------------

        by = evidence.answers("plan")
        read = self.plan_of(out)

        # Two facts about the plan, and they are not the same fact, so both are
        # written down and neither is inferred from the other:
        #
        #   `how`     how the numbers got here. `read` is machine-read off a file;
        #             `typed` is a person reading a sheet or a sentence and writing
        #             it down, which no re-run will re-derive.
        #   `weight`  what the evidence model thinks that source is worth on the
        #             question of the plan -- 2 measured, 1 declared.
        #
        # A capture is `read` and weight 1: photogrammetry draws a fuzzy outline,
        # machine-read or not. A scaled drawing is `typed` and weight 2: real metres,
        # arrived by hand. Collapsing the two into one word is what made an earlier
        # version of this file call a typed plan "measured" in one field and
        # "declared" in another, in the same JSON.
        step = measure.staircase(read.frame)
        out["plan"] = {
            "by": by.name,
            "how": "read" if by.name in ("vector", "map", "model", "capture")
               else "typed",
            "weight": by.weight("plan"),
            "measured": by.metric("plan"),
        }
        out["frame"] = {
            "origin": [round(v, 2) for v in read.frame.origin],
            "angle": round(read.frame.angle, 2),
            "extent": [round(read.frame.extent_u, 1), round(read.frame.extent_v, 1)],
            "staircase": round(step, 3),
            # What the drawing board was put onto and what that cost, or the
            # building's reason for staying off it. Written whichever way it
            # went: a snap nobody can see the price of is a snap nobody can
            # argue with.
            "slope": (read.slope.to_json() if read.slope is not None else None),
            "unlatticed": read.unlatticed,
        }
        if read.slope is not None and read.slope.strained:
            out["frame"]["slope"]["strained"] = True
            out["frame"]["slope"]["budget"] = round(
                self.t.LATTICE_BUDGET
                or lattice.budget_for(max(read.frame.extent_u,
                                          read.frame.extent_v)), 2)
        out["parts"] = [
            {"name": name, "kind": read.named[name].kind,
             "u": [round(read.named[name].u0, 1), round(read.named[name].u1, 1)],
             "v": [round(read.named[name].v0, 1), round(read.named[name].v1, 1)],
             "cells": read.named[name].mask.count()}
            for name in read.order
        ]

        self.notches_of(out, read)
        self.declarations_of(out, read)

        # -- the section --------------------------------------------------------

        # Fitted in `plan_of`, because the plan is assembled through it, and
        # taken rather than fitted again here: two fits of one pair is one fit
        # too many, and the second would be made against a plan the first
        # helped to write.
        link = read.link

        self.storeys_of(out, read, link)
        self.skyline_of(out, read, link)
        self.provenance_of(out, read)
        self.roof_of(out, read, link)
        self.witnesses_of(out, evidence, read, link)

        # The building's own windows into the reference, if it opened any.
        # Everything above is true of any building; what a probe adds is the
        # part only this one needs. It is handed the same three things the
        # apparatus works with, and whatever it writes into `out` lands in
        # derived.json beside the rest.
        if self.probes is not None:
            self.probes(out, read, link)

        self.paths.DERIVED.parent.mkdir(parents=True, exist_ok=True)
        self.paths.DERIVED.write_text(json.dumps(out, indent=1), encoding="utf-8")
        return out

    def report(self, out: dict) -> list[str]:
        e = out["evidence"]
        lines = [f"evidence: {e['tier']}"]
        for question, answer in e["answers"].items():
            if answer["by"] is None:
                lines.append(f"  {question:9s} nothing answers it")
                continue
            lines.append(
                f"  {question:9s} {answer['how']:8s} from {answer['by']}"
                + (", checked against " + ", ".join(answer["witnesses"])
                   if answer["witnesses"] else ", nothing to check it against"))
        lines.append("")
        lines.append("coverage: what measured each part")
        lines.append(f"  {'part':22s} {'footprint':10s} {'height':10s} "
                     f"witness")
        for record in out.get("parts", []):
            p = record.get("provenance", {})
            lines.append(
                f"  {record['name']:22s} {p.get('plan', '-'):10s} "
                f"{p.get('height', '-'):10s} {p.get('witness', '-')}")
        lines.append("")

        # How that plan was put together, printed whichever way it went. The
        # sizes are here so that a first run on a new site has something to
        # write CAPTURE_PARTS from -- the same bargain STRIPS and MODEL_PARTS
        # make -- and so that a piece the matching absorbed or refused is a
        # number a reader can argue with rather than a silence.
        a = out.get("assembly")
        if a and a.get("why"):
            lines += [f"assembly: {a['drawn']} drawn part(s); {a['why']}", ""]
        elif a:
            # Every piece of the reference accounted for in one line: matched,
            # added, dropped as too slight, or off the canvas. A count that
            # does not add up to what the reference split into is a piece
            # that went somewhere unsaid.
            gone = a.get("dropped") or {}
            lines.append(
                f"assembly: {a['drawn']} drawn part(s); the reference splits "
                f"into {a['reference_parts']}, of which {a['matched']} overlap "
                f"one that is drawn and {a['extra']} do not"
                + (f", {gone['count']} fall under the part thresholds"
                   if gone.get("count") else "")
                + (f", and {len(a['off_plan'])} lie off the plan's canvas"
                   if a.get("off_plan") else "")
                + f" (floor {a['floor']:.2f})")
            # The number that decides where an assembled part lands, on the run
            # that decides it. The margin is printed beside the asymmetry and
            # never alone: it is the gap between the four ways round, and on an
            # axis that reads the same reversed that gap is noise with a
            # decimal point.
            o = a.get("orientation") or {}
            if o:
                lines.append(
                    f"  which way round: margin {o.get('margin')}, profile "
                    f"differs from its reverse by {o.get('shape')} m along u "
                    f"and across v, frames {o.get('bearing')} deg apart"
                    + (f" -- {' and '.join(o['undetermined'])} reads the same "
                       "either way, so the canonical fits settle it"
                       if o.get("undetermined") else ""))
            # Printed even when it threw nothing away. A run that never
            # applied the bars and a run that applied them to nothing look
            # the same without this line, and they are not the same fact.
            lines.append(
                f"  dropped: {gone.get('count', 0)} piece(s), "
                f"{gone.get('area', 0.0)} m2 in all, tallest "
                f"{gone.get('tallest', 0.0)} m -- under "
                f"PART_LEAST_AREA or PART_LEAST_HEIGHT")
            for one in a.get("added", []):
                lines.append(
                    f"  {one['name']:15s} {one['cells']:5d} cells to "
                    f"{one['top']:5.1f} m, best overlap {one['overlap']:.2f}"
                    + (f", {one['clipped']} cell(s) past the plan's canvas"
                       if one["clipped"] else ""))
            for one in a.get("off_plan", []):
                lines.append(
                    f"  {'':15s} {one['cells']:5d} cells to {one['top']:5.1f} m "
                    f"reach the plan's canvas at {one['on_the_plan']} cell(s), "
                    f"{one['past']} past it -- too little of it is on this plan "
                    "to be a part of it. Widen the crop the plan was drawn on, "
                    "or clip the reference to what that crop covers")
            lines.append("")

        f = out["frame"]
        lines += [
            f"plan {out['plan']['how']} from {out['plan']['by']}"
            + ("" if out["plan"]["measured"] else " -- a declaration, not a survey")
            + ("  -- photogrammetry draws a fuzzy outline; treat every plan "
               "dimension as good to a metre or two"
               if out["plan"]["by"] == "capture" else ""),
            f"frame  origin ({f['origin'][0]}, {f['origin'][1]})  "
            f"{f['angle']} deg  {f['extent'][0]} x {f['extent'][1]} m  "
            f"staircase {f['staircase']}",
        ]
        s = f.get("slope")
        if s:
            motif = "-".join(str(n) for n in s["motif"]) or "flat"
            lines.append(
                f"lattice {abs(s['step'][0])}:{abs(s['step'][1])}  motif {motif}"
                f"  period {s['period']} m  "
                f"{s['error']:+.2f} deg off the measured {s['measured']}"
                f"  {s['cost']} m at the ends"
                + (f"  -- OVER the {s['budget']} m budget; the section is what "
                   "grades this" if s.get("strained") else ""))
        elif f.get("unlatticed"):
            lines.append(f"lattice none -- {f['unlatticed']}")
        lines += ["", "the parts"]
        for p in out["parts"]:
            lines.append(f"  {p['name']:12s} {p['kind']:5s} "
                         f"u {p['u'][0]:6.1f}..{p['u'][1]:6.1f}  "
                         f"v {p['v'][0]:5.1f}..{p['v'][1]:5.1f}  "
                         f"{p['cells']:5d} cells")
        for name, d in out.get("discs", {}).items():
            lines.append(f"  {name} fitted at ({d['centre'][0]}, {d['centre'][1]}), "
                         f"r {d['radius']} m, residual {d['residual']} m")

        if out.get("guides"):
            lines += ["", "the interior drawn lines, which the parts were cut on"]
            for g in out["guides"]:
                lines.append(f"  along {g['along']}  v {g['mid_v']:5.1f}  "
                             f"u {g['u'][0]:6.1f}..{g['u'][1]:6.1f}  "
                             f"{g['cells']:4d} cells")

        n = out["notch"]
        if n.get("measured"):
            lines += ["", f"notches on the long edge: {n['pitch']} m "
                          f"(r={n['score']:+.2f}), searched "
                          f"{n['searched'][0]}..{n['searched'][1]} m"]
            if n["next"]:
                lines.append("  then " + ", ".join(f"{p} m r={r:+.2f}"
                                                   for p, r in n["next"]))
        for key, d in out.get("declared", {}).items():
            lines.append(f"{key}: declared from {d['source']}")
            if "pitch" in d:
                lines.append("  centres " + ", ".join(
                    f"{name} {pitch} m" for name, pitch in d["pitch"].items())
                    + " -- " + ("they agree" if d["agree"] else "THEY DISAGREE"))
                if n.get("measured"):
                    lines.append(
                        f"  the notch pitch of {n['pitch']} m "
                        + (f"corroborates {', '.join(d['corroborated'])}"
                           if d["corroborated"]
                           else "matches nothing here, so it reads as a facade "
                                "sub-bay"))

        m = out.get("mesh", {})
        if m.get("read"):
            lines += [
                "",
                f"{m['kind']}: {m['vertices']} vertices, ground at {m['datum']} m, "
                f"top {m['top']} m above it",
                f"  its own frame {m['frame']['angle']} deg, "
                f"{m['frame']['extent'][0]} x {m['frame']['extent'][1]} m",
            ]
        r = out.get("registration", {})
        if r.get("needed"):
            lines += [
                f"  registered on material above {r['floor']} m: "
                f"u scale {r['u']['scale']}, v scale {r['v']['scale']}, "
                f"differ by {r['disagreement']}"
                + ("" if r["agrees"] else
                   ("  <-- ungraded, expected" if r.get("expected")
                    else "  <-- DOES NOT REGISTER")),
                f"    u  mesh {r['u']['mesh'][0]}..{r['u']['mesh'][1]} "
                f"-> plan {r['u']['plan'][0]}..{r['u']['plan'][1]}",
                f"    v  mesh {r['v']['mesh'][0]}..{r['v']['mesh'][1]} "
                f"-> plan {r['v']['plan'][0]}..{r['v']['plan'][1]}",
            ]
            if r.get("expected"):
                lines.append("    " + r["expected"])
        elif r:
            lines.append("  " + r["why"])

        if r.get("turned") and r["turned"] != "the same way round":
            scores = dict(r.get("orientation") or {})
            margin = scores.pop("margin", None)
            shape = scores.pop("shape", None)
            blind = scores.pop("undetermined", "")
            scores.pop("shape_floor", None)
            lines.append(f"    the reference stands {r['turned']} to the plan; "
                         f"fit {scores}")
            if margin is not None and margin < 0.15:
                lines.append(
                    f"    chosen by {margin:.3f}, which is close. On a "
                    "symmetric building either way round is the same building "
                    "and it does not matter; on one with a short wing or a "
                    "round end, check it.")
            # And which of the two axes had a profile to decide it with. A
            # margin says how far apart the four scored; it does not say
            # whether the thing they scored could be told from its own reverse.
            lines.append(
                f"    decided on a width profile differing from its reverse by "
                f"{shape} m along u and across v"
                + (f"; {' and '.join(blind)} reads the same either way, so that "
                   "flip is the canonical fits' and not the profile's"
                   if blind else ""))

        sq = r.get("square") if r else None
        if sq:
            lines.append(
                f"    frames square to within {sq['best']:+.1f} deg: the two "
                f"footprints overlap {sq['as_fitted']:.3f} as fitted, "
                f"{sq['overlap']:.3f} at the best angle in the sweep")
            if abs(sq["best"]) >= 2.0 and sq["gain"] >= 0.05:
                lines.append(
                    f"    THE FRAMES ARE NOT SQUARE. Turning the reference "
                    f"{sq['best']:+.1f} deg would gain {sq['gain']:.3f} of "
                    "overlap, which means the two fits disagree about which "
                    "way this building points. The registration does not "
                    "rotate, so that disagreement is spread over every station "
                    "of every section as if it were photogrammetry noise.\n"
                    "    Fix the clip or the capture's heading. Do not widen a "
                    "tolerance: the sections will still be wrong, quietly.")

        if out.get("facade", {}).get("bay"):
            lines.append("")
            lines.extend(_texture_lines("bay", out["facade"]["bay"], None))
            lines.append("  the pier rhythm the elevations actually carry. It "
                         "is a candidate for FACADE_PITCH, which every building "
                         "so far has chosen by hand.")

        s = out["storeys"]
        if s["by"] == "declared":
            lines += ["", f"storey height {s['spacing']} m, declared from "
                          f"{s['source']}",
                      "  floors at " + ", ".join(f"{v:.2f}" for v in s["levels"])]
            if s.get("measured"):
                lines.append(f"  the reference was searched first and returned "
                             f"{s['measured']['spacing']} m at "
                             f"r={s['measured']['score']:+.2f}, which is noise")
                if s["measured"].get("from_texture"):
                    lines.extend(_texture_lines(
                        "storey", s["measured"]["from_texture"],
                        float(s["spacing"])))
        elif s["by"] == "nothing":
            lines += ["", "storey height: " + s["why"]]
        else:
            lines += [
                "",
                f"storey height {s['spacing']} m (r={s['score']:+.2f}) over the "
                f"modelled facades, u {s['window'][0]}..{s['window'][1]}",
                "  floors at " + ", ".join(f"{v:.2f}" for v in s["levels"]),
            ]
            if s.get("from_texture"):
                lines.extend(_texture_lines(
                    "storey", s["from_texture"], s["spacing"] if s["found"]
                    else None))
            if not s["found"]:
                lines.append("  NOT FOUND -- " + (
                    s["why"] if s.get("tiling") else
                    "that correlation is noise, and the spacing above is the "
                    "peak of it. Move the bands onto a facade the reference "
                    "actually modelled, read the rhythm off the texture, or "
                    "declare it from a photograph."))

        if out.get("witness_lines"):
            lines += [""] + out["witness_lines"]

        if out.get("roof_lines"):
            lines += ["", "what the reference holds over each part"]
            lines += out["roof_lines"]

        lines += ["", "skyline"]
        for name, p in out["skyline"].items():
            if p.get("declared"):
                lines.append(f"  {name:15s} {p['median']:5.1f} m declared from "
                             f"{p['source']}")
            elif p["stations"]:
                line = (f"  {name:15s} {p['low']:5.1f} .. {p['median']:5.1f} "
                        f".. {p['high']:5.1f} m over {p['stations']} stations")
                if p.get("elsewhere"):
                    line += (f"; only {p['share']:.0%} of it is at that median, "
                             f"{p['elsewhere']} station(s) stand {p['apart']:+.1f} m "
                             "away -- something else is over this part")
                lines.append(line)
            else:
                lines.append(f"  {name:15s} nothing over it in the reference")
        for name, d in out.get("discs", {}).items():
            if d.get("mesh_top") is not None:
                lines.append(f"  {name:15s} the reference closes it at "
                             f"{d['mesh_top']} m")
        return lines


__all__ = ["DEFAULTS", "Link", "Read", "Survey", "Tables"]
