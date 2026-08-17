"""The dozen steps every build script ends with.

A `build.py` is a recipe: parts, constants with the reason each one has its
value, and the geometry that follows from them. What it is not is a program for
writing files -- yet every one of them ended with the same forty lines of
schematic writing, schedule saving, tallying, silhouette PNGs, render sets and
comparison sheets. Forty lines that are identical between buildings are forty
lines that will diverge between buildings, and the first thing to go is always
the part that fails soft.

    summary = finish(canvas, paths.OUT, frame, schedule=SCHEDULE,
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

from .render import STANDARD, View, render, render_set

# East elevation against the long view, top-down against the plan: the two pairs
# whose cameras genuinely match, so their silhouettes can be compared as
# silhouettes rather than as two pictures of the same object.
SHEETS = (("east", View.long(), "bottom"), ("top", View.plan(), "centre"))


class Finished:
    """What a finished build turned out to be, and where it was written."""

    __slots__ = ("blocks", "counts", "joined", "schematic",
                 "items", "undeclared", "views", "sheets", "overlap",
                 "plans", "notes", "said", "stamp")

    def __init__(self):
        self.blocks = 0
        self.counts: dict[str, int] = {}
        self.joined = 0
        self.schematic: Path | None = None
        self.items = 0
        self.undeclared: list[tuple[str, str]] = []
        self.views: dict[str, Path] = {}
        self.sheets: dict[str, Path] = {}
        self.overlap: dict[str, float] = {}
        self.plans: dict[str, Path] = {}
        self.notes: list[str] = []
        # Plain reporting, as against `notes`, which are things to attend to.
        # A build with no world anchor is not a build with a problem, and a
        # line starting with `!` said otherwise for as long as the two shared
        # a list.
        self.said: list[str] = []
        self.stamp: Path | None = None

    def lines(self) -> list[str]:
        out = [f"finalize: {self.joined} connectable blocks given their "
               "neighbours' state"]
        out.extend("! " + n for n in self.notes)
        out.extend("  " + n for n in self.said)
        if self.schematic:
            out.append(f"wrote {self.schematic.name} (local coordinates)")
        if self.items:
            out.append(f"schedule: {self.items - len(self.undeclared)} of "
                       f"{self.items} parts built")
            for name, what in self.undeclared:
                out.append(f"  not built  {name}: {what}")
        out.append(f"{self.blocks} blocks"
                   + (f" -- tallied in {self.stamp.name} for checking against "
                      "a world" if self.stamp else ""))
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
            "schedule": {"items": self.items,
                         "undeclared": [n for n, _ in self.undeclared]},
            "iou": {k: round(v, 4) for k, v in self.overlap.items()},
            "notes": list(self.notes),
            "said": list(self.said),
        }


def stamp(out: Path, name: str, done: "Finished") -> Path:
    """What is in this build, in a form that can be checked against a world.

    The pipeline ends at a `.schem`. The building ends in a world somebody
    pasted it into, and between those two there is nothing: no record of which
    build is standing, and no way to tell a screenshot of last week's paste from
    a screenshot of this morning's. That gap cost a whole exchange -- a
    screenshot showing faults that had already been fixed, with nobody able to
    say whether the world was current.

    A hash is no use for this, because a world cannot be hashed. What a world
    *can* do is count blocks:

        //pos1, //pos2 round the pasted build, then
        //count minecraft:white_concrete

    So the stamp is the block tally, which WorldEdit will read back out of the
    world one block type at a time. Two tallies that agree are the same build;
    two that differ say by how much and in what. It is the only handshake
    available across a boundary the pipeline does not own.
    """
    lines = [
        f"# {name}",
        "",
        "What is standing, if the world holds this build. Check it with "
        "WorldEdit:",
        "",
        "    //pos1 and //pos2 round the pasted build",
        "    //count <block>",
        "",
        f"blocks   {done.blocks}",
        f"palette  {len(done.counts)}",
        "",
    ]
    lines += [f"{n:>9}  {block}" for block, n in done.counts.items()]
    lines += [
        "",
        "A world that counts differently is not this build. Re-paste before "
        "judging a screenshot: a fault fixed in the schematic and not in the "
        "world reads exactly like a fault that was never fixed.",
    ]
    path = out / f"{name}.stamp.md"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


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


def finish(canvas, out, frame, *, schedule=None,
           name: str = "massing", scale: float = 6.0, orthos=None,
           views=STANDARD, sheets=SHEETS, plans=(), photos=()) -> Finished:
    """Join the blocks up, write everything, and draw what came out.

    `name` is the stem of everything this writes that is *this* build: the
    schematic, the stamp, the silhouette and the plan cuts. A greybox and a
    finished build share an `out/`, and without the stem they would overwrite
    each other -- which is how form and material used to be one file.

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

    done.schematic = out / f"{name}.schem"
    canvas.write(done.schematic)

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
    done.stamp = stamp(out, name, done)

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


__all__ = ["Finished", "finish", "SHEETS"]
