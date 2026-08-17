"""The greybox holds the volumes and nothing after them.

Two things can go wrong and both are quiet. A section left out of both lists
never runs, and the greybox comes back missing a wing that the full build has --
so the form gets agreed on a building that is not the one being made. A section
in both lists runs twice, and the second call wins wherever they disagree.

    python -m tools.greybox_selftest
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from blockwright.schem import Schematic
from tools.pipeline_selftest import MAPPED, make

ROOT = Path(__file__).resolve().parent.parent
KIND = "greybox"

# Blocks that belong to DETAIL and must not appear in a greybox. Glass is the
# clearest: nothing in GREYBOX has any reason to place a transparent block.
DETAIL_BLOCKS = ("stained_glass", "_pane", "_stairs", "_slab")

# The skeleton's own detail, which is not glass: a deck and a parapet stay
# invisible to the marks above if they leak into the greybox.
SKELETON_DETAIL = ("light_gray_concrete", "smooth_quartz")


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

        n_parts = len(json.loads(
            (where / "out" / "derived.json").read_text(encoding="utf-8"))["parts"])
        print(f"greybox has {n_parts} part(s) and no detail")
        return 0
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(where, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
