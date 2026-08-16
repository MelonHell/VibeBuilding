"""What counts as right for this building.

    python -m buildings.<name>.gate
    python -m buildings.<name>.gate --profile

The *order* the rows are asked in belongs to `grading.Grading`, and so does every
row itself. What belongs here is the part only this building can say: which
stretches of it to cut a section through, how many parts a plan cut through a
storey should show, which of its parts are a pair, and how much clutter a
finished build of this size is allowed.

This file used to carry the whole sequence -- its own `solid`, its own `holds`,
its own loops over `COUNTS` and `WATERTIGHT`, four hundred lines of it. That copy
had the failure mode `blockwright.gate` warns about in its own docstring: a
building started from here got **none** of the rows the library grew afterwards
-- not `corners`, not `facades built alike`, not `grounds`, not `the elevations
agree`, not `stands at that storey` -- and nothing said so, because a row that
does not exist prints nothing. Everybody worked around it by starting from the
last building instead, which is a template maintained by not being used.

So the sequence is gone and the constants are the file. A row the library adds
tomorrow appears here tomorrow, and if it needs a table this building has not
filled in, it says so as an `[----]` row naming the table. That is the point:

  * An empty table and a satisfied one look identical from outside -- both print
    nothing -- so "no rows failed" quietly comes to mean "no rows were asked".
    Every table below therefore has a companion that says *this building has
    nothing to declare here*: `UNDIVIDED` for `COUNTS`, `SINGULAR` for `TWINS`,
    `UNIFORM` for `FACADES`, `GROUNDLESS` for `GROUNDS`. Setting one is a
    decision a reader can disagree with. Leaving both empty is a silence.

  * Every budget should be set by lowering it onto a build that already passed
    rather than typed in advance: a budget with an order of magnitude of slack
    has stopped being a check and become a comment. What is not written here is
    inherited from `grading.DEFAULTS`, and the row says `(default)` when it was,
    so an inherited budget is legible as one.

The section is the one check in this pipeline that can come back wrong. Every
other row grades the build against something it was drawn from, which is a
tautology -- the build is the plan, extruded, so of course they agree. The
reference is read only for numbers, never for geometry, so a section cut through
both is a comparison against something the build was never given.

That argument holds exactly as far as the evidence does, and the gate says how
far that is. Where the plan came from a map and the section is cut against a
capture, the two are independent and the section is a real outside check. Where
the same model supplied both, the section grades the simplification and cannot
catch the model being wrong. Where there is no model and no capture at all, there
is nothing to cut a section against, and the run is reported `ungraded` -- not
`pass`, which would be this file claiming a building was checked because nothing
contradicted it.

Two things a building may override with a function rather than a constant, both
documented in `grading.Grading`: `windows()`, the stretches to cut through and
the exemptions each may use, and `build_cloud()`, for a build that has to bring
its heights back to the reference's scale before the comparison. Neither has a
silent default worth relying on -- write one only with the reason beside it.
"""

from __future__ import annotations

import sys

from blockwright import grading

from . import paths
from .probes import derive

BUILDING = "<name>"

# The library registers on material above half the build's height. Raise it when
# the clip carries tall trees or a neighbour that photogrammetry cannot tell from
# building: fitting an extent against a belt of trees stretches that axis, and
# what is then graded is the landscaping. `derive` reports the disagreement
# between the two axes, which is how this number gets chosen.
#
# Read only if the survey never registered anything. The registration this gate
# grades through is the one `derive` already made, because every number in the
# build came through that fit.
REGISTER_AT = 0.5

# Planting and water stand on the building rather than being it. Left in, a palm
# sets the skyline and the section grades a frond against a parapet.
SOFT = {
    "minecraft:moss_block",
    "minecraft:jungle_leaves",
    "minecraft:jungle_log",
    "minecraft:short_grass",
    "minecraft:grass_block",
    "minecraft:water",
}

# What this building's fixtures actually cost -- planter feet standing clear on a
# podium, rail posts and truss webs ending in air. Set from a run that passed.
# `FLOATING_BLOCKS` is not written here because it is not a budget: nothing may
# hang unsupported, ever, and the default is zero.
GROUNDED_STRAYS = 200

# -- how the building divides -------------------------------------------------
#
# What a plan cut through a storey should show, part by part: the name the build
# declared in its schedule, and how many separate components a cut through it
# ought to find. Counted rather than looked at, so a floor that lost its division
# between houses fails as a number instead of being noticed later in a render.
#
# Counted inside each part's own declared footprint and nowhere else. Everything
# that crosses between parts -- galleries, bridges, canopy piers -- stands at some
# height in the same layer, and a count taken over the whole layer either grades
# a pier as a house or merges two houses through a bridge and grades the pair as
# one.
#
# At several heights and not at one, because a division can survive at 3 m and be
# gone at 17 m. Choose heights that fall between floor lines, not on them: a cut
# taken exactly at a deck reads the deck.
COUNTS: dict[str, int] = {
    # "villas": 13,
}
LEVELS: tuple[int, ...] = (3, 8, 12, 17, 20)

# Set True for a building that genuinely has no repeated division: one shed, one
# tower, one hall. Then the row says so, by name, and the reader can disagree
# with a decision instead of guessing at a silence.
UNDIVIDED = False

# Parts that should read as one closed ring at one height -- a drum, a shell, a
# tower shaft. The height is chosen above whatever stands under the part and
# below whatever caps it: a taper drawn one course too thin closes to a dashed
# line at some height and at no other.
RINGS: dict[str, int] = {
    # "cone": 20,
}

# Where to test that a wall ring actually holds what it encloses, and for which
# parts. A band of footprint-minus-erosion leaks at every diagonal, and on a
# rotated frame every wall in the building is a diagonal. The height must be one
# of `LEVELS`: this reads the cut the gate already took.
WATERTIGHT_AT = 8
WATERTIGHT: tuple[str, ...] = tuple(COUNTS)

# -- whether the building agrees with itself ----------------------------------
#
# Plan parts that are a mirror pair, with the axis taken between them. Nothing
# else can ask this: two bars of the same footprint have one silhouette and one
# profile, so whatever happens on their facades, every outside row passes.
TWINS: tuple[tuple[str, str], ...] = (
    # ("front", "back"),
)
# True where this building genuinely has no mirror pair. Say it rather than
# leaving `TWINS` empty -- but note that the storey rhythm is asked either way,
# by `stands at that storey`, and that row needs no table at all. What repeats in
# most buildings is translation, not reflection.
SINGULAR = False

# What repeats by translation is asked in the manifest instead, not here:
# `Item(..., copies=N)` beside the part and `Schedule.declare_repeat` beside the
# stamp that made them. The row compares the blocks of each copy with the first,
# cell for cell, which is the one thing that tells eight identical sections from
# eight sections that were drawn eight times -- they cast the same silhouette,
# cut the same section, cover the same plan and hold the same materials.
#
# This is the reason a building with nothing in it twice gives, so that the row
# prints a decision rather than a silence. A sentence, not a flag: `UNIFORM =
# True` stood on two buildings and turned off the only row that looked at a wall.
UNREPEATED: str | bool = False

# How many distinct top heights a part is allowed. A parapet built at nine
# heights along one roof passes every station it is judged at -- it is what those
# stations were cut from -- and reads as a staircase in the first render.
LEVEL: dict[str, int] = {
    # "front": 5,
}

# Plan parts that are not walls and so have no storey to stand at: a retractable
# roof vault, a girder rail, a plaza apron. Asking one where its floors are is a
# question with no right answer, and it answers with its own geometry -- an arch
# reports the pitch of its curvature. Named parts print a row of their own, and a
# name the plan does not draw stops the gate.
NOT_WALLS: tuple[str, ...] = ()

# -- whether the reference agrees ---------------------------------------------
#
# Groups of parts the reference says are faced alike, and the parts that stand
# apart. This asks the opposite of `TWINS`: not "you said these are the same, are
# they?" but "does the reference agree with what the build made uniform?" Only
# the reference can raise it.
FACADES: dict[str, tuple[str, ...]] = {
    # "towers": ("tower_east", "tower_west"),
}
# True where the reference shows one facade treatment over the whole building.
# It takes a reason rather than a bare flag on a building that has one.
UNIFORM = False

# The ground this building stands on, by surface and the height band it lies in.
# The clip the section is cut against throws the grounds away by construction, so
# without this the deck, the road and the beach can be any size and any shape and
# every other row stays green. It has happened twice.
GROUNDS: dict[str, tuple[float, float]] = {
    # "deck": (1.5, 3.0),
}
RECTANGULAR: tuple[str, ...] = ()
# True where this build lays no ground at all.
GROUNDLESS = False

# Parts the reference reads at two heights, and what the second one is: a plant
# room on a roof, a neighbour standing on a podium. Never a failure -- the
# capture is what it is -- but unexplained it leaves a measured number with
# nothing beside it.
PLATEAUS: dict[str, str] = {
    # "podium": "the chiller house on its north end",
}


GATE = grading.Grading(paths, derive, sys.modules[__name__])


def main() -> int:
    return GATE.main()


if __name__ == "__main__":
    sys.exit(main())
