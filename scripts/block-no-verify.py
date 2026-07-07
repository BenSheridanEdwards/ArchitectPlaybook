#!/usr/bin/env python3
"""PreToolUse hook: block --no-verify on git commit and git push.

Bypassing the local gates is a named violation in this repository's operating
rules. This hook reads the Bash tool input from stdin (the Claude Code
PreToolUse payload) and denies any git commit or git push that passes
--no-verify (or its short form) so the pre-commit, commit-msg, and pre-push
hooks cannot be skipped.
"""

from __future__ import annotations

import json
import re
import sys

GIT_COMMIT_OR_PUSH = re.compile(r"\bgit\b[^\n;|&]*\b(commit|push)\b")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command", "")
    if not isinstance(command, str):
        return 0
    if "--no-verify" in command and GIT_COMMIT_OR_PUSH.search(command):
        print(
            "Blocked: --no-verify bypasses the repository gates "
            "(pre-commit, commit-msg, pre-push). Fix the failure instead.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
