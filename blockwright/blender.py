"""Where Blender is, asked once instead of guessed in three places.

`reviewing.py` knew how to find it; `tools/render_orthos.py` required it on
PATH, which on Windows it never is, so every orthographic render was run with a
hand-typed path. Two buildings recorded hunting for the executable, and one of
them wrote the path into its own notes as a fact about the machine.

The install locations are globs and not fixed versions on purpose: a list naming
4.1 and 5.2 stops finding anything the week 5.3 lands, and the failure is quiet
-- the script prints a command for a person to run instead, which is a perfectly
good fallback that nobody wants weekly.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Checked in order. An explicit BLENDER in the environment wins, because a
# machine with two installs is a machine where somebody has an opinion.
GLOBS = (
    r"C:\Program Files\Blender Foundation\Blender */blender.exe",
    r"C:\Program Files\Blender\Blender */blender.exe",
    "/Applications/Blender.app/Contents/MacOS/Blender",
    "/usr/local/bin/blender",
    "/usr/bin/blender",
)


def find() -> str | None:
    """The Blender executable, or None with nothing printed.

    Callers decide what to do about None: `reviewing` prints the command for a
    person to run, `render_orthos` refuses. Neither should be guessing at paths.
    """
    named = os.environ.get("BLENDER")
    if named and Path(named).exists():
        return named
    found = shutil.which("blender")
    if found:
        return found
    for pattern in GLOBS:
        if "*" not in pattern:
            if Path(pattern).exists():
                return pattern
            continue
        root = Path(pattern).anchor or "."
        rest = pattern[len(root):] if root != "." else pattern
        hits = sorted(Path(root).glob(rest.replace("\\", "/")), reverse=True)
        if hits:
            return str(hits[0])
    return None


def require() -> str:
    """The executable, or a refusal that says what to do about it."""
    found = find()
    if found:
        return found
    raise SystemExit(
        "Blender is not on PATH and not in any of the usual install "
        "locations.\n"
        "Set BLENDER to the executable, or put it on PATH.\n"
        "It is needed to convert a Google Earth capture and to render the "
        "reference. A build without it still runs; what it loses is the "
        "comparison sheets and half the photo review.")


__all__ = ["GLOBS", "find", "require"]
