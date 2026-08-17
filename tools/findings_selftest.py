"""The journal parses, and the audit catches what a journal hides.

Four failures, each of which has happened: a finding closed with no account of
what would prevent it, a loop with no empty round at the end, a rejection raised
again and again, and a heading the parser cannot read at all -- which turns a
ledger into prose without saying so.

    python -m tools.findings_selftest
"""

from __future__ import annotations

import contextlib
import io
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from blockwright import findings
from blockwright.reviewing import LEFTOVER, Review
from tools import run

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "buildings" / "_template"

LEDGER = """\
# fixture -- review journal

## Gate 4 -- greybox

### Round 1 -- agent
- F-01 the north wing is shorter than the south; the reference has them level

### Round 2 -- agent
- F-01 closed, confirmed on 01-high-front

### Round 3 -- agent

### Round 1 -- human
- F-02 the club wing has balconies; the photographs show it blank

### F-01 | built | gate 4 | round 1 | seen 1 | by: reviewer
**The north wing is shorter than the south.**

**Fixed:** the wing was extruded to the median of the whole plan rather than to
its own skyline. It now reads `skyline["north_wing"]`.

**Arose from:** the skeleton's `shell()` takes one top for every part.

**Prevented by:** a gate row comparing each part's built top against its own
measured skyline. -- gate row

### F-02 | open | gate 4 | round 1 | seen 1 | by: human
**The club wing has balconies; the photographs show it blank.**
"""

# Two gates, because a chronicle that forgot which heading a round sat under
# would hand every gate the same three rounds and the next reader would always
# answer gate 1.
TWO_GATES = """\
# fixture -- two gates

## Gate 4 -- greybox

### Round 1 -- agent
- F-01 the north wing is short

### Round 2 -- agent

## Gate 5 -- photo

### Round 1 -- agent
- F-10 the tower reads blue

### F-01 | built | gate 4 | round 1 | seen 1 | by: reviewer
**The north wing is short.**

**Fixed:** raised it.

**Arose from:** one top for every part.

**Prevented by:** a gate row.

### F-10 | open | gate 5 | round 1 | seen 1
**The tower reads blue.**
"""

CLOSED_BARE = """\
# fixture -- closed with no Prevented by

## Gate 4 -- greybox

### Round 1 -- agent

### F-01 | built | gate 4 | round 1 | seen 1 | by: reviewer
**The north wing is short.**

**Fixed:** raised it.

**Arose from:** one top for every part.
"""

UNFINISHED = """\
# fixture -- abandoned loop

## Gate 4 -- greybox

### Round 1 -- agent
- F-01 the north wing is short

### F-01 | open | gate 4 | round 1 | seen 1 | by: reviewer
**The north wing is short.**
"""

STALE = """\
# fixture -- rejection that keeps coming back

## Gate 4 -- greybox

### Round 1 -- agent

### F-01 | rejected | gate 4 | round 1 | seen 3 | by: reviewer
**The tower is too short.**

**Fixed:** the section says 67 m.

**Arose from:** the capture looks taller than it is.

**Prevented by:** nothing -- the capture is the height.
"""

PROSE = """\
# fixture -- a review with no ledger

The north wing is shorter than the south; the reference has them level.
"""

CLEAN = """\
# fixture -- finished

## Gate 4 -- greybox

### Round 1 -- agent
- F-01 the north wing is short

### Round 2 -- agent

### Round 1 -- human

### F-01 | built | gate 4 | round 1 | seen 1 | by: reviewer
**The north wing is short.**

**Fixed:** raised it.

**Arose from:** one top for every part.

**Prevented by:** a gate row.
"""


def fail(msg: str) -> int:
    print(f"FAIL: {msg}")
    return 1


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "findings.md"

        path.write_text(LEDGER, encoding="utf-8")
        found = findings.read(path)
        if len(found) != 2:
            return fail(f"parsed {len(found)} finding(s), expected 2")
        if found[1].by != "human":
            return fail(f"F-02 read as by {found[1].by!r}, expected 'human'")
        if found[0].gate != 4:
            return fail(f"F-01 read gate {found[0].gate}, expected 4")
        if "skyline" not in found[0].field("Fixed"):
            return fail("the Fixed field did not parse")
        if found[0].field("Prevented by") == "":
            return fail("the Prevented by field did not parse")

        said = findings.audit(path)
        if not any("human loop has no empty round" in line for line in said):
            return fail("audit missed the unfinished human loop")
        if any("agent loop has no empty round" in line for line in said):
            return fail("audit flagged a finished agent loop")
        if any("F-01" in line and "prevented" in line.lower() for line in said):
            return fail("audit complained about a finding that has the field")

        path.write_text(TWO_GATES, encoding="utf-8")
        ran = findings.rounds(path)
        expect = [
            (4, 1, "agent", 1),
            (4, 2, "agent", 0),
            (5, 1, "agent", 1),
        ]
        if ran != expect:
            return fail(f"rounds() lost the gate: {ran}")
        if findings.read(path)[1].by != "reviewer":
            return fail("omitted by: did not default to reviewer")
        said = findings.audit(path)
        if not any("agent loop has no empty round" in line and "gate 5" in line
                   for line in said):
            return fail("audit missed the unfinished agent loop at gate 5")
        if any("gate 4" in line and "no empty round" in line for line in said):
            return fail("audit flagged a finished loop at gate 4")

        # Each rule has to fail on a journal that has the defect and pass on
        # one that does not. A rule that cannot fail is a rule that is not
        # there.
        path.write_text(CLOSED_BARE, encoding="utf-8")
        said = findings.audit(path)
        if not any("F-01" in line and "prevented" in line.lower()
                   for line in said):
            return fail("audit missed a closed finding with no Prevented by")

        path.write_text(UNFINISHED, encoding="utf-8")
        said = findings.audit(path)
        if not any("agent loop has no empty round" in line for line in said):
            return fail("audit missed an abandoned agent loop")

        path.write_text(STALE, encoding="utf-8")
        said = findings.audit(path)
        if not any("rejected and raised again" in line for line in said):
            return fail("audit missed a rejection raised three times")
        if any("prevented" in line.lower() for line in said):
            return fail("audit complained about a rejection that has the field")

        path.write_text(PROSE, encoding="utf-8")
        said = findings.audit(path)
        if not any("no ledger headings" in line for line in said):
            return fail("audit missed a journal the parser cannot read")

        path.write_text(CLEAN, encoding="utf-8")
        said = findings.audit(path)
        if any("no empty round" in line for line in said):
            return fail("audit flagged a finished loop")
        if any("prevented" in line.lower() for line in said):
            return fail("audit flagged a finding that has Prevented by")
        if any("rejected and raised again" in line for line in said):
            return fail("audit invented a stale rejection")
        if any("no ledger headings" in line for line in said):
            return fail("audit did not read a well-formed ledger")
        if any("no gate sections" in line for line in said):
            return fail("audit invented a missing gate on a journal that has one")

        # A round with no gate heading over it is not a thing, and must not
        # be counted under every gate the next reader asks about. The audit
        # has to say so: dropping the rounds and staying silent is how a
        # loop that never closed looks finished.
        path.write_text(
            "### Round 1 -- agent\n- F-01 stray\n", encoding="utf-8")
        if findings.rounds(path):
            return fail("rounds() accepted a round outside a gate")
        said = findings.audit(path)
        if not any("no gate sections, so the loops cannot be judged" in line
                   for line in said):
            return fail("audit was silent about a chronicle with no gate")

        if prove_clear(Path(tmp) / "frozen") != 0:
            return 1

        if prove_waiting(path) != 0:
            return 1

    if prove_pause() != 0:
        return 1

    print("ledger parses, audit catches all four")
    print("manual mode waits at the closed agent gate and leaves --auto alone")
    return 0


def prove_waiting(path: Path) -> int:
    """The waiting state is read off the chronicle, per gate.

    The brief's first sketch asked every gate the same rounds and always
    answered 1. These journals are why that is a test and not a comment.
    """
    path.write_text(TWO_GATES, encoding="utf-8")
    if run.waiting_at("x", path) != 4:
        return fail(f"TWO_GATES should wait at 4, got {run.waiting_at('x', path)}")

    path.write_text(CLEAN, encoding="utf-8")
    if run.waiting_at("x", path) is not None:
        return fail("a closed human loop should not wait")

    path.write_text(UNFINISHED, encoding="utf-8")
    if run.waiting_at("x", path) is not None:
        return fail("an abandoned agent loop should not wait")

    path.write_text(LEDGER, encoding="utf-8")
    if run.waiting_at("x", path) != 4:
        return fail("an open human loop should wait at 4")

    path.write_text(CLOSED_BARE, encoding="utf-8")
    if run.waiting_at("x", path) != 4:
        return fail("an empty agent round and no human should wait at 4")

    # Gate 4 fully closed, gate 5's agent closed -- the first wait is 5,
    # not 1 and not 4. This is the case the ungated parser could not see.
    path.write_text(
        "# fixture -- next gate\n\n"
        "## Gate 4 -- greybox\n\n"
        "### Round 1 -- agent\n\n"
        "### Round 1 -- human\n\n"
        "## Gate 5 -- photo\n\n"
        "### Round 1 -- agent\n\n",
        encoding="utf-8")
    if run.waiting_at("x", path) != 5:
        return fail(f"closed gate 4 should expose gate 5, "
                    f"got {run.waiting_at('x', path)}")
    print("  waiting_at reads the gate off the chronicle")
    return 0


def prove_pause() -> int:
    """`--manual` stops before the full build; `--auto` and no flag do not.

    A stub building, not a fixture, so the proof does not spend a derive
    and does not depend on one being left on disk. Also a look-again (the
    human finding that forced the pause must print) and a wait at gate 5
    (that one still rebuilds).
    """
    name = "_selftest_manual"
    where = ROOT / "buildings" / name
    shutil.rmtree(where, ignore_errors=True)
    where.mkdir()
    (where / "__init__.py").write_text("", encoding="utf-8")
    shutil.copy(TEMPLATE / "paths.py", where / "paths.py")
    (where / "probes").mkdir()
    (where / "probes" / "__init__.py").write_text("", encoding="utf-8")
    (where / "probes" / "derive.py").write_text(
        "def main():\n"
        "    print('derive-ran')\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8")
    (where / "build.py").write_text(
        "from pathlib import Path\n"
        "HERE = Path(__file__).resolve().parent\n"
        "def main(argv=None):\n"
        "    (HERE / 'out').mkdir(exist_ok=True)\n"
        "    (HERE / 'out' / 'BUILT').write_text('yes', encoding='utf-8')\n"
        "    print('build-ran')\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8")
    (where / "gate.py").write_text(
        "def main():\n"
        "    print('gate-ran')\n"
        "if __name__ == '__main__':\n"
        "    main()\n",
        encoding="utf-8")
    (where / "findings.md").write_text(
        "# scratch -- review journal\n\n"
        "## Gate 4 -- greybox\n\n"
        "### Round 1 -- agent\n\n",
        encoding="utf-8")
    built = where / "out" / "BUILT"
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run.main([name, "--manual"])
        said = buf.getvalue()
        if code != run.WAITING:
            return fail(f"--manual returned {code}, not WAITING")
        if "gate 4 is waiting for you" not in said:
            return fail("--manual did not say the gate is waiting:\n" + said)
        if built.exists():
            return fail("--manual ran the full build while gate 4 was waiting")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run.main([name])
        if code == run.WAITING:
            return fail("a normal run returned WAITING")
        if not built.exists():
            return fail("a normal run did not reach the build")

        built.unlink()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run.main([name, "--auto"])
        if code == run.WAITING:
            return fail("--auto returned WAITING")
        if not built.exists():
            return fail("--auto did not reach the build")

        # Look-again: last human round still has findings. The pause must
        # print that finding -- it is why the gate is waiting -- and must
        # name the rebuild, not just --manual again.
        (where / "findings.md").write_text(LEDGER, encoding="utf-8")
        built.unlink()
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run.main([name, "--manual"])
        said = buf.getvalue()
        if code != run.WAITING:
            return fail(f"look-again returned {code}, not WAITING")
        if "yours:" not in said or "F-02" not in said:
            return fail("look-again did not print the human finding:\n" + said)
        if f"python -m buildings.{name}.build --greybox" not in said:
            return fail("gate 4 announce did not name build --greybox:\n" + said)
        if f"python -m buildings.{name}.review --greybox --mesh" not in said:
            return fail("gate 4 announce did not name review --greybox:\n" + said)
        if built.exists():
            return fail("look-again at gate 4 ran the full build")

        # Gate 5 is the last review: AFTER[5] is empty, so a wait there
        # still rebuilds. That is the opposite of gate 4, and cheap to lose.
        (where / "findings.md").write_text(
            "# scratch -- gate 5\n\n"
            "## Gate 4 -- greybox\n\n"
            "### Round 1 -- agent\n\n"
            "### Round 1 -- human\n\n"
            "## Gate 5 -- photo\n\n"
            "### Round 1 -- agent\n\n",
            encoding="utf-8")
        # Look-again left BUILT gone. A wait at 5 must write it: AFTER[5]
        # is empty, so the build still runs.
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run.main([name, "--manual"])
        said = buf.getvalue()
        if code != run.WAITING:
            return fail(f"gate 5 wait returned {code}, not WAITING")
        if "gate 5 is waiting for you" not in said:
            return fail("gate 5 wait did not say the gate is waiting:\n" + said)
        if not built.exists():
            return fail("a wait at gate 5 skipped the build")
        if f"python -m buildings.{name}.build --greybox" in said:
            return fail("gate 5 announce named a greybox rebuild:\n" + said)
        if f"python -m buildings.{name}.review --mesh" not in said:
            return fail("gate 5 announce did not name review --mesh:\n" + said)

        # A real failure is still 1, even under --manual: the pause is not
        # a blanket for every stop.
        (where / "probes" / "derive.py").write_text(
            "import sys\n"
            "print('derive-broke')\n"
            "sys.exit(1)\n",
            encoding="utf-8")
        # An unfinished agent loop must not pause, so the failure is visible.
        (where / "findings.md").write_text(
            "# scratch -- abandoned\n\n"
            "## Gate 4 -- greybox\n\n"
            "### Round 1 -- agent\n"
            "- F-01 still open\n",
            encoding="utf-8")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = run.main([name, "--manual"])
        if code != 1:
            return fail(f"--manual on a failed derive returned {code}, not 1")
        print("  --manual exits 10 and does not run the next stage; "
              "--auto and a failure do not")
        return 0
    finally:
        shutil.rmtree(where, ignore_errors=True)


def prove_clear(here: Path) -> int:
    """What `clear` does to a directory that still holds an old-form journal.

    The freeze is that the text is not rewritten and is not the new file.
    Destroying it is not the freeze.
    """
    review = here / "out" / "review"
    review.mkdir(parents=True)
    old = ("# 009 -- review ledger\n\n"
           "### F-01 | built | round 1 | seen 1\n"
           "**The open two-storey base is missing.**\n")
    leftover = review / "findings.md"
    leftover.write_text(old, encoding="utf-8")
    (review / "01-high-front.build.png").write_bytes(b"png")
    (review / "prompt.txt").write_text("old prompt", encoding="utf-8")

    paths = SimpleNamespace(HERE=here, OUT=here / "out",
                            FINDINGS=here / "findings.md")
    Review(paths, (), "a building", where=review).clear()

    aside = here / LEFTOVER
    if not aside.is_file():
        return fail("clear() did not move the leftover journal out of out/")
    if aside.read_text(encoding="utf-8") != old:
        return fail("clear() rewrote the leftover journal")
    if (here / "findings.md").exists():
        return fail("clear() migrated the leftover onto the new journal path")
    if leftover.exists():
        return fail("clear() left findings.md in the review folder after a move")
    if (review / "01-high-front.build.png").exists():
        return fail("clear() left a render in the review folder")
    if (review / "prompt.txt").exists():
        return fail("clear() left prompt.txt in the review folder")
    print(f"  leftover journal moved to {aside.name}, text unchanged")

    leftover.write_text(old, encoding="utf-8")
    Review(paths, (), "a building", where=review).clear()
    if aside.read_text(encoding="utf-8") != old:
        return fail("a second clear() overwrote the copy already moved aside")
    if not leftover.is_file() or leftover.read_text(encoding="utf-8") != old:
        return fail("a second clear() unlinked the leftover it no longer owns")
    print("  second clear() left both copies alone")
    return 0


if __name__ == "__main__":
    sys.exit(main())
