"""The journal parses, and the audit catches what a journal hides.

Four failures, each of which has happened: a finding closed with no account of
what would prevent it, a loop with no empty round at the end, a rejection raised
again and again, and a heading the parser cannot read at all -- which turns a
ledger into prose without saying so.

    python -m tools.findings_selftest
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

from blockwright import findings

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

        # A round with no gate heading over it is not a thing, and must not
        # be counted under every gate the next reader asks about.
        path.write_text(
            "### Round 1 -- agent\n- F-01 stray\n", encoding="utf-8")
        if findings.rounds(path):
            return fail("rounds() accepted a round outside a gate")

    print("ledger parses, audit catches all four")
    return 0


if __name__ == "__main__":
    sys.exit(main())
