"""Second round of measurements: texture recipes, relief, decks, colour.

    python -m tools.gta_style.detail [world-dir]

Reads the caches ``survey`` and ``segment`` leave in ``tmp/gta/`` and answers
the questions the first pass only raised: what exactly the surface noise is
made of, how deep a facade steps, whether a tower tapers, how an elevated road
is carried, how much of the city is lit, and what colour the whole thing is.
"""

from __future__ import annotations

import collections
import colorsys
import json
import sys

import numpy as np
from scipy import ndimage

from tools.gta_style import shared, views
from tools.gta_style.look import box_for
from tools.world_read import World

GROUND = 33
X0, Z0 = 5120, 8192
TILES = [(5200, 8560), (5450, 8560), (5700, 8560), (5450, 8800),
         (5700, 8800), (5950, 8800), (5300, 9200), (5700, 9300),
         (6100, 9300)]
# Anything that reads as a window from outside: real glass, and the dark
# panels this map uses instead of glass.
GLAZING = ("glass", "black_wool", "gray_wool", "black_stained",
           "dark_prismarine", "black_concrete")


def head(title):
    print("\n" + "=" * 68 + "\n" + title + "\n" + "=" * 68)


def _cache():
    pal = open("tmp/gta/pal.txt").read().split("\n")
    return (pal,
            np.load("tmp/gta/height.npy"),
            np.load("tmp/gta/top.npy"),
            np.load("tmp/gta/carriage.npy"))


def _flat_roofs(h, carriage):
    flat = np.ones_like(h, bool)
    for d in (1, -1):
        flat &= np.roll(h, d, 0) == h
        flat &= np.roll(h, d, 1) == h
    return ndimage.binary_erosion(flat & (h >= GROUND + 4) & ~carriage,
                                  np.ones((3, 3), bool))


# ------------------------------------------------------------------ texture

def report_dither(pal, h, top, carriage):
    head("SURFACE NOISE: WHAT IS MIXED WITH WHAT")
    base = np.array([p.split("[")[0][len("minecraft:"):] for p in pal])
    uniq = {n: i for i, n in enumerate(sorted(set(base.tolist())))}
    inv = {v: k for k, v in uniq.items()}
    mat = np.array([uniq[n] for n in base])[top]

    def pairs(mask, label, keep=8):
        c = collections.Counter()
        for ax in (0, 1):
            a = np.roll(mat, 1, ax)
            m = mask & np.roll(mask, 1, ax)
            d = (a != mat) & m
            for u, v in zip(mat[d].tolist(), a[d].tolist()):
                c[tuple(sorted((inv[u], inv[v])))] += 1
        tot = max(1, sum(c.values()))
        print("-- %s (%d adjacencies where the two differ)" % (label, tot))
        for (u, v), n in c.most_common(keep):
            ca = np.array(shared.colour("minecraft:" + u), float)
            cb = np.array(shared.colour("minecraft:" + v), float)
            print("   %5.1f%%  dRGB=%5.1f  %s + %s"
                  % (100 * n / tot, np.linalg.norm(ca - cb), u, v))
        return c

    roof = _flat_roofs(h, carriage)
    pairs(roof, "flat roofs")
    band = ndimage.binary_dilation(carriage, np.ones((9, 9), bool))
    band &= ~carriage & (h <= GROUND + 2)
    pairs(band, "pavement", 6)
    pairs(carriage, "carriageway", 4)

    # Is the commonest roof mix noise or a pattern?
    lab, _ = ndimage.label(roof, np.ones((3, 3), int))
    ia, ib = uniq.get("andesite"), uniq.get("stone")
    if ia is None or ib is None:
        return
    ratio, differ, parity, blobs = [], [], [], []
    for i, s in enumerate(ndimage.find_objects(lab), start=1):
        m = lab[s] == i
        if m.sum() < 300:
            continue
        v = mat[s]
        two = ((v == ia) | (v == ib)) & m
        if two.sum() < 0.7 * m.sum():
            continue
        a = (v == ia) & two
        ratio.append(a.sum() / two.sum())
        tot = dif = 0
        for ax in (0, 1):
            n = np.roll(two, 1, ax) & two
            tot += n.sum()
            dif += ((np.roll(a, 1, ax) != a) & n).sum()
        differ.append(dif / max(1, tot))
        xs, zs = np.nonzero(two)
        parity.append((a[xs, zs] == ((xs + zs) % 2 == 0)).mean())
        l2, n2 = ndimage.label(a, np.ones((3, 3), int))
        if n2:
            blobs += ndimage.sum(a, l2, range(1, n2 + 1)).tolist()
    if not ratio:
        return
    print("-- the andesite/stone mix, on the %d roofs made of it" % len(ratio))
    print("   andesite share: p25=%.2f median=%.2f p75=%.2f"
          % (*np.percentile(ratio, [25, 50, 75]),))
    print("   neighbour differs: median %.2f  (0.50 = white noise, "
          "1.00 = checkerboard)" % np.median(differ))
    print("   agreement with a checkerboard: median %.2f (0.50 = none)"
          % np.median(parity))
    b = np.array(blobs)
    print("   run of one material: median %d, mean %.1f, longest %d"
          % (np.median(b), b.mean(), b.max()))


# ------------------------------------------------------------------- relief

def _angle(lab, r):
    sl = (slice(r["x0"] - X0 - 1, r["x0"] - X0 + r["w"] + 1),
          slice(r["z0"] - Z0 - 1, r["z0"] - Z0 + r["d"] + 1))
    m = lab[sl] == r["id"]
    xs, zs = np.nonzero(m)
    if len(xs) < 150:
        return None
    p = np.stack([xs - xs.mean(), zs - zs.mean()])
    ev, evec = np.linalg.eigh(p @ p.T / len(xs))
    v = evec[:, np.argmax(ev)]
    a = np.degrees(np.arctan2(v[1], v[0])) % 90
    return min(a, 90 - a)


def _first_hit_depth(solid, axis):
    if axis in ("+x", "-x"):
        order = np.arange(solid.shape[0])
        if axis == "-x":
            order = order[::-1]
        st = solid[order]
    else:
        order = np.arange(solid.shape[2])
        if axis == "-z":
            order = order[::-1]
        st = solid.transpose(2, 1, 0)[order]
    return st.argmax(axis=0).astype(np.int32), st.any(axis=0)


def report_relief(world, rows, lab):
    head("FACADE RELIEF AND TOWER SECTION")
    # Relief only makes sense on buildings that are not stepping a diagonal.
    aligned = [r for r in rows
               if r["area"] >= 400 and (_angle(lab, r) or 99) < 4]
    agg = np.zeros(4)
    for r in aligned[:30]:
        box = box_for(r, margin=2, world=world)
        solid = ~views._empty_mask(box.palette)[box.blocks]
        solid[:, :GROUND - box.origin[1], :] = False
        for axis in ("-z", "+z", "-x", "+x"):
            hit, seen = _first_hit_depth(solid, axis)
            big = np.where(seen, hit, 10 ** 6)
            rel = hit - ndimage.minimum_filter(big, size=(1, 5))
            ok = seen & (rel >= 0) & (rel <= 8)
            if ok.sum() < 200:
                continue
            agg += np.bincount(np.clip(rel[ok], 0, 3), minlength=4)
    print("axis-aligned buildings read: %d" % len(aligned))
    print("how far a facade cell sits behind the nearest plane within five "
          "blocks:")
    print("   flat %.0f%%   1 block %.0f%%   2 blocks %.0f%%   3+ %.0f%%"
          % tuple(100 * agg / agg.sum()))

    tall = [r for r in rows if r["tall"] >= 45]
    prof, kinds, podium = [], collections.Counter(), []
    for r in tall:
        box = box_for(r, margin=2, world=world)
        solid = ~views._empty_mask(box.palette)[box.blocks]
        y0 = GROUND - box.origin[1]
        top = solid.shape[1] - 1
        while top > y0 and solid[:, top, :].sum() < 8:
            top -= 1
        height = top - y0
        if height < 40:
            continue
        a = np.array([solid[:, y0 + k, :].sum() for k in range(height + 1)],
                     float)
        ref = a[int(height * 0.35)]
        if ref < 40:
            continue
        prof.append([a[int(height * f)] / ref
                     for f in (0.02, 0.10, 0.35, 0.50, 0.70, 0.85, 0.95)])
        body = a[int(height * 0.35):int(height * 0.85)] / ref
        kinds["prismatic" if body.std() < 0.06
              else ("stepped" if body.min() < 0.8 else "other")] += 1
        k = 0
        while k < height and a[k] > 1.25 * ref:
            k += 1
        podium.append(k)
    p = np.array(prof)
    print("towers profiled: %d" % len(p))
    print("section against the section at 35% of height, "
          "at 2/10/35/50/70/85/95%:")
    for name, q in (("median", 50), ("p25", 25), ("p75", 75)):
        print("   %-7s" % name
              + "  ".join("%.2f" % v for v in np.percentile(p, q, axis=0)))
    print("body: " + ", ".join("%s %d" % kv for kv in kinds.items()))
    pod = np.array(podium)
    print("podium height: median %d, p75 %d, p90 %d; none at all %.0f%%"
          % (np.median(pod), np.percentile(pod, 75), np.percentile(pod, 90),
             100 * (pod == 0).mean()))


# ----------------------------------------------------------------- openings

def report_openings(world):
    head("OPENINGS AND GLAZING")
    sizes, pitch_x, pitch_y = (collections.Counter() for _ in range(3))
    bands = collections.defaultdict(lambda: [0, 0])
    for x, z in TILES:
        box = world.load(x, z, x + 128, z + 128, GROUND, 200)
        solid = ~views._empty_mask(box.palette)[box.blocks]
        glazed = np.array([any(g in p for g in GLAZING) for p in box.palette])
        face = np.zeros_like(solid)
        for ax in (0, 2):
            for d in (1, -1):
                face |= solid & ~np.roll(solid, d, axis=ax)
        for k in range(box.blocks.shape[1]):
            m = face[:, k, :]
            if m.sum() < 50:
                continue
            b = min(k // 8, 12)
            bands[b][0] += int(m.sum())
            bands[b][1] += int(glazed[box.blocks[:, k, :]][m].sum())
        for axis, d in (("-z", 1), ("+z", -1)):
            plane = solid & ~np.roll(solid, d, axis=2)
            win = plane & glazed[box.blocks]
            for zi in range(win.shape[2]):
                m = win[:, :, zi]
                if m.sum() < 12:
                    continue
                lab, _ = ndimage.label(m, np.ones((3, 3), int))
                corners = []
                for i, s in enumerate(ndimage.find_objects(lab), start=1):
                    a = lab[s] == i
                    wd = s[0].stop - s[0].start
                    ht = s[1].stop - s[1].start
                    if a.sum() < 2 or wd > 12 or ht > 12:
                        continue
                    if a.sum() / (wd * ht) < 0.75:
                        continue
                    sizes[(wd, ht)] += 1
                    corners.append((s[0].start, s[1].start))
                corners.sort()
                for (a1, b1), (a2, b2) in zip(corners, corners[1:]):
                    if b1 == b2 and 0 < a2 - a1 <= 12:
                        pitch_x[a2 - a1] += 1
                for (a1, b1), (a2, b2) in zip(sorted(corners),
                                              sorted(corners)[1:]):
                    if a1 == a2 and 0 < b2 - b1 <= 12:
                        pitch_y[b2 - b1] += 1
    tot = max(1, sum(sizes.values()))
    print("window rectangles: %d" % tot)
    print("   size (wide x tall): " + " ".join(
        "%dx%d:%.0f%%" % (a, b, 100 * n / tot)
        for (a, b), n in sizes.most_common(6)))
    for name, d in (("horizontal", pitch_x), ("vertical", pitch_y)):
        s = max(1, sum(d.values()))
        print("   %s pitch: " % name + " ".join(
            "%d:%.0f%%" % (k, 100 * v / s) for k, v in d.most_common(5)))
    print("share of the exposed facade that is glazing or dark panel:")
    for b in sorted(bands):
        t, g = bands[b]
        print("   +%3d..+%3d  %5.1f%%" % (b * 8, b * 8 + 7, 100 * g / t))


# --------------------------------------------------------------- structures

def report_decks(world, h, carriage, pal, top):
    head("ELEVATED ROADS")
    el = carriage & (h >= GROUND + 6)
    supported = spanning = 0
    thick = []
    for x in range(X0, X0 + 1536, 256):
        for z in range(Z0, Z0 + 1536, 256):
            sub = el[x - X0:x - X0 + 256, z - Z0:z - Z0 + 256]
            if sub.sum() < 200:
                continue
            box = world.load(x, z, x + 256, z + 256, GROUND, 80)
            solid = ~views._empty_mask(box.palette)[box.blocks]
            hh = h[x - X0:x - X0 + 256, z - Z0:z - Z0 + 256]
            xs, zs = np.nonzero(sub)
            for i in range(0, len(xs), 3):
                xi, zi = xs[i], zs[i]
                k = hh[xi, zi] - 1 - GROUND
                if k < 6 or k >= solid.shape[1]:
                    continue
                col = solid[xi, :k + 1, zi]
                t, y = 0, k
                while y >= 0 and col[y]:
                    t += 1
                    y -= 1
                thick.append(t)
                if t >= k + 1:
                    supported += 1
                else:
                    spanning += 1
    tot = max(1, supported + spanning)
    th = np.array(thick)
    print("elevated road columns sampled: %d" % tot)
    print("   solid to the ground (embankment) %.0f%%, "
          "spanning over air (viaduct) %.0f%%"
          % (100 * supported / tot, 100 * spanning / tot))
    print("   deck depth under the running surface: median %d, p75 %d, p90 %d"
          % (np.median(th), np.percentile(th, 75), np.percentile(th, 90)))
    edge = ndimage.binary_dilation(el, np.ones((3, 3), bool)) & ~el
    edge &= h >= GROUND + 5
    c = collections.Counter(top[edge].reshape(-1).tolist())
    t = max(1, int(edge.sum()))
    print("   the deck edge: " + ", ".join(
        "%s %.0f%%" % (pal[i].split("[")[0][len("minecraft:"):], 100 * n / t)
        for i, n in c.most_common(4)))
    grown = ndimage.grey_dilation(np.where(el, h, -999), size=(3, 3))
    rc = collections.Counter((h - grown)[edge].tolist())
    print("   parapet above the deck: " + " ".join(
        "%+d:%.0f%%" % (k, 100 * v / t) for k, v in rc.most_common(4)))


def report_land_use(h, top, carriage, pal, lab):
    head("LAND USE, LIGHT, SIGNS")
    base = np.array([p.split("[")[0][len("minecraft:"):] for p in pal])
    occ = h > -64
    green = np.isin(top, [i for i, n in enumerate(base)
                          if n in ("grass_block", "short_grass", "tall_grass",
                                   "fern", "dirt", "coarse_dirt", "podzol",
                                   "moss_block", "lime_terracotta",
                                   "green_terracotta")
                          or "leaves" in n or "flower" in n])
    wet = np.isin(top, [i for i, n in enumerate(base)
                        if n in ("water", "sand", "sandstone",
                                 "smooth_sandstone", "gravel", "clay")])
    wet &= h < 30
    build = lab > 0
    rest = occ & ~carriage & ~green & ~wet & ~build
    for name, m in (("carriageway", carriage & occ),
                    ("segmented building footprint", build & occ),
                    ("green", green & occ),
                    ("water and river bed", wet & occ),
                    ("the rest at grade: pavement, plaza, parking, lots",
                     rest)):
        print("   %-52s %5.1f%%" % (name, 100 * m.sum() / occ.sum()))

    d = ndimage.distance_transform_edt(~carriage)
    rim = build & ~ndimage.binary_erosion(build, np.ones((3, 3), bool))
    v = d[rim]
    print("wall to the nearest carriageway: p25=%.0f p50=%.0f p75=%.0f; "
          "on the kerb (<=2) %.0f%%"
          % (*np.percentile(v, [25, 50, 75]), 100 * (v <= 2).mean()))

    layers = json.loads(open("tmp/gta/layers.json").read())
    tot = collections.Counter()
    for c in layers.values():
        for k, n in c.items():
            if k != "minecraft:air":
                tot[k] += n
    lights = {"sea_lantern", "redstone_lamp", "glowstone", "beacon", "torch",
              "wall_torch", "end_rod", "shroomlight", "campfire", "lantern",
              "soul_lantern", "jack_o_lantern", "froglight"}
    lit = sum(v for k, v in tot.items()
              if k[len("minecraft:"):] in lights)
    print("light-emitting blocks: %d in the whole world (%.4f%% of non-air) -- "
          "%s" % (lit, 100 * lit / sum(tot.values()),
                  ", ".join("%s %d" % (k[len("minecraft:"):], v)
                            for k, v in tot.most_common()
                            if k[len("minecraft:"):] in lights)[:120]))
    signs = collections.Counter({k: v for k, v in tot.items()
                                 if "banner" in k or k.endswith("_sign")})
    print("signage blocks: %d -- %s" % (
        sum(signs.values()),
        ", ".join("%s %d" % (k[len("minecraft:"):], v)
                  for k, v in signs.most_common(4))))


def report_colour(pal, h, top):
    head("COLOUR")
    lut = shared.palette_lut(pal).astype(float)
    occ = h > -64
    cnt = np.bincount(top[occ].reshape(-1), minlength=len(pal))
    w = cnt / cnt.sum()
    mean = (lut * w[:, None]).sum(axis=0)
    hsv = np.array([colorsys.rgb_to_hsv(*(c / 255)) for c in lut])
    print("area-weighted mean surface colour: rgb%s"
          % (tuple(mean.round().astype(int).tolist()),))
    print("mean saturation %.3f, mean value %.3f"
          % ((hsv[:, 1] * w).sum(), (hsv[:, 2] * w).sum()))
    for lim in (0.05, 0.10, 0.20):
        print("   surface below saturation %.2f: %5.1f%%"
              % (lim, 100 * w[hsv[:, 1] < lim].sum()))
    by = collections.Counter()
    for i, c in enumerate(lut):
        if w[i]:
            by[tuple(c.astype(int).tolist())] += w[i]
    print("the colours that cover the city:")
    for c, v in by.most_common(10):
        share = collections.Counter()
        for i in range(len(pal)):
            if cnt[i] and tuple(lut[i].astype(int).tolist()) == c:
                share[pal[i][len("minecraft:"):].split("[")[0]] += cnt[i]
        names = [k for k, _ in share.most_common()]
        print("   %5.2f%%  rgb%-18s %s"
              % (100 * v, str(c), ", ".join(names[:3])))


def main():
    world = World(sys.argv[1] if len(sys.argv) > 1 else shared.WORLD)
    pal, h, top, carriage = _cache()
    rows = json.loads(open("tmp/gta/buildings.json").read())
    lab = np.load("tmp/gta/labels.npy")
    report_dither(pal, h, top, carriage)
    report_relief(world, rows, lab)
    report_openings(world)
    report_decks(world, h, carriage, pal, top)
    report_land_use(h, top, carriage, pal, lab)
    report_colour(pal, h, top)


if __name__ == "__main__":
    main()
