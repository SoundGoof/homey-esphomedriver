#!/usr/bin/env bash
# Build a wheel that a resolver cannot confuse with the published release.
#
# The local build and the PyPI release share version 0.1.0, so `homey app
# dependencies add --find-links` may install either — silently running upstream
# code instead of the working tree. A PEP 440 local segment sorts above the
# plain release, so the local wheel always wins. It is never committed and
# never published: release-on-version-bump.yml publishes on a version change to
# main, which is exactly what this must not trigger.
#
# The local segment carries a timestamp because pip treats an identical version
# as already satisfied: rebuilding as the same "+local" leaves the previous
# wheel installed, and the app keeps running the code you just changed away.
set -euo pipefail
cd "$(dirname "$0")"

VERSION_LINE=$(grep -n '^version = ' pyproject.toml)
ORIGINAL=$(printf '%s' "$VERSION_LINE" | sed -E 's/.*version = "([^"]+)".*/\1/')
restore() { sed -i -E "s/^version = \".*\"/version = \"${ORIGINAL}\"/" pyproject.toml; }
trap restore EXIT

LOCAL="${ORIGINAL}+local.$(date -u +%Y%m%d%H%M%S)"
sed -i -E "s/^version = \".*\"/version = \"${LOCAL}\"/" pyproject.toml
rm -rf dist
.venv/bin/python -m build >/dev/null
ls -1 dist/
echo "pin the app to: homey-esphomedriver==${LOCAL}"
