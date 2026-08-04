"""Reader for Minecraft Anvil worlds (``region/*.mca``), stdlib plus numpy.

A region file is 32x32 chunks. Its first 4 KiB is a location table -- one
four-byte entry per chunk holding a three-byte offset in 4 KiB sectors and a
one-byte sector count, zero when the chunk was never generated. The next 4 KiB
is timestamps. Chunk payloads follow, each a four-byte length, a one-byte
compression id (1 gzip, 2 zlib, 3 none) and the compressed chunk NBT.

Inside a chunk, ``sections`` is a list of 16-block-tall slices. Each carries a
``block_states`` compound with a ``palette`` of block names and a ``data`` long
array of palette indices, packed at ``max(4, ceil(log2(len(palette))))`` bits
per entry and never spanning a long boundary. A section with a single-entry
palette omits ``data`` entirely.

    world = World("saves/GTA_5_cut_2")
    box = world.load(x0, z0, x1, z1)          # dense voxels for a box
    box[x, y, z]                              # index into box.palette

Indices are into a per-box palette of block strings in the project's usual
``minecraft:stone[axis=y]`` form, with index 0 always ``minecraft:air``.
"""

from __future__ import annotations

import gzip
import math
import zlib
from pathlib import Path

import numpy as np

from blockwright import nbt

SECTOR = 4096
AIR = "minecraft:air"


def _block_name(entry: dict) -> str:
    name = entry["Name"]
    props = entry.get("Properties")
    if not props:
        return name
    inner = ",".join(f"{k}={v}" for k, v in sorted(props.items()))
    return f"{name}[{inner}]"


def _unpack(data, count: int, bits: int) -> np.ndarray:
    """Unpack ``count`` indices of ``bits`` each from a NBT long array.

    Since 1.16 an entry never straddles two longs, so a long holds
    ``64 // bits`` entries and the leftover high bits are padding.
    """
    longs = np.asarray(data, dtype=np.int64).view(np.uint64)
    per = 64 // bits
    shifts = (np.arange(per, dtype=np.uint64) * np.uint64(bits))
    out = (longs[:, None] >> shifts[None, :]) & np.uint64((1 << bits) - 1)
    return out.reshape(-1)[:count].astype(np.uint16)


class Box:
    """A dense block box with its own palette.

    ``blocks`` is indexed ``[x, y, z]`` in box-local coordinates; ``origin``
    is the world coordinate of ``[0, 0, 0]``.
    """

    def __init__(self, blocks: np.ndarray, palette: list[str],
                 origin: tuple[int, int, int]):
        self.blocks = blocks
        self.palette = palette
        self.origin = origin

    @property
    def shape(self):
        return self.blocks.shape

    def index(self, block: str) -> int | None:
        try:
            return self.palette.index(block)
        except ValueError:
            return None

    def counts(self) -> list[tuple[str, int]]:
        """Every block in the box with its count, commonest first."""
        hits = np.bincount(self.blocks.reshape(-1),
                           minlength=len(self.palette))
        pairs = [(self.palette[i], int(n)) for i, n in enumerate(hits) if n]
        pairs.sort(key=lambda p: -p[1])
        return pairs

    def solid_mask(self) -> np.ndarray:
        """True where the block is neither air nor a fluid."""
        empty = {i for i, b in enumerate(self.palette)
                 if b.split("[")[0] in (AIR, "minecraft:cave_air",
                                        "minecraft:void_air",
                                        "minecraft:water",
                                        "minecraft:lava")}
        keep = np.ones(len(self.palette), dtype=bool)
        for i in empty:
            keep[i] = False
        return keep[self.blocks]


class World:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.region_dir = self.path / "region"
        if not self.region_dir.is_dir():
            raise FileNotFoundError(f"no region/ under {self.path}")
        self._cache: dict[tuple[int, int], bytes] = {}

    def regions(self) -> list[tuple[int, int]]:
        out = []
        for f in sorted(self.region_dir.glob("r.*.*.mca")):
            _, rx, rz, _ = f.name.split(".")
            out.append((int(rx), int(rz)))
        return out

    def bounds(self) -> tuple[int, int, int, int]:
        """Block-coordinate bounds of every region present, ``x0 z0 x1 z1``."""
        rs = self.regions()
        xs = [r[0] for r in rs]
        zs = [r[1] for r in rs]
        return (min(xs) * 512, min(zs) * 512,
                (max(xs) + 1) * 512, (max(zs) + 1) * 512)

    def _region_bytes(self, rx: int, rz: int) -> bytes | None:
        key = (rx, rz)
        if key not in self._cache:
            f = self.region_dir / f"r.{rx}.{rz}.mca"
            self._cache[key] = f.read_bytes() if f.is_file() else b""
            # One region is 4.5 MB at most; hold a handful, not all nine.
            if len(self._cache) > 4:
                for old in list(self._cache)[:-4]:
                    del self._cache[old]
        return self._cache[key] or None

    def chunk(self, cx: int, cz: int) -> dict | None:
        """The raw chunk NBT at chunk coordinates, or None if ungenerated."""
        rx, rz = cx >> 5, cz >> 5
        raw = self._region_bytes(rx, rz)
        if raw is None:
            return None
        slot = ((cz & 31) * 32 + (cx & 31)) * 4
        off = int.from_bytes(raw[slot:slot + 3], "big")
        count = raw[slot + 3]
        if not off or not count:
            return None
        head = off * SECTOR
        length = int.from_bytes(raw[head:head + 4], "big")
        scheme = raw[head + 4]
        body = raw[head + 5:head + 4 + length]
        if scheme == 1:
            body = gzip.decompress(body)
        elif scheme == 2:
            body = zlib.decompress(body)
        elif scheme != 3:
            raise ValueError(f"chunk {cx},{cz}: compression {scheme}")
        return nbt.loads(body)[1]

    def chunk_sections(self, chunk: dict):
        """Yield ``(section_y, palette, indices)`` for a decoded chunk.

        ``indices`` is a 4096-long array in the section's own YZX order, or
        None when the section is one block throughout.
        """
        for sec in chunk.get("sections", []):
            states = sec.get("block_states")
            if not states:
                continue
            palette = [_block_name(e) for e in states["palette"]]
            data = states.get("data")
            if data is None:
                yield int(sec["Y"]), palette, None
                continue
            bits = max(4, math.ceil(math.log2(len(palette))))
            yield int(sec["Y"]), palette, _unpack(data, 4096, bits)

    def load(self, x0: int, z0: int, x1: int, z1: int,
             y0: int = -64, y1: int = 320) -> Box:
        """Dense blocks over ``[x0, x1) x [y0, y1) x [z0, z1)``."""
        w, h, d = x1 - x0, y1 - y0, z1 - z0
        blocks = np.zeros((w, h, d), dtype=np.uint16)
        palette = [AIR]
        lookup = {AIR: 0}

        for cx in range(x0 >> 4, ((x1 - 1) >> 4) + 1):
            for cz in range(z0 >> 4, ((z1 - 1) >> 4) + 1):
                chunk = self.chunk(cx, cz)
                if chunk is None:
                    continue
                bx, bz = cx * 16, cz * 16
                # Overlap of this chunk with the requested box.
                sx0, sx1 = max(x0, bx), min(x1, bx + 16)
                sz0, sz1 = max(z0, bz), min(z1, bz + 16)
                if sx0 >= sx1 or sz0 >= sz1:
                    continue

                for sy, sec_pal, idx in self.chunk_sections(chunk):
                    ty0, ty1 = sy * 16, sy * 16 + 16
                    cy0, cy1 = max(y0, ty0), min(y1, ty1)
                    if cy0 >= cy1:
                        continue
                    remap = np.empty(len(sec_pal), dtype=np.uint16)
                    for i, name in enumerate(sec_pal):
                        got = lookup.get(name)
                        if got is None:
                            got = lookup[name] = len(palette)
                            palette.append(name)
                        remap[i] = got
                    if idx is None:
                        if remap[0] == 0:
                            continue
                        blocks[sx0 - x0:sx1 - x0, cy0 - y0:cy1 - y0,
                               sz0 - z0:sz1 - z0] = remap[0]
                        continue
                    # Section order is YZX; transpose to XYZ.
                    cube = idx.reshape(16, 16, 16).transpose(2, 0, 1)
                    part = cube[sx0 - bx:sx1 - bx, cy0 - ty0:cy1 - ty0,
                                sz0 - bz:sz1 - bz]
                    blocks[sx0 - x0:sx1 - x0, cy0 - y0:cy1 - y0,
                           sz0 - z0:sz1 - z0] = remap[part]

        return Box(blocks, palette, (x0, y0, z0))

    def surface(self, x0: int, z0: int, x1: int, z1: int,
                y0: int = -64, y1: int = 320):
        """Height and surface block over a box, without holding the voxels.

        Returns ``(height, top, palette)``: ``height[x, z]`` is the Y of the
        highest non-air block plus one (so ``y0`` means an empty column), and
        ``top[x, z]`` indexes ``palette`` with that block.
        """
        w, d = x1 - x0, z1 - z0
        height = np.full((w, d), y0, dtype=np.int32)
        top = np.zeros((w, d), dtype=np.uint16)
        palette = [AIR]
        lookup = {AIR: 0}

        for cx in range(x0 >> 4, ((x1 - 1) >> 4) + 1):
            for cz in range(z0 >> 4, ((z1 - 1) >> 4) + 1):
                chunk = self.chunk(cx, cz)
                if chunk is None:
                    continue
                bx, bz = cx * 16, cz * 16
                sx0, sx1 = max(x0, bx), min(x1, bx + 16)
                sz0, sz1 = max(z0, bz), min(z1, bz + 16)
                if sx0 >= sx1 or sz0 >= sz1:
                    continue
                col_h = np.full((sx1 - sx0, sz1 - sz0), y0, dtype=np.int32)
                col_t = np.zeros((sx1 - sx0, sz1 - sz0), dtype=np.uint16)

                for sy, sec_pal, idx in self.chunk_sections(chunk):
                    ty0 = sy * 16
                    if ty0 >= y1 or ty0 + 16 <= y0:
                        continue
                    remap = np.empty(len(sec_pal), dtype=np.uint16)
                    for i, name in enumerate(sec_pal):
                        got = lookup.get(name)
                        if got is None:
                            got = lookup[name] = len(palette)
                            palette.append(name)
                        remap[i] = got
                    if idx is None:
                        cube = np.full((16, 16, 16), remap[0], dtype=np.uint16)
                    else:
                        cube = remap[idx.reshape(16, 16, 16).transpose(2, 0, 1)]
                    part = cube[sx0 - bx:sx1 - bx, :, sz0 - bz:sz1 - bz]
                    filled = part != 0
                    ys = np.where(filled, np.arange(16)[None, :, None], -1)
                    best = ys.max(axis=1)
                    hit = best >= 0
                    cand = ty0 + best + 1
                    take = hit & (cand > col_h)
                    col_h = np.where(take, cand, col_h)
                    picked = np.take_along_axis(
                        part, np.clip(best, 0, 15)[:, None, :], axis=1)[:, 0, :]
                    col_t = np.where(take, picked, col_t)

                height[sx0 - x0:sx1 - x0, sz0 - z0:sz1 - z0] = col_h
                top[sx0 - x0:sx1 - x0, sz0 - z0:sz1 - z0] = col_t

        return height, top, palette
