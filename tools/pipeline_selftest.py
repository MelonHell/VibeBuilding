"""The whole pipeline, four ways, against a building nobody built.

`tools/fixture.py` makes a synthetic two-wing building; this runs it through
derive, build and gate once per *kind of evidence* -- mapped, modelled,
described, vector -- and fails if any answerable check comes back red.

Four and not one, because the branches are what break. Every change to the
measuring code is written against whichever branch the building in front of you
happens to have, and the other three keep working right up until somebody runs
them. The declared branch in particular has no reference at all, so half the
apparatus is asked to say "cannot answer" rather than to answer, and the
difference between those two is the whole verdict model.

    python -m tools.pipeline_selftest
    python -m tools.pipeline_selftest --keep     # leave the scratch buildings

It writes into `buildings/_selftest_<kind>/` and deletes them afterwards, which
is ugly and is the price of the import path: a building is a package under
`buildings/`, and the scripts under test are the real ones, imported the real
way. A harness that copied the library somewhere else would be testing a copy.

What it does not check, and cannot: whether the described branch's *numbers* are
right. There is no reference there, so the declared height is both what the
build was made from and all there is to grade it against -- change it to
nineteen metres and all thirteen checks still pass, correctly. That is the
verdict model working, not a hole in it, and it is the reason a declared number
has to name its source: the source is the only thing that makes it checkable at
all, and the checking is done by a person.
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "buildings" / "_template"

# What each branch has to have written into its `derive.py` before it can run.
# The fixture is 80 x 14 at 12 m and 80 x 10 at 8 m, thirty degrees off the
# axes, with a floor line every 3 m -- see `tools/fixture.py`. The mesh also
# carries a 20 x 12 outbuilding at 6 m that the map and the vector plan do
# not draw. The modelled branch names it, because there the mesh is the plan.
# Everything below either names those parts or declares those numbers with a
# source, exactly as a real building would.
MAPPED = """
STRIPS = ("front", "back")
"""

MODELLED = """
MODEL_PARTS = ("front", "back", "outbuilding")
MESH_FLOOR = 2.0
REGISTER_FLOOR = 2.0
"""

DESCRIBED = """
from blockwright import declared as _declared

PLAN_ANGLE = 0.0
DECLARED_PLAN = [
    _declared.Rect("front", 0.0, 80.0, 0.0, 14.0,
                   source="brief.md: 'a long block eighty metres by fourteen'"),
    _declared.Rect("back", 0.0, 80.0, 24.0, 34.0,
                   source="brief.md: 'a lower wing behind it, ten deep'"),
]
DECLARED_HEIGHTS = {
    "front": {"top": 12.0, "source": "brief.md: 'four storeys'"},
    "back": {"top": 8.0, "source": "brief.md: 'the wing is lower, about eight'"},
}
DECLARED_STOREY = {
    "spacing": 3.0,
    "base": 0.0,
    "source": "brief.md: 'floor to floor about three metres'",
}
"""

# Same floors as the mapped branch, and for the same reason: the 6 m
# outbuilding is not on the vector plan, and a floor below its roof would
# register that extra mass as a stretched axis. The hole that documents is
# coverage_selftest's, not this harness's.
VECTOR = """
MODEL_PARTS = ("front", "back")
MESH_FLOOR = 6.0
REGISTER_FLOOR = 6.0
"""

BRIEF = """# The fixture

A long block of four storeys, eighty metres by fourteen, with a lower wing of
about eight metres behind it, ten metres deep, and a court between them. Floor
to floor about three metres.

Nothing here is real. It exists so that the described branch has a brief to be
read off, the way a real declared plan is read off one.
"""

SCENARIOS = (
    ("mapped", MAPPED, ("layout.png", "mesh")),
    ("modelled", MODELLED, ("model.obj",)),
    ("described", DESCRIBED, ("brief.md",)),
    ("vector", VECTOR, ("plan.geojson", "mesh")),
)


def build_inputs(where: Path, wants: tuple[str, ...]) -> None:
    """Give a scratch building exactly the inputs its branch is meant to have.

    The fixture writes a map and a mesh; the other two kinds are made from the
    same numbers here, so that all four branches describe one building and their
    answers can be compared with each other rather than only with themselves.
    """
    from tools import fixture

    scratch = where / "input"
    scratch.mkdir(parents=True, exist_ok=True)
    with contextlib.redirect_stdout(io.StringIO()):
        fixture.main([str(scratch),
                      str(where / "out" / "mesh-clip" / "merged.obj")])

    if "layout.png" not in wants:
        (scratch / "layout.png").unlink(missing_ok=True)
    if "model.obj" in wants:
        # The same geometry, offered as a model rather than as a capture. That
        # is the whole difference between the two branches: what the evidence
        # is *allowed to decide*, not what it contains.
        (where / "out" / "mesh-clip" / "merged.obj").replace(
            scratch / "model.obj")
    if "mesh" not in wants:
        shutil.rmtree(where / "out" / "mesh-clip", ignore_errors=True)
    if "brief.md" in wants:
        (scratch / "brief.md").write_text(BRIEF, encoding="utf-8")
    if "plan.geojson" in wants:
        write_vector(scratch / "plan.geojson")


def write_vector(path: Path) -> None:
    """The fixture's two wings as a GeoJSON in metres.

    In metres and not in longitude and latitude on purpose: the projection is
    tested by `vectorplan`'s own numbers, and what this branch is here to check
    is that a plan stated as rings rather than measured off pixels reaches the
    gate intact.
    """
    from tools import fixture

    def ring(u0, u1, v0, v1):
        corners = [(u0, v0), (u1, v0), (u1, v1), (u0, v1), (u0, v0)]
        return [list(fixture.rotate(u, v)) for u, v in corners]

    shapes = [{"type": "Feature",
               "properties": {"name": name},
               "geometry": {"type": "Polygon",
                            "coordinates": [ring(u0, u1, v0, v1)]}}
              for name, u0, u1, v0, v1, _ in fixture.PARTS]
    path.write_text(json.dumps({"type": "FeatureCollection",
                                "features": shapes}), encoding="utf-8")


def make(kind: str, extra: str, wants: tuple[str, ...]) -> Path:
    """A scratch building of one kind, ready to run."""
    where = ROOT / "buildings" / f"_selftest_{kind}"
    shutil.rmtree(where, ignore_errors=True)
    shutil.copytree(TEMPLATE, where,
                    ignore=shutil.ignore_patterns("__pycache__", "out"))
    # The configuration goes *above* the module's own tables being handed over,
    # not at the end of the file. Appended, it is assigned after `SURVEY` has
    # been built and after `main()` has run under `if __name__ == "__main__"`,
    # so the branch runs with every table still at its default -- which looks
    # from the outside exactly like the tables never being read at all.
    derive = where / "probes" / "derive.py"
    text = derive.read_text(encoding="utf-8")
    anchor = "def probes(out: dict, read, link) -> None:"
    if anchor not in text:
        raise SystemExit("the template's derive.py no longer defines `probes`; "
                         "this harness patches the file just above that line")
    derive.write_text(text.replace(anchor, extra + "\n\n" + anchor, 1),
                      encoding="utf-8")
    if kind == "modelled":
        # Same-file registration is skipped, so the gate falls back to
        # REGISTER_AT. At the template's 0.5 that floor is 6 m -- exactly the
        # outbuilding's roof -- and the mesh side of the fit drops it while
        # the build still has it. A storey lower keeps both sides on the
        # same three volumes.
        gate_py = where / "gate.py"
        gtext = gate_py.read_text(encoding="utf-8")
        if "REGISTER_AT = 0.5" not in gtext:
            raise SystemExit("the template's gate.py no longer sets "
                             "REGISTER_AT = 0.5; this harness patches that line")
        gate_py.write_text(
            gtext.replace("REGISTER_AT = 0.5", "REGISTER_AT = 0.4", 1),
            encoding="utf-8")
    build_inputs(where, wants)
    return where


def _ask_witnesses(expected=None, witness=None, iou=0.90):
    """`Survey.witnesses_of` against a fake pair, so the refusals do not need a building."""
    from blockwright.survey import Survey

    tables = SimpleNamespace(
        EXPECTED={} if expected is None else expected,
        WITNESS=witness,
    )
    read = SimpleNamespace(
        source=SimpleNamespace(name="map"),
        frame=SimpleNamespace(angle=30.0),
        drawn_bounds=lambda: (0.0, 80.0, 0.0, 34.0),
    )
    link = SimpleNamespace(
        kind="capture",
        frame=SimpleNamespace(angle=30.0),
    )
    evidence = SimpleNamespace(
        sources=(),
        witnesses_for=lambda question: (),
    )
    out = {
        "registration": {
            "needed": True,
            "u": {"mesh": [0.0, 80.0]},
            "v": {"mesh": [0.0, 34.0]},
            "square": {"as_fitted": iou},
        },
        "mesh": {"kind": "capture"},
        "storeys": {
            "by": "declared",
            "spacing": 3.0,
            "measured": {"found": True, "spacing": 3.1, "by": "capture"},
        },
    }
    Survey(None, tables).witnesses_of(out, evidence, read, link)
    return out


def prove_witness_ceiling(keep: bool = False) -> str | None:
    """The refusals and the greying, left where a reviewer can see them fail.

    Four things this task has to keep true, and a fifth that says what the
    switch actually turns off -- because a switch that greys more than it
    claims is the defect `JAGGED = None` already was, and one that claims
    more than it greys is the same sentence read the other way.
    """
    from blockwright.gate import Gate
    from blockwright.grading import Grading

    reason = ("the capture is of the real building in Miami; "
              "the map is a game map of a different tower")

    try:
        _ask_witnesses(expected={"plan overlap": "the silhouettes differ"})
    except SystemExit as why:
        text = str(why)
        if "cannot declare" not in text or "two buildings" not in text:
            return f"FAIL: plan-overlap ban said the wrong thing: {why}"
    else:
        return "FAIL: EXPECTED['plan overlap'] should have stopped the run"

    for blank in ("", "   ", True):
        try:
            _ask_witnesses(witness=blank)
        except SystemExit as why:
            if "sentence" not in str(why):
                return f"FAIL: empty WITNESS {blank!r} said the wrong thing: {why}"
        else:
            return f"FAIL: WITNESS = {blank!r} should have been refused"

    try:
        _ask_witnesses(iou=0.51)
    except SystemExit as why:
        text = str(why)
        if "0.51" not in text or "0.70" not in text or "WITNESS" not in text:
            return f"FAIL: low overlap said the wrong thing: {why}"
        if "two buildings" in text and "cannot declare" in text:
            return f"FAIL: low overlap reused the EXPECTED-ban text: {why}"
    else:
        return "FAIL: overlap 0.51 against a floor of 0.70 should have stopped the run"

    grey = _ask_witnesses(iou=0.51, witness=reason)
    questions = {row["question"] for row in grey["witnesses"]}
    wanted = {"plan overlap", "extent", "bearing", "storey height"}
    if not wanted <= questions:
        return f"FAIL: greying fixture lost rows: {questions}"
    for row in grey["witnesses"]:
        if row["ok"] is not None:
            return f"FAIL: {row['question']} stayed graded under WITNESS: {row}"
        if row["expected"] != reason:
            return (f"FAIL: {row['question']} carried {row['expected']!r} "
                    f"instead of the WITNESS sentence")

    # storey height is not a resemblance row. Greying it is the blast radius
    # of "every agreement row that asked the reference", and the proof has
    # to name it so a later narrowing cannot happen in silence.
    storey = next(r for r in grey["witnesses"] if r["question"] == "storey height")
    if storey["ok"] is not None:
        return "FAIL: storey height stayed graded under WITNESS"

    g = Gate("prove")
    Grading(None, None, SimpleNamespace()).witnesses(g, grey)
    printed = [c for c in g.checks if c.name.endswith(" agree")]
    if not printed:
        return "FAIL: grading.witnesses wrote no agree rows"
    for check in printed:
        if check.ok is not None or reason not in check.detail:
            return f"FAIL: gate did not print [----] with the sentence: {check.line()}"

    clean = _ask_witnesses(iou=0.90)
    overlap = next(r for r in clean["witnesses"] if r["question"] == "plan overlap")
    if overlap["ok"] is not True:
        return f"FAIL: overlap 0.90 should be green, got {overlap}"
    declared = _ask_witnesses(
        expected={"bearing": "the map stands on its own street grid"})
    bearing = next(r for r in declared["witnesses"] if r["question"] == "bearing")
    if bearing["ok"] is not None or "street grid" not in bearing["expected"]:
        return f"FAIL: EXPECTED['bearing'] should still declare, got {bearing}"
    still = next(r for r in declared["witnesses"] if r["question"] == "plan overlap")
    if still["ok"] is not True:
        return f"FAIL: declaring bearing greys plan overlap too: {still}"

    extra = MAPPED + f"\nWITNESS = {reason!r}\n"
    where = make("witness", extra, ("layout.png", "mesh"))
    try:
        for step in ("probes.derive", "build", "gate"):
            code, said = run("witness", step)
            if code != 0:
                return f"FAIL: witness-set {step} died:\n{said[-2000:]}"
        derived = json.loads((where / "out" / "derived.json").read_text(encoding="utf-8"))
        if not derived.get("witnesses"):
            return "FAIL: mapped fixture wrote no witness rows to grey"
        for row in derived.get("witnesses", []):
            if row.get("ok") is not None or row.get("expected") != reason:
                return (f"FAIL: derive under WITNESS left {row.get('question')} "
                        f"ok={row.get('ok')} expected={row.get('expected')!r}")
        report = json.loads((where / "out" / "report.json").read_text(encoding="utf-8"))
        checks = {c["name"]: c for c in report.get("checks", [])}
        agrees = [n for n in checks if n.endswith(" agree")]
        if not agrees:
            return "FAIL: mapped fixture wrote no agree rows to grey"
        for name in agrees:
            row = checks[name]
            if row.get("ok") is not None or reason not in (row.get("detail") or ""):
                return f"FAIL: gate row {name} was not greyed with the sentence: {row}"
        registration = checks.get("registration")
        if registration is None or registration.get("ok") is not True:
            return f"FAIL: registration should still grade under WITNESS, got {registration}"
        scale = checks.get("scale")
        if scale is None or scale.get("ok") is None:
            return f"FAIL: scale should still grade under WITNESS, got {scale}"
        # The section is the reference witnessing the build. WITNESS does not
        # turn it off -- that is the blast radius this proof is here to name.
        if not report.get("sections"):
            return ("FAIL: no sections were cut under WITNESS; "
                    "the switch greys more than the agreement rows")
        stayed = [n for n, c in checks.items()
                  if c.get("ok") is True
                  and not n.endswith(" agree")]
        print("witness ceiling: refusals hold; greying holds; "
              f"section/registration/scale still grade ({len(stayed)} other green rows)",
              flush=True)
    finally:
        if not keep:
            shutil.rmtree(where, ignore_errors=True)
    return None


def run(kind: str, step: str) -> tuple[int, str]:
    """One stage of one scratch building, as a person would run it."""
    done = subprocess.run(
        [sys.executable, "-m", f"buildings._selftest_{kind}.{step}"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace")
    return done.returncode, (done.stdout or "") + (done.stderr or "")


def main(argv: list[str]) -> int:
    keep = "--keep" in argv
    bad = 0
    print("-- witness ceiling")
    failed = prove_witness_ceiling(keep=keep)
    if failed:
        print("   " + failed)
        bad += 1
    for kind, extra, wants in SCENARIOS:
        print(f"-- {kind}")
        try:
            make(kind, extra, wants)
        except Exception as why:                    # noqa: BLE001
            print(f"   could not be set up: {why}")
            bad += 1
            continue
        for step in ("probes.derive", "build", "gate"):
            code, said = run(kind, step)
            if code != 0:
                print(f"   {step} failed:")
                print("\n".join("     " + line
                                for line in said.strip().splitlines()[-12:]))
                bad += 1
                break
            if step == "gate":
                # The gate's own rows are the verdict, and `[FAIL]` is the only
                # thing this harness treats as a regression. `[----]` is not:
                # an ungraded row is a question the fixture genuinely cannot
                # answer -- it has no photographs, so nothing states how many
                # houses a terrace divides into -- and turning that red would
                # train whoever runs this to ignore the output.
                red = [line.strip() for line in said.splitlines()
                       if line.startswith("[FAIL]")]
                summary = [line.strip() for line in said.splitlines()
                           if "answerable check" in line]
                print("   " + (summary[-1] if summary
                               else said.strip().splitlines()[-1]))
                for line in red:
                    print("     " + line)
                bad += bool(red)
        if not keep:
            shutil.rmtree(ROOT / "buildings" / f"_selftest_{kind}",
                          ignore_errors=True)

    # And the buildings that actually exist, which the four synthetic branches
    # above do not stand in for. They are built from `_template`, and the
    # template calls a strict subset of the library: not `site`, not
    # `paths.Layout`, not `roof`, not `palm`. Everything a real building leans
    # on beyond that subset was outside this harness until this call, and the
    # price of that was six buildings dead on import for a fortnight with this
    # test green.
    print()
    from . import lint_buildings
    bad += lint_buildings.main()

    print()
    if bad:
        print(f"{bad} of {len(SCENARIOS)} branches did not come through clean.")
        return 1
    print(f"all {len(SCENARIOS)} branches run end to end and grade clean")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))