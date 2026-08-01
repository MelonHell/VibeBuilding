# Pipeline rework, July 2026

The July review (`docs/review-2026-07.md`) found six defects by eye that the
harness found none of, and for five of the six it could not have. The three
causes it named were that a fault **cannot be expressed**, **cannot be seen**, or
**cannot be named**. This rework closes the first and the third in the library,
and gives the second a fixed place to be looked at.

It is also a settling of accounts. Most of the technique the pipeline has is not
in `vicemine/`; it is in one building's 592-line gate and its thirty throwaway
probes. That is fine for one building and useless for the second. The work here
is mostly relocation.

## What this is not

Three questions were settled before any of this was designed, and the answers
constrain everything below.

**The build script stays hand-written.** No `building_spec.json`, no declarative
core. `docs/pipeline.md` already lists "generate the build script" among the
things deliberately not done, and the reason holds: a spec format expressive
enough for a barrel vault landing on a parapet is a programming language with
worse tooling. The library supplies primitives and measurements; the building
supplies a recipe.

**Blender is not brought into the loop.** It renders orthographics of the Google
Earth mesh (`tools/render_orthos.py`) and nothing else. `vicemine/render.py`
already draws blocks honestly -- transparency, sub-block shapes, neighbour
culling that only culls behind opaque cubes -- so a second renderer would add a
second answer to a question that already has one.

**The critic layer is deterministic.** Everything numeric -- section, schedule,
connectivity, free ends, watertightness, silhouette IoU -- is computed by code
and written to `out/report.json`. Comparison sheets are rendered to a fixed
place. Judging what the sheets show is done by a person (or by a model reading
them as pictures), not by an orchestrator inside the pipeline. No API calls, no
knowledge base.

## The shape of the change

Four pieces of new library, one deletion, one rewrite.

```
vicemine/measure.py     measurement, as functions that return numbers
vicemine/gate.py        registration, section grading, exemptions, verdicts
vicemine/report.py      the machine-readable defect report
vicemine/finish.py      finish(): the twelve lines every build.py ends with
    + checks.watertight(), template.layers(), template.guides()

buildings/terra_beachside_villas/     deleted except input/
    then rewritten thin against the new library
```

### Ordering

Harvest, then delete, then rewrite. Deleting first would keep the *numbers* --
they are in Terra's README -- and lose the *methods*, which exist only as code.
The gate's registration fit, the exemption mechanism, the autocorrelation period
with its lower bound set from the frame angle: none of that is written down
anywhere except in the files being deleted.

## 1. `vicemine/measure.py`

Thirty probes, each a script that prints. The same measurement is wanted by the
build (to place something) and by the gate (to grade it), and today both would
have to re-implement it, which is how two answers to one question get into a
pipeline. The rule for this module is one line long:

> Every function returns a number or a structure. Nothing prints.

The probes collapse into eight families.

```python
storey_height(mesh, frame, bands, u0, u1, step=0.25, ceiling=25.0) -> Storeys
```
`floor_probe`. Balcony slabs and window heads put far more vertices at their own
height than at the blank wall between, so a histogram of vertex height over the
facades has one peak per floor. Returns the peak heights and the median spacing.
`bands` are (v0, v1) strips -- the facades that carry the rhythm -- because a
histogram over the whole building averages the balconies away.

```python
skyline(mesh, frame, u0, u1, datum, cell=1.0) -> dict[int, float]
```
Highest material at each v station inside a u window. This is what the gate's
section grading reads, and what `section_probe`, `section2_probe`, `ends_probe`,
`roof_probe` and `heightmap_probe` each rebuilt by hand. Absent stations mean
"no mesh here", which is not the same as zero and must not be flattened to it.

```python
silhouette(mesh, frame, u0, u1, datum, cell=1.0) -> Grid
```
`slice_probe`. Bins vertices by (v, height) rather than keeping only the top, so
a void under a canopy is visible where a skyline shows a solid wall.

```python
period(values, step, lo, hi) -> Period
```
Replaces `bays_probe`, `ortho_bays_probe`, `piers_probe`, `pitch_probe`,
`notch_probe`, `segment_probe`, `rib_probe`, `bay_probe` -- eight probes that are
one algorithm with eight inputs. Autocorrelation over a detrended,
median-smoothed series; peaks returned sorted by score.

`lo` has no default and that is the point. `pitch_probe`'s docstring records the
mistake it was written to fix: the first attempt counted every rise as a notch
and measured the rasterisation staircase, which at 52 degrees repeats every 1.3
to 1.6 m. The caller must state a lower bound above the staircase, and the frame
angle gives it: `lo > |cos t| + |sin t|`. A helper `staircase(frame)` returns
that number so the caller has no excuse to guess.

```python
edge_profile(mask, frame, bin=0.5) -> Profile
```
The low and high v of a part at each station along u -- what `period` is usually
run over, and what `frame.profile` returns in a shape nobody wanted.

```python
floor_lines(image, x0, x1, metres_per_pixel) -> list[float]
```
`elevation_probe`. A slab shows as a row that differs sharply from the row below
it, all the way across; summing that difference per row turns an orthographic
elevation into a 1D signal whose peaks are the slab edges. Steadier than reading
storey heights off a picture by eye.

```python
column_signal(image, y0, y1) -> list[float]
```
The same thing turned ninety degrees, for `column_probe`: vertical edges, which
feed `period` to give a pier or column rhythm off an ortho.

```python
presence(mesh, frame, u0, u1, v0, v1, datum, floor, ceiling) -> float
```
How much material stands in a box, as a fraction of its cells. This is the
question "is there a bridge here" and the question "is the pool under the cone
empty", both of which the review found by eye.

Two more measurements belong with the map, not the mesh, so they go in
`template.py`:

```python
template.layers(path) -> Layers      # fill, line and mass as separate masks
template.guides(path, frame) -> list[Guide]
```

`layers_probe` and `guides_probe`. The drawn line layer is the only record of
where one villa ends and the next begins, and `footprint()` merges it into the
mass. `plan.lines()` already extracts it; `layers` names all three so the
distinction stops being folklore.

And one belongs with the checks:

```python
checks.watertight(footprint, wall) -> list[tuple[int, int]]
```

`leak_probe`, which was written for exactly this and then left as a probe. An
8-connected flood from outside -- diagonal steps included, because a diagonal
pinhole is a hole you can see daylight through -- returns the interior cells it
reached. Empty means watertight.

## 2. `vicemine/gate.py`

Terra's gate is 592 lines, of which perhaps sixty are about Terra. The rest is
apparatus that any building needs and that would otherwise be copied -- and a
copied gate rots differently in each copy, which is worse than no gate.

```python
class Check:        name, ok, detail
class Gate:         add(), extend(), require_fresh(), lines(), failures, ok
```

`Gate` collects verdicts and owns the exit code, so nothing else in the pipeline
decides what a failure is.

`require_fresh(made, sources)` is the staleness guard, and it is not a nicety.
Terra found that a stale `massing.schem` registers perfectly against the mesh and
the gate goes on reporting a pass -- the build had changed and the grade had not.
Anything the gate reads must be newer than everything the build read.

```python
class Registration:
    u_scale, v_scale, to_mesh_u(u), to_build_u(u), to_build_v(v)
    @classmethod fit(mesh_uv, build_uv, trim=TRIM, agreement=SCALE_AGREEMENT)
```

The mesh and the build are the same building measured twice, and neither is in
the other's coordinates. Terra started with four hand-fitted constants for this
and they rotted silently. So it is measured on every run: take the extents of
each cloud at the `trim` quantile (0.5%, which drops photogrammetry spray
without touching the building), and the ratio of the spans is the scale. The two
axes are asserted to agree within 3% -- if the building is 1.15x in u and 0.98x
in v, the registration is not a registration and the section grade that follows
it is noise.

A helper `above(field, fraction)` returns the (u, v) of every height-field cell
above `fraction` of the maximum, which is the cloud the fit should use: at
ground level the mesh includes road, planting and neighbours, and registering
against those measures the capture, not the building.

```python
class Exemption:    name, why, stations, sign, covers(k, d)
```

Some disagreements between mesh and build are correct. Photogrammetry drops
thin tapering tips, so a cone is shorter in the mesh than it should be; past the
last station of this lot, the mesh is the next lot's roof. Both are real, both
are permanent, and both are stated -- with a reason and a signed direction, so an
exemption that hides an *under*-build cannot silently also hide an over-build.

Every exemption prints every run, whether or not it fired. An exemption that has
stopped covering anything is as much a signal as one that fires: it means the
geometry moved.

```python
def section(skyline, built, registration, window, tolerance=TOLERANCE,
            exemptions=(), drawn=None, seam=2) -> SectionResult
```

The heart of the old gate, parameterised. Per station across the building, the
mesh top against the built top, graded only where the plan claims something
(`drawn` count above `EDGE_CELLS`), skipping within `seam` of a window edge
where the two disagree for a reason that is not a defect. "Mesh has height,
build has none" is reported as `EMPTY` and counted as a miss -- not as a zero
difference, which is how a missing volume grades as a small error.

`window` is a named (u0, u1) span. Terra derives its two windows from the drawn
parts rather than typing them in; the library takes them as given.

## 3. `vicemine/report.py` and `vicemine/finish.py`

### `finish()`

Every `build.py` ends with the same twelve steps: read the placement out of the
layout schematic (failing soft, because a missing layout is not a build error),
`canvas.finalize()`, write the schematic, save the schedule, tally the counts,
write the silhouette PNG, render the standard set, and build the comparison
sheets against the mesh orthographics. Review item E1.

```python
finish(canvas, out, frame, layout=None, schedule=None,
       scale=6.0, orthos=None, photos=(), views=STANDARD) -> dict
```

Returns what it did, so the caller can print it or ignore it, and so
`report.py` can put it in the report without re-deriving it.

### `report.py`

```python
class Defect:   kind, where, detail, severity
def write(path, name, gate, **sections) -> dict
```

One JSON file, `out/report.json`, holding every number the run produced: each
check with its verdict and detail, the schedule audit, the section misses per
station, the block counts, the palette, the free ends, the strays, the IoU
against each mesh ortho. Nothing summarised away.

This is the artefact the "cannot be named" failures need. A defect that has a
name in a file can be tracked between runs; a defect that exists only as a line
of console output cannot. The comparison sheets are written to a fixed path
beside it, so the visual check has one place to look and the same place every
time.

## 4. Terra, rewritten

### Deleted

Everything under `buildings/terra_beachside_villas/` except `input/`:
`build.py` (1171 lines), `gate.py` (592), `probes/` (30 files), `README.md`,
`paths.py`, `__init__.py`, `out/`. `input/` is the three references and is not
reproducible.

### Rebuilt

```
__init__.py     unchanged in spirit
paths.py        unchanged
build.py        the recipe: parts, constants with their prose, the geometry
gate.py         ~60 lines: windows, tolerances, exemptions, budgets
probes/         three genuinely one-off probes, plus derive.py
README.md       the measurements, as before
```

`gate.py` shrinks by an order of magnitude because everything in it that was
apparatus is now imported. What is left is a declaration: which windows, what
tolerance, which exemptions and why, how many strays and free ends are
acceptable.

### The honesty test

Terra's constants are **re-derived from `input/` through the new `measure.*`
functions, not copied out of the README**. If `measure.storey_height` returns
4.5 m and the derived levels come back at v = 13.5 / 26.5 / 39.5, the extraction
is honest. If they come back at something else, the extraction lied and it has
surfaced here rather than inside a building.

`probes/derive.py` runs every derivation and prints the table, so the claim can
be checked in one command. The README's numbers become the test on the library:

```
storey height 4.5 m
strips at v 1.8..13.6, 14.0..39.4, 39.9..53.4
disc centre (125.7, 19.9), radius 9.5 m, residual 1.2 m
house centres 19.9 m front, 19.6 m back
notch pitch 12.3..13.5 m
```

### Constants keep their prose

Review section E is emphatic and it is right: the sixty-odd module constants
with a sentence each are the most valuable thing in the build script, and the
distinction between **measured** and **chosen** must survive. `SPRING = 22` came
off the mesh and the gate may check it. `JOINT = 3.0` is a legibility decision
and the gate must not. `PORT_SEED` is arbitrary. Marking them is a comment
convention, not a type -- anything heavier would be re-inventing the spec format
that was already rejected.

## Live bugs to fix in passing

From the review, still open in the library:

- leaves placed without `persistent=true` decay in-world (already fixed in
  Terra's current `build.py`; the fix must survive the rewrite)
- `build.windows` is imported and never called anywhere
- `blocks.py` falls back to a colour derived from `hash(base)`, which is
  non-deterministic between runs under Python's string hash randomisation --
  two runs of the same build render different colours

## Verification

1. `python -m buildings.terra_beachside_villas.probes.derive` -- the derived
   numbers match the README.
2. `python -m buildings.terra_beachside_villas.build` -- writes
   `out/massing.schem`, `out/schedule.json`, `out/renders/`, `out/compare/`.
3. `python -m buildings.terra_beachside_villas.gate` -- exits 0, and
   `out/report.json` holds the full numeric account.
4. The comparison sheets are read by eye against the six defects the review
   listed, and each is either absent or named in the report.
