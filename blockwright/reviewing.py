"""The photo review, as apparatus: cameras, renders, the folder, the prompt.

Every line in this module is the same on every building. What differs is two
tables -- where the cameras stand, and what the building is in a sentence -- and
those stay in `buildings/<name>/review.py`, which is now those tables and a call
to `Review(...).run(...)`.

It used to be one file, copied whole into every building, six hundred lines a
copy. That is the argument `gate.py` makes about itself in its own docstring --
a copied driver rots differently in each copy, and the copies go on printing
green -- and it applied here too, for longer.

Why one folder, one request and one reviewer; why the reviewer is handed
pictures and nothing else; why the prompt names only the kinds of image actually
present: `docs/pipeline.md`, stage 5.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import findings, render, sources
from .paths import schematic_of
from .schem import Schematic






# What the reviewer is told, and the stale filename a leftover journal
# still has in this folder. The journal is the building's `findings.md`,
# above `out/` -- nothing rebuilds it, so this folder no longer owns one.
# `clear` must not unlink the name: for two frozen buildings it is the
# only copy.
#
# **Nothing reads the set-aside copy.** `findings.path_of` looks for
# `findings.md` and only that, so what lands under `LEFTOVER` is invisible to
# the audit, to the manual mode's waiting check and to every printout. It is
# rescued text, not a journal, and the move says so where it happens.
PROMPT_FILE = "prompt.txt"
FINDINGS = "findings.md"
LEFTOVER = "findings.leftover.md"


SIZE = (1280, 720)


# Where Blender is. Often not on PATH on Windows, so the usual install locations
# are tried in turn before giving up and printing the command for a person to run
# themselves.
# Photographs, for material and rhythm. Every one there is, not a chosen few:
# they are not matched to any shot and are not meant to be, so there is no reason
# to prefer one over another, and between them they cover the parts of the
# building at ground level -- which is exactly where a capture is worst.
#
# The long edge a photograph is reduced to. Above about 1500 pixels a model gains
# nothing from the extra detail and the request pays for it anyway.
PHOTO_EDGE = 1500


# Drawings and sketches go in too, at the same size. A dimensioned elevation is
# the one reference that states the rhythm exactly, and a reviewer holding it
# beside a render can count bays rather than judge them. They are not renders of
# anything and are labelled so: a drawing shows intent, and a build that differs
# from it may be following the photographs, which show what was built.
DRAWING_EDGE = 1500


# The kinds of image, one block each, assembled in `collect` from what is
# actually in the folder. Describing a kind that is not there is not harmless:
# a reviewer told it has photographs and given none goes looking for material
# evidence in a render that carries none, and reports the block palette as the
# building's colour.
KIND_BUILD = """  <n>-<view>.build   the Minecraft build, one block to one metre."""


KIND_INK = """  <n>-<view>.ink     the same build from the same camera, drawn as lines: a
                     line wherever one part meets another, a surface turns, or
                     something stands in front of something else. No colour, no
                     shading, no block grid. This is the image to judge SHAPE on
                     -- silhouette, proportion, rhythm, how many volumes there
                     are and where each one stops. The shaded render's sun is
                     deliberately hard so that a roof and a wall never take the
                     same brightness, which is exactly what makes it a poor
                     picture for this."""


# Photo-review ink names rhythm. A greybox prompt that repeats that word
# tells a naive reviewer to count bays on a building that has none.
KIND_INK_FORM = """  <n>-<view>.ink     the same build from the same camera, drawn as lines: a
                     line wherever one part meets another, a surface turns, or
                     something stands in front of something else. No colour, no
                     shading, no block grid. This is the image to judge SHAPE on
                     -- silhouette, proportion, how many volumes there are and
                     where each one stops. The shaded render's sun is
                     deliberately hard so that a roof and a wall never take the
                     same brightness, which is exactly what makes it a poor
                     picture for this."""


KIND_PARTS = """  <n>-<view>.parts   the same build from the same camera, with each declared part
                     of the building in one flat colour and black between them.
                     One colour is one part throughout the set, so the same
                     colour in two images is the same thing. Use it to answer
                     "which part is that" and "is this part there at all" --
                     including the question the other images are worst at: two
                     things that ought to be identical come out as two colour
                     maps you can lay side by side. The colours are arbitrary
                     labels and say NOTHING about material; do not report them."""


KIND_MESH = """  <n>-<view>.mesh    a Google Earth photogrammetry capture of the real building,
                     rendered from the same camera as the build image with the
                     same number. Trustworthy for bulk, height and proportion; it
                     smooths small detail away, and its ground, trees and the
                     square-cut edges of the capture are noise. The capture was
                     flown, so it never saw under a roof or a canopy: in shots
                     taken from inside the building it is smeared and
                     half-invented, and for those you should weigh the
                     photographs instead."""


KIND_SOLID = """  <n>-<view>.mesh-solid  the same reference from the same camera, shaded flat in
                     one colour with cavity shading and no texture at all. It is
                     the reference's answer to the ink drawing: photogrammetry
                     glues a photograph of a wall onto whatever shape it
                     reconstructed, and the photograph hides the shape. Read
                     *form* here -- where a roof steps, whether a face is one
                     plane or two, what stands proud of a facade, what is a
                     separate box on a roof -- and read material off the
                     textured image beside it. Where the two disagree about
                     whether something is there, the solid pass is the geometry
                     and the texture is a picture painted on it."""


KIND_MODEL = """  <n>-<view>.mesh    the 3D model this build was made from, rendered from the
                     same camera as the build image with the same number. It is
                     the authority for shape and for proportion, so where the
                     build and the model differ, the build is wrong. It says
                     nothing about material unless it is textured."""


KIND_PHOTO = """  90-photo-<nn>      a photograph of the real building, from a viewpoint of its
                     own. Trustworthy for material, colour, glazing and rhythm,
                     and the only trustworthy reference for anything a capture
                     could not see from the air."""


# Greybox photos answer the four form questions, not the material ones. The
# photo-review sentence above is poison here: a reviewer told that photographs
# are trustworthy for colour reports that the greybox has no colour.
KIND_PHOTO_FORM = """  90-photo-<nn>      a photograph of the real building, from a viewpoint of its
                     own. Trustworthy for volume, proportion, the shape of the
                     plan and where the parts stand. Not for material, colour
                     or finish -- those are absent from the greybox on
                     purpose."""


# The photo-review solid pass tells the reviewer to read material off the
# textured image beside it. A greybox has no material to read.
KIND_SOLID_FORM = """  <n>-<view>.mesh-solid  the same reference from the same camera, shaded flat in
                     one colour with cavity shading and no texture. Read form
                     here -- where a roof steps, whether a face is one plane or
                     two, what stands proud of a facade."""


KIND_DRAWING = """  80-drawing-<nn>    a drawing of the building -- a plan, an elevation, a
                     section or a sketch. It shows what was intended, at a scale
                     that may or may not be stated, and from a viewpoint no
                     camera has. Read it for rhythm, for how the parts are
                     divided, and for what exists; where it disagrees with a
                     photograph, the photograph is what was built."""


PAIRING = (" Each numbered pair is the same camera in both models, but the two "
           "models were\nfitted to different sources, so the framing agrees to a "
           "few percent and not exactly.")


# Printed instead of PAIRING when nothing recorded which way the reference
# stands to the plan. Promising "the same camera" when it may not be is worse
# than promising nothing: the reviewer explains the difference between two sides
# of a building as a fault in the build, at high confidence, and the finding is
# about nothing at all.
UNPAIRED = (" The numbered images are meant to be the same camera in both "
            "models, but nothing in\nthis run could confirm the two models "
            "stand the same way round. Check that each pair\nshows the same "
            "side of the building before comparing them, and say so if one "
            "does not.")


# The light is deliberate and the reviewer cannot know that. Two rounds on one
# building returned "one long face is a blank grey wall" and "the tonality is
# inverted"; both were the render's own lighting, which separates a roof from a
# wall by brightness so that the joint between two volumes reads at all.
LIGHT = """The build renders are lit so that a roof and a wall separate in
brightness. A face in shadow is a face in shadow and not another material: judge
material by HUE, and call a colour wrong only when the hue is wrong."""


# Asked because nothing else asks it. Every other question here is
# build-against-reference; this one is about relations inside the building. A
# build can satisfy every external comparison while making two parts identical
# that the real building deliberately distinguishes -- on one hotel the left
# tower is balconied and the right one a blank shaft, the build made them twins,
# the gate passed them (same silhouette, same profile), and nobody asked.
INTERNAL = """Then one more comparison, which is not against the references but
inside the building. Where the real building makes two parts DIFFERENT -- one
tower balconied and the other blank, one wing glazed and the other solid, one
end tall and the other stepped -- does the build make them different too, and by
the same means? And where the real building repeats a part unchanged, does the
build repeat it? Report both directions: sameness where there should be
difference, and difference where there should be sameness.

And the ground: what stands on the terraces, the decks and the courts. Parasols,
loungers, hedges, planting, kerbs, the trees close to the water. These are as
much a part of how a building reads as its facade, a photograph is the authority
for whether they exist and how many, and a capture flown from the air answers
neither. Report an empty deck the way you would report a missing storey -- as a
count and a proportion, never in metres."""


# Said only where there is a capture to say it about. A reviewer told to ignore
# surrounding vegetation in a folder that contains none spends its attention
# looking for some.
NOISE_CAPTURE = (" Ignore the capture's surrounding terrain and vegetation and "
                 "its cut edges.")


NOISE_MODEL = (" The model has no material and no surroundings; judge shape and "
               "proportion against it, and material against the photographs.")


NOISE_MODEL_FORM = (" The model has no surroundings; judge shape and "
                    "proportion against it.")


PROMPT = """You are reviewing a Minecraft recreation of a real building.

{description}

You are given {count} images, each labelled before it:

{kinds}

Every render is a perspective camera, so near things are larger than far ones and
verticals converge; do not read that as a change in proportion.{pairing}

The viewpoints:

{views}

Compare the build against the references and report what is WRONG with the build.
Report each fault once, at the viewpoint that shows it best -- do not repeat a
finding for every image it appears in. For each finding give:
 - what is wrong, in one sentence;
 - which images show it, by name;
 - what it should be instead, and which image says so;
 - how wrong, as a count or a proportion -- three bays where there are five,
   half the height of its neighbour. Not in metres: you have no scale, and a
   finding in metres is answered by a section you cannot see.
 - your confidence: high, medium or low.

Rank the findings by severity: anything that changes how the building reads goes
first, fine detail last.

{internal}

{light}

Ignore differences that are only the medium: blocky staircasing along diagonals,
the block grid, the dark background of the build renders, and the difference
between a render and a photographic lens.{noise}

**Colour and material are not the medium.** Report them every time they are
wrong, including when you suspect no block matches: naming the colour you see is
your job, and finding a block for it is not. A review that stays silent about
colour because the palette is limited has skipped half of what it is for.

Be specific and be brief.
"""


GREYBOX_PROMPT = """You are reviewing a greybox of a real building -- the volumes
and their standing, with no windows, no balconies, no roof covering and no
materials. Those absences are not defects. Do not report them.

{description}

You are given {count} images, each labelled before it:

{kinds}

Every render is a perspective camera, so near things are larger than far ones and
verticals converge; do not read that as a change in proportion.{pairing}

The viewpoints:

{views}

Answer four questions and no others:

1. The volumes. How many parts the build has and how many the reference has;
   where one has a part the other does not.
2. The proportions. Length to width, height to length, the parts against each
   other.
3. The plan shape. Rectangular stays rectangular, round stays round, a wing
   goes the same way as on the reference.
4. The placement. How the parts stand relative to each other, the gaps, what
   abuts what.

A part that stands conspicuously lower than its neighbours is a placeholder
whose height has not been measured yet. Do not discuss its height. Its
footprint and position are fair game.

Do not report on material, colour, palette or any finish. They are not there
by construction. A finding of "no windows" on a greybox is a finding that you
are looking at a greybox.

Report each fault once, at the viewpoint that shows it best -- do not repeat a
finding for every image it appears in. For each finding give:
 - what is wrong, in one sentence;
 - which images show it, by name;
 - what it should be instead, and which image says so;
 - how wrong, as a count or a proportion -- three volumes where there are five,
   half the height of its neighbour. Not in metres: you have no scale, and a
   finding in metres is answered by a section you cannot see.
 - your confidence: high, medium or low.

Rank the findings by severity: anything that changes how the building reads goes
first, fine detail last.

Ignore differences that are only the medium: blocky staircasing along diagonals,
the block grid, the dark background of the build renders, and the difference
between a render and a photographic lens.{noise}

Be specific and be brief.
"""


# Words that point at finish rather than at form. Any one of them in a greybox
# prompt is a more specific instruction than the four questions, and a naive
# reviewer follows the more specific one -- it goes looking for the windows the
# greybox does not have and reports them missing.
#
# The list can only ever be approximate, and that is survivable because of
# which way it fails: a sentence wrongly dropped costs the reviewer some
# context it could have used, and a sentence wrongly kept costs a round of
# findings about nothing. So it is written wide, and substrings rather than
# words -- "balcon" catches balcony, balconied and balconies.
_FORM_POISON = (
    "glazing", "bays", "rhythm", "material", "colour", "color",
    "palette", "window", "finish", "cladding", "glass", "balcon",
    "concrete", "brick", "stucco", "render", "timber", "stone", "steel",
    "paint", "tile", "shingle", "louvre", "louver", "awning", "clad",
)


_FORM_LOOK = ("The volumes, their proportions, the shape in plan, and "
              "where the parts stand")


# Printed where the building's own sentence would go, when every sentence of it
# named finish. Saying nothing there would leave the reviewer unable to tell a
# building nobody described from one whose description did not survive.
_FORM_NO_DESCRIPTION = (
    "Nothing here describes this building. What was written for it names "
    "material and finish, which a greybox has none of, so it was left out. "
    "Read the volumes off the images.")


def form_reading(shot) -> str:
    """A viewpoint caption that names only form.

    The photo-review `reading` is written to point at glazing, bays and
    rhythm. Pasting it into a greybox prompt tells a naive reviewer to
    look at exactly what is forbidden. The first sentence usually names
    the camera; it is kept when it is clean. The question is replaced.
    """
    text = (getattr(shot, "reading", None) or "").strip()
    first = text.split(".", 1)[0].strip() if text else ""
    lowered = first.lower()
    if first and not any(word in lowered for word in _FORM_POISON):
        return f"{first}. {_FORM_LOOK}."
    return f"{_FORM_LOOK}."


def form_description(text: str) -> str:
    """The building's own description, with anything about finish taken out.

    The greybox prompt carried no description at all for a while, and that was
    the wrong half of the trade. The reviewer's first question is how many
    parts the build has against how many the reference has, and answering it
    off a photogrammetry capture without being told what the building IS means
    guessing which blobs are the building and which are its neighbours. That
    is exactly what `check_written` refuses an unwritten `DESCRIPTION` for.

    What could not go in as written is the rest of it: the skeleton asks an
    author for the volumes, **what they are made of**, and how they stand -- so
    a description written to that instruction names material, and a material
    sentence in this prompt beats three general forbids, the same way a camera
    caption did.

    So it goes in a sentence at a time, and only the sentences that name no
    finish. A sentence naming both -- "two towers of white concrete stand
    either side of a low wing" -- is lost whole, which loses form the reviewer
    could have used and leaks nothing, and that is the trade being made on
    purpose.
    """
    text = (text or "").strip()
    kept = [part.strip() for part in re.split(r"(?<=[.!?])\s+", text)
            if part.strip()
            and not any(word in part.lower() for word in _FORM_POISON)]
    return " ".join(kept) if kept else _FORM_NO_DESCRIPTION


@dataclass(frozen=True)
class Shot:
    """One perspective camera, given as fractions of the building's own extent.

    `eye` and `target` are `(fu, fv, height)`: the first two are fractions of the
    frame's u and v extents, so 0.5 is halfway along and 0 and 1 are the ends;
    the third is metres above the ground, which is not a fraction of anything.

    Fractions rather than metres because the same camera has to be placed in two
    frames that were fitted to two different sources -- the same building, read
    off a drawing and off a photogrammetry capture, differing by a few per cent.
    A camera given in metres would land in a court in one and inside a wall in the
    other; given in fractions it lands in the same *place* in both, which is what
    the comparison needs.
    """

    name: str
    eye: tuple[float, float, float]
    target: tuple[float, float, float]
    fov: float
    reading: str


@dataclass(frozen=True)
class Outside:
    """An exterior camera given as a direction to look from, not a place to stand.

    Where a camera outside the building should stand is not a thing worth
    choosing by hand: it is however far back the whole building fits from, and
    guessing at it produces frames with a corner cut off or the subject lost in
    the middle. So the direction is chosen -- `azimuth` around the building and
    `pitch` above the horizon, the same convention `render.View.orbit` uses -- and
    the distance is solved for.

    **`azimuth` is not a compass bearing.** It turns around the building's own
    long axis: 0 stands off the far end past the +u extreme, 90 out beyond the
    +v flank. On a crop whose building sits thirty degrees off the map's north
    -- which is most of them, because a street grid is not a compass -- naming a
    camera `east` or `beach` off the azimuth is naming it off the wrong angle,
    and the mistake survives every check except looking at the render. `Review.holds`
    prints which declared parts each camera has in shot, which settles it from
    the geometry before anything is rendered.
    """

    name: str
    azimuth: float
    pitch: float
    fov: float
    target: tuple[float, float, float]
    reading: str
    margin: float = 1.02


def orbit_basis(azimuth: float, pitch: float):
    """The same basis `render.View.orbit` builds, for measuring against."""
    view = render.View.orbit(azimuth, pitch)
    return view.forward, view.right, view.up


def _screen_box(corners, eye, basis):
    """Where the building's corners land on the film, in half-frames.

    Returns the spans in tangent units -- an offset of 1 is exactly one whole
    half-angle -- or None if any corner has got behind the lens, which is how the
    search below knows it has come too close.
    """
    forward, right, up = basis
    xs, ys = [], []
    for corner in corners:
        offset = [corner[i] - eye[i] for i in range(3)]
        depth = sum(offset[i] * forward[i] for i in range(3))
        if depth < 1e-3:
            return None
        xs.append(sum(offset[i] * right[i] for i in range(3)) / depth)
        ys.append(sum(offset[i] * up[i] for i in range(3)) / depth)
    return (min(xs), max(xs)), (min(ys), max(ys))


def frame_camera(entry: Outside, extent: tuple[float, float, float],
                 aspect: float, rounds: int = 4):
    """Where an exterior camera has to stand for the whole building to be in shot.

    Two things are solved at once and they are not independent: how far back to
    stand, and where to aim. Fitting the distance with a fixed aim leaves the
    building crammed into one side of the frame and a third of the image empty;
    recentring the aim then changes what has to fit. So they are alternated, four
    times, which is more than enough for a box seen from outside itself.

    The distance is found by halving rather than by formula because the fit
    condition, once recentring is allowed, is the *span* of the projected corners
    and not any one corner's offset -- and a span is not linear in the distance.
    Whether the building fits is monotone in it, though, which is all halving
    needs.
    """
    basis = orbit_basis(entry.azimuth, entry.pitch)
    forward = basis[0]
    tan_h = math.tan(math.radians(entry.fov) * 0.5)
    tan_v = tan_h / aspect
    corners = list(itertools.product((0.0, extent[0]), (0.0, extent[1]),
                                     (0.0, extent[2])))
    target = [entry.target[0] * extent[0], entry.target[1] * extent[1],
              entry.target[2]]

    def at(distance):
        eye = [target[i] - distance * forward[i] for i in range(3)]
        return eye, _screen_box(corners, eye, basis)

    def fits(distance):
        _, box = at(distance)
        if box is None:
            return False
        (x0, x1), (y0, y1) = box
        return (x1 - x0) <= 2.0 * tan_h * entry.margin \
            and (y1 - y0) <= 2.0 * tan_v * entry.margin

    eye = None
    for _ in range(rounds):
        far = max(extent)
        while not fits(far):
            far *= 2.0
            if far > 1e5:
                raise SystemExit(f"{entry.name}: no distance frames the building")
        near = 0.0
        for _ in range(40):
            middle = (near + far) * 0.5
            if fits(middle):
                far = middle
            else:
                near = middle
        eye, box = at(far)
        (x0, x1), (y0, y1) = box
        # Slide the whole camera sideways -- not turn it, which would tilt the
        # verticals -- until the building sits in the middle of the film.
        reach = sum((corners[0][i] - eye[i]) * forward[i] for i in range(3))
        for i in range(3):
            target[i] += reach * ((x0 + x1) * 0.5 * basis[1][i]
                                  + (y0 + y1) * 0.5 * basis[2][i])
    return eye, target


def blender() -> str | None:
    """Blender, from the one place that knows where it lives."""
    from . import blender as finder

    return finder.find()


def place(frame, point: tuple[float, float, float]) -> tuple[float, float, float]:
    """A shot's fractions, in one frame's metres."""
    fu, fv, height = point
    return (fu * frame.extent_u, fv * frame.extent_v, height)


def shot_spec(shot: Shot, mesh_frame, datum: float,
              turn: tuple[bool, bool] = (False, False)) -> str:
    """One `--shot` argument for the Blender script, in its world metres.

    The mesh frame is fitted in the export's (east, south) plane, so a point in
    it converts to Blender's axes as X = east, Y = -south, Z = height above the
    datum -- the datum being where the build's own ground sits.

    `turn` is the registration's flips, and without it this whole module tells
    the reviewer a lie. A camera is given as fractions of the frame so that it
    lands in the same *place* in two frames fitted to two different sources --
    which works only while those frames point the same way. Two independent fits
    of one building often do not: three buildings of five in this repository
    stand mirrored or half a turn round, recorded as `registration.turned`. On
    those, fu=0.2 is one end of the building in the plan and the other end in
    the capture, so the pair labelled "the same camera" showed the pool court in
    one image and the street front in the other -- and the prompt said, in so
    many words, that the framing agreed to a few per cent.

    A reviewer given that pair compares two different sides of a building and
    has no way to know. It is the worst kind of fault this stage can have: it
    does not fail, it produces confident findings about nothing.
    """
    numbers = []
    flip_u, flip_v = turn
    for point in (shot.eye, shot.target):
        fu, fv, height = point
        if flip_u:
            fu = 1.0 - fu
        if flip_v:
            fv = 1.0 - fv
        u, v, height = place(mesh_frame, (fu, fv, height))
        east, south = mesh_frame.to_world(u, v)
        numbers += [east, -south, datum + height]
    numbers.append(shot.fov)
    return f"{shot.name}:" + ",".join(f"{n:.3f}" for n in numbers)


class Review:
    """One building's review: where its files are, and how it gets looked at.

    `plan` is the cameras -- `Outside` for a direction to look from, `Shot` for
    a place to stand -- and `description` is the one sentence the reviewer is
    told about the building. Everything else here is the same for every building
    there has ever been, which is why it lives in the library and not in a copy.

    `schematic`, `where` and `greybox` are how a greybox review leaves the
    finished build's folder alone. Defaults keep the photo review: the
    finished schematic, `out/review/`, drawings and the colour prompt.
    """

    def __init__(self, paths, plan, description: str,
                 size: tuple[int, int] = SIZE, *,
                 schematic: Path | None = None,
                 where: Path | None = None,
                 greybox: bool = False):
        self.paths = paths
        self.plan = tuple(plan)
        self.description = description
        self.size = size
        self.schematic = Path(schematic) if schematic is not None else None
        self.out = Path(where) if where is not None else paths.OUT / "review"
        self.greybox = greybox
        # Which round this run is. Set by `archive`, which counts what is
        # already kept; 1 until then, because a first run has nothing to keep.
        self.round = 1
        # Where `tools/render_orthos.py` lives: `paths.HERE` is
        # buildings/<name>, so two levels up is the project root.
        self.repo = Path(paths.HERE).resolve().parents[1]

    def built_path(self) -> Path:
        """The schematic this run renders, not whatever schematic_of prefers.

        schematic_of never returns the greybox. A greybox review that asked it
        would render the finished build, or refuse, and write the stamp line
        against the wrong file.
        """
        if self.schematic is not None:
            return self.schematic
        return schematic_of(self.paths)

    def resolve(self, extent: tuple[float, float, float], aspect: float) -> tuple[Shot, ...]:
        """`self.plan` with every exterior direction turned into a place to stand."""
        out = []
        for entry in self.plan:
            if isinstance(entry, Shot):
                out.append(entry)
                continue
            eye, target = frame_camera(entry, extent, aspect)
            out.append(Shot(
                entry.name,
                eye=(eye[0] / extent[0], eye[1] / extent[1], eye[2]),
                target=(target[0] / extent[0], target[1] / extent[1], target[2]),
                fov=entry.fov,
                reading=entry.reading,
            ))
        return tuple(out)

    def check_cameras(self, model, frame, shots) -> None:
        """Refuse a camera standing inside the building.

        A perspective shot is given as fractions of the frame so that it lands
        in the same place in two models, and a fraction is easy to get wrong by
        a tenth. A tenth of a building is a wall: on one round a camera meant to
        look out from under an arcade stood inside one of its piers, and the
        render is a brown rectangle filling the frame. Nothing failed. The image
        went to the reviewer with a viewpoint description saying what it was
        supposed to show, and the findings that came back were about a building
        nobody could see.

        Cheap to test exactly, because the thing to test against is the
        schematic itself: if the block at the eye is not air, the camera is
        inside the building. A camera in a court, under a canopy or in a loggia
        is air and passes, which is right -- those are the shots worth having.
        """
        from .schem import AIR

        inside = []
        for shot in shots:
            u, v, height = place(frame, shot.eye)
            x, z = frame.to_world(u, v)
            x, y, z = int(x), int(height), int(z)
            if not (0 <= x < model.width and 0 <= z < model.length
                    and 0 <= y < model.height):
                continue
            block = model.get(x, y, z)
            if block and block != AIR:
                inside.append((shot.name, block, (x, y, z)))
        if inside:
            raise SystemExit(
                "these cameras stand inside the build, and each of them renders "
                "the inside of a block filling the frame:\n"
                + "\n".join(f"  {name}: {block} at ({x}, {y}, {z})"
                            for name, block, (x, y, z) in inside)
                + "\nMove the eye out, or raise it above the part it is in. A "
                "shot from inside a court or under a canopy stands in air and "
                "is not caught by this.")

    def holds(self, frame, shots, groups) -> None:
        """Print which side of the building each camera stands on, and what is
        nearest it.

        A camera's name is written by whoever placed it and is checked by
        nothing. `Outside.azimuth` turns around the **building's own long axis**
        -- the same convention `render.View.orbit` uses -- and on a crop whose
        building stands 33 degrees off the map's north, 90 is the street and 270
        is the beach. Three cameras in one round were named for the wrong side
        of the building, and every one of them was found by looking at a render
        afterwards, which costs minutes and a person.

        This is the same fact taken off the geometry, in a line each, before
        anything renders: where the eye stands in the frame's own fractions,
        which flank or end that is, and the declared parts nearest to it. A
        camera called `beach` whose eye is beyond the flank the deck is not on
        is wrong, and the line says so without an image.

        Deliberately not "what is inside the film". Every exterior camera is
        solved to fit the whole building, so every one of them holds every part
        and the answer is 100 per cent six times over, which distinguishes
        nothing. What separates one exterior camera from another is which side
        of the building it is on.
        """
        if not groups:
            return
        print("[review] where each camera stands, from the geometry:")
        for shot in shots:
            fu, fv, height = shot.eye
            eye = place(frame, shot.eye)
            where = []
            if fv < 0.0:
                where.append("off the -v flank")
            elif fv > 1.0:
                where.append("off the +v flank")
            if fu < 0.0:
                where.append("past the -u end")
            elif fu > 1.0:
                where.append("past the +u end")
            if not where:
                where.append("over the plan itself")

            near = []
            for name, mask in groups:
                cells = mask.cells()
                if not cells:
                    continue
                step = max(1, len(cells) // 400)
                best = min(
                    (eye[0] - u) ** 2 + (eye[1] - v) ** 2
                    for u, v in (frame.to_local(x + 0.5, z + 0.5)
                                 for x, z in cells[::step]))
                near.append((best ** 0.5, name))
            near.sort()
            names = ", ".join(f"{name} {far:.0f} m" for far, name in near[:4])
            print(f"           {shot.name}: eye at u {fu:+.2f}, v {fv:+.2f}, "
                  f"{height:.0f} m up -- {', '.join(where)}; nearest {names}")

    def _shown(self, path: Path) -> Path:
        try:
            return path.relative_to(self.repo)
        except ValueError:
            return path

    def _set_aside(self, leftover: Path) -> None:
        """Move a leftover journal out of `out/` once, without reading it.

        The freeze is that the old text is not rewritten and is not the new
        journal. Putting it above `out/` is the cheap half of that: `out/` is
        the folder the method document invites you to delete, and leaving the
        only copy there is how it dies on the next tidy.
        """
        aside = Path(self.paths.HERE) / LEFTOVER
        if aside.exists():
            print(f"[review] leftover journal still at {self._shown(leftover)}; "
                  f"{self._shown(aside)} already holds the copy that was moved "
                  "out of this folder, so this file is left alone")
            return
        leftover.replace(aside)
        print(f"[review] moved {self._shown(leftover)} to {self._shown(aside)} "
              "-- this folder no longer owns the journal, and the text was "
              "not rewritten")
        print(f"[review] nothing reads {self._shown(aside)}: the audit, the "
              "manual mode and this printout all look at "
              f"{self._shown(findings.path_of(self.paths))} and only there. It "
              "is kept text, not a ledger -- copy across whatever still applies.")

    def clear(self) -> None:
        """Empty the review folder. Does not unlink a leftover findings.md.

        A folder that accumulates is a folder where last week's render of a
        viewpoint that no longer exists sits beside this week's, gets counted in the
        prompt, and is read by a reviewer who was told that every numbered pair is
        the same camera in two models. The journal used to live here. It does
        not any more, and for two frozen buildings this file is the only copy.
        Deleting it is not leaving the freeze alone -- it is destroying the
        freeze. Move it out of `out/` once and leave the text as it is.
        """
        if not self.out.is_dir():
            return
        leftover = self.out / FINDINGS
        if leftover.is_file():
            self._set_aside(leftover)
        for stale in self.out.iterdir():
            if stale.is_file() and stale.name != FINDINGS:
                stale.unlink()
        # `rounds/` is a directory and survives by being one, which is the kind
        # of accident that stops being true the day somebody makes this
        # recursive. It is stated instead: the archive is the point.

    def render_build(self, model, frame, shots, groups=None) -> None:
        """The schematic, from each shot, in each way of looking at it.

        Three pictures per viewpoint, and the second two are not decoration.

        The shaded render answers what something is made of and nothing else
        does. It is also the worst picture in the folder for every other
        question: the sun is deliberately hard so that a roof and a wall never
        take the same shade, the palette is the palette, and a reviewer is
        asked in the prompt to judge material "by hue, not by lightness" --
        which is to say, asked to do a conversion in their head.

        So the same camera also draws the building as a line drawing, where
        shape and rhythm and silhouette are all there is, and as a map of its
        declared parts in flat colour, where "which part is that" and "is this
        part here at all" are answered by looking rather than inferred. Two
        towers that should match become two colour maps to lay side by side.

        Same geometry, same camera, same pixel, so a finding on one lands on the
        same place in the others.
        """
        self.out.mkdir(parents=True, exist_ok=True)
        for shot in shots:
            view = render.View.shot(place(frame, shot.eye), place(frame, shot.target),
                                    fov=shot.fov, name=shot.name)
            for mode, suffix in (("shaded", "build"), ("ink", "ink"),
                                 ("parts", "parts")):
                if mode == "parts" and not groups:
                    continue
                out = self.out / f"{shot.name}.{suffix}.png"
                render.render(model, out, view=view, frame=frame,
                              size=self.size, mode=mode, groups=groups)
                print(f"[review] {out.relative_to(self.repo)}")

    def check_written(self) -> None:
        """Refuse a review whose tables are still the template's.

        Three buildings of five went to a reviewer with `DESCRIPTION` left as
        the literal `<what this building is, in a sentence or two...>`. It went
        into `prompt.txt` verbatim, angle brackets and all, and the reviewer --
        which is given pictures and nothing else -- did not know it was looking
        at a hotel, that there were two towers, or that the towers differ. It
        then reported what it could, which was less than half of what was wrong.

        The same refusal `derive` makes for a declared number with no source,
        for the same reason: the unfilled case and the filled case look
        identical from the outside, and only one of them is a review.

        A `reading` left as the template's is checked too but only warned about:
        the shot still renders, and a generic reading blinds one viewpoint
        rather than the whole round.
        """
        if self.description.lstrip().startswith("<"):
            raise SystemExit(
                "DESCRIPTION in review.py is still the template's placeholder, "
                "and it goes into the prompt as written.\n"
                "The reviewer is given pictures and nothing else, so this "
                "sentence is the whole of what it knows: what the volumes are, "
                "what they are made of, and how they stand to each other. "
                "Three buildings were reviewed without it and every one came "
                "back with findings about the wrong things.\n"
                "Write it -- two or three sentences -- and run this again.")

        blind = [s.name for s in self.plan
                 if getattr(s, "reading", "").lstrip().startswith("<")]
        if blind:
            print("[review] ! no reading written for "
                  f"{', '.join(blind)}: the reviewer will be told nothing about "
                  "what to look at from those cameras")

    def measured(self) -> dict:
        """What `derive` fitted, so that this stage does not fit it again.

        The review used to read the capture, take `Mesh.ground()` for the datum
        and fit its own frame at a hard-coded six-metre floor. That is a second
        answer to a question that must have one, and it is the same fault the
        gate had before `Registration.measured`: the survey registers the plan
        against the reference once, every number in the build comes through that
        registration, and a second fit here points the cameras somewhere else.

        It also costs a minute of reading an OBJ nobody needs read.
        """
        found = getattr(self.paths, "DERIVED", None)
        if found is None or not Path(found).exists():
            return {}
        try:
            return json.loads(Path(found).read_text(encoding="utf-8"))
        except ValueError:
            return {}

    def turn_of(self, derived: dict) -> tuple[bool, bool]:
        """Whether the reference stands mirrored to the plan, from `derive`."""
        flip = (derived.get("registration") or {}).get("flip")
        if not flip:
            return (False, False)
        return (bool(flip[0]), bool(flip[1]))

    def mesh_frame_of(self, derived: dict):
        """The frame and datum `derive` fitted to the reference.

        Falls back to reading the capture and fitting one only when the survey
        recorded none -- which means a building measured before this existed, or
        a review run against a reference the survey never opened. The fallback
        says so, because a second fit is a second answer and the first one is
        supposed to be the only one.
        """
        from .frame import Frame

        found = (derived.get("mesh") or {}).get("frame")
        if found:
            # The extents as well as the origin and the angle. A camera in this
            # module is given as *fractions* of the frame it stands in -- that
            # is the whole mechanism by which one shot lands in the same place
            # in two frames fitted to two different sources -- and `Frame`
            # defaults both extents to zero. Rebuilt without them, every
            # fraction multiplied out to nought: eye and target collapsed onto
            # the frame's own origin, differing only in height, so Blender was
            # handed a camera standing on one corner of the capture looking
            # vertically down. Every `*.mesh.jpg` in every review came out as a
            # close-up of a roof, and the prompt went on saying the pair was
            # framed alike.
            extent = found.get("extent") or (0.0, 0.0)
            return (Frame(tuple(found["origin"]), float(found["angle"]),
                          float(extent[0]), float(extent[1])),
                    float(derived["mesh"]["datum"]))

        from .mesh import Mesh

        print("[review] ! derived.json records no fitted frame for the "
              "reference, so this is fitting its own. Re-run probes/derive.py: "
              "two fits of one pair point the cameras at two different places.")
        mesh = Mesh.read(self.paths.MESH)
        datum = mesh.ground()
        return mesh.frame(datum=datum, floor=6.0), datum

    def archive(self) -> Path | None:
        """Keep the last round's images before this one overwrites them.

        Without this a round leaves nothing behind, and two questions become
        unanswerable: did a fix change the picture at all, and did it break a
        viewpoint nobody was working on. The renderer is deterministic, so both
        are a pixel comparison -- see `tools/review_diff.py` -- and a comparison
        needs something to compare against.

        Cheap: a round is two dozen images and the folder is regenerable in
        full.
        """
        if not self.out.is_dir():
            return None
        images = [p for p in self.out.iterdir()
                  if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg")]
        if not images:
            return None
        rounds = self.out / "rounds"
        rounds.mkdir(parents=True, exist_ok=True)
        seen = [int(p.name) for p in rounds.iterdir()
                if p.is_dir() and p.name.isdigit()]
        self.round = (max(seen) if seen else 0) + 2
        into = rounds / f"{(max(seen) + 1 if seen else 1):02d}"
        into.mkdir()
        for image in images:
            shutil.copy2(image, into / image.name)
        # The journal is not in this folder. Copying a leftover `findings.md`
        # would keep a second copy that nothing writes and everything used to
        # trust.
        if (self.out / PROMPT_FILE).exists():
            shutil.copy2(self.out / PROMPT_FILE, into / PROMPT_FILE)
        print(f"[review] last round kept in {into.relative_to(self.repo)}")
        return into

    def render_mesh(self, mesh_frame, datum: float, shots,
                    geometry=None, turn: tuple[bool, bool] = (False, False)
                    ) -> bool:
        """The reference geometry, from the same cameras, through Blender.

        A capture and a 3D model go through the same script and come out looking
        very different, which is the point: one is a photographic surface with trees
        fused to it, the other is clean geometry. What each is worth is said in the
        prompt, not here.
        """
        exe = blender()
        command = [
            exe or "blender", "-b", "-P", str(self.repo / "tools" / "render_orthos.py"), "--",
            str(geometry or self.paths.MESH), "-o", str(self.paths.ORTHOS), "--only-extra",
            "--shot-size", str(self.size[0]), str(self.size[1]),
        ]
        for shot in shots:
            command += ["--shot", shot_spec(shot, mesh_frame, datum, turn)]

        if exe is None:
            print("[review] Blender is not installed where this expected it. Run:")
            print("  " + subprocess.list2cmdline(command))
            return False
        print("[review] " + subprocess.list2cmdline(command[1:]))
        return subprocess.run(command, cwd=self.repo).returncode == 0

    def collect(self, shots, reference=None, paired: bool = True) -> None:
        """One folder: both renders of every shot, the photographs, and the prompt.

        One folder and one prompt, because the review is one request. A reviewer that
        sees every angle at once can tell that a fault at one viewpoint and a fault at
        another are the same fault, and can weigh which viewpoint shows it best; split
        across requests it reports the same thing three times in three different sizes
        and no request holds the evidence to settle which is right.

        The mesh renders are re-encoded as JPEG on the way in. They are photographic
        -- every pixel differs from its neighbour -- so PNG stores them at over a
        megabyte each, and this folder is already two dozen images. The build renders
        stay PNG: they are flat colour, where PNG is both smaller and lossless, and
        they are the thing being judged.
        """
        self.out.mkdir(parents=True, exist_ok=True)

        # Both passes of every mesh shot, not just the textured one. Blender
        # already renders each camera twice -- the photographic texture, and a
        # flat single-colour pass with cavity shading -- and the second was
        # being left on the floor. It is the mesh's answer to the build's `ink`:
        # a photogrammetric texture is a picture of a wall glued to whatever
        # shape the reconstruction happened to make, and it hides the shape it
        # is glued to. The orthographic stage has read massing off the solid
        # pass since it was written, for exactly this reason; the perspective
        # shots had no reason to be different.
        meshes = solids = 0
        for shot in shots:
            # A textured photogrammetry pass is the windows the greybox does
            # not have. Greybox review takes only the solid pass -- form,
            # no photograph glued on.
            mesh = self.paths.ORTHOS / f"{shot.name}_tex.png"
            if mesh.exists() and not self.greybox:
                Image.open(mesh).convert("RGB").save(
                    self.out / f"{shot.name}.mesh.jpg", quality=90)
                meshes += 1
            solid = self.paths.ORTHOS / f"{shot.name}_solid.png"
            if solid.exists():
                # PNG, not JPEG: this pass is flat colour with a shading term,
                # so it compresses like the build renders rather than like a
                # photograph, and the edges are the whole of what it carries.
                Image.open(solid).convert("RGB").save(
                    self.out / f"{shot.name}.mesh-solid.png")
                solids += 1

        def gather(folder: Path) -> list[Path]:
            if not folder.is_dir():
                return []
            return sorted(p for p in folder.iterdir() if p.is_file()
                          and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"))

        def shrink(source: Path, target: Path, edge: int, quality: int) -> None:
            image = Image.open(source).convert("RGB")
            if max(image.size) > edge:
                scale = edge / max(image.size)
                image = image.resize(
                    (round(image.width * scale), round(image.height * scale)),
                    Image.LANCZOS)
            image.save(target, quality=quality)

        # Drawings stay out of a greybox folder even when they exist on disk.
        # A plan or an elevation is the wrong reference for volumes: the
        # reviewer starts counting bays and reporting missing windows.
        drawings = []
        if not self.greybox:
            drawings = gather(self.paths.DRAWINGS) + gather(self.paths.SKETCHES)
            for i, sheet in enumerate(drawings, start=1):
                shrink(sheet, self.out / f"80-drawing-{i:02d}.jpg", DRAWING_EDGE, 90)

        photos = gather(self.paths.PHOTOS)
        for i, photo in enumerate(photos, start=1):
            shrink(photo, self.out / f"90-photo-{i:02d}.jpg", PHOTO_EDGE, 88)

        images = sorted(p.name for p in self.out.iterdir()
                        if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        is_model = reference is not None and reference.name == "model"
        # Named only where they exist, like every other kind: the part map is
        # not made for a build that declared no manifest.
        inks = any(n.endswith(".ink.png") for n in images)
        parts = any(n.endswith(".parts.png") for n in images)
        has_ref = bool(meshes or solids)
        if self.greybox:
            kinds = ([KIND_BUILD]
                     + [KIND_INK_FORM] * inks
                     + [KIND_PARTS] * parts
                     + [KIND_SOLID_FORM] * bool(solids)
                     + [KIND_PHOTO_FORM] * bool(photos))
        else:
            kinds = ([KIND_BUILD]
                     + [KIND_INK] * inks
                     + [KIND_PARTS] * parts
                     + [KIND_MODEL if is_model else KIND_MESH] * bool(meshes)
                     + [KIND_SOLID] * bool(solids)
                     + [KIND_DRAWING] * bool(drawings)
                     + [KIND_PHOTO] * bool(photos))
        if not has_ref:
            noise = ""
        elif is_model:
            noise = NOISE_MODEL_FORM if self.greybox else NOISE_MODEL
        else:
            noise = NOISE_CAPTURE
        if self.greybox:
            views = "\n".join(f"  {s.name}  {form_reading(s)}" for s in shots)
        else:
            views = "\n".join(f"  {s.name}  {s.reading}" for s in shots)
        pairing = ("" if not has_ref else (PAIRING if paired else UNPAIRED))
        if self.greybox:
            text = GREYBOX_PROMPT.format(
                count=len(images), views=views,
                description=form_description(self.description),
                kinds="\n".join(kinds), noise=noise, pairing=pairing)
        else:
            text = PROMPT.format(
                count=len(images), views=views,
                description=self.description,
                kinds="\n".join(kinds), noise=noise,
                internal=INTERNAL, light=LIGHT, pairing=pairing)
        (self.out / PROMPT_FILE).write_text(text, encoding="utf-8")

        print(f"[review] {self.out.relative_to(self.repo)}: {len(images)} images")
        for name in images:
            print(f"           {name}")

        # A folder with nothing but the build in it is not a weak review but a
        # fabricated one: the only thing left to grade against is the reviewer's idea
        # of what the building ought to look like. Say so, and do not ask for one.
        # Drawings do not count for a greybox: they are not in the folder.
        if not has_ref and not photos and (self.greybox or not drawings):
            if self.greybox:
                print("[review] nothing to compare against: no reference renders "
                      f"(pass --mesh), no photographs in {self.paths.PHOTOS}.")
            else:
                print("[review] nothing to compare against: no reference renders "
                      f"(pass --mesh), no drawings, no photographs in {self.paths.PHOTOS}.")
            print("[review] not enough for a review; the renders are there to look "
                  "at, but do not send them to a reviewer on their own")
            return

        if not has_ref:
            if self.greybox:
                print("[review] ! no reference renders; whatever else is here has to "
                      "carry volume and proportion")
            else:
                print("[review] ! no reference renders; whatever else is here has to "
                      "carry bulk and height as well as material")
        if not photos:
            if self.greybox:
                print(f"[review] ! no photographs in {self.paths.PHOTOS}; "
                      "form is judged against the reference renders alone")
            else:
                print(f"[review] ! no photographs in {self.paths.PHOTOS}; nothing states the "
                      "material, and a capture cannot see under a roof")

        # The journal and its audit, at both gates. This block used to sit
        # inside the non-greybox branch, so gate 4 -- the one gate whose whole
        # artefact is a loop -- said nothing about the loop: no round count, no
        # open findings, no missing `Prevented by`, no abandoned-loop line. All
        # of it was recovered at gate 5, one gate late, by which time the
        # detail the greybox exists to precede had been drawn.
        journal = findings.path_of(self.paths)
        try:
            shown = journal.relative_to(self.repo)
        except ValueError:
            shown = journal
        print(f"[review] read every image in that folder against {PROMPT_FILE}, then "
              f"write the findings and what was done about each to {shown}")
        if self.greybox:
            print("[review] this round is gate 4: write it under "
                  "`## Gate 4 -- greybox`, as `### Round N -- agent`")
        # The state of the loop, printed rather than looked up. A round that
        # leaves findings open is a round that has not closed, and the count is
        # the only thing that says so out loud.
        for line in findings.lines(journal, after=self.round):
            print(f"[review] {line}")

        # What this stage renders is the schematic. What a person looks at is a
        # world somebody pasted it into, and the pipeline does not own that
        # boundary: a fault fixed here and not re-pasted there reads exactly
        # like a fault that was never fixed. It has happened.
        built = self.built_path()
        tally = self.paths.OUT / f"{built.stem}.stamp.md"
        if tally.exists():
            print(f"[review] the renders are of {built.name}. If a "
                  f"world is being judged instead, check it against "
                  f"{tally.name} first (//count) or re-paste.")

    def run(self, read, argv=None) -> int:
        """Render, collect, and say what to do with the folder.

        `read` is the plan, passed in rather than measured here: the building's
        `probes/derive.py` holds the tables that decide how a plan is read, and
        a second reader in this module would be a second answer to a question
        that must have one.
        """
        parser = argparse.ArgumentParser(description="the photo review")
        parser.add_argument("--mesh", action="store_true",
                            help="also render the reference geometry, which needs "
                                 "Blender")
        parser.add_argument("--greybox", action="store_true",
                            help="review the greybox into out/greybox/review/")
        parser.add_argument("--no-mesh", action="store_true",
                            help="skip the reference geometry (the command's fallback)")
        args = parser.parse_args(argv)
        if args.greybox:
            self.greybox = True
            if self.schematic is None:
                grey = getattr(self.paths, "GREYBOX", None)
                if grey is None:
                    raise SystemExit(
                        "--greybox needs paths.GREYBOX; this building has none")
                self.schematic = Path(grey)
            if self.out == Path(self.paths.OUT) / "review":
                self.out = Path(self.paths.OUT) / "greybox" / "review"

        built = self.built_path()
        if not built.exists():
            hint = ("run build.py --greybox first" if self.greybox
                    else "run build.py first")
            raise SystemExit(f"{built} is missing; {hint}")

        self.check_written()

        # The same freshness rule the gate applies, and for the same reason. A
        # stale schematic renders perfectly -- it was a real build of the same
        # building -- so a review of it reads as a review of the current one, and
        # the findings answer last week's question. Worse here than at the gate:
        # a person then spends an hour fixing faults that were fixed last week.
        recipe = Path(self.paths.HERE) / "build.py"
        if built.stat().st_mtime < recipe.stat().st_mtime:
            raise SystemExit(f"{built.name} is older than {recipe.name}; "
                             f"rebuild before reviewing")

        derived = self.measured()
        # Whether the pairing can be promised at all. `turn_of` reads the flips
        # the survey fitted; no survey means no answer, and no answer means the
        # prompt says so instead of claiming the cameras match.
        paired = bool((derived.get("registration") or {}).get("flip") is not None
                      or (derived.get("registration") or {}).get("needed") is False
                      or read.massing is not None)
        self.archive()
        self.clear()
        frame = read.frame
        model = Schematic.read(built)
        shots = self.resolve((frame.extent_u, frame.extent_v,
                              float(model.height)),
                             self.size[0] / self.size[1])
        # The manifest, if the build wrote one, so the part map has names to
        # colour by. Absent -- a build that declared nothing -- the shaded and
        # line renders still go out and the part map is simply not made, which
        # is the honest answer rather than a picture of one colour.
        # A leftover full-build schedule would colour the greybox with floors
        # and glazing that are not in it. Greybox finish writes no schedule.
        groups = None
        if not self.greybox and self.paths.SCHEDULE.exists():
            from .schedule import Schedule
            groups = [(name, held.mask) for name, held
                      in Schedule.load(self.paths.SCHEDULE).built.items()]
        # Both before the renders, because both are about whether the renders
        # are worth making: one refuses a camera that would fill the frame with
        # the inside of a wall, the other says which side of the building each
        # camera is actually on.
        self.check_cameras(model, frame, shots)
        self.holds(frame, shots, groups)
        self.render_build(model, frame, shots, groups)

        # survey() reads WITNESS off the already-imported derive module, so
        # a dropped reference stays dropped here too.
        reference = sources.survey(self.paths).reference
        if args.mesh:
            dropped = derived.get("witness")
            if reference is None and dropped:
                # The files are on disk and are not evidence. Saying "there is
                # none" here sends a reader to look for a missing OBJ they are
                # standing on, and the honest sentence is the building's own.
                raise SystemExit(
                    "--mesh renders the reference geometry, and this building "
                    "dropped its reference: WITNESS in probes/derive.py says "
                    f"{dropped!r}\n"
                    "The capture or the model may well be on disk; it is not "
                    "evidence for this building, so rendering it beside the "
                    "build would put two different buildings on one sheet. Run "
                    "without --mesh, and the review is the build against the "
                    "photographs and the drawings.")
            if reference is None:
                raise SystemExit(
                    "--mesh renders the reference geometry, and there is none: no "
                    f"{self.paths.MODEL.name} and no converted capture at {self.paths.MESH}. "
                    "Run without it, and the review will be the build against the "
                    "photographs and the drawings.")
            if read.massing is not None and reference.name == read.source.name:
                # The plan came out of this same file, so its frame addresses the
                # geometry exactly and no second fit is needed.
                self.render_mesh(read.massing.model_frame, read.massing.datum,
                                 shots, reference.path)
            else:
                self.render_mesh(*self.mesh_frame_of(derived), shots,
                                 reference.path, turn=self.turn_of(derived))

        self.collect(shots, reference, paired=paired)
        return 0


__all__ = ["FINDINGS", "LEFTOVER", "Outside", "PROMPT_FILE", "Review", "Shot",
           "form_description", "form_reading"]
