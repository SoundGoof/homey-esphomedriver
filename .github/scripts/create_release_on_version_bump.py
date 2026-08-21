#!/usr/bin/env python3
"""Create a GitHub release when __version__ changes on push; body from CHANGELOG.md."""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path


def read_version_from_init(text: str) -> str | None:
    """Parse ``__version__`` from package ``__init__.py`` source."""
    m = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', text, re.MULTILINE)
    return m.group(1) if m else None


def git_show_parent_file(repo_root: Path, rel_path: str) -> str | None:
    """Return file contents at ``HEAD~1``, or None if unavailable."""
    try:
        out = subprocess.run(
            ["git", "show", f"HEAD~1:{rel_path}"],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return out.stdout
    except subprocess.CalledProcessError:
        return None


def extract_changelog_section(changelog: str, version: str) -> str:
    """Return the body under ``## [version]`` until the next ``## [`` heading."""
    start_re = rf"^## \[{re.escape(version)}\][^\n]*\n"
    m = re.search(start_re, changelog, re.MULTILINE)
    if not m:
        return ""
    rest = changelog[m.end() :]
    next_hdr = re.search(r"^## \[", rest, re.MULTILINE)
    body = rest[: next_hdr.start()] if next_hdr else rest
    return body.strip()


def tag_exists_on_remote(repo_root: Path, tag: str) -> bool:
    """Return True if ``refs/tags/{tag}`` exists on ``origin``."""
    r = subprocess.run(
        ["git", "ls-remote", "--exit-code", "--tags", "origin", f"refs/tags/{tag}"],
        cwd=repo_root,
        capture_output=True,
        text=True,
    )
    return r.returncode == 0


def changelog_fallback_link(tag: str) -> str:
    """Build a default release body when no section matches ``CHANGELOG.md``."""
    repo = os.environ.get("GITHUB_REPOSITORY", "OWNER/REPO")
    changelog = f"https://github.com/{repo}/blob/{tag}/CHANGELOG.md"
    return f"See [CHANGELOG.md]({changelog}) for details."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: cwd)",
    )
    parser.add_argument(
        "--init-path",
        default="src/homey_esphomedriver/__init__.py",
        help="Path to __init__.py relative to repo root",
    )
    parser.add_argument(
        "--changelog-path",
        default="CHANGELOG.md",
        help="Path to CHANGELOG.md relative to repo root",
    )
    args = parser.parse_args()
    repo_root = args.repo_root.resolve()
    init_file = repo_root / args.init_path
    changelog_file = repo_root / args.changelog_path

    current_text = init_file.read_text(encoding="utf-8")
    current = read_version_from_init(current_text)
    if not current:
        print(
            "Could not parse __version__ from current tree; skipping.",
            file=sys.stderr,
        )
        return 0

    parent_text = git_show_parent_file(repo_root, args.init_path)
    if parent_text is None:
        print("No parent commit for __init__.py; skipping.", file=sys.stderr)
        return 0
    previous = read_version_from_init(parent_text)
    if not previous:
        print("Could not parse __version__ in parent; skipping.", file=sys.stderr)
        return 0

    if current == previous:
        print(f"Version unchanged ({current}); skipping release.")
        return 0

    tag = f"v{current}"
    if tag_exists_on_remote(repo_root, tag):
        print(f"Tag {tag} already exists on origin; skipping.", file=sys.stderr)
        return 0

    changelog_text = (
        changelog_file.read_text(encoding="utf-8") if changelog_file.is_file() else ""
    )
    body = extract_changelog_section(changelog_text, current)
    if not body:
        body = changelog_fallback_link(tag)

    with tempfile.NamedTemporaryFile(
        mode="w",
        suffix=".md",
        delete=False,
        encoding="utf-8",
    ) as tmp:
        tmp.write(body)
        notes_path = tmp.name

    try:
        subprocess.run(
            [
                "gh",
                "release",
                "create",
                tag,
                "--title",
                tag,
                "--notes-file",
                notes_path,
            ],
            cwd=repo_root,
            check=True,
        )
    finally:
        Path(notes_path).unlink(missing_ok=True)

    print(f"Created release {tag}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
