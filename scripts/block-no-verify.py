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
import shlex
import sys

GIT_COMMIT_OR_PUSH = re.compile(r"\bgit\b[^\n;|&]*\b(commit|push)\b")


def commit_has_short_bypass(command: str) -> bool:
    commands: list[str] = []
    parts: list[str] = []
    quote, escaped, comment, word_start = "", False, False, True
    for character in command:
        if comment:
            if character != "\n":
                continue
            comment = False
        if escaped:
            escaped = False
            if character == "\n":
                parts.pop()
            else:
                parts.append(character)
                word_start = False
        elif character == "\\" and quote != "'":
            parts.append(character)
            escaped = True
        elif quote:
            parts.append(character)
            if character == quote:
                quote = ""
            word_start = False
        elif character in "'\"":
            parts.append(character)
            quote = character
            word_start = False
        elif character == "#" and word_start:
            comment = True
        elif character in ";&|()\n":
            commands.append("".join(parts))
            parts = []
            word_start = True
        else:
            parts.append(character)
            word_start = character.isspace()
    commands.append("".join(parts))
    global_values = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--config-env", "--super-prefix"}
    commit_values = {"--message", "--file", "--reuse-message", "--reedit-message", "--template",
                     "--author", "--date", "--fixup", "--squash", "--cleanup", "--trailer", "--pathspec-from-file"}
    for parts in commands:
        try:
            arguments = iter(shlex.split(parts))
        except ValueError:
            continue
        executable = next(arguments, None)
        while executable and (executable in {"command", "exec", "env"} or "=" in executable):
            executable = next(arguments, None)
        if not executable or executable.rsplit("/", 1)[-1] != "git":
            continue
        operation = next(arguments, None)
        while operation and operation.startswith("-"):
            if operation in global_values:
                next(arguments, None)
            operation = next(arguments, None)
        if operation != "commit":
            continue
        for argument in arguments:
            if argument == "--":
                break
            if not argument.startswith("-"):
                continue
            if argument.startswith("--"):
                if argument in commit_values:
                    next(arguments, None)
                continue
            for offset, option in enumerate(argument[1:], start=1):
                if option == "n":
                    return True
                if option in "mFCctSu":
                    if option in "mFCct" and offset == len(argument) - 1:
                        next(arguments, None)
                    break
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command", "")
    if not isinstance(command, str):
        return 0
    if ("--no-verify" in command and GIT_COMMIT_OR_PUSH.search(command)) or commit_has_short_bypass(command):
        print(
            "Blocked: skipping verification bypasses the repository gates "
            "(pre-commit, commit-msg, pre-push). Fix the failure instead.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
