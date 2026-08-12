"""The run, written down where something other than a person can read it.

The July 2026 review found six defects by eye and the harness found none of
them. Three reasons were given, and the third was that a fault **cannot be
named**: the pipeline's whole account of itself was console output, which exists
until the terminal scrolls. A defect with no name cannot be tracked between runs,
cannot be counted, cannot be shown to have been fixed, and cannot be handed to
anything that did not watch it happen.

So every run writes one JSON file holding every number it produced: each check
with its verdict, every station a section disagreed on, the schedule audit, the
block counts, the strays, the free ends, the silhouette overlaps. Nothing
summarised away, because the summary is what the console was already good at.

This is deliberately not a grader. It records what the gate decided; it does not
decide anything itself. The one thing it adds is a flat list of defects pulled
out of the various structures, so that "what is wrong with this build" is a
question with one answer in one place rather than a walk through four objects.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class Defect:
    """One thing wrong, named so it can be followed between runs."""

    __slots__ = ("kind", "where", "detail", "severity")

    def __init__(self, kind: str, where: str, detail: str,
                 severity: str = "fail"):
        self.kind = kind            # check | section | schedule | structure
        self.where = where          # the name it goes by
        self.detail = detail
        self.severity = severity    # fail | watch | ungraded

    def as_dict(self) -> dict:
        return {"kind": self.kind, "where": self.where,
                "detail": self.detail, "severity": self.severity}

    def __repr__(self) -> str:
        return f"<{self.severity} {self.kind} {self.where}: {self.detail}>"


def defects(gate, sections=(), finished=None, findings=None,
            frame=None) -> list[Defect]:
    """Every fault the run turned up, flattened out of wherever it was found.

    A failing check and a station the section could not grade are the same fact
    at two resolutions, and both are kept: the check is what fails the build, the
    stations are what would have to be looked at to fix it.
    """
    out: list[Defect] = []

    for check in gate.failures:
        out.append(Defect("check", check.name, check.detail))

    # Questions nothing could answer are recorded as defects of the evidence,
    # not of the build. They are the difference between "this stood up to what
    # we had" and "this stood up", and a report that dropped them would let the
    # second be read off a run that only earned the first.
    for check in gate.unanswered:
        out.append(Defect("ungraded", check.name, check.detail, "ungraded"))

    for section in sections:
        for station in section.stations:
            if station.state == "ok" or station.state == "exempt":
                continue
            if station.build is None:
                detail = (f"the mesh has {station.mesh:.1f} m here and nothing "
                          "is built")
            else:
                detail = (f"built {station.build:.1f} m against the mesh's "
                          f"{station.mesh:.1f} m, {station.diff:+.1f} m")
            out.append(Defect(
                "section", f"{section.name} at v {station.v:.0f}", detail,
                "fail" if section.ok is False else "watch"))
        for exemption in section.exemptions:
            if not section.hits[exemption.name]:
                out.append(Defect(
                    "section", f"{section.name} exemption {exemption.name}",
                    "covers nothing this run -- the geometry it was written "
                    f"about has moved ({exemption.why})", "watch"))

    if finished is not None:
        for name, what in finished.undeclared:
            out.append(Defect("schedule", name,
                              f"nothing in the build claims to place it: {what}"))
        for note in finished.notes:
            out.append(Defect("check", "finish", note, "watch"))

    if findings is not None:
        # Where, not how many. `structure.floating: 1` was a true statement that
        # cost somebody a throwaway script to act on: the coordinates were
        # already inside `Findings` and simply never reached the file.
        for group in findings.adrift:
            x, y, z = group.where()
            out.append(Defect(
                "structure", f"floating at ({x}, {y}, {z})",
                f"{group.count} block(s) with nothing under them, "
                f"y {group.y0}..{group.y1}"))
        for group in findings.strays:
            x, y, z = group.where()
            span = ""
            if frame is not None:
                u0, u1 = group.span(frame)
                span = f", u {u0:.0f}..{u1:.0f}"
            out.append(Defect(
                "structure", f"stray at ({x}, {y}, {z})",
                f"{group.count} blocks, y {group.y0}..{group.y1}{span}",
                "fail" if group.y0 > 0 else "watch"))
        for block in findings.unknown:
            out.append(Defect("structure", block,
                              "nothing knows what colour to draw it"))

    return out


def moved(old: dict | None, new: dict, section_step: float = 0.5,
          plan_step: float = 0.005) -> list[str]:
    """What changed since the last run of this gate, in a handful of numbers.

    A round of fixing is a sequence of edits, and the question after each of
    them is not "does it pass" -- it usually did not before and does not now --
    but "did that make it better or worse". Nothing answered it. One building
    was fixed three times and broken twice on the way, and both regressions
    were silent: the row that moved was not the row being worked on, the console
    prints two hundred lines, and a number that got worse looks exactly like a
    number that was always that bad.

    So the report reads the report it is about to overwrite. Only movement is
    printed, never state, and nothing here fails a run: this is the sentence
    "plan overlap 0.982 -> 0.968", which is the whole of what was missing.

    Thresholds are there because photogrammetry and rasterisation both wobble
    in the last digit, and a delta that reports every run as changed is a delta
    nobody reads.
    """
    if not old:
        return []
    out: list[str] = []

    if old.get("verdict") != new.get("verdict"):
        out.append(f"verdict {old.get('verdict')} -> {new.get('verdict')}")

    was = {c["name"]: c["ok"] for c in old.get("checks", [])}
    now = {c["name"]: c["ok"] for c in new.get("checks", [])}
    word = {True: "pass", False: "FAIL", None: "ungraded"}
    for name, state in now.items():
        if name not in was:
            if state is not True:
                out.append(f"new row {name}: {word[state]}")
        elif was[name] != state:
            out.append(f"{name}: {word[was[name]]} -> {word[state]}")
    for name in was:
        if name not in now:
            out.append(f"row gone: {name}")

    before = {s["name"]: s for s in old.get("sections", [])}
    for s in new.get("sections", []):
        older = before.get(s["name"])
        if older is None:
            continue
        if abs(older.get("worst", 0.0) - s.get("worst", 0.0)) >= section_step:
            out.append(f"{s['name']} worst station "
                       f"{older['worst']:.1f} -> {s['worst']:.1f} m")
        if older.get("misses") != s.get("misses"):
            out.append(f"{s['name']} stations out "
                       f"{older.get('misses')} -> {s.get('misses')}")

    was_plan = old.get("plan") or {}
    for name, found in (new.get("plan") or {}).items():
        older = was_plan.get(name)
        if older is None:
            continue
        if abs(older.get("iou", 0.0) - found.get("iou", 0.0)) >= plan_step:
            out.append(f"{name} plan overlap {older['iou']:.3f} -> "
                       f"{found['iou']:.3f}")
        for side in ("worst_missing", "worst_outside"):
            if older.get(side) != found.get(side):
                out.append(f"{name} {side.replace('_', ' ')} "
                           f"{older.get(side)} -> {found.get(side)} cell(s)")

    if len(old.get("defects", [])) != len(new.get("defects", [])):
        out.append(f"defects {len(old.get('defects', []))} -> "
                   f"{len(new.get('defects', []))}")
    return out


def write(path, building: str, gate, sections=(), finished=None,
          findings=None, frame=None, registration=None, schedule=(),
          **extra) -> dict:
    """Write `out/report.json` and return what was written.

    `schedule` is the audit rows -- (name, ok, detail) -- which the gate has
    usually already taken; they are repeated here whole rather than only where
    they failed, because "this part is built and is 214 cells" is the number that
    makes the next run's change visible.
    """
    found = defects(gate, sections, finished, findings, frame)
    doc = {
        "building": building,
        "when": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "ok": gate.ok,
        "verdict": gate.verdict,
        "checks": gate.report(),
        "defects": [d.as_dict() for d in found],
        "sections": [s.report() for s in sections],
        "schedule": [{"name": n, "ok": ok, "detail": d} for n, ok, d in schedule],
    }
    if registration is not None:
        doc["registration"] = {
            "ceiling": round(registration.ceiling, 2),
            "u_scale": round(registration.u_scale, 4),
            "v_scale": round(registration.v_scale, 4),
            "mesh_u": [round(v, 1) for v in registration.mesh_u],
            "mesh_v": [round(v, 1) for v in registration.mesh_v],
            "build_u": [round(v, 1) for v in registration.build_u],
            "build_v": [round(v, 1) for v in registration.build_v],
        }
    if finished is not None:
        doc["build"] = finished.report()
    if findings is not None:
        doc["structure"] = {
            "floating_at": [{"where": list(g.where()), "count": g.count,
                             "y": [g.y0, g.y1]} for g in findings.adrift],
            "pieces": len(findings.pieces),
            "largest": findings.pieces[0].count if findings.pieces else 0,
            "strays": len(findings.strays),
            "stray_blocks": sum(g.count for g in findings.strays),
            "floating": len(findings.adrift),
            "free_ends": len(findings.ends),
            "unknown": list(findings.unknown),
            "layers": {str(y): len(parts)
                       for y, parts in sorted(findings.layers.items())},
        }
    doc.update(extra)

    # Read before writing, or the comparison is against the run being written.
    path = Path(path)
    was = None
    if path.exists():
        try:
            was = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            was = None
    doc["moved"] = moved(was, doc)

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return doc


def lines(doc: dict, limit: int = 12) -> list[str]:
    """The report as a handful of lines, for the end of a console run."""
    verdict = doc.get("verdict", "pass" if doc["ok"] else "fail")
    out = [f"{doc['building']}: {verdict.upper() if verdict != 'pass' else 'pass'}"
           f", {len(doc['defects'])} defect(s) recorded"]
    for entry in doc["defects"][:limit]:
        out.append(f"  {entry['severity']:5s} {entry['kind']:9s} "
                   f"{entry['where']}: {entry['detail']}")
    if len(doc["defects"]) > limit:
        out.append(f"  ... and {len(doc['defects']) - limit} more")
    # Last, because it is what a reader in the middle of a round of fixing
    # actually came for, and the last lines are the ones still on the screen.
    if doc.get("moved"):
        out.append(f"moved since the last run ({len(doc['moved'])}):")
        out.extend("  " + line for line in doc["moved"])
    elif "moved" in doc:
        out.append("nothing moved since the last run")
    return out


__all__ = ["Defect", "defects", "lines", "moved", "write"]
