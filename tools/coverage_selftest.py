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
from blockwright.grading import (
    CLIP_HOLDS_WITHIN, COVERAGE_SHARE, COVERAGE_STOREYS, Grading)
from blockwright.mask import Mask
from blockwright.plan import Part, decompose
from blockwright.schedule import Declaration, Item, Schedule

from tools import fixture
from tools.pipeline_selftest import make

ROOT = Path(__file__).resolve().parent.parent
KIND = "coverage"
ROW = "every part is measured by something"
SITE_ROW = "the clip holds the site"

# How far a face of an assembled part may stand from where the fixture drew it.
# Four metres, which is the sum of the four things that legitimately move a face
# here and not a metre more:
#
#   0.5   the capture's surface stands `fixture.SKIN` outside the drawn face,
#         on each face, the way photogrammetry stands on the outside of a wall
#   1.4   the registration is fitted on the two wings and this part sits
#         fifteen metres past them. Its v scale is 1.035 -- which is mostly
#         that same skin, read as a scale over a 35 m depth -- and carrying it
#         27 m out moves the far face about 0.9, with 0.45 more across the
#         part's own depth
#   0.7   the lattice snap, at the ends of the building
#   1.4   a cell of the frame's own staircase, at each end of each axis
#
# The run comes in at 3.7 on the far v face, inside the sum rather than fitted
# to it. The failure this bounds is a part at the wrong end of an eighty-metre
# building, which is sixty metres out -- fifteen times this.
PLACED_WITHIN = 4.0


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


def prove_orientation() -> str | None:
    """A flip is read off asymmetry, or it is not read at all.

    `Registration.orient` is a pure function of the points and the plan, so the
    cases that matter are three rectangles here rather than a second fixture.
    Lives beside `prove_coverage_row` for the same reason: a reviewer can see it
    fail. Returns a FAIL line, or None.

    The trick that makes each case decide something is asking the **same**
    question twice, of a shape and of its own mirror. A profile that reads the
    same reversed cannot tell the two apart, so the honest answer is identical
    both times; a flip read off the sub-cell wobble of a traced face is
    necessarily opposite between them, because the wobble reverses with it.
    """
    import math

    from blockwright.gate import PROFILE_BINS, Plan, Registration, _profile

    U, V = (0.0, 80.0), (0.0, 25.0)

    def cloud(*boxes) -> list[tuple[float, float]]:
        return [(u + 0.5, v + 0.5)
                for u0, u1, v0, v1 in boxes
                for u in range(u0, u1) for v in range(v0, v1)]

    def mirrored(points):
        return [(U[0] + U[1] - u, v) for u, v in points]

    def wobbled(points):
        """Sub-cell noise, the size a traced face carries. What a flip used to
        be read off wherever the profile itself said nothing."""
        return [(u, v + 0.4 * math.sin(u * 0.7)) for u, v in points]

    def asked(plan_points, mesh_points):
        # Mesh extents equal to the plan's, so the correspondence is the
        # identity and the only thing under test is which way round it goes.
        return Registration(U, V, U, V, 0.0).orient(mesh_points,
                                                    Plan(plan_points))

    # A plain bar: the same width at every station, which is the fixture's own
    # case and the one that started this.
    bar = cloud((0, 80, 0, 10))
    for how, points in (("as drawn", wobbled(bar)),
                        ("mirrored", wobbled(mirrored(bar)))):
        fu, _, table = asked(bar, points)
        if fu or "u" not in table["undetermined"]:
            return (f"FAIL: a plain bar read {how} came back flip_u={fu}, "
                    f"undetermined={table['undetermined']!r}. A bar reads the "
                    "same end for end, so nothing may decide its u flip")

    # And an H, which varies enormously and still reads the same reversed. This
    # is the case a spread test passes straight through to the coin toss.
    ends = cloud((0, 80, 0, 10), (0, 20, 10, 25), (60, 80, 10, 25))
    along = _profile(Plan(ends).points, U, PROFILE_BINS)
    varies = max(along) - min(along)
    if varies < 10.0:
        return (f"FAIL: the H's own profile only varies {varies:.1f} m, so this "
                "case no longer tells a spread test from an asymmetry test")
    for how, points in (("as drawn", wobbled(ends)),
                        ("mirrored", wobbled(mirrored(ends)))):
        fu, _, table = asked(ends, points)
        if fu or "u" not in table["undetermined"]:
            return (f"FAIL: an H read {how} came back flip_u={fu}, "
                    f"undetermined={table['undetermined']!r}. Its profile "
                    f"varies {varies:.0f} m and is identical reversed, which is "
                    "shape without asymmetry -- exactly what must not decide a "
                    "flip")

    # A wing at one end only, which genuinely is decidable. The gate must not
    # have turned the whole measurement off.
    wing = cloud((0, 80, 0, 10), (0, 20, 10, 25))
    fu, _, table = asked(wing, wobbled(wing))
    if fu or "u" in table["undetermined"]:
        return (f"FAIL: a bar with one wing came back flip_u={fu}, "
                f"undetermined={table['undetermined']!r}; it is asymmetric and "
                "unmirrored, so the answer is a decided no-flip")
    fu, _, table = asked(wing, wobbled(mirrored(wing)))
    if not fu or "u" in table["undetermined"]:
        return (f"FAIL: the same wing mirrored came back flip_u={fu}, "
                f"undetermined={table['undetermined']!r}; the flip is there to "
                "be read and this is the reading")

    print("orientation: a flip comes off asymmetry, and off nothing else",
          flush=True)
    return None


def _raise_drops(kind: str, extra: str, label: str, least_area: float
                 ) -> str | None:
    """Run derive with a raised bar and fail unless the outbuilding went."""
    where = make(kind, extra, ("layout.png", "mesh"))
    try:
        ran = subprocess.run(
            [sys.executable, "-m",
             f"buildings._selftest_{kind}.probes.derive"],
            cwd=ROOT, check=True, capture_output=True, text=True)
        text = ran.stdout
        if "dropped: 1 piece(s)" not in text:
            return (f"FAIL: {label} should print 'dropped: 1 piece(s)'; "
                    f"derive said:\n{text}")
        derived = json.loads((where / "out" / "derived.json").read_text(
            encoding="utf-8"))
        gone = (derived.get("assembly") or {}).get("dropped") or {}
        if gone.get("count") != 1:
            return (f"FAIL: {label} should drop 1 piece, "
                    f"assembly.dropped={gone!r}")
        if gone.get("area", 0) < least_area:
            return (f"FAIL: dropped area {gone.get('area')} m2 is under "
                    "the ordinary PART_LEAST_AREA; this is not the "
                    "outbuilding")
        if gone.get("tallest", 0) < 5.0:
            return (f"FAIL: dropped tallest {gone.get('tallest')} m is "
                    "not the outbuilding's 6 m")
        names = [p["name"] for p in derived.get("parts", [])]
        if any("capture" in n for n in names):
            return (f"FAIL: a capture part survived {label}: {names}")
    except subprocess.CalledProcessError as err:
        return (f"FAIL: derive with {label} exited {err.returncode}:\n"
                f"{err.stdout}\n{err.stderr}")
    finally:
        if "--keep" not in sys.argv:
            shutil.rmtree(where, ignore_errors=True)
    return None


def prove_thresholds() -> str | None:
    """The part thresholds cut, and the run says what they threw away.

    The fixture's outbuilding is 240 m² and 6 m, above both bars, so the
    ordinary run cannot say whether they cut. Raising a bar over it and
    then reverting the raise proves nothing to the next reader. This is
    that raise, kept -- once for area, once for height.
    """
    from buildings._template.probes import derive as tables

    u0, u1, v0, v1, top = fixture.OUTBUILDING
    area = (u1 - u0) * (v1 - v0)
    if area < tables.PART_LEAST_AREA or top < tables.PART_LEAST_HEIGHT:
        return (f"FAIL: the fixture outbuilding is {area:.0f} m2 and "
                f"{top} m, which is under PART_LEAST_AREA="
                f"{tables.PART_LEAST_AREA} or PART_LEAST_HEIGHT="
                f"{tables.PART_LEAST_HEIGHT}; the ordinary run going "
                "green would not be evidence the bars were applied")
    if not (20.0 < tables.PART_LEAST_AREA):
        return "FAIL: PART_LEAST_AREA no longer cuts a 20 m2 hedge"
    if not (2.0 < tables.PART_LEAST_HEIGHT):
        return "FAIL: PART_LEAST_HEIGHT no longer cuts a 2 m awning"

    missed = _raise_drops(
        "thresholds",
        'STRIPS = ("front", "back")\nPART_LEAST_AREA = 400.0\n',
        "PART_LEAST_AREA=400", tables.PART_LEAST_AREA)
    if missed:
        return missed
    missed = _raise_drops(
        "thresholds_h",
        'STRIPS = ("front", "back")\nPART_LEAST_HEIGHT = 10.0\n',
        "PART_LEAST_HEIGHT=10", tables.PART_LEAST_AREA)
    if missed:
        return missed
    print("thresholds: a piece under PART_LEAST_AREA or "
          "PART_LEAST_HEIGHT is dropped, and the run says so", flush=True)
    return None


def _site_grade(built_rect, mesh_bounds, frame=None):
    """Run `site_covered` on a tiny schedule. `built_rect` is (x0, x1, z0, z1)."""
    w, l = 40, 40
    frame = frame or Frame((0.0, 0.0), 0.0, float(w), float(l))
    mask = _rect(w, l, built_rect[0], built_rect[1], built_rect[2], built_rect[3])
    sched = Schedule((Item("podium", "what", "source"),))
    sched.built["podium"] = Declaration(mask, 0, 1)
    sched.width, sched.length = w, l
    if mesh_bounds is None:
        derived = {"mesh": {"read": False, "why": "no reference"}}
    elif mesh_bounds is False:
        derived = {"mesh": {"read": True}}
    else:
        derived = {"mesh": {"read": True, "bounds": mesh_bounds}}
    g = Gate("prove")
    read = SimpleNamespace(frame=frame)
    Grading(None, None, SimpleNamespace()).site_covered(g, derived, sched, read)
    if len(g.checks) != 1:
        raise SystemExit(f"FAIL: site_covered wrote {len(g.checks)} row(s)")
    return g.checks[0], sched


def prove_site_covered() -> str | None:
    """The clip-holds-the-site row in both states, and the ungraded one.

    Lives here so a reviewer can see it fail. Returns a FAIL line, or None.
    """
    wide = {"u0": 0.0, "u1": 30.0, "v0": 0.0, "v1": 30.0}
    green, sched = _site_grade((5, 15, 5, 15), wide)
    if green.name != SITE_ROW or green.ok is not True:
        return f"FAIL: a build inside the reference should be green, got {green.line()}"
    if "inside the reference" not in green.detail:
        return f"FAIL: green detail does not say the build is inside: {green.detail}"

    identity = Frame((0.0, 0.0), 0.0, 40.0, 40.0)
    west = sched.extent(identity)[0]
    just_in, _ = _site_grade(
        (5, 15, 5, 15),
        {"u0": west + CLIP_HOLDS_WITHIN - 0.01, "u1": 30.0, "v0": 0.0, "v1": 30.0})
    if just_in.ok is not True:
        return (f"FAIL: {CLIP_HOLDS_WITHIN - 0.01:g} m past the clip should "
                f"still be green, got {just_in.line()}")
    just_out, _ = _site_grade(
        (5, 15, 5, 15),
        {"u0": west + CLIP_HOLDS_WITHIN + 0.01, "u1": 30.0, "v0": 0.0, "v1": 30.0})
    if just_out.ok is not False or "west" not in just_out.detail:
        return (f"FAIL: {CLIP_HOLDS_WITHIN + 0.01:g} m past the clip should "
                f"be red on the west, got {just_out.line()}")
    if "Re-clip to hold u" not in just_out.detail:
        return f"FAIL: red row does not print the union box: {just_out.detail}"
    hold_u = min(west, west + CLIP_HOLDS_WITHIN + 0.01)
    if f"{hold_u:.0f}" not in just_out.detail:
        return (f"FAIL: red row hid the union west edge {hold_u:.0f}: "
                + just_out.detail)

    blank, _ = _site_grade((5, 15, 5, 15), None)
    if blank.ok is not None or "no reference" not in blank.detail:
        return f"FAIL: no mesh should be ungraded, got {blank.line()}"
    stale, _ = _site_grade((5, 15, 5, 15), False)
    if stale.ok is not None:
        return f"FAIL: a mesh with no plan bounds should be ungraded, got {stale.line()}"

    # A building off the axes: the world AABB is larger than the plan AABB.
    # Comparing world cells to plan bounds would go red; converting through
    # the frame must not. And the two extents must actually differ, or the
    # conversion is dead and this case is the identity case again.
    angled = Frame((0.0, 0.0), 30.0, 40.0, 40.0)
    _, turned = _site_grade((5, 15, 5, 15), wide, frame=angled)
    world = turned.extent()
    plan = turned.extent(angled)
    if world == plan:
        return ("FAIL: extent() at 30 deg matches world cells; "
                "the plan conversion is dead")
    boxed = {"u0": plan[0], "u1": plan[1], "v0": plan[2], "v1": plan[3]}
    aligned, _ = _site_grade((5, 15, 5, 15), boxed, frame=angled)
    if aligned.ok is not True:
        return (f"FAIL: a 30 deg build inside its own plan box went red: "
                f"{aligned.line()} (world {world}, plan {plan})")

    print("the clip holds the site: green, red, ungraded, and the 30 deg box hold",
          flush=True)
    return None


def prove_clip_up() -> str | None:
    """`--clip-up` is a ceiling, and a floor is refused with the cost named."""
    help_out = subprocess.run(
        [sys.executable, "tools/ge_convert.py", "--help"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    text = (help_out.stdout or "") + (help_out.stderr or "")
    if help_out.returncode != 0:
        return f"FAIL: ge_convert --help exited {help_out.returncode}: {text}"
    idx = text.find("--clip-up")
    if idx < 0:
        return "FAIL: --help does not mention --clip-up"
    block = text[idx:idx + 500]
    if "CEILING" not in block and "ceiling" not in block.lower():
        return f"FAIL: --clip-up help is not a ceiling: {block!r}"
    if "MIN" in block and "MAX" in block:
        return f"FAIL: --clip-up help still describes a MIN MAX pair: {block!r}"

    refused = subprocess.run(
        [sys.executable, "tools/ge_convert.py", "nowhere", "--clip-up", "1", "2"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    said = (refused.stdout or "") + (refused.stderr or "")
    if refused.returncode == 0:
        return "FAIL: --clip-up 1 2 was accepted"
    if "ceiling" not in said.lower() or "silently" not in said:
        return f"FAIL: refusal did not say what clipping the floor costs: {said!r}"
    print("clip-up: ceiling only, a floor is refused", flush=True)
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
        missed = (prove_coverage_row() or prove_orientation()
                  or prove_thresholds() or prove_site_covered()
                  or prove_clip_up())
        if missed:
            print(missed)
            return 1

        out = build()
        derived = json.loads((out / "derived.json").read_text(encoding="utf-8"))
        parts = [p["name"] for p in derived.get("parts", [])]
        gone = (derived.get("assembly") or {}).get("dropped")
        if not isinstance(gone, dict) or "count" not in gone or "area" not in gone:
            print("FAIL: derived.json assembly does not carry dropped "
                  f"count and area; assembly={derived.get('assembly')!r}")
            return 1
        if gone.get("count"):
            print("FAIL: the fixture outbuilding is 240 m2 and 6 m, "
                  f"above both bars, but assembly.dropped={gone!r}")
            return 1
        mesh_bounds = (derived.get("mesh") or {}).get("bounds")
        if not isinstance(mesh_bounds, dict) or not all(
                k in mesh_bounds for k in ("u0", "u1", "v0", "v1")):
            print("FAIL: derived.json mesh has no plan bounds; "
                  f"mesh={derived.get('mesh')!r}")
            return 1

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
        if SITE_ROW not in rows:
            print("FAIL: report.json has no row "
                  f"{SITE_ROW!r}; the gate asked {len(rows)} check(s)")
            return 1
        site = next(c for c in report["checks"] if c["name"] == SITE_ROW)
        print(f"site row present: ok={site['ok']!r}: {site['detail']}")
        if site["ok"] is not True:
            print("FAIL: the fixture clip should hold the site")
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

        # And that it landed where the fixture put it, which existence alone
        # cannot say. The plan frame and the fixture's own (u, v) run the same
        # way to within the lattice snap, so the outbuilding's box is a number
        # this can check against `fixture.OUTBUILDING` directly.
        #
        # The half of this file that matters most. Every row downstream reads
        # the reference through one registration, so a part placed through a
        # flip that registration guessed is a part every check agrees with:
        # the section maps its mask back onto the real outbuilding and finds
        # the right height, `stands where the plan says` compares a build and a
        # plan that are wrong together, and the map-fill test above passes
        # because a u flip leaves v alone. That is exactly what happened once --
        # `orient` read the flip off a width profile flat to within the
        # rasterisation, and the part came back sixty metres away with two
        # green selftests over it.
        u0, u1, v0, v1 = fixture.OUTBUILDING[:4]
        placed = min(covered, key=lambda p: abs(p["v"][0] - v0))
        off = max(abs(placed["u"][0] - u0), abs(placed["u"][1] - u1),
                  abs(placed["v"][0] - v0), abs(placed["v"][1] - v1))
        if off > PLACED_WITHIN:
            print(f"FAIL: {placed['name']} stands at "
                  f"u {placed['u'][0]}..{placed['u'][1]}, "
                  f"v {placed['v'][0]}..{placed['v'][1]}; the fixture puts the "
                  f"outbuilding at u {u0}..{u1}, v {v0}..{v1} -- out by "
                  f"{off:.1f} m against {PLACED_WITHIN} m. An assembled part is "
                  "placed through the registration, so this is where a flip "
                  "read off jitter, a scale fitted to the wrong material or a "
                  "frame turned the wrong way shows up. Nothing else here can "
                  "see it: every other row reads the reference through the "
                  "same fit and agrees with whatever it says.")
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
