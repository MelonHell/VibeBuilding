"""Sponge Schematic v2/v3 reader and writer on top of tools/nbt.py.

A schematic is a dense box of block states. The palette maps a block string to a
small integer; Data is those integers as LEB128 varints in Y, then Z, then X
order, so index = (y * length + z) * width + x.

Two coordinate systems meet here and mixing them up is the usual bug:

    local     0..width/height/length, the array's own indices
    world     local + Origin + Offset

Offset is where the box's minimum corner sat relative to the copy origin, and
WorldEdit records that absolute origin in Metadata. Neither alone places the
schematic; `min_corner` adds them and is what local (0, 0, 0) maps to.

    s = Schematic.read("massing.schem")
    s.get(10, 0, 20)                  # 'minecraft:gray_concrete'
    s.set(10, 0, 20, "minecraft:stone")
    s.write("massing.schem")
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path

from . import nbt
from .blocks import AIR, COLORS as PREVIEW_COLORS  # noqa: F401  (re-exported)
from .blocks import colour as block_colour


def decode_varints(data, count: int) -> list[int]:
    out = []
    value = 0
    shift = 0
    for raw in data:
        byte = raw & 0xFF
        value |= (byte & 0x7F) << shift
        if byte & 0x80:
            shift += 7
            continue
        out.append(value)
        value = 0
        shift = 0
    if len(out) != count:
        raise ValueError(f"expected {count} block entries, decoded {len(out)}")
    return out


def encode_varints(values) -> bytearray:
    """LEB128 for a whole block array.

    A palette of 128 entries or fewer makes every id a one-byte varint, and then
    the encoding is the identity -- `bytes()` in C rather than a Python loop over
    every cell in the box. On a building that is a rounding error; on a schematic
    the size of an island it is the difference between a second and a minute.
    """
    if values and 0 <= min(values) and max(values) < 0x80:
        return bytearray(values)
    out = bytearray()
    for value in values:
        while True:
            byte = value & 0x7F
            value >>= 7
            if value:
                out.append(byte | 0x80)
            else:
                out.append(byte)
                break
    return out


class Schematic:
    __slots__ = (
        "width",
        "height",
        "length",
        "offset",
        "origin",
        "palette",
        "blocks",
        "block_entities",
        "data_version",
        "metadata",
    )

    def __init__(
        self,
        width: int,
        height: int,
        length: int,
        palette: list[str] | None = None,
        blocks: list[int] | None = None,
        offset: tuple[int, int, int] = (0, 0, 0),
        origin: tuple[int, int, int] = (0, 0, 0),
        data_version: int = 4671,
    ):
        self.width = width
        self.height = height
        self.length = length
        self.offset = tuple(offset)
        self.origin = tuple(origin)
        self.palette = palette if palette is not None else [AIR]
        self.blocks = (
            blocks if blocks is not None else [0] * (width * height * length)
        )
        self.block_entities = nbt.List([], nbt.TAG_COMPOUND)
        self.data_version = data_version
        self.metadata = {}

    # -- geometry ---------------------------------------------------------

    @property
    def volume(self) -> int:
        return self.width * self.height * self.length

    @property
    def min_corner(self) -> tuple[int, int, int]:
        """World position of local (0, 0, 0)."""
        return tuple(o + d for o, d in zip(self.origin, self.offset))

    def to_world(self, x: int, y: int, z: int) -> tuple[int, int, int]:
        ox, oy, oz = self.min_corner
        return (ox + x, oy + y, oz + z)

    def index(self, x: int, y: int, z: int) -> int:
        if not (0 <= x < self.width and 0 <= y < self.height and 0 <= z < self.length):
            raise IndexError(f"({x}, {y}, {z}) outside {self.size_str()}")
        return (y * self.length + z) * self.width + x

    def size_str(self) -> str:
        return f"{self.width}x{self.height}x{self.length}"

    def get(self, x: int, y: int, z: int) -> str:
        return self.palette[self.blocks[self.index(x, y, z)]]

    def set(self, x: int, y: int, z: int, block: str) -> None:
        self.blocks[self.index(x, y, z)] = self.palette_id(block)

    def palette_id(self, block: str) -> int:
        try:
            return self.palette.index(block)
        except ValueError:
            self.palette.append(block)
            return len(self.palette) - 1

    def counts(self) -> Counter:
        tally = Counter(self.blocks)
        return Counter({self.palette[i]: n for i, n in tally.items()})

    # -- io ---------------------------------------------------------------

    @classmethod
    def read(cls, path: str | Path) -> "Schematic":
        _, root = nbt.read(path)
        # v3 nests everything under "Schematic"; v2 puts it at the root.
        doc = root.get("Schematic", root)
        version = int(doc.get("Version", 2))
        if version == 3:
            block_container = doc["Blocks"]
            palette_map = block_container["Palette"]
            data = block_container["Data"]
            block_entities = block_container.get(
                "BlockEntities", nbt.List([], nbt.TAG_COMPOUND)
            )
        else:
            palette_map = doc["Palette"]
            data = doc["BlockData"]
            block_entities = doc.get("BlockEntities", nbt.List([], nbt.TAG_COMPOUND))

        palette = [""] * (max(int(v) for v in palette_map.values()) + 1)
        for block, i in palette_map.items():
            palette[int(i)] = block

        width = int(doc["Width"])
        height = int(doc["Height"])
        length = int(doc["Length"])
        offset = tuple(int(v) for v in doc.get("Offset", (0, 0, 0)))
        world_edit = doc.get("Metadata", {}).get("WorldEdit", {})
        origin = tuple(int(v) for v in world_edit.get("Origin", (0, 0, 0)))

        schematic = cls(
            width,
            height,
            length,
            palette=palette,
            blocks=decode_varints(data, width * height * length),
            offset=offset,
            origin=origin,
            data_version=int(doc.get("DataVersion", 4671)),
        )
        schematic.block_entities = block_entities
        schematic.metadata = doc.get("Metadata", {})
        return schematic

    def write(self, path: str | Path) -> None:
        doc = {
            "Version": nbt.Int(3),
            "DataVersion": nbt.Int(self.data_version),
            "Metadata": self.metadata or {},
            "Width": nbt.Short(self.width),
            "Height": nbt.Short(self.height),
            "Length": nbt.Short(self.length),
            "Offset": nbt.Array(self.offset, nbt.TAG_INT_ARRAY),
            "Blocks": {
                "Palette": {
                    block: nbt.Int(i) for i, block in enumerate(self.palette)
                },
                "Data": encode_varints(self.blocks),
                "BlockEntities": self.block_entities,
            },
        }
        nbt.write(path, {"Schematic": doc})

    # -- preview ----------------------------------------------------------

    def preview_color(self, block: str) -> tuple[int, int, int]:
        """One table, shared with the renderer. Unknown blocks come out magenta.

        This used to derive a pastel from `hash(base)`, which is salted per
        process in Python, so the same schematic previewed twice gave two
        different pictures and no two runs could be compared.
        """
        return block_colour(block)

    def to_png(self, path: str | Path, scale: int = 1, layer: int | None = None):
        """Top-down view. Each column shows its highest non-air block."""
        from PIL import Image

        image = Image.new("RGB", (self.width, self.length))
        pixels = image.load()
        y_range = [layer] if layer is not None else range(self.height - 1, -1, -1)
        for z in range(self.length):
            for x in range(self.width):
                color = PREVIEW_COLORS[AIR]
                for y in y_range:
                    block = self.get(x, y, z)
                    if block != AIR:
                        color = self.preview_color(block)
                        break
                pixels[x, z] = color
        if scale != 1:
            image = image.resize(
                (self.width * scale, self.length * scale), Image.NEAREST
            )
        image.save(path)
        return image


def describe(path: str | Path) -> str:
    s = Schematic.read(path)
    lines = [
        f"{Path(path).name}",
        f"  size     {s.size_str()}  ({s.volume} blocks)",
        f"  origin   {s.origin}  offset {s.offset}",
        f"  world    local (0,0,0) is {s.min_corner}",
        f"  version  DataVersion {s.data_version}",
        f"  palette  {len(s.palette)} entries",
    ]
    for block, n in s.counts().most_common():
        lines.append(f"    {n:>8}  {100.0 * n / s.volume:5.1f}%  {block}")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    make_png = "--png" in sys.argv
    for arg in args:
        print(describe(arg))
        if make_png:
            out = Path(arg).with_suffix(".png")
            Schematic.read(arg).to_png(out, scale=3)
            print(f"  wrote {out}")
