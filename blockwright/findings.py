"""The review's journal: a chronicle of rounds beside a ledger of findings.

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

    ### F-03 | open | gate 5 | round 1 | seen 2 | by: reviewer
    **What is wrong.** The two towers are identical; the left one is balconied.
    **Fixed:**
    **Arose from:** the skeleton offers no balcony primitive.
    **Prevented by:** a `build.balcony` primitive and a line in the skeleton.

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

The ledger cannot say whether the loop converged -- an abandoned loop and a
finished one look identical from outside unless the empty round is written
down -- and it cannot say why a defect arose, without which a fix does not
generalise. The chronicle is the other reading: one heading per round, per
gate, and an empty round is a heading with nothing under it.

The file lives on the building, above `out/`. Everything under `out/` is
derived and is deleted and rebuilt whole, and nothing rebuilds a journal.
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
    r"\|\s*gate\s+(?P<gate>\d+)\s*"
    r"\|\s*round\s+(?P<first>\d+)\s*\|\s*seen\s+(?P<seen>\d+)\s*"
    r"(?:\|\s*by:\s*(?P<by>\w+)\s*)?$",
    re.MULTILINE)

# The chronicle: one heading per round, per gate. It answers the question the
# per-finding ledger cannot -- did the loop converge, and in how many rounds --
# and an empty round is a heading with nothing under it, which is what
# convergence looks like written down.
ROUND = re.compile(
    r"^###\s+Round\s+(?P<n>\d+)\s+--\s+(?P<who>agent|human)\s*$",
    re.MULTILINE)

# A round outside a gate is not a thing. The next reader loops gates 1, 4 and 5
# and asks this chronicle for each; if the parser dropped the heading the
# round sat under, every gate would see the same rounds and always answer 1.
GATE = re.compile(
    r"^##\s+Gate\s+(?P<n>\d+)\b",
    re.MULTILINE)

# What a closed finding has to say beyond its title. The third is the one that
# makes the journal worth keeping: a fix recorded as "changed recess to band"
# does not generalise, and the same fix with the reason it was needed does.
FIELDS = ("Fixed", "Arose from", "Prevented by")


class Finding:
    """One thing the eyes found, and what became of it."""

    __slots__ = ("id", "state", "gate", "first", "seen", "by", "body")

    def __init__(self, id: str, state: str, gate: int, first: int, seen: int,
                 by: str = "reviewer", body: str = ""):
        self.id = id
        self.state = state
        self.gate = gate
        self.first = first
        self.seen = seen
        self.by = by or "reviewer"
        self.body = body

    @property
    def stale(self) -> bool:
        """A rejection the reviewer keeps overturning by looking again."""
        return self.state == "rejected" and self.seen >= REOPEN

    def head(self) -> str:
        return (f"### {self.id} | {self.state} | gate {self.gate} | "
                f"round {self.first} | seen {self.seen} | by: {self.by}")

    def field(self, name: str) -> str:
        """The text of one **Name:** field of this finding's body, or ''."""
        mark = re.search(rf"^\*\*{re.escape(name)}:\*\*\s*(.+?)(?=^\*\*|\Z)",
                         self.body, re.MULTILINE | re.DOTALL)
        return mark.group(1).strip() if mark else ""

    def title(self) -> str:
        for line in self.body.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                return re.sub(r"\*+", "", line)[:96]
        return "(no text)"

    def __repr__(self) -> str:
        return (f"<{self.id} {self.state} gate {self.gate} "
                f"seen {self.seen} by {self.by}>")


def path_of(paths) -> Path:
    """The building's journal. Above `out/`, because nothing rebuilds it."""
    found = getattr(paths, "FINDINGS", None)
    if found is not None:
        return Path(found)
    return Path(paths.HERE) / "findings.md"


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
            int(mark.group("gate")), int(mark.group("first")),
            int(mark.group("seen")), mark.group("by") or "reviewer",
            text[mark.end():end].strip()))
    return found


def rounds(path: str | Path) -> list[tuple[int, int, str, int]]:
    """(gate, number, who, findings) for every round heading in the chronicle.

    The gate is the section the heading sat under. A round outside a gate is
    dropped: counting it under every gate is how a later reader would always
    answer gate 1.
    """
    text = Path(path).read_text(encoding="utf-8") if Path(path).exists() else ""
    gates = list(GATE.finditer(text))
    marks = list(ROUND.finditer(text))
    heads = list(HEAD.finditer(text))
    out: list[tuple[int, int, str, int]] = []
    for i, mark in enumerate(marks):
        gate = None
        for heading in gates:
            if heading.start() < mark.start():
                gate = int(heading.group("n"))
            else:
                break
        if gate is None:
            continue
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        for cut in gates + heads:
            if mark.end() <= cut.start() < end:
                end = cut.start()
        body = text[mark.end():end]
        listed = [ln for ln in body.splitlines() if ln.strip().startswith("- ")]
        out.append((gate, int(mark.group("n")), mark.group("who"), len(listed)))
    return out


def audit(path: str | Path, after: int = 0) -> list[str]:
    """What the journal says, and what is wrong with it.

    Printed at the end of every review run, so that the state of the loop is
    visible without opening the file -- and so that a review folder that has
    findings in prose and no ledger says so on every run until somebody fixes
    it.
    """
    path = Path(path)
    if not path.exists():
        return ["  no findings yet"]

    text = path.read_text(encoding="utf-8")
    gateless = bool(ROUND.search(text) and not GATE.search(text))

    found = read(path)
    if not found:
        out = ["  findings.md has no ledger headings, so nothing in it can be "
               "tracked between rounds.",
               "  Give each finding a heading: "
               "`### F-01 | open | gate 4 | round 1 | seen 1`"]
        if gateless:
            out.append("  no gate sections, so the loops cannot be judged")
        return out

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

    # A loop that never came back empty was abandoned, not finished, and from
    # the outside the two look identical. The empty round is the evidence.
    # Checked per gate: a finished gate 4 followed by an abandoned gate 5 is
    # two facts, and a chronicle that forgot which heading a round sat under
    # would collapse them into one.
    if gateless:
        out.append("  no gate sections, so the loops cannot be judged")
    chronicle = rounds(path)
    seen_gates: list[int] = []
    for rec in chronicle:
        if rec[0] not in seen_gates:
            seen_gates.append(rec[0])
    for gate in seen_gates:
        for who in ("agent", "human"):
            mine = [r for r in chronicle if r[0] == gate and r[2] == who]
            if mine and mine[-1][3]:
                out.append(
                    f"  the {who} loop has no empty round: gate {gate} last "
                    f"round ({mine[-1][1]}) raised {mine[-1][3]} finding(s), "
                    "so it was left rather than closed")

    for finding in found:
        if (finding.state in ("built", "rejected")
                and not finding.field("Prevented by")):
            out.append(
                f"  {finding.id} is closed and does not say what would have "
                "prevented it -- that field is what the journal is kept for")

    if after and tally["open"]:
        out.append(f"  {tally['open']} open after {after} round(s); the loop "
                   "closes when every finding is built, rejected with a number, "
                   "or ungraded with the missing input named")
    return out


def lines(path: str | Path, after: int = 0) -> list[str]:
    return ["the review ledger"] + audit(path, after)


__all__ = [
    "DISPOSITIONS", "FIELDS", "Finding", "REOPEN",
    "audit", "lines", "path_of", "read", "rounds",
]
