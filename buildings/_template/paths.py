"""Where this building's files live.

Everything is resolved from this file's own location, so a script can be run from
anywhere. `input/` holds what a person supplied and is never written to; `out/`
holds what a script made and can be deleted and regenerated in full -- see the
README for the commands, in order.

This is also the list `sources.survey` reads to work out what was supplied, so a
building that keeps an input somewhere else changes it here and nowhere else.
Nothing in `input/` is required: seven paths are named below, any combination of
them may exist, and what is present decides what can be measured and what has to
be declared. See `docs/sources.md`.
"""

from pathlib import Path

HERE = Path(__file__).resolve().parent
INPUT = HERE / "input"
OUT = HERE / "out"

# -- the kinds of input -----------------------------------------------------

BRIEF = INPUT / "brief.md"              # what the building is, in words
VECTOR = INPUT / "plan.geojson"         # an outline, in lon/lat or in metres
VECTOR_SVG = INPUT / "plan.svg"         # the same, drawn rather than surveyed
LAYOUT = INPUT / "layout.png"           # a flat map crop, 1 px = 1 m
DRAWINGS = INPUT / "drawings"           # plans and elevations that carry a scale
SKETCHES = INPUT / "sketches"           # drawings that do not
MODEL = INPUT / "model.obj"             # a real 3D model, metres, Y up
GE_EXPORT = INPUT / "ge-export"         # a raw Google Earth capture
PHOTOS = INPUT / "photos"               # photographs of the real thing

# -- what the pipeline makes ------------------------------------------------

MESH_FULL = OUT / "mesh" / "merged.obj"     # the whole capture, converted
MESH = OUT / "mesh-clip" / "merged.obj"     # clipped to this site, not this building
ORTHOS = OUT / "mesh-clip" / "orthos"       # Blender elevations of the clip

SCHEM = OUT / "massing.schem"
SCHEDULE = OUT / "schedule.json"
REPORT = OUT / "report.json"
DERIVED = OUT / "derived.json"          # what `probes/derive.py` measured

OUT.mkdir(parents=True, exist_ok=True)
