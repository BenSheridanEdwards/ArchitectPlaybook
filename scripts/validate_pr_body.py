#!/usr/bin/env python3
"""Validate a pull-request title and body against the repository contract.

The title and body are read from environment variables (PR_TITLE, PR_BODY) so
that untrusted pull-request text is never interpolated into the workflow script.
The checks are deterministic and use only the Python standard library:

- The title is a Conventional Commit subject with no agent or tool prefix.
- The body contains every required section heading from the pull-request
  template, in order.
- Each required section has real content, not the empty template placeholder.
- The Behavioural Proof section contains an inline image (``![``) or the exact
  string ``Not applicable``.
"""

from __future__ import annotations

import os
import re
import sys

ALLOWED_TYPES = "feat|fix|chore|docs|refactor|test|perf|build|ci"
TITLE_PATTERN = re.compile(rf"^({ALLOWED_TYPES})(\([a-z0-9-]+\))?!?: [a-z0-9].+")
FORBIDDEN_TITLE_PREFIX = re.compile(r"^\s*\[[^\]]+\]")

REQUIRED_SECTIONS = (
    "Why does this feature exist?",
    "What changed?",
    "Behavioural Proof (with video and screenshots)",
    "Verification Summary",
)
BEHAVIOURAL_PROOF_SECTION = "Behavioural Proof (with video and screenshots)"


def heading_index(body: str, heading: str) -> int:
    pattern = re.compile(rf"^#{{1,6}}\s+{re.escape(heading)}\s*$", re.MULTILINE)
    match = pattern.search(body)
    return match.start() if match else -1


def section_text(body: str, heading: str) -> str:
    start = heading_index(body, heading)
    if start == -1:
        return ""
    after_heading = body.index("\n", start) + 1 if "\n" in body[start:] else len(body)
    next_heading = re.search(r"^#{1,6}\s+\S", body[after_heading:], re.MULTILINE)
    end = after_heading + next_heading.start() if next_heading else len(body)
    return body[after_heading:end]


def is_placeholder(text: str) -> bool:
    stripped = [line.strip() for line in text.splitlines() if line.strip()]
    if not stripped:
        return True
    # Every non-empty line is a bare template bullet or empty label such as "-",
    # "- Video:", "- Screenshots:" with nothing filled in.
    for line in stripped:
        content = line
        if content.startswith("-"):
            content = content[1:].strip()
        if content.endswith(":"):
            content = content[:-1].strip()
        if content:
            return False
    return True


def validate(title: str, body: str) -> list[str]:
    errors: list[str] = []

    title = title.strip()
    if FORBIDDEN_TITLE_PREFIX.match(title):
        errors.append(f"title must not start with an agent or tool prefix: {title!r}")
    elif not TITLE_PATTERN.match(title):
        errors.append(
            "title must be a Conventional Commit subject "
            f"(type(scope): summary); got: {title!r}"
        )

    last_index = -1
    for heading in REQUIRED_SECTIONS:
        index = heading_index(body, heading)
        if index == -1:
            errors.append(f"missing required section heading: {heading}")
            continue
        if index < last_index:
            errors.append(f"section out of order: {heading}")
        last_index = index
        if is_placeholder(section_text(body, heading)):
            errors.append(f"section has only placeholder content: {heading}")

    proof = section_text(body, BEHAVIOURAL_PROOF_SECTION)
    if heading_index(body, BEHAVIOURAL_PROOF_SECTION) != -1:
        if "![" not in proof and "Not applicable" not in proof:
            errors.append(
                "Behavioural Proof section must embed an inline image (![...]) "
                "or state 'Not applicable'"
            )

    return errors


def main() -> int:
    title = os.environ.get("PR_TITLE", "")
    body = os.environ.get("PR_BODY", "")
    errors = validate(title, body)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        print(f"pull-request contract failed with {len(errors)} error(s)")
        return 1
    print("pull-request contract passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
