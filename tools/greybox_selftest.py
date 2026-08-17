"""The greybox holds the volumes and nothing after them.

Two things can go wrong and both are quiet. A section left out of both lists
never runs, and the greybox comes back missing a wing that the full build has --
so the form gets agreed on a building that is not the one being made. A section
in both lists runs twice, and the second call wins wherever they disagree.

The review the volumes go to is checked here too, on the same fixture: that its
folder holds no textured reference pass, and that its prompt is built from the
form-only field and refuses when that field is unwritten. A greybox with a
prompt that says "glazing" is the same defect one step later.

    python -m tools.greybox_selftest
"""

from __future__ import annotations

import importlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from blockwright.schem import AIR, Schematic
from tools import fixture
from tools.pipeline_selftest import MAPPED, make

ROOT = Path(__file__).resolve().parent.parent
KIND = "greybox"

# Blocks that belong to DETAIL and must not appear in a greybox. Glass is the
# clearest: nothing in GREYBOX has any reason to place a transparent block.
DETAIL_BLOCKS = ("stained_glass", "_pane", "_stairs", "_slab")

# The skeleton's own detail, which is not glass: a deck and a parapet stay
# invisible to the marks above if they leak into the greybox.
SKELETON_DETAIL = ("light_gray_concrete", "smooth_quartz")

# What a filled-in pair of fields looks like. The skeleton asks for material in
# the first and forbids it in the second, so these are written to be told apart:
# no phrase is in both, and only the first names a finish. A greybox prompt
# holding any part of DESCRIBED is a greybox prompt holding DESCRIPTION.
DESCRIBED = ("Three boxes stand in a row, the middle one taller than its "
             "neighbours. They are clad in white concrete with glazed bays "
             "along the long face.")
FORMED = ("Three plain rectangles stand in a row along one face, the middle "
          "one about half again the height of its neighbours. They touch at "
          "their short ends with no gap.")


def run(*args: str) -> str:
    done = subprocess.run(
        [sys.executable, *args], cwd=ROOT, check=False,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        sys.stdout.write(done.stdout or "")
        sys.stderr.write(done.stderr or "")
        raise SystemExit(f"FAIL: {' '.join(args)} exited {done.returncode}")
    return done.stdout


def counts_of(path: Path) -> dict[str, int]:
    return dict(Schematic.read(path).counts())


def fixture_boxes() -> list[tuple[float, float, float, float, float]]:
    boxes = [(p[1], p[2], p[3], p[4], p[5]) for p in fixture.PARTS]
    boxes.append(fixture.OUTBUILDING)
    return boxes


def fixture_volume() -> float:
    return sum((u1 - u0) * (v1 - v0) * top for u0, u1, v0, v1, top in fixture_boxes())


def fixture_ring() -> float:
    # Outline cells of a rectangle, times height. The four corners are in
    # both side counts and come out once.
    n = 0.0
    for u0, u1, v0, v1, top in fixture_boxes():
        du, dv = u1 - u0, v1 - v0
        n += (2.0 * (du + dv) - 4.0) * top
    return n


def prove_occupancy(where: Path, grey: Path) -> str | None:
    """Fail if the greybox is hollow, or if GREYBOX never extruded a part.

    A ring and a volume produce two different WALL counts from the same
    boxes; the fixture's own geometry is those two numbers. A mid-height
    layer of each part being filled (not an outline) is the same fact
    asked another way, and catches a list that skipped a wing whose
    remaining stone still looks closer to a volume than a ring.
    """
    recipe = importlib.import_module(f"buildings._selftest_{KIND}.build")
    wall = counts_of(grey).get(recipe.WALL, 0)
    volume = fixture_volume()
    ring = fixture_ring()
    if abs(wall - volume) >= abs(wall - ring):
        return (f"FAIL: greybox holds {wall} WALL, closer to a ring "
                f"({ring:.0f}) than a volume ({volume:.0f})")

    site = recipe.Site(
        json.loads((where / "out" / "derived.json").read_text(encoding="utf-8")),
        greybox=True)
    model = Schematic.read(grey)
    for name in site.tops:
        interior = site.footprint(name).erode(recipe.THICK)
        if interior.count() == 0:
            return f"FAIL: {name} is too thin to have an interior"
        y = site.ground + max(1, (site.tops[name] - site.ground) // 2)
        empty = sum(1 for x, z in interior.cells() if model.get(x, y, z) == AIR)
        if empty:
            return (f"FAIL: {name} at y={y} is hollow "
                    f"({empty} of {interior.count()} interior cells empty)")
    return None


def prove_placeholder(where: Path) -> str | None:
    """Greybox stands an unmeasured part and marks it; the full build refuses.

    Run after occupancy. Occupancy counts the first greybox against the
    fixture's real heights; a placeholder on a tall wing shrinks that count
    toward the full-height ring, so this rebuild is not fed back into it.

    Forgetting a skyline median is the condition `Site` actually reads.
    Raising `PART_LEAST_HEIGHT` only drops assembled extras, and the
    template assigns that bar after `CAPTURE_PARTS`, so an insert there
    would not even stick.
    """
    derived_path = where / "out" / "derived.json"
    derived = json.loads(derived_path.read_text(encoding="utf-8"))
    measured = []
    for rec in derived.get("parts") or []:
        name = rec.get("name")
        entry = (derived.get("skyline") or {}).get(name) or {}
        if isinstance(entry, dict) and entry.get("median") is not None:
            measured.append((float(entry["median"]), name))
    if not measured:
        return "FAIL: derived.json has no part with a measured height to forget"
    # Tallest first, so the old mid-height sits above the one-storey stand
    # and leftover stone from the first greybox would still be visible.
    old_top, name = max(measured)
    derived["skyline"][name]["median"] = None

    recipe = importlib.import_module(f"buildings._selftest_{KIND}.build")
    site = recipe.Site(derived, greybox=True)
    want = site.ground + site.storey
    if name not in site.placeholder:
        return f"FAIL: {name} was not marked as a placeholder"
    if site.tops[name] != want:
        return (f"FAIL: placeholder {name} stands at {site.tops[name]} m, "
                f"not one storey ({want} m)")
    try:
        recipe.Site(derived, greybox=False)
    except SystemExit as why:
        text = str(why)
        if ("nothing states how tall" not in text or name not in text
                or "--greybox" not in text):
            return f"FAIL: the full Site refused the wrong way: {why}"
    else:
        return "FAIL: the full build accepted a part with no measured height"

    derived_path.write_text(json.dumps(derived), encoding="utf-8")
    said = run("-m", f"buildings._selftest_{KIND}.build", "--greybox", "--quick")
    if "placeholder" not in said or name not in said:
        return "FAIL: greybox did not mark a part with no measured height"

    grey = where / "out" / "greybox.schem"
    model = Schematic.read(grey)
    interior = site.footprint(name).erode(recipe.THICK)
    if interior.count() == 0:
        return f"FAIL: placeholder {name} is too thin to have an interior"
    y_low = site.ground + max(1, (site.tops[name] - site.ground) // 2)
    empty = sum(1 for x, z in interior.cells() if model.get(x, y_low, z) == AIR)
    if empty:
        return (f"FAIL: placeholder {name} at y={y_low} is hollow "
                f"({empty} of {interior.count()} interior cells empty)")
    y_old = site.ground + max(1, (int(old_top + 0.5) - site.ground) // 2)
    if y_old >= site.tops[name]:
        leftover = sum(
            1 for x, z in interior.cells() if model.get(x, y_old, z) != AIR)
        if leftover:
            return (f"FAIL: placeholder {name} still has stone at y={y_old} "
                    f"({leftover} of {interior.count()} cells) -- the first "
                    "greybox was not rebuilt")

    done = subprocess.run(
        [sys.executable, "-m", f"buildings._selftest_{KIND}.build"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    if done.returncode == 0:
        return "FAIL: the full build accepted a part with no measured height"
    text = (done.stdout or "") + (done.stderr or "")
    if "nothing states how tall" not in text or "--greybox" not in text:
        return f"FAIL: the full build refused without pointing at --greybox:\n{text}"
    return None


def plant(where: Path, name: str, value: str) -> bool:
    """Fill one of the skeleton's placeholder constants in `review.py`.

    A lambda for the replacement, not a string: the planted text is prose and
    `re.sub` would read a backslash or a `\\g` in it as a group reference.
    """
    recipe = where / "review.py"
    source = recipe.read_text(encoding="utf-8")
    swapped = re.sub(rf'{name} = "<.*?>"', lambda _: f'{name} = "{value}"',
                     source, flags=re.S)
    if swapped == source:
        return False
    recipe.write_text(swapped, encoding="utf-8")
    return True


def prove_form_refusal(where: Path) -> str | None:
    """`--greybox` refuses while FORM is unwritten, and says what it is for.

    The same refusal `DESCRIPTION` already carries, for the same reason: the
    unfilled case and the filled case look identical from the outside, and
    only one of them is a review. It is asserted separately from the
    description's because the two fire on different runs -- this one only on
    the greybox path, since gate 5 never interpolates FORM.

    DESCRIPTION is planted first so that the refusal under test is reached at
    all: `check_written` asks about the description before it asks about the
    form, and a fixture with neither written would prove the wrong one.
    """
    if not plant(where, "DESCRIPTION", DESCRIBED):
        return "FAIL: could not plant a DESCRIPTION in the fixture's review.py"

    done = subprocess.run(
        [sys.executable, "-m", f"buildings._selftest_{KIND}.review", "--greybox"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    if done.returncode == 0:
        return ("FAIL: the greybox review ran with FORM still the skeleton's "
                "placeholder")
    text = (done.stdout or "") + (done.stderr or "")
    if "FORM" not in text or "how many parts" not in text:
        return ("FAIL: the refusal did not name FORM or say what it is for, "
                "which is the half a reader acts on:\n" + text)
    return None


def prove_textured_skip(where: Path) -> str | None:
    """The reference's textured pass does not reach the greybox reviewer.

    `if mesh.exists() and not self.greybox` is one line, and it is the only
    thing keeping a photogrammetric texture -- which is to say windows, on a
    building that has none -- out of the folder a naive form reviewer is
    handed. Until now its whole evidence was a hand run recorded in a report,
    which is the kind of proof that stops being true without anyone noticing.

    No Blender: both passes are planted as flat images under `paths.ORTHOS`,
    exactly as Blender would leave them, and the review is run without
    `--mesh` so nothing tries to render. What is asserted is what the line is
    for -- the solid pass copied, the textured pass not -- plus the prompt
    around it, since a folder with no windows in it and a prompt that says
    "glazing" is the same defect one step later.

    The prompt half also asserts which field it was built from. FORM's own
    sentence has to be there and DESCRIPTION's has to be absent, so a prompt
    that went back to interpolating the description fails here even if the
    description it was handed happened to be clean.

    Runs after `prove_form_refusal`, which is what leaves DESCRIPTION filled
    in: the review refuses on that field before it looks at this one.
    """
    if not plant(where, "FORM", FORMED):
        return "FAIL: could not plant a FORM in the fixture's review.py"

    module = importlib.import_module(f"buildings._selftest_{KIND}.review")
    importlib.reload(module)
    shots = [shot.name for shot in module.PLAN]

    orthos = where / "out" / "mesh-clip" / "orthos"
    orthos.mkdir(parents=True, exist_ok=True)
    for name in shots:
        Image.new("RGB", (64, 48), (200, 40, 40)).save(orthos / f"{name}_tex.png")
        Image.new("RGB", (64, 48), (160, 160, 160)).save(orthos / f"{name}_solid.png")

    run("-m", f"buildings._selftest_{KIND}.review", "--greybox")

    folder = where / "out" / "greybox" / "review"
    textured = sorted(p.name for p in folder.glob("*.mesh.jpg"))
    if textured:
        return ("FAIL: the textured reference pass reached the greybox "
                "reviewer: " + ", ".join(textured))
    solid = sorted(p.stem.split(".")[0] for p in folder.glob("*.mesh-solid.png"))
    if solid != sorted(shots):
        return (f"FAIL: the solid reference pass was not copied: got {solid}, "
                f"expected {sorted(shots)}")

    prompt = (folder / "prompt.txt").read_text(encoding="utf-8").lower()
    leaked = [word for word in ("glazing", "bays", "rhythm", "concrete", "clad")
              if word in prompt]
    if leaked:
        return ("FAIL: the greybox prompt points at finish: "
                + ", ".join(leaked))
    if "three plain rectangles stand in a row" not in prompt:
        return ("FAIL: the greybox prompt does not carry FORM; the reviewer is "
                "left to guess how many parts it should see")
    if "three boxes stand in a row" in prompt:
        return ("FAIL: the greybox prompt carries DESCRIPTION, which is written "
                "to name material and belongs to gate 5")
    return None


def prove_overlap() -> str | None:
    """A section in both lists must refuse before it draws anything."""
    from buildings._template import build as recipe

    saved = recipe.DETAIL
    recipe.DETAIL = (recipe.ground,) + tuple(saved)
    try:
        recipe.main(["--greybox"])
    except SystemExit as why:
        text = str(why)
        if "ground" not in text or "GREYBOX" not in text or "DETAIL" not in text:
            return f"FAIL: overlap said the wrong thing: {why}"
    else:
        return "FAIL: a section in both lists should have been refused"
    finally:
        recipe.DETAIL = saved
    return None


def main() -> int:
    failed = prove_overlap()
    if failed:
        print(failed)
        return 1

    where = make(KIND, MAPPED, ("layout.png", "mesh"))
    try:
        run("-m", f"buildings._selftest_{KIND}.probes.derive")
        run("-m", f"buildings._selftest_{KIND}.build", "--greybox", "--quick")

        grey = where / "out" / "greybox.schem"
        full = where / "out" / "build.schem"
        if not grey.exists():
            print("FAIL: greybox.schem was not written")
            return 1
        if full.exists():
            print("FAIL: --greybox wrote build.schem")
            return 1

        failed = prove_occupancy(where, grey)
        if failed:
            print(failed)
            return 1

        run("-m", f"buildings._selftest_{KIND}.build", "--quick")
        if not full.exists():
            print("FAIL: build.schem was not written")
            return 1

        counts = counts_of(grey)
        marks = DETAIL_BLOCKS + SKELETON_DETAIL
        leaked = [name for name in counts if any(mark in name for mark in marks)]
        if leaked:
            print("FAIL: detail blocks in the greybox: " + ", ".join(leaked))
            return 1

        if counts_of(full) == counts:
            print("FAIL: the full build and the greybox hold the same blocks -- "
                  "DETAIL is empty or never ran")
            return 1

        failed = prove_placeholder(where)
        if failed:
            print(failed)
            return 1

        failed = prove_form_refusal(where)
        if failed:
            print(failed)
            return 1
        print("  the greybox review refuses an unwritten FORM")

        failed = prove_textured_skip(where)
        if failed:
            print(failed)
            return 1
        print("  the greybox review takes the solid reference pass and not "
              "the textured one, and its prompt carries FORM and no finish")

        n_parts = len(json.loads(
            (where / "out" / "derived.json").read_text(encoding="utf-8"))["parts"])
        print(f"greybox has {n_parts} part(s) and no detail")
        return 0
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(where, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
