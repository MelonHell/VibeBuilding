"""Helpers shared by the GTA-map analysis scripts.

blockwright's own COLORS table is a curated eighty blocks -- enough for the
palettes we choose, not enough to draw somebody else's world. What is here is a
colour for anything: the sixteen dyes crossed with the dyed families, a
materials table, and a fallback that strips the obvious prefixes and suffixes.
"""

from __future__ import annotations

import numpy as np

from blockwright import blocks

WORLD = (r"D:/Games/Minecraft/PrismLauncher/instances/"
         r"SkolkovoDev 1.21.11/.minecraft/saves/GTA_5_cut_2")

DYES = {
    "white": (233, 236, 236), "orange": (240, 118, 19),
    "magenta": (189, 68, 179), "light_blue": (58, 175, 217),
    "yellow": (248, 198, 39), "lime": (112, 185, 25),
    "pink": (237, 141, 172), "gray": (62, 68, 71),
    "light_gray": (142, 142, 134), "cyan": (21, 137, 145),
    "purple": (121, 42, 172), "blue": (53, 57, 157),
    "brown": (114, 71, 40), "green": (84, 109, 27),
    "red": (160, 39, 34), "black": (20, 21, 25),
}
TERRACOTTA = {
    "white": (209, 178, 161), "orange": (161, 83, 37),
    "magenta": (149, 88, 108), "light_blue": (113, 108, 137),
    "yellow": (186, 133, 35), "lime": (103, 117, 52),
    "pink": (161, 78, 78), "gray": (57, 42, 35),
    "light_gray": (135, 106, 97), "cyan": (86, 91, 91),
    "purple": (118, 70, 86), "blue": (74, 59, 91),
    "brown": (77, 51, 35), "green": (76, 83, 42),
    "red": (143, 61, 46), "black": (37, 22, 16),
}
DYED_FAMILIES = ("wool", "carpet", "concrete", "concrete_powder",
                 "stained_glass", "glazed_terracotta", "shulker_box",
                 "bed", "banner", "candle")

MATERIALS = {
    "stone": (125, 125, 125), "smooth_stone": (159, 159, 159),
    "stone_bricks": (122, 121, 122), "cobblestone": (127, 127, 127),
    "andesite": (136, 136, 137), "diorite": (188, 188, 189),
    "granite": (149, 103, 85), "deepslate": (77, 77, 80),
    "blackstone": (42, 35, 40), "basalt": (80, 79, 85),
    "tuff": (108, 109, 102), "calcite": (223, 224, 219),
    "sandstone": (216, 203, 155), "red_sandstone": (186, 99, 29),
    "quartz": (236, 233, 226), "quartz_block": (236, 233, 226),
    "bricks": (150, 97, 83), "nether_bricks": (44, 22, 26),
    "prismarine": (99, 156, 151), "prismarine_bricks": (99, 171, 158),
    "dark_prismarine": (52, 89, 72), "purpur": (169, 125, 169),
    "end_stone": (219, 222, 158), "terracotta": (152, 94, 67),
    "copper": (192, 107, 79), "iron": (220, 220, 220),
    "gold": (246, 208, 61), "glass": (200, 226, 235),
    "dirt": (134, 96, 67), "coarse_dirt": (119, 85, 59),
    "grass": (95, 141, 60), "sand": (219, 207, 163),
    "gravel": (131, 127, 126), "clay": (160, 166, 179),
    "mud": (60, 51, 53), "snow": (249, 254, 254),
    "ice": (145, 183, 253), "water": (60, 90, 200),
    "lava": (207, 92, 10), "rail": (124, 111, 87),
    "oak": (162, 130, 78), "birch": (192, 175, 121),
    "spruce": (114, 84, 48), "jungle": (160, 115, 80),
    "acacia": (168, 90, 50), "dark_oak": (66, 43, 20),
    "mangrove": (117, 54, 48), "cherry": (226, 177, 172),
    "bamboo": (194, 175, 74), "crimson": (101, 48, 70),
    "warped": (43, 104, 99), "leaves": (60, 143, 40),
    "log": (102, 81, 50), "planks": (162, 130, 78),
    "lapis": (32, 67, 181), "coal": (16, 16, 16),
    "emerald": (42, 203, 88), "diamond": (98, 219, 214),
    "redstone": (171, 26, 9), "netherite": (66, 60, 62),
    "amethyst": (135, 106, 195), "honey": (251, 184, 48),
    "slime": (111, 192, 91), "podzol": (91, 65, 30),
    "moss": (89, 109, 45), "sponge": (195, 192, 75),
    "bone": (229, 226, 208), "obsidian": (15, 10, 24),
    "netherrack": (97, 38, 38), "soul": (74, 57, 45),
    "mycelium": (111, 98, 100), "magma": (142, 65, 30),
    "target": (219, 175, 158), "note": (87, 55, 35),
    "cauldron": (73, 73, 73), "hopper": (55, 55, 55),
    "furnace": (104, 104, 104), "brewing": (124, 103, 81),
    "shroomlight": (240, 146, 70), "sea_lantern": (172, 199, 190),
    "glowstone": (171, 131, 84), "froglight": (227, 233, 214),
}

_SUFFIX = ("_slab", "_stairs", "_wall", "_fence_gate", "_fence", "_carpet",
           "_button", "_pressure_plate", "_trapdoor", "_door", "_pane",
           "_pillar", "_bricks", "_brick", "_block", "_wood", "_log",
           "_leaves", "_planks", "_sign", "_sapling")
_PREFIX = ("smooth_", "polished_", "cut_", "chiseled_", "infested_",
           "cracked_", "mossy_", "waxed_", "exposed_", "weathered_",
           "oxidized_", "stripped_")


def _direct(stem: str):
    full = "minecraft:" + stem
    hit = blocks.COLORS.get(full)
    if hit:
        return hit
    if stem in MATERIALS:
        return MATERIALS[stem]
    if stem.endswith("_terracotta"):
        got = TERRACOTTA.get(stem[:-len("_terracotta")])
        if got:
            return got
    for fam in DYED_FAMILIES:
        if stem.endswith("_" + fam):
            got = DYES.get(stem[:-len(fam) - 1])
            if got:
                return got
    for dye in DYES:
        if stem.startswith(dye + "_"):
            return DYES[dye]
    return None


def colour(block: str) -> tuple[int, int, int]:
    """A colour for any block, falling back through obvious families."""
    stem = blocks.base(block)[len("minecraft:"):]
    seen, queue = set(), [stem]
    while queue:
        cur = queue.pop(0)
        if cur in seen or not cur:
            continue
        seen.add(cur)
        hit = _direct(cur)
        if hit:
            return hit
        for suf in _SUFFIX:
            if cur.endswith(suf) and len(cur) > len(suf):
                queue.append(cur[:-len(suf)])
        for pre in _PREFIX:
            if cur.startswith(pre):
                queue.append(cur[len(pre):])
        for word in ("stone", "wood", "glass", "metal"):
            if word in cur:
                queue.append(word)
    return blocks.UNKNOWN


def palette_lut(pal: list[str]) -> np.ndarray:
    lut = np.zeros((len(pal), 3), np.uint8)
    for i, b in enumerate(pal):
        lut[i] = colour(b)
    return lut


def unresolved(pal: list[str]) -> list[str]:
    return [b for b in pal if colour(b) == blocks.UNKNOWN]
