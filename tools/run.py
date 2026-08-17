"""One command for a round, and only what changed since the last one.

    python -m tools.run <name>              measure, build, grade
    python -m tools.run <name> --quick      skip the renders and the sheets
    python -m tools.run <name> --full       print every row, not just the moved ones
    python -m tools.run <name> --build      skip the measuring, build and grade
    python -m tools.run <name> --gate       grade what is already built
    python -m tools.run <name> --remeasure  measure again even if nothing moved
    python -m tools.run <name> --review     and render the review folder after
    python -m tools.run <name> --manual     stop when a gate is waiting for a person
    python -m tools.run <name> --auto       the default: do not wait

A round of work on a building is `derive`, `build`, `gate`, read the output,
change one number, repeat. The three commands are cheap; reading their output is
not. Between two rounds almost every line is identical, and the two or three
lines that moved are the entire content of the run -- so this prints those, and
says how many it did not print.

That is a saving in attention rather than in seconds, and attention is the
scarce one. A gate report is thirty rows and a section is two hundred stations;
a round that moved one station from red to green produces one line here.

`--quick` also skips everything only a person looks at -- the renders, the plan
cuts, the comparison sheets -- which is most of the build's wall clock and none
of what the gate reads. Use it while chasing numbers; drop it before looking at
anything, and before a review.

Nothing here decides anything or touches a building's files. It runs the same
three modules a person would run, in the same order, and diffs two reports.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Numbers this far apart are the same number. A section's worst station is
# reported to the centimetre and moves by a centimetre when nothing has changed,
# and a delta that reports noise is a delta nobody reads.
EPSILON = 0.01

# What a run exits with when it has stopped on purpose. Distinct from 1, which
# means something failed: a gate waiting for a person is not a failure, and a
# caller that cannot tell the two apart will either treat every pause as a
# breakage or every breakage as a pause.
WAITING = 10

# Stages this runner would do *after* a gate. A pause at that gate must not
# run them -- otherwise "nothing after this gate runs until you do" is a
# caption on a run that already did the next thing. Gate 5 is the last
# review; nothing here sits after it, so a wait there still rebuilds.
AFTER = {
    1: ("probes.derive", "build", "gate", "review"),
    4: ("build", "gate", "review"),
    5: (),
}


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def rows(report: dict) -> dict[str, tuple]:
    """Every graded thing in a report, keyed by name.

    Flat on purpose: a check, a section and a structural count are different
    shapes in the file and the same kind of thing to a reader -- something with
    a name that was true last time and may not be now.
    """
    out: dict[str, tuple] = {}
    for check in report.get("checks", ()):
        out["check " + check.get("name", "?")] = (check.get("ok"),
                                                  check.get("detail", ""))
    for section in report.get("sections", ()):
        out["section " + section.get("name", "?")] = (
            section.get("ok"),
            f"{section.get('misses', 0)} of {section.get('graded', 0)} outside, "
            f"worst {section.get('worst', 0):.2f} m")
    structure = report.get("structure", {})
    for key in ("pieces", "strays", "stray_blocks", "floating", "free_ends"):
        if key in structure:
            out["structure " + key] = (None, structure[key])
    return out


def same(a, b) -> bool:
    """Whether two report values are the same to a reader.

    Floats compare within `EPSILON`, and a detail string that differs only in
    its numbers by less than that is still a change worth hiding -- but only the
    numeric part is compared loosely, so "0 outside" becoming "1 outside" is
    always a change.
    """
    if isinstance(a, float) and isinstance(b, float):
        return abs(a - b) < EPSILON
    return a == b


def delta(before: dict, after: dict) -> tuple[list[str], int]:
    """The lines worth reading, and how many were identical."""
    old, new = rows(before), rows(after)
    moved: list[str] = []
    still = 0
    for name in new:
        if name not in old:
            moved.append(f"  new    {name}: {new[name][1]}")
            continue
        was_ok, was = old[name]
        now_ok, now = new[name]
        if same(was_ok, now_ok) and same(was, now):
            still += 1
            continue
        mark = {True: "pass", False: "FAIL", None: "----"}.get(now_ok, "    ")
        moved.append(f"  {mark}   {name}: {now}")
        if not same(was, now):
            moved.append(f"         was: {was}")
    for name in old:
        if name not in new:
            moved.append(f"  gone   {name}")
    return moved, still


def verdict(report: dict) -> str:
    checks = report.get("checks", ()) or ()
    red = [c for c in checks if c.get("ok") is False]
    grey = [c for c in checks if c.get("ok") is None]
    green = len(checks) - len(red) - len(grey)
    return (f"{report.get('verdict', '?')}: {green} pass, {len(red)} fail, "
            f"{len(grey)} ungraded")


def kept(building: str, step: str) -> Path:
    """Where the last run's output for one stage is remembered.

    Under the building's own `out/`, which is the directory that can be deleted
    and regenerated in full -- a lost cache costs one verbose run and nothing
    else.
    """
    return ROOT / "buildings" / building / "out" / "last-run" / f"{step}.txt"


def keep(building: str, step: str, said: str) -> None:
    path = kept(building, step)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(said, encoding="utf-8")


def shifted(building: str, step: str, said: str) -> list[str]:
    """The lines of a stage's output that are not what it said last time.

    A unified diff without the context: two rounds of work produce two hundred
    identical lines and four that moved, and the four are the whole content of
    the round. Marked `-` and `+` so a number that changed reads as a number
    that changed rather than as a fresh fact.
    """
    import difflib

    path = kept(building, step)
    was = path.read_text(encoding="utf-8") if path.exists() else None
    keep(building, step, said)
    if was is None:
        return [line for line in said.strip().splitlines()]
    out = [line.rstrip() for line in difflib.unified_diff(
        was.splitlines(), said.splitlines(), lineterm="", n=0)
        if line[:1] in "-+" and not line.startswith(("---", "+++"))]
    return out


def fingerprint(building: str) -> str:
    """What `probes/derive.py` would be reading, as one string.

    Every input file, the probe itself, and the whole of `blockwright` -- the
    last one because most of the runs where this matters are runs where the
    library is what changed, and a cache that missed those would hand back
    measurements from the code being edited.

    Size and modification time rather than contents: a capture is a hundred
    megabytes and hashing it costs more than the measurement it would save.
    """
    parts = []
    here = ROOT / "buildings" / building
    for path in sorted([*(here / "input").rglob("*"),
                        *(here / "out" / "mesh-clip").rglob("*"),
                        *(here / "probes").glob("*.py"),
                        *(ROOT / "blockwright").glob("*.py")]):
        if path.is_file():
            stat = path.stat()
            parts.append(f"{path.name}:{stat.st_size}:{int(stat.st_mtime)}")
    return "\n".join(parts)


def unchanged(building: str) -> bool:
    """Whether the measuring stage can be skipped this round.

    Only ever skips `derive`, and only when nothing it reads has moved. The
    build and the gate always run: they are what the round is about, and the
    gate has its own freshness check against every input, which this must never
    be allowed to satisfy falsely.
    """
    key = kept(building, "derive.key")
    now = fingerprint(building)
    was = key.read_text(encoding="utf-8") if key.exists() else None
    if was == now:
        return True
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text(now, encoding="utf-8")
    return False


def journal_of(building: str) -> Path:
    """The building's journal, via its own paths -- not a string kept here.

    The journal lives above `out/` and is named on the skeleton as
    `paths.FINDINGS`. A second hardcoded path would be the same mistake as a
    state file: two stories about one fact, out of step when it matters.
    """
    import importlib

    from blockwright import findings

    try:
        paths = importlib.import_module(f"buildings.{building}.paths")
    except ModuleNotFoundError:
        return ROOT / "buildings" / building / "findings.md"
    return findings.path_of(paths)


def waiting_at(building: str, journal: Path | None = None) -> int | None:
    """The first gate whose agent loop has closed and whose human loop has not.

    Read off the journal rather than off a state file. A state file is a second
    account of what happened, and the two go out of step exactly when it matters
    -- the journal already says which rounds ran and who ran them.

    The agent's loop closes on an empty round. A loop with no empty round at
    the end was abandoned rather than finished, and from outside the two look
    identical. The human's has not closed when it has never run, or when its
    last round still has findings -- that is the look-again after a fix.
    """
    from blockwright import findings

    path = Path(journal) if journal is not None else journal_of(building)
    if not path.exists():
        return None
    ran = findings.rounds(path)
    for gate in (1, 4, 5):
        # rounds() is (gate, number, who, count). Filter the gate first --
        # asking every gate the same rounds is how this always answered 1.
        agent = [r for r in ran if r[0] == gate and r[2] == "agent"]
        human = [r for r in ran if r[0] == gate and r[2] == "human"]
        if agent and not agent[-1][3] and (not human or human[-1][3]):
            return gate
    return None


def announce(building: str, gate: int) -> None:
    """What to open, and what the agent already found at this gate."""
    from blockwright import findings

    journal = journal_of(building)
    here = ROOT / "buildings" / building / "out"
    print()
    print(f"gate {gate} is waiting for you. The agent loop closed; "
          "yours has not.")
    if gate == 1:
        print(f"  open   {here / 'mesh-clip' / 'orthos'}")
    elif gate == 4:
        print(f"  open   {here / 'greybox' / 'top.png'}")
        print(f"  and    {here / 'greybox' / 'review'}")
    else:
        print(f"  open   {here / 'review'}")
    print(f"  agent findings are in {journal}")
    for one in findings.read(journal):
        if one.gate == gate and one.by != "human":
            print(f"    {one.id} | {one.state} | {one.title()}")
    print("Say what it missed, or say it is fine. Nothing after this "
          "gate runs until you do.")


def stage(building: str, step: str, quick: bool) -> tuple[int, str, float]:
    env = dict(os.environ)
    if quick:
        env["BLOCKWRIGHT_QUICK"] = "1"
    command = [sys.executable, "-m", f"buildings.{building}.{step}"]
    if step == "review":
        # Without the reference renders a review is the build against the
        # photographs alone, which is half a review and reads like a whole one.
        command.append("--mesh")
    started = time.time()
    done = subprocess.run(
        command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
        errors="replace", env=env)
    return (done.returncode,
            (done.stdout or "") + (done.stderr or ""),
            time.time() - started)


def main(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("-")]
    if not names:
        print(__doc__.strip().splitlines()[0])
        print("    python -m tools.run <name> [--quick] [--full] "
              "[--build] [--gate] [--manual|--auto]")
        return 2
    building = names[0].strip("/\\").replace("buildings/", "")
    quick = "--quick" in argv
    full = "--full" in argv
    # --auto is the default and changes nothing. --manual is scaffolding: the
    # end state is auto only, and what the flag is for is measuring the gap
    # between what the agent's eyes catch and what a person's do.
    manual = "--manual" in argv

    steps = ["probes.derive", "build", "gate"]
    if "--gate" in argv:
        steps = ["gate"]
    elif "--build" in argv:
        steps = ["build", "gate"]
    if "--review" in argv:
        # The eyes, at the end of the numbers and never instead of them. A red
        # gate stops the loop below before this runs: there is no sense spending
        # a reviewer on a build the numbers have already rejected.
        steps.append("review")

    if manual:
        blocked = waiting_at(building)
        if blocked is not None:
            later = AFTER[blocked]
            steps = [step for step in steps if step not in later]

    where = ROOT / "buildings" / building / "out" / "report.json"
    before = load(where)

    if ("probes.derive" in steps and "--remeasure" not in argv
            and (ROOT / "buildings" / building / "out" / "derived.json").exists()
            and unchanged(building)):
        steps.remove("probes.derive")
        print("-- probes.derive skipped: no input, probe or library file has "
              "moved since it last ran (--remeasure to force)")

    for step in steps:
        code, said, took = stage(building, step, quick)
        if code != 0:
            print(f"-- {step} failed after {took:.1f} s")
            print(said.strip())
            return 1
        if full:
            print(f"-- {step} {took:.1f} s")
            print(said.strip())
            keep(building, step, said)
            continue

        # What derive and build print is measurements and tallies -- the reason
        # the gate moved, and identical between rounds except in the two or
        # three lines that are the point. So the previous run's output is kept
        # and only the difference is shown.
        changed = shifted(building, step, said)
        print(f"-- {step} {took:.1f} s" + ("" if changed else ", same output"))
        for line in changed:
            print("   " + line)

    if manual:
        blocked = waiting_at(building)
        if blocked is not None:
            announce(building, blocked)
            return WAITING

    after = load(where)
    if not after:
        print("no report.json to compare")
        return 1

    print()
    print(verdict(after))
    if not before:
        print("  first run: nothing to compare against")
        return 0

    moved, still = delta(before, after)
    if not moved:
        print(f"  nothing moved ({still} rows identical to the last run)")
        return 0
    print(f"  {len(moved)} line(s) moved, {still} unchanged:")
    for line in moved:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
