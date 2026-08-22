"""Nothing in ``src`` may be defined without a production caller.

A passing test proves a function works, not that anything needs it, so a
helper can keep its tests and its docstring long after the last real call goes
— and reviewing for that means noticing an absence, which is exactly what
review is worst at. This check is mechanical instead.

Dispatch by string is real use: the command handlers are keyed by capability
id, so a method named ``onoff`` is reached through metadata rather than an
attribute access. String constants therefore count as references. Names
starting with ``on_`` are Homey's to call — lifecycle hooks and brand
overrides — and have no caller here by design.
"""

from __future__ import annotations

import ast
import pathlib
from collections import Counter

SRC = pathlib.Path(__file__).resolve().parent.parent / "src" / "homey_esphomedriver"

KNOWN_ORPHANS = {
    # Defined in bootstrap.py and reached only from tests/test_bootstrap.py.
    # Drop this entry once it gains a caller in src/ or is removed.
    "profile_constant",
}


def _definitions() -> dict[str, str]:
    """Every function, method and class defined under ``src``, to its location."""
    found: dict[str, str] = {}
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                found.setdefault(node.name, f"{path.relative_to(SRC)}:{node.lineno}")
    return found


def _references() -> Counter[str]:
    """Every name, attribute and string constant appearing under ``src``."""
    seen: Counter[str] = Counter()
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                seen[node.id] += 1
            elif isinstance(node, ast.Attribute):
                seen[node.attr] += 1
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                seen[node.value] += 1
    return seen


def test_no_orphaned_definitions() -> None:
    definitions = _definitions()
    references = _references()

    orphans = {
        name
        for name, _where in definitions.items()
        if not name.startswith(("__", "on_")) and references[name] == 0
    }

    unexpected = sorted(orphans - KNOWN_ORPHANS)
    assert not unexpected, "defined but never used in src/: " + ", ".join(
        f"{name} ({definitions[name]})" for name in unexpected
    )


def test_known_orphans_are_still_orphans() -> None:
    """Keeps the allowlist honest: a fixed entry has to be deleted from it."""
    definitions = _definitions()
    references = _references()

    stale = sorted(
        name
        for name in KNOWN_ORPHANS
        if name not in definitions or references[name] > 0
    )
    assert not stale, "no longer orphaned, drop from KNOWN_ORPHANS: " + ", ".join(stale)
