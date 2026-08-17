"""Lay out one round's before-and-after so a fresh pair of eyes can close it.

    python -m tools.review_verify <name>              last archived round vs now
    python -m tools.review_verify <name> 2 3          round 2 vs round 3

Writes `out/review/verify/` : for every viewpoint that actually changed, the
build render before, the build render after, and the reference from the same
camera. Then a prompt naming the open findings and asking the one closed
question a verifier is for -- is this fixed.

Why this is a separate step from the review, and a separate agent
(`blockwright-fix-verifier`): the naive reviewer must not know what was being
looked for, or it stops finding what nobody expected. The verifier must know,
because it is judging a fix rather than a building. Keeping them apart is what
lets the second one be told the answer without poisoning the first.

Viewpoints that did not change are left out, and named. A fix that moved no
pixels at the viewpoint it was aimed at did not land, and that is worth knowing
before anybody looks at anything: it is the failure `findings.md` used to record
as "built".
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.review_diff import MOVED, compare, rounds_of   # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

PROMPT = """You are verifying fixes to a Minecraft recreation of a real building.

{description}

For each viewpoint below you are given three images:

  <view>.before.png   the build before this round's fixes
  <view>.after.png    the build after them
  <view>.mesh.jpg     the real building from the same camera (or, where the
                      capture could not see it, a photograph)

The findings that were being fixed:

{findings}

For each one, answer the closed question -- is it fixed -- with one of:
fixed, partly, not fixed, worse. Say in one sentence what changed between
before and after that makes you say so, and how sure you are.

Do not look for other faults. Do not judge the building as a whole. Do not give
measurements in metres: you have no scale. A viewpoint that does not show the
place in question is a legitimate "cannot tell" -- say so rather than guess.

The build renders are lit so that a roof and a wall separate in brightness; a
face in shadow is a face in shadow, not another material.
"""


def main(argv: list[str]) -> int:
    names = [a for a in argv if not a.startswith("-")]
    if not names:
        print("    python -m tools.review_verify <name> [earlier] [later]")
        return 2
    building = names[0].strip("/\\").replace("buildings/", "")
    where = ROOT / "buildings" / building / "out" / "review"
    kept = rounds_of(where)
    if not kept:
        print(f"no archived rounds in {where}: run the review twice, with the "
              "fixes in between, and there will be a before to compare to")
        return 1

    if len(names) >= 3:
        pick = {p.name: p for p in kept}
        earlier, later = pick.get(names[1].zfill(2)), pick.get(names[2].zfill(2))
        if earlier is None or later is None:
            print(f"rounds present: {', '.join(p.name for p in kept)}")
            return 1
    else:
        earlier, later = kept[-1], where

    into = where / "verify"
    if into.exists():
        shutil.rmtree(into)
    into.mkdir(parents=True)

    moved, still = [], []
    for before in sorted(earlier.glob("*.build.png")):
        view = before.name[:-len(".build.png")]
        after = later / before.name
        if not after.exists():
            continue
        shift = compare(before, after)
        if shift is None or shift < MOVED:
            still.append(view)
            continue
        moved.append((view, shift))
        shutil.copy2(before, into / f"{view}.before.png")
        shutil.copy2(after, into / f"{view}.after.png")
        # The reference from the same camera, whichever round has it: it does
        # not change between rounds, so either copy is the same picture.
        for source in (later, earlier):
            mesh = source / f"{view}.mesh.jpg"
            if mesh.exists():
                shutil.copy2(mesh, into / f"{view}.mesh.jpg")
                break

    # Photographs go in too. Half the findings a review makes are about material
    # and rhythm, and a capture answers neither -- a verifier given only the
    # capture would call every colour fix "cannot tell".
    for photo in sorted(later.glob("90-photo-*.jpg")) or sorted(
            earlier.glob("90-photo-*.jpg")):
        shutil.copy2(photo, into / photo.name)

    if not moved:
        print("nothing moved between these two rounds.")
        print("Whatever was recorded as built did not reach any viewpoint the "
              "cameras cover. Either the fix did not land, or it landed "
              "somewhere no camera looks -- and both are worth knowing before "
              "anybody is asked to look at a picture.")
        return 1

    import importlib
    from blockwright import findings as ledger

    # The journal lives on the building, not in this folder. A leftover
    # findings.md under out/review/ is a second copy that nothing writes.
    try:
        journal = ledger.path_of(
            importlib.import_module(f"buildings.{building}.paths"))
    except Exception:                                     # noqa: BLE001
        journal = ROOT / "buildings" / building / "findings.md"

    open_now = [one for one in ledger.read(journal)
                if one.state in ("open", "built")]
    listed = ("\n".join(f"  {one.id}: {one.title()}" for one in open_now)
              or "  (findings.md carries no ledger headings; name them by hand)")

    # The building's own sentence, imported rather than scraped out of the file:
    # a description spread over eight string literals with a trailing paren is
    # exactly what a regex gets wrong, and it is the only thing the verifier is
    # told about the building.
    description = ""
    try:
        module = importlib.import_module(f"buildings.{building}.review")
        description = getattr(module, "DESCRIPTION", "")
    except Exception:                                     # noqa: BLE001
        pass
    (into / "prompt.txt").write_text(
        PROMPT.format(description=description or "(no description written)",
                      findings=listed), encoding="utf-8")

    print(f"-- {into.relative_to(ROOT)}")
    for view, shift in moved:
        print(f"   {view:24s} {shift * 100:5.1f}% moved")
    for view in still:
        print(f"   {view:24s} unchanged -- left out")
    print(f"   {len(open_now)} finding(s) to close")
    print("Send that folder to blockwright-fix-verifier, one request, and write "
          f"each verdict back into {journal}.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
