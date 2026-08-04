"""Is the coin still a coin?

    python -m tools.style_selftest

`Mask.speckle` and `build.dither` exist to reproduce one recipe measured off a
finished city: an honest fifty-fifty coin per cell, no checkerboard, no stripes,
no clumps. Every one of those is a *statistical* property, and a positional hash
that quietly acquired structure would keep laying blocks, keep passing the gate,
keep rendering, and be wrong in the one way it exists not to be.

That is not hypothetical. The five call sites this replaced all wrote the same
thing -- `(x * 7 + z * 3) % 11 < 4` -- which looks like scattering and is a
lattice: it repeats along the diagonal 82% of the time, so what it lays down is
diagonal stripes. Nothing in the pipeline noticed for six buildings, because
there is no number anywhere that a stripe fails.

So this measures the four figures the survey measured, on a plain field and on a
shape with holes in it, and fails if any of them has drifted. It also checks the
thing that makes the hash worth having over a sequence: a mask that grows by a
cell must not reshuffle the cells it already had.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blockwright import blocks as blocklib          # noqa: E402
from blockwright import build, style                # noqa: E402
from blockwright.build import Canvas                # noqa: E402
from blockwright.mask import Mask                   # noqa: E402

SIDE = 160
TOLERANCE = 0.03        # three points, on 25 600 cells


def field(side: int = SIDE) -> Mask:
    mask = Mask(side, side)
    for i in range(len(mask.bits)):
        mask.bits[i] = 1
    return mask


def check(name: str, got: float, want: float,
          tolerance: float = TOLERANCE) -> int:
    ok = abs(got - want) <= tolerance
    print(f"  [{'ok  ' if ok else 'FAIL'}] {name:34s} {got:6.3f}  want "
          f"{want:5.3f} +/- {tolerance}")
    return 0 if ok else 1


def main() -> int:
    faults = 0
    print("[style] a plain field, share 0.5")
    whole = field()
    heads = whole.speckle(0.5)
    cells = {(x, z) for x, z in heads.cells()}

    faults += check("share", heads.count() / whole.count(), 0.500)

    # Along one axis. A lattice scores high here and a coin scores a half.
    seen = differ = 0
    for x in range(SIDE - 1):
        for z in range(SIDE):
            seen += 1
            differ += ((x, z) in cells) != ((x + 1, z) in cells)
    faults += check("neighbours differ", differ / seen, 0.500)

    # The diagonal is the one the lattices failed: theirs repeats 0.82 of the
    # time. A coin has no diagonal.
    seen = same = 0
    for x in range(SIDE - 1):
        for z in range(SIDE - 1):
            seen += 1
            same += ((x, z) in cells) == ((x + 1, z + 1) in cells)
    faults += check("diagonal repeats", same / seen, 0.500)

    # And a checkerboard, which would satisfy both of the above and be wrong.
    board = sum(((x, z) in cells) == ((x + z) % 2 == 0)
                for x in range(SIDE) for z in range(SIDE))
    faults += check("checkerboard agreement", board / (SIDE * SIDE), 0.500)

    print("[style] other shares are the share asked for")
    for share in (0.1, 0.25, 0.36, 0.75, 0.9):
        faults += check(f"share {share}", whole.speckle(share).count()
                        / whole.count(), share, 0.02)

    print("[style] a seed changes the picks, not the statistics")
    other = whole.speckle(0.5, seed=1)
    moved = sum(a != b for a, b in zip(heads.bits, other.bits)) / whole.count()
    faults += check("cells that changed", moved, 0.500, 0.05)
    faults += check("share, second seed",
                    other.count() / whole.count(), 0.500)

    # The property a sequential generator does not have, and the reason this is
    # hashed on position: growing the region must not disturb what was already
    # there. A build whose map is redrawn one cell wider should not come back
    # with a different roof.
    print("[style] growing the region leaves the old cells alone")
    small = Mask(SIDE, SIDE)
    for x in range(SIDE - 10):
        for z in range(SIDE - 10):
            small.set(x, z)
    big = small.dilate(2.0)
    a, b = small.speckle(0.5), big.speckle(0.5)
    kept = all(b.get(x, z) for x, z in a.cells())
    print(f"  [{'ok  ' if kept else 'FAIL'}] every cell picked in the small "
          "region is still picked in the grown one")
    faults += 0 if kept else 1

    print("[style] dither lays two blocks and reads back as the recipe")
    canvas = Canvas(SIDE, 4, SIDE)
    a_block, b_block = "minecraft:stone", "minecraft:polished_andesite"
    apart = blocklib.apart(a_block, b_block)
    written = build.dither(canvas, whole, 1, a_block, b_block, seed=2)
    faults += check("cells written", written / whole.count(), 1.0, 0.0)
    read = style.dither_of(canvas, whole, 1)
    faults += check("share", read["share"], 0.500)
    faults += check("neighbours differ", read["differs"], 0.500)
    faults += check("checkerboard agreement", read["checkerboard"], 0.500)
    # Mean and not median. The cell-weighted median of a coin lands exactly on
    # the fence between 2 and 3 -- the cumulative share of cells reaches 0.500
    # at the end of the runs of two -- so asserting on it would fail on a
    # rounding accident about a third of the time. The mean is 2.0 and stays
    # there, and the median is printed beside it because that is the figure the
    # survey published.
    faults += check("run, mean", read["run_mean"], 2.0, 0.06)
    print(f"  [    ] {'run, cell-weighted median':34s} {read['run']:6d}  "
          f"(the survey printed {style.REFERENCE['dither_run']}; 2 or 3 is a "
          "coin)")
    faults += 0 if read["run"] in (2, 3) else 1
    ok = read["materials"] == 2
    print(f"  [{'ok  ' if ok else 'FAIL'}] {'materials':34s} "
          f"{read['materials']:6d}  want 2   ({apart:.1f} RGB apart)")
    faults += 0 if ok else 1

    # The rule the signature is supposed to carry. `dither` takes a level, not a
    # range, so there is no way to spell "dither this wall" -- and if that ever
    # stops being true the horizontal-only finding stops being enforced by
    # anything at all.
    print("[style] dither cannot be pointed at a wall")
    filled = sum(1 for y in range(4)
                 for z in range(SIDE) for x in range(SIDE)
                 if canvas.get(x, y, z) != "minecraft:air")
    ok = filled == whole.count()
    print(f"  [{'ok  ' if ok else 'FAIL'}] one course only: {filled} blocks "
          f"over 4 levels, want {whole.count()}")
    faults += 0 if ok else 1

    if faults:
        print(f"\n{faults} fault(s): the coin has acquired structure")
        return 1
    print("\nthe coin is a coin, and only lays one course at a time")
    return 0


if __name__ == "__main__":
    sys.exit(main())
