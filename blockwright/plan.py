"""Reading the layout template as a plan rather than as a silhouette.

`flatmap.footprint` merges the drawn lines into the mass and returns one blob.
That is the right input for "where does the building sit and which way does it
point", and the wrong input for everything after: a blob has no rooms, so the
build ends up as one extruded outline and the parts the mapper drew -- the
rotunda, the courtyard, the separate wings -- are lost before the first block is
placed.

The lines are the plan. Subtracting the line layer from the mass leaves the
enclosed areas, and each of those is a part with its own shape, its own size and
its own reason to exist. This module hands them over already measured and already
classified, so a build script can say "the round one" and "the long one" instead
of hard-coding the coordinates a human read off a picture.

Classification is by fitted circle, not by the isoperimetric ratio. `4*pi*A/P^2`
looks like the obvious test and fails here, because contour tracing charges one
step for a diagonal and so under-measures the perimeter of a shape drawn at 52
degrees; the ratio then reports a rectangle as fairly round. A circle fit is
scale-free and reports its own residual, which is the number worth trusting.
"""

from __future__ import annotations

import math

from .frame import Frame
from .mask import Mask
from .flatmap import footprint, layers
from .flatmap import read as read_template

ROUND = 0.25        # fit residual, as a fraction of the fitted radius
SQUARE = 1.35       # longer extent over shorter, at most


def fit_circle(points: list[tuple[float, float]]):
    """Least-squares circle through a set of points: (cu, cv, radius, rms).

    Kasa's linearisation. Writing the circle as `u^2 + v^2 = 2a*u + 2b*v + c`
    makes it linear in (a, b, c), which turns the fit into one 3x3 solve instead
    of an iteration that needs a starting guess. It pulls the centre slightly
    towards a dense arc, which does not matter for a shape that is drawn all the
    way round, and it cannot fail to converge, which does matter in a pipeline.
    """
    n = len(points)
    if n < 3:
        raise ValueError("a circle needs three points")

    su = sv = suu = svv = suv = sw = swu = swv = 0.0
    for u, v in points:
        w = u * u + v * v
        su += u
        sv += v
        suu += u * u
        svv += v * v
        suv += u * v
        sw += w
        swu += w * u
        swv += w * v

    m = [[suu, suv, su, swu],
         [suv, svv, sv, swv],
         [su, sv, float(n), sw]]

    for col in range(3):
        pivot = max(range(col, 3), key=lambda r: abs(m[r][col]))
        if abs(m[pivot][col]) < 1e-12:
            raise ValueError("degenerate circle fit")
        m[col], m[pivot] = m[pivot], m[col]
        for row in range(col + 1, 3):
            factor = m[row][col] / m[col][col]
            for k in range(col, 4):
                m[row][k] -= factor * m[col][k]

    sol = [0.0, 0.0, 0.0]
    for row in (2, 1, 0):
        total = m[row][3] - sum(m[row][k] * sol[k] for k in range(row + 1, 3))
        sol[row] = total / m[row][row]

    cu, cv = sol[0] / 2, sol[1] / 2
    radius = math.sqrt(max(0.0, sol[2] + cu * cu + cv * cv))
    error = sum((math.hypot(u - cu, v - cv) - radius) ** 2 for u, v in points)
    return cu, cv, radius, math.sqrt(error / n)


class Part:
    """One enclosed area of the plan, measured in the building's frame."""

    __slots__ = ("mask", "kind", "u0", "u1", "v0", "v1",
                 "centre", "radius", "residual")

    def __init__(self, mask: Mask, frame: Frame):
        self.mask = mask
        us, vs = [], []
        for x, z in mask.cells():
            u, v = frame.to_local(x + 0.5, z + 0.5)
            us.append(u)
            vs.append(v)
        self.u0, self.u1 = min(us), max(us)
        self.v0, self.v1 = min(vs), max(vs)

        rim = [frame.to_local(x + 0.5, z + 0.5) for x, z in mask.rim().cells()]
        try:
            cu, cv, fitted, rms = fit_circle(rim)
        except ValueError:
            cu, cv, fitted, rms = 0.0, 0.0, 0.0, float("inf")
        self.centre = (cu, cv)
        self.residual = rms

        du, dv = self.u1 - self.u0, self.v1 - self.v0
        self.kind = (
            "disc"
            if fitted > 0 and rms < ROUND * fitted
            and max(du, dv) < SQUARE * min(du, dv)
            else "strip"
        )
        # A drawn circle is nearly always clipped by whatever it abuts, and a
        # long straight chord drags a least-squares circle inwards. The centre
        # survives that -- the clipping is roughly symmetric -- so the radius is
        # read back off the arc that did survive, not off the fit.
        spread = sorted(math.hypot(u - cu, v - cv) for u, v in rim)
        self.radius = (
            spread[int(0.95 * (len(spread) - 1))] if self.kind == "disc"
            else fitted
        )

    @property
    def extent(self) -> tuple[float, float]:
        return (self.u1 - self.u0, self.v1 - self.v0)

    def __repr__(self) -> str:
        du, dv = self.extent
        shape = (f"r {self.radius:.1f} m at ({self.centre[0]:.1f}, "
                 f"{self.centre[1]:.1f}), rms {self.residual:.2f} m"
                 if self.kind == "disc" else f"{du:.1f} x {dv:.1f} m")
        return (f"<{self.kind} {self.mask.count()} cells, "
                f"u {self.u0:.1f}..{self.u1:.1f}, "
                f"v {self.v0:.1f}..{self.v1:.1f}, {shape}>")


def lines(path, min_cells: int = 200, palette=None) -> Mask:
    """The drawn line layer on its own -- darker than fill, lighter than road.

    Kept as a name because `decompose` reads better for it. The extraction
    itself belongs to `flatmap`, which is where a map's palette is written down;
    having the thresholds in two files is how the two files come to disagree
    about what a line is.
    """
    from .flatmap import DEFAULT

    return layers(path, min_cells, palette or DEFAULT).line


def decompose(path, min_cells: int = 20, palette=None):
    """(mass, frame, parts) -- the plan split along its own drawn lines.

    Parts come back largest first, each already classified and already measured
    in the frame fitted to the whole mass, so their coordinates compose.

    `palette` says which greys this map draws a building with; the default is
    one particular map's, and a crop from anywhere else needs its own. See
    `flatmap.Palette`.
    """
    from .flatmap import DEFAULT

    palette = palette or DEFAULT
    mass, frame = read_template(path, palette)
    rooms = (mass - lines(path, palette=palette)).components(min_cells=min_cells)
    return mass, frame, [Part(room, frame) for room in rooms]


__all__ = ["Part", "decompose", "fit_circle", "footprint", "lines"]
