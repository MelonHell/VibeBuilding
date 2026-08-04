"""What a block looks like: its colour, whether it hides things, what it fills.

One table, read by every part of the pipeline that has to draw or reason about a
block. There used to be two -- `schem.PREVIEW_COLORS` with a hash fallback and
`render._colour` with a grey one -- which meant the same unknown block came out
two different plausible colours and neither of them announced itself. A cone once
rendered in wall grey and stayed invisible for a whole iteration.

So: **an unknown block is magenta**. There is no safe default colour. A colour
that looks like architecture is worse than no colour at all, because it is
indistinguishable from a correct one.

The hash fallback that magenta replaces was worse still: Python salts string
hashes per process, so the same block came out a different pastel on every run
and nothing could be compared between two renders.

What is asked of a block here:

    colour(block)     what to draw it in
    occludes(block)   does it hide what is behind it -- glass does not
    boxes(block)      the sub-cells it actually fills, in cell-local 0..1
    connects(block)   does it reach sideways for its neighbours, and as what
    joins(fam, other) would a block of that family take hold of this neighbour
    with_state(...)   the same block with some of its state replaced

`boxes` is what keeps the renderer honest once non-cube blocks enter the build.
A slab that draws as a full cube reads half a metre too tall; a glass pane that
draws as a full cube turns a balustrade into a parapet, which is exactly the
"thirteen villas read as one bar" failure the renderer exists to catch. Every
shape here is a list of axis-aligned boxes because that is what the renderer can
draw exactly, and an approximation that is honest about its silhouette is worth
far more than a faithful one that arrives later.
"""

from __future__ import annotations

import math

AIR = "minecraft:air"

UNKNOWN = (255, 0, 255)

# Block string -> (r, g, b). Base names only; states are stripped before lookup.
COLORS: dict[str, tuple[int, int, int]] = {
    AIR: (24, 26, 30),
    "minecraft:white_concrete": (207, 213, 214),
    "minecraft:light_gray_concrete": (125, 125, 115),
    "minecraft:gray_concrete": (54, 57, 61),
    "minecraft:black_concrete": (8, 10, 15),
    "minecraft:light_gray_concrete_powder": (154, 161, 161),
    "minecraft:white_concrete_powder": (225, 229, 230),
    "minecraft:gray_concrete_powder": (76, 81, 84),
    "minecraft:blue_concrete": (44, 46, 143),
    "minecraft:red_concrete": (142, 32, 32),
    "minecraft:yellow_concrete": (240, 175, 21),
    "minecraft:lime_concrete": (94, 168, 24),
    "minecraft:green_concrete": (73, 91, 36),
    "minecraft:cyan_concrete": (21, 119, 136),
    # Added after three buildings each hit a hole in this table and each wrote
    # down the compromise it settled for: a pale blue block with no match at
    # all, a brass drum built in yellow, a green glass facade built in blue.
    # An unknown block draws magenta on purpose, and that rule only works while
    # the unknowns are genuinely rare.
    "minecraft:brown_concrete": (96, 60, 32),
    "minecraft:light_blue_concrete": (36, 137, 199),
    "minecraft:magenta_concrete": (169, 48, 159),
    "minecraft:purple_concrete": (100, 32, 156),
    "minecraft:pink_concrete": (213, 101, 143),
    "minecraft:orange_concrete": (224, 97, 0),
    "minecraft:brown_terracotta": (77, 51, 35),
    "minecraft:white_terracotta": (209, 178, 161),
    "minecraft:light_gray_terracotta": (135, 106, 97),
    "minecraft:cyan_terracotta": (86, 91, 91),
    "minecraft:light_blue_terracotta": (113, 108, 137),
    "minecraft:tuff": (108, 109, 102),
    "minecraft:deepslate": (77, 77, 80),
    "minecraft:copper_block": (192, 107, 79),
    "minecraft:waxed_copper_block": (192, 107, 79),
    "minecraft:weathered_copper": (108, 153, 128),
    "minecraft:green_stained_glass": (84, 109, 27),
    "minecraft:lime_stained_glass": (128, 199, 31),
    "minecraft:cyan_stained_glass": (76, 127, 153),
    "minecraft:light_blue_stained_glass_pane": (102, 153, 216),
    "minecraft:red_sand": (190, 102, 33),
    "minecraft:clay": (160, 166, 179),
    "minecraft:mud": (60, 55, 60),
    "minecraft:packed_mud": (142, 106, 79),
    "minecraft:mud_bricks": (137, 103, 78),
    "minecraft:orange_concrete": (224, 97, 0),
    "minecraft:stone": (125, 125, 125),
    "minecraft:water": (60, 90, 200),
    "minecraft:smooth_quartz": (236, 233, 226),
    "minecraft:quartz_block": (236, 233, 226),
    "minecraft:calcite": (223, 225, 220),
    "minecraft:diorite": (189, 189, 185),
    "minecraft:polished_diorite": (192, 192, 189),
    "minecraft:light_blue_stained_glass": (108, 173, 217),
    "minecraft:gray_stained_glass": (76, 81, 84),
    "minecraft:white_stained_glass": (240, 242, 245),
    "minecraft:glass": (200, 225, 235),
    "minecraft:sand": (219, 207, 163),
    "minecraft:sandstone": (216, 203, 155),
    "minecraft:smooth_sandstone": (224, 214, 173),
    "minecraft:gravel": (131, 127, 126),
    "minecraft:deepslate": (77, 77, 80),
    "minecraft:polished_deepslate": (72, 72, 75),
    "minecraft:cobbled_deepslate": (78, 76, 82),
    "minecraft:oxidized_copper": (82, 162, 132),
    "minecraft:weathered_copper": (109, 154, 118),
    "minecraft:moss_block": (89, 109, 45),
    "minecraft:grass_block": (95, 141, 60),
    "minecraft:jungle_leaves": (48, 110, 20),
    "minecraft:oak_leaves": (60, 120, 36),
    "minecraft:jungle_log": (86, 67, 30),
    "minecraft:oak_log": (108, 87, 51),
    "minecraft:terracotta": (152, 94, 67),
    "minecraft:polished_andesite": (132, 134, 132),
    "minecraft:oak_planks": (162, 130, 78),
    "minecraft:jungle_planks": (160, 115, 80),
    "minecraft:spruce_planks": (114, 84, 48),
    "minecraft:dark_oak_planks": (66, 43, 20),
    "minecraft:bamboo_planks": (194, 167, 84),
    "minecraft:stone_bricks": (122, 121, 122),
    "minecraft:brick": (150, 97, 83),
    "minecraft:bricks": (150, 97, 83),
    "minecraft:andesite": (136, 136, 137),
    "minecraft:cobblestone": (127, 127, 127),
    "minecraft:iron_bars": (150, 152, 155),
    "minecraft:chain": (100, 104, 112),
    # The rest of the grey stone family, and the dark glazing one. Added
    # together because the survey in `docs/gta5-style-findings.md` is what named
    # them: four of the ten colours covering the most surface on that city were
    # blocks this table had never heard of, so a recipe reaching for them got a
    # magenta build and a failed `checks.palette` rather than a material. The
    # greys matter twice over -- they are also where the dither pairs live, and
    # `smooth_stone` at 159 is the one partner `light_gray_concrete` had no
    # answer for.
    "minecraft:smooth_stone": (159, 159, 159),
    "minecraft:granite": (149, 103, 85),
    "minecraft:polished_granite": (154, 107, 89),
    "minecraft:cut_sandstone": (216, 203, 155),
    "minecraft:chiseled_sandstone": (214, 202, 157),
    "minecraft:red_sandstone": (166, 82, 26),
    # Glazing that is not glass. Measured on that map as the way a window reads
    # from any distance: an opaque dark plane rather than a hole with the sky
    # behind it. Kept here as material, not as policy -- what a given building's
    # windows are made of is what its own photographs say.
    "minecraft:black_stained_glass": (25, 25, 25),
    "minecraft:black_wool": (21, 21, 26),
    "minecraft:gray_wool": (62, 68, 71),
    "minecraft:light_gray_wool": (142, 142, 134),
    "minecraft:white_wool": (234, 236, 237),
    "minecraft:dark_prismarine": (52, 90, 74),
    "minecraft:yellow_terracotta": (186, 133, 35),
    "minecraft:lime_terracotta": (103, 117, 52),
    # Loose planting. These are the map colour of the plant itself, not of the
    # block below it, because the renderer draws the cross shape rather than
    # tinting the ground -- so a tuft reads as a tuft against whatever it is
    # standing on.
    "minecraft:short_grass": (94, 145, 60),
    "minecraft:tall_grass": (94, 145, 60),
    "minecraft:fern": (86, 137, 55),
}

# What a variant borrows its colour from. Saves restating a palette three times
# over once slabs and stairs arrive: smooth_quartz_slab is smooth_quartz.
# A fence or a door is made of planks, not of the log it is named after, so wood
# variants get a second candidate.
# Two more candidates than the obvious one, each for a family whose whole block
# is not named after its cut. `quartz_slab` is cut from `quartz_block`, and
# `stone_brick_slab` from `stone_bricks` -- plural. Without them both came out
# magenta while their parents sat in the table, which is the failure mode this
# derivation exists to prevent, and both are ordinary paving on any grey deck.
_DERIVED = {
    "_slab": ("", "_block", "s", "_planks"),
    "_stairs": ("", "_block", "s", "_planks"),
    "_wall": ("", "_block", "s", "_planks"),
    "_fence": ("_planks", ""),
    "_fence_gate": ("_planks", ""),
    "_pane": ("",),
    "_trapdoor": ("_planks", ""),
    "_door": ("_planks", ""),
    "_button": ("_planks", ""),
    "_pressure_plate": ("_planks", ""),
    "_carpet": ("_wool", ""),
}

# How much of the light a block lets past, 0 opaque. Only the ones that are not
# opaque need saying.
TRANSPARENCY: dict[str, float] = {
    "minecraft:glass": 0.72,
    "minecraft:glass_pane": 0.72,
    "minecraft:tinted_glass": 0.35,
    "minecraft:water": 0.45,
    "minecraft:ice": 0.55,
    "minecraft:jungle_leaves": 0.30,
    "minecraft:oak_leaves": 0.30,
    "minecraft:iron_bars": 0.60,
    "minecraft:chain": 0.60,
    "minecraft:scaffolding": 0.50,
}

_GLASS_FAMILIES = ("_stained_glass", "_stained_glass_pane")


def base(block: str) -> str:
    """The block name with its state stripped: 'oak_slab[type=top]' -> 'oak_slab'."""
    return block.split("[", 1)[0]


def state(block: str) -> dict[str, str]:
    """The block's state as a dict. Empty for a plain block."""
    if "[" not in block:
        return {}
    inner = block.split("[", 1)[1].rstrip("]")
    out = {}
    for pair in inner.split(","):
        if "=" in pair:
            key, value = pair.split("=", 1)
            out[key.strip()] = value.strip()
    return out


def with_state(block: str, **values) -> str:
    """The same block with these state values set, replacing any already there.

    Keys come out sorted, so two routes to the same block produce the same string
    and therefore the same palette entry. Without that a canvas can end up holding
    `oak_fence[east=true,north=true]` and `oak_fence[north=true,east=true]` as two
    different blocks, which is invisible in a render and doubles the palette.
    """
    st = state(block)
    st.update({k: str(v) for k, v in values.items()})
    if not st:
        return base(block)
    return f"{base(block)}[{','.join(f'{k}={st[k]}' for k in sorted(st))}]"


def colour(block: str) -> tuple[int, int, int]:
    """What to draw it in. Magenta if we have never heard of it."""
    name = base(block)
    hit = COLORS.get(name)
    if hit is not None:
        return hit
    for suffix, candidates in _DERIVED.items():
        if not name.endswith(suffix):
            continue
        stem = name[: -len(suffix)]
        for tail in candidates:
            parent = COLORS.get(stem + tail)
            if parent is not None:
                return parent
    return UNKNOWN


def nearest(rgb: tuple[int, int, int], count: int = 5,
            solid: bool = True) -> list[tuple[str, float]]:
    """The closest blocks to a colour, with their distances.

    For answering a review finding about colour with a measurement instead of an
    opinion. "There is no block at this hue" was written down twice as a reason
    to leave a building the wrong colour, both times without a single candidate
    named -- and one of those buildings is pale ice blue in its own capture, so
    two independent references agreed and neither was consulted.

    Distance is in a cylinder around the grey axis: hue and saturation are
    weighted above lightness, because lightness is the axis a render's own
    lighting moves anyway, and a block of the right hue two shades dark reads as
    the building where a block of the right lightness and no hue does not. That
    is the same argument one building made by hand when it took
    `oxidized_copper` ninety levels darker than the real thing rather than
    settle for grey.

    `solid` keeps out the glass, the leaves and everything else whose colour on
    screen is mostly whatever stands behind it -- a pane the exact hue of the
    wall is not an answer to "what should this wall be made of".
    """
    import colorsys

    want = colorsys.rgb_to_hls(*(v / 255 for v in rgb))
    out = []
    for block, colour in COLORS.items():
        if solid and (not is_cube(block) or transparency(block) > 0.05):
            continue
        h, l, s = colorsys.rgb_to_hls(*(v / 255 for v in colour))
        turn = abs(h - want[0])
        turn = min(turn, 1.0 - turn) * 2.0        # 0..1 round the wheel
        # Hue only means anything on a colour that has some. Between two greys
        # the whole distance is lightness, which is why the weight rides on
        # saturation rather than being a constant.
        weight = min(s, want[2])
        far = ((turn * 2.0 * weight) ** 2
               + (s - want[2]) ** 2
               + ((l - want[1]) * 0.6) ** 2) ** 0.5
        out.append((block, round(far, 3)))
    out.sort(key=lambda row: row[1])
    return out[:count]


def why_not(rgb: tuple[int, int, int], count: int = 5) -> list[str]:
    """`nearest`, as the lines a rejected colour finding has to carry.

    A finding closed with "no block matches" is an assertion; the same finding
    closed with five named blocks and their distances is a measurement, and the
    next round can argue with it.
    """
    found = nearest(rgb, count)
    out = [f"  nothing in the palette is exactly rgb{rgb}; the nearest are:"]
    for block, far in found:
        out.append(f"    {far:5.3f}  {block:38s} rgb{COLORS[block]}")
    out.append("  Take the closest in HUE even when it is darker: a colour of "
               "the right hue and the wrong value reads as the building, and a "
               "grey of the right value does not.")
    return out


# The measured band. On the GTA V map the pair carrying three fifths of all the
# dithered surface sits at 19.6 RGB units apart; the runner-up recipe uses a pair
# at 185 and reads as speckle rather than as an even tone, and a pair at 6
# (`diorite` + `polished_diorite`) is a third thing again, invisible at any
# distance. So the distance is the style parameter, and these are the edges of
# the one that was measured rather than a tolerance around a preference.
DITHER_NEAR = 8.0
DITHER_FAR = 32.0


def apart(a: str, b: str) -> float:
    """Plain RGB distance between two blocks.

    Deliberately not `nearest`'s metric. That one weights hue over lightness to
    answer "what should this wall be made of", where this one answers "how far
    apart are these two", and between two greys -- which is the whole of the
    measured recipe -- the hue weighting collapses to nearly nothing and would
    call a pair 60 units apart identical.
    """
    return math.dist(colour(a), colour(b))


def pairs(block: str, near: float = DITHER_NEAR, far: float = DITHER_FAR,
          count: int = 8) -> list[tuple[str, float]]:
    """Blocks that would dither with this one, nearest first.

    Proposes; does not choose. Colour distance is the only thing this can see,
    and it is not the only thing that matters: `brown_concrete` and `jungle_log`
    are twelve units apart and no roof is made of both. Two blocks belong in a
    pair when they agree on material as well as on tone, and nothing in this
    table knows what a block is made of -- so the recipe names the pair, the way
    it names every other palette decision, and this prints the candidates it is
    choosing between.
    """
    out = []
    for other in COLORS:
        if base(other) == base(block):
            continue
        if not is_cube(other) or transparency(other) > 0.05:
            continue
        far_off = apart(block, other)
        if near <= far_off <= far:
            out.append((other, round(far_off, 1)))
    out.sort(key=lambda row: row[1])
    return out[:count]


def why_pair(block: str, count: int = 8) -> list[str]:
    """`pairs`, as lines -- for a build script's comment or a review answer."""
    found = pairs(block, count=count)
    out = [f"  dither partners for {block} rgb{colour(block)}, "
           f"{DITHER_NEAR:.0f}-{DITHER_FAR:.0f} RGB apart "
           f"(the measured band is ~20):"]
    for other, far_off in found:
        out.append(f"    {far_off:5.1f}  {other:38s} rgb{colour(other)}")
    if not found:
        out.append("    nothing in the palette is that close; either widen the "
                   "band on purpose or leave the surface plain.")
    out.append("  Pick the one that is the same *material*, not just the same "
               "distance: colour is all this table can see.")
    return out


def known(block: str) -> bool:
    return colour(block) is not UNKNOWN


def unknown_blocks(palette) -> list[str]:
    """Every palette entry that would render magenta. Print this, loudly."""
    return sorted({base(b) for b in palette if not known(b)})


def transparency(block: str) -> float:
    """0 for an opaque block, up to 1 for one you can see straight through."""
    name = base(block)
    hit = TRANSPARENCY.get(name)
    if hit is not None:
        return hit
    for family in _GLASS_FAMILIES:
        if name.endswith(family):
            return 0.72
    if name.endswith("_leaves"):
        return 0.30
    return 0.0


def occludes(block: str) -> bool:
    """Does this block hide what is behind it.

    Used for face culling. A face against glass must still be drawn, or the
    structure under a glazed canopy disappears -- and checking that structure is
    the whole reason the canopy gets rendered.
    """
    return transparency(block) < 0.05


# -- shape ----------------------------------------------------------------
#
# A box is (x0, x1, y0, y1, z0, z1) in cell-local coordinates, 0..1, with y up
# and x/z on the world grid. A full cube is the unit box; everything else is
# some subset of it. Approximate where it has to be, but never larger than the
# real block, because the silhouette is the thing being checked.

CUBE = (0.0, 1.0, 0.0, 1.0, 0.0, 1.0)
_FULL = [CUBE]

_THIN = 0.125           # panes, iron bars
_POST = 0.25            # fences, walls
_FENCE_TOP = 1.5        # a fence is taller than its cell; clipped to it here

_FACING_ARM = {
    # which half of the cell an arm reaches into, per connection direction
    "north": (0.5 - _THIN / 2, 0.5 + _THIN / 2, 0.0, 0.5),
    "south": (0.5 - _THIN / 2, 0.5 + _THIN / 2, 0.5, 1.0),
    "west": (0.0, 0.5, 0.5 - _THIN / 2, 0.5 + _THIN / 2),
    "east": (0.5, 1.0, 0.5 - _THIN / 2, 0.5 + _THIN / 2),
}

# Stairs, approximated as a bottom half plus a quarter riser on the back side.
# The facing state names the direction the *front* faces, so the riser is
# opposite it.
_RISER = {
    "north": (0.0, 1.0, 0.5, 1.0),
    "south": (0.0, 1.0, 0.0, 0.5),
    "west": (0.5, 1.0, 0.0, 1.0),
    "east": (0.0, 0.5, 0.0, 1.0),
}
_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def opposite(direction: str) -> str:
    """The other way. Public because call sites need it constantly.

    A stair's `facing` names the side its full-height part stands on, not the
    side its tread looks towards -- so a cornice shedding outwards faces *in*,
    and a step up onto a terrace faces in as well. Getting this backwards builds
    a parapet with a chamfer on the wrong side, which is a thing that reads as
    deliberate architecture in a render and is only caught by knowing the rule.
    """
    return _OPPOSITE[direction]


def boxes(block: str) -> list[tuple[float, float, float, float, float, float]]:
    """The sub-cells this block actually fills."""
    name = base(block)
    st = state(block)

    if name == AIR:
        # Nothing, which is not the same as everything. The renderer guards air
        # separately, but `is_cube(AIR)` answering yes would tell `finalize` that
        # every pane in open air has a solid block beside it to connect to.
        return []

    if name.endswith("_slab"):
        kind = st.get("type", "bottom")
        if kind == "double":
            return _FULL
        return [(0.0, 1.0, 0.5, 1.0, 0.0, 1.0) if kind == "top"
                else (0.0, 1.0, 0.0, 0.5, 0.0, 1.0)]

    if name.endswith("_stairs"):
        facing = st.get("facing", "north")
        half = st.get("half", "bottom")
        low, high = (0.0, 0.5) if half == "bottom" else (0.5, 1.0)
        step_low, step_high = (0.5, 1.0) if half == "bottom" else (0.0, 0.5)
        x0, x1, z0, z1 = _RISER[_OPPOSITE.get(facing, "south")]
        return [
            (0.0, 1.0, low, high, 0.0, 1.0),
            (x0, x1, step_low, step_high, z0, z1),
        ]

    if name.endswith("_pane") or name in ("minecraft:iron_bars",):
        return _connected(block, _THIN, 0.0, 1.0)

    if name.endswith("_fence") or name.endswith("_wall"):
        return _connected(block, _POST, 0.0, 1.0)

    if name.endswith("_fence_gate"):
        # A gate has `facing` and `open` rather than four sides, so it is not a
        # `_connected` block: closed it stands across the opening as one rail,
        # open it has swung back against the two jambs. `facing` names the way
        # the front looks, so a north-facing gate spans x and is thin in z.
        facing = st.get("facing", "north")
        lo, hi = 0.5 - _POST / 2, 0.5 + _POST / 2
        spans_x = facing in ("north", "south")
        if st.get("open") != "true":
            return [(0.0, 1.0, 0.0, 1.0, lo, hi) if spans_x
                    else (lo, hi, 0.0, 1.0, 0.0, 1.0)]
        if spans_x:
            return [(0.0, _POST, 0.0, 1.0, lo, hi),
                    (1.0 - _POST, 1.0, 0.0, 1.0, lo, hi)]
        return [(lo, hi, 0.0, 1.0, 0.0, _POST),
                (lo, hi, 0.0, 1.0, 1.0 - _POST, 1.0)]

    if name.endswith("_trapdoor"):
        if st.get("open") == "true":
            facing = st.get("facing", "north")
            x0, x1, z0, z1 = _FACING_ARM[facing][0:2] + _FACING_ARM[facing][2:4]
            return [_arm_box(facing, 0.1875, 0.0, 1.0)]
        low, high = (0.0, 0.1875) if st.get("half", "bottom") == "bottom" else (0.8125, 1.0)
        return [(0.0, 1.0, low, high, 0.0, 1.0)]

    if name.endswith("_carpet") or name == "minecraft:snow":
        return [(0.0, 1.0, 0.0, 0.0625, 0.0, 1.0)]

    if name.endswith("_pressure_plate"):
        return [(0.0625, 0.9375, 0.0, 0.0625, 0.0625, 0.9375)]

    if name in ("minecraft:grass", "minecraft:short_grass", "minecraft:tall_grass",
                "minecraft:fern", "minecraft:large_fern"):
        # A cross-shaped plant. Drawn as a thin upright slice so it reads as
        # planting without claiming a cube of silhouette.
        return [(0.5 - _THIN, 0.5 + _THIN, 0.0, 0.8, 0.0, 1.0),
                (0.0, 1.0, 0.0, 0.8, 0.5 - _THIN, 0.5 + _THIN)]

    return _FULL


def _arm_box(direction: str, thickness: float, y0: float, y1: float):
    a0, a1, b0, b1 = _FACING_ARM[direction]
    if direction in ("north", "south"):
        return (0.5 - thickness / 2, 0.5 + thickness / 2, y0, y1, b0, b1)
    return (a0, a1, y0, y1, 0.5 - thickness / 2, 0.5 + thickness / 2)


def _connected(block: str, thickness: float, y0: float, y1: float):
    """A post in the middle of the cell plus an arm to each connected side.

    A pane written with the default state -- every side false -- really is a
    lone post in the game, which is what WorldEdit pastes when it skips block
    updates. Drawing it that way is not a shortcoming of the renderer; it is the
    renderer telling the truth about a build that forgot to run `finalize`.
    """
    st = state(block)
    half = thickness / 2
    out = [(0.5 - half, 0.5 + half, y0, y1, 0.5 - half, 0.5 + half)]
    for direction in ("north", "south", "east", "west"):
        # Panes and fences say true/false; walls have said none/low/tall since
        # 1.16. Reading "anything that is not absent" covers both without the
        # renderer having to know which family it is looking at.
        if st.get(direction, "false") not in ("false", "none"):
            out.append(_arm_box(direction, thickness, y0, y1))
    return out


def is_cube(block: str) -> bool:
    """Does this block fill its cell exactly. The fast path for culling."""
    b = boxes(block)
    return len(b) == 1 and b[0] == CUBE


# -- connections ----------------------------------------------------------
#
# Which blocks reach sideways for their neighbours, and what they will take
# hold of. Written here rather than beside the code that walks the canvas,
# because it is a fact about blocks and the walk is a fact about builds.

_BARS = {"minecraft:iron_bars"}


def connects(block: str) -> str | None:
    """Which connecting family this block belongs to, or None if it has none.

    Fence gates are deliberately absent. They have `facing` and `open` rather
    than four sides, so nothing should be rewriting their state -- but a fence
    beside one does connect to it, which `joins` handles.
    """
    name = base(block)
    if name.endswith("_pane") or name in _BARS:
        return "pane"
    if name.endswith("_fence"):
        return "fence"
    if name.endswith("_wall"):
        return "wall"
    return None


def joins(family: str, neighbour: str) -> bool:
    """Would a block of `family` reach sideways for this neighbour.

    The vanilla rule is "its own kind, or a block with a solid face on that
    side"; the approximation here is "its own kind, or a full cube". It errs
    towards not connecting -- a pane against a slab stays a post, which reads as
    a gap rather than as a rail that is not there.
    """
    name = base(neighbour)
    if name == AIR:
        return False
    kin = connects(neighbour)
    if family == "pane":
        if kin == "pane":
            return True
    elif family == "fence":
        if kin == "fence" or name.endswith("_fence_gate"):
            return True
    elif family == "wall":
        if kin == "wall" or name.endswith("_fence_gate"):
            return True
    return is_cube(neighbour)
