"""The Google Earth mesh, read as a height field.

The mesh is a reference and a measuring stick, never a source of geometry: it is
photogrammetry, so its surfaces are noisy, its windows are painted on and its
trees are fused to the walls it should be describing. Voxelising it would import
all of that. What it is reliable for is bulk -- how tall each part of the building
is, and where one part steps down to another -- and that is what this module
extracts.

Sampling is by vertex rather than by rasterised triangle. A Google Earth tile is
roughly uniformly tessellated, so vertices already sample the surface densely
enough for metre-scale cells; triangle rasterisation would cost far more and add
nothing at this resolution.

Axes follow ge_convert.py's output: X east, Y up, Z south -- the same handedness
as Minecraft, so a mesh point drops straight into a Frame built from the layout.
"""

from __future__ import annotations

from pathlib import Path


class Mesh:
    """Vertices of a merged OBJ, in metres, X east / Y up / Z south."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x: list[float], y: list[float], z: list[float]):
        self.x = x
        self.y = y
        self.z = z

    def __len__(self) -> int:
        return len(self.x)

    @classmethod
    def read(cls, path: str | Path) -> "Mesh":
        xs: list[float] = []
        ys: list[float] = []
        zs: list[float] = []
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if not line.startswith("v "):
                    continue
                _, a, b, c = line.split(maxsplit=3)
                xs.append(float(a))
                ys.append(float(b))
                zs.append(float(c))
        return cls(xs, ys, zs)

    def bounds(self) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
        return (
            (min(self.x), min(self.y), min(self.z)),
            (max(self.x), max(self.y), max(self.z)),
        )

    def ground(self, quantile: float = 0.02) -> float:
        """Datum height, as a low quantile of Y.

        A flat minimum would latch onto whatever the export clipped through --
        a kerb, a sliver of road cut below grade -- so take a quantile instead.
        """
        ys = sorted(self.y)
        return ys[max(0, min(len(ys) - 1, int(quantile * len(ys))))]

    def above(self, datum: float, height: float) -> list[int]:
        """Indices of vertices more than `height` metres above `datum`."""
        limit = datum + height
        return [i for i, y in enumerate(self.y) if y > limit]

    def plan(self, indices: list[int] | None = None) -> list[tuple[float, float]]:
        """Vertices projected to the XZ plane, for fitting a frame."""
        if indices is None:
            return list(zip(self.x, self.z))
        return [(self.x[i], self.z[i]) for i in indices]

    def frame(self, datum: float | None = None, floor: float = 6.0,
              pad: float = 0.0):
        """A frame fitted to whatever stands well clear of the ground.

        Fitted to the material above `floor` and not to every vertex, because a
        clip is a box cut out of a larger capture: its ground plane runs out to
        the corners of that box, so a frame fitted to all of it measures the
        clip rather than the building.

        This is the *mesh's* frame. It is not the layout's, even when the clip
        was cut along the building's own axes -- the two were fitted to
        different sources, so their origins and their spans differ by a metre or
        two, and anything reading both has to register them before it compares
        them. See `gate.Registration`.
        """
        from .frame import Frame

        if datum is None:
            datum = self.ground()
        return Frame.fit(self.plan(self.above(datum, floor)), pad=pad)

    def height_field(
        self, frame, datum: float, cell: float = 1.0
    ) -> dict[tuple[int, int], float]:
        """Highest point above `datum` in each (u, v) cell of `frame`.

        Empty cells are absent rather than zero, so a caller can tell "nothing
        was built here" from "something flat was built here".
        """
        field: dict[tuple[int, int], float] = {}
        for i in range(len(self.x)):
            u, v = frame.to_local(self.x[i], self.z[i])
            key = (int(u // cell), int(v // cell))
            top = self.y[i] - datum
            if top > field.get(key, -1e9):
                field[key] = top
        return field
