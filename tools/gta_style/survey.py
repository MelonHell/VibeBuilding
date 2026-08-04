"""Every number the findings note quotes, in one run.

    python -m tools.gta_style.survey <world-dir>

Writes its caches to ``tmp/gta/`` -- a surface pass (height and top block per
column), a per-layer block census, and the facade planes -- then prints the
measurements. The caches are what ``segment``, ``roads``, ``look`` and ``zoom``
read, so run this first.
"""

from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

import numpy as np
from scipy import ndimage

from tools.gta_style import planes, shared, views
from tools.world_read import World

CACHE = Path("tmp/gta")
GROUND = 33            # the modal street plane, as a height (top block + 1)
ASPHALT = "minecraft:cyan_terracotta"
TILES = [(5200, 8560), (5450, 8560), (5700, 8560), (5450, 8800),
         (5700, 8800), (5950, 8800), (5300, 9200), (5700, 9300),
         (6100, 9300)]


def head(title):
    print("\n" + "=" * 68 + "\n" + title + "\n" + "=" * 68)


# ---------------------------------------------------------------- caches

def surface(world) -> tuple:
    CACHE.mkdir(parents=True, exist_ok=True)
    hp, tp, pp = CACHE / "height.npy", CACHE / "top.npy", CACHE / "pal.txt"
    if hp.exists() and tp.exists() and pp.exists():
        return (np.load(hp), np.load(tp), pp.read_text().split("\n"))
    x0, z0, x1, z1 = world.bounds()
    h, top, pal = world.surface(x0, z0, x1, z1)
    np.save(hp, h)
    np.save(tp, top)
    pp.write_text("\n".join(pal))
    return h, top, pal


def layers(world) -> dict:
    path = CACHE / "layers.json"
    if path.exists():
        return {int(k): collections.Counter(v)
                for k, v in json.loads(path.read_text()).items()}
    out = collections.defaultdict(collections.Counter)
    for rx, rz in world.regions():
        for cx in range(rx * 32, (rx + 1) * 32):
            for cz in range(rz * 32, (rz + 1) * 32):
                chunk = world.chunk(cx, cz)
                if chunk is None:
                    continue
                for sy, pal, idx in world.chunk_sections(chunk):
                    names = [p.split("[")[0] for p in pal]
                    if idx is None:
                        if names[0] == "minecraft:air":
                            continue
                        for k in range(16):
                            out[sy * 16 + k][names[0]] += 256
                        continue
                    cube = idx.reshape(16, 16, 16)
                    for k in range(16):
                        bc = np.bincount(cube[k].reshape(-1), minlength=len(pal))
                        d = out[sy * 16 + k]
                        for i, n in enumerate(bc):
                            if n:
                                d[names[i]] += int(n)
    path.write_text(json.dumps({str(k): dict(v) for k, v in out.items()}))
    return dict(out)


def facade_planes(world) -> list:
    path = CACHE / "planes_all.json"
    if path.exists():
        return json.loads(path.read_text())
    x0, z0, x1, z1 = world.bounds()
    got = []
    for x in range(x0, x1, 256):
        for z in range(z0, z1, 256):
            box = world.load(x, z, x + 256, z + 256, GROUND - 2, 260)
            if not box.blocks.any():
                continue
            got += planes.scan(box)
    path.write_text(json.dumps(got))
    return got


# ------------------------------------------------------------ measurements

def report_massing(h, top, pal):
    head("MASSING")
    occ = h > -64
    v = h[occ]
    print("built columns %d; height p50=%d p90=%d p99=%d max=%d (street=%d)"
          % (occ.sum(), *np.percentile(v, [50, 90, 99, 100]).astype(int),
             GROUND))
    c = collections.Counter(v.tolist())
    print("commonest surface heights: "
          + "  ".join("y=%d:%d" % kv for kv in c.most_common(5)))


def report_roads(h, top, pal):
    head("STREETS")
    name = np.array([p.split("[")[0] for p in pal])
    asphalt = np.isin(top, np.where(name == ASPHALT)[0])
    cross = np.array([[0, 1, 0], [1, 0, 1], [0, 1, 0]])
    around = ndimage.convolve(asphalt.astype(np.uint8), cross, mode="constant")
    marking = (~asphalt) & (around >= 3)
    carriage = ndimage.binary_closing(asphalt | marking, np.ones((3, 3), bool))
    np.save(CACHE / "carriage.npy", carriage)

    occ = h > -64
    print("asphalt is %s, %.1f%% of the built area"
          % (ASPHALT, 100 * asphalt.sum() / occ.sum()))
    c = collections.Counter(top[carriage].reshape(-1).tolist())
    tot = carriage.sum()
    print("carriageway make-up: "
          + ", ".join("%s %.1f%%" % (pal[i].split(":")[1].split("[")[0],
                                     100 * n / tot)
                      for i, n in c.most_common(4)))

    def runs(m, t):
        arr = m.T if t else m
        out, n = [], 0
        for row in arr:
            for v in row:
                if v:
                    n += 1
                elif n:
                    out.append(n)
                    n = 0
            if n:
                out.append(n)
                n = 0
        return out

    w = np.array(runs(carriage, False) + runs(carriage, True))
    w = w[(w >= 4) & (w <= 60)]
    print("carriageway width p25=%d p50=%d p75=%d p90=%d"
          % tuple(np.percentile(w, [25, 50, 75, 90]).astype(int)))
    lanes = np.array(runs(asphalt, False) + runs(asphalt, True))
    lanes = lanes[(lanes >= 2) & (lanes <= 20)]
    lc = collections.Counter(lanes.tolist())
    print("gap between markings: "
          + " ".join("%d:%d" % kv for kv in
                     sorted(lc.items(), key=lambda kv: -kv[1])[:6]))

    lab, _ = ndimage.label(marking, np.ones((3, 3), int))
    dims = collections.Counter()
    for i, s in enumerate(ndimage.find_objects(lab), start=1):
        a = lab[s] == i
        if a.sum() > 40:
            continue
        dims[(min(s[0].stop - s[0].start, s[1].stop - s[1].start),
              max(s[0].stop - s[0].start, s[1].stop - s[1].start))] += 1
    print("marking piece sizes: "
          + " ".join("%dx%d:%d" % (a, b, n) for (a, b), n in dims.most_common(5)))

    ring = ndimage.binary_dilation(carriage, np.ones((3, 3), bool)) & ~carriage
    step = collections.Counter((h[ring] - GROUND).tolist())
    print("kerb height step: "
          + " ".join("%+d:%.0f%%" % (k, 100 * n / ring.sum())
                     for k, n in step.most_common(4)))
    is_slab = np.array(["_slab" in p and "type=bottom" in p for p in pal])
    print("kerb block is a bottom slab %.0f%% of the time"
          % (100 * is_slab[top[ring]].mean()))
    return carriage


def report_surfaces(h, top, pal, carriage):
    head("SURFACE TEXTURE")
    base = np.array([p.split("[")[0] for p in pal])
    uniq = {n: i for i, n in enumerate(sorted(set(base.tolist())))}
    inv = {v: k for k, v in uniq.items()}
    mat = np.array([uniq[n] for n in base])[top]

    flat = np.ones_like(h, bool)
    for d in (1, -1):
        flat &= np.roll(h, d, 0) == h
        flat &= np.roll(h, d, 1) == h
    roof = ndimage.binary_erosion(flat & (h >= GROUND + 4) & ~carriage,
                                  np.ones((3, 3), bool))
    lab, n = ndimage.label(roof, np.ones((3, 3), int))
    kinds, edges, used = [], [], collections.Counter()
    seen = 0
    for i, s in enumerate(ndimage.find_objects(lab), start=1):
        m = lab[s] == i
        if m.sum() < 120:
            continue
        seen += 1
        v = mat[s][m]
        c = collections.Counter(v.tolist())
        keep = [k for k, cn in c.items() if cn / len(v) >= 0.03]
        kinds.append(len(keep))
        for k in keep:
            used[inv[k]] += 1
        sub = mat[s]
        dxx = (sub[1:, :] != sub[:-1, :]) & m[1:, :] & m[:-1, :]
        vxx = m[1:, :] & m[:-1, :]
        dzz = (sub[:, 1:] != sub[:, :-1]) & m[:, 1:] & m[:, :-1]
        vzz = m[:, 1:] & m[:, :-1]
        if vxx.sum() + vzz.sum():
            edges.append((dxx.sum() + dzz.sum()) / (vxx.sum() + vzz.sum()))
    kinds, edges = np.array(kinds), np.array(edges)
    print("flat roof plates >=120 cols: %d" % seen)
    print("materials per roof (>=3%% of it): p25=%d median=%d p75=%d p90=%d"
          % (np.percentile(kinds, 25), np.median(kinds),
             np.percentile(kinds, 75), np.percentile(kinds, 90)))
    print("neighbouring roof blocks differ: p25=%.0f%% median=%.0f%% p75=%.0f%%"
          % (100 * np.percentile(edges, 25), 100 * np.median(edges),
             100 * np.percentile(edges, 75)))
    print("roof materials, share of roofs containing them: "
          + ", ".join("%s %.0f%%" % (k.split(":")[1], 100 * v / seen)
                      for k, v in used.most_common(8)))


def report_orientation():
    head("ORIENTATION")
    path = CACHE / "labels.npy"
    if not path.exists():
        print("run `python -m tools.gta_style.segment` first")
        return
    lab = np.load(path)
    rows = json.loads((CACHE / "buildings.json").read_text())
    x0, z0 = 5120, 8192
    angs = []
    for r in rows:
        if r["area"] < 150:
            continue
        sl = (slice(r["x0"] - x0 - 1, r["x0"] - x0 + r["w"] + 1),
              slice(r["z0"] - z0 - 1, r["z0"] - z0 + r["d"] + 1))
        m = lab[sl] == r["id"]
        xs, zs = np.nonzero(m)
        if len(xs) < 150:
            continue
        p = np.stack([xs - xs.mean(), zs - zs.mean()])
        ev, evec = np.linalg.eigh(p @ p.T / len(xs))
        v = evec[:, np.argmax(ev)]
        a = np.degrees(np.arctan2(v[1], v[0])) % 90
        angs.append(min(a, 90 - a))
    angs = np.array(angs)
    print("n=%d  angle off the grid p25=%.0f p50=%.0f p75=%.0f deg"
          % (len(angs), *np.percentile(angs, [25, 50, 75])))
    print("within 3 deg of axis-aligned %.0f%%; over 15 deg off %.0f%%"
          % (100 * (angs < 3).mean(), 100 * (angs > 15).mean()))


def report_facades(P):
    head("FACADE RHYTHM")

    def bucket(p):
        t = p["y1"] - GROUND
        return "high" if t >= 60 else ("mid" if t >= 25 else "low")

    for b in ("high", "mid", "low"):
        sel = [p for p in P if bucket(p) == b]
        per = [p["period"] for p in sel if p["period"] and p["score"] >= 0.55]
        c = collections.Counter(per)
        print("-- %s (%d planes, %d with a clear period)"
              % (b, len(sel), len(per)))
        print("   period: " + " ".join(
            "%d:%.0f%%" % (k, 100 * v / max(1, len(per)))
            for k, v in sorted(c.items())[:8]))
        pairs = collections.Counter(
            (p["wall"].split(":")[1].split("[")[0],
             p["second"][0].split(":")[1].split("[")[0])
            for p in sel if p["second"])
        tot = max(1, sum(pairs.values()))
        print("   wall + companion: " + ", ".join(
            "%s+%s %.0f%%" % (w, s, 100 * v / tot)
            for (w, s), v in pairs.most_common(5)))


def report_grain(world):
    head("FACADE GRAIN")
    vert, horiz = collections.Counter(), collections.Counter()
    par_hit, par_tot = np.zeros(2), np.zeros(2)
    for x, z in TILES:
        box = world.load(x, z, x + 128, z + 128, GROUND, 180)
        empty = views._empty_mask(box.palette)
        solid = ~empty[box.blocks]
        face = solid & ~np.roll(solid, 1, axis=2)
        m = box.blocks
        same = (m[:, 1:, :] == m[:, :-1, :]) & face[:, 1:, :] & face[:, :-1, :]
        valid = face[:, 1:, :] & face[:, :-1, :]
        ys = np.arange(1, m.shape[1]) + GROUND
        for par in (0, 1):
            sel = ys % 2 == par
            par_tot[par] += valid[:, sel, :].sum()
            par_hit[par] += same[:, sel, :].sum()
        for xi in range(0, m.shape[0], 3):
            for zi in range(0, m.shape[2], 3):
                _tally(m[xi, :, zi], face[xi, :, zi], vert)
        for zi in range(0, m.shape[2], 2):
            for yi in range(0, m.shape[1], 2):
                _tally(m[:, yi, zi], face[:, yi, zi], horiz)
    for label, d in (("vertical", vert), ("horizontal", horiz)):
        s = sum(d.values())
        print("%s run of one material: " % label
              + " ".join("%d:%.0f%%" % (k, 100 * d[k] / s) for k in range(1, 5)))
    print("P(row y == row y+1) on facades: y even %.2f, y odd %.2f"
          % (par_hit[0] / par_tot[0], par_hit[1] / par_tot[1]))


def _tally(line, mask, counter):
    cur, n = None, 0
    for v, f in zip(line, mask):
        if f and v == cur:
            n += 1
        else:
            if n:
                counter[n] += 1
            cur = v if f else None
            n = 1 if f else 0
    if n:
        counter[n] += 1


def report_volume(L):
    head("VOLUME AND FAMILIES")
    tot = collections.Counter()
    for c in L.values():
        for k, v in c.items():
            if k != "minecraft:air":
                tot[k] += v
    grand = sum(tot.values())
    nonstone = grand - tot["minecraft:stone"]
    print("non-air blocks %.1f M; %.0f%% of them are plain stone (fill)"
          % (grand / 1e6, 100 * tot["minecraft:stone"] / grand))
    fam = collections.Counter()
    rules = [("_stairs", "stairs"), ("_slab", "slab"), ("_wall", "wall"),
             ("_fence", "fence"), ("_carpet", "carpet"), ("_wool", "wool"),
             ("_terracotta", "terracotta"), ("_pane", "pane")]
    for k, v in tot.items():
        n = k[len("minecraft:"):]
        if "glass" in n and not n.endswith("_pane"):
            fam["glass"] += v
            continue
        if "leaves" in n:
            fam["leaves"] += v
            continue
        for suf, name in rules:
            if n.endswith(suf):
                fam[name] += v
                break
    print("share of the non-stone volume: "
          + ", ".join("%s %.1f%%" % (k, 100 * v / nonstone)
                      for k, v in fam.most_common(9)))
    print("top materials: " + ", ".join(
        "%s %.1f%%" % (k.split(":")[1], 100 * v / grand)
        for k, v in tot.most_common(10)))


def main():
    world = World(sys.argv[1] if len(sys.argv) > 1 else shared.WORLD)
    h, top, pal = surface(world)
    report_massing(h, top, pal)
    carriage = report_roads(h, top, pal)
    report_surfaces(h, top, pal, carriage)
    report_orientation()
    report_facades(facade_planes(world))
    report_grain(world)
    report_volume(layers(world))


if __name__ == "__main__":
    main()
