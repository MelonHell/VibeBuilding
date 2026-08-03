"""The standard layout of a building package, in one place.

Every building has the same shelves: `input/` holds what a person supplied and is
never written to, `out/` holds what a script made and can be deleted and
regenerated in full. Six buildings each wrote out that list -- fifty-odd lines of
paths and the comments explaining what each one is for -- and the copies had
already started to disagree: three of them keep the capture in `ge-export` and
three in `google_earth`, which is a difference nobody chose.

So the list lives here and a building's `paths.py` becomes the part that is
actually its own: where it keeps anything unusual, and why.

    from blockwright.paths import Layout

    globals().update(vars(Layout(__file__)))

`globals().update` rather than an import, because `sources.survey` and every
script reads these as attributes of the building's own `paths` module, and that
is worth keeping: `paths.SCHEM` says which building it belongs to and
`Layout.SCHEM` does not.

Nothing in `input/` is required. Every path below may or may not exist, and what
is present is what decides what can be measured and what has to be declared --
see `docs/sources.md`.
"""

from __future__ import annotations

from pathlib import Path

# The two spellings a capture folder has been given. The first is the canonical
# one; the second is honoured so that a building already using it goes on
# working without anybody renaming a directory full of glTF tiles.
CAPTURE_NAMES = ("ge-export", "google_earth")


class Layout:
    """Where one building's files live, resolved from its own location.

    Everything is relative to the package directory, so a script can be run from
    anywhere. Pass `__file__` of any module directly inside the package; a module
    in a subdirectory (`probes/derive.py`) passes `up=1`.
    """

    def __init__(self, anchor: str | Path, up: int = 0):
        here = Path(anchor).resolve()
        if here.is_file():
            here = here.parent
        for _ in range(up):
            here = here.parent

        self.HERE = here
        self.INPUT = here / "input"
        self.OUT = here / "out"

        # -- the kinds of input ------------------------------------------
        #
        # What a person supplied. Never written to by anything here.
        self.BRIEF = self.INPUT / "brief.md"            # what it is, in words
        self.VECTOR = self.INPUT / "plan.geojson"       # an outline, surveyed
        self.VECTOR_SVG = self.INPUT / "plan.svg"       # the same, drawn
        self.LAYOUT = self.INPUT / "layout.png"         # flat map crop, 1 px = 1 m
        self.DRAWINGS = self.INPUT / "drawings"         # sheets carrying a scale
        self.SKETCHES = self.INPUT / "sketches"         # sheets that do not
        self.MODEL = self.INPUT / "model.obj"           # a real model, metres, Y up
        self.PHOTOS = self.INPUT / "photos"             # photographs of the real thing
        self.GE_EXPORT = self._capture()                # a raw Google Earth capture

        # Where the build lands in the world, for the case where that has been
        # decided in advance. **Optional, and usually absent.** The pipeline ends
        # at a schematic in local coordinates, which is a finished result: it
        # opens in an editor and pastes wherever somebody puts it, and every
        # stage grades it the same either way. This file is only needed when the
        # build has to land on one exact spot in one existing world, and a map
        # crop already fixes that spot.
        #
        # A building measured off a model or off a description has no world
        # position at all, because nothing supplied carries one. Where it stands
        # is then a decision a person makes, outside this pipeline.
        self.LAYOUT_SCHEM = self.INPUT / "layout.schem"

        # -- what the pipeline makes -------------------------------------
        self.MESH_FULL = self.OUT / "mesh" / "merged.obj"       # the whole capture
        self.MESH = self.OUT / "mesh-clip" / "merged.obj"       # clipped to the building
        self.ORTHOS = self.OUT / "mesh-clip" / "orthos"         # Blender elevations

        # The wider clip, covering the grounds rather than the building.
        #
        # Left unset here on purpose, because most buildings do not need one: the
        # clip that is right for a building is wrong for its site, and a building
        # that needs both says so in its own `paths.py`, next to the conversion
        # command that made it. A building whose whole export is already the
        # parcel points these at `MESH_FULL` and its orthos and says why.
        #
        #     MESH_SITE = MESH_FULL
        #     ORTHOS_SITE = OUT / "mesh" / "orthos"
        self.MESH_SITE = None
        self.ORTHOS_SITE = None

        self.SCHEM = self.OUT / "massing.schem"
        self.SCHEDULE = self.OUT / "schedule.json"
        self.REPORT = self.OUT / "report.json"
        self.DERIVED = self.OUT / "derived.json"    # what `probes/derive.py` measured

        self.OUT.mkdir(parents=True, exist_ok=True)

    def _capture(self) -> Path:
        """The capture folder, whichever of the two names it was given.

        The first that exists, and the canonical name when neither does -- so a
        new building gets told the preferred spelling and an old one goes on
        working without being renamed.
        """
        for name in CAPTURE_NAMES:
            candidate = self.INPUT / name
            if candidate.exists():
                return candidate
        return self.INPUT / CAPTURE_NAMES[0]

    def __repr__(self) -> str:
        return f"<layout of {self.HERE.name}>"


__all__ = ["CAPTURE_NAMES", "Layout"]
