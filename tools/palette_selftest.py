"""Is the colour table unambiguous, and does it answer for what builds ask?

    python -m tools.palette_selftest

`blocks.py` runs one policy: a block with no colour comes back magenta, because
"a colour that looks like architecture is worse than no colour at all". That
policy is only worth anything if the table itself is unambiguous, and a Python
dict literal will not say so -- write a key twice with two different values and
the second one wins, silently, at import.

That has already happened. An outside reading of this repository found
`minecraft:weathered_copper` listed twice, at (108, 153, 128) and at
(109, 154, 118), with the second quietly winning; `deepslate` and
`orange_concrete` are listed twice as well, agreeing. Nothing in the module can
see any of it, because by the time the module exists the duplicates are gone.

So this reads the source rather than the module. It also asks the derivation
rules to answer for the families a build actually writes -- slabs, stairs, walls,
panes -- because those are not in the table and are the blocks the relief
primitives lay.
"""

from __future__ import annotations

import ast
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from blockwright import blocks as blocklib            # noqa: E402

SOURCE = ROOT / "blockwright" / "blocks.py"

# What the relief and furniture primitives write. None of these is in `COLORS`
# by name: they are all meant to be derived from the block they are cut from,
# and a derivation that stops working takes the colour of a whole surface with
# it -- to magenta, which is at least loud.
DERIVED = [
    "minecraft:smooth_quartz_slab",
    "minecraft:smooth_quartz_stairs",
    "minecraft:quartz_stairs",
    "minecraft:smooth_stone_slab",
    "minecraft:polished_andesite_slab",
    "minecraft:black_stained_glass_pane",
    "minecraft:light_blue_stained_glass_pane",
    "minecraft:brick_stairs",
    "minecraft:stone_brick_slab",
    "minecraft:granite_stairs",
]

failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def duplicate_keys(path: Path):
    """Every literal string key written more than once in one dict, per dict."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        seen = defaultdict(list)
        for key, value in zip(node.keys, node.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                seen[key.value].append((key.lineno, ast.unparse(value)))
        for name, rows in seen.items():
            if len(rows) > 1:
                found.append((name, rows))
    return found


def main() -> int:
    dupes = duplicate_keys(SOURCE)
    disagreeing = [(n, r) for n, r in dupes if len({v for _, v in r}) > 1]

    check("no key is written twice with two different values", not disagreeing,
          "; ".join(f"{n} at {[ln for ln, _ in r]} -> {[v for _, v in r]}"
                    for n, r in disagreeing) or "none")
    check("no key is written twice at all", not dupes,
          "; ".join(f"{n} at {[ln for ln, _ in r]}" for n, r in dupes)
          or f"{len(dupes)} repeated")

    # The policy itself: an unknown block is magenta and says so, rather than
    # taking a safe-looking grey. A grey default once drew a whole cone in the
    # colour of the wall behind it and hid it in plain sight for an iteration.
    loud = blocklib.colour("minecraft:not_a_real_block")
    check("an unknown block is loud", loud == (255, 0, 255),
          f"{loud} for a name that is not in the table")
    check("and it is not known", not blocklib.known("minecraft:not_a_real_block"),
          "`known` agrees with `colour`")

    # Derivation, which is what every relief primitive depends on.
    lost = [b for b in DERIVED if not blocklib.known(b)]
    check("every cut block a build writes has a colour", not lost,
          f"{len(DERIVED)} checked, {len(lost)} magenta"
          + (f": {lost}" if lost else ""))

    # A cut block has to agree with the block it was cut from, or a wall and its
    # coping read as two materials in every comparison sheet.
    apart = [(b, blocklib.apart(b, blocklib.base(b).rsplit("_", 1)[0]))
             for b in ("minecraft:smooth_quartz_slab",
                       "minecraft:smooth_stone_slab")]
    check("a cut block matches what it was cut from",
          all(d < 1.0 for _, d in apart),
          ", ".join(f"{b.split(':')[-1]} {d:.1f} RGB" for b, d in apart))

    # And the pale trunk, which a review named as missing by name: a royal
    # palm's stem is grey-white and the table held `jungle_log` and `oak_log`
    # and nothing lighter, so the build could not have one.
    pale = [b for b in ("minecraft:birch_log", "minecraft:stripped_birch_log")
            if blocklib.known(b)]
    check("there is a pale trunk to plant", len(pale) == 2,
          f"{len(pale)} of 2 pale logs in the table: {pale}")

    if failures:
        print(f"\n[palette] {len(failures)} failed: {', '.join(failures)}")
        return 1
    print("\n[palette] the colour table answers once, and for what builds write")
    return 0


if __name__ == "__main__":
    sys.exit(main())
