"""Do the regularising operations do what they claim, and only that?

    python -m tools.evenness_selftest

`Mask.straighten`, `open`, `round`, `mirrored` and `symmetrise` exist to make a
shape read as drawn on purpose rather than as traced off a wobbly drawing. Every
one of them fails silently. A straightening that quietly filled a courtyard, a
symmetry that came back not quite symmetric, an opening that ate a real wing --
none of those raises anything, none moves a gate row on its own, and all of them
arrive as "the building looks a bit off" three rounds later.

`Site.STRAIGHT` now defaults to a metre, so `straighten` runs on every part of
every building whether or not anybody asked for it. That is the reason this file
exists: an operation that used to be opt-in and unused is now on the critical
path for the whole corpus.

Every shape here is built at 52 degrees to the world grid, which is the angle the
real corpus keeps producing and the angle at which every one of these operations
is hardest -- a rasterised edge there is a staircase, and the whole difficulty is
telling a staircase from a sawtooth.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blockwright import checks                       # noqa: E402
from blockwright.frame import Frame                  # noqa: E402
from blockwright.mask import Mask, iou               # noqa: E402

ANGLE = 52.0
W = L = 96

failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def frame() -> Frame:
    return Frame((12.0, 9.0), ANGLE)


def block(f: Frame, u0=6.0, u1=54.0, v0=6.0, v1=30.0) -> Mask:
    return f.rect(W, L, u0, u1, v0, v1)


def sawtoothed(f: Frame, mask: Mask) -> Mask:
    """The same block with a cell bitten out every third step of one edge.

    Built by hand rather than by a morphological operation, because the defect
    being reproduced is a hand-drawn edge and a morphological one would leave a
    different, more regular mark.
    """
    out = mask.copy()
    ring = mask.contour()
    for n, (x, z) in enumerate(ring):
        if n % 3 == 0 and f.v_of(x + 0.5, z + 0.5) > 18.0:
            out.set(x, z, 0)
    return out


def main() -> int:
    f = frame()
    plain = block(f)

    # 1. A shape that is already straight has to survive being straightened.
    # If this fails, every clean part in the corpus is being damaged by the new
    # default, which is a worse outcome than the sawtooth it was turned on for.
    kept = plain.straighten(f, 1.0)
    score = iou(plain, kept)
    check("straighten keeps a clean rectangle", score >= 0.97,
          f"iou {score:.3f} against itself")

    # 2. And it has to actually remove a sawtooth.
    rough = sawtoothed(f, plain)
    before = checks.jaggedness(rough, f)["share"]
    after = checks.jaggedness(rough.straighten(f, 1.0), f)["share"]
    check("straighten removes a sawtooth", after < before / 2.0,
          f"jaggedness {before:.3f} -> {after:.3f}")

    # 3. A court must survive. `contour()` traces the outer ring only, so the
    # first version of this filled every courtyard in the corpus and nothing
    # would have said so: the silhouette is identical and the section grades a
    # skyline, which a court does not reach.
    court = block(f, 20.0, 40.0, 12.0, 24.0)
    holed = plain - court
    out = holed.straighten(f, 1.0)
    hole_before, hole_after = holed.holes().count(), out.holes().count()
    check("straighten keeps a court",
          hole_after >= hole_before * 0.8 and hole_after > 0,
          f"{hole_before} cells of court -> {hole_after}")

    # 4. Two pieces in one mask must both survive, for the same reason.
    far = block(f, 66.0, 84.0, 6.0, 30.0)
    pair = plain | far
    out = pair.straighten(f, 1.0)
    check("straighten keeps every piece",
          len(out.components(min_cells=8)) == 2,
          f"{len(pair.components(min_cells=8))} pieces in, "
          f"{len(out.components(min_cells=8))} out")

    # 5. An opening cuts a whisker off; a closing does not. This is the pair the
    # library was missing -- `close` was there alone, so a shape could have its
    # notches filled and kept every spike.
    whiskered = plain.copy()
    spur = f.rect(W, L, 28.0, 29.0, 30.0, 36.0)
    whiskered = whiskered | spur
    opened = whiskered.open(1.5)
    closed = whiskered.close(1.5)
    left_open = (opened & spur).count()
    left_closed = (closed & spur).count()
    check("open cuts a whisker", left_open < spur.count() * 0.35,
          f"{spur.count()} cells of spur -> {left_open} after open, "
          f"{left_closed} after close")
    check("close keeps a whisker", left_closed >= spur.count() * 0.8,
          f"{left_closed} of {spur.count()} kept, which is why both exist")

    # 6. A rounding takes both corners off and leaves a straight edge alone.
    # The notch is two metres across, because a closing only bridges a gap
    # narrower than twice its radius -- a six-metre bite out of an edge is a
    # shape, not noise, and `round` is right to leave it alone.
    notched = plain - f.rect(W, L, 20.0, 22.0, 6.0, 10.0)
    # At 2.0 rather than 1.5, and that gap is the operation telling on itself:
    # the opening runs first and widens a notch a little before the closing
    # sees it, so a radius only just over half the notch no longer bridges it.
    rounded = (notched | spur).round(2.0)
    filled = (rounded & (plain - notched)).count()
    notch = (plain - notched).count()
    check("round fills a notch and cuts a spike",
          filled >= notch * 0.5 and (rounded & spur).count() < spur.count() * 0.5,
          f"notch {notch} -> {filled} filled, spur {spur.count()} -> "
          f"{(rounded & spur).count()} left")

    # 7. Mirroring twice about the same axis is the identity, up to two
    # rasterisations. If it is not, the reflection is being done to the cells
    # somewhere rather than to the outline.
    #
    # The threshold is 0.90 and cannot be pushed much higher, for the reason
    # `Frame.flipped` gives at length: a reflection at 52 degrees is an isometry
    # of the plane and not of the cell lattice. The mirror image lands at a
    # different phase against the world grid, so it rasterises to its own
    # correct staircase rather than to a copy of the first one, and coming back
    # crosses the same gap again. What this catches is a mirror done to the
    # bits, which drifts far further and keeps drifting with every call.
    there = plain.mirrored(f, 30.0, "u", 1.0)
    back = there.mirrored(f, 30.0, "u", 1.0)
    score = iou(plain, back)
    check("mirrored twice is the same shape", score >= 0.90,
          f"iou {score:.3f} after there and back")

    # 8. What `symmetrise` returns has to be symmetric. Nothing else in the
    # library asks this: `checks.twins` measures a pair and reports, and a
    # symmetry that came back 4 % out would read as a slightly worse pair rather
    # than as a broken operation.
    #
    # Same lattice floor as above, so the bar is agreement with its own mirror
    # at 0.93 *and* a clear improvement on what went in. Idempotence is the
    # stricter half of the claim and is checked separately: symmetrising an
    # already symmetric shape has nothing left to move, and if it moves
    # something the operation is not converging on anything.
    lopsided = block(f, 6.0, 54.0, 6.0, 30.0) | f.rect(W, L, 54.0, 62.0, 10.0, 26.0)
    axis = 34.0
    before = iou(lopsided, lopsided.mirrored(f, axis, "u", 1.0))
    even = lopsided.symmetrise(f, axis, "u", 1.0)
    after = iou(even, even.mirrored(f, axis, "u", 1.0))
    check("symmetrise returns something symmetric", after >= 0.93 and after > before,
          f"agreement with own mirror {before:.3f} -> {after:.3f}")

    again = iou(even, even.symmetrise(f, axis, "u", 1.0))
    check("symmetrise settles", again >= 0.97,
          f"iou {again:.3f} between one pass and two")

    # 9. The union reading may only add. A symmetry that took material away
    # while claiming to take the larger reading of every edge would shorten a
    # wing on a building whose gate rows are all green.
    lost = (lopsided - even).count()
    check("symmetrise union never removes", lost <= lopsided.count() * 0.02,
          f"{lost} of {lopsided.count()} cells dropped")

    inner = lopsided.symmetrise(f, axis, "u", 1.0, keep="intersection")
    check("symmetrise intersection never adds",
          (inner - lopsided).count() <= lopsided.count() * 0.02,
          f"{(inner - lopsided).count()} cells added")

    if failures:
        print(f"\n[evenness] {len(failures)} failed: {', '.join(failures)}")
        return 1
    print("\n[evenness] every regularising operation does what it claims")
    return 0


if __name__ == "__main__":
    sys.exit(main())
