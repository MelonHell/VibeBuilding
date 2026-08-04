"""Measure facade planes across a tile of the world.

Segmenting buildings is unreliable here -- downtown blocks touch, and a curved
row of towers comes back as one component. Facade planes do not need the
building: a cell is a facade cell if it is solid and the block one step out is
not, planes are the connected components of that in each slice, and every plane
tall enough to have storeys is a sample. Thousands of samples per tile beat a
handful of hand-picked buildings.

Each plane reports its wall material, its vertical pattern of wall versus
not-wall rows, and the period that pattern repeats at.
"""

from __future__ import annotations

import collections
import json
import sys

import numpy as np
from scipy import ndimage


from tools.gta_style import shared
from tools.gta_style import views
from tools.world_read import World

GROUND = 33


def periods(profile: np.ndarray, lo=2, hi=16):
    """Every lag scored by autocorrelation, for a 1-D pattern."""
    x = profile.astype(np.float32)
    x = x - x.mean()
    if not x.any():
        return []
    n = len(x)
    out = []
    for lag in range(lo, min(hi, n // 2) + 1):
        a, b = x[:n - lag], x[lag:]
        denom = np.sqrt((a * a).sum() * (b * b).sum())
        out.append((lag, float((a * b).sum() / denom) if denom else 0.0))
    return out


def best_period(profile: np.ndarray, lo=2, hi=16):
    scored = periods(profile, lo, hi)
    if not scored:
        return None, 0.0
    # Prefer the shortest lag within a whisker of the best, so a period of 4
    # is not reported as 8 just because 8 also lines up.
    top = max(s for _, s in scored)
    for lag, s in scored:
        if s >= top - 0.02:
            return lag, top
    return None, 0.0


def runs_of(mask: np.ndarray):
    out, n = [], 0
    for v in mask:
        if v:
            n += 1
        elif n:
            out.append(n)
            n = 0
    if n:
        out.append(n)
    return out


def scan(box, min_h=10, min_w=4):
    """Facade planes in a box, as dicts. Coordinates are box-local."""
    pal = box.palette
    empty = views._empty_mask(pal)
    solid = ~empty[box.blocks]
    y_ground = GROUND - box.origin[1]
    found = []

    directions = (("+x", 0, 1), ("-x", 0, -1), ("+z", 2, 1), ("-z", 2, -1))
    for name, axis, step in directions:
        outward = np.roll(solid, -step, axis=axis)
        # The rolled-in edge is unknown; drop that slice from consideration.
        edge = [slice(None)] * 3
        edge[axis] = 0 if step > 0 else -1
        outward[tuple(edge)] = True
        face = solid & ~outward

        n_slices = box.blocks.shape[axis]
        for k in range(n_slices):
            sl = [slice(None)] * 3
            sl[axis] = k
            plane = face[tuple(sl)]                 # (y, other) or (other, y)
            if axis == 0:
                plane2 = plane                      # (y, z)
                mats = box.blocks[k]                # (y, z)
            else:
                plane2 = plane.T                    # (y, x)
                mats = box.blocks[:, :, k].T        # (y, x)
            if plane2.sum() < min_h * min_w:
                continue
            lab, n = ndimage.label(plane2, structure=np.ones((3, 3), int))
            for i, s in enumerate(ndimage.find_objects(lab), start=1):
                if s is None:
                    continue
                sub = lab[s] == i
                ys, xs = s
                h, w = ys.stop - ys.start, xs.stop - xs.start
                if h < min_h or w < min_w or sub.sum() < min_h * min_w:
                    continue
                if ys.stop <= y_ground:             # wholly below the street
                    continue
                block = mats[s]
                counts = collections.Counter(block[sub].reshape(-1).tolist())
                wall, wall_n = counts.most_common(1)[0]
                share = wall_n / sub.sum()
                if share > 0.98 or share < 0.2:
                    continue                        # blank wall, or no wall
                # Per-row share of the wall material, over the plane's own rows.
                rowsum = sub.sum(axis=1)
                wallrow = ((block == wall) & sub).sum(axis=1)
                with np.errstate(invalid="ignore"):
                    prof = np.where(rowsum > 0, wallrow / np.maximum(rowsum, 1),
                                    np.nan)
                good = ~np.isnan(prof)
                if good.sum() < min_h:
                    continue
                prof = prof[good]
                per, score = best_period(prof)
                openrow = prof < 0.5
                second = [pal[c] for c, _ in counts.most_common(3)[1:]]
                found.append({
                    "axis": name, "k": int(k),
                    "y0": int(box.origin[1] + ys.start),
                    "y1": int(box.origin[1] + ys.stop),
                    "h": int(h), "w": int(w),
                    "wall": pal[wall],
                    "wall_share": round(float(share), 3),
                    "second": second,
                    "period": per, "score": round(score, 3),
                    "open_runs": runs_of(openrow),
                    "wall_runs": runs_of(~openrow),
                })
    return found


def main():
    x0, z0, size = (int(a) for a in sys.argv[1:4])
    tag = sys.argv[4] if len(sys.argv) > 4 else f"{x0}_{z0}"
    world = World(shared.WORLD)
    box = world.load(x0, z0, x0 + size, z0 + size, GROUND - 2, 260)
    got = scan(box)
    json.dump(got, open(f"tmp/gta/planes_{tag}.json", "w"))
    print(tag, "planes", len(got))


if __name__ == "__main__":
    main()
