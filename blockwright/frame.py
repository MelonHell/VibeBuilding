"""The building's own coordinate frame.

Buildings on this map sit at whatever angle the street grid gives them -- Terra
Beachside is at 52.43 degrees -- and they have to be built at that angle, not
snapped to the axes. So geometry is authored in the building's frame:

    u   along the long axis, 0 at the near end
    v   across it, 0 at one long edge
    y   up, shared with the world

The frame is both the measuring device and the drawing board. Fitting it to the
layout template reads off the three things the template is authoritative for --
where the building sits, how big it is, and which way it points. Everything after
that is authored in (u, v) as clean shapes and rasterised back out through
`region`.

Tracing the template's own pixels instead is a mistake that looks like accuracy.
The template is a hand-drawn map: its edges wander by a metre or two and its
internal lines are not straight, so a footprint copied cell by cell inherits every
wobble and there is no later step that can tell a deliberate step in the plan from
a shaky hand. Measuring first and drawing second keeps the two apart.

The axis comes from the minimum-area oriented bounding box. Principal component
analysis is the obvious alternative and it is wrong here -- a notch or a narrowed
wing skews the second moment, and on this footprint PCA reported 57.2 degrees
against the true 52.4. Rotating calipers on the convex hull is exact: the
minimum-area rectangle always has a side flush with a hull edge, so only the hull
edge directions need testing.
"""

from __future__ import annotations

import math

from . import fast


def convex_hull(points: list[tuple[float, float]]) -> list[tuple[float, float]]:
    """Andrew's monotone chain, counter-clockwise, without collinear points."""
    pts = sorted(set(points))
    if len(pts) < 3:
        return pts

    def half(seq):
        out: list[tuple[float, float]] = []
        for p in seq:
            while len(out) >= 2:
                (ax, ay), (bx, by) = out[-2], out[-1]
                if (bx - ax) * (p[1] - ay) - (by - ay) * (p[0] - ax) > 0:
                    break
                out.pop()
            out.append(p)
        return out

    lower = half(pts)
    upper = half(reversed(pts))
    return lower[:-1] + upper[:-1]


class Frame:
    """Maps between world XZ and the building's (u, v).

    `origin` is the world point that (0, 0) maps to; `angle` is measured from
    +X (east) towards +Z (south), matching Minecraft's left-handed grid.
    """

    __slots__ = ("origin", "angle", "extent_u", "extent_v", "_cos", "_sin")

    def __init__(
        self,
        origin: tuple[float, float],
        angle_deg: float,
        extent_u: float = 0.0,
        extent_v: float = 0.0,
    ):
        self.origin = (float(origin[0]), float(origin[1]))
        self.angle = float(angle_deg)
        self.extent_u = float(extent_u)
        self.extent_v = float(extent_v)
        rad = math.radians(self.angle)
        self._cos = math.cos(rad)
        self._sin = math.sin(rad)

    def to_local(self, x: float, z: float) -> tuple[float, float]:
        dx = x - self.origin[0]
        dz = z - self.origin[1]
        return (dx * self._cos + dz * self._sin, -dx * self._sin + dz * self._cos)

    def to_world(self, u: float, v: float) -> tuple[float, float]:
        return (
            self.origin[0] + u * self._cos - v * self._sin,
            self.origin[1] + u * self._sin + v * self._cos,
        )

    def u_of(self, x: float, z: float) -> float:
        return (x - self.origin[0]) * self._cos + (z - self.origin[1]) * self._sin

    def v_of(self, x: float, z: float) -> float:
        return -(x - self.origin[0]) * self._sin + (z - self.origin[1]) * self._cos

    @property
    def direction(self) -> str:
        """Compass bearing of +u, for sanity-checking a fit."""
        names = ["east", "southeast", "south", "southwest",
                 "west", "northwest", "north", "northeast"]
        return names[int((self.angle % 360 + 22.5) // 45) % 8]

    def __repr__(self) -> str:
        return (
            f"Frame(origin=({self.origin[0]:.2f}, {self.origin[1]:.2f}), "
            f"angle={self.angle:.2f} deg towards {self.direction}, "
            f"extent={self.extent_u:.1f}x{self.extent_v:.1f})"
        )

    # -- fitting ----------------------------------------------------------

    @classmethod
    def fit(cls, points: list[tuple[float, float]], pad: float = 0.5) -> "Frame":
        """Minimum-area oriented bounding box around a set of cells.

        Cells are addressed by their corner, so `pad` grows the box by half a
        block on each side to cover the cell bodies rather than their corners.
        The longer extent becomes u.
        """
        if not points:
            raise ValueError("cannot fit a frame to no points")
        hull = convex_hull([(float(x), float(z)) for x, z in points])
        if len(hull) < 3:
            hull = [(float(x), float(z)) for x, z in points]

        best = None
        for i in range(len(hull)):
            ax, az = hull[i]
            bx, bz = hull[(i + 1) % len(hull)]
            edge = math.hypot(bx - ax, bz - az)
            if edge < 1e-9:
                continue
            c, s = (bx - ax) / edge, (bz - az) / edge
            us = [x * c + z * s for x, z in hull]
            vs = [-x * s + z * c for x, z in hull]
            u0, u1 = min(us), max(us)
            v0, v1 = min(vs), max(vs)
            area = (u1 - u0) * (v1 - v0)
            if best is None or area < best[0]:
                best = (area, c, s, u0, u1, v0, v1)

        _, c, s, u0, u1, v0, v1 = best
        du, dv = u1 - u0, v1 - v0
        if dv > du:
            # Swap so u is the long axis: rotate the frame a quarter turn.
            c, s = -s, c
            u0, u1, v0, v1 = v0, v1, -u1, -u0
            du, dv = dv, du

        if s < 0 or (abs(s) < 1e-12 and c < 0):
            # Point +u into the eastern half-plane, always. A minimum-area box
            # fixes an axis and not a direction: the same building fitted twice
            # can come back at 30 degrees or at 210, and both are correct
            # descriptions of the same rectangle.
            #
            # It stops being harmless the moment two frames of one building are
            # registered against each other, which is what happens whenever a
            # map and a capture, or a model and a capture, are both supplied.
            # Registration is scale and shift per axis; it cannot express a
            # half-turn. So the extents match, the scales agree, every check
            # passes -- and u runs backwards, so the front wing is graded
            # against the back one's roof. It was found by the witness rows the
            # moment they existed: "part heights agree: capture 8.00 against
            # model 12.00", on a fixture whose two wings are 8 and 12.
            c, s = -c, -s
            u0, u1, v0, v1 = -u1, -u0, -v1, -v0

        u0 -= pad
        v0 -= pad
        du += 2 * pad
        dv += 2 * pad
        # Un-rotate the local minimum corner back into world space.
        origin = (u0 * c - v0 * s, u0 * s + v0 * c)
        return cls(origin, math.degrees(math.atan2(s, c)) % 360.0, du, dv)

    @classmethod
    def fit_mask(cls, mask, pad: float = 0.5) -> "Frame":
        return cls.fit(mask.cells(), pad=pad)

    # -- drawing ----------------------------------------------------------

    def region(self, width: int, length: int, predicate):
        """Rasterise a shape described in (u, v) onto the world grid.

        `predicate(u, v)` is asked about the centre of every cell, so a shape
        written as plain inequalities -- `0 <= u < 105 and 4 <= v < 13` -- comes
        out as a mask at the building's angle, with the staircase falling where
        the geometry actually puts it rather than where a hand-drawn line did.
        """
        from .mask import Mask

        out = Mask(width, length)
        for z in range(length):
            for x in range(width):
                u, v = self.to_local(x + 0.5, z + 0.5)
                if predicate(u, v):
                    out.bits[z * width + x] = 1
        return out

    def rect(self, width: int, length: int,
             u0: float, u1: float, v0: float, v1: float):
        """The common case of `region`: a rectangle in the building's frame.

        Nine calls in ten to `region` are this one -- every footprint, every
        wall band, every floor plate -- so it has a fast path rather than going
        through a Python predicate per cell. The two agree bit for bit and
        `tools/mask_selftest.py` will not pass unless they do.
        """
        from .mask import Mask

        if fast.HAVE:
            return Mask(width, length,
                        fast.rect(width, length, self.origin, self._cos,
                                  self._sin, u0, u1, v0, v1))
        return self.region(
            width, length,
            lambda u, v: u0 <= u < u1 and v0 <= v < v1,
        )

    def disc(self, width: int, length: int,
             cu: float, cv: float, radius: float, inner: float = 0.0):
        """A circle, or an annulus if `inner` is given.

        Rasterised from the equation rather than traced, so it stays round at
        any angle and at any radius -- a circle drawn on the map is a circle in
        the build, not a polygon inherited from the map's own pixels.
        """
        from .mask import Mask

        outer2, inner2 = radius * radius, inner * inner
        if fast.HAVE:
            return Mask(width, length,
                        fast.disc(width, length, self.origin, self._cos,
                                  self._sin, cu, cv, outer2, inner2))
        return self.region(
            width, length,
            lambda u, v: inner2 <= (u - cu) ** 2 + (v - cv) ** 2 < outer2,
        )

    # -- dividing ---------------------------------------------------------

    def bays(self, u0: float, u1: float, count: int,
             joint: float = 0.0) -> list[tuple[float, float]]:
        """Split a run along u into equal bays, each shrunk by half a joint.

        The joint is the gap left between neighbours -- the recess that makes a
        terrace read as separate houses rather than one long block. Returned
        spans are the solid parts, so the first and last keep the run's own ends.
        """
        if count < 1:
            raise ValueError("a run has at least one bay")
        pitch = (u1 - u0) / count
        out = []
        for i in range(count):
            a = u0 + i * pitch + (joint / 2 if i else 0.0)
            b = u0 + (i + 1) * pitch - (joint / 2 if i < count - 1 else 0.0)
            out.append((a, b))
        return out

    def split_u(self, mask, u0: float, u1: float, count: int,
                joint: float = 0.0) -> list:
        """`bays`, applied to a mask: one mask per bay, in order along u."""
        return [self.slice_u(mask, a, b) for a, b in self.bays(u0, u1, count, joint)]

    # -- measuring --------------------------------------------------------

    def profile(self, mask, step: int = 1) -> list[tuple[int, float, float]]:
        """Width of a mask across the axis, bucketed by u.

        Returns (u, v_min, v_max) per bucket -- the section drawing you would
        sketch before deciding where a building steps in or changes height.
        """
        buckets: dict[int, list[float]] = {}
        for x, z in mask.cells():
            u, v = self.to_local(x + 0.5, z + 0.5)
            buckets.setdefault(int(u // step) * step, []).append(v)
        return [(k, min(vs), max(vs)) for k, vs in sorted(buckets.items())]

    def slice_u(self, mask, u0: float, u1: float):
        """Cells whose u falls in [u0, u1) -- one wing, bay or section."""
        return mask.filter(
            lambda x, z: u0 <= self.u_of(x + 0.5, z + 0.5) < u1
        )

    def slice_v(self, mask, v0: float, v1: float):
        return mask.filter(
            lambda x, z: v0 <= self.v_of(x + 0.5, z + 0.5) < v1
        )
