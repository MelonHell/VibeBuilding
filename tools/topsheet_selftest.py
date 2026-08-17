"""The three panels of the top sheet cover the same ground.

The sheet exists so that a person can compare a footprint against the drawing it
came from and against the reference. It stops doing that the moment the panels
cover different areas: one real sheet put a reference clipped to the building
beside a build covering the whole plot, and the missing three quarters of the
site read as a framing choice.

    python -m tools.topsheet_selftest
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from blockwright.compare import BACKGROUND, Panel, aligned
from blockwright.schem import Schematic
from tools.pipeline_selftest import MAPPED, make

ROOT = Path(__file__).resolve().parent.parent
KIND = "topsheet"

# A tight clip of the reference, in metres. Well inside the fixture's map
# crop, so the sheet has something smaller to pad -- the case the function
# exists for.
CLIP = (24, 16)


def run(*args: str) -> str:
    done = subprocess.run(
        [sys.executable, *args], cwd=ROOT, check=False,
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode != 0:
        sys.stdout.write(done.stdout or "")
        sys.stderr.write(done.stderr or "")
        raise SystemExit(f"FAIL: {' '.join(args)} exited {done.returncode}")
    return done.stdout


def plant_orthos(where: Path) -> None:
    """A stub top (and east, so the existing compare loop has its view).

    Solid fill so `mesh_panel`'s trim leaves the extent alone: a flat image
    has no content bbox, and a trimmed-away stub would not be a small panel.
    """
    orthos = where / "out" / "mesh-clip" / "orthos"
    orthos.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", CLIP, (90, 96, 88)).save(orthos / "top_tex.png")
    Image.new("RGB", (40, 16), (90, 96, 88)).save(orthos / "east_tex.png")
    (orthos / "render_meta.json").write_text(json.dumps({
        "views": {
            "top": {
                "files": {"tex": "top_tex.png", "solid": "top_tex.png"},
                "metres_per_pixel": 1.0,
            },
            "east": {
                "files": {"tex": "east_tex.png", "solid": "east_tex.png"},
                "metres_per_pixel": 1.0,
            },
        }
    }), encoding="utf-8")


def prove_aligned() -> str | None:
    """Padding equalises extent; it does not stretch the smaller picture."""
    small = Image.new("RGB", (10, 10), (200, 40, 40))
    large = Image.new("RGB", (30, 20), (40, 40, 200))
    got = aligned([Panel(small, 1.0, "small"), Panel(large, 1.0, "large")])
    if [p.image.size for p in got] != [(30, 20), (30, 20)]:
        return f"FAIL: aligned sizes {[p.image.size for p in got]}, want (30, 20)"
    if any(p.metres_per_pixel != 1.0 for p in got):
        return "FAIL: aligned changed metres-per-pixel"
    # Centre of the small panel: (30-10)//2 = 10, (20-10)//2 = 5.
    if got[0].image.getpixel((15, 10)) != (200, 40, 40):
        return "FAIL: small panel's content is not where padding left it"
    if got[0].image.getpixel((0, 0)) != BACKGROUND:
        return ("FAIL: padding is not the sheet background -- the small "
                "panel was scaled to the large one")
    if got[1].image.getpixel((0, 0)) != (40, 40, 200):
        return "FAIL: the larger panel was not left at its own extent"

    # Different sampling, same ground: 5 m × 5 m at 0.5 m/px beside 20 m at
    # 1 m/px. Restating to the finer grid must grow the large panel's pixels,
    # not the small panel's metres.
    fine = Image.new("RGB", (10, 10), (200, 40, 40))
    coarse = Image.new("RGB", (20, 20), (40, 40, 200))
    got = aligned([Panel(fine, 0.5, "fine"), Panel(coarse, 1.0, "coarse")])
    if [p.image.size for p in got] != [(40, 40), (40, 40)]:
        return (f"FAIL: restated sizes {[p.image.size for p in got]}, "
                "want (40, 40) -- 20 m at 0.5 m/px")
    if any(abs(p.metres_per_pixel - 0.5) > 1e-9 for p in got):
        return "FAIL: aligned did not restate both panels to the finer grid"
    if got[0].image.getpixel((0, 0)) != BACKGROUND:
        return "FAIL: the 5 m panel was stretched to 20 m"
    return None


def prove_sheet(where: Path) -> str | None:
    sheet = where / "out" / "greybox" / "top.png"
    if not sheet.exists():
        return "FAIL: no top sheet was written"

    layer = where / "out" / "greybox" / "plan-ground.png"
    if not layer.exists():
        return "FAIL: no layered plan was written"
    upper = where / "out" / "greybox" / "plan-upper.png"
    if not upper.exists():
        return "FAIL: no upper-layer plan was written"

    # One pixel to one block, asserted against the canvas the build wrote
    # (greybox.schem width). derived.json["frame"] has origin / angle /
    # extent / staircase -- there is no "width" key to read.
    canvas_w = Schematic.read(where / "out" / "greybox.schem").width
    layer_w = Image.open(layer).width
    if layer_w != canvas_w:
        return (f"FAIL: plan-ground.png is {layer_w} px wide, "
                f"the canvas is {canvas_w} blocks -- not one pixel to one block")

    # The written sheet laid three equal panels: after aligned() the common
    # extent is the map (the largest of the three, in metres) and sheet()
    # draws each at 4 px/m. A sheet that skipped aligned() would be the map
    # plus two much smaller pictures, and come out short of three map-widths.
    map_w = Image.open(where / "input" / "layout.png").width
    want = 3 * map_w * 4 + 4 * 20
    got = Image.open(sheet).width
    if abs(got - want) > 6:
        return (f"FAIL: top.png is {got} px wide, three map-extent panels "
                f"at the sheet's 4 px/m would be {want} -- the panels do "
                "not share one extent")
    return None


def main() -> int:
    failed = prove_aligned()
    if failed:
        print(failed)
        return 1

    where = make(KIND, MAPPED, ("layout.png", "mesh"))
    try:
        plant_orthos(where)
        run("-m", f"buildings._selftest_{KIND}.probes.derive")
        run("-m", f"buildings._selftest_{KIND}.build", "--greybox")
        failed = prove_sheet(where)
        if failed:
            print(failed)
            return 1
        print("three panels, one extent")
        return 0
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(where, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
