"""numpy, when it is there, for the operations where it changes nothing.

The library is standard-library-only on purpose: it has to run on a machine
where `pip install` is somebody else's decision, and the only hard dependency is
Pillow because there is no way to read a PNG without one. That is a real cost --
a metre-grid pipeline in pure Python spends its life in cell loops -- and this
module is the part of the cost that can be bought back without paying for it
twice.

The rule that makes it safe: **every function here must return exactly what the
pure-Python version returns, bit for bit.** Not "close enough", not "the same up
to floating point" -- identical. `tools/mask_selftest.py` checks that on random
masks and refuses to pass if it ever stops being true.

That rule is what keeps the thresholds honest. `ROUND = 0.25`, the two-metre
section tolerance, `STOREY_SCORE = 0.15` -- every one of them was chosen by
looking at numbers this code produced. A faster implementation that changed an
answer by a percent would move all of them silently, and the first thing anybody
would notice is a build that used to pass and now does not, for no reason
anybody could name.

So what is accelerated here is only the arithmetic that has one answer: boolean
combination, counting, packing a schematic layer into a mask. The distance
transform, the circle fit, the autocorrelation and the contour walk stay in
`mask.py` and `plan.py` in the form that was calibrated, whether or not numpy is
installed. They are the ones where a rewrite would be a re-decision.
"""

from __future__ import annotations

try:  # pragma: no cover - the whole point is that both paths exist
    import numpy as _np
except ImportError:  # pragma: no cover
    _np = None

HAVE = _np is not None


def array(bits):
    """A view of a mask's bytes as a numpy array. No copy."""
    return _np.frombuffer(bits, dtype=_np.uint8)


def combine(a, b, op: str):
    """`a op b` over two mask buffers, returning a new bytearray.

    `op` is "and", "or" or "sub". The pure version walks the cells; this one
    does not, and both produce a bytearray of 0 and 1 with the same contents.
    """
    x, y = array(a), array(b)
    if op == "and":
        out = x & y
    elif op == "or":
        out = x | y
    elif op == "sub":
        out = x & ~y
    else:
        raise ValueError(op)
    return bytearray((out != 0).astype(_np.uint8).tobytes())


def invert(bits):
    return bytearray((array(bits) == 0).astype(_np.uint8).tobytes())


def count(bits) -> int:
    return int(array(bits).sum())


def layer(blocks, palette_ids: set[int], base: int, area: int):
    """One schematic layer as a mask buffer: cells whose block is in the set.

    The gate walks every cell of every layer twice -- once for the schedule and
    once for the point cloud -- and on a building of any size that is the single
    largest loop in the run.
    """
    slab = _np.frombuffer(blocks, dtype=_np.uint8, count=area, offset=base) \
        if isinstance(blocks, (bytes, bytearray)) \
        else _np.asarray(blocks[base:base + area], dtype=_np.int32)
    keep = _np.zeros(int(slab.max()) + 1 if slab.size else 1, dtype=bool)
    for i in palette_ids:
        if i < keep.size:
            keep[i] = True
    return bytearray(keep[slab].astype(_np.uint8).tobytes())


def _local(width: int, length: int, origin, cos: float, sin: float,
           flip_u: float | None = None, flip_v: float | None = None,
           nudge: float = 0.0):
    """(u, v) at the centre of every cell, as two `length` x `width` grids.

    The same arithmetic `Frame.to_local` does, in the same order and with the
    same steps, so the two agree to the bit: `dx * cos + dz * sin` is one
    multiply-add either way, numpy does not reassociate it, and the mirror and
    the nudge are applied here in the order they are applied there.

    The mirrors used to be missing, and that was not a slow path being slightly
    different -- it was the fast path drawing a different building. Everything
    authored through `Site.flipped` (which is how the second of every mirrored
    pair is drawn) came out reflected about the wrong place on any machine with
    numpy installed, and `mask_selftest` never fitted a flipped frame, so the
    one check that exists to catch exactly this passed every time.
    """
    dx = _np.arange(width, dtype=_np.float64) + 0.5 - origin[0]
    dz = _np.arange(length, dtype=_np.float64) + 0.5 - origin[1]
    u = dx[None, :] * cos + dz[:, None] * sin
    v = -dx[None, :] * sin + dz[:, None] * cos
    if flip_u is not None:
        u = 2.0 * flip_u - u
    if flip_v is not None:
        v = 2.0 * flip_v - v
    if nudge:
        u = u + nudge
        v = v + nudge
    return u, v


def rect(width: int, length: int, origin, cos: float, sin: float,
         u0: float, u1: float, v0: float, v1: float,
         flip_u: float | None = None, flip_v: float | None = None,
         nudge: float = 0.0):
    """A rectangle in the building's frame, rasterised by the centre rule.

    `Frame.region` asks a predicate about 14 million cell centres on a building
    of any size, and a rectangle is what it is asked about nine times in ten:
    every footprint, every wall band, every floor plate. Answering all of them
    at once is the single largest saving numpy buys here.
    """
    u, v = _local(width, length, origin, cos, sin, flip_u, flip_v, nudge)
    hit = (u >= u0) & (u < u1) & (v >= v0) & (v < v1)
    return bytearray(hit.astype(_np.uint8).tobytes())


def disc(width: int, length: int, origin, cos: float, sin: float,
         cu: float, cv: float, outer2: float, inner2: float,
         flip_u: float | None = None, flip_v: float | None = None,
         nudge: float = 0.0):
    """A circle or an annulus, by the same rule and the same squared radii."""
    u, v = _local(width, length, origin, cos, sin, flip_u, flip_v, nudge)
    d = (u - cu) ** 2 + (v - cv) ** 2
    hit = (d >= inner2) & (d < outer2)
    return bytearray(hit.astype(_np.uint8).tobytes())


def occupancy(blocks, keep: set[int], width: int, length: int, height: int):
    """Per-cell bitmaps of which layers hold wanted material.

    Returns a list of ints, one per cell, bit y set when layer y holds a block
    in `keep` -- the same structure `Schedule.standing` builds by hand, which is
    a triple loop over the whole schematic in pure Python.
    """
    area = width * length
    data = _np.asarray(blocks, dtype=_np.int32).reshape(height, area) \
        if not isinstance(blocks, (bytes, bytearray)) \
        else _np.frombuffer(blocks, dtype=_np.uint8).reshape(height, area)
    mask = _np.zeros(int(data.max()) + 1 if data.size else 1, dtype=bool)
    for i in keep:
        if i < mask.size:
            mask[i] = True
    hit = mask[data]                       # height x area, bool
    out = [0] * area
    for y in range(height):
        row = _np.flatnonzero(hit[y])
        bit = 1 << y
        for i in row.tolist():
            out[i] |= bit
    return out


__all__ = ["HAVE", "array", "combine", "count", "disc", "invert", "layer",
           "occupancy", "rect"]
