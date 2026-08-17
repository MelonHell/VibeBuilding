"""<name>: the cameras the photo review looks through.

    python -m buildings.<name>.review            # the finished build
    python -m buildings.<name>.review --mesh     # and the reference, via Blender
    python -m buildings.<name>.review --greybox  # the volumes, into out/greybox/review/

Two tables and a call. Everything else -- solving where a camera has to stand,
rendering the reference from the same viewpoints through Blender, collecting the
photographs and the drawings, writing the prompt that names what is actually in
the folder -- is `blockwright.reviewing`, because it is the same on every
building.

The folder it leaves in `out/review/` is the whole of the review's evidence, and
it is read by the model driving the session. There is no second model, no
endpoint and no credential in this step. See `docs/pipeline.md`, stage 5, for
what the review is allowed to decide and what it is not.

Do not try to stand the cameras where the photographers stood. An earlier
version did: a photograph does not say where it was taken from, the guesses were
tens of metres out, and one of them put a canopy behind a wing so that the
reviewer reported it missing. Choose the angles for what they show, which is a
thing that can be got right.
"""

from __future__ import annotations

import sys

from blockwright.reviewing import Outside, Review, Shot

from . import paths
from .probes import derive

# What this building is. It is the only building-specific text in the prompt --
# everything else the reviewer needs it can see -- and it is the whole of what a
# reviewer given pictures and nothing else knows.
#
# **The review refuses to run while this is still the placeholder**, because
# three buildings went to a reviewer with these angle brackets in the prompt and
# every one of them came back with findings about the wrong things. Name the
# volumes, what they are made of, and how they stand to each other; where two
# parts differ deliberately -- one tower balconied and one blank -- say so, since
# that is exactly what a build makes identical and no number catches.
DESCRIPTION = "<what this building is, in a sentence or two: the volumes, what " \
              "they are made of, and how they stand relative to each other>"

# Four corners, two of them low. Enough to see the whole of a building from
# outside, and no more: a fifth exterior angle mostly reports the same faults a
# second time, and the reviewer then has to work out that it is the same fault.
#
# Add interior cameras -- `Shot`, with a place to stand rather than a direction
# -- wherever this building has an inside a person would walk through: a court, a
# passage, an atrium, under a canopy. They are the only views that show what
# carries what, and they are the views a capture is worst at, so they are also
# where the photographs earn their place. Three or four is usual:
#
#     Shot("05-court", eye=(0.35, 0.50, 2.0), target=(0.62, 0.50, 6.0),
#          fov=65.0,
#          reading="Standing in the court at eye level, looking along it. What "
#                  "encloses it, what carries the gallery, and whether the far "
#                  "end is closed or open."),
#
# `reading` is not a label. It is what the reviewer is told to look for at that
# viewpoint, and it is the difference between a report on the building and a
# report on whatever happened to be nearest the camera.
PLAN = (
    Outside(
        "01-high-front", azimuth=215.0, pitch=32.0, fov=50.0,
        target=(0.48, 0.42, 12.0),
        reading="High off one long side, from the near end. The whole length, "
                "the massing, and how the parts stand relative to each other.",
    ),
    Outside(
        "02-low-front", azimuth=245.0, pitch=7.0, fov=55.0,
        target=(0.50, 0.35, 12.0),
        reading="Near ground level, looking along the same side. The facade in "
                "perspective: storeys, bays, glazing, and how it is divided.",
    ),
    Outside(
        "03-far-end", azimuth=325.0, pitch=14.0, fov=52.0,
        target=(0.62, 0.40, 13.0),
        reading="Off the far end, low. What terminates the building at that end, "
                "and whether the end reads as an end.",
    ),
    Outside(
        "04-high-back", azimuth=60.0, pitch=25.0, fov=50.0,
        target=(0.50, 0.55, 12.0),
        reading="From the other long side, raised. The back of the building and "
                "anything standing behind it.",
    ),
    # The one camera that is not about the building. Every other shot here is
    # framed to hold the whole of it, which is a view nobody ever has: a person
    # arrives on foot, from the street, and the first thing they see is the
    # ground floor and the way the building meets the pavement. That is also
    # the part a capture flown from the air knows least about, and the part a
    # build most often leaves as bare podium.
    #
    # Eye height and a wide lens, standing back at the far edge of the site.
    # Move it to whichever side the approach is really on -- and if that is not
    # obvious, that is worth knowing before anything else here is judged.
    Shot(
        "05-approach", eye=(0.15, -0.25, 1.7), target=(0.45, 0.35, 8.0),
        fov=70.0,
        reading="At eye level on the approach, as somebody walking up to it "
                "would see. What the building does where it meets the ground: "
                "the entrance, the podium, the kerb, and whether the ground "
                "floor reads as a place to walk into or as a plinth.",
    ),
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    greybox = "--greybox" in argv
    schematic = paths.GREYBOX if greybox else paths.BUILD
    where = paths.OUT / "greybox" / "review" if greybox else paths.OUT / "review"
    return Review(paths, PLAN, DESCRIPTION,
                  schematic=schematic, where=where, greybox=greybox).run(
                      derive.plan_of(), argv)


if __name__ == "__main__":
    sys.exit(main())
