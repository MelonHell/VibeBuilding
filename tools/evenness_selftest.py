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

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blockwright import checks                       # noqa: E402
from blockwright.frame import Frame                  # noqa: E402
from blockwright.mask import Mask, iou, _fit_face    # noqa: E402

ANGLE = 52.0
W = L = 96

failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def frame() -> Frame:
    return Frame((12.0, 9.0), ANGLE)


def clear() -> Frame:
    """The same angle, placed so `block` fits on the canvas whole.

    At `frame()` the block runs off the west edge and is cut by it. Checks 1--9
    do not care: a clipped rectangle is still straight, still holds a court and
    still mirrors. The edge checks care completely -- a clipped shape has a
    sixteen-metre face along the *world* axis that the building never had, and
    reading the faces a shape actually has is the whole of what they test.
    """
    return Frame((24.0, 6.0), ANGLE)


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

    # From here on the shapes are read rather than reshaped, so they are built
    # on the frame that keeps them off the canvas edge -- see `clear`.
    g = clear()
    slab = block(g)
    toothed = sawtoothed(g, slab)

    # 10. A rectangle has four faces and they run along u and v. This is the
    # floor under everything else here: if a shape whose faces are known cannot
    # be read back, no number `Mask.edges` reports about a shape whose faces are
    # not known means anything.
    faces = slab.edges(g, 1.0)
    angles = sorted(round(e.angle) for e in faces)
    worst = max((e.spread for e in faces), default=9.9)
    check("edges reads a rectangle as four faces",
          len(faces) == 4 and worst <= 0.5
          and all(min(abs(a - 0), abs(a - 90), abs(a - 180)) <= 2
                  for a in angles),
          f"{len(faces)} face(s) at {angles} deg, worst fit {worst:.2f} m")

    # 11. A rake is a measurement and has to survive. This is the case the whole
    # primitive exists for: `squared` can only offer a rectangle in u and v, so
    # on a raked end it squares off the one thing the mapper drew on purpose.
    raked = g.region(W, L, lambda u, v: 6.0 <= u < 54.0 and 6.0 <= v < 30.0
                     and v < 30.0 - 0.4 * (u - 30.0))
    out = raked.faceted(g, 1.0)
    kept = iou(raked, out) if out is not None else 0.0
    boxed = raked.squared(g)
    square = iou(raked, g.rect(W, L, *boxed)) if boxed else 0.0
    check("faceted keeps a rake that squaring loses",
          kept >= 0.95 and kept > square + 0.05,
          f"iou {kept:.3f} faceted against {square:.3f} squared")

    # 12. And it has to take a sawtooth off, which is what it shares with
    # straightening. The difference is in 13.
    #
    # Counted in outline segments rather than in `checks.jaggedness`'s `share`.
    # That share is defined as *what straightening would change*, and a tooth
    # deep enough for Douglas-Peucker to keep is a tooth straightening keeps
    # too: on this shape the share reads 0.003 while the outline is still
    # eighteen segments long. Fitting has no such hole -- a face is a line or it
    # is not a face -- and the segment count is where that shows.
    before = checks.jaggedness(toothed, g)["vertices"]
    out = toothed.faceted(g, 1.0)
    after = checks.jaggedness(out, g)["vertices"] if out is not None else 99
    check("faceted removes a sawtooth", after <= 6 and after < before / 2,
          f"outline of {before} segment(s) -> {after}")

    # 13. The corner `Site.footprint` rounds off comes back square. Nothing
    # else in the library can do this: the pad and the closing are both
    # Euclidean and a Euclidean dilation is a disc, so every convex corner is
    # bitten by a quarter-disc that no straightening tolerance will recover --
    # a chamfer is further off the chord than drawing noise ever is.
    #
    # Here the arc is not a face at all. It runs about a metre and a half,
    # which is under `least` stations, so it is dropped rather than fitted, and
    # the two faces either side of it meet where their own lines cross.
    grown = slab.dilate(1.0).erode(0.5)           # what `Site.footprint` does
    faces = grown.edges(g, 1.0, least=3)
    check("edges drops the arc a pad leaves at a corner", len(faces) == 4,
          f"{len(faces)} face(s): "
          + ", ".join(f"{e.angle:.0f} deg over {e.span} stations"
                      for e in faces))
    # The corner itself is asked of the polygon and not of `checks.corners`,
    # which reads a *rasterised* outline: a rectangle drawn dead square at this
    # angle comes back from it as five or six vertices, so "four" is not an
    # answer any raster at 52 degrees can give. What the primitive claims is
    # narrower and exact -- the corner is where the two faces cross -- and a
    # half-metre pad puts that crossing on the corner of the padded rectangle.
    out = grown.faceted(g, 1.0)
    crosses = [faces[i].meets(faces[(i + 1) % len(faces)])
               for i in range(len(faces))]
    want = [(5.5, 5.5), (54.5, 5.5), (54.5, 30.5), (5.5, 30.5)]
    off = (max(min(math.hypot(u - wu, v - wv) for wu, wv in want)
               for u, v in crosses)
           if len(faces) == 4 and all(c is not None for c in crosses)
           else 99.9)
    check("faceted puts a corner where its own two faces cross",
          out is not None and off <= 1.0,
          f"worst corner {off:.2f} m from where a half-metre pad puts it")

    # 14. And a chamfer long enough to be a face is kept as one. `least` has to
    # cut between the quarter-disc the pipeline puts in by itself and the corner
    # somebody cut back on purpose, and a rule that squared off both would be
    # `box` with more arithmetic.
    chamfer = g.region(W, L, lambda u, v: 6.0 <= u < 54.0 and 6.0 <= v < 30.0
                       and (u - 6.0) + (v - 6.0) >= 8.0)
    faces = chamfer.edges(g, 1.0, least=3)
    skew = [e for e in faces
            if min(abs(e.angle - 0), abs(e.angle - 90),
                   abs(e.angle - 180)) > 20]
    check("edges keeps a chamfer that is long enough to be a face",
          len(faces) == 5 and len(skew) == 1 and abs(skew[0].angle - 135) <= 5,
          f"{len(faces)} face(s), skew one at "
          + (f"{skew[0].angle:.0f} deg over {skew[0].span} stations"
             if skew else "none"))

    # 15. A curve comes back as the polygon the tolerance asks for, and says as
    # much by the number of faces: a disc is not four faces badly fitted, it is
    # a dozen short ones fitted well. A caller wanting a circle draws a circle
    # -- `Frame.disc` -- and this is here so that reading one is not silently
    # mistaken for reading a building.
    disc = g.disc(W, L, 30.0, 30.0, 16.0)
    faces = disc.edges(g, 1.0)
    longest = max((e.run for e in faces), default=0.0)
    check("edges reads a curve as many short faces",
          len(faces) >= 8 and longest <= 16.0,
          f"{len(faces)} face(s), longest {longest:.1f} m of a "
          f"{2 * 16.0:.0f} m disc")

    # 16. And every one of them is inside the tolerance at its *worst* station
    # rather than on average. This is the guarantee that makes a fitted polygon
    # worth drawing at all, and it is not the same guarantee as a small RMS: an
    # arc's stations all sit on one side of its chord, so a face can average
    # half a metre off and be two metres off in the middle. Merged on the RMS
    # this disc came back as a heptagon whose every side was a metre outside it.
    bowed = max((e.worst for e in faces), default=9.9)
    check("no fitted face is a bow the average hides", bowed <= 1.0,
          f"worst station on any face {bowed:.2f} m off its line")

    # 17. Stations the shape is not on are dropped before the fit and not read
    # as zero. Asked of the fitting helper directly, because the failure is
    # invisible from outside: a face with a gap in it still comes back as a
    # face, just tilted, and the tilt is a degree or two on a real building.
    line = [(u, 4.0) for u in range(0, 41)]
    gapped = [p for p in line if not 12.0 <= p[0] < 28.0]
    whole = _fit_face(line, 1.0, 1.0)
    holed = _fit_face(gapped, 1.0, 1.0)
    check("a fit drops the stations it has nothing on",
          abs(whole["m"] - holed["m"]) < 1e-6
          and holed["absent"] == 16 and holed["kept"] == 25,
          f"slope {whole['m']:+.4f} whole against {holed['m']:+.4f} gapped, "
          f"{holed['absent']} station(s) absent of {holed['span']}")

    if failures:
        print(f"\n[evenness] {len(failures)} failed: {', '.join(failures)}")
        return 1
    print("\n[evenness] every regularising operation does what it claims")
    return 0


if __name__ == "__main__":
    sys.exit(main())
