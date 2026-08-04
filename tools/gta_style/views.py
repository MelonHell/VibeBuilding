"""Plan and elevation renders of a box of world, for looking at.

An elevation is a ray cast per pixel from outside the box: the first block that
is not air or glass-air is what the eye would meet, so glass reads as glass and
the frame behind it does not bleed through. Colours come from ``shared.colour``
and are shaded by depth so a recessed balcony reads as recessed.
"""

from __future__ import annotations

import numpy as np
from PIL import Image

from tools.gta_style import shared

TRANSPARENT = ("air", "water", "barrier", "light", "structure_void")


def _empty_mask(pal: list[str]) -> np.ndarray:
    out = np.zeros(len(pal), bool)
    for i, b in enumerate(pal):
        name = b.split("[")[0][len("minecraft:"):]
        if name in TRANSPARENT or name.endswith("_air"):
            out[i] = True
    return out


def elevation(box, axis: str, scale: int = 1) -> Image.Image:
    """First-hit view along ``axis`` in ``+x -x +z -z``."""
    blocks = box.blocks
    empty = _empty_mask(box.palette)
    solid = ~empty[blocks]

    if axis in ("+x", "-x"):
        order = np.arange(blocks.shape[0])
        if axis == "-x":
            order = order[::-1]
        stack = solid[order]                    # depth, y, z
        cube = blocks[order]
        width_axis = 2
    else:
        order = np.arange(blocks.shape[2])
        if axis == "-z":
            order = order[::-1]
        stack = solid.transpose(2, 1, 0)[order]  # depth, y, x
        cube = blocks.transpose(2, 1, 0)[order]
        width_axis = 2

    hit = stack.argmax(axis=0)
    any_hit = stack.any(axis=0)
    picked = np.take_along_axis(cube, hit[None], axis=0)[0]

    lut = shared.palette_lut(box.palette)
    img = lut[picked].astype(np.float32)
    depth = hit.astype(np.float32)
    fade = np.clip(1.06 - depth / max(stack.shape[0], 1) * 0.9, 0.18, 1.06)
    img *= fade[..., None]
    img[~any_hit] = 24

    # argmax already left us (y, width); only Y needs flipping to point up.
    img = np.flipud(img).astype(np.uint8)
    if axis in ("+x", "-z"):
        img = np.fliplr(img)
    out = Image.fromarray(img)
    if scale != 1:
        out = out.resize((out.width * scale, out.height * scale),
                         Image.NEAREST)
    return out


def plan(box, scale: int = 1, y: int | None = None) -> Image.Image:
    """Top-down view: highest non-air block, or the slice at world ``y``."""
    blocks = box.blocks
    empty = _empty_mask(box.palette)
    solid = ~empty[blocks]
    lut = shared.palette_lut(box.palette)

    if y is None:
        ys = np.where(solid, np.arange(blocks.shape[1])[None, :, None], -1)
        best = ys.max(axis=1)
        picked = np.take_along_axis(
            blocks, np.clip(best, 0, None)[:, None, :], axis=1)[:, 0, :]
        img = lut[picked].astype(np.float32)
        hh = best.astype(np.float32)
        gy, gx = np.gradient(hh)
        img *= np.clip(0.85 + 0.09 * (gx + gy), 0.4, 1.6)[..., None]
        img[best < 0] = 24
    else:
        k = y - box.origin[1]
        picked = blocks[:, k, :]
        img = lut[picked].astype(np.float32)
        img[~solid[:, k, :]] = 24

    img = np.transpose(np.clip(img, 0, 255).astype(np.uint8), (1, 0, 2))
    out = Image.fromarray(img)
    if scale != 1:
        out = out.resize((out.width * scale, out.height * scale),
                         Image.NEAREST)
    return out


def sheet(box, path: str, scale: int = 1):
    """Plan plus the four elevations, side by side on one image."""
    views = [plan(box, scale)] + [elevation(box, a, scale)
                                  for a in ("-z", "+x", "+z", "-x")]
    pad = 8
    w = sum(v.width for v in views) + pad * (len(views) + 1)
    h = max(v.height for v in views) + pad * 2
    canvas = Image.new("RGB", (w, h), (12, 12, 14))
    x = pad
    for v in views:
        canvas.paste(v, (x, pad + (h - 2 * pad - v.height)))
        x += v.width + pad
    canvas.save(path)
    return canvas
