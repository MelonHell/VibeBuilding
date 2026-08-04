"""Close crops of one elevation: `python -m tools.gta_style.zoom.py <id> <axis> [scale]`."""

from __future__ import annotations

import json
import sys


from tools.gta_style import views
from tools.gta_style.look import box_for                                  # noqa: E402


def main():
    bid = int(sys.argv[1])
    axis = sys.argv[2] if len(sys.argv) > 2 else "-z"
    scale = int(sys.argv[3]) if len(sys.argv) > 3 else 5
    rec = {r["id"]: r for r in json.load(open("tmp/gta/buildings.json"))}[bid]
    box = box_for(rec)
    img = views.elevation(box, axis, 1)
    w, h = img.size

    # Base, a middle band and the crown, each a window of the same height.
    band = min(h, max(40, h // 4))
    cuts = {"base": (h - band, h),
            "mid": ((h - band) // 2, (h - band) // 2 + band),
            "top": (0, band)}
    for name, (y0, y1) in cuts.items():
        crop = img.crop((0, y0, w, y1))
        crop = crop.resize((crop.width * scale, crop.height * scale),
                           views.Image.NEAREST)
        path = f"tmp/gta/z{bid}_{axis.replace('+', 'p').replace('-', 'm')}" \
               f"_{name}.png"
        crop.save(path)
        print(path, crop.size)


if __name__ == "__main__":
    main()
