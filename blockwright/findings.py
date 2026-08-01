"""The review's ledger: findings that survive between rounds.

`findings.md` was prose. Prose cannot answer the questions a second round asks:
how many are still open, which of these did the reviewer already raise and get
told no, and -- the one that matters -- has anything come *back*.

The reviewer is deliberately blind: it is not allowed to read earlier findings,
because a reviewer holding last round's answers stops looking and starts
confirming. So it re-raises whatever is still visible, every round, and without
identity across rounds the session cannot tell a repeat from something new. It
then re-argues each one from scratch, and a finding that was wrongly rejected in
round one is wrongly rejected in rounds two and three by the same argument.

So each finding keeps a heading a machine can read and a person can write:

    ### F-03 open | round 1 | seen 2
    **What is wrong.** The two towers are identical; the left one is balconied.
    **Where.** 01-high-court.build.png, 90-photo-03.jpg
    **Disposition.** Open: needs a balcony idiom that is not a wall recess.

Four dispositions, and they are not interchangeable:

    open        seen, agreed, not built yet.
    built       the code changed AND the picture changed. Both halves matter:
                `tools/review_diff.py` exists because "built" used to mean only
                the first one, and a fix that changed nothing kept its tick.
    rejected    answered by a number, and the number is written down. Not "we
                disagree" -- a measurement, with where it came from.
    ungraded    nothing supplied can settle it. The same grey verdict the gate
                uses, and it must name the input that is missing.

`seen` is the count of rounds a blind reviewer has raised it. A finding rejected
three times over is not a reviewer being stubborn; it is the rejection being
wrong, or the build being wrong in a way the number does not cover. `REOPEN` is
where that stops being the session's judgement and becomes a person's.
"""

from __future__ import annotations

import re
from pathlib import Path

# A rejected finding raised this many rounds running goes to a person. Three
# because two is a coincidence: an independent reviewer looking at the same
# picture will say the same thing twice for the same reason. The third time,
# the reason is worth doubting.
REOPEN = 3

DISPOSITIONS = ("open", "built", "rejected", "ungraded")

HEAD = re.compile(
    r"^###\s+(?P<id>[A-Z]-\d+)\s*\|\s*(?P<state>\w+)\s*"
    r"\|\s*round\s+(?P<first>\d+)\s*\|\s*seen\s+(?P<seen>\d+)\s*$",
    re.MULTILINE)


class Finding:
    """One thing the eyes found, and what became of it."""

    __slots__ = ("id", "state", "first", "seen", "body")

    def __init__(self, id: str, state: str, first: int, seen: int,
                 body: str = ""):
        self.id = id
        self.state = state
        self.first = first
        self.seen = seen
        self.body = body

    @property
    def stale(self) -> bool:
        """A rejection the reviewer keeps overturning by looking again."""
        return self.state == "rejected" and self.seen >= REOPEN

    def head(self) -> str:
        return (f"### {self.id} | {self.state} | round {self.first} | "
                f"seen {self.seen}")

    def title(self) -> str:
        for line in self.body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return re.sub(r"\*+", "", line)[:96]
        return "(no text)"

    def __repr__(self) -> str:
        return f"<{self.id} {self.state} seen {self.seen}>"


def read(path: str | Path) -> list[Finding]:
    """Every finding with a machine-readable heading. Prose around them is left.

    A file with no such headings returns nothing rather than raising: findings
    written before this existed are still a perfectly good review, they are just
    not a ledger, and saying so is `audit`'s job rather than this one's.
    """
    path = Path(path)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    found: list[Finding] = []
    marks = list(HEAD.finditer(text))
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        found.append(Finding(
            mark.group("id"), mark.group("state").lower(),
            int(mark.group("first")), int(mark.group("seen")),
            text[mark.end():end].strip()))
    return found


def audit(path: str | Path, rounds: int = 0) -> list[str]:
    """What the ledger says, and what is wrong with it.

    Printed at the end of every review run, so that the state of the loop is
    visible without opening the file -- and so that a review folder that has
    findings in prose and no ledger says so on every run until somebody fixes
    it.
    """
    path = Path(path)
    if not path.exists():
        return ["  no findings yet"]

    found = read(path)
    if not found:
        return ["  findings.md has no ledger headings, so nothing in it can be "
                "tracked between rounds.",
                "  Give each finding a heading: "
                "`### F-01 | open | round 1 | seen 1`"]

    tally = {state: 0 for state in DISPOSITIONS}
    unknown = []
    for one in found:
        if one.state in tally:
            tally[one.state] += 1
        else:
            unknown.append(one)

    out = ["  " + ", ".join(f"{n} {state}" for state, n in tally.items() if n)]
    for one in unknown:
        out.append(f"  {one.id}: '{one.state}' is not a disposition -- "
                   f"one of {', '.join(DISPOSITIONS)}")

    stale = [one for one in found if one.stale]
    for one in stale:
        out.append(f"  {one.id} rejected and raised again {one.seen} times: "
                   f"{one.title()}")
    if stale:
        out.append("  A blind reviewer that keeps returning to the same thing "
                   "is not being stubborn -- it cannot see the earlier "
                   "findings. Either the number that rejected it does not "
                   "cover what it is seeing, or the rejection was wrong. "
                   "Put it to a person.")

    if rounds and tally["open"]:
        out.append(f"  {tally['open']} open after {rounds} round(s); the loop "
                   "closes when every finding is built, rejected with a number, "
                   "or ungraded with the missing input named")
    return out


def lines(path: str | Path, rounds: int = 0) -> list[str]:
    return ["the review ledger"] + audit(path, rounds)


__all__ = ["DISPOSITIONS", "Finding", "REOPEN", "audit", "lines", "read"]
