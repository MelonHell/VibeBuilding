"""Rendering a build so that it can be looked at.

A flat silhouette answers "how tall is it" and nothing else. It cannot answer
"is this one long building or thirteen short ones", because a row of villas and
a single bar cast the same silhouette. That question was missed for exactly that
reason, so this module exists: it draws the blocks as blocks, with the faces
shaded, from any angle including an oblique one.

The camera lives in the building's frame, so a view can be put beside the Google
Earth render of the same angle and the two compared directly. Blocks are still
cubes on the world grid, which sits at 52 degrees to that frame -- so the
staircase shows, as it should, since that is what a person standing there would
see.

It is orthographic by default and perspective when given an eye. Parallel views
measure: two at the same scale can be laid over each other. Perspective views
are what a person sees, and they are the only ones that can be taken from inside
the site -- an orthographic camera has no position to put in the court.

Faces are drawn painter's algorithm, back to front. That is exact for a voxel
model: axis-aligned unit cubes cannot interpenetrate, so a depth sort of faces
has no cycles to resolve. Interior faces are dropped before sorting, which for a
solid build is most of them.

Blocks are not all cubes. A slab, a pane, a fence, a stair fills only part of its
cell, and `blocks.boxes` says which part; each sub-box is drawn as its own little
volume. Once sub-boxes differ in size the depth sort becomes an approximation --
their centroids can order two faces the wrong way where the cubes' never could --
which is accepted, because drawing a slab as a full cube is a much larger lie
than mis-ordering two faces that touch.

Transparency is real too: glass, panes and leaves do not cull the face behind
them and are drawn with alpha. That matters more than it sounds. The renderer
exists to answer "is this one long building or thirteen short ones", and every
way it can be wrong is a way of showing an open thing as closed.

    render(canvas, "iso.png", View.iso(), frame=frame)
    render_set(canvas, out_dir, frame=frame)
"""

from __future__ import annotations

import math
from pathlib import Path

from . import blocks as blocklib

BACKGROUND = (26, 28, 33)

# Sun over the shoulder: high enough that a roof always reads brighter than any
# wall, with enough sideways bias that two walls meeting at a corner never take
# the same shade -- which is what makes a joint between two villas visible.
LIGHT = (-0.30, -0.45, 0.84)
AMBIENT = 0.44
DIFFUSE = 0.56

# How much darker a block's own outline is drawn than its face. Enough to show
# the courses; not so much that a wall turns into a grid.
SEAM = 0.88


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(sum(c * c for c in vector)) or 1.0
    return (vector[0] / n, vector[1] / n, vector[2] / n)


def _cross(a, b):
    return (
        a[1] * b[2] - a[2] * b[1],
        a[2] * b[0] - a[0] * b[2],
        a[0] * b[1] - a[1] * b[0],
    )


class View:
    """An orthographic camera, in the building's (u, v, y).

    `forward` points from the camera into the scene, so a face is visible when
    its normal opposes it and depth increases away from the eye. `right` and
    `up` are the screen axes.

    (u, v, y) is a left-handed triple, because the world grid it is fitted to is
    Minecraft's: x east, z south, y up. So a screen basis built with an ordinary
    cross product comes out mirrored, and a render laid beside a Blender ortho of
    the same angle shows the same building with its two ends swapped -- which
    reads as a real disagreement and is not one. Every basis here is built so
    that `_cross(right, up) == forward` in frame components, which because of that
    handedness is the condition for an image that is not a mirror.

    That test is necessary and not sufficient: negating `right` and `up` together
    is a half turn, which preserves handedness and passes. So a view is also
    checked for roll -- `up` must lean the same way as the world's up, except
    looking straight down where there is no such thing.

    An `eye` makes the camera a perspective one, standing at that point in the
    frame rather than infinitely far off it. That is the only way to render a
    view from inside the site, where an orthographic camera has nowhere to
    stand: parallel projection has no position, so "in the middle of the court
    looking up at the canopy" is not a thing it can express.
    """

    __slots__ = ("forward", "right", "up", "name", "seams", "eye", "fov")

    def __init__(self, forward, right, up, name: str = "view", seams: bool = True,
                 eye: tuple[float, float, float] | None = None,
                 fov: float = 55.0):
        self.forward = _unit(forward)
        self.right = _unit(right)
        self.up = _unit(up)
        self.name = name
        # Looking straight down a frame axis, no block face is parallel to the
        # screen -- the wall is a diagonal staircase, and outlining every step
        # turns it into noise. Such views ask for seams off; obliques want them.
        self.seams = seams
        self.eye = None if eye is None else tuple(float(c) for c in eye)
        self.fov = float(fov)

    @classmethod
    def orbit(cls, azimuth: float, pitch: float, name: str = "orbit") -> "View":
        """Camera at a compass bearing and an elevation, looking at the origin.

        `azimuth` turns around the building's long axis -- 0 puts the eye off the
        far end, past the +u extreme, and 90 puts it out beyond the +v flank --
        and `pitch` lifts it, 90 being straight overhead.

        `right` is the horizontal quarter turn *clockwise* from the eye, not
        anticlockwise: in a left-handed frame that is what puts the building the
        way round a photograph of it shows.
        """
        a, p = math.radians(azimuth), math.radians(pitch)
        forward = (-math.cos(p) * math.cos(a), -math.cos(p) * math.sin(a), -math.sin(p))
        right = (math.sin(a), -math.cos(a), 0.0)
        return cls(forward, right, _cross(forward, right), name)

    @classmethod
    def plan(cls, name: str = "plan") -> "View":
        """Straight down, with the building standing up the page."""
        return cls((0, 0, -1), (0, 1, 0), (1, 0, 0), name, seams=False)

    @classmethod
    def long(cls, flip: bool = False, name: str | None = None) -> "View":
        """The long elevation, from one side or the other."""
        s = -1 if flip else 1
        return cls((0, s, 0), (-s, 0, 0), (0, 0, 1),
                   name or ("long_far" if flip else "long_near"), seams=False)

    @classmethod
    def end(cls, flip: bool = False, name: str | None = None) -> "View":
        """The end elevation, looking along the building."""
        s = -1 if flip else 1
        return cls((s, 0, 0), (0, s, 0), (0, 0, 1),
                   name or ("end_far" if flip else "end_near"), seams=False)

    @classmethod
    def iso(cls, azimuth: float = 35.0, pitch: float = 28.0,
            name: str = "iso") -> "View":
        """The workhorse oblique: enough angle to read plan and height at once."""
        return cls.orbit(azimuth, pitch, name)

    @classmethod
    def shot(cls, eye, target, fov: float = 55.0, name: str = "shot",
             seams: bool = True) -> "View":
        """A perspective camera standing at `eye`, aimed at `target`.

        Both points are in the frame's (u, v, y), in metres, so a camera can be
        put in the court as easily as out on the street. `fov` is the horizontal
        field of view; 55 degrees is roughly a 35 mm lens on full frame, wide
        enough to hold a facade from the pavement and narrow enough not to bend
        it.

        The camera keeps the world's up rather than rolling with the aim, which
        is what a photographer with a level does. `right` then follows from the
        handedness rule in the class docstring.
        """
        forward = _unit((target[0] - eye[0], target[1] - eye[1], target[2] - eye[2]))
        # Straight up or straight down there is no horizon to level against, so
        # take the long axis as the reference instead of the vertical.
        reference = (1.0, 0.0, 0.0) if abs(forward[2]) > 0.999 else (0.0, 0.0, 1.0)
        along = sum(a * b for a, b in zip(reference, forward))
        up = _unit(tuple(reference[i] - along * forward[i] for i in range(3)))
        return cls(forward, _cross(up, forward), up, name, seams=seams,
                   eye=eye, fov=fov)


def _normals(angle_deg: float) -> dict[str, tuple[float, float, float]]:
    """Face normals expressed in the frame, which the world grid is turned in."""
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    return {
        "up": (0.0, 0.0, 1.0),
        "down": (0.0, 0.0, -1.0),
        "east": (c, -s, 0.0),
        "west": (-c, s, 0.0),
        "south": (s, c, 0.0),
        "north": (-s, -c, 0.0),
    }


def _blocks(model):
    """Canvas and Schematic hold the same grid under different names."""
    data = getattr(model, "data", None)
    if data is None:
        data = model.blocks
    return data, model.palette, model.width, model.height, model.length


# How close a face may come to a perspective eye before it is cut away. Anything
# nearer is behind the lens or straddling it, and a straddling quad projects to
# nonsense on the far side of the image.
NEAR = 0.05


def _clip_near(polygon):
    """Sutherland-Hodgman against the near plane, on camera-space points.

    A face is a convex quad and the near plane is a half-space, so the clip is
    convex too and comes back as a polygon that can be drawn as-is. Without this
    a camera standing inside the building draws the wall behind its own head.
    """
    out = []
    for i, current in enumerate(polygon):
        following = polygon[(i + 1) % len(polygon)]
        inside, next_inside = current[2] >= NEAR, following[2] >= NEAR
        if inside:
            out.append(current)
        if inside != next_inside:
            t = (NEAR - current[2]) / (following[2] - current[2])
            out.append(tuple(current[k] + t * (following[k] - current[k])
                             for k in range(3)))
    return out


def render(
    model,
    path: str | Path,
    view: View | None = None,
    frame=None,
    scale: float = 6.0,
    margin: int = 12,
    background: tuple[int, int, int] = BACKGROUND,
    seams: bool | None = None,
    size: tuple[int, int] = (1600, 900),
):
    """Draw a canvas or schematic from one camera. Returns the image.

    `scale` is pixels per metre, so two orthographic renders at the same scale
    can be laid beside each other and measured. A perspective view -- one with
    an `eye` -- has no such constant, so it uses `size` for the image instead and
    frames whatever the lens takes in. Without a `frame` the camera works in
    world axes, which is right for a schematic nobody has fitted a frame to.
    `seams` outlines each block, which shows the courses and the joints between
    volumes; left as None the view decides.
    """
    from PIL import Image, ImageDraw

    view = view or View.iso()
    seams = view.seams if seams is None else seams
    data, palette, W, H, L = _blocks(model)
    area = W * L

    if frame is None:
        to_local = lambda x, z: (float(x), float(z))  # noqa: E731
        angle = 0.0
    else:
        to_local = frame.to_local
        angle = frame.angle
    normals = _normals(angle)

    filled = [i for i, v in enumerate(data) if v]
    if not filled:
        raise ValueError("nothing to render")

    light = _unit(LIGHT)
    fx, fy, fz = view.forward
    rx, ry, rz = view.right
    ux, uy, uz = view.up

    eye = view.eye
    if eye is not None:
        ex, ey_, ez = eye
        half_width, half_height = size[0] * 0.5, size[1] * 0.5
        focal = half_width / math.tan(math.radians(view.fov) * 0.5)

    # Shade and screen-project once per (face kind), not per face.
    shade = {}
    visible = {}
    for kind, n in normals.items():
        visible[kind] = n[0] * fx + n[1] * fy + n[2] * fz < -1e-9
        lit = max(0.0, n[0] * light[0] + n[1] * light[1] + n[2] * light[2])
        shade[kind] = AMBIENT + DIFFUSE * lit

    # Per palette entry, resolved once: what shape it is, whether it hides what
    # is behind it, and how solid it looks.
    shapes = [blocklib.boxes(b) for b in palette]
    cube = [blocklib.is_cube(b) for b in palette]
    opaque = [blocklib.occludes(b) for b in palette]
    alpha = [255 if opaque[v]
             else max(24, min(255, int(255 * (1.0 - blocklib.transparency(b)))))
             for v, b in enumerate(palette)]
    # Only a full opaque cube can hide the face behind it. Culling against
    # anything else is how a glazed canopy erases the frame it is carrying.
    hides = [cube[v] and opaque[v] for v in range(len(palette))]

    unknown = blocklib.unknown_blocks(palette[1:])
    if unknown:
        print("render: no colour for " + ", ".join(unknown) + " -- drawn magenta")

    tint: dict[tuple[int, str], tuple[tuple[int, ...], tuple[int, ...]]] = {}

    def colour(value: int, kind: str):
        """Face colour and the colour of its own outline, cached per block kind."""
        key = (value, kind)
        hit = tint.get(key)
        if hit is None:
            r, g, b = blocklib.colour(palette[value])
            k = shade[kind]
            a = alpha[value]
            face = (min(255, int(r * k)), min(255, int(g * k)),
                    min(255, int(b * k)), a)
            # Drawn with a matching outline the quads seal; with a darker one the
            # individual blocks read. Either way the outline is needed, or
            # rounding leaves hairline gaps along every shared edge. A see-through
            # block keeps a firmer outline than its face, so a sheet of glass
            # still shows where its panes divide.
            edge = tuple(int(c * SEAM) for c in face[:3]) + (min(255, a + 70),) \
                if seams else (face[:3] + (min(255, a + 70),) if a < 255 else face)
            hit = (face, edge)
            tint[key] = hit
        return hit

    # Plan corners of a whole cell, in the frame. Shared by every layer above it,
    # and by every block that fills its cell edge to edge, which is nearly all.
    corners: dict[int, tuple] = {}

    def plan_corners(x: int, z: int):
        key = z * W + x
        hit = corners.get(key)
        if hit is None:
            hit = (
                to_local(x, z),
                to_local(x + 1, z),
                to_local(x + 1, z + 1),
                to_local(x, z + 1),
            )
            corners[key] = hit
        return hit

    def box_corners(x: int, z: int, bx0, bx1, bz0, bz1):
        if bx0 == 0.0 and bx1 == 1.0 and bz0 == 0.0 and bz1 == 1.0:
            return plan_corners(x, z)
        return (
            to_local(x + bx0, z + bz0),
            to_local(x + bx1, z + bz0),
            to_local(x + bx1, z + bz1),
            to_local(x + bx0, z + bz1),
        )

    edges = {"east": (1, 2), "west": (3, 0), "south": (2, 3), "north": (0, 1)}

    quads = []
    for i in filled:
        y, rest = divmod(i, area)
        z, x = divmod(rest, W)
        value = data[i]

        # A face is drawn wherever it can be seen: against air, against glass,
        # or against a block that does not fill its own cell. The inside of a
        # solid floor plate still costs nothing.
        neighbours = (
            ("up", data[i + area] if y + 1 < H else 0),
            ("down", data[i - area] if y else 0),
            ("east", data[i + 1] if x + 1 < W else 0),
            ("west", data[i - 1] if x else 0),
            ("south", data[i + W] if z + 1 < L else 0),
            ("north", data[i - W] if z else 0),
        )
        # Two cells of the same see-through block share one face; drawing both
        # doubles the alpha and a glass wall darkens at every joint.
        merge = value if not opaque[value] else -1
        covered = {
            kind: bool(n) and (hides[n] or n == merge)
            for kind, n in neighbours
        }

        for bx0, bx1, by0, by1, bz0, bz1 in shapes[value]:
            # A face only meets the neighbouring cell if the box reaches the
            # cell wall. A slab's top at 0.5 is always exposed, whatever sits
            # in the cell above it.
            at_wall = {
                "up": by1 >= 1.0, "down": by0 <= 0.0,
                "east": bx1 >= 1.0, "west": bx0 <= 0.0,
                "south": bz1 >= 1.0, "north": bz0 <= 0.0,
            }
            c = None
            for kind, _ in neighbours:
                if at_wall[kind] and covered[kind]:
                    continue
                # A parallel camera sees the same side of every face, so which
                # faces it can see is decided once per face kind. A perspective
                # one cannot: standing in the court it sees the inward side of
                # one wing and the outward side of nothing, and which side that
                # is depends on where the face is, so the test moves per face.
                if eye is None and not visible[kind]:
                    continue
                if c is None:
                    c = box_corners(x, z, bx0, bx1, bz0, bz1)
                if kind in ("up", "down"):
                    order = (0, 1, 2, 3) if kind == "up" else (3, 2, 1, 0)
                    h = y + (by1 if kind == "up" else by0)
                    points = [(c[j][0], c[j][1], h) for j in order]
                else:
                    a, b = edges[kind]
                    lo, hi = y + by0, y + by1
                    points = [
                        (c[a][0], c[a][1], lo),
                        (c[b][0], c[b][1], lo),
                        (c[b][0], c[b][1], hi),
                        (c[a][0], c[a][1], hi),
                    ]
                if eye is None:
                    screen = [
                        (p[0] * rx + p[1] * ry + p[2] * rz,
                         -(p[0] * ux + p[1] * uy + p[2] * uz),
                         p[0] * fx + p[1] * fy + p[2] * fz)
                        for p in points
                    ]
                    depth = sum(s[2] for s in screen) * 0.25
                    quads.append((depth, [(s[0], s[1]) for s in screen],
                                  colour(value, kind)))
                    continue

                offsets = [(p[0] - ex, p[1] - ey_, p[2] - ez) for p in points]
                n = normals[kind]
                d = offsets[0]
                if n[0] * d[0] + n[1] * d[1] + n[2] * d[2] >= 0.0:
                    continue
                camera = _clip_near([
                    (o[0] * rx + o[1] * ry + o[2] * rz,
                     o[0] * ux + o[1] * uy + o[2] * uz,
                     o[0] * fx + o[1] * fy + o[2] * fz)
                    for o in offsets
                ])
                if len(camera) < 3:
                    continue
                depth = sum(s[2] for s in camera) / len(camera)
                quads.append((
                    depth,
                    [(half_width + a / c2 * focal, half_height - b / c2 * focal)
                     for a, b, c2 in camera],
                    colour(value, kind),
                ))

    if eye is None:
        xs = [p[0] for _, poly, _ in quads for p in poly]
        ys = [p[1] for _, poly, _ in quads for p in poly]
        x0, y0 = min(xs), min(ys)
        width = int((max(xs) - x0) * scale) + 2 * margin + 1
        height = int((max(ys) - y0) * scale) + 2 * margin + 1
        place = lambda poly: [((px - x0) * scale + margin,  # noqa: E731
                               (py - y0) * scale + margin) for px, py in poly]
    else:
        # A perspective view cannot be fitted to its content the way a parallel
        # one is: the frame is the camera's, and cropping it to whatever happens
        # to be in shot would change the field of view after the fact.
        width, height = size
        place = lambda poly: poly  # noqa: E731

    image = Image.new("RGB", (width, height), background)
    # The RGBA context blends a translucent fill onto the opaque image as it
    # draws, so painter order does the compositing for free: back to front is
    # exactly the order alpha wants.
    draw = ImageDraw.Draw(image, "RGBA")
    quads.sort(key=lambda q: -q[0])
    for _, poly, (fill, edge) in quads:
        pts = place(poly)
        # Off-screen quads are common in perspective -- a wall a metre from the
        # lens projects to something the size of a city -- and PIL rasterises
        # the whole of one before clipping it.
        if (max(p[0] for p in pts) < 0 or min(p[0] for p in pts) > width
                or max(p[1] for p in pts) < 0 or min(p[1] for p in pts) > height):
            continue
        draw.polygon(pts, fill=fill, outline=edge)

    if path:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        image.save(path)
    return image


STANDARD = (
    View.iso(name="iso_near"),          # both wings, near side lit
    View.iso(145.0, 28.0, "iso_far"),   # the other flank and the far end
    View.orbit(35.0, 70.0, "iso_high"), # nearly a plan, but the walls still show
    View.long(False),
    View.end(False),
    View.plan(),
)


def render_set(model, out_dir: str | Path, frame=None, scale: float = 6.0,
               views=STANDARD, **kwargs) -> dict[str, Path]:
    """The standard sheet: two obliques, a raking view, an elevation, an end, a plan."""
    out = Path(out_dir)
    made = {}
    for view in views:
        path = out / f"{view.name}.png"
        render(model, path, view=view, frame=frame, scale=scale, **kwargs)
        made[view.name] = path
    return made
