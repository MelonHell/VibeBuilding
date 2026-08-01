"""Minecraft NBT reader and writer, stdlib only.

NBT is a tagged binary tree: one byte of tag type, a length-prefixed UTF-8 name,
then a payload whose shape the tag type decides. Compounds nest until a TAG_End
byte; lists carry one element type and a count.

Tags are decoded into plain Python values. Type information that Python cannot
carry on its own -- a byte versus an int, a list's element type, an empty list's
element type -- is kept in wrapper classes so a file can be read, edited, and
written back without silently changing types.

    root_name, root = read("thing.schem")     # gzip is detected
    write("thing.schem", root, root_name)
"""

from __future__ import annotations

import gzip
import struct
from pathlib import Path

TAG_END = 0
TAG_BYTE = 1
TAG_SHORT = 2
TAG_INT = 3
TAG_LONG = 4
TAG_FLOAT = 5
TAG_DOUBLE = 6
TAG_BYTE_ARRAY = 7
TAG_STRING = 8
TAG_LIST = 9
TAG_COMPOUND = 10
TAG_INT_ARRAY = 11
TAG_LONG_ARRAY = 12

TAG_NAMES = {
    TAG_END: "end",
    TAG_BYTE: "byte",
    TAG_SHORT: "short",
    TAG_INT: "int",
    TAG_LONG: "long",
    TAG_FLOAT: "float",
    TAG_DOUBLE: "double",
    TAG_BYTE_ARRAY: "byte_array",
    TAG_STRING: "string",
    TAG_LIST: "list",
    TAG_COMPOUND: "compound",
    TAG_INT_ARRAY: "int_array",
    TAG_LONG_ARRAY: "long_array",
}

_STRUCTS = {
    TAG_BYTE: struct.Struct(">b"),
    TAG_SHORT: struct.Struct(">h"),
    TAG_INT: struct.Struct(">i"),
    TAG_LONG: struct.Struct(">q"),
    TAG_FLOAT: struct.Struct(">f"),
    TAG_DOUBLE: struct.Struct(">d"),
}
_LEN = struct.Struct(">i")
_USHORT = struct.Struct(">H")


class Typed(int):
    """An integer that remembers which NBT width it came from."""

    tag = TAG_INT

    def __repr__(self) -> str:
        return f"{TAG_NAMES[self.tag]}({int(self)})"


class Byte(Typed):
    tag = TAG_BYTE


class Short(Typed):
    tag = TAG_SHORT


class Int(Typed):
    tag = TAG_INT


class Long(Typed):
    tag = TAG_LONG


class TypedFloat(float):
    tag = TAG_DOUBLE

    def __repr__(self) -> str:
        return f"{TAG_NAMES[self.tag]}({float(self)})"


class Float(TypedFloat):
    tag = TAG_FLOAT


class Double(TypedFloat):
    tag = TAG_DOUBLE


class Array(list):
    """A byte/int/long array. Distinct from TAG_List, which nests tags."""

    def __init__(self, values, tag: int):
        super().__init__(values)
        self.tag = tag

    def __repr__(self) -> str:
        return f"{TAG_NAMES[self.tag]}[{len(self)}]"


class List(list):
    """A TAG_List. Carries its element type so empty lists round-trip."""

    def __init__(self, values=(), element_tag: int = TAG_END):
        super().__init__(values)
        self.element_tag = element_tag

    def __repr__(self) -> str:
        return f"list<{TAG_NAMES[self.element_tag]}>[{len(self)}]"


class Reader:
    __slots__ = ("data", "pos")

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if end > len(self.data):
            raise ValueError("truncated NBT")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def string(self) -> str:
        (length,) = _USHORT.unpack_from(self.data, self.pos)
        self.pos += 2
        return self.take(length).decode("utf-8", errors="replace")

    def payload(self, tag: int):
        if tag in _STRUCTS:
            value = _STRUCTS[tag].unpack_from(self.data, self.pos)[0]
            self.pos += _STRUCTS[tag].size
            return _WRAPPERS[tag](value)
        if tag == TAG_STRING:
            return self.string()
        if tag == TAG_BYTE_ARRAY:
            (n,) = _LEN.unpack_from(self.data, self.pos)
            self.pos += 4
            return Array(struct.unpack(f">{n}b", self.take(n)), TAG_BYTE_ARRAY)
        if tag == TAG_INT_ARRAY:
            (n,) = _LEN.unpack_from(self.data, self.pos)
            self.pos += 4
            return Array(struct.unpack(f">{n}i", self.take(n * 4)), TAG_INT_ARRAY)
        if tag == TAG_LONG_ARRAY:
            (n,) = _LEN.unpack_from(self.data, self.pos)
            self.pos += 4
            return Array(struct.unpack(f">{n}q", self.take(n * 8)), TAG_LONG_ARRAY)
        if tag == TAG_LIST:
            element_tag = self.take(1)[0]
            (n,) = _LEN.unpack_from(self.data, self.pos)
            self.pos += 4
            return List(
                [self.payload(element_tag) for _ in range(n)], element_tag
            )
        if tag == TAG_COMPOUND:
            out = {}
            while True:
                child = self.take(1)[0]
                if child == TAG_END:
                    return out
                # Name before payload: both advance the cursor, and Python
                # evaluates a subscript assignment's right side first.
                name = self.string()
                out[name] = self.payload(child)
        raise ValueError(f"unknown NBT tag {tag}")


_WRAPPERS = {
    TAG_BYTE: Byte,
    TAG_SHORT: Short,
    TAG_INT: Int,
    TAG_LONG: Long,
    TAG_FLOAT: Float,
    TAG_DOUBLE: Double,
}


def tag_of(value) -> int:
    """Infer the NBT tag for a Python value."""
    if isinstance(value, (Typed, TypedFloat)):
        return value.tag
    if isinstance(value, bool):
        return TAG_BYTE
    if isinstance(value, int):
        return TAG_INT
    if isinstance(value, float):
        return TAG_DOUBLE
    if isinstance(value, str):
        return TAG_STRING
    if isinstance(value, (bytes, bytearray)):
        return TAG_BYTE_ARRAY
    if isinstance(value, Array):
        return value.tag
    if isinstance(value, (List, list, tuple)):
        return TAG_LIST
    if isinstance(value, dict):
        return TAG_COMPOUND
    raise TypeError(f"cannot store {type(value).__name__} in NBT")


def write_payload(out: bytearray, tag: int, value) -> None:
    if tag in _STRUCTS:
        out += _STRUCTS[tag].pack(value)
    elif tag == TAG_STRING:
        encoded = value.encode("utf-8")
        out += _USHORT.pack(len(encoded)) + encoded
    elif tag == TAG_BYTE_ARRAY:
        # Raw bytes go straight out. A signed byte and an unsigned one have the
        # same eight bits, so this is the same file -- and it is the difference
        # between writing a city-sized schematic and not: the general path below
        # explodes a twenty-million entry array into that many arguments.
        if isinstance(value, (bytes, bytearray)):
            out += _LEN.pack(len(value)) + bytes(value)
        else:
            out += _LEN.pack(len(value)) + struct.pack(f">{len(value)}b", *value)
    elif tag == TAG_INT_ARRAY:
        out += _LEN.pack(len(value)) + struct.pack(f">{len(value)}i", *value)
    elif tag == TAG_LONG_ARRAY:
        out += _LEN.pack(len(value)) + struct.pack(f">{len(value)}q", *value)
    elif tag == TAG_LIST:
        element_tag = getattr(value, "element_tag", TAG_END)
        if not element_tag and value:
            element_tag = tag_of(value[0])
        out += bytes([element_tag]) + _LEN.pack(len(value))
        for item in value:
            write_payload(out, element_tag, item)
    elif tag == TAG_COMPOUND:
        for key, item in value.items():
            child = tag_of(item)
            encoded = key.encode("utf-8")
            out += bytes([child]) + _USHORT.pack(len(encoded)) + encoded
            write_payload(out, child, item)
        out += bytes([TAG_END])
    else:
        raise ValueError(f"cannot write NBT tag {tag}")


def loads(data: bytes) -> tuple[str, dict]:
    if data[:2] == b"\x1f\x8b":
        data = gzip.decompress(data)
    reader = Reader(data)
    tag = reader.take(1)[0]
    if tag != TAG_COMPOUND:
        raise ValueError("NBT root is not a compound")
    return reader.string(), reader.payload(TAG_COMPOUND)


def dumps(root: dict, name: str = "") -> bytes:
    out = bytearray([TAG_COMPOUND])
    encoded = name.encode("utf-8")
    out += _USHORT.pack(len(encoded)) + encoded
    write_payload(out, TAG_COMPOUND, root)
    return bytes(out)


def read(path: str | Path) -> tuple[str, dict]:
    return loads(Path(path).read_bytes())


def write(path: str | Path, root: dict, name: str = "", compress: bool = True) -> None:
    raw = dumps(root, name)
    Path(path).write_bytes(gzip.compress(raw, mtime=0) if compress else raw)


def summary(value, indent: int = 0, limit: int = 12) -> str:
    """Render a tree for inspection, truncating long arrays."""
    pad = "  " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, List)) and item:
                lines.append(f"{pad}{key}:")
                lines.append(summary(item, indent + 1, limit))
            else:
                lines.append(f"{pad}{key} = {_brief(item, limit)}")
        return "\n".join(lines)
    if isinstance(value, List):
        return "\n".join(
            summary(item, indent, limit) if isinstance(item, dict)
            else f"{pad}- {_brief(item, limit)}"
            for item in value[:limit]
        ) + (f"\n{pad}... {len(value) - limit} more" if len(value) > limit else "")
    return f"{pad}{_brief(value, limit)}"


def _brief(value, limit: int) -> str:
    if isinstance(value, Array):
        head = ", ".join(str(v) for v in value[:limit])
        more = f", ... {len(value) - limit} more" if len(value) > limit else ""
        return f"{TAG_NAMES[value.tag]}[{len(value)}] {{{head}{more}}}"
    if isinstance(value, dict):
        return f"compound({len(value)} keys)"
    if isinstance(value, List):
        return repr(value)
    return repr(value)


if __name__ == "__main__":
    import sys

    for arg in sys.argv[1:]:
        name, root = read(arg)
        print(f"== {arg}  root name {name!r}")
        print(summary(root))
