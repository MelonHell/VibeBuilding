"""A plan for a building nothing measured.

Two of the six kinds of input state a plan without ever fixing a dimension: a
verbal description, and a drawing with no scale on it. "Two wings round a court,
the long one about sixty metres" is a plan -- it says what the parts are and
roughly where they go -- and there is no image to rasterise and no mesh to
project, so the numbers have to be written down by somebody and marked as
written down.

That is what this module is: the same `(mass, frame, parts)` the map and the
model produce, assembled from a table of rectangles and circles that a person
typed after reading the brief. Everything downstream is then identical, which is
the point -- one build script, one gate, one review, whatever the evidence was.

What it does **not** do is pretend. Every shape carries the source it came from,
`sources.py` records the whole project as `declared`, and the gate reports
`ungraded` rather than `pass` where nothing independent can contradict the
numbers. A declared plan is a legitimate way to build; a declared plan reported
as a survey is not.

The grid is made here, so a declared building knows its own size and shape and
nothing about where it stands in the world. Placing it is a separate decision --
see `paths.LAYOUT_SCHEM`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .frame import Frame
from .mask import Mask
from .plan import Part

MARGIN = 4      # blocks of clear grid around the whole plan


@dataclass(frozen=True)
class Rect:
    """A rectangular part, in the building's own (u, v), metres.

    `source` names what the numbers were read off -- a sentence of the brief, a
    sheet of an unscaled drawing, a photograph somebody paced out. A declared
    number with no source cannot be checked, argued with, or re-read later, and
    is indistinguishable from one somebody remembered wrong.
    """

    name: str
    u0: float
    u1: float
    v0: float
    v1: float
    source: str

    def bounds(self) -> tuple[float, float, float, float]:
        return (self.u0, self.u1, self.v0, self.v1)

    def draw(self, frame: Frame, width: int, length: int) -> Mask:
        return frame.rect(width, length, self.u0, self.u1, self.v0, self.v1)


@dataclass(frozen=True)
class Round:
    """A round part -- a drum, a rotunda, a tower -- in (u, v), metres."""

    name: str
    cu: float
    cv: float
    radius: float
    source: str

    def bounds(self) -> tuple[float, float, float, float]:
        return (self.cu - self.radius, self.cu + self.radius,
                self.cv - self.radius, self.cv + self.radius)

    def draw(self, frame: Frame, width: int, length: int) -> Mask:
        return frame.disc(width, length, self.cu, self.cv, self.radius)


class Layout:
    """A declared plan, in the same three pieces every other plan comes in."""

    __slots__ = ("mass", "frame", "parts", "order", "shapes")

    def __init__(self, mass: Mask, frame: Frame, parts: dict[str, Part],
                 shapes: list):
        self.mass = mass
        self.frame = frame
        self.parts = parts
        self.order = [s.name for s in shapes]
        self.shapes = shapes

    def lines(self) -> list[str]:
        out = [repr(self.frame)]
        for shape in self.shapes:
            part = self.parts[shape.name]
            du, dv = part.extent
            out.append(f"  {shape.name:14s} {du:6.1f} x {dv:5.1f} m   "
                       f"declared from {shape.source}")
        return out


def layout(shapes: list, angle: float = 0.0, margin: int = MARGIN) -> Layout:
    """Rasterise a table of declared shapes into a plan.

    `angle` is the building's bearing, and zero is a perfectly good answer for a
    building that nothing places on a map: a declared plan has no street grid to
    sit in, and drawing it square keeps the staircase out of a build whose
    dimensions are already the softest thing about it. Give an angle when
    something does fix it -- a site plan, a map the build will be dropped onto.
    """
    if not shapes:
        raise ValueError("a declared plan needs at least one shape")
    for shape in shapes:
        if not getattr(shape, "source", ""):
            raise SystemExit(
                f"declared part {shape.name!r} names no source. Every number in "
                "a declared plan is somebody's statement, and one that does not "
                "say whose is a number that cannot be re-read, corrected, or "
                "argued with.")

    u0 = min(s.bounds()[0] for s in shapes)
    u1 = max(s.bounds()[1] for s in shapes)
    v0 = min(s.bounds()[2] for s in shapes)
    v1 = max(s.bounds()[3] for s in shapes)

    # The grid has to hold the plan after it is turned, so it is sized on the
    # world bounding box of the rotated corners rather than on the u/v extent.
    rad = math.radians(angle)
    cos, sin = math.cos(rad), math.sin(rad)
    corners = [(u * cos - v * sin, u * sin + v * cos)
               for u in (u0, u1) for v in (v0, v1)]
    x0 = min(x for x, _ in corners)
    z0 = min(z for _, z in corners)
    width = int(math.ceil(max(x for x, _ in corners) - x0)) + 2 * margin + 1
    length = int(math.ceil(max(z for _, z in corners) - z0)) + 2 * margin + 1
    frame = Frame((margin - x0, margin - z0), angle, u1 - u0, v1 - v0)

    parts: dict[str, Part] = {}
    masks = []
    for shape in shapes:
        mask = shape.draw(frame, width, length)
        if not mask.count():
            raise SystemExit(
                f"declared part {shape.name!r} rasterised to nothing. Its "
                "dimensions are under a metre, or two of the numbers are the "
                "wrong way round.")
        masks.append(mask)
        parts[shape.name] = Part(mask, frame)

    return Layout(Mask.union(masks, width, length), frame, parts, list(shapes))


__all__ = ["Layout", "Rect", "Round", "layout"]
