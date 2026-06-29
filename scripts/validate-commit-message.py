#!/usr/bin/env python3
"""Validate Conventional Commit subject lines for Architect Playbook."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ALLOWED_TYPES = "feat|fix|chore|docs|refactor|test|perf|build|ci"
PATTERN = re.compile(rf"^({ALLOWED_TYPES})(\([a-z0-9-]+\))?!?: [a-z0-9].+")
SKIP_PREFIXES = ("Merge ", "Revert ", "fixup!", "squash!")


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate-commit-message.py <commit-message-file>", file=sys.stderr)
        return 2
    path = Path(sys.argv[1])
    subject = path.read_text(encoding="utf-8").splitlines()[0].strip() if path.exists() else ""
    if not subject:
        print("commit message is empty", file=sys.stderr)
        return 1
    if subject.startswith(SKIP_PREFIXES):
        return 0
    if PATTERN.match(subject):
        return 0
    print("commit message must be a Conventional Commit subject", file=sys.stderr)
    print(f"got: {subject}", file=sys.stderr)
    print("expected: type(scope): subject, where type is feat|fix|chore|docs|refactor|test|perf|build|ci", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
