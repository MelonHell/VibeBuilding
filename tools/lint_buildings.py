"""Every building, checked against the library it actually calls.

    python -m tools.lint_buildings

`pipeline_selftest` builds a synthetic building out of `buildings/_template`,
which is the right test of the four input branches and no test at all of the six
real buildings: the template does not import `site`, `grading` or `paths.Layout`,
so a library refactor can delete or rename any of them and the selftest stays
green while every real building is dead on import.

That is not hypothetical. It happened: `blockwright.site` and
`blockwright.grading` went missing from the tree, `flatmap.surfaces` was renamed
to `flatmap.surrounds`, `build.scatter` to `build.hash_scatter` -- and the only
thing that noticed was somebody running a build by hand, weeks later.

Two passes, cheap to run and cheap to read:

    imports     every `blockwright.<module>` a building imports exists, and
                every `<module>.<name>` it reads is defined there. Static, so it
                catches a building nobody has run since the rename.
    load        every building's four modules import cleanly. Catches what the
                static pass cannot -- a signature that changed under a call, a
                constant that moved.

Neither runs a pipeline. This is the check that says "these six files still
speak the same language as the library", and it is meant to be fast enough to
run after every library edit.
"""

from __future__ import annotations

import ast
import importlib
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = ("build", "gate", "review", "paths", "probes.derive")


def library() -> dict[str, set[str]]:
    """What each library module defines at its top level."""
    out: dict[str, set[str]] = {}
    for path in sorted((ROOT / "blockwright").glob("*.py")):
        if path.stem == "__init__":
            continue
        names: set[str] = set()
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                names.update(t.id for t in node.targets
                             if isinstance(t, ast.Name))
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target,
                                                               ast.Name):
                names.add(node.target.id)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                # Re-exports count: `from .blocks import AIR` makes `schem.AIR`
                # a real attribute, and flagging it would train the reader to
                # skim past this tool's output.
                names.update(a.asname or a.name.split(".")[0]
                             for a in node.names)
        out[path.stem] = names
    return out


def buildings() -> list[Path]:
    return [d for d in sorted((ROOT / "buildings").glob("*/"))
            if (d / "build.py").exists() and d.name != "__pycache__"]


def unresolved(path: Path, lib: dict[str, set[str]]) -> list[str]:
    """Names this file reads out of the library that the library lacks."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    alias: dict[str, str] = {}
    found: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module \
                and node.module.split(".")[0] == "blockwright":
            parts = node.module.split(".")
            for a in node.names:
                if len(parts) == 1:                 # from blockwright import x
                    alias[a.asname or a.name] = a.name
                    if a.name not in lib:
                        found.append(
                            f"line {node.lineno}: no module "
                            f"blockwright.{a.name}")
                elif parts[1] in lib and a.name not in lib[parts[1]]:
                    found.append(f"line {node.lineno}: blockwright.{parts[1]} "
                                 f"has no {a.name}")

    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = alias.get(node.value.id)
            if module and module in lib and node.attr not in lib[module]:
                found.append(f"line {node.lineno}: {module} has no "
                             f"{node.attr}")
    return sorted(set(found), key=lambda s: int(s.split()[1].rstrip(":")))


def main() -> int:
    sys.path.insert(0, str(ROOT))
    lib = library()
    bad = 0

    print("-- imports")
    for directory in buildings():
        problems = []
        for stem in MODULES:
            path = directory.joinpath(*stem.split(".")).with_suffix(".py")
            if path.exists():
                problems += [f"{stem}.py {line}" for line in unresolved(path, lib)]
        if problems:
            bad += 1
            print(f"   {directory.name}")
            for line in problems:
                print(f"      {line}")
    if not bad:
        print(f"   all {len(buildings())} buildings call names the library has")

    print("-- load")
    broken = 0
    loaded = 0
    for directory in buildings():
        for stem in MODULES:
            path = directory.joinpath(*stem.split(".")).with_suffix(".py")
            if not path.exists():
                # `review.py` is optional: the roads package has no cameras
                # because it has no building to photograph.
                continue
            loaded += 1
            name = f"buildings.{directory.name}.{stem}"
            try:
                importlib.import_module(name)
            except Exception as exc:              # noqa: BLE001 -- reporting
                broken += 1
                print(f"   {name}")
                print(f"      {type(exc).__name__}: {exc}")
                if not isinstance(exc, (ImportError, AttributeError,
                                        SystemExit)):
                    traceback.print_exc()
    if not broken:
        print(f"   all {loaded} modules import")

    total = bad + broken
    print()
    print("every building is in step with the library" if not total
          else f"{total} building module(s) are not")
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
