"""<name>: every number this building is set out on, re-measured from `input/`.

    python -m buildings.<name>.probes.derive

This file is the *tables*. The reading of them -- work out what was supplied,
pick the branch that states the plan, load the reference, find the storey
rhythm, read the skyline over each part, ask the held-out sources what they say,
write `out/derived.json` -- is `blockwright.survey`, because it is the same on
every building. `build.py` reads that file and refuses to start without it.

Three plans and two sections are possible, and the branch taken is reported
rather than assumed:

    the plan       a flat map, decomposed along its drawn lines
                   a 3D model or a capture, split at its roof steps
                   a table of declared shapes, typed from a brief or a drawing

    the section    measured off an OBJ -- storey rhythm and skyline
                   declared, when there is no OBJ at all

Nothing here is typed in except the **windows** -- which facade to look at, which
stretch along the building -- because a window is a choice about where to look
and not a result. Everything else is measured on every run, so when the map is
redrawn or the capture re-clipped the numbers change and the build changes with
them.

The exception is a declaration: a fact no supplied source can measure, written
down with its source named. The tables below are where those go, beside the
measured numbers rather than hidden among them.
"""

from __future__ import annotations

import sys

from blockwright import declared, flatmap, measure, survey

from .. import paths

# -- the declared plan ------------------------------------------------------
#
# Used when nothing machine-readable states the plan -- no map, no model, no
# capture. Then the plan is somebody's reading of the brief, of a sketch, or of
# a drawing, and every shape says whose reading it was.
#
# A drawing that carries a scale lands here too, and its numbers are real metres
# rather than guesses. What it shares with the brief is the mechanism: a person
# read a sheet and typed, and re-running this script will not re-derive it.
#
# Zero degrees is the right angle for a building nothing places on a street grid:
# a declared plan has no grid to sit in, and drawing it square keeps the
# rasterisation staircase out of a build whose dimensions are already the softest
# thing about it.
PLAN_ANGLE = 0.0

DECLARED_PLAN: list = [
    # declared.Rect("front", 0.0, 60.0, 0.0, 12.0,
    #               source="brief.md: 'a long block about sixty metres by twelve'"),
    # declared.Round("drum", 66.0, 6.0, 7.0,
    #                source="sketches/east.png, read against the block beside it"),
]

# -- the declared section ---------------------------------------------------
#
# Used when there is no OBJ to measure heights off, and as the fallback when
# there is one and it carries no storey rhythm -- a clean model of a blank
# facade has nothing to autocorrelate, and that is not a failure of the search.
DECLARED_HEIGHTS: dict[str, dict] = {
    # "front": {"top": 12.0, "source": "brief.md: 'four storeys'"},
}

DECLARED_STOREY: dict = {
    # "spacing": 3.0, "source": "brief.md: 'floor to floor about three metres'",
}

# -- windows: the only hand-chosen numbers here -----------------------------
#
# The stretch along the building where it is a uniform extrusion -- no entrance,
# no end block, nothing tapering. Read as a fraction of the drawn length rather
# than in metres, so it survives the plan being redrawn.
BODY = (0.05, 0.60)

# The storey rhythm is read off the facades that carry the balconies. A facade
# without them has no rhythm to find, and averaging a blank facade together with
# a modelled one deletes the peaks. The bands are taken from the measured edges,
# one metre inside each, so they follow the plan rather than a typed v.
FACADE_INSET = 1.0

# Height search windows. `STOREY_RANGE` starts at 2 m because a storey shorter
# than that is not a storey, and stops at 6 m because past that the
# autocorrelation starts reporting the two-floor harmonic. Raise the ceiling for
# a tall building; it only bounds where the search looks.
STOREY_RANGE = (2.0, 6.0)

STOREY_STEP = 0.25

STOREY_CEILING = 26.0

# Below this correlation the search found no rhythm, and its peak is noise. It
# is recorded as "not found" rather than dropped, because which bands were
# looked at and how badly they scored is the evidence for moving them.
STOREY_SCORE = 0.15

# The notch pitch on the long edges. The lower bound is the rasterisation
# staircase with margin: below it, what is being measured is the pixel grid.
NOTCH_RANGE_HI = 25.0

PROFILE_BIN = 0.5

PROFILE_TRIM = 8.0      # metres off each end, where the strip turns its corner

# Fitting an OBJ's own frame: material this far above the datum is building
# rather than the ground plane of the clip box.
MESH_FLOOR = 6.0

# Material this far above the ground is building, and the reference and the plan
# are registered on it. Six metres is two storeys: high enough to leave the
# road, the cars, the hedges and the street furniture behind, low enough to keep
# every part of the building itself, including a single-storey wing.
#
# It is a height in metres and not a fraction of the reference's own top, and
# that is the whole point. A fraction discards every part shorter than the
# tallest -- and the plan it is being registered against does not -- so on a
# podium-and-tower the two sides describe different buildings and their scales
# disagree by tens of per cent.
#
# Raise it when the clip carries a belt of tall trees, and understand what that
# costs: anything below the new floor stops being registered.
REGISTER_FLOOR = 6.0

# Plan cells for `measure.presence`, used by the void probes at the bottom.
SECTION_CELL = 1.0

# -- what a map is expected to hold -----------------------------------------
#
# Names for the parts a map decomposes into, so that everything downstream --
# this file, `build.py`, `gate.py`, the report -- speaks about the building
# rather than about "strip 2". Strips are named in v order, low v first; discs in
# u order. A count that does not match what `decompose` found is a fault in the
# map or in this list, and it stops the run rather than mislabelling the parts.
#
# Only used on the map branch. A model names its parts through `MODEL_PARTS`, and
# a declared plan names them in `DECLARED_PLAN`.
# Empty is the right starting point: run `derive` once, read the parts it
# prints, then name them here and run it again. A name typed before the first
# decomposition is a guess about a number nobody knows yet.
STRIPS: tuple[str, ...] = ()

DISCS: tuple[str, ...] = ()

# Names for the parts a model or a capture splits into, largest first. Leave it
# empty and they are called part-1, part-2 and so on -- which builds, and which
# makes every later conversation about the building worse.
MODEL_PARTS: tuple[str, ...] = ()

# The model's own axes and units. An OBJ carries neither, so both are stated
# here: `up` is 'y' or 'z', and the scale is 1.0 for a model already in metres.
# Guessing a scale multiplies one guess through every dimension downstream, and
# nothing later can catch it -- the build agrees with itself perfectly, at the
# wrong size.
MODEL_UP = "y"

MODEL_SCALE = 1.0

# Which greys this map draws a building in. Stated here for the same reason the
# model's up-axis is: the file does not say, so somebody has to. The default is
# one particular map's palette, and a crop from anywhere else -- another game,
# an OSM export, a scan in black on white -- needs its own.
#
#     python -m tools.map_probe buildings/<name>/input/layout.png
#
# prints the greys the crop actually contains and proposes a palette. `None`
# means the default; `footprint` says so loudly rather than quietly finding
# nothing when it does not fit.
MAP_PALETTE = None

# -- disagreements that are facts about the inputs --------------------------
#
# Two supplied sources can differ for a reason that is not a fault, and the
# commonest is that they are of different things. A crop from a game map stands
# its building on an invented street grid; the capture is of the real prototype,
# forty degrees away. The bearings then disagree forever, correctly.
#
# Naming one here makes its row **ungraded with the reason printed**, rather
# than failing. It is not a widened tolerance: widening would excuse every other
# disagreement on that axis too, including the ones that are faults, and would
# do it silently. Keyed by question -- bearing, extent, part heights, storey
# height, plan overlap.
#
# A key that matches nothing is left alone, so a stale entry shows up as a row
# that has gone back to being graded.
EXPECTED: dict[str, str] = {
    # "bearing": "the map is a game map and stands this building on its own "
    #            "street grid, 37.5 degrees off the real one the capture is of",
}

# Metres per unit of an SVG plan. A GeoJSON in longitude and latitude is
# projected and needs none; one already in metres needs none either. An SVG
# carries a viewBox and no units at all, so it cannot be read without this --
# and one guessed scale multiplies through every dimension downstream.
VECTOR_SCALE = 0.0

# -- declarations: what only a photograph can say ---------------------------
#
# Each entry names the photograph it was read off. A declared number with no
# source is a number somebody remembered, and it will still be here, unchanged
# and wrong, after the plan is redrawn twice.
#
# Counts of parts belong here. Neither a map nor a capture divides a terrace into
# houses on its own: the map draws notches, but a notch pitch is evidence and not
# a count, and photogrammetry smooths the joints away. Whatever *follows* from a
# declared count -- the bay pitch, and whether the notches agree with it -- is
# measured below.
DECLARED: dict[str, dict] = {
    # "houses": {
    #     "front": 5,
    #     "back": 8,
    #     "source": "A11795157_35.jpeg from above, 11275.jpg in elevation",
    # },
}

def probes(out: dict, read, link) -> None:
    """This building's own windows into the reference.

    Everything the apparatus measures is true of any building. What goes here is
    the part only this one needs, and each entry should carry the reason it was
    opened. Three that come up often:

      A void. A skyline cannot see one: material at the top and material at the
      bottom with nothing between reads as a solid block as tall as the roof.
      `measure.presence(link.mesh, link.frame, u0, u1, v0, v1, h0, h1,
      datum=link.datum, cell=SECTION_CELL)` answers "what fraction of this floor
      area has anything at all over it, between these two heights" -- in plan and
      not in volume, because photogrammetry returns a surface and a filled box
      would report every real floor as mostly empty.

      A curved section -- a vault, a dome, a shell. Take the whole profile,
      station by station, and not three points of it. Fitting an arc through
      springing, crown and far springing is a guess about the twenty stations
      between them. Report the arc as well, and its error against the profile, so
      that "it is not an arc" is something the file states rather than something
      a reader has to notice.

      A tapering tip -- a spire, a cone. Photogrammetry rounds one off, so a
      capture will read short. Take the height from the photographs, record the
      capture's reading beside it with a note saying why they differ, and let the
      gate exempt that part by name rather than widening its tolerance.

    `link` is None when nothing was supplied to measure against, so anything
    here has to say what it does in that case.
    """
    if link is None:
        return


# The tables above, handed over whole. `sys.modules[__name__]` is this module
# itself: thirty constants passed as thirty arguments would be a list to keep in
# step, and the point of the split is that there is only one such list and it is
# this file.
SURVEY = survey.Survey(paths, sys.modules[__name__], probes=probes)


def plan_of(out: dict | None = None):
    """The plan, read the way `main` reads it.

    `build.py`, `gate.py` and `review.py` all call this rather than reading a
    plan of their own: they have to agree cell for cell, and they used to each
    open the map, which worked until the day one of them was given a model
    instead and the other went on reading a map that was no longer the authority.
    """
    return SURVEY.plan_of(out)


def main() -> dict:
    return SURVEY.main()


if __name__ == "__main__":
    for line in SURVEY.report(main()):
        print(line)
    print()
    print(f"written to {paths.DERIVED}")
