"""Swatch sheets for the two halves of the non-cube block vocabulary.

Terra's build is made entirely of full cubes, so nothing in the pipeline
exercises the sub-box path or the connection pass. Either could break and no
render would look any different until the day a real build uses them, at which
point the renders would quietly start lying in the direction they were written
to stop lying in. So both are drawn against a synthetic build instead.

Two sheets, because there are two separate things to be wrong.

**shapes** is written by hand, state and all, and rendered exactly as written.
It asks one question: does `blocks.boxes` describe each family correctly. A slab
that draws as a full cube reads half a metre too tall; a glass pane that draws
as a full cube turns a balustrade into a parapet, which is the "thirteen villas
read as one bar" failure the renderer exists to catch.

**joins** is written plain -- every connecting block with no state at all -- and
then put through `Canvas.finalize()`. It asks the other question: does the
writer work out the state a block's neighbours imply. WorldEdit pastes without
running block updates, so whatever state is written is what stands in the world;
a pane written plain stays a lone post forever. The states finalize produced are
printed as well as drawn, because a wall whose `up` is wrong is a one-block
difference that is easy to miss in pixels and obvious in a line of text.

Run them and look. The point of a swatch sheet is that a wrong shape is obvious
at a glance, which no assertion about pixel counts would be.

    python -m tools.shape_sheet [outdir]
"""

from __future__ import annotations

import sys
from pathlib import Path

from blockwright import blocks as blocklib
from blockwright.build import Canvas
from blockwright.render import View, render

# -- what is drawn ---------------------------------------------------------

PANE = "minecraft:glass_pane"
BARS = "minecraft:iron_bars"
FENCE = "minecraft:oak_fence"
GATE = "minecraft:oak_fence_gate"
WALL = "minecraft:cobblestone_wall"
BLOCK = "minecraft:white_concrete"

# Written by hand, drawn as written. None leaves a gap.
SHAPES: list[tuple[str, list[str | None]]] = [
    ("cube", [BLOCK] * 6),
    ("slab", ["minecraft:smooth_quartz_slab[type=bottom]"] * 3
             + ["minecraft:smooth_quartz_slab[type=top]"] * 3),
    ("stairs", [f"minecraft:oak_stairs[facing={f},half=bottom]"
                for f in ("north", "east", "south", "west")]
               + ["minecraft:oak_stairs[facing=north,half=top]"] * 2),
    ("pane", [f"{PANE}[east=true,west=true]"] * 4 + [None, PANE]),
    ("fence", [f"{FENCE}[east=true,west=true]"] * 4 + [None, FENCE]),
    ("wall", [f"{WALL}[east=low,west=low,up=false]"] * 4
             + [None, f"{WALL}[up=true]"]),
    ("gate", [f"{GATE}[facing=north]"] * 2 + [f"{GATE}[facing=east]"] * 2
             + [None, f"{GATE}[facing=north,open=true]"]),
    ("glass", ["minecraft:light_blue_stained_glass"] * 6),
]

# Written plain; `finalize` has to supply the state. The third field is the cell
# that gets a stub of the same block one step north, making a T-junction -- the
# case that separates a wall showing its post from one that does not, and the
# only case a single row cannot reach.
JOINS: list[tuple[str, list[str | None], int | None]] = [
    ("pane", [PANE] * 4 + [None, PANE], 1),
    ("pane+cube", [BLOCK, PANE, PANE, PANE, BLOCK], None),
    ("bars", [BARS] * 4 + [None, BARS], None),
    ("fence", [FENCE] * 4 + [None, FENCE], 1),
    ("fence+gate", [GATE, FENCE, FENCE, FENCE, GATE], None),
    ("wall", [WALL] * 5, 2),
]

MARGIN = 2
PITCH = 3               # blank rows between families, so shapes do not merge
SCALE = 30              # the sheet is a dozen blocks across; draw it big enough
                        # to see a quarter-block riser
DECK = "minecraft:gray_concrete"


def _canvas(rows: list) -> tuple[Canvas, int]:
    """A deck wide enough for these rows, and the z of the first one."""
    width = MARGIN * 2 + max(len(r[1]) for r in rows)
    length = MARGIN * 2 + PITCH * len(rows)
    canvas = Canvas(width, 4, length)
    for x in range(width):
        for z in range(length):
            canvas.set(x, 0, z, DECK)
    return canvas, MARGIN


def shapes() -> Canvas:
    """Hand-written state, left exactly as written."""
    canvas, z0 = _canvas(SHAPES)
    for row, (_label, cells) in enumerate(SHAPES):
        z = z0 + row * PITCH
        for i, block in enumerate(cells):
            if block is not None:
                canvas.set(MARGIN + i, 1, z, block)
    return canvas


def joins() -> tuple[Canvas, list[tuple[str, list[str]]], int]:
    """Plain blocks plus one `finalize` pass, and what it made of them."""
    canvas, z0 = _canvas(JOINS)
    for row, (_label, cells, stub) in enumerate(JOINS):
        z = z0 + row * PITCH
        for i, block in enumerate(cells):
            if block is not None:
                canvas.set(MARGIN + i, 1, z, block)
        if stub is not None:
            canvas.set(MARGIN + stub, 1, z - 1, cells[stub])

    rewritten = canvas.finalize()

    out = []
    for row, (label, cells, _stub) in enumerate(JOINS):
        z = z0 + row * PITCH
        got = [canvas.get(MARGIN + i, 1, z)
               for i, block in enumerate(cells) if block is not None]
        out.append((label, got))
    return canvas, out, rewritten


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else Path("out")
    outdir.mkdir(parents=True, exist_ok=True)

    shape_canvas = shapes()
    join_canvas, states, rewritten = joins()

    for name, canvas in (("shape_sheet", shape_canvas),
                         ("join_sheet", join_canvas)):
        model = canvas.to_schematic()
        render(model, outdir / f"{name}.png", View.iso(name="iso"), scale=SCALE)
        stray = blocklib.unknown_blocks(model.palette)
        print(f"{outdir / name}.png: {len(model.palette) - 1} block(s)"
              + (f"  UNKNOWN: {', '.join(stray)}" if stray else ""))

    print(f"\nfinalize rewrote {rewritten} block(s):")
    for label, got in states:
        print(f"  {label}")
        for block in got:
            state = blocklib.state(block)
            shown = " ".join(f"{k}={state[k]}" for k in sorted(state)) or "(plain)"
            print(f"    {blocklib.base(block).split(':')[-1]:20s} {shown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
