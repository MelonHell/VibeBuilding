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
import math
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import render, sources
from .schem import Schematic






# What the reviewer is told, and where the answer goes. The findings file is the
# ledger for the eyes, the way `blockwright.report` is the ledger for the numbers:
# a fault seen once and only ever said out loud cannot be tracked between runs,
# counted, or shown to have been fixed.
PROMPT_FILE = "prompt.txt"


FINDINGS = "findings.md"


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


KIND_MESH = """  <n>-<view>.mesh    a Google Earth photogrammetry capture of the real building,
                     rendered from the same camera as the build image with the
                     same number. Trustworthy for bulk, height and proportion; it
                     smooths small detail away, and its ground, trees and the
                     square-cut edges of the capture are noise. The capture was
                     flown, so it never saw under a roof or a canopy: in shots
                     taken from inside the building it is smeared and
                     half-invented, and for those you should weigh the
                     photographs instead."""


KIND_MODEL = """  <n>-<view>.mesh    the 3D model this build was made from, rendered from the
                     same camera as the build image with the same number. It is
                     the authority for shape and for proportion, so where the
                     build and the model differ, the build is wrong. It says
                     nothing about material unless it is textured."""


KIND_PHOTO = """  90-photo-<nn>      a photograph of the real building, from a viewpoint of its
                     own. Trustworthy for material, colour, glazing and rhythm,
                     and the only trustworthy reference for anything a capture
                     could not see from the air."""


KIND_DRAWING = """  80-drawing-<nn>    a drawing of the building -- a plan, an elevation, a
                     section or a sketch. It shows what was intended, at a scale
                     that may or may not be stated, and from a viewpoint no
                     camera has. Read it for rhythm, for how the parts are
                     divided, and for what exists; where it disagrees with a
                     photograph, the photograph is what was built."""


PAIRING = (" Each numbered pair is the same camera in both models, but the two "
           "models were\nfitted to different sources, so the framing agrees to a "
           "few percent and not exactly.")


# Said only where there is a capture to say it about. A reviewer told to ignore
# surrounding vegetation in a folder that contains none spends its attention
# looking for some.
NOISE_CAPTURE = (" Ignore the capture's surrounding terrain and vegetation and "
                 "its cut edges.")


NOISE_MODEL = (" The model has no material and no surroundings; judge shape and "
               "proportion against it, and material against the photographs.")


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
 - how wrong, in metres, storeys, or a count, wherever you can put a number on it;
 - your confidence: high, medium or low.

Rank the findings by severity: anything that changes how the building reads goes
first, fine detail last.

Ignore differences that are only the medium: blocky staircasing along diagonals,
the limited block palette, the dark background of the build renders, and the
difference between a render and a photographic lens.{noise}

Do not list anything the build gets right. Be specific and be brief.
"""


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


def shot_spec(shot: Shot, mesh_frame, datum: float) -> str:
    """One `--shot` argument for the Blender script, in its world metres.

    The mesh frame is fitted in the export's (east, south) plane, so a point in
    it converts to Blender's axes as X = east, Y = -south, Z = height above the
    datum -- the datum being where the build's own ground sits.
    """
    numbers = []
    for point in (shot.eye, shot.target):
        u, v, height = place(mesh_frame, point)
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
    """

    def __init__(self, paths, plan, description: str,
                 size: tuple[int, int] = SIZE):
        self.paths = paths
        self.plan = tuple(plan)
        self.description = description
        self.size = size
        self.out = paths.OUT / "review"
        # Where `tools/render_orthos.py` lives: `paths.HERE` is
        # buildings/<name>, so two levels up is the project root.
        self.repo = Path(paths.HERE).resolve().parents[1]

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

    def clear(self) -> None:
        """Empty the review folder, keeping the findings.

        A folder that accumulates is a folder where last week's render of a
        viewpoint that no longer exists sits beside this week's, gets counted in the
        prompt, and is read by a reviewer who was told that every numbered pair is
        the same camera in two models. `findings.md` survives: it is written by
        hand, it carries the dispositions of earlier rounds, and nothing else in
        here is worth keeping.
        """
        if not self.out.is_dir():
            return
        for stale in self.out.iterdir():
            if stale.is_file() and stale.name != FINDINGS:
                stale.unlink()

    def render_build(self, model, frame, shots) -> None:
        """The schematic, from each shot."""
        self.out.mkdir(parents=True, exist_ok=True)
        for shot in shots:
            view = render.View.shot(place(frame, shot.eye), place(frame, shot.target),
                                    fov=shot.fov, name=shot.name)
            out = self.out / f"{shot.name}.build.png"
            render.render(model, out, view=view, frame=frame, size=self.size)
            print(f"[review] {out.relative_to(self.repo)}")

    def render_mesh(self, mesh_frame, datum: float, shots,
                        geometry=None) -> bool:
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
            command += ["--shot", shot_spec(shot, mesh_frame, datum)]

        if exe is None:
            print("[review] Blender is not installed where this expected it. Run:")
            print("  " + subprocess.list2cmdline(command))
            return False
        print("[review] " + subprocess.list2cmdline(command[1:]))
        return subprocess.run(command, cwd=self.repo).returncode == 0

    def collect(self, shots, reference=None) -> None:
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

        meshes = 0
        for shot in shots:
            mesh = self.paths.ORTHOS / f"{shot.name}_tex.png"
            if mesh.exists():
                Image.open(mesh).convert("RGB").save(
                    self.out / f"{shot.name}.mesh.jpg", quality=90)
                meshes += 1

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

        drawings = gather(self.paths.DRAWINGS) + gather(self.paths.SKETCHES)
        for i, sheet in enumerate(drawings, start=1):
            shrink(sheet, self.out / f"80-drawing-{i:02d}.jpg", DRAWING_EDGE, 90)

        photos = gather(self.paths.PHOTOS)
        for i, photo in enumerate(photos, start=1):
            shrink(photo, self.out / f"90-photo-{i:02d}.jpg", PHOTO_EDGE, 88)

        images = sorted(p.name for p in self.out.iterdir()
                        if p.suffix.lower() in (".png", ".jpg", ".jpeg"))
        is_model = reference is not None and reference.name == "model"
        kinds = ([KIND_BUILD]
                 + [KIND_MODEL if is_model else KIND_MESH] * bool(meshes)
                 + [KIND_DRAWING] * bool(drawings)
                 + [KIND_PHOTO] * bool(photos))
        noise = "" if not meshes else (NOISE_MODEL if is_model else NOISE_CAPTURE)
        views = "\n".join(f"  {s.name}  {s.reading}" for s in shots)
        (self.out / PROMPT_FILE).write_text(
            PROMPT.format(count=len(images), views=views, description=self.description,
                          kinds="\n".join(kinds), noise=noise,
                          pairing=PAIRING if meshes else ""),
            encoding="utf-8")

        print(f"[review] {self.out.relative_to(self.repo)}: {len(images)} images")
        for name in images:
            print(f"           {name}")

        # A folder with nothing but the build in it is not a weak review but a
        # fabricated one: the only thing left to grade against is the reviewer's idea
        # of what the building ought to look like. Say so, and do not ask for one.
        if not meshes and not photos and not drawings:
            print("[review] nothing to compare against: no reference renders "
                  f"(pass --mesh), no drawings, no photographs in {self.paths.PHOTOS}.")
            print("[review] not enough for a review; the renders are there to look "
                  "at, but do not send them to a reviewer on their own")
            return

        if not meshes:
            print("[review] ! no reference renders; whatever else is here has to "
                  "carry bulk and height as well as material")
        if not photos:
            print(f"[review] ! no photographs in {self.paths.PHOTOS}; nothing states the "
                  "material, and a capture cannot see under a roof")

        print(f"[review] read every image in that folder against {PROMPT_FILE}, then "
              f"write the findings and what was done about each to {FINDINGS}")

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
        args = parser.parse_args(argv)

        if not self.paths.SCHEM.exists():
            raise SystemExit(f"{self.paths.SCHEM} is missing; run build.py first")

        # The same freshness rule the gate applies, and for the same reason. A
        # stale schematic renders perfectly -- it was a real build of the same
        # building -- so a review of it reads as a review of the current one, and
        # the findings answer last week's question. Worse here than at the gate:
        # a person then spends an hour fixing faults that were fixed last week.
        recipe = Path(self.paths.HERE) / "build.py"
        if self.paths.SCHEM.stat().st_mtime < recipe.stat().st_mtime:
            raise SystemExit(f"{self.paths.SCHEM.name} is older than {recipe.name}; "
                             f"rebuild before reviewing")

        self.clear()
        frame = read.frame
        model = Schematic.read(self.paths.SCHEM)
        shots = self.resolve((frame.extent_u, frame.extent_v,
                              float(model.height)),
                             self.size[0] / self.size[1])
        self.render_build(model, frame, shots)

        reference = sources.survey(self.paths).reference
        if args.mesh:
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
                from .mesh import Mesh

                mesh = Mesh.read(reference.path)
                datum = mesh.ground()
                self.render_mesh(mesh.frame(datum=datum, floor=6.0), datum,
                                 shots, reference.path)

        self.collect(shots, reference)
        return 0


__all__ = ["FINDINGS", "Outside", "PROMPT_FILE", "Review", "Shot"]
