"""A plan read straight off a 3D model, for a building that has no map.

`plan.decompose` reads a flat map: one pixel to one metre, the drawn lines
subtracted, each enclosed area a part. That is the best plan there is when it
exists, and plenty of jobs have no map at all -- somebody hands over an OBJ of a
Counter-Strike level, or a photogrammetry capture and nothing else. This module
answers the same three questions off the model instead:

    where does it sit, how big is it, which way does it point   -> a frame
    which parts does it have                                    -> plan.Part list
    how tall is each part                                       -> tops, in metres

and it answers them in the same types, so a build script that takes
`(mass, frame, parts)` does not care which of the two produced them.

**The plan comes from the roof.** A map divides a building by the lines somebody
drew inside it; a model divides it by where the roof steps. Both are recording
the same fact -- this piece is a piece -- and the model's version is the one that
needs no draughtsman. Cells are grouped into height plateaus, plateaus into
connected areas, and each area is a part with its own measured top.

**Where a model and a capture differ.** Both arrive here as an OBJ and both are
read the same way, and that is where the similarity stops. A model is
authoritative for shape: what it says is round is round. A Google Earth capture
is authoritative for bulk and heights and nothing else -- its surfaces are noisy,
its windows are painted on, its trees are welded to the walls. This module will
happily decompose either, and `sources.py` is what remembers which one it was and
therefore what the answer is worth. Reading curves back out of a capture is the
one mistake that this whole pipeline is arranged to prevent, so do not do it here
by reaching for `Part.radius` on a capture and calling the result a rotunda.

**Scale is stated, never guessed.** An OBJ carries no units. If the extents come
out absurd this module stops and asks rather than scaling by whatever would make
the answer look like a building -- one guessed scale factor multiplies through
every dimension downstream and there is no later check that can catch it, because
everything agrees with everything else.
"""

from __future__ import annotations

import math
from pathlib import Path

from .frame import Frame
from .mask import Mask
from .mesh import Mesh
from .plan import Part

FLOOR = 2.0     # material this far above the datum is building, not ground
CELL = 1.0      # metres per grid cell, which is one block
MARGIN = 4      # blocks of clear grid left around the mass
STEP = 2.0      # a roof step this big or bigger starts a new part
MIN_CELLS = 24  # an area smaller than this is a fragment, not a part

# What a building's longest side may plausibly be, in metres. Outside this the
# model is not in metres, and going on would be worse than stopping.
SANE = (3.0, 4000.0)


class Grid:
    """The translation between the model's XZ and the schematic's cells.

    A map project gets its grid for free: the crop is the grid, one pixel to one
    block, and the world position comes with it. A model carries no such thing --
    its coordinates start wherever the exporter's origin happened to be -- so the
    grid is made here, big enough for the building and `MARGIN` clear all round.

    This means a model-only project knows the building's size and shape exactly
    and its position in the Minecraft world not at all. Where it stands is a
    decision a person makes, outside this pipeline.
    """

    __slots__ = ("width", "length", "x0", "z0", "cell")

    def __init__(self, width: int, length: int, x0: float, z0: float,
                 cell: float = CELL):
        self.width = width
        self.length = length
        self.x0 = x0
        self.z0 = z0
        self.cell = cell

    def to_cell(self, x: float, z: float) -> tuple[int, int]:
        return (int((x - self.x0) / self.cell), int((z - self.z0) / self.cell))

    def to_model(self, x: int, z: int) -> tuple[float, float]:
        return (self.x0 + (x + 0.5) * self.cell, self.z0 + (z + 0.5) * self.cell)

    def __repr__(self) -> str:
        return (f"<grid {self.width}x{self.length} cells of {self.cell} m, "
                f"origin ({self.x0:.1f}, {self.z0:.1f})>")


class Massing:
    """What a model says about the building, before anything is drawn.

    `mass` and `frame` and `parts` are the same three things `plan.decompose`
    returns and can be used interchangeably with them. `tops` and `field` are the
    extra a model has and a map does not: an actual height for every part and for
    every cell, measured rather than inferred from a separate capture.
    """

    __slots__ = ("mass", "frame", "parts", "tops", "field", "grid", "datum")

    def __init__(self, mass: Mask, frame: Frame, parts: list[Part],
                 tops: list[float], field: dict[tuple[int, int], float],
                 grid: Grid, datum: float):
        self.mass = mass
        self.frame = frame
        self.parts = parts
        self.tops = tops
        self.field = field
        self.grid = grid
        self.datum = datum

    @property
    def model_frame(self) -> Frame:
        """The plan's frame, expressed in the model's own coordinates.

        The plan is measured on the grid and the mesh lives where the exporter
        left it, so anything that wants to read the mesh in the plan's u and v --
        `measure.skyline`, `measure.storey_height`, `measure.presence` -- needs
        the same frame shifted back by the grid's origin. Shifted, not fitted:
        fitting a second frame to the same mesh would put it a metre or two off
        the first for no reason but arithmetic, and then every window opened in
        one would be in the wrong place in the other.

        This is what makes a model-only project need no registration at all. A
        map project has two independent fits and must reconcile them; here there
        is one fit and a translation.
        """
        if self.grid.cell != 1.0:
            raise ValueError(
                "one block is one metre in this pipeline, so a grid of "
                f"{self.grid.cell} m cells has nothing to say to a build. "
                "Re-read the model with cell=1.0.")
        return Frame((self.frame.origin[0] + self.grid.x0,
                      self.frame.origin[1] + self.grid.z0),
                     self.frame.angle, self.frame.extent_u, self.frame.extent_v)

    def lines(self) -> list[str]:
        pieces = len(self.mass.components())
        out = [repr(self.grid), repr(self.frame),
               f"  datum {self.datum:.2f} m, {self.mass.count()} cells of plan"
               + (f" in {pieces} separate pieces" if pieces > 1 else "")]
        for i, part in enumerate(self.parts):
            du, dv = part.extent
            out.append(f"  part {i:<2d} {part.kind:5s} "
                       f"u {part.u0:7.1f}..{part.u1:7.1f}  "
                       f"v {part.v0:6.1f}..{part.v1:6.1f}  "
                       f"{du:5.1f} x {dv:5.1f} m  to {self.tops[i]:5.1f} m")
        return out


def load(path: str | Path, up: str = "y", scale: float = 1.0) -> Mesh:
    """The OBJ, in metres, X east / Y up / Z south.

    `up` is the model's own up axis, because half the exporters in the world
    write Z-up and the other half write Y-up and the file does not say which.
    Getting it wrong is not subtle -- the building comes out lying on its side --
    so this is a stated argument rather than a sniffed one.
    """
    mesh = Mesh.read(path)
    if not len(mesh):
        raise SystemExit(f"{path} has no vertices. Is it an OBJ, and did the "
                         "export include geometry rather than only materials?")
    if up == "z":
        mesh = Mesh(mesh.x, [v for v in mesh.z], [-v for v in mesh.y])
    elif up != "y":
        raise ValueError(f"up must be 'y' or 'z', not {up!r}")
    if scale != 1.0:
        mesh = Mesh([v * scale for v in mesh.x],
                    [v * scale for v in mesh.y],
                    [v * scale for v in mesh.z])
    return mesh


def _check_scale(mesh: Mesh, path) -> None:
    (x0, y0, z0), (x1, y1, z1) = mesh.bounds()
    longest = max(x1 - x0, z1 - z0)
    if SANE[0] <= longest <= SANE[1]:
        return
    raise SystemExit(
        f"{path} measures {x1 - x0:.1f} x {y1 - y0:.1f} x {z1 - z0:.1f} in its "
        f"own units, so its longest side is {longest:.1f}.\n"
        "An OBJ does not carry units, and this is not metres. State the scale "
        "rather than let it be guessed -- pass scale= to model.read, and say in "
        "the build script where the number came from. One guessed scale factor "
        "multiplies through every dimension downstream, and nothing later can "
        "catch it: the build will agree with itself perfectly at the wrong size.")


def _fill_holes(mask: Mask) -> Mask:
    """Close the courtyards a shell leaves behind.

    A model is a surface, so its vertices land on the walls and the roof and
    nowhere in between: projected down, a hollow building draws a ring, and
    everything inside the ring reads as open air. Flooding the outside and
    keeping what it could not reach fills those in.

    A real courtyard -- open to the sky, walls all round -- gets filled by this
    too, and it should be: the plan of a building with a courtyard includes the
    courtyard, and what happens inside it is a decision the build makes later,
    not something to lose here by accident.
    """
    w, h = mask.width, mask.length
    outside = Mask(w, h)
    stack = []
    for x in range(w):
        for z in (0, h - 1):
            i = z * w + x
            if not mask.bits[i] and not outside.bits[i]:
                outside.bits[i] = 1
                stack.append(i)
    for z in range(h):
        for x in (0, w - 1):
            i = z * w + x
            if not mask.bits[i] and not outside.bits[i]:
                outside.bits[i] = 1
                stack.append(i)

    while stack:
        i = stack.pop()
        x, z = i % w, i // w
        for nx, nz in ((x - 1, z), (x + 1, z), (x, z - 1), (x, z + 1)):
            if not (0 <= nx < w and 0 <= nz < h):
                continue
            j = nz * w + nx
            if mask.bits[j] or outside.bits[j]:
                continue
            outside.bits[j] = 1
            stack.append(j)

    out = mask.copy()
    for i, v in enumerate(outside.bits):
        if not v:
            out.bits[i] = 1
    return out


def _plateaus(tops: list[float], step: float) -> list[tuple[float, float]]:
    """Height bands, split where the roof steps.

    Gap clustering rather than fixed bins: a building whose two wings stand at
    11.8 m and 12.1 m is one plateau however the bins fall, and one at 11.9 and
    15.9 is two even though a 4 m bin would put them in the same bucket. Fixed
    bins put the answer at the mercy of where zero happens to be.
    """
    ordered = sorted(tops)
    if not ordered:
        return []
    bands, lo, prev = [], ordered[0], ordered[0]
    for value in ordered[1:]:
        if value - prev >= step:
            bands.append((lo, prev))
            lo = value
        prev = value
    bands.append((lo, prev))
    return bands


def _absorb(parts: list[Mask], mass: Mask) -> list[Mask]:
    """Hand every unclaimed cell to the part nearest it.

    Banding leaves crumbs: a sloped roof, a parapet, a chimney and every noisy
    cell of a capture fall into components too small to be parts of their own.
    Dropping them would leave holes in the plan, and a hole in the plan is a hole
    in the building. So they are given to whichever part reaches them first,
    which is the same answer a person gives when asked which piece the edge of
    the roof belongs to.
    """
    claimed = Mask(mass.width, mass.length)
    owner = [-1] * len(mass.bits)
    for index, part in enumerate(parts):
        for i, v in enumerate(part.bits):
            if v:
                claimed.bits[i] = 1
                owner[i] = index

    frontier = [i for i, v in enumerate(claimed.bits) if v]
    w, h = mass.width, mass.length
    while frontier:
        nxt = []
        for i in frontier:
            x, z = i % w, i // w
            for nx, nz in ((x - 1, z), (x + 1, z), (x, z - 1), (x, z + 1)):
                if not (0 <= nx < w and 0 <= nz < h):
                    continue
                j = nz * w + nx
                if not mass.bits[j] or claimed.bits[j]:
                    continue
                claimed.bits[j] = 1
                owner[j] = owner[i]
                nxt.append(j)
        frontier = nxt

    grown = [Mask(w, h) for _ in parts]
    for i, index in enumerate(owner):
        if index >= 0:
            grown[index].bits[i] = 1
    return grown


def read(path: str | Path, *, up: str = "y", scale: float = 1.0,
         floor: float = FLOOR, step: float = STEP, cell: float = CELL,
         margin: int = MARGIN, min_cells: int = MIN_CELLS) -> Massing:
    """Everything a model can state about a building's plan and its heights.

    `floor` is what separates building from ground: material below it is the
    terrain the capture or the model brought along, and including it would put
    the car park inside the footprint. Two metres keeps a plinth and loses a
    kerb.
    """
    mesh = load(path, up=up, scale=scale)
    _check_scale(mesh, path)

    datum = mesh.ground()
    standing = mesh.above(datum, floor)
    if not standing:
        (_, y0, _), (_, y1, _) = mesh.bounds()
        raise SystemExit(
            f"nothing in {path} stands more than {floor} m above its ground "
            f"({y1 - y0:.1f} m from the lowest vertex to the highest). Either "
            "this is a terrain tile with no building on it, or the up axis is "
            "wrong -- pass up='z' if the exporter wrote Z-up.")

    xs = [mesh.x[i] for i in standing]
    zs = [mesh.z[i] for i in standing]
    grid = Grid(int(math.ceil((max(xs) - min(xs)) / cell)) + 2 * margin + 1,
                int(math.ceil((max(zs) - min(zs)) / cell)) + 2 * margin + 1,
                min(xs) - margin * cell, min(zs) - margin * cell, cell)

    # The height field, and the silhouette that comes with it. Highest vertex
    # per cell, as `mesh.height_field` does, but on this grid rather than in a
    # frame -- the frame is not fitted yet, and it is fitted to this.
    field: dict[tuple[int, int], float] = {}
    for i in standing:
        key = grid.to_cell(mesh.x[i], mesh.z[i])
        top = mesh.y[i] - datum
        if top > field.get(key, -1e9):
            field[key] = top

    silhouette = Mask(grid.width, grid.length)
    for (x, z) in field:
        silhouette.set(x, z)
    blobs = _fill_holes(silhouette).components(min_cells=min_cells)
    if not blobs:
        raise SystemExit(
            f"{path} left nothing bigger than {min_cells} cells standing above "
            f"{floor} m. If the building really is that small, lower min_cells "
            "and say why in the build script.")
    # Every piece, not the largest. A building with a detached wing across a
    # court is two pieces of one building, and keeping only the biggest silently
    # deletes the other one -- which is exactly what a map does *not* do, because
    # there the drawn line joins the wings into one blob.
    #
    # The rule this relies on is that the file has been clipped to this building.
    # A neighbour left inside the clip arrives here as another piece of it, and
    # the registration check downstream is what catches that: material that is
    # not this building stretches one axis against the other.
    mass = Mask.union(blobs, grid.width, grid.length)
    frame = Frame.fit_mask(mass)

    # Interior cells have no vertices of their own -- the fill above invented
    # them -- so they are given the height of the nearest cell that does, by
    # taking the plateau of whatever grew into them. Nothing here reads a height
    # off a filled cell.
    banded: list[Mask] = []
    for lo, hi in _plateaus([field[key] for key in field if mass.get(*key)],
                            step):
        band = Mask(grid.width, grid.length)
        for key, top in field.items():
            if lo <= top <= hi and mass.get(*key):
                band.set(*key)
        banded.extend(band.components(min_cells=min_cells))

    if not banded:
        banded = [mass]
    ordered = sorted(_absorb(banded, mass), key=lambda m: -m.count())

    parts, tops = [], []
    for piece in ordered:
        heights = sorted(field[key] for key in field.keys()
                         if piece.get(*key))
        if not heights:
            continue
        parts.append(Part(piece, frame))
        # The median, not the maximum: one aerial or one spike of photogrammetry
        # noise sets a maximum, and a part's height is what most of it reaches.
        tops.append(heights[len(heights) // 2])

    return Massing(mass, frame, parts, tops, field, grid, datum)


def decompose(path: str | Path, **kwargs):
    """(mass, frame, parts) -- `plan.decompose`'s signature, off a model.

    For a build script that should work from either kind of project without
    caring which. Heights are dropped on the floor here; call `read` when you
    want them, which is most of the time, because heights are the reason to have
    a model in the first place.
    """
    massing = read(path, **kwargs)
    return massing.mass, massing.frame, massing.parts


__all__ = ["Grid", "Massing", "decompose", "load", "read"]
