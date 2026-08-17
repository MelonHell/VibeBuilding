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

from types import SimpleNamespace

from blockwright import model as model3d
from blockwright.frame import Frame
from blockwright.gate import Gate
from blockwright.grading import COVERAGE_SHARE, COVERAGE_STOREYS, Grading
from blockwright.mask import Mask
from blockwright.plan import Part, decompose
from blockwright.schedule import Declaration, Item, Schedule

from tools import fixture
from tools.pipeline_selftest import make

ROOT = Path(__file__).resolve().parent.parent
KIND = "coverage"
ROW = "every part is measured by something"


def _rect(width: int, length: int, x0: int, x1: int, z0: int, z1: int) -> Mask:
    mask = Mask(width, length)
    for z in range(z0, z1):
        for x in range(x0, x1):
            mask.set(x, z)
    return mask


def _grade(parts, items, built, excused=None, spacing=3.0):
    """Run `coverage` on a tiny schedule. `built` is name -> (mask, y0, y1)."""
    width = next(iter(built.values()))[0].width if built else 20
    length = next(iter(built.values()))[0].length if built else 20
    frame = Frame((0.0, 0.0), 0.0, float(width), float(length))
    named = {}
    for rec in parts:
        mask = rec.get("mask")
        if mask is None:
            continue
        named[rec["name"]] = Part(mask, frame)
    mass = Mask.union([p.mask for p in named.values()], width, length)
    read = SimpleNamespace(named=named, mass=mass, frame=frame)
    derived = {
        "parts": [{"name": r["name"],
                   "provenance": r.get("provenance", {})} for r in parts],
        "storeys": {"spacing": spacing},
    }
    sched = Schedule(Item(n, "what", "source") for n in items)
    for name, (mask, y0, y1) in built.items():
        sched.built[name] = Declaration(mask, y0, y1)
    sched.width, sched.length = width, length
    g = Gate("prove")
    cfg = SimpleNamespace(UNMEASURED={} if excused is None else excused)
    Grading(None, None, cfg).coverage(g, derived, sched, read)
    if len(g.checks) != 1:
        raise SystemExit(f"FAIL: coverage wrote {len(g.checks)} row(s)")
    return g.checks[0]


def prove_coverage_row() -> str | None:
    """The three states, both ways in, and the things the row must not accuse.

    Lives here so a reviewer can see it fail. Returns a FAIL line, or None.
    """
    w, l = 20, 20
    plan = _rect(w, l, 2, 10, 2, 10)
    on_plan = _rect(w, l, 3, 9, 3, 9)
    away = _rect(w, l, 14, 18, 14, 18)
    measured = {"plan": "map", "height": "capture", "witness": "section"}
    blank = {"plan": "declared", "height": "none", "witness": "none"}
    front = {"name": "front", "provenance": measured, "mask": plan}
    skeleton = ["podium", "shell", "floors", "glazing", "parapets"]
    short = {
        "podium": (_rect(w, l, 1, 12, 1, 12), 0, 1),
        "shell": (plan, 0, 12),
        "floors": (on_plan, 3, 12),
        "glazing": (on_plan, 1, 11),
        "parapets": (plan, 12, 13),
    }

    green = _grade([front], skeleton, short)
    if green.name != ROW or green.ok is not True:
        return f"FAIL: skeleton should be green, got {green.line()}"
    if "carry a measured footprint" in green.detail or "all 2 plan" in green.detail:
        return f"FAIL: pass text still counts unexamined parts: {green.detail}"
    if "nothing stands unmeasured" not in green.detail:
        return f"FAIL: green detail does not say what was found: {green.detail}"

    red = _grade([front], skeleton + ["villas"],
                 {**short, "villas": (away, 0, 14)})
    if red.ok is not False or "villas" not in red.detail:
        return f"FAIL: villas 14 m off the plan should be red, got {red.line()}"
    for name in skeleton:
        if name in red.detail:
            return f"FAIL: {name} was accused: {red.detail}"
    if "declare their sizes" in red.detail:
        return f"FAIL: fail text still sends the reader to DECLARED_*: {red.detail}"
    if "UNMEASURED" not in red.detail or "widen the clip" not in red.detail:
        return f"FAIL: fail text dropped the ways out: {red.detail}"
    if f"{COVERAGE_SHARE:.0%}" not in red.detail:
        return f"FAIL: red row hid the share threshold: {red.detail}"
    if f"{COVERAGE_STOREYS:g} storey" not in red.detail:
        return f"FAIL: red row hid the height threshold: {red.detail}"

    club = _grade([front], ["clubhouse"], {"clubhouse": (away, 0, 4)})
    if club.ok is not False or "clubhouse" not in club.detail:
        return f"FAIL: clubhouse 4 m off the plan should be red, got {club.line()}"

    storey = _grade([front], ["deck"], {"deck": (away, 0, 3)})
    if storey.ok is not True:
        return f"FAIL: a one-storey deck off the plan is a surface, got {storey.line()}"

    named = _grade(
        [{"name": "front", "provenance": blank, "mask": plan}],
        ["front"], {})
    if named.ok is not False or "front" not in named.detail:
        return f"FAIL: a plan-named part with empty columns should be red, got {named.line()}"

    excused = _grade([front], skeleton + ["villas"],
                     {**short, "villas": (away, 0, 14)},
                     {"villas": "the clip stops twelve metres short of it"})
    if excused.ok is not None:
        return f"FAIL: UNMEASURED villas should be ungraded, got {excused.line()}"
    if "twelve metres" not in excused.detail:
        return f"FAIL: ungraded detail dropped the phrase: {excused.detail}"

    try:
        _grade([front], skeleton, short, {"floors": "not a building"})
    except SystemExit as why:
        if "floors" not in str(why):
            return f"FAIL: stale UNMEASURED should name floors, got {why}"
    else:
        return "FAIL: UNMEASURED of a surface should have stopped the run"

    for cover in (
        {"plan": "vector", "height": "none", "witness": "none"},
        {"plan": "declared", "height": "model", "witness": "none"},
        {"plan": "declared", "height": "none", "witness": "section"},
    ):
        one = _grade([{"name": "front", "provenance": cover, "mask": plan}],
                     ["front"], {})
        if one.ok is not True:
            return f"FAIL: {cover} should cover, got {one.line()}"

    print("coverage row: green, red, ungraded, and the geometric line hold",
          flush=True)
    return None


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
        missed = prove_coverage_row()
        if missed:
            print(missed)
            return 1

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

        # The fixture's schedule is the skeleton, so the row is green -- the
        # outbuilding is still not a derived part, and that is this file's red.
        subprocess.run(
            [sys.executable, "-m", f"buildings._selftest_{KIND}.build"],
            cwd=ROOT, check=True)
        # check=False: report.json is written even when a later row is red, so
        # a failing coverage row cannot hide the outbuilding check below.
        subprocess.run(
            [sys.executable, "-m", f"buildings._selftest_{KIND}.gate"],
            cwd=ROOT, check=False)
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        rows = [c["name"] for c in report.get("checks", [])]
        if "every part is measured by something" not in rows:
            print("FAIL: report.json has no row "
                  "'every part is measured by something'; "
                  f"the gate asked {len(rows)} check(s)")
            return 1
        row = next(c for c in report["checks"]
                   if c["name"] == "every part is measured by something")
        print(f"gate row present: ok={row['ok']!r}: {row['detail']}")
        if row["ok"] is not True:
            print("FAIL: the fixture skeleton went unmeasured")
            return 1
        if "carry a measured footprint" in row["detail"]:
            print("FAIL: fixture pass text still counts unexamined parts: "
                  + row["detail"])
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

        # Three words, and each one has to be a word the survey is allowed to
        # say. A missing key used to be the whole failure; now the values are
        # what this half is for, and a part whose plan is "declared" because
        # nobody filled the field must not read the same as one that really
        # was declared.
        allowed = {
            "plan": {"map", "vector", "capture", "model", "declared"},
            "height": {"capture", "model", "declared", "none"},
            "witness": {"section", "none"},
        }
        missing = [p for p in derived["parts"]
                   if any(p.get("provenance", {}).get(key) not in values
                          for key, values in allowed.items())]
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
