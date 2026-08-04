"""Does the pipeline still notice that a building is not all the same?

    python -m tools.variety_selftest

Four mechanisms, one failure. A building whose right-hand wing has no balconies
was built with balconies along the whole of it, and its rooftop plant deck --
a machine room standing beside an open rack of air handlers -- was built as one
blind box. Every row of that run was green. Not because the checks were lenient:
because a facade is not a number anywhere in this pipeline, and neither is the
difference between a box and a frame.

    measure.rhythm_by_span   read the pier rhythm per stretch, not per compass
                             point, so two wings with different facades come
                             back as two numbers instead of one average
    measure.apart            which stretches agreed, by index, so the answer can
                             point at a wing
    roof.woven               tell a surface the capture split from two things
                             standing on each other, by contact and not extent
    build.rack               an open frame, which is what half of what stands on
                             a roof actually is

Every one of them fails silently if it drifts. A `woven` threshold that slipped
would merge real pairs and print nothing; a rack whose posts stopped landing
would be a slab in mid-air; a span reading that quietly returned the whole
elevation would agree with itself forever. So each is exercised on a synthetic
case whose answer is known by construction.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blockwright import build, checks, measure, roof            # noqa: E402
from blockwright.build import Canvas                            # noqa: E402
from blockwright.frame import Frame                             # noqa: E402
from blockwright.mask import Mask                               # noqa: E402

W = L = 40
WALL = "minecraft:white_concrete"


def ok(label: str, passed: bool, detail: str = "") -> int:
    print(f"  [{'ok  ' if passed else 'FAIL'}] {label:44s} {detail}")
    return 0 if passed else 1


def rect(x0: int, x1: int, z0: int, z1: int) -> Mask:
    mask = Mask(W, L)
    for z in range(z0, z1):
        for x in range(x0, x1):
            mask.set(x, z)
    return mask


def woven_cases() -> int:
    """A checkerboard is one surface; two shapes that overlap in plan are two."""
    print("[variety] telling a split surface from two things")
    faults = 0

    # What photogrammetry does to a glazed hall: one rectangle, cells landing
    # alternately at two heights.
    a, b = Mask(W, L), Mask(W, L)
    for z in range(6, 20):
        for x in range(6, 20):
            (a if (x + z) % 2 == 0 else b).set(x, z)
    score = roof.woven(a, b)
    faults += ok("checkerboard over one rectangle", score > 0.95,
                 f"woven {score:.3f}, one_surface {roof.one_surface([a, b])}")
    faults += ok("  and it is called one surface", roof.one_surface([a, b]))

    # The case the rule this replaces got wrong: a solid box beside an open
    # rack. Their bounding boxes overlap, so an extent test merges them, and the
    # build comes out one blind box where the reference has a box and a frame.
    box = rect(6, 14, 6, 20)
    frame_of = rect(14, 22, 12, 28)
    score = roof.woven(box, frame_of)
    boxes_overlap = (14 < 22 and 6 < 14) and (12 < 20 and 6 < 28)
    faults += ok("box beside a rack, extents overlapping", score < 0.3,
                 f"woven {score:.3f}, extents overlap {boxes_overlap}")
    faults += ok("  and it is NOT called one surface",
                 not roof.one_surface([box, frame_of]))

    # A smaller box standing on a deck: also two things, and the one the border
    # ratio is closest to failing on, so it is the case worth keeping.
    deck = rect(6, 22, 6, 22) - rect(10, 17, 10, 17)
    hut = rect(10, 17, 10, 17)
    score = roof.woven(deck, hut)
    faults += ok("a hut standing on a deck", score < roof.WOVEN,
                 f"woven {score:.3f} against the {roof.WOVEN} threshold")

    # Three levels where two are an artefact and the third is real: not one
    # surface, and an average of the three pairwise scores would say it was.
    faults += ok("artefact pair plus a real third",
                 not roof.one_surface([a, b, hut]))
    return faults


def span_cases() -> int:
    """Two stretches of one elevation with different pier rhythms."""
    print("[variety] reading a rhythm per stretch of one elevation")
    from PIL import Image

    faults = 0
    scale = 0.25                    # metres per pixel
    width, height = 800, 240        # 200 m by 60 m
    image = Image.new("L", (width, height), 200)
    px = image.load()
    # Left half: piers every 4 m. Right half: piers every 10 m. Nothing else
    # differs, so any disagreement in the reading is the rhythm.
    for x in range(width):
        metres = x * scale
        pitch = 4.0 if metres < 100.0 else 10.0
        if (metres % pitch) < 1.0:
            for y in range(height):
                px[x, y] = 40

    spans = [(0.0, 100.0), (100.0, 200.0)]
    found = measure.rhythm_by_span(image, scale, spans, 2.0, 16.0)
    left, right = found[0]["value"], found[1]["value"]
    faults += ok("left stretch reads its own pitch", abs(left - 4.0) < 0.6,
                 f"{left:.2f} m, want 4.0")
    faults += ok("right stretch reads its own pitch", abs(right - 10.0) < 1.2,
                 f"{right:.2f} m, want 10.0")

    groups = measure.apart([left, right])
    faults += ok("and they are not put in one group", len(groups) == 2,
                 f"{len(groups)} group(s): {groups}")

    # The whole elevation at once is what used to be asked, and it answers with
    # one number that is neither. Not a fault -- it is the point.
    whole = measure.rhythm_by_span(image, scale, [(0.0, 200.0)], 2.0, 16.0)
    print(f"  [    ] {'the whole elevation reads':44s} {whole[0]['value']:.2f} m"
          " -- one number for two facades, which is the failure this replaces")

    # A stretch too short to hold the rhythm says so rather than answering.
    short = measure.rhythm_by_span(image, scale, [(0.0, 20.0)], 2.0, 16.0)
    faults += ok("a stretch too short refuses to answer",
                 short[0]["value"] == 0.0 and bool(short[0]["why"]))
    return faults


def rack_case() -> int:
    """An open frame: legs, a platform, and daylight in between."""
    print("[variety] a rack is a comb, not a lid")
    faults = 0
    canvas = Canvas(W, 12, L)
    foot = rect(8, 24, 8, 20)
    canvas.fill(foot, 0, 1, "minecraft:light_gray_concrete")

    posts = Mask(W, L)
    for z in range(8, 20, 3):
        for x in range(8, 24, 3):
            posts.set(x, z)
    units = Mask(W, L)
    for z in range(9, 19, 3):
        for x in range(10, 23, 4):
            units.set(x, z)

    placed = build.rack(canvas, foot, posts, 1, 5, "minecraft:iron_bars",
                        deck="minecraft:smooth_stone_slab",
                        units=units, unit="minecraft:gray_concrete",
                        unit_height=2)

    def level(y: int) -> int:
        return sum(1 for z in range(L) for x in range(W)
                   if canvas.get(x, y, z) != "minecraft:air")

    legs, platform = level(2), level(4)
    faults += ok("you can see between the legs", legs < platform / 4,
                 f"{legs} blocks at leg height against {platform} on the deck")
    faults += ok("the platform is whole", platform == foot.count(),
                 f"{platform} of {foot.count()}")

    pieces = checks.islands(canvas)
    adrift = checks.floating(pieces)
    faults += ok("nothing is adrift", not adrift,
                 f"{len(pieces)} piece(s), {len(adrift)} floating")
    faults += ok("it returns everything it placed", placed.count() >= foot.count())

    # A rack whose posts miss is a slab in mid-air, and it stops rather than
    # building one. `checks.floating` would be the only thing left to notice.
    try:
        build.rack(canvas, foot, Mask(W, L), 1, 5, "minecraft:iron_bars",
                   deck="minecraft:smooth_stone_slab")
    except ValueError:
        faults += ok("a rack with no legs refuses to build", True)
    else:
        faults += ok("a rack with no legs refuses to build", False,
                     "it built a floating slab")
    return faults


def facade_case() -> int:
    """Two stretches of wall, one with loggias cut in and one blank."""
    print("[variety] a blank wing does not read as a balconied one")
    faults = 0
    frame = Frame((0.0, 0.0), 0.0, float(W), float(L))
    canvas = Canvas(W, 12, L)
    mass = rect(2, 38, 10, 20)
    canvas.fill(mass.outline(1.0), 0, 10, WALL)

    # Loggias in the left half only: cut out of the wall and glazed behind.
    loggias = Mask(W, L)
    for x in range(4, 18, 3):
        for z in (10, 19):
            loggias.set(x, z)
            loggias.set(x + 1, z)
    canvas.carve(loggias, 2, 9)

    left = checks.facade_mix(canvas, mass, frame, 2.0, 18.0)
    right = checks.facade_mix(canvas, mass, frame, 20.0, 38.0)
    far = checks.mix_apart(left["mix"], right["mix"])
    faults += ok("a cut wing reads apart from a blank one", far >= 0.10,
                 f"{far:.3f} apart, budget 0.10")

    # And two stretches of the same wall do not, or the row would fail on every
    # building that really is one facade.
    a = checks.facade_mix(canvas, mass, frame, 20.0, 28.0)
    b = checks.facade_mix(canvas, mass, frame, 29.0, 37.0)
    same = checks.mix_apart(a["mix"], b["mix"])
    faults += ok("two stretches of one wall read alike", same < 0.10,
                 f"{same:.3f} apart")
    faults += ok("both stretches actually had wall in them",
                 left["cells"] > 0 and right["cells"] > 0,
                 f"{left['cells']} and {right['cells']} cells")
    return faults


def main() -> int:
    faults = (woven_cases() + span_cases() + rack_case() + facade_case())
    print()
    if faults:
        print(f"{faults} fault(s): the pipeline has stopped seeing that a "
              "building can be two different things")
        return 1
    print("a building is allowed to be two different things, and all four "
          "mechanisms still see it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
