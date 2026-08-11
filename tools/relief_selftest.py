"""Does the relief actually face the way it looks like it faces?

    python -m tools.relief_selftest

`build.cornice`, `build.balustrade`, `build.kerb` and `build.pave` are the first
things in this library to write a block whose *state* carries the meaning. That
is a new way for a build to be wrong, and it is the quietest one yet.

A cornice with its stairs turned the wrong way round is not a missing cornice.
It is a course of stairs, in the right cells, at the right level, in the right
material, chamfered on the inside where nothing can see it -- and from any
camera the pipeline renders it reads as a deliberate parapet. The gate cannot
see it: the section grades a skyline and the course is one block. The schedule
audit cannot see it: the cells are declared and the blocks are there. The
reviewer cannot see it, because it looks like architecture.

So the check here does not ask the code what it wrote. It reads the finished
canvas back and puts the question to `blocks.boxes` -- which sub-cells does this
block really fill -- and asks whether the solid part ended up on the inside of
the building. That is the same dictionary the renderer trusts, reached by a
different road.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blockwright import blocks as blocklib            # noqa: E402
from blockwright import build                         # noqa: E402
from blockwright.mask import Mask                     # noqa: E402
from blockwright.schem import AIR                     # noqa: E402

W = L = 32
X0, X1, Z0, Z1 = 6, 24, 6, 20

failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def rectangle() -> Mask:
    """An axis-aligned block, so that "north edge" means one thing.

    Every other test in this repository works at 52 degrees on purpose. This one
    does the opposite on purpose: the question is which way a state points, and
    at an angle every edge cell is a diagonal compromise. Squared up, the answer
    is checkable by hand.
    """
    mask = Mask(W, L)
    for z in range(Z0, Z1):
        for x in range(X0, X1):
            mask.set(x, z, 1)
    return mask


def riser_side(block: str) -> str | None:
    """Which side of its cell this stair's full-height part stands on."""
    if not blocklib.base(block).endswith("_stairs"):
        return None
    # A stair comes back as two boxes -- the half slab, then the quarter riser --
    # each (x0, x1, y0, y1, z0, z1). The riser is the one that says which way it
    # is turned, and reading it from the box rather than from the state is the
    # point: it is the same answer the renderer draws.
    x0, x1, _, _, z0, z1 = blocklib.boxes(block)[1]
    if z1 <= 0.5:
        return "north"
    if z0 >= 0.5:
        return "south"
    if x1 <= 0.5:
        return "west"
    if x0 >= 0.5:
        return "east"
    return None


def main() -> int:
    plan = rectangle()

    # 1. The cornice sheds outward on all four sides. Read back from the canvas
    # and answered by `blocks.boxes`, so an inverted `opposite` fails here even
    # though the code that wrote it and the code that checks it agree about
    # everything else.
    canvas = build.Canvas(W, 8, L)
    build.solid(canvas, plan, 0, 4, "minecraft:white_concrete")
    written = build.cornice(canvas, plan, 4, "minecraft:quartz_stairs")

    inward = {"north": "south", "south": "north", "west": "east", "east": "west"}
    edges = {
        "north": [(x, Z0) for x in range(X0 + 2, X1 - 2)],
        "south": [(x, Z1 - 1) for x in range(X0 + 2, X1 - 2)],
        "west": [(X0, z) for z in range(Z0 + 2, Z1 - 2)],
        "east": [(X1 - 1, z) for z in range(Z0 + 2, Z1 - 2)],
    }
    wrong = []
    for side, cells in edges.items():
        for x, z in cells:
            got = riser_side(canvas.get(x, 4, z))
            if got != inward[side]:
                wrong.append(f"{side} edge at ({x},{z}) risers {got}")
    check("cornice sheds outward on every side", not wrong,
          f"{written} blocks, {len(wrong)} facing the wrong way"
          + (f" -- first: {wrong[0]}" if wrong else ""))

    # 2. And it covers the ring it was given. A cell whose facing came back None
    # is silently skipped by `fill_fn`, which would leave a cornice with gaps in
    # it wherever the facade could not answer.
    ring = plan.outline(1.0)
    gaps = [(i % W, i // W) for i, v in enumerate(ring.bits)
            if v and canvas.get(i % W, 4, i // W) == AIR]
    check("cornice leaves no gaps", not gaps,
          f"{ring.count()} cells of ring, {len(gaps)} empty")

    # 3. A projecting cornice stands on cells outside the footprint, where the
    # footprint's own facade has no answer at all. If the `Facade` is not rebuilt
    # on the dilated shape the whole course disappears -- silently, because
    # `fill_fn` treats None as "leave the cell alone".
    out = build.Canvas(W, 8, L)
    build.solid(out, plan, 0, 4, "minecraft:white_concrete")
    projected = build.cornice(out, plan, 4, "minecraft:quartz_stairs", project=2.0)
    beyond = plan.dilate(2.0) - plan
    landed = sum(1 for i, v in enumerate(beyond.bits)
                 if v and out.get(i % W, 4, i // W) != AIR)
    check("a projecting cornice reaches past the wall",
          projected > 0 and landed > 0,
          f"{projected} blocks, {landed} of them outside the footprint")

    # 4. The refusal, which is the reason `balustrade` exists as a named thing
    # rather than as a call to `fill`.
    try:
        build.balustrade(build.Canvas(W, 8, L), ring, 4,
                         "minecraft:black_stained_glass")
        refused = False
    except ValueError:
        refused = True
    check("balustrade refuses a full cube", refused,
          "a cube rail is a parapet, and one build shipped with one")

    # 5. What it writes has to come out as a run rather than as a row of posts.
    # WorldEdit pastes without block updates, so this is `Canvas.finalize`'s job
    # and it is easy to leave uncalled -- in which case every pane arrives as a
    # stump and the balcony reads as a dotted line.
    rail = build.Canvas(W, 8, L)
    build.solid(rail, plan, 0, 4, "minecraft:white_concrete")
    build.balustrade(rail, ring, 4, "minecraft:glass_pane",
                     cap="minecraft:quartz_slab")
    rail.finalize()
    middle = canvas_state(rail, (X0 + X1) // 2, 4, Z0)
    joined = sum(1 for side in ("north", "east", "south", "west")
                 if middle.get(side) == "true")
    check("a balustrade joins up", joined >= 2,
          f"a pane mid-run reports {joined} connections {middle}")

    # And the cap is a slab sitting on top of it, not a cube.
    cap = rail.get((X0 + X1) // 2, 5, Z0)
    check("the hand rail is a slab", blocklib.base(cap).endswith("_slab"),
          f"{cap} over the rail")

    # 6. A kerb is half a block proud. A full course of it is a parapet round a
    # plaza, which is why the cube is refused here too.
    paving = build.Canvas(W, 8, L)
    try:
        build.kerb(paving, plan, 0, "minecraft:smooth_stone")
        refused = False
    except ValueError:
        refused = True
    check("kerb refuses a full block", refused, "a kerb of cubes is a wall")

    build.pave(paving, plan, 0, "minecraft:smooth_stone",
               "minecraft:andesite", edge="minecraft:smooth_stone_slab")
    edge_cell = paving.get(X0, 1, Z0)
    check("the kerb sits one course over the paving",
          blocklib.base(edge_cell).endswith("_slab")
          and blocklib.state(edge_cell).get("type") == "bottom",
          f"{edge_cell} at y=1 over paving at y=0")

    # 7. And paving is still one course, for the reason `dither` is one course:
    # the reference city puts its noise on horizontal surfaces and keeps its
    # walls clean, and a paving primitive that climbed would undo that.
    above = sum(1 for x in range(W) for z in range(L)
                for y in (2, 3)
                if paving.get(x, y, z) != AIR)
    check("paving lays one course", above == 0,
          f"{above} blocks above the kerb course")

    # 8. A flight of steps has to descend as it goes out. Built with the rings
    # in the wrong order it climbs, which from above is indistinguishable and
    # from the ground is a wall with a moulding on it.
    flight = build.Canvas(W, 12, L)
    build.solid(flight, plan, 0, 6, "minecraft:white_concrete")
    build.steps(flight, plan, 6, "minecraft:quartz_stairs", count=3, tread=1.0,
                riser="minecraft:white_concrete")
    heights = []
    for n in range(3):
        band = plan.dilate(1.0 * (n + 1)) - plan.dilate(1.0 * n)
        tops = [y for i, v in enumerate(band.bits) if v
                for y in range(11, -1, -1)
                if flight.get(i % W, y, i // W) != AIR]
        heights.append(max(tops) if tops else None)
    check("a flight descends as it goes out",
          None not in heights and heights == sorted(heights, reverse=True),
          f"top course of each ring outward: {heights}")

    # And it stands on something. Bare treads over open ground are a stack of
    # floating blocks; `checks.floating` finds them, but only after a build and
    # a gate, and only if the part was declared.
    hollow = build.Canvas(W, 12, L)
    build.solid(hollow, plan, 0, 6, "minecraft:white_concrete")
    build.steps(hollow, plan, 6, "minecraft:quartz_stairs", count=3)
    band = plan.dilate(3.0) - plan.dilate(2.0)
    under = sum(1 for i, v in enumerate(band.bits) if v
                and hollow.get(i % W, 2, i // W) != AIR)
    propped = sum(1 for i, v in enumerate(band.bits) if v
                  and flight.get(i % W, 2, i // W) != AIR)
    check("a riser is what holds a flight up", under == 0 and propped > 0,
          f"{under} cells under the outer tread without a riser, "
          f"{propped} with one")

    # 9. A planter has a rim its soil does not spill over. Laid flat the bed is
    # a patch of different-coloured ground, which is what every one of these
    # was before there was a primitive for it.
    bed = build.Canvas(W, 8, L)
    placed = build.planter(bed, plan, 0, "minecraft:smooth_stone",
                           "minecraft:coarse_dirt", plant="minecraft:fern")
    rim_cell = bed.get(X0, 0, Z0)
    inner_cell = bed.get((X0 + X1) // 2, 0, (Z0 + Z1) // 2)
    check("a planter keeps its rim",
          blocklib.base(rim_cell) == "minecraft:smooth_stone"
          and blocklib.base(inner_cell) == "minecraft:coarse_dirt",
          f"rim {blocklib.base(rim_cell).split(':')[-1]}, "
          f"fill {blocklib.base(inner_cell).split(':')[-1]}")
    check("a planter returns what it planted", placed.count() >= plan.count(),
          f"{placed.count()} cells back for a bed of {plan.count()}")

    # 10. And a standard comes back with its post, so that declaring it cannot
    # leave a light floating over the plaza.
    plaza = build.Canvas(W, 8, L)
    stem = build.lamp(plaza, (X0 + 4, Z0 + 4), base=0, height=4,
                      post="minecraft:smooth_stone",
                      head="minecraft:white_concrete")
    column = [plaza.get(X0 + 4, y, Z0 + 4) for y in range(6)]
    check("a lamp is a post and a head",
          stem.count() == 1 and all(b != AIR for b in column[:5])
          and column[5] == AIR,
          f"{sum(1 for b in column if b != AIR)} courses standing")

    if failures:
        print(f"\n[relief] {len(failures)} failed: {', '.join(failures)}")
        return 1
    print("\n[relief] every relief primitive faces the way it claims")
    return 0


def canvas_state(canvas, x: int, y: int, z: int) -> dict:
    return blocklib.state(canvas.get(x, y, z))


if __name__ == "__main__":
    sys.exit(main())
