"""What greys a map crop is actually drawn in.

    python -m tools.map_probe buildings/<name>/input/layout.png

The flat-map reader needs three things said about a crop: which grey the
building's body is, which grey the line around it is, and how dark the road core
is. The file does not say, and the default in `flatmap.Palette` is one
particular map's -- so on an unfamiliar crop the first run either finds nothing
or finds the wrong thing.

Guessing those numbers by opening the picture in an editor and hovering over
pixels works and takes twenty minutes. This prints the histogram instead, marks
the peaks, and proposes a palette to paste into `probes/derive.py`. What it
proposes is a starting point and not an answer: it is looking for piles of
achromatic pixels, and a map with a grey sea in it has a pile that is the sea.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blockwright.flatmap import DEFAULT, Palette, footprint, greys  # noqa: E402

BAR = 48        # characters in the widest histogram bar


def peaks(counts: list[tuple[int, int]], limit: int = 4) -> list[tuple[int, int]]:
    """The fullest buckets, darkest first."""
    return sorted(sorted(counts, key=lambda c: -c[1])[:limit])


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    path = Path(argv[0])
    counts = greys(str(path))
    if not counts:
        print(f"{path} has no achromatic pixels at all. Either it is a colour "
              "map -- in which case the flat-map reader is the wrong tool for "
              "it -- or it is not a map crop.")
        return 1

    widest = max(n for _, n in counts)
    print(f"{path}: grey levels, {sum(n for _, n in counts)} pixels")
    for level, n in counts:
        bar = "#" * max(1, round(BAR * n / widest))
        print(f"  {level:3d}..{level + 15:3d}  {n:8d}  {bar}")

    tall = peaks(counts)
    print("\nthe fullest buckets, darkest first: "
          + ", ".join(f"{level}..{level + 15}" for level, _ in tall))
    print("\nOn a typical map crop the darkest of those is the road, the "
          "lightest is the ground,\nand the building is the two in between: the "
          "line is the darker of the pair.")

    if len(tall) >= 4:
        line, fill = tall[1][0], tall[2][0]
        proposal = Palette(fill=(fill - 8, fill + 24), line=(line - 8, fill - 9),
                           road=max(1, tall[0][0] + 16))
        print("\n    MAP_PALETTE = flatmap.Palette(\n"
              f"        fill={proposal.fill}, line={proposal.line},\n"
              f"        road={proposal.road})")
        try:
            found = footprint(str(path), palette=proposal)
        except SystemExit as stop:
            print(f"\nand it finds nothing: {stop}")
            return 1
        bounds = found.bounds()
        print(f"\nwith that palette the largest building is {found.count()} "
              f"cells, {bounds[2] - bounds[0] + 1} x {bounds[3] - bounds[1] + 1} "
              "in the crop.\nIf that is not the building you meant, set the "
              "ranges by hand from the histogram.")
    else:
        print(f"\nToo few piles to propose anything. The default palette is "
              f"fill={DEFAULT.fill}, line={DEFAULT.line}, road={DEFAULT.road}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
