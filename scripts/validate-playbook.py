#!/usr/bin/env python3
"""Validate Architect Playbook repository contracts.

The playbook is mostly Markdown, so this validator intentionally uses only the
Python standard library. It enforces the contracts that keep the skill set
installable, auditable, and safe to run in parallel sessions.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import urllib.parse
from dataclasses import dataclass
from pathlib import Path

REQUIRED_SECTIONS = (
    "Usage",
    "What this skill does",
    "Implementation steps",
    "What this skill explicitly does NOT do",
)
FINDINGS_FILES = ("findings.md", "findings.json", "snapshot.md", "metadata.json")
IGNORED_SKILL_DIRECTORIES = {".git", ".claude", ".architect-audits", "scripts", "docs"}
LINK_PATTERN = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
HTML_ANCHOR_PATTERN = re.compile(r"<a\s+[^>]*name=[\"']([^\"']+)[\"'][^>]*>", re.IGNORECASE)
HEADING_PATTERN = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
FRONTMATTER_LINE_PATTERN = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")


@dataclass(frozen=True)
class Finding:
    severity: str
    path: Path
    message: str


def rel(path: Path, root: Path) -> str:
    return str(path.relative_to(root))


def strip_code_fences(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.DOTALL)


def strip_inline_link_examples(text: str) -> str:
    return re.sub(r"`[^`\n]*\[[^\]]+\]\([^)]+\)[^`\n]*`", "", text)


def parse_frontmatter(text: str) -> tuple[dict[str, str], list[str], str]:
    if not text.startswith("---\n"):
        return {}, [], text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, [], text
    raw = text[4:end]
    body = text[end + len("\n---") :].lstrip("\n")
    values: dict[str, str] = {}
    keys: list[str] = []
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = FRONTMATTER_LINE_PATTERN.match(line)
        if not match:
            continue
        key, value = match.groups()
        keys.append(key.strip())
        values[key.strip()] = value.strip().strip('"\'')
    return values, keys, body


def is_stub(body: str) -> bool:
    return "**Status:** stub" in body


def skill_directories(root: Path) -> list[Path]:
    return sorted(
        path
        for path in root.iterdir()
        if path.is_dir()
        and path.name not in IGNORED_SKILL_DIRECTORIES
        and not path.name.startswith(".")
        and (path / "SKILL.md").is_file()
    )


def audit_directories(root: Path) -> list[Path]:
    return [path for path in skill_directories(root) if path.name.endswith("-audit")]


def github_anchor(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[`*_~]", "", text)
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text.strip("-")


def markdown_anchors(text: str) -> set[str]:
    anchors: set[str] = set(HTML_ANCHOR_PATTERN.findall(text))
    counts: dict[str, int] = {}
    for line in text.splitlines():
        match = HEADING_PATTERN.match(line)
        if not match:
            continue
        base = github_anchor(match.group(2))
        if not base:
            continue
        count = counts.get(base, 0)
        anchors.add(base if count == 0 else f"{base}-{count}")
        counts[base] = count + 1
    return anchors


def section_exists(body: str, section: str) -> bool:
    return re.search(rf"^##\s+{re.escape(section)}\s*$", body, flags=re.MULTILINE) is not None


def usage_section(body: str) -> str:
    match = re.search(r"^##\s+Usage\s*$([\s\S]*?)(?=^##\s+|\Z)", body, flags=re.MULTILINE)
    return match.group(1) if match else ""


def validate_skills(root: Path, findings: list[Finding]) -> None:
    for directory in skill_directories(root):
        skill_path = directory / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        frontmatter, keys, body = parse_frontmatter(text)
        expected_keys = ["name", "description", "trigger"]
        if keys[:3] != expected_keys:
            findings.append(Finding("error", skill_path, "frontmatter key order must start with name, description, trigger"))
        for key in expected_keys:
            if key not in frontmatter or not frontmatter[key].strip():
                findings.append(Finding("error", skill_path, f"frontmatter missing non-empty {key!r}"))
        if "\n" in frontmatter.get("description", ""):
            findings.append(Finding("error", skill_path, "frontmatter description must be one line"))
        expected_name = directory.name
        expected_trigger = f"/{directory.name}"
        if frontmatter.get("name") != expected_name:
            findings.append(Finding("error", skill_path, f"frontmatter name must be {expected_name!r}"))
        if frontmatter.get("trigger") != expected_trigger:
            findings.append(Finding("error", skill_path, f"frontmatter trigger must be {expected_trigger!r}"))
        if is_stub(body):
            continue
        for section in REQUIRED_SECTIONS:
            if not section_exists(body, section):
                findings.append(Finding("error", skill_path, f"missing required section: {section}"))
        if directory.name.endswith("-audit"):
            validate_audit_contract(skill_path, body, findings)


def validate_audit_contract(skill_path: Path, body: str, findings: list[Finding]) -> None:
    usage = usage_section(body)
    audit_name = skill_path.parent.name
    if f"/{audit_name} --worktree" not in usage:
        findings.append(Finding("error", skill_path, "audit Usage must document --worktree as a flag on the audit command"))
    if "--target" in usage:
        findings.append(Finding("error", skill_path, "audit Usage must not document internal --target flag"))
    if "--worktree" not in body:
        findings.append(Finding("error", skill_path, "audit body must describe the --worktree workflow"))
    for filename in FINDINGS_FILES:
        if filename not in body:
            findings.append(Finding("error", skill_path, f"audit skill missing findings-file reference: {filename}"))


def validate_no_standalone_worktree(root: Path, findings: list[Finding]) -> None:
    worktree_skill = root / "worktree" / "SKILL.md"
    if worktree_skill.exists():
        findings.append(Finding("error", worktree_skill, "worktrees are a flag on each audit, not a standalone slash command"))


def readme_skill_links(readme_text: str) -> set[str]:
    links: set[str] = set()
    for _, raw_target in LINK_PATTERN.findall(strip_code_fences(readme_text)):
        target = raw_target.strip()
        parsed = urllib.parse.urlsplit(target)
        if parsed.scheme or target.startswith("#"):
            continue
        path = urllib.parse.unquote(parsed.path)
        normalized = os.path.normpath(path)
        if normalized.endswith("/SKILL.md") and not normalized.startswith(".."):
            links.add(normalized)
    return links


def validate_readme_index(root: Path, findings: list[Finding]) -> None:
    readme = root / "README.md"
    text = readme.read_text(encoding="utf-8")
    links = readme_skill_links(text)
    expected = {f"{path.name}/SKILL.md" for path in skill_directories(root)}
    for link in sorted(links):
        if not (root / link).is_file():
            findings.append(Finding("error", readme, f"skill index link does not exist: {link}"))
    for link in sorted(expected - links):
        findings.append(Finding("error", readme, f"top-level skill missing from README index: {link}"))


def validate_bootstrap_contract(root: Path, findings: list[Finding]) -> None:
    readme = (root / "README.md").read_text(encoding="utf-8")
    claim = ".claude/skills/install-architect-playbook-globally" in readme
    bootstrap = root / ".claude" / "skills" / "install-architect-playbook-globally" / "SKILL.md"
    if claim and not bootstrap.is_file():
        findings.append(Finding("error", root / "README.md", "README claims bootstrap global installer is committed, but .claude/skills/install-architect-playbook-globally/SKILL.md is missing"))
    if bootstrap.is_file():
        frontmatter, _, _ = parse_frontmatter(bootstrap.read_text(encoding="utf-8"))
        if frontmatter.get("name") != "install-architect-playbook-globally":
            findings.append(Finding("error", bootstrap, "bootstrap installer frontmatter name must match install-architect-playbook-globally"))


def iter_markdown_files(root: Path) -> list[Path]:
    ignored = {".git"}
    files: list[Path] = []
    for path in root.rglob("*.md"):
        if any(part in ignored for part in path.relative_to(root).parts):
            continue
        files.append(path)
    return sorted(files)


def validate_markdown_links(root: Path, findings: list[Finding]) -> None:
    for path in iter_markdown_files(root):
        text = path.read_text(encoding="utf-8")
        without_code = strip_inline_link_examples(strip_code_fences(text))
        for _, raw_target in LINK_PATTERN.findall(without_code):
            validate_one_link(root, path, raw_target.strip(), findings)


def validate_one_link(root: Path, source: Path, raw_target: str, findings: list[Finding]) -> None:
    if not raw_target or raw_target.startswith("#"):
        target_file = source
        fragment = raw_target[1:]
    else:
        parsed = urllib.parse.urlsplit(raw_target)
        if parsed.scheme or raw_target.startswith(("mailto:", "tel:")):
            return
        if not parsed.path and parsed.fragment:
            target_file = source
            fragment = parsed.fragment
        else:
            decoded_path = urllib.parse.unquote(parsed.path)
            if decoded_path.startswith("/"):
                return
            target_file = (source.parent / decoded_path).resolve()
            fragment = parsed.fragment
    try:
        target_file.relative_to(root.resolve())
    except ValueError:
        findings.append(Finding("error", source, f"internal link escapes repository: {raw_target}"))
        return
    if not target_file.exists():
        findings.append(Finding("error", source, f"internal link target does not exist: {raw_target}"))
        return
    if fragment and target_file.is_file() and target_file.suffix.lower() in {".md", ".mdx"}:
        text = target_file.read_text(encoding="utf-8")
        anchors = markdown_anchors(text)
        normalized = urllib.parse.unquote(fragment).lower()
        if normalized not in anchors:
            findings.append(Finding("error", source, f"internal link anchor does not exist: {raw_target}"))


def validate_trailing_whitespace(root: Path, findings: list[Finding]) -> None:
    for path in iter_markdown_files(root) + sorted(root.rglob("*.json")) + sorted(root.rglob("*.yml")) + sorted(root.rglob("*.yaml")) + sorted(root.rglob("*.py")):
        if ".git" in path.relative_to(root).parts:
            continue
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if line.endswith((" ", "\t")):
                findings.append(Finding("error", path, f"trailing whitespace on line {line_number}"))


def print_findings(root: Path, findings: list[Finding]) -> None:
    errors = [finding for finding in findings if finding.severity == "error"]
    warnings = [finding for finding in findings if finding.severity == "warning"]
    if not findings:
        print("playbook validation passed")
        return
    for label, group in (("ERROR", errors), ("WARNING", warnings)):
        for finding in group:
            print(f"{label}: {rel(finding.path, root)}: {finding.message}")
    print(f"validation completed with {len(errors)} error(s), {len(warnings)} warning(s)")


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Architect Playbook skills and repository contracts.")
    parser.add_argument("root", nargs="?", default=Path(__file__).resolve().parents[1], type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    findings: list[Finding] = []
    validate_skills(root, findings)
    validate_no_standalone_worktree(root, findings)
    validate_readme_index(root, findings)
    validate_bootstrap_contract(root, findings)
    validate_markdown_links(root, findings)
    validate_trailing_whitespace(root, findings)
    print_findings(root, findings)
    return 1 if any(finding.severity == "error" for finding in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
