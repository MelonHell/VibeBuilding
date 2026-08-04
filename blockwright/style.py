"""How the finished surfaces are textured, read back off the build.

Everything else in this package measures the building. This measures the
*drawing* -- how varied a surface is, and on which planes the variation sits --
because that turned out to be a question the pipeline could answer and never
asked.

It exists because of one comparison. `docs/gta5-style-findings.md` is a survey
of somebody else's Los Santos in Minecraft, fifty million blocks of it, and the
single most reproducible thing in it is not a material list: it is that the
horizontal surfaces carry noise and the vertical ones do not. Neighbouring
blocks on a flat roof differ half the time; on a facade, one time in six. Run
the same two statistics over six of our finished builds and the facades come out
at 0.10 to 0.22 -- the same building as theirs, near enough -- while the
horizontals come out at 0.015 to 0.067, ten to thirty times flatter. Every deck,
every terrace and every pavement we lay is one printed rectangle of one block.

That is the whole finding, and it needed no opinion to reach. Which is the point
of this module: style arguments are otherwise unwinnable, because the only
evidence is a picture and everyone is looking at a different one. A photo review
that says "the roof looks bare" and a build that answers "0.02 against a
reference 0.50" have had an argument that can end.

Two things this deliberately does not do.

**It does not grade.** There is no correct value. A Miami condo is not a Los
Santos office block, and its own photographs outrank any number taken off
another city; the reference column is context, not a target. If a building ever
wants a budget here it declares one itself, with a source, the way
`DECLARED_STOREY` already works.

**It does not overrule the eyes.** A number about the coin may reject a finding
about the coin -- "the noise looks patchy" answers to a checkerboard score of
0.50 -- and nothing else. A finding that a roof does not read as a roof is about
reading, and no statistic here has standing over it. The pipeline has been here
before: a canopy was measured correct and was still wrong, and what fixed it was
taking clutter off the deck rather than arguing with the reviewer.
"""

from __future__ import annotations

from . import blocks as blocklib

AIR = "minecraft:air"

# What the survey measured, for the column beside ours. Every number here is
# from `docs/gta5-style-findings.md`; none of it is a target. The dither figures
# are from the 25 roofs laid entirely in `andesite` + `stone`, which is the pair
# carrying three fifths of all the texture on that map.
REFERENCE = {
    "who": "GTA V Los Santos, docs/gta5-style-findings.md",
    "horizontal_differs": 0.50,
    "vertical_differs": 0.166,
    "dither_share": 0.49,
    "dither_checkerboard": 0.50,
    "dither_run": 2,
    "slabs": 0.080,
    "stairs": 0.005,
}

_SIDES = ((1, 0, 0), (-1, 0, 0), (0, 0, 1), (0, 0, -1))


class Texture:
    """The variation statistics of one finished build.

    `horizontal` and `vertical` are the fraction of adjacent visible pairs whose
    blocks differ, counted separately on the two kinds of plane -- a cell with
    air above it is horizontal surface, a cell with air beside it is facade.
    A cell can be both, on a parapet, and is counted in both: it genuinely shows
    two faces to two different questions.

    Only *visible* cells count. The fill inside a wall is not texture, and a
    build that is hollow and a build that is solid would otherwise measure
    differently for a reason nobody can see.
    """

    __slots__ = ("horizontal", "vertical", "horizontal_pairs", "vertical_pairs",
                 "top_mix", "slabs", "stairs", "blocks")

    def __init__(self, horizontal, vertical, horizontal_pairs, vertical_pairs,
                 top_mix, slabs, stairs, blocks):
        self.horizontal = horizontal
        self.vertical = vertical
        self.horizontal_pairs = horizontal_pairs
        self.vertical_pairs = vertical_pairs
        self.top_mix = top_mix              # [(block, share)], commonest first
        self.slabs = slabs
        self.stairs = stairs
        self.blocks = blocks

    def report(self) -> dict:
        return {
            "horizontal_differs": round(self.horizontal, 3),
            "vertical_differs": round(self.vertical, 3),
            "horizontal_pairs": self.horizontal_pairs,
            "vertical_pairs": self.vertical_pairs,
            "top_mix": [[b, round(s, 3)] for b, s in self.top_mix[:6]],
            "slabs": round(self.slabs, 4),
            "stairs": round(self.stairs, 4),
            "blocks": self.blocks,
            "reference": REFERENCE,
        }

    def lines(self) -> list[str]:
        """Ours beside theirs. Printed, never graded."""
        out = [f"texture (against {REFERENCE['who']}):"]

        def row(what, ours, theirs, pairs=None):
            tail = f"   n={pairs}" if pairs is not None else ""
            out.append(f"  {what:30s} {ours:6.3f}   ref {theirs:5.3f}{tail}")

        row("horizontal neighbours differ", self.horizontal,
            REFERENCE["horizontal_differs"], self.horizontal_pairs)
        row("facade neighbours differ", self.vertical,
            REFERENCE["vertical_differs"], self.vertical_pairs)
        row("slabs, of all blocks", self.slabs, REFERENCE["slabs"])
        row("stairs, of all blocks", self.stairs, REFERENCE["stairs"])
        if self.top_mix:
            mix = ", ".join(f"{b.split(':')[-1]} {s:.0%}"
                            for b, s in self.top_mix[:4])
            out.append(f"  {'top surfaces are':30s} {mix}")
        return out


def texture(model) -> Texture:
    """Read a Canvas or a Schematic and count what varies where."""
    width, height, length = model.width, model.height, model.length
    get = model.get

    def at(x, y, z):
        if 0 <= x < width and 0 <= y < height and 0 <= z < length:
            return blocklib.base(get(x, y, z))
        return AIR

    top: dict[tuple[int, int, int], str] = {}
    side: dict[tuple[int, int, int], str] = {}
    counts: dict[str, int] = {}
    for y in range(height):
        for z in range(length):
            for x in range(width):
                block = at(x, y, z)
                if block == AIR:
                    continue
                counts[block] = counts.get(block, 0) + 1
                if at(x, y + 1, z) == AIR:
                    top[(x, y, z)] = block
                if any(at(x + dx, y, z + dz) == AIR for dx, _, dz in _SIDES):
                    side[(x, y, z)] = block

    # Along the two ground axes for a horizontal surface, and up as well as
    # along for a facade: a wall's rhythm is as much vertical as horizontal, and
    # counting only sideways would read a striped elevation as plain.
    h_rate, h_pairs = _differs(top, ((1, 0, 0), (0, 0, 1)))
    v_rate, v_pairs = _differs(side, ((1, 0, 0), (0, 0, 1), (0, 1, 0)))

    total = sum(counts.values()) or 1
    surface = len(top) or 1
    mix: dict[str, int] = {}
    for block in top.values():
        mix[block] = mix.get(block, 0) + 1

    return Texture(
        horizontal=h_rate,
        vertical=v_rate,
        horizontal_pairs=h_pairs,
        vertical_pairs=v_pairs,
        top_mix=sorted(((b, n / surface) for b, n in mix.items()),
                       key=lambda row: -row[1]),
        slabs=sum(n for b, n in counts.items() if b.endswith("_slab")) / total,
        stairs=sum(n for b, n in counts.items() if b.endswith("_stairs")) / total,
        blocks=total,
    )


def _differs(cells: dict, steps) -> tuple[float, int]:
    """Fraction of adjacent pairs within one set of cells that are not alike.

    Pairs are counted once, not twice: only the forward neighbour of each cell
    is looked at. Counting both ways gives the same fraction and twice the `n`,
    which reads as more evidence than there is.
    """
    seen = differ = 0
    for (x, y, z), block in cells.items():
        for dx, dy, dz in steps:
            other = cells.get((x + dx, y + dy, z + dz))
            if other is None:
                continue
            seen += 1
            differ += other != block
    return (differ / seen if seen else 0.0), seen


def dither_of(model, mask, y: int) -> dict:
    """Check one laid course against the recipe it claims to follow.

    Answers the four questions the survey asked of a dithered roof, on one
    horizontal layer: what share the commoner block takes, how often a
    neighbour differs, how much of it lands on a checkerboard, and how long the
    runs are. A coin gives 0.50, 0.50, 0.50 and a cell-weighted median of 2.

    Checkerboard agreement is the one worth keeping even though it looks
    redundant. A build that laid `(x + z) % 2` would score a perfect 0.50 share
    and a perfect 1.00 neighbour-differs, and at a glance the first number alone
    would say the recipe was followed. This is the number that catches it.
    """
    rows: dict[tuple[int, int], str] = {}
    for x, z in mask.cells():
        if 0 <= x < model.width and 0 <= z < model.length and 0 <= y < model.height:
            block = blocklib.base(model.get(x, y, z))
            if block != AIR:
                rows[(x, z)] = block
    if not rows:
        return {"cells": 0}

    mix: dict[str, int] = {}
    for block in rows.values():
        mix[block] = mix.get(block, 0) + 1
    common = max(mix, key=lambda b: mix[b])

    seen = differ = board = 0
    for (x, z), block in rows.items():
        board += (block == common) == ((x + z) % 2 == 0)
        other = rows.get((x + 1, z))
        if other is not None:
            seen += 1
            differ += other != block
        other = rows.get((x, z + 1))
        if other is not None:
            seen += 1
            differ += other != block

    runs: list[int] = []
    for z in {z for _, z in rows}:
        row = sorted(x for x, zz in rows if zz == z)
        run = 0
        previous = None
        for x in row:
            block = rows[(x, z)]
            if previous is not None and x == previous[0] + 1 \
                    and block == previous[1]:
                run += 1
            else:
                if run:
                    runs.append(run)
                run = 1
            previous = (x, block)
        if run:
            runs.append(run)
    runs.sort()
    # Cell-weighted, because that is what the survey reports: a cell is more
    # likely to sit in a long run than a run is to be long, and the two medians
    # differ by exactly one on an honest coin.
    #
    # And the cell-weighted median of a coin sits exactly on the fence. A run of
    # length L holds a share of the cells proportional to L * 2**-L, so the
    # cumulative share reaches 0.500 precisely at the end of L = 2 and which
    # side of it a given field lands on is a rounding accident. `run_mean` is
    # the same statistic without the fence -- it is 2.0 for a coin and stays
    # there -- so that is the one to test against; `run` is kept because it is
    # the figure the survey printed and the two want to be comparable.
    half = sum(runs) / 2
    running = 0
    median = 0
    for length in runs:
        running += length
        if running >= half:
            median = length
            break

    return {
        "cells": len(rows),
        "materials": len(mix),
        "share": round(mix[common] / len(rows), 3),
        "commonest": common,
        "differs": round(differ / seen, 3) if seen else 0.0,
        "checkerboard": round(board / len(rows), 3),
        "run": median,
        "run_mean": round(sum(runs) / len(runs), 2) if runs else 0.0,
        "reference": {"share": REFERENCE["dither_share"],
                      "differs": REFERENCE["horizontal_differs"],
                      "checkerboard": REFERENCE["dither_checkerboard"],
                      "run": REFERENCE["dither_run"]},
    }


__all__ = ["REFERENCE", "Texture", "dither_of", "texture"]
