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

    print(f"[mask] {checked} comparisons over {len(SIZES)} shapes: the fast "
          "path and the plain path are identical")
    return 0


if __name__ == "__main__":
    sys.exit(main())
