"""Is a wall on a lattice slope really periodic, and is a stamp really a copy?

    python -m tools.lattice_selftest

Everything the lattice work rests on is one claim: at a rational slope the
rasterised edge is *exactly* invariant under the step vector, so a section
repeated at a whole number of periods is the same cells and not a second
rasterisation of the same shape. The claim is true for a good reason -- moving
by `(a, b)` changes `v = -x sin t + z cos t` by `-a sin t + b cos t`, which is
zero exactly when `(a, b)` is parallel to the direction -- and it is worth an
assertion anyway, because it is a claim about floating point as much as about
geometry.

Each check here also has to fail on the case it is supposed to catch. A test
that passes at 52.43 degrees as well as at 53.13 would be testing nothing: the
whole difference between the pipeline before this work and after it is that one
of those two angles repeats and the other does not.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from blockwright import blocks, checks, grading, lattice   # noqa: E402
from blockwright.build import Canvas                       # noqa: E402
from blockwright.frame import Frame                        # noqa: E402
from blockwright.mask import Mask                          # noqa: E402
from blockwright.schedule import Item, Schedule            # noqa: E402

W = L = 128

failures: list[str] = []


def check(name: str, ok: bool, detail: str) -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


# -- the slopes themselves --------------------------------------------------


def motifs_are_what_they_say() -> None:
    """The motif sums to the long leg and has one entry per run."""
    bad = []
    for slope in lattice.slopes(runs=3, reach=24):
        long = max(abs(slope.a), abs(slope.b))
        if slope.runs == 0:
            if slope.motif:
                bad.append(f"{slope!r} is axis-aligned and claims a motif")
            continue
        if len(slope.motif) != slope.runs:
            bad.append(f"{slope!r} has {len(slope.motif)} runs, claims {slope.runs}")
        if sum(slope.motif) != long:
            bad.append(f"{slope!r} motif sums to {sum(slope.motif)}, not {long}")
        if max(slope.motif) - min(slope.motif) > 1:
            bad.append(f"{slope!r} motif is not balanced: {slope.motif}")
    check("motifs describe their own slope", not bad,
          bad[0] if bad else
          f"{len(lattice.slopes(runs=3, reach=24))} slopes, each motif "
          "one entry per run, summing to the long leg, balanced")


def only_short_motifs_are_offered() -> None:
    worst = max(lattice.slopes(runs=3, reach=48), key=lambda s: s.runs)
    check("nothing longer than three runs is offered", worst.runs <= 3,
          f"the longest motif in the catalogue is {worst.runs} run(s): {worst!r}")


def the_grid_is_dense_near_the_axes_and_sparse_near_45() -> None:
    """The shape of the cost, stated rather than discovered on a building."""
    angles = [s.angle for s in lattice.slopes(runs=3, reach=96)
              if 0.0 <= s.angle <= 90.0]
    angles.sort()
    gaps = [(b - a, a) for a, b in zip(angles, angles[1:])]
    widest, where = max(gaps)
    near_zero = max(g for g, a in gaps if a < 20.0)
    check("the angle grid is sparse where 45 degrees is", widest > 4.0,
          f"widest gap {widest:.2f} deg at {where:.2f}; near the axis the "
          f"widest is {near_zero:.2f} deg")


def choosing_prefers_a_period_it_can_repeat_on() -> None:
    """Sorting by angle alone picks a slope nothing can be repeated on."""
    closest = lattice.nearest(0.5, span=165.0)
    picked = lattice.choose(0.5, 165.0)
    check("a nearly-square building is built square",
          picked.period < closest.period and not picked.strained,
          f"closest by angle is {closest.a}:{closest.b} with a "
          f"{closest.period:.1f} m period; chosen is {picked.a}:{picked.b} at "
          f"{picked.period:.1f} m, {picked.cost:.2f} m at the ends")


def an_unaffordable_snap_says_so() -> None:
    picked = lattice.choose(39.0, 165.0)
    cheap = lattice.choose(52.43, 165.0)
    check("a snap nothing can afford is marked, not hidden",
          picked.strained and not cheap.strained,
          f"39 deg -> {picked.a}:{picked.b}, {picked.cost:.2f} m at the ends "
          f"against a {lattice.budget_for(165.0):.2f} m budget (strained); "
          f"52.43 deg -> {cheap.a}:{cheap.b}, {cheap.cost:.2f} m (not)")


# -- the claim the whole thing rests on --------------------------------------


def _edge(frame: Frame) -> list[int]:
    """Where the half-plane `v >= 0` starts in each column, or -1."""
    mask = frame.region(W, L, lambda u, v: v >= 0.0)
    out = []
    for x in range(W):
        first = -1
        for z in range(L):
            if mask.get(x, z):
                first = z
                break
        out.append(first)
    return out


def _periodic(starts: list[int], a: int, b: int) -> tuple[int, int]:
    """(columns compared, columns where the edge is not the step's translate)."""
    seen = wrong = 0
    for x in range(W - abs(a)):
        here, there = starts[x], starts[x + a]
        if here < 1 or there < 1 or here > L - 2 or there > L - 2:
            continue
        seen += 1
        if there - here != b:
            wrong += 1
    return seen, wrong


def a_lattice_wall_repeats_exactly() -> None:
    slope = lattice.Slope(3, 4)
    frame = Frame((17.5, 23.5), slope.angle)
    seen, wrong = _periodic(_edge(frame), slope.a, slope.b)
    check("a wall on a lattice slope is its own translate",
          seen > 40 and wrong == 0,
          f"{slope.a}:{slope.b} at {slope.angle:.2f} deg: {seen} columns "
          f"compared, {wrong} where the edge is not exactly {slope.b} cells "
          f"along at {slope.a} across")


def a_measured_wall_does_not() -> None:
    """The same check on the angle this work exists to stop building at."""
    frame = Frame((17.5, 23.5), 52.43)
    seen, wrong = _periodic(_edge(frame), 3, 4)
    check("the same check fails at the measured angle", seen > 40 and wrong > 0,
          f"52.43 deg: {wrong} of {seen} columns are not the translate -- "
          "the run lengths never come back into phase")


def a_section_authored_twice_lands_on_the_same_cells() -> None:
    """Two rectangles a whole number of periods apart, rasterised separately."""
    slope = lattice.Slope(3, 4)
    frame = Frame((14.0, 11.0), slope.angle)
    periods = 4
    pitch = periods * slope.period
    step = slope.times(periods)

    first = frame.rect(W, L, 6.0, 6.0 + 18.0, 8.0, 8.0 + 11.0)
    second = frame.rect(W, L, 6.0 + pitch, 6.0 + pitch + 18.0, 8.0, 8.0 + 11.0)
    moved, lost = first.shifted(*step)
    apart = (moved ^ second).count() if hasattr(moved, "__xor__") else \
        ((moved - second) | (second - moved)).count()
    check("a section drawn twice on a lattice slope is one shape",
          apart == 0 and lost == 0 and first.count() > 150,
          f"pitch {pitch:.2f} m = {periods} periods = step {step}; "
          f"{first.count()} cells, {apart} differ between the translate and "
          "the redraw")


def the_same_two_sections_disagree_off_the_lattice() -> None:
    frame = Frame((14.0, 11.0), 52.43)
    pitch = 20.0
    first = frame.rect(W, L, 6.0, 6.0 + 18.0, 8.0, 8.0 + 11.0)
    second = frame.rect(W, L, 6.0 + pitch, 6.0 + pitch + 18.0, 8.0, 8.0 + 11.0)
    moved, _ = first.shifted(12, 16)
    apart = ((moved - second) | (second - moved)).count()
    check("off the lattice the redraw is a different shape", apart > 0,
          f"52.43 deg, pitch {pitch:.1f} m: {apart} of {first.count()} cells "
          "differ between the nearest whole-cell translate and the redraw")


# -- the stamp ---------------------------------------------------------------


def a_shift_loses_nothing_it_does_not_report() -> None:
    mask = Frame((4.0, 4.0), 53.13).rect(W, L, 0.0, 20.0, 0.0, 12.0)
    inside, lost_in = mask.shifted(5, 5)
    _, lost_out = mask.shifted(0, L)
    check("a shift is exact and reports what fell off",
          inside.count() == mask.count() and lost_in == 0
          and lost_out == mask.count(),
          f"{mask.count()} cells shifted in the grid keep {inside.count()}, "
          f"{lost_in} lost; shifted off it, {lost_out} lost")


def a_stamp_copies_the_blocks_and_the_holes() -> None:
    slope = lattice.Slope(3, 4)
    frame = Frame((10.0, 6.0), slope.angle)
    step = slope.times(4)                      # 20.00 m along the building
    motif = frame.rect(W, L, 4.0, 24.0, 6.0, 18.0)

    canvas = Canvas(W, 16, L)
    canvas.fill(motif, 0, 10, "minecraft:white_concrete")
    canvas.fill(motif.erode(1.5), 1, 9, "minecraft:air")
    canvas.fill(frame.rect(W, L, 9.0, 13.0, 6.0, 7.5), 3, 7,
                "minecraft:light_blue_stained_glass")
    canvas.stamp(motif, 0, 10, step, 3)

    apart = []
    for n in (1, 2, 3):
        ox, oz = step[0] * n, step[1] * n
        for x, z in motif.cells():
            for y in range(0, 10):
                if canvas.get(x + ox, y, z + oz) != canvas.get(x, y, z):
                    apart.append((n, x, y, z))
    glass = sum(1 for b in canvas.counts()
                if b == "minecraft:light_blue_stained_glass")
    check("a stamp copies the volume, air and all", not apart and glass == 1,
          f"{motif.count()} cells x 10 courses x 3 copies, "
          f"{len(apart)} differing; the carved window and the glazing came "
          "with them")


def a_stamp_that_runs_out_of_canvas_stops() -> None:
    frame = Frame((10.0, 6.0), 53.13)
    motif = frame.rect(W, L, 4.0, 24.0, 6.0, 18.0)
    canvas = Canvas(W, 8, L)
    canvas.fill(motif, 0, 4, "minecraft:stone")
    try:
        canvas.stamp(motif, 0, 4, (3, 4), 40)
    except ValueError as err:
        check("a stamp off the edge of the canvas refuses", "copy" in str(err),
              str(err).split(" leaves ")[0] + " ...")
        return
    check("a stamp off the edge of the canvas refuses", False,
          "forty copies of a twenty-metre section fitted a 128 m grid")


# -- circles -----------------------------------------------------------------


def _runs(mask: Mask) -> list[int]:
    """Cells per column, over the columns that have any."""
    out = []
    for x in range(mask.width):
        n = sum(1 for z in range(mask.length) if mask.get(x, z))
        if n:
            out.append(n)
    return out


def a_circle_on_the_grid_is_symmetric() -> None:
    """Eight-fold, which is the only property a circle has."""
    disc = Mask.circle(W, L, 41.3, 55.8, 13.0)
    columns = _runs(disc)
    rows = [sum(1 for x in range(W) if disc.get(x, z))
            for z in range(L) if any(disc.get(x, z) for x in range(W))]
    check("a circle drawn on the block grid is symmetric",
          columns == columns[::-1] and rows == rows[::-1]
          and columns == rows,
          f"{disc.count()} cells; columns {columns[:4]}... read the same "
          "backwards, and the rows are the columns")


def a_circle_through_the_frame_is_not() -> None:
    frame = Frame((7.3, 11.9), 53.13)
    disc = frame.disc(W, L, 30.0, 20.0, 13.0)
    columns = _runs(disc)
    check("the same circle through the frame is lopsided",
          columns != columns[::-1],
          f"{disc.count()} cells; the column runs are {columns[:3]}... at one "
          f"end and {columns[-3:]} at the other")


def a_circle_never_widens_as_it_goes_out() -> None:
    bad = []
    for radius in (4.0, 7.5, 11.0, 16.0, 23.5):
        columns = _runs(Mask.circle(W, L, 60.0, 60.0, radius))
        peak = columns.index(max(columns))
        left, right = columns[:peak + 1], columns[peak:]
        if left != sorted(left) or right != sorted(right, reverse=True):
            bad.append(f"r={radius}: {columns}")
    check("a circle's runs fall away and never come back", not bad,
          bad[0] if bad else
          "five radii, every one monotone out from the middle in both "
          "directions -- no run longer than the one before it")


# -- a copy that is turned ---------------------------------------------------


def a_turn_carries_the_state_round_with_it() -> None:
    cases = [
        ("minecraft:oak_stairs[facing=north,half=bottom,shape=straight]", 1,
         "minecraft:oak_stairs[facing=east,half=bottom,shape=straight]"),
        ("minecraft:oak_log[axis=x]", 1, "minecraft:oak_log[axis=z]"),
        ("minecraft:oak_log[axis=x]", 2, "minecraft:oak_log[axis=x]"),
        ("minecraft:glass_pane[east=true,north=false,south=false,west=false]", 1,
         "minecraft:glass_pane[east=false,north=false,south=true,west=false]"),
        ("minecraft:oak_sign[rotation=0]", 1, "minecraft:oak_sign[rotation=4]"),
        ("minecraft:rail[shape=north_south]", 1,
         "minecraft:rail[shape=east_west]"),
    ]
    bad = [f"{block} turned {q} gave {blocks.turned(block, q)}, wanted {want}"
           for block, q, want in cases if blocks.turned(block, q) != want]
    check("a quarter turn takes the block state with it", not bad,
          bad[0] if bad else
          f"{len(cases)} states -- facing, axis, the four sides, rotation and "
          "a rail shape -- all land where the geometry lands")


def a_state_with_no_rule_refuses() -> None:
    try:
        blocks.turned("minecraft:some_block[wobble=left]", 1)
    except ValueError as err:
        check("a state nothing has a rule for refuses to turn",
              "wobble" in str(err), str(err).split(". Add")[0])
        return
    check("a state nothing has a rule for refuses to turn", False,
          "an unknown state was carried across unchanged")


def a_placed_copy_lands_where_it_says_and_faces_the_new_way() -> None:
    frame = Frame((6.0, 6.0), 0.0)
    motif = frame.rect(W, L, 4.0, 16.0, 4.0, 12.0)
    canvas = Canvas(W, 8, L)
    canvas.fill(motif, 0, 4, "minecraft:stone")
    canvas.fill(motif.rim(), 4, 5,
                "minecraft:oak_stairs[facing=north,half=bottom,shape=straight]")

    x0, z0, x1, z1 = motif.bounds()
    canvas.place(motif, 0, 5, (60, 20), turn=1)

    # The turned copy occupies the box the other way round, and every stair in
    # it faces a quarter turn on from the one it was copied from.
    east = sum(1 for x in range(W) for z in range(L) for y in range(8)
               if canvas.get(x, y, z).startswith("minecraft:oak_stairs")
               and "facing=east" in canvas.get(x, y, z))
    north = sum(1 for x in range(W) for z in range(L) for y in range(8)
                if canvas.get(x, y, z).startswith("minecraft:oak_stairs")
                and "facing=north" in canvas.get(x, y, z))
    stood = sum(1 for x in range(60, 60 + (z1 - z0 + 1))
                for z in range(20, 20 + (x1 - x0 + 1))
                if canvas.get(x, 0, z) != "minecraft:air")
    check("a turned copy lands in its own box and faces the new way",
          east == north and east > 0 and stood == motif.count(),
          f"{stood} of {motif.count()} cells landed inside the "
          f"{z1 - z0 + 1}x{x1 - x0 + 1} box at (60, 20); {north} stairs face "
          f"north and {east} face east")


# -- the row that grades a stamp ---------------------------------------------


def _row(edit=None, rail: bool = False):
    """A three-copy run, optionally with one cell changed in copy two."""
    slope = lattice.Slope(3, 4)
    frame = Frame((10.0, 6.0), slope.angle)
    step = slope.times(4)
    motif = frame.rect(W, L, 4.0, 24.0, 6.0, 18.0)

    canvas = Canvas(W, 16, L)
    canvas.fill(motif, 0, 8, "minecraft:white_concrete")
    canvas.fill(motif.erode(1.5), 1, 7, "minecraft:air")
    if rail:
        # A joining block round the rim, which `finalize` will state from the
        # neighbours each copy actually has.
        canvas.fill(motif.rim(), 8, 9, "minecraft:oak_fence")
    canvas.stamp(motif, 0, 9, step, 2)
    if rail:
        canvas.finalize()
    if edit is not None:
        x, z = sorted(motif.cells())[len(motif.cells()) // 2]
        canvas.set(x + step[0] * edit, 3, z + step[1] * edit, "minecraft:stone")
    return canvas, motif, step


def the_row_passes_a_stamped_run() -> None:
    canvas, motif, step = _row()
    out = checks.copies(canvas, motif, 0, 9, step, 3)
    check("the row passes a run that was stamped", out["ok"],
          f"{out['copies']} copies, {out['distinct']} distinct over "
          f"{out['cells']} cells x {out['courses']} courses")


def the_row_catches_one_changed_cell() -> None:
    canvas, motif, step = _row(edit=2)
    out = checks.copies(canvas, motif, 0, 9, step, 3)
    check("the row catches a single changed block",
          not out["ok"] and out["distinct"] == 2 and out["differ"][2] == 1,
          f"{out['distinct']} distinct, copy 2 differs in "
          f"{out['differ'][2]} cell(s), first at {out['first'][1:4]}")


def joining_states_on_the_rim_are_excused_and_counted() -> None:
    """`finalize` states a fence by its neighbours, and the ends have others."""
    canvas, motif, step = _row(rail=True)
    out = checks.copies(canvas, motif, 0, 9, step, 3)
    check("a rim that joins is excused by name and counted",
          out["ok"] and out["excused"] > 0 and "fence" in out["families"],
          f"{out['excused']} rim cell(s) excused, "
          + ", ".join(f"{k} x{v}" for k, v in out["families"].items()))


# -- the manifest and the row it feeds ---------------------------------------


class _Rows:
    """A grader that only remembers what it was told."""

    def __init__(self):
        self.rows = []

    def add(self, name, ok, detail):
        self.rows.append((name, ok, detail))

    def ungraded(self, name, detail):
        self.rows.append((name, None, detail))


class _Config:
    pass


def _graded(sched, canvas, derived, **tables):
    g = _Rows()
    config = _Config()
    for key, value in tables.items():
        setattr(config, key, value)
    grader = object.__new__(grading.Grading)
    grader.c = config
    grader.repetition(g, canvas, sched, derived)
    return g.rows


def _declared_row(edit=None):
    canvas, motif, step = _row(edit=edit)
    sched = Schedule([Item("villa", "one section of the row",
                           "tbs_example.png: three the same",
                           blocks="minecraft:white_concrete", copies=3)])
    sched.declare("villa", motif.stamped(step, 2), 0, 9)
    sched.declare_repeat("villa", motif, 0, 9, step, 3)
    return sched, canvas


def the_gate_row_reads_a_declared_repeat() -> None:
    sched, canvas = _declared_row()
    derived = {"frame": {"slope": lattice.Slope(3, 4).to_json()}}
    rows = _graded(sched, canvas, derived)
    name, ok, detail = rows[0]
    check("the gate row passes a declared, stamped repeat",
          len(rows) == 1 and ok is True, f"{name}: {detail}")


def the_gate_row_fails_a_repeat_that_drifted() -> None:
    sched, canvas = _declared_row(edit=2)
    derived = {"frame": {"slope": lattice.Slope(3, 4).to_json()}}
    rows = _graded(sched, canvas, derived)
    name, ok, detail = rows[0]
    check("the gate row fails a copy that is not one", ok is False,
          f"{name}: {detail}")


def an_undeclared_build_is_ungraded_not_silent() -> None:
    sched = Schedule([Item("shell", "the whole thing", "photo-01.jpg")])
    canvas = Canvas(8, 4, 8)
    plain = _graded(sched, canvas, {})
    excused = _graded(sched, canvas, {}, UNREPEATED="a point tower and one "
                                                   "pavilion; nothing is twice")
    flagged = _graded(sched, canvas, {}, UNREPEATED=True)
    check("nothing declared is a row, not a silence",
          plain[0][1] is None and excused[0][1] is None
          and "pavilion" in excused[0][2] and "not a flag" in flagged[0][2],
          "unset -> [----] naming the mechanism; a reason -> [----] printing "
          "it; a bare True -> [----] refusing it")


def a_manifest_count_with_no_stamp_fails() -> None:
    sched = Schedule([Item("villa", "one of a row", "photo-01.jpg", copies=3)])
    canvas = Canvas(8, 4, 8)
    rows = _graded(sched, canvas, {})
    check("a count the build never stamped is red", rows[0][1] is False,
          rows[0][2][:96] + "...")


def the_schedule_survives_the_round_trip() -> None:
    sched, canvas = _declared_row()
    with tempfile.TemporaryDirectory() as where:
        path = Path(where) / "schedule.json"
        sched.save(path)
        back = Schedule.load(path)
    a, b = sched.repeated["villa"], back.repeated["villa"]
    check("a declared repeat survives being written and read",
          back.by_name["villa"].copies == 3 and b.step == a.step
          and b.count == a.count and b.motif.bits == a.motif.bits,
          f"copies {back.by_name['villa'].copies}, step {b.step}, "
          f"{b.motif.count()} cells of motif")


def main() -> int:
    motifs_are_what_they_say()
    only_short_motifs_are_offered()
    the_grid_is_dense_near_the_axes_and_sparse_near_45()
    choosing_prefers_a_period_it_can_repeat_on()
    an_unaffordable_snap_says_so()
    print()
    a_lattice_wall_repeats_exactly()
    a_measured_wall_does_not()
    a_section_authored_twice_lands_on_the_same_cells()
    the_same_two_sections_disagree_off_the_lattice()
    print()
    a_shift_loses_nothing_it_does_not_report()
    a_stamp_copies_the_blocks_and_the_holes()
    a_stamp_that_runs_out_of_canvas_stops()
    print()
    a_circle_on_the_grid_is_symmetric()
    a_circle_through_the_frame_is_not()
    a_circle_never_widens_as_it_goes_out()
    print()
    a_turn_carries_the_state_round_with_it()
    a_state_with_no_rule_refuses()
    a_placed_copy_lands_where_it_says_and_faces_the_new_way()
    print()
    the_row_passes_a_stamped_run()
    the_row_catches_one_changed_cell()
    joining_states_on_the_rim_are_excused_and_counted()
    print()
    the_gate_row_reads_a_declared_repeat()
    the_gate_row_fails_a_repeat_that_drifted()
    an_undeclared_build_is_ungraded_not_silent()
    a_manifest_count_with_no_stamp_fails()
    the_schedule_survives_the_round_trip()

    print()
    if failures:
        print(f"[lattice] {len(failures)} failed: {', '.join(failures)}")
        return 1
    print("[lattice] a lattice wall repeats, a stamp copies, and both checks "
          "still fail on the case they are for")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
