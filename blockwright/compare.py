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

    def at(self, metres_per_pixel: float) -> "Panel":
        """The same ground, sampled at a different metres-per-pixel.

        Extent stays put; only the sampling changes. `aligned` restates every
        panel onto one grid before it pads them to one rectangle -- restating
        is not scaling the ground, and the two have to stay distinct or the
        pad becomes a stretch.
        """
        if self.metres_per_pixel is None:
            raise ValueError(
                f"{self.label} is unmeasured; it has no metres to restate")
        if metres_per_pixel <= 0:
            raise ValueError("metres_per_pixel must be positive")
        factor = self.metres_per_pixel / metres_per_pixel
        if abs(factor - 1.0) < 1e-12:
            return self
        from PIL import Image

        size = (max(1, round(self.image.width * factor)),
                max(1, round(self.image.height * factor)))
        # Nearest-neighbour so a 1 px = 1 m map restated to a finer grid stays
        # a grid, not a blur: the sheet is for comparing shape, and a Lanczos
        # upsample would invent edges that were never drawn.
        return Panel(self.image.convert("RGB").resize(size, Image.NEAREST),
                     metres_per_pixel, self.label)


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


def map_panel(path, label: str = "map") -> Panel:
    """A layout.png crop: one pixel is one metre by contract.

    Not trimmed. The crop *is* the plot, and the empty ground around the
    building is the extent the other panels have to pad out to. Trimming it
    the way `mesh_panel` trims an ortho would throw that ground away and
    then pad it back, which is how a missing site reads as a framing choice.
    """
    return Panel(_open(path), 1.0, label)


def aligned(panels: list[Panel], metres_per_pixel: float | None = None
            ) -> list[Panel]:
    """The same panels, all covering the same rectangle of ground.

    `sheet` already puts panels at one scale, and one scale is not one extent: a
    reference clipped to the building beside a build that covers the whole plot
    comes out as a small picture next to a large one, both at a true 1:1. The eye
    then spends its time finding the building instead of comparing the shape,
    which is what the sheet is for -- and on one real site that is exactly how a
    missing half of the plot went unnoticed.

    So each panel is padded, never scaled, to the union of all their extents.
    Padding says "nothing was known here"; scaling would say "this is what was
    there", which is false.
    """
    from PIL import Image

    measured = [p for p in panels if p.metres_per_pixel is not None]
    if not measured:
        return list(panels)
    scale = metres_per_pixel or min(p.metres_per_pixel for p in measured)
    restated = [p if p.metres_per_pixel is None else p.at(scale)
                for p in panels]
    # Union of the restated pixel boxes, not of the original metre extents
    # converted back: a round-trip through metres is off by a pixel, and a
    # paste that does not fit is a clip, which is a lie about the edge.
    box = (
        max(p.image.width for p in restated if p.metres_per_pixel is not None),
        max(p.image.height for p in restated if p.metres_per_pixel is not None),
    )
    out = []
    for panel in restated:
        if panel.metres_per_pixel is None:
            out.append(panel)
            continue
        padded = Image.new("RGB", box, BACKGROUND)
        image = panel.image.convert("RGB")
        padded.paste(image, ((box[0] - image.width) // 2,
                             (box[1] - image.height) // 2))
        out.append(Panel(padded, scale, panel.label))
    return out


# Both, drawn only, built only. Chosen so the two faults read at a glance and in
# opposite directions: warm where the build did not reach the plan, cool where
# it went past it, and quiet grey where the two agree -- which is most of the
# picture on a build that is right.
AGREE = (150, 154, 160)
MISSING = (214, 108, 92)
BEYOND = (86, 140, 196)


def overlay(drawn, built, path, scale: int = 2,
            off: tuple[int, int, int] = (28, 30, 34)):
    """Two masks on one picture: agreed, drawn-only, built-only.

    For the conformance question -- did the build stand where its own plan says
    -- where two panels side by side are the wrong picture. A number says four
    per cent of the plan is unbuilt and says nothing about *which* four per
    cent, and the difference between "a cell all round the edge" and "the whole
    of the north arm" is the difference between rounding and a defect. Laid over
    each other, the shape of the disagreement is the answer.

    Both masks have to be on the same grid, which they are: the plan's parts and
    the build's layers are rasterised through the same frame onto the same
    canvas.
    """
    from PIL import Image

    if (drawn.width, drawn.length) != (built.width, built.length):
        raise ValueError("the plan and the build are on different grids")
    image = Image.new("RGB", (drawn.width, drawn.length))
    px = image.load()
    for z in range(drawn.length):
        base = z * drawn.width
        for x in range(drawn.width):
            a, b = drawn.bits[base + x], built.bits[base + x]
            px[x, z] = (AGREE if a and b else
                        MISSING if a else
                        BEYOND if b else off)
    if scale != 1:
        image = image.resize((drawn.width * scale, drawn.length * scale),
                             Image.NEAREST)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path)
    return path


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


# Below this, the two panels are not pictures of the same thing at the same
# size, and their overlap is not a score of anything. Three buildings printed a
# number here that never moved -- 0.001, 0.10, 0.47 -- because one panel held a
# whole site and the other held one clipped building; on one of them half a
# facade was a blank wall for several rounds and the number did not notice.
COMPARABLE = 0.35


def iou(a: Panel, b: Panel, scale: float = 1.0, align: str = "bottom",
        comparable: float = COMPARABLE) -> float | None:
    """Overlap of two silhouettes, or None when they are not comparable.

    Aligning by bounding box means this measures shape and proportion, not
    placement -- placement is the gate's job, and it does it in metres.

    None rather than a small number when the two silhouettes differ in area by
    more than `comparable`. A build rendered with its whole site against an
    ortho clipped to the building alone overlaps by a hundredth, and that
    hundredth is stable: it reads as "bad, but consistently bad" when it means
    "these two are not the same picture". A number nobody can act on is worse
    than an honest absence.
    """
    sa, sb = silhouette(a, scale), silhouette(b, scale)
    if not sa or not sb:
        return 0.0
    ratio = min(len(sa), len(sb)) / max(len(sa), len(sb))
    if ratio < comparable:
        return None

    def anchor(cells):
        xs = [c[0] for c in cells]
        ys = [c[1] for c in cells]
        return min(xs), (max(ys) if align == "bottom" else min(ys))

    ax, ay = anchor(sa)
    bx, by = anchor(sb)
    sb = {(x - bx + ax, y - by + ay) for x, y in sb}
    return len(sa & sb) / len(sa | sb)
