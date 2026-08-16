"""Do the fast path and the plain path agree, bit for bit?

    python -m tools.mask_selftest

`blockwright/fast.py` accelerates a handful of mask operations when numpy is
installed. The whole safety of that arrangement rests on one claim -- that the
two implementations return identical bytes -- and a claim nobody checks is a
claim that stops being true on a Tuesday.

So this runs both on random masks and compares them cell by cell. What it is
protecting is not the masks: it is every threshold in the pipeline. `ROUND`, the
two-metre section tolerance, `STOREY_SCORE`, the block budgets -- all of them
were chosen by looking at numbers this code produced, and an implementation that
answered a percent differently would move every one of them at once, silently,
and the first symptom would be a build that used to pass and now does not for no
reason anybody can name.

With numpy absent there is nothing to compare, and this says so and exits clean
rather than reporting a pass it did not earn.
"""

from __future__ import annotations

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blockwright import fast                       # noqa: E402
from blockwright.frame import Frame                # noqa: E402
from blockwright.mask import Mask                  # noqa: E402

SIZES = ((1, 1), (3, 7), (16, 16), (37, 23), (64, 65))
ROUNDS = 40


def sprinkle(width: int, length: int, density: float, rng) -> Mask:
    mask = Mask(width, length)
    for i in range(width * length):
        if rng.random() < density:
            mask.bits[i] = 1
    return mask


def plain(a: Mask, b: Mask, op: str) -> Mask:
    """The pure-Python answer, written out here rather than borrowed.

    Deliberately not `Mask.__and__`: that is the thing under test, and on a
    machine with numpy it takes the fast path. This is the definition the fast
    path has to match.
    """
    out = Mask(a.width, a.length)
    for i in range(len(a.bits)):
        x, y = a.bits[i], b.bits[i]
        out.bits[i] = 1 if (x and y if op == "and"
                            else x or y if op == "or"
                            else x and not y) else 0
    return out


def main() -> int:
    if not fast.HAVE:
        print("[mask] numpy is not installed, so there is no fast path to "
              "compare against.")
        print("[mask] the plain implementation is the only one running, which "
              "is a supported configuration -- nothing here failed, and "
              "nothing was checked either.")
        return 0

    rng = random.Random(20260801)
    checked = 0
    for width, length in SIZES:
        for _ in range(ROUNDS):
            density = rng.choice((0.0, 0.02, 0.3, 0.5, 0.95, 1.0))
            a = sprinkle(width, length, density, rng)
            b = sprinkle(width, length, rng.random(), rng)
            for op, symbol in (("and", "&"), ("or", "|"), ("sub", "-")):
                want = plain(a, b, op)
                got = Mask(width, length, fast.combine(a.bits, b.bits, op))
                if got.bits != want.bits:
                    print(f"[mask] FAIL: {symbol} on {width}x{length} differs")
                    return 1
                checked += 1
            if fast.count(a.bits) != sum(a.bits):
                print(f"[mask] FAIL: count on {width}x{length} differs")
                return 1
            inverted = bytearray(0 if v else 1 for v in a.bits)
            if fast.invert(a.bits) != inverted:
                print(f"[mask] FAIL: invert on {width}x{length} differs")
                return 1
            checked += 2

    # The rasterisers, which is a stiffer test than the boolean ops: they do
    # floating-point arithmetic per cell, at an arbitrary angle, and a fast path
    # that reassociated one multiply-add would put a cell on the wrong side of a
    # wall roughly once in a very large number of cells -- which is exactly the
    # kind of difference nobody would ever trace back to here.
    #
    # A quarter of the frames are mirrored, and that share is not decoration.
    # The fast path did not carry `flip_u`/`flip_v` at all: every second half of
    # every mirrored pair -- which is how `Site.flipped` draws one -- came out
    # reflected about the wrong place on any machine with numpy installed, and
    # this loop passed every time because it had never fitted a flipped frame.
    for round_ in range(ROUNDS * 4):
        width = rng.randint(1, 64)
        length = rng.randint(1, 64)
        frame = Frame((rng.uniform(-40, 40), rng.uniform(-40, 40)),
                      rng.uniform(0.0, 360.0))
        if round_ % 4 == 1:
            frame = frame.flipped(rng.uniform(-30, 30), "u")
        elif round_ % 4 == 2:
            frame = frame.flipped(rng.uniform(-30, 30), "v")
        elif round_ % 4 == 3:
            frame = frame.flipped(rng.uniform(-30, 30), "u") \
                         .flipped(rng.uniform(-30, 30), "v")
        u0 = rng.uniform(-30, 30)
        v0 = rng.uniform(-30, 30)
        u1, v1 = u0 + rng.uniform(0, 50), v0 + rng.uniform(0, 50)
        want = frame.region(width, length,
                            lambda u, v: u0 <= u < u1 and v0 <= v < v1)
        if frame.rect(width, length, u0, u1, v0, v1).bits != want.bits:
            print(f"[mask] FAIL: rect on {width}x{length} at "
                  f"{frame.angle:.2f} deg differs from the predicate")
            return 1

        cu, cv = rng.uniform(-20, 20), rng.uniform(-20, 20)
        radius = rng.uniform(0, 30)
        inner = rng.uniform(0, radius)
        outer2, inner2 = radius * radius, inner * inner
        want = frame.region(
            width, length,
            lambda u, v: inner2 <= (u - cu) ** 2 + (v - cv) ** 2 < outer2)
        if frame.disc(width, length, cu, cv, radius, inner).bits != want.bits:
            print(f"[mask] FAIL: disc on {width}x{length} at "
                  f"{frame.angle:.2f} deg differs from the predicate")
            return 1
        checked += 2

    print(f"[mask] {checked} comparisons over {len(SIZES)} shapes: the fast "
          "path and the plain path are identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
