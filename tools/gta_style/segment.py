"""Find building footprints in the GTA map from the surface pass.

Anything eight blocks above the street plane is a candidate, minus the road
surface and the street furniture that would otherwise chain every block on a
side into one component -- trees, fences, walls, rails, lamps, the overhead
cables. A 3x3 opening breaks what is left of those chains, then each opened
core claims the mask around it back, so footprints are not eroded.
"""

from __future__ import annotations

import json

import numpy as np
from scipy import ndimage

X0, Z0 = 5120, 8192
GROUND = 33            # the modal street plane, as a height (top block + 1)

# Substrings, not names: state suffixes and the whole colour cross-product make
# an exact list unmaintainable, and a false positive here only costs a roof.
NOT_BUILDING = (
    "cyan_terracotta", "_concrete", "leaves", "_log", "_wood", "fence",
    "_wall", "rail", "tripwire", "chain", "_sign", "lantern", "lamp",
    "_carpet", "_bed", "vine", "_stem", "sapling", "flower", "grass", "bush",
    "cobweb", "ladder", "scaffold", "iron_bars", "glass_pane", "banner",
    "torch", "string", "_pot", "cactus", "bamboo", "snow", "water", "ice",
    "powder", "armor_stand", "head", "skull", "wire", "button", "pressure",
    "trapdoor", "_door", "anvil", "barrel", "chest", "campfire", "candle",
    "end_rod", "conduit",
)


def main():
    pal = open("tmp/gta/pal.txt").read().split("\n")
    h = np.load("tmp/gta/height.npy")
    top = np.load("tmp/gta/top.npy")

    bad = np.array([any(s in b for s in NOT_BUILDING) for b in pal])
    mask = (h >= GROUND + 8) & ~bad[top]

    core = ndimage.binary_opening(mask, np.ones((3, 3), bool))
    lab, n = ndimage.label(core, structure=np.ones((3, 3), int))
    # Give the eroded rim back to whichever core is nearest.
    _, idx = ndimage.distance_transform_edt(lab == 0, return_indices=True)
    grown = np.where(mask, lab[tuple(idx)], 0)

    rows = []
    for i, sl in enumerate(ndimage.find_objects(grown), start=1):
        if sl is None:
            continue
        sub = grown[sl] == i
        area = int(sub.sum())
        if area < 60:
            continue
        hh = h[sl][sub]
        xs, zs = sl
        w, d = xs.stop - xs.start, zs.stop - zs.start
        rows.append({
            "id": i,
            "x0": X0 + xs.start, "z0": Z0 + zs.start,
            "w": int(w), "d": int(d), "area": area,
            "fill": round(area / (w * d), 3),
            "top": int(hh.max()),
            "p95": int(np.percentile(hh, 95)),
            "median": int(np.median(hh)),
            "base": int(np.percentile(hh, 5)),
            "tall": int(np.percentile(hh, 95)) - GROUND,
            "peak": int(hh.max()) - GROUND,
        })

    rows.sort(key=lambda r: -r["tall"])
    json.dump(rows, open("tmp/gta/buildings.json", "w"), indent=1)
    np.save("tmp/gta/labels.npy", grown.astype(np.int32))

    print("components %d, kept %d" % (n, len(rows)))
    head = (f"{'id':>5} {'x':>6} {'z':>6} {'w':>4} {'d':>4} {'area':>6} "
            f"{'fill':>5} {'tall':>5} {'peak':>5}")
    print("\n-- tallest 40 --\n" + head)
    for r in rows[:40]:
        print(f"{r['id']:>5} {r['x0']:>6} {r['z0']:>6} {r['w']:>4} "
              f"{r['d']:>4} {r['area']:>6} {r['fill']:>5} {r['tall']:>5} "
              f"{r['peak']:>5}")

    tall = np.array([r["tall"] for r in rows])
    area = np.array([r["area"] for r in rows])
    print("\nheight above street, n=%d" % len(tall))
    print("  " + "  ".join("p%d=%d" % (p, np.percentile(tall, p))
                           for p in (10, 25, 50, 75, 90, 95, 99)))
    print("footprint area  " + "  ".join(
        "p%d=%d" % (p, np.percentile(area, p)) for p in (25, 50, 75, 90, 99)))


if __name__ == "__main__":
    main()
