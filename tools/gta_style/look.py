"""Render a sheet for one segmented building: `python -m tools.gta_style.look.py <id>`.

The box is masked down to the component's own footprint first. Downtown blocks
sit shoulder to shoulder, so without that the elevation is a picture of the
neighbour's back wall as often as of the building asked for.
"""

from __future__ import annotations

import json
import sys

import numpy as np


from tools.gta_style import shared
from tools.gta_style import views
from tools.world_read import World

GROUND = 33
X0, Z0 = 5120, 8192


def box_for(rec, margin=3, ytop_pad=6, isolate=True, world=None):
    w = world or World(shared.WORLD)
    x0, z0 = rec["x0"] - margin, rec["z0"] - margin
    x1, z1 = rec["x0"] + rec["w"] + margin, rec["z0"] + rec["d"] + margin
    box = w.load(x0, z0, x1, z1, GROUND - 6, rec["top"] + ytop_pad)
    if isolate:
        lab = np.load("tmp/gta/labels.npy", mmap_mode="r")
        # The label raster only covers the generated regions; a building on the
        # world edge asks for a window that runs off it. Clip, then pad back.
        ax0, az0 = max(0, x0 - X0), max(0, z0 - Z0)
        ax1 = min(lab.shape[0], x1 - X0)
        az1 = min(lab.shape[1], z1 - Z0)
        own = np.zeros((x1 - x0, z1 - z0), bool)
        if ax0 < ax1 and az0 < az1:
            own[ax0 - (x0 - X0):ax1 - (x0 - X0),
                az0 - (z0 - Z0):az1 - (z0 - Z0)] = (
                    np.asarray(lab[ax0:ax1, az0:az1]) == rec["id"])
        # Keep the ground plane so the building is not floating, drop the rest.
        keep = own[:, None, :] | (
            np.arange(box.blocks.shape[1])[None, :, None] < GROUND - box.origin[1])
        box.blocks = np.where(keep, box.blocks, 0)
    return box


def main():
    rows = {r["id"]: r for r in json.load(open("tmp/gta/buildings.json"))}
    world = World(shared.WORLD)
    for arg in sys.argv[1:]:
        bid = int(arg)
        rec = rows[bid]
        scale = 2 if max(rec["w"], rec["d"]) < 80 else 1
        box = box_for(rec, world=world)
        path = f"tmp/gta/b{bid}.png"
        views.sheet(box, path, scale)
        print(bid, rec["x0"], rec["z0"], rec["w"], rec["d"],
              "tall", rec["tall"], "->", path)


if __name__ == "__main__":
    main()
