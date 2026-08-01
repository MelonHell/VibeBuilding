"""A rotated wall dressed with everything that has to know which way is out.

`Facade.facing_of` and `Canvas.fill_fn` exist so a course of stairs can be laid
round a building and each one turned to face the way its own stretch of wall
looks. Nothing in Terra uses either yet -- Terra is cubes -- so both would rot
silently, which is the failure the shape sheet was written to stop for `boxes`
and this one stops for the rest of the vocabulary.

The test subject is a rectangle at the build's own 52 degrees, because that is
where a facing goes wrong. A cell's clear cardinal neighbours on a diagonal wall
alternate between two directions from one step to the next, so a facing read
straight off them writes a run of stairs that rotate back and forth along a
straight wall. `facing_of` reads a smoothed contour normal instead, and the
number this prints -- how many runs of constant facing the ring breaks into --
is what says whether the smoothing is doing its job. Four sides should give four
runs, give or take the wrap; a dozen means it is alternating again.

What is drawn, bottom to top:

    walls       plain cubes, for the courses above to sit on
    stairs      a cornice, each one facing the way its own wall looks out
    slab        a coping at half a level, which only a slab can sit at
    pane        a balustrade written plain, joined up by `finalize`

Run it and look at the corners: the stairs should turn once per side, and the
balustrade should be a continuous rail rather than a row of posts.

    python -m tools.facade_sheet [outdir]
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

from blockwright import blocks as blocklib
from blockwright.build import Canvas, Facade, slab, solid
from blockwright.mask import Mask
from blockwright.render import View, render

ANGLE = 52.43           # the angle Terra is drawn at
HALF_U, HALF_V = 14.0, 7.0
PAD = 4

WALL = "minecraft:white_concrete"
CORNICE = "minecraft:smooth_quartz_stairs"
COPING = "minecraft:smooth_quartz_slab"
RAIL = "minecraft:glass_pane"

WALL_TOP = 4            # the stairs sit on this course
SCALE = 22


def footprint() -> Mask:
    """A rectangle at ANGLE degrees, with room around it for the render."""
    span = int(2 * math.hypot(HALF_U, HALF_V)) + 2 * PAD
    mask = Mask(span, span)
    t = math.radians(ANGLE)
    cos, sin = math.cos(t), math.sin(t)
    centre = span / 2.0
    for x in range(span):
        for z in range(span):
            dx, dz = x + 0.5 - centre, z + 0.5 - centre
            u = dx * cos + dz * sin
            v = -dx * sin + dz * cos
            if abs(u) <= HALF_U and abs(v) <= HALF_V:
                mask.set(x, z)
    return mask


def sheet() -> tuple[Canvas, Facade, Mask]:
    plan = footprint()
    rim = plan.rim()
    face = Facade(plan)

    canvas = Canvas(plan.width, WALL_TOP + 4, plan.length)
    solid(canvas, plan, 0, WALL_TOP, WALL)

    # A cornice, stepping down and out. Each stair is turned by its own stretch
    # of wall, which is the whole reason `fill_fn` takes a function rather than a
    # block -- and turned to the *opposite* of the way that wall looks, because a
    # stair's `facing` names the side its full-height part stands on. Written the
    # obvious way round it comes out as a parapet chamfered on the inside, which
    # looks purposeful enough in a render to survive unnoticed.
    canvas.fill_fn(rim, WALL_TOP, WALL_TOP + 1,
                   lambda x, y, z: blocklib.with_state(
                       CORNICE, half="bottom",
                       facing=blocklib.opposite(face.facing_of(x, z))))

    # A coping half a level up. Asking for 0.5 is what turns the slab over;
    # asking a full cube for it raises, which is the point of the check.
    slab(canvas, rim, WALL_TOP + 1.5, COPING)

    # A balustrade, written with no state at all. What makes it a rail rather
    # than a row of disconnected posts is `finalize`, below.
    canvas.fill(rim, WALL_TOP + 2, WALL_TOP + 3, RAIL)

    return canvas, face, rim


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else Path("out")
    outdir.mkdir(parents=True, exist_ok=True)

    canvas, face, rim = sheet()
    rewritten = canvas.finalize()

    # How many runs of constant facing the ring breaks into. Four sides want
    # four, give or take one for where the walk wraps round.
    runs, last = 0, None
    for x, z in face.ring:
        side = face.facing_of(x, z)
        if side != last:
            runs += 1
            last = side
    print(f"ring {len(face.ring)} cells, perimeter {face.perimeter:.1f} m, "
          f"{runs} run(s) of constant facing")
    print(f"finalize rewrote {rewritten} block(s)")

    model = canvas.to_schematic()
    stray = blocklib.unknown_blocks(model.palette)
    print(f"palette {len(model.palette) - 1} block(s)"
          + (f"  UNKNOWN: {', '.join(stray)}" if stray else ""))
    render(model, outdir / "facade_sheet.png", View.iso(name="iso"), scale=SCALE)
    print(f"{outdir / 'facade_sheet.png'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
