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

        **This is the single most dangerous number in the pipeline, and it fails
        quietly.** Photogrammetry hangs skirts of unclosed polygons below the
        surface it could not seal; on one capture seven per cent of the vertices
        were *under* the ground, and a two per cent quantile landed nine metres
        below it. Nothing downstream complains: every threshold is measured from
        the datum, so "six metres above the ground" becomes "inside the ground",
        the registration fits the clip box instead of the building, and it
        reports honest-looking numbers while doing it.

        Use `datum()` instead wherever the answer matters. It returns this
        number *and* the height the ground actually piles up at, so the two can
        be compared before anything is built on either.
        """
        ys = sorted(self.y)
        return ys[max(0, min(len(ys) - 1, int(quantile * len(ys))))]

    def levels(self, band: float = 0.5) -> list[tuple[float, int]]:
        """How many vertices sit at each height, in bands of `band` metres.

        The shape of a capture in one list: the ground is the fullest band by a
        long way, each terrace is a bump, and a skirt of unclosed polygons is a
        thin tail below everything. Sorted by height, not by count, because what
        is being read off it is where things are and not which is biggest.
        """
        if not self.y:
            return []
        low = min(self.y)
        tally: dict[int, int] = {}
        for value in self.y:
            k = int((value - low) / band)
            tally[k] = tally.get(k, 0) + 1
        return [(low + k * band, n) for k, n in sorted(tally.items())]

    def datum(self, quantile: float = 0.02, band: float = 0.5,
              floor: float = 0.5) -> tuple[float, float, float]:
        """(quantile, densest ground band, how far apart they are).

        The second number is the height at which the most vertices pile up
        below the middle of the capture -- the pavement, the car park, the pool
        deck, the road, which on a real site all come out within a metre of each
        other. That is the ground. The first is what `ground()` says. When they
        disagree by more than a metre, the capture is carrying geometry below
        its own grade and the datum is wrong.

        `floor` drops bands holding less than that fraction of the busiest, so a
        skirt of a few hundred polygons cannot be mistaken for a surface.
        """
        cheap = self.ground(quantile)
        bands = self.levels(band)
        if not bands:
            return cheap, cheap, 0.0
        middle = (max(self.y) + min(self.y)) / 2
        low = [(h, n) for h, n in bands if h <= middle]
        if not low:
            low = bands
        busiest = max(n for _, n in low)
        real = max((h for h, n in low if n >= floor * busiest), default=cheap)
        # The *lowest* band that is still a surface, not the fullest one: a
        # building with a big flat roof can out-vote its own pavement, and the
        # ground is the thing underneath everything.
        surfaces = [h for h, n in low if n >= floor * busiest]
        real = min(surfaces) if surfaces else cheap
        return cheap, real, abs(real - cheap)

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
