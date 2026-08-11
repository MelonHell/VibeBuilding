"""A synthetic building that is wrong in every way the schedule can detect.

`schedule` is the one check that grades a build against what it was *meant* to
be, so it is the one check whose own silence is indistinguishable from success.
If it stopped noticing missing parts tomorrow, every gate downstream would go
green and the report would read exactly as it does now. The shape sheets exist
because an unexercised code path rots; this exists for the same reason, and
because a schedule has no picture to look at, it asserts instead of drawing.

The subject is a 24x24x12 toy with six deliberate faults, one per failure mode:

    pool      in the manifest, never declared             -- nobody built it
    ghost     declared over empty air                     -- built to nothing
    bridge    stops five metres short of the cone         -- wrong place, in plan
    mast      sits over the tower with five metres of air -- wrong place, in y
    glazing   declared over ten courses, built in three   -- mostly not there
    cladding  declared in moss, standing in concrete      -- built of something else

and three parts that are correct, so a check that failed everything would not
pass either. The audit runs against a schedule that has been through
`save`/`load`, because the build and the gate are separate processes and the
sidecar is the only thing between them.

The last two modes are newer than this file and were unexercised for as long as
they existed, which is how this check came to be failing on its own expected
strings: the audit learned to print found against declared -- after a hotel
declared its glazing over 297 cells and 27 courses, stood 158 cells of it in the
bottom three, and passed with the cheerful half of that -- and nothing here was
updated to the wider line. A guard whose expectations lag the thing it guards
reports a fault every run, which is the same as reporting none.

    python -m tools.schedule_selftest
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from blockwright.build import Canvas
from blockwright.mask import Mask
from blockwright.schedule import Item, Schedule

WIDE, TALL, LONG = 24, 12, 24

STONE = "minecraft:white_concrete"
MOSS = "minecraft:moss_block"
GLASS = "minecraft:light_blue_stained_glass"

MANIFEST = [
    Item("tower", "the stair core", "photo 1"),
    Item("bridge", "walkway from the tower to the cone", "photo 2",
         near=("tower", "cone")),
    Item("cone", "the leaning cone", "photo 2"),
    Item("lawn", "open ground inside the road", "the map"),
    Item("mast", "aerial on the tower roof", "photo 3", near="tower"),
    Item("glazing", "the window wall, full height", "photo 5", blocks=GLASS),
    Item("cladding", "the planted screen", "photo 6", blocks=MOSS),
    Item("pool", "plunge pool under the cone", "photo 4"),
    Item("ghost", "a part whose code places nothing", "nowhere"),
]

# (name, ok, detail), in manifest order. Adjacency lines follow the part they
# belong to, which is the order `audit` yields them in.
#
# Every detail here is worked out from the subject below rather than pasted out
# of a run, because an expectation copied from the output tests only that the
# output has not changed. `tower` is 4x4 declared over y 0..6 and filled solid,
# so sixteen of sixteen cells occupying layers 0 to 5; `lawn` is 18x8 one course
# deep, and it counts the cells the tower overwrote because a part that names no
# material is satisfied by anything that is not air.
EXPECTED = [
    ("part tower", True, "16 of 16 cells, y 0..5 of 0..6"),
    ("part bridge", True, "12 of 12 cells, y 4..4 of 4..5"),
    ("bridge meets tower", True,
     "1.0 m apart, 0 m of daylight in y; reach 1.5 m"),
    ("bridge meets cone", False,
     "5.0 m apart, 0 m of daylight in y; reach 1.5 m"),
    ("part cone", True, "16 of 16 cells, y 0..5 of 0..6"),
    ("part lawn", True, "144 of 144 cells, y 0..0 of 0..1"),
    ("part mast", True, "4 of 4 cells, y 10..10 of 10..11"),
    ("mast meets tower", False,
     "0.0 m apart, 5 m of daylight in y; reach 1.5 m"),
    # Every declared cell is glazed and only three of the ten declared courses
    # are, so the cell count alone would read as fully built. The y range is
    # what gives it away, and it has to be in the line for that to happen.
    ("part glazing", False,
     "16 of 16 cells, y 0..2 of 0..10 -- most of what was declared of "
     f"{GLASS} is not there"),
    # Nothing of the named block stands in the mask, and the concrete that does
    # is named and counted: sixteen cells over two courses. Without the material
    # in the declaration this part would have passed on the concrete.
    ("part cladding", False,
     f"declared 16 cells over y 0..2, nothing of {MOSS} stands there; what "
     f"stands in the rest is {STONE} (32)"),
    ("part pool", False, "not built; nothing declares it "
                         "(plunge pool under the cone)"),
    ("part ghost", False, "declared 16 cells over y 0..4, nothing stands there"),
]

EXPECTED_UNDECLARED = ["pool"]


def box(x0: int, x1: int, z0: int, z1: int) -> Mask:
    """A half-open rectangle, in the same convention as `Canvas.fill`."""
    mask = Mask(WIDE, LONG)
    for z in range(z0, z1):
        for x in range(x0, x1):
            mask.set(x, z)
    return mask


def subject() -> tuple[Canvas, Schedule]:
    """The toy build, and the claims it makes about itself."""
    sched = Schedule(MANIFEST)
    canvas = Canvas(WIDE, TALL, LONG)

    # Ground first; the solids above overwrite it, and the lawn still counts
    # those cells because the schedule asks what is not air, not what it is.
    lawn = box(2, 20, 10, 18)
    canvas.fill(lawn, 0, 1, MOSS)
    sched.declare("lawn", lawn, 0, 1)

    tower = box(2, 6, 10, 14)
    canvas.fill(tower, 0, 6, STONE)
    sched.declare("tower", tower, 0, 6)

    cone = box(16, 20, 10, 14)
    canvas.fill(cone, 0, 6, STONE)
    sched.declare("cone", cone, 0, 6)

    # Runs out of the tower and stops five metres short of the cone. Declared in
    # two halves, to exercise the merge in `declare`.
    for x0, x1 in ((6, 9), (9, 12)):
        span = box(x0, x1, 11, 13)
        canvas.fill(span, 4, 5, STONE)
        sched.declare("bridge", span, 4, 5)

    # Directly over the tower in plan, five metres above its roof.
    mast = box(3, 5, 11, 13)
    canvas.fill(mast, 10, 11, STONE)
    sched.declare("mast", mast, 10, 11)

    # Declared the full height of a window wall and glazed only at the bottom.
    # The share of cells is perfect and the share of courses is three tenths,
    # which is the shape of the real defect this branch was written for.
    pane = box(2, 6, 2, 6)
    canvas.fill(pane, 0, 3, GLASS)
    sched.declare("glazing", pane, 0, 10)

    # Declared in moss and standing in concrete. Nothing of the named block is
    # there at all, so the part is not "five per cent built" -- it is built out
    # of something else, and the audit has to say which.
    screen = box(8, 12, 2, 6)
    canvas.fill(screen, 0, 2, STONE)
    sched.declare("cladding", screen, 0, 2)

    # Claimed, never placed.
    sched.declare("ghost", box(2, 6, 20, 24), 0, 4)

    return canvas, sched


def authoring_errors() -> list[tuple[str, str]]:
    """The five mistakes an author can make, and what each one raises.

    Each is a way of writing a schedule that would otherwise grade something
    other than what was written -- a duplicate name silently losing one item, an
    adjacency naming a part that does not exist and so never being tested.
    """
    good = Mask(WIDE, LONG)
    good.set(0, 0)
    out = []

    for label, thunk in (
        ("duplicate name",
         lambda: Schedule([Item("a", "", ""), Item("a", "", "")])),
        ("unknown neighbour",
         lambda: Schedule([Item("a", "", "", near="b")])),
        ("neighbour of itself",
         lambda: Schedule([Item("a", "", "", near="a")])),
        ("declaring an unknown part",
         lambda: Schedule(MANIFEST).declare("nope", good, 0, 1)),
        ("empty y range",
         lambda: Schedule(MANIFEST).declare("tower", good, 3, 3)),
        # A part positioned by judgement that will not say against what. The
        # whole value of `placed` is the sentence naming the anchor it was
        # counted from; blank, it is the pipeline guessing with a field set.
        ("placed by nothing",
         lambda: Item("a", "", "photo-01.jpg", placed="   ")),
    ):
        try:
            thunk()
        except (ValueError, KeyError) as exc:
            out.append((label, f"{type(exc).__name__}: {exc}"))
        else:
            out.append((label, ""))
    return out


def main(argv: list[str]) -> int:
    canvas, sched = subject()

    # Through the sidecar, because that is how the gate receives it.
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "schedule.json"
        sched.save(path)
        reloaded = Schedule.load(path)

    faults = 0

    undeclared = reloaded.undeclared
    print(f"undeclared: {undeclared}")
    if undeclared != EXPECTED_UNDECLARED:
        print(f"  WRONG: expected {EXPECTED_UNDECLARED}")
        faults += 1

    # A placed part has to survive the sidecar. The note is the only record of
    # why that part stands where it does, and the gate that prints it is a
    # different process from the build that wrote it -- dropped in transit, the
    # part quietly becomes one that nobody ever said was positioned by
    # judgement, which is the state this field exists to end.
    note = "the third arch from the north corner, counted on the measured bay"
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "placed.json"
        Schedule([Item("fins", "entrance fins", "90-photo-06", placed=note),
                  Item("deck", "the deck", "90-photo-01")]).save(path)
        back = Schedule.load(path)
    print(f"placed: {[i.name for i in back.placed]}")
    if [i.name for i in back.placed] != ["fins"] \
            or back.placed[0].placed != note:
        print("  WRONG: the placement note did not survive save and load")
        faults += 1

    got = list(reloaded.audit(canvas.to_schematic()))
    for i, (name, ok, detail) in enumerate(got):
        print(f"  {'pass' if ok else 'FAIL'}  {name}  {detail}")
        if i >= len(EXPECTED):
            print("  WRONG: not in the expected audit at all")
            faults += 1
            continue
        if (name, ok, detail) != EXPECTED[i]:
            want = EXPECTED[i]
            print(f"  WRONG: expected {'pass' if want[1] else 'FAIL'}  "
                  f"{want[0]}  {want[2]}")
            faults += 1
    if len(got) < len(EXPECTED):
        for name, ok, detail in EXPECTED[len(got):]:
            print(f"  WRONG: missing  {name}  {detail}")
            faults += 1

    print("\nauthoring errors:")
    for label, raised in authoring_errors():
        print(f"  {label:26s} {raised or 'NOTHING RAISED'}")
        if not raised:
            faults += 1

    print()
    if faults:
        print(f"{faults} fault(s): the schedule is not grading what it claims to")
        return 1
    print("the schedule sees all six failure modes and rejects all six "
          "authoring errors")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
