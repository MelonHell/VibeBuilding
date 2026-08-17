"""Every part the build puts up is measured by something -- or said not to be.

The failure this exists for is not a wrong number. It is a part nobody measured
at all, standing in a report that reads exactly like a measured one: on the
building this was written after, `part villas: 842 of 842 cells` printed green
beside `north_tower stands where the plan says: the two overlap 0.910`, and the
first compares the build against its own declaration while the second compares
it against a measured plan. Nothing in the file told them apart.

The fixture's outbuilding is that case in miniature: it stands in the capture
and the map never drew it. A run that says nothing about it is the run this
guards against.

    python -m tools.coverage_selftest
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

from PIL import Image

from blockwright import model as model3d
from blockwright.frame import Frame
from blockwright.plan import decompose

from tools import fixture
from tools.pipeline_selftest import make

ROOT = Path(__file__).resolve().parent.parent
KIND = "coverage"


def build() -> Path:
    """Make the fixture building and measure it. Returns its out/ directory."""
    # Mapped inputs: the map draws the two wings, the mesh also has the
    # outbuilding. That is the disagreement this file exists to see.
    where = make(KIND, 'STRIPS = ("front", "back")\n', ("layout.png", "mesh"))
    subprocess.run(
        [sys.executable, "-m", f"buildings._selftest_{KIND}.probes.derive"],
        cwd=ROOT, check=True)
    return where / "out"


def _boxes_overlap(a: tuple[float, float, float, float],
                   b: tuple[float, float, float, float]) -> bool:
    return a[0] < b[1] and a[1] > b[0] and a[2] < b[3] and a[3] > b[2]


def _map_fill_box(layout: Path, frame: Frame
                  ) -> tuple[float, float, float, float] | None:
    """Axis-aligned (u, v) box of the pixels the map painted as building."""
    image = Image.open(layout)
    px = image.load()
    width, length = image.size
    us: list[float] = []
    vs: list[float] = []
    for z in range(length):
        for x in range(width):
            if px[x, z] != fixture.FILL:
                continue
            u, v = frame.to_local(x + 0.5, z + 0.5)
            us.append(u)
            vs.append(v)
    if not us:
        return None
    return (min(us), max(us), min(vs), max(vs))


def main() -> int:
    where = ROOT / "buildings" / f"_selftest_{KIND}"
    try:
        out = build()
        derived = json.loads((out / "derived.json").read_text(encoding="utf-8"))
        parts = [p["name"] for p in derived.get("parts", [])]

        layout = where / "input" / "layout.png"
        _, _, drawn = decompose(layout)

        # Confirm the capture really holds the extra mass. Without this the
        # fail below would also fire on a fixture that never wrote the
        # outbuilding, which is a different bug.
        capture = model3d.read(out / "mesh-clip" / "merged.obj")
        if len(capture.parts) <= len(drawn):
            print("FAIL: the capture has no mass the map did not draw; "
                  f"the mesh split into {len(capture.parts)} part(s), "
                  f"the map into {len(drawn)}")
            return 1

        # A part the map never drew is one whose footprint misses the painted
        # fill, in the plan frame derived.json already uses. Frame.fit_mask
        # points +u east and v follows, so a signed-v test (v1 < -10) can
        # miss the same building on the other side of the origin.
        info = derived["frame"]
        frame = Frame(tuple(info["origin"]), info["angle"],
                      info["extent"][0], info["extent"][1])
        fill = _map_fill_box(layout, frame)
        covered = []
        if fill is not None:
            covered = [p for p in derived.get("parts", [])
                       if not _boxes_overlap(
                           (p["u"][0], p["u"][1], p["v"][0], p["v"][1]),
                           fill)]
        if not covered:
            print(f"FAIL: outbuilding is in the capture and in no part list; "
                  f"the survey found {len(parts)} part(s): {', '.join(parts)}")
            return 1

        missing = [p for p in derived["parts"]
                   if not p.get("provenance", {}).get("plan")]
        if missing:
            print("FAIL: parts with no stated provenance: "
                  + ", ".join(p["name"] for p in missing))
            return 1

        print(f"all {len(parts)} part(s) measured by something")
        return 0
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(where, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
