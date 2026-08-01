"""Did the fix change the picture, and did it break a viewpoint nobody touched?

    python -m tools.review_diff <name>            this round against the last
    python -m tools.review_diff <name> 3 5        round 3 against round 5

Two questions the review stage could not answer until `Review.archive` started
keeping rounds. Both are a pixel comparison, because the renderer is
deterministic: the same schematic from the same camera produces the same file,
byte for byte. So a build render that has not changed proves the fix did not
land, and one that changed at a viewpoint nobody was working on is collateral.

The first is the important one. A finding is marked **built** when the code
changed, which is not the same as when the *building* changed -- one real round
recorded a fix and the note "changed nothing" only because somebody happened to
look. This makes that automatic:

    01-high-court     4.1% of pixels moved
    02-low-court      unchanged -- the fix did not reach this viewpoint
    03-low-road       0.2% of pixels moved
    04-high-end       11.8% of pixels moved

It says nothing about whether the change was an improvement. That is what eyes
are for, and this is here so the eyes are spent on rounds that actually differ.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parent.parent

# Below this fraction of pixels, two renders are the same picture: a build that
# moved one block at the far end of a 1280x720 frame is not a round of work.
# It is reported anyway, with its number -- the threshold only decides the word.
MOVED = 0.001


def rounds_of(where: Path) -> list[Path]:
    kept = where / "rounds"
    if not kept.is_dir():
        return []
    return sorted((p for p in kept.iterdir() if p.is_dir() and p.name.isdigit()),
                  key=lambda p: int(p.name))


def compare(before: Path, after: Path) -> float | None:
    """Fraction of pixels that differ, or None if the two cannot be compared."""
    from PIL import Image

    try:
        a = Image.open(before).convert("RGB")
        b = Image.open(after).convert("RGB")
    except OSError:
        return None
    if a.size != b.size:
        return None
    from blockwright import fast

    if fast.HAVE:
        import numpy as np

        left = np.frombuffer(a.tobytes(), dtype=np.uint8).reshape(-1, 3)
        right = np.frombuffer(b.tobytes(), dtype=np.uint8).reshape(-1, 3)
        moved = int((left != right).any(axis=1).sum())
    else:
        pa, pb = a.tobytes(), b.tobytes()
        moved = sum(1 for i in range(0, len(pa), 3)
                    if pa[i:i + 3] != pb[i:i + 3])
    return moved / (a.width * a.height)


def main(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("-")]
    if not names:
        print("    python -m tools.review_diff <name> [earlier] [later]")
        return 2
    building = names[0].strip("/\\").replace("buildings/", "")
    where = ROOT / "buildings" / building / "out" / "review"
    kept = rounds_of(where)
    if not kept:
        print(f"no archived rounds in {where}; run the review at least twice")
        return 1

    # Default: the last archived round against what is in the folder now. The
    # live folder is this round; `rounds/NN` is the one before it.
    if len(names) >= 3:
        pick = {p.name: p for p in kept}
        earlier = pick.get(names[1].zfill(2))
        later = pick.get(names[2].zfill(2))
        if earlier is None or later is None:
            print(f"rounds present: {', '.join(p.name for p in kept)}")
            return 1
    else:
        earlier, later = kept[-1], where

    print(f"-- {earlier.name if earlier != where else 'live'} "
          f"-> {later.name if later != where else 'live'}")
    shown = 0
    for image in sorted(earlier.glob("*.build.png")):
        other = later / image.name
        if not other.exists():
            print(f"   {image.stem:24s} gone from the later round")
            continue
        moved = compare(image, other)
        shown += 1
        if moved is None:
            print(f"   {image.stem:24s} not comparable (different size)")
        elif moved < MOVED:
            print(f"   {image.stem:24s} unchanged -- nothing here moved")
        else:
            print(f"   {image.stem:24s} {moved * 100:5.1f}% of pixels moved")

    for image in sorted(later.glob("*.build.png")):
        if not (earlier / image.name).exists():
            print(f"   {image.stem:24s} new in the later round")
            shown += 1
    if not shown:
        print("   no build renders in either round")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
