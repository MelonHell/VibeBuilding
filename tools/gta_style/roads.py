"""Measure the street network: carriageway, markings, kerb, pavement.

Raw runs of the asphalt block are not lane widths -- the lane markings are
blocks too, and they cut every run. So the carriageway is asphalt plus anything
enclosed by asphalt, lane width is the gap between markings inside it, and the
kerb is what sits one step outside with its height step.
"""

from __future__ import annotations

import collections

import numpy as np
from scipy import ndimage

GROUND = 33
ASPHALT = "minecraft:cyan_terracotta"


def load():
    pal = open("tmp/gta/pal.txt").read().split("\n")
    h = np.load("tmp/gta/height.npy")
    top = np.load("tmp/gta/top.npy")
    name = np.array([p.split("[")[0] for p in pal])
    return pal, name, h, top


def main():
    pal, name, h, top = load()
    asphalt = np.isin(top, np.where(name == ASPHALT)[0])

    # Markings: not asphalt, but with asphalt on three sides or more.
    cross = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]])
    around = ndimage.convolve(asphalt.astype(np.uint8), cross, mode="constant")
    marking = (~asphalt) & (around >= 3)
    carriage = asphalt | marking
    carriage = ndimage.binary_closing(carriage, np.ones((3, 3), bool))

    print("asphalt %d cols, markings %d, carriageway %d"
          % (asphalt.sum(), marking.sum(), carriage.sum()))
    c = collections.Counter(top[marking].reshape(-1).tolist())
    print("-- what the markings are made of")
    for i, n in c.most_common(8):
        print("   %5.1f%%  %s" % (100 * n / marking.sum(), pal[i]))

    # Carriageway width: straight runs across, both axes, ignoring junctions.
    def runs(m, transpose):
        arr = m.T if transpose else m
        out = []
        for row in arr:
            n = 0
            for v in row:
                if v:
                    n += 1
                elif n:
                    out.append(n)
                    n = 0
            if n:
                out.append(n)
        return out

    widths = np.array(runs(carriage, False) + runs(carriage, True))
    widths = widths[(widths >= 4) & (widths <= 60)]
    cw = collections.Counter(widths.tolist())
    print("-- carriageway width (block runs)")
    print("   " + "  ".join("%d:%d" % kv for kv in
                            sorted(cw.items(), key=lambda kv: -kv[1])[:14]))
    print("   p25=%d p50=%d p75=%d p90=%d"
          % tuple(np.percentile(widths, [25, 50, 75, 90]).astype(int)))

    # Lane width: asphalt runs between markings, measured inside carriageway.
    lanes = np.array(runs(asphalt, False) + runs(asphalt, True))
    lanes = lanes[(lanes >= 2) & (lanes <= 20)]
    lc = collections.Counter(lanes.tolist())
    print("-- gap between markings (lane width)")
    print("   " + "  ".join("%d:%d" % kv for kv in
                            sorted(lc.items(), key=lambda kv: -kv[1])[:10]))

    # Kerb: the ring one step out, its block and its height step.
    ring = ndimage.binary_dilation(carriage, np.ones((3, 3), bool)) & ~carriage
    steps = h[ring] - GROUND
    sc = collections.Counter(steps.tolist())
    print("-- height step from street plane at the kerb")
    for k, n in sorted(sc.items())[:8]:
        print("   %+d : %5.1f%%" % (k, 100 * n / ring.sum()))
    kc = collections.Counter(top[ring].reshape(-1).tolist())
    print("-- kerb block")
    for i, n in kc.most_common(8):
        print("   %5.1f%%  %s" % (100 * n / ring.sum(), pal[i]))

    # Pavement: the band beyond the kerb, up to six blocks out.
    band = ndimage.binary_dilation(carriage, np.ones((13, 13), bool))
    band &= ~ndimage.binary_dilation(carriage, np.ones((5, 5), bool))
    pc = collections.Counter(top[band & (h <= GROUND + 2)].reshape(-1).tolist())
    tot = max(1, int((band & (h <= GROUND + 2)).sum()))
    print("-- what is 3..6 blocks off the kerb, at street level")
    for i, n in pc.most_common(10):
        print("   %5.1f%%  %s" % (100 * n / tot, pal[i]))

    np.save("tmp/gta/carriage.npy", carriage)


if __name__ == "__main__":
    main()
