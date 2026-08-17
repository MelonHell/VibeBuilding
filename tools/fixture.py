"""A synthetic building, for testing the pipeline rather than a building.

    python -m tools.fixture buildings/<name>/input

Writes a map crop and a mesh of the same imaginary two-wing building: a long
block twelve metres high, a lower wing eight metres high, a court between them,
the whole thing turned thirty degrees off the axes so that every rasterisation
question is live. The mesh also carries an outbuilding the map does not draw
-- twenty by twelve, six metres high, fifteen metres clear of the main block
-- so a survey that only reads the map can be caught missing it.

It exists because the pipeline's own failure modes are hard to provoke on a real
building and trivial to provoke here. A fixture that passes every stage proves
the stages run; a fixture with a known fault in it proves they fail when they
should. Neither proves anything about a real capture, and this is not a
substitute for measuring one.

The mesh is built to be a plausible *capture*, not a model: it stands half a
block outside the drawn footprint, the way a photogrammetry surface stands on
the outer face of a wall the map drew as a line, and it carries a heavier band
of vertices at every floor line, the way balcony slabs and window heads do. Both
are what the measurements downstream actually key on.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ANGLE = 30.0            # degrees, off the world axes
MARGIN = 20             # metres of ground around the building in the crop

# The building, in its own (u, v). Two strips with a court between them.
PARTS = (
    ("front", 0.0, 80.0, 0.0, 14.0, 12.0),
    ("back", 0.0, 80.0, 24.0, 34.0, 8.0),
)

# An outbuilding the map does not draw. It is the whole point of
# `coverage_selftest`: a real site has a clubhouse, a pool house, a row of
# villas that the plan crop never showed, and until something says so they are
# built freehand and graded by nothing.
#
# Fifteen metres clear of the main block, which is far enough that nothing
# measuring the site joins the two: `model3d.read` splits the reference into
# three pieces, and `Read.mass` and `Read.site` are unions and never closings,
# so the gap is intact in the plan and in `derived.json`. It is *not* far enough
# to survive the skeleton's podium, which closes by `COURT = 8` and therefore
# bridges up to sixteen -- deliberately, because that closing is what stops the
# `strays` row calling a detached pool house blocks adrift. Paving between the
# two is a build decision; the fifteen metres are a measurement, and they
# survive as one.
OUTBUILDING = (0.0, 20.0, -27.0, -15.0, 6.0)   # u0, u1, v0, v1, top

STOREY = 3.0            # metres floor to floor, what the rhythm should read

# The map's palette, which `blockwright.template` reads back.
GROUND = (217, 217, 217)
FILL = (176, 176, 176)
LINE = (120, 120, 120)
ROAD = (82, 82, 82)

SKIN = 0.5              # how far the capture's surface stands outside the plan
STEP = 0.5              # vertex spacing on a wall
BAND = 4                # extra vertices laid at every floor line


def rotate(u: float, v: float) -> tuple[float, float]:
    rad = math.radians(ANGLE)
    return (u * math.cos(rad) - v * math.sin(rad),
            u * math.sin(rad) + v * math.cos(rad))


def unrotate(x: float, z: float) -> tuple[float, float]:
    rad = math.radians(ANGLE)
    return (x * math.cos(rad) + z * math.sin(rad),
            -x * math.sin(rad) + z * math.cos(rad))


def extent() -> tuple[float, float, int, int]:
    """Where to put the origin, and how big the crop has to be.

    Sized to the **site** and not to the two wings the map draws. The crop used
    to stop `MARGIN` past the building, which left the outbuilding hanging over
    its north edge -- eleven cells of a three-hundred-cell footprint -- and a
    footprint the plan's canvas cannot hold is a footprint `Survey.assemble`
    now refuses outright, because a build redraws a part as a rectangle on its
    measured extent. Widening the canvas is the fixture taking its own advice:
    the refusal says "widen the crop the plan was drawn on".

    The map still paints only `PARTS`. What grew is the sheet, not the drawing,
    and the case the fixture exists for -- a building in the capture that the
    map never drew -- is intact.
    """
    boxes = [(p[1], p[2], p[3], p[4]) for p in PARTS]
    boxes.append(OUTBUILDING[:4])
    corners = [rotate(u, v)
               for u0, u1, v0, v1 in boxes
               for u in (u0, u1) for v in (v0, v1)]
    x0 = min(x for x, _ in corners) - MARGIN
    z0 = min(z for _, z in corners) - MARGIN
    width = int(max(x for x, _ in corners) - x0) + MARGIN + 1
    length = int(max(z for _, z in corners) - z0) + MARGIN + 1
    return x0, z0, width, length


def write_map(path: Path) -> None:
    """The crop: ground, a road loop round the plot, the building on it."""
    from PIL import Image

    x0, z0, width, length = extent()
    image = Image.new("RGB", (width, length), GROUND)
    px = image.load()

    u1 = max(p[2] for p in PARTS)
    v1 = max(p[4] for p in PARTS)
    for z in range(length):
        for x in range(width):
            u, v = unrotate(x + 0.5 + x0, z + 0.5 + z0)
            inside = any(a <= u < b and c <= v < d
                         for _, a, b, c, d, _ in PARTS)
            near = -1.0 <= u < u1 + 1.0 and -1.0 <= v < v1 + 1.0
            if inside:
                px[x, z] = FILL
            elif near:
                # The outline and the court, both drawn in the darker grey: the
                # map does not distinguish them, and `plan.decompose` subtracts
                # the lot to find the enclosed areas.
                px[x, z] = LINE

    # A road loop round the plot, which is what `flatmap.parcel` floods to.
    for z in range(length):
        for x in range(width):
            if 2 <= x < width - 2 and 2 <= z < length - 2:
                continue
            px[x, z] = ROAD
    image.save(path)


def shell(vertex, u0: float, u1: float, v0: float, v1: float,
          top: float) -> None:
    """One volume as a capture holds it: outer surfaces, floor bands heavy.

    `vertex(u, v, y)` places a point in the building's own coordinates. Factored
    out of `write_mesh` so that `annex` can add a volume to a mesh that is
    already written and have it be the same kind of thing -- a surface, standing
    `SKIN` outside the drawn face, with the heavy band at every floor line that
    the storey search keys on. A second, simpler shell written beside this one
    would be a second answer to what a capture looks like.
    """
    a, b = u0 - SKIN, u1 + SKIN
    c, d = v0 - SKIN, v1 + SKIN
    floors = [i * STOREY for i in range(int(top / STOREY) + 1)]

    steps_u = int((b - a) / STEP) + 1
    steps_v = int((d - c) / STEP) + 1
    heights = [i * STEP for i in range(int(top / STEP) + 1)]

    for i in range(steps_u):
        u = a + i * STEP
        for y in heights:
            vertex(u, c, y)
            vertex(u, d, y)
        for y in floors:
            for _ in range(BAND):
                vertex(u, c, y)
                vertex(u, d, y)
        for j in range(steps_v):
            vertex(u, c + j * STEP, top)

    for j in range(steps_v):
        v = c + j * STEP
        for y in heights:
            vertex(a, v, y)
            vertex(b, v, y)


def annex(path: Path, box: tuple[float, float, float, float, float]) -> None:
    """Add one more volume to a mesh already written, in the same coordinates.

    A knob rather than a fifth entry in `PARTS`, because the volumes this places
    are the ones a test wants present in some runs and absent in others -- a
    neighbouring block standing above `REGISTER_FLOOR` that the map never drew,
    which is what pulls a registration out to the parcel. Putting it in `PARTS`
    would put it in every branch of every selftest at once.

    The file holds only `v` lines, so appending is the whole operation.
    """
    x0, z0, _, _ = extent()
    with open(path, "a", encoding="utf-8") as out:
        def vertex(u: float, v: float, y: float) -> None:
            x, z = rotate(u, v)
            out.write(f"v {x - x0:.3f} {y:.3f} {z - z0:.3f}\n")

        shell(vertex, *box)


def write_mesh(path: Path) -> None:
    """A capture of the same building: outer surfaces only, floor bands heavy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    x0, z0, width, length = extent()

    with open(path, "w", encoding="utf-8") as out:
        def vertex(u: float, v: float, y: float) -> None:
            x, z = rotate(u, v)
            out.write(f"v {x - x0:.3f} {y:.3f} {z - z0:.3f}\n")

        # The ground the capture brought with it, which every reader has to
        # discard for itself. It reaches `MARGIN` past the outbuilding as well
        # as past the wings: without that the mesh AABB stops at the
        # outbuilding's skin, and a modelled build's apron sits past the
        # reference for want of a clip rather than for want of a building.
        # `extent` already covers the outbuilding, so this is only the margin
        # on top of it.
        gx0, gz0 = 0.0, 0.0
        gx1, gz1 = float(width), float(length)
        ou0, ou1, ov0, ov1, _ = OUTBUILDING
        for u, v in ((ou0, ov0), (ou1, ov0), (ou1, ov1), (ou0, ov1)):
            x, z = rotate(u, v)
            gx0 = min(gx0, x - x0 - MARGIN)
            gx1 = max(gx1, x - x0 + MARGIN)
            gz0 = min(gz0, z - z0 - MARGIN)
            gz1 = max(gz1, z - z0 + MARGIN)
        for i in range(int(math.floor(gx0)), int(math.ceil(gx1)) + 1, 2):
            for j in range(int(math.floor(gz0)), int(math.ceil(gz1)) + 1, 2):
                out.write(f"v {float(i)} 0.000 {float(j)}\n")

        # PARTS is what the map draws. The outbuilding is written the same way
        # -- a surface shell, not a solid -- so it still reads as a capture.
        for _, u0, u1, v0, v1, top in [*PARTS, ("outbuilding",) + OUTBUILDING]:
            shell(vertex, u0, u1, v0, v1, top)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    into = Path(argv[0])
    into.mkdir(parents=True, exist_ok=True)
    write_map(into / "layout.png")
    print(f"wrote {into / 'layout.png'}")
    mesh = Path(argv[1]) if len(argv) > 1 else into.parent / "out" / "mesh-clip" / "merged.obj"
    write_mesh(mesh)
    print(f"wrote {mesh}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
