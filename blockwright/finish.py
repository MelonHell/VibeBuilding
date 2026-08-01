"""The dozen steps every build script ends with.

A `build.py` is a recipe: parts, constants with the reason each one has its
value, and the geometry that follows from them. What it is not is a program for
writing files -- yet every one of them ended with the same forty lines of
placement arithmetic, schematic writing, schedule saving, tallying, silhouette
PNGs, render sets and comparison sheets. Forty lines that are identical between
buildings are forty lines that will diverge between buildings, and the first
thing to go is always the part that fails soft.

    summary = finish(canvas, paths.OUT, frame, template=template,
                     layout=paths.LAYOUT_SCHEM, schedule=SCHEDULE,
                     orthos=paths.ORTHOS, scale=RENDER_SCALE)
    for line in summary.lines():
        print(line)

Nothing here decides anything. It writes what the build made and reports what it
wrote, so the deciding stays in the recipe and the grading stays in the gate.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .mask import Mask
from .render import STANDARD, View, render, render_set
from .schem import Schematic

# East elevation against the long view, top-down against the plan: the two pairs
# whose cameras genuinely match, so their silhouettes can be compared as
# silhouettes rather than as two pictures of the same object.
SHEETS = (("east", View.long(), "bottom"), ("top", View.plan(), "centre"))


class Finished:
    """What a finished build turned out to be, and where it was written."""

    __slots__ = ("blocks", "counts", "joined", "placement", "schematic",
                 "items", "undeclared", "views", "sheets", "overlap",
                 "plans", "notes")

    def __init__(self):
        self.blocks = 0
        self.counts: dict[str, int] = {}
        self.joined = 0
        self.placement: dict = {}
        self.schematic: Path | None = None
        self.items = 0
        self.undeclared: list[tuple[str, str]] = []
        self.views: dict[str, Path] = {}
        self.sheets: dict[str, Path] = {}
        self.overlap: dict[str, float] = {}
        self.plans: dict[str, Path] = {}
        self.notes: list[str] = []

    def lines(self) -> list[str]:
        out = [f"finalize: {self.joined} connectable blocks given their "
               "neighbours' state"]
        out.extend("! " + n for n in self.notes)
        if self.schematic:
            where = "placed" if self.placement else "local coordinates"
            out.append(f"wrote {self.schematic.name} ({where})")
        if self.items:
            out.append(f"schedule: {self.items - len(self.undeclared)} of "
                       f"{self.items} parts built")
            for name, what in self.undeclared:
                out.append(f"  not built  {name}: {what}")
        out.append(f"{self.blocks} blocks")
        out.extend(f"  {n:7d}  {block}" for block, n in self.counts.items())
        for name, value in self.overlap.items():
            out.append(f"  {name} silhouette IoU {value:.3f}")
        return out

    def report(self) -> dict:
        return {
            "blocks": self.blocks,
            "palette": len(self.counts),
            "counts": self.counts,
            "joined": self.joined,
            "placed": bool(self.placement),
            "schedule": {"items": self.items,
                         "undeclared": [n for n, _ in self.undeclared]},
            "iou": {k: round(v, 4) for k, v in self.overlap.items()},
            "notes": list(self.notes),
        }


def placement_of(layout: Path, template: Mask, blocks) -> tuple[dict, list[str]]:
    """Where in the world this build goes, from an older crop of the same map.

    The template PNG and the layout schematic are two crops of the same map, so
    the world position carries over as a shift between their grids.

    It fails soft on purpose. This used to be a bare read that raised, which
    meant a missing schematic killed the run *after* every block was placed and
    *before* anything was written -- so `out/` kept a build from two layouts ago
    while the gate went on grading it and reporting a pass. A build that cannot
    be placed in the world is still a build worth looking at; one that silently
    does not exist is not.
    """
    layout = Path(layout)
    if not layout.exists():
        return {}, [f"no {layout.name}: writing in local coordinates, so the "
                    "result cannot be pasted at the right place in the world"]

    schematic = Schematic.read(layout)
    marked = Mask.from_schematic(schematic, list(blocks)).components()
    if not marked:
        return {}, [f"{layout.name} has none of {', '.join(blocks)} on its "
                    "bottom layer, so there is nothing in it to line the build "
                    "up with: writing in local coordinates. Either the crop was "
                    "marked with different blocks -- pass them as "
                    "`layout_blocks` -- or the building is not on layer 0."]
    old = marked[0]
    a, b = old.bounds(), template.bounds()
    shift = (a[0] - b[0], a[1] - b[1])
    notes = []
    if (a[2] - a[0], a[3] - a[1]) != (b[2] - b[0], b[3] - b[1]):
        notes.append(f"template and layout footprints differ in size; "
                     f"shift {shift} is approximate")
    return dict(
        origin=schematic.origin,
        offset=(schematic.offset[0] + shift[0], schematic.offset[1],
                schematic.offset[2] + shift[1]),
    ), notes


# The two greys a layout schematic marks a building with: fill, and the guide
# lines drawn inside it.
LAYOUT_BLOCKS = ("minecraft:light_gray_concrete",
                 "minecraft:light_gray_concrete_powder")


def quickly() -> bool:
    """Whether this run should skip everything only a person reads.

    Read off `--quick` in the command line or `BLOCKWRIGHT_QUICK` in the
    environment rather than taken as an argument, and deliberately: a building's
    `build.py` is written once and buildings are not part of this project, so a
    flag that had to be threaded through every recipe would reach the ones
    written after it and none of the ones written before.

    What it turns off is the renders, the plan cuts and the comparison sheets --
    on a real building most of the run, and none of it read by the gate. What it
    never turns off is the schematic and the schedule, because those are what
    the next stage grades.
    """
    return "--quick" in sys.argv or os.environ.get("BLOCKWRIGHT_QUICK") == "1"


def finish(canvas, out, frame, *, template: Mask | None = None,
           layout=None, layout_blocks=LAYOUT_BLOCKS, schedule=None,
           name: str = "massing", scale: float = 6.0, orthos=None,
           views=STANDARD, sheets=SHEETS, plans=(), photos=()) -> Finished:
    """Join the blocks up, write everything, and draw what came out.

    `plans` are extra (label, y) horizontal cuts to write as PNGs -- a silhouette
    says how wide and how tall, and cannot say whether a court is open.

    The order matters in one place: `canvas.finalize()` runs first and runs once,
    on the finished build. WorldEdit pastes without running block updates, so a
    pane written plain stays a lone post in the world forever and every railing
    comes out as a row of disconnected stubs. A pane joined before its
    neighbours were drawn would be joined to a neighbour that is not there.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    done = Finished()
    quick = quickly()

    done.joined = canvas.finalize()

    if layout is not None and template is not None:
        done.placement, notes = placement_of(layout, template, layout_blocks)
        done.notes.extend(notes)

    done.schematic = out / f"{name}.schem"
    canvas.write(done.schematic, **done.placement)

    # The schedule travels beside the schematic rather than inside it: the gate
    # is a separate process, and importing the build module to ask it questions
    # would run the whole build again. It carries only the claims -- the counting
    # happens over there, against the blocks, so a build cannot mark its own
    # homework.
    if schedule is not None:
        schedule.save(out / "schedule.json")
        done.items = len(schedule.items)
        done.undeclared = [(n, schedule.by_name[n].what)
                           for n in schedule.undeclared]

    done.blocks = canvas.block_count()
    done.counts = canvas.counts()

    if quick:
        # Everything above this line is what the gate reads. Everything below it
        # is for a person to look at, costs most of the run, and is worth
        # nothing during a round of "move the number and see which stations go
        # green". Skipped by name rather than silently, because a stale render
        # that nobody knows is stale is worse than no render.
        done.notes.append(
            "quick run: no renders, no plans, no comparison sheets. The "
            "schematic and the schedule are current; everything in views/ and "
            "compare/ is from an earlier run. Re-run without --quick before "
            "looking at anything.")
        return done

    done.plans[f"{name}_plan"] = out / f"{name}_plan.png"
    canvas.silhouette().to_png(done.plans[f"{name}_plan"], scale=2)
    for label, y in plans:
        path = out / f"{name}_{label}.png"
        canvas.layer(y).to_png(path, scale=2)
        done.plans[label] = path

    # The silhouettes say how tall and how wide. They cannot say whether this is
    # one long block or a row of houses, so the renders exist to be looked at,
    # and the sheets to be looked at beside the mesh they were measured from.
    done.views = render_set(canvas, out / "views", frame=frame, scale=scale,
                            views=views)

    if orthos is None or not (Path(orthos) / "render_meta.json").exists():
        done.notes.append("no mesh orthos; skipping the comparison sheets")
        return done

    from . import compare

    for ortho, view, align in sheets:
        image = render(canvas, None, view=view, frame=frame, scale=scale)
        panels = [compare.mesh_panel(orthos, ortho),
                  compare.build_panel(image, scale, label=f"build {view.name}")]
        panels.extend(compare.photo(p) for p in photos)
        path = out / "compare" / f"{ortho}.png"
        compare.sheet(panels, path, scale=4.0, align=align)
        done.sheets[ortho] = path
        score = compare.iou(panels[0], panels[1], 0.5)
        if score is None:
            done.notes.append(
                f"{ortho} vs {view.name}: not comparable -- one panel covers "
                "the site and the other only the clipped building, so their "
                "overlap would be a number that never moves. Clip the render "
                "to the plan, or read the sheet with your eyes and ignore the "
                "score.")
        else:
            done.overlap[f"{ortho} vs {view.name}"] = score

    return done


__all__ = ["Finished", "finish", "placement_of", "LAYOUT_BLOCKS", "SHEETS"]
