"""Putting a build beside the things it was drawn from.

A render on its own is only half an eye. The question is never "does this look
like a building" but "does this look like *that* building", and that needs the
two pictures at one scale, side by side, with a ruler under them.

Everything here works in metres per pixel. A Blender ortho of the mesh carries
its own in `render_meta.json`; a `render.render()` of the build has `1 / scale`;
a photograph has none, and is placed at whatever size looks right, unmeasured
and labelled as such. Mixing them is the whole job.

    sheet([mesh_panel(orthos, "east"), build_panel(image, 6.0), photo(path)],
          "compare.png")

Silhouette IoU is offered for the pairs whose cameras really do match -- the
mesh's east ortho against the build's long elevation, say. It is a number to
watch move between runs, not a grade: the two silhouettes come from different
things and will never reach 1.
"""

from __future__ import annotations

import json
from pathlib import Path

BACKGROUND = (26, 28, 33)
LABEL = (196, 200, 206)
RULE = (92, 98, 108)


class Panel:
    """One picture, with the scale it was drawn at and a caption.

    `metres_per_pixel` of None means the picture is unmeasured -- a photograph --
    and it will be fitted by height rather than scaled to match.
    """

    __slots__ = ("image", "metres_per_pixel", "label")

    def __init__(self, image, metres_per_pixel: float | None, label: str):
        self.image = image
        self.metres_per_pixel = metres_per_pixel
        self.label = label


def _open(source):
    from PIL import Image

    if hasattr(source, "convert"):
        return source
    return Image.open(source)


def trim(image, tolerance: int = 12, border: int = 2):
    """Crop away a flat background, so two pictures can be aligned by content.

    The background colour is taken from the top-left pixel, which is outside the
    subject in every render either tool produces.
    """
    from PIL import Image, ImageChops

    rgb = image.convert("RGB")
    flat = Image.new("RGB", rgb.size, rgb.getpixel((0, 0)))
    diff = ImageChops.difference(rgb, flat).convert("L").point(
        lambda v: 255 if v > tolerance else 0
    )
    box = diff.getbbox()
    if box is None:
        return image
    x0, y0, x1, y1 = box
    return image.crop((
        max(0, x0 - border), max(0, y0 - border),
        min(image.width, x1 + border), min(image.height, y1 + border),
    ))


def mesh_panel(orthos: str | Path, view: str, textured: bool = True,
               label: str | None = None) -> Panel:
    """A Blender ortho of the Google Earth mesh, at the scale it was rendered."""
    orthos = Path(orthos)
    meta = json.loads((orthos / "render_meta.json").read_text())["views"][view]
    name = meta["files"]["tex" if textured else "solid"]
    return Panel(trim(_open(orthos / name)), meta["metres_per_pixel"],
                 label or f"mesh {view}")


def build_panel(image, scale: float, label: str = "build") -> Panel:
    """A `render.render()` result, whose scale is pixels per metre by definition."""
    return Panel(trim(_open(image)), 1.0 / scale, label)


def photo(path, label: str = "photo") -> Panel:
    """A photograph: no scale, so it is fitted rather than measured against."""
    return Panel(_open(path), None, label)


def _fit(panel: Panel, scale: float, height: int):
    """Resize a panel to the sheet's metres per pixel, or to its height."""
    from PIL import Image

    image = panel.image.convert("RGB")
    if panel.metres_per_pixel is None:
        factor = height / image.height
    else:
        factor = panel.metres_per_pixel * scale
    size = (max(1, round(image.width * factor)), max(1, round(image.height * factor)))
    return image.resize(size, Image.LANCZOS)


def sheet(panels: list[Panel], path: str | Path | None = None, scale: float = 4.0,
          gap: int = 20, align: str = "bottom",
          background: tuple[int, int, int] = BACKGROUND):
    """Lay panels out in a row at one scale, captioned, with a 10 m rule.

    `align` is "bottom" for elevations, where ground level is the thing to line
    up, and "centre" for plans and obliques.
    """
    from PIL import Image, ImageDraw

    measured = [p for p in panels if p.metres_per_pixel is not None]
    height = max((round(p.image.height * p.metres_per_pixel * scale)
                  for p in measured), default=400)
    images = [_fit(p, scale, height) for p in panels]

    pad, caption, rule = gap, 18, 26
    width = sum(i.width for i in images) + gap * (len(images) + 1)
    tall = max(i.height for i in images)
    canvas = Image.new("RGB", (width, tall + 2 * pad + caption + rule), background)
    draw = ImageDraw.Draw(canvas)

    x = gap
    for panel, image in zip(panels, images):
        y = pad + (tall - image.height if align == "bottom" else
                   (tall - image.height) // 2)
        canvas.paste(image, (x, y))
        note = panel.label if panel.metres_per_pixel is not None \
            else f"{panel.label} (not to scale)"
        draw.text((x, pad + tall + 4), note, fill=LABEL)
        x += image.width + gap

    # Ten metres, so anything in the sheet can be measured off it directly.
    span = 10.0 * scale
    y = canvas.height - rule // 2
    draw.line([(gap, y), (gap + span, y)], fill=RULE, width=2)
    draw.line([(gap, y - 4), (gap, y + 4)], fill=RULE, width=2)
    draw.line([(gap + span, y - 4), (gap + span, y + 4)], fill=RULE, width=2)
    draw.text((gap + span + 8, y - 7), "10 m", fill=RULE)

    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        canvas.save(path)
    return canvas


def silhouette(panel: Panel, scale: float, tolerance: int = 12) -> set:
    """The panel's occupied cells, on a common metre grid.

    Sampled at `scale` cells per metre from whatever resolution the picture
    happens to be, so two pictures rendered at different sizes still compare.
    """
    from PIL import Image, ImageChops

    image = panel.image.convert("RGB")
    flat = Image.new("RGB", image.size, image.getpixel((0, 0)))
    mask = ImageChops.difference(image, flat).convert("L").point(
        lambda v: 255 if v > tolerance else 0
    )
    factor = (panel.metres_per_pixel or 1.0) * scale
    size = (max(1, round(mask.width * factor)), max(1, round(mask.height * factor)))
    mask = mask.resize(size, Image.BILINEAR)
    pixels = mask.load()
    return {(x, y) for y in range(size[1]) for x in range(size[0]) if pixels[x, y] > 127}


def iou(a: Panel, b: Panel, scale: float = 1.0, align: str = "bottom") -> float:
    """Overlap of two silhouettes, each pushed into the same corner first.

    Aligning by bounding box means this measures shape and proportion, not
    placement -- placement is the gate's job, and it does it in metres.
    """
    sa, sb = silhouette(a, scale), silhouette(b, scale)
    if not sa or not sb:
        return 0.0

    def anchor(cells):
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        return min(xs), (max(ys) if align == "bottom" else min(ys))

    ax, ay = anchor(sa)
    bx, by = anchor(sb)
    sb = {(x - bx + ax, y - by + ay) for x, y in sb}
    return len(sa & sb) / len(sa | sb)
