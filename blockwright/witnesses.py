"""Making the held-out sources answer, instead of listing them.

`sources.witnesses_for` returns every input that could have answered a question
and was not asked. For a long time that list was printed and nothing more --
"plan: measured from map, checked against capture" -- and nothing ever checked
it against the capture. The bookkeeping was honest and the sentence was not.

This is the sentence made true. Where two supplied inputs can both answer the
same question, both answer it, and the difference is written down as a number
with a tolerance beside it. The gate then has rows that can come back red for a
reason no section can produce: not "the build disagrees with the reference" but
"the two references disagree with each other".

That is a different class of fault and a common one:

    a map crop rescaled before it was cropped -- every dimension out by the same
    factor, invisible to everything else, because the build agrees with the map
    perfectly and the map is what is wrong;

    a capture of the building next door, or of the same design a phase earlier,
    which registers beautifully and grades a different building;

    a model of a later revision than the photographs, where the wing that moved
    is the wing the whole review will argue about;

    a drawing read at the wrong scale, where the storey height comes out plausible
    and everything derived from it inherits the error.

None of these are caught by comparing the build to one reference. All of them
are caught by comparing two references to each other, and the pipeline already
has both in hand.

**The tolerances here are wider than the gate's**, and deliberately. Two sources
of the same building are allowed to differ: a capture is of the thing as built,
a map is a drawing of it, a model is of the design. Six per cent between a map
and a capture is ordinary. Twenty-five is a rescaled crop.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# What two independent readings of the same fact may differ by before the
# disagreement stops being ordinary. Each is a fraction unless it says otherwise.
SIZE = 0.06         # overall extent, map against capture
ANGLE = 2.0         # degrees between two fitted frames
HEIGHT = 2.0        # metres, one part read off two references
STOREY = 0.5        # metres, floor to floor
OVERLAP = 0.70      # intersection over union of two plans


@dataclass
class Reading:
    """One number, and which source it came off."""

    by: str
    value: float

    def __str__(self) -> str:
        return f"{self.by} {self.value:.2f}"


@dataclass
class Agreement:
    """Two sources asked the same question, and how far apart they came out.

    `worst` is in the units of the question -- metres, degrees, or a fraction --
    and `tolerance` is in the same units, so the row reads without a legend.
    `unit` is only for printing.
    """

    question: str
    unit: str
    tolerance: float
    rows: dict[str, tuple[Reading, Reading]] = field(default_factory=dict)
    expected: str = ""

    def see(self, where: str, a: Reading, b: Reading) -> None:
        self.rows[where] = (a, b)

    def declare(self, why: str) -> "Agreement":
        """Say that this disagreement is a fact about the inputs.

        Two sources can differ for a reason that is not a fault, and the
        commonest one is that they are of different things: a crop from a game
        map stands its building on an invented street grid, and the capture is
        of the real prototype forty degrees away. The bearings then disagree
        forever, correctly, and no amount of re-clipping will change it.

        The row becomes ungraded rather than passing, and carries the reason.
        That is the same shape as an exemption in `gate.py` and for the same
        argument: widening the tolerance would excuse every other disagreement
        on that axis too, including the ones that are faults, and it would do it
        silently. Naming the reason excuses exactly one thing and leaves it
        legible -- and if the inputs are ever swapped for two of the same
        building, the row goes back to being graded by deleting one line.
        """
        self.expected = why
        return self

    @property
    def pair(self) -> str:
        for a, b in self.rows.values():
            return f"{a.by} vs {b.by}"
        return ""

    @property
    def gaps(self) -> dict[str, float]:
        return {where: abs(a.value - b.value) for where, (a, b) in self.rows.items()}

    @property
    def worst(self) -> float:
        return max(self.gaps.values(), default=0.0)

    @property
    def where(self) -> str:
        gaps = self.gaps
        return max(gaps, key=gaps.get) if gaps else ""

    @property
    def ok(self) -> bool | None:
        """None when nothing could be compared, or when the gap is declared.

        A declared disagreement is ungraded and not passing: the two sources
        really do differ, nobody is claiming otherwise, and a green row would
        say this was checked and agreed.
        """
        if not self.rows or self.expected:
            return None
        return self.worst <= self.tolerance

    @property
    def name(self) -> str:
        return f"{self.question} agree"

    @property
    def detail(self) -> str:
        if not self.rows:
            return "only one source can answer this, so nothing was compared"
        a, b = self.rows[self.where]
        measured = (f"{self.pair}: {self.worst:.2f} {self.unit} apart at "
                    f"{self.where} ({a} against {b})")
        if self.expected:
            return f"{measured}. Declared expected: {self.expected}"
        return f"{measured}, tolerance {self.tolerance:.2f} {self.unit}"

    def report(self) -> dict:
        return {
            "question": self.question,
            "pair": self.pair,
            "unit": self.unit,
            "tolerance": self.tolerance,
            "worst": round(self.worst, 3),
            "ok": self.ok,
            "expected": self.expected,
            "rows": {where: [round(a.value, 3), round(b.value, 3)]
                     for where, (a, b) in self.rows.items()},
        }


def size(plan_u: float, plan_v: float, mesh_u: float, mesh_v: float,
         plan_by: str, mesh_by: str, tolerance: float = SIZE) -> Agreement:
    """Two readings of how big the building is, as a fraction of each other.

    Compared as a ratio and not in metres because the fault this catches is
    multiplicative: a crop rescaled by a quarter is out by a quarter everywhere,
    and a metre of tolerance would pass it on a small building and fail it on a
    large one.
    """
    out = Agreement("extent", "x", tolerance)
    if plan_u > 0 and mesh_u > 0:
        out.see("along", Reading(plan_by, 1.0), Reading(mesh_by, mesh_u / plan_u))
    if plan_v > 0 and mesh_v > 0:
        out.see("across", Reading(plan_by, 1.0), Reading(mesh_by, mesh_v / plan_v))
    return out


def bearing(plan_angle: float, mesh_angle: float, plan_by: str, mesh_by: str,
            tolerance: float = ANGLE) -> Agreement:
    """Two fits of the same building, and whether they point the same way.

    Modulo ninety degrees: a minimum-area box has no opinion about which of its
    two axes is `u` beyond which is longer, and on a nearly square building the
    two fits can pick differently. What is being asked is whether the *grid*
    agrees, not whether the labels do.
    """
    out = Agreement("bearing", "deg", tolerance)
    gap = (plan_angle - mesh_angle) % 90.0
    gap = min(gap, 90.0 - gap)
    out.see("frame", Reading(plan_by, 0.0), Reading(mesh_by, gap))
    return out


def heights(first: dict[str, float], second: dict[str, float],
            first_by: str, second_by: str,
            tolerance: float = HEIGHT) -> Agreement:
    """The same parts read off two references.

    Only the parts both of them have anything over: a capture that never
    modelled a wing is silent about it, and silence is not a disagreement.
    """
    out = Agreement("part heights", "m", tolerance)
    for name, value in first.items():
        if name in second:
            out.see(name, Reading(first_by, value), Reading(second_by, second[name]))
    return out


def storey(measured: float, declared: float, measured_by: str, declared_by: str,
           tolerance: float = STOREY) -> Agreement:
    """A storey height measured off a reference against one somebody declared.

    Worth a row of its own because everything above the ground floor is built on
    it: a floor-to-floor half a metre out puts the top deck of an eight-storey
    building four metres wrong, and every check downstream compares that build
    against the same reference it was mis-derived from.
    """
    out = Agreement("storey height", "m", tolerance)
    if measured > 0 and declared > 0:
        out.see("floor to floor",
                Reading(measured_by, measured), Reading(declared_by, declared))
    return out


def overlap(iou: float, first_by: str, second_by: str,
            floor: float = OVERLAP) -> Agreement:
    """Two plans of the same building, as intersection over union.

    Anchored by bounding box before it is scored, so this measures shape and
    proportion rather than placement -- placement is what `size` and `bearing`
    are for. Two plans of one building from different sources will never reach
    one; below about seven tenths they are not the same footprint.
    """
    out = Agreement("plan overlap", "IoU", 1.0 - floor)
    out.see("silhouette", Reading(first_by, 1.0), Reading(second_by, iou))
    return out


def lines(found: list[Agreement]) -> list[str]:
    out = ["what the held-out sources say"]
    if not found:
        return out + ["  nothing was held out: every question had one answerer"]
    for one in found:
        mark = "  " if one.ok else ("--" if one.ok is None else "!!")
        out.append(f"{mark} {one.name}: {one.detail}")
    return out


def declare(found: list[Agreement], expected: dict[str, str]) -> list[Agreement]:
    """Mark the disagreements a building has said to expect, by question.

    Keyed on the question -- "bearing", "extent", "part heights" -- because that
    is what a person writing the table knows. A key that matches nothing is left
    alone rather than raising: it usually means the disagreement it was written
    about has gone away, which is worth seeing in the report as a row that is
    graded again.
    """
    for one in found:
        why = expected.get(one.question)
        if why:
            one.declare(why)
    return found


__all__ = ["ANGLE", "Agreement", "HEIGHT", "OVERLAP", "Reading", "SIZE",
           "STOREY", "bearing", "heights", "lines", "overlap", "size", "storey"]
