#!/usr/bin/env python3
"""Validate Architect Playbook repository contracts.

The playbook is mostly Markdown, so this validator intentionally uses only the
Python standard library. It enforces the contracts that keep the skill set
installable, auditable, and safe to run in parallel sessions.
"""

from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import subprocess
import sys
import urllib.parse
from typing import Any
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

REQUIRED_SECTIONS = (
    "Usage",
    "What this skill does",
    "Implementation steps",
    "What this skill explicitly does NOT do",
)
FINDINGS_FILES = ("findings.md", "findings.json", "snapshot.md", "metadata.json")
CHECK_REQUIRED_FIELDS = ("checkId", "layer", "title", "expectation", "violationSignal")
VALID_STATUSES = {"present", "partial", "missing", "violation"}
SUPPORTED_CHECK_SCHEMA_VERSIONS = ("1.1.0", "1.2.0")
SEVERITY_CHECK_SCHEMA_VERSION = "1.2.0"
SUPPORTED_SCORE_POLICY_SCHEMA_VERSIONS = ("1.0.0", "1.1.0")
SEVERITY_SCORE_POLICY_SCHEMA_VERSION = "1.1.0"
VALID_SEVERITIES = ("critical", "high", "medium", "low")
VALID_METHODS = ("tool", "model")
SEVERITY_CHECK_REQUIRED_FIELDS = ("severity", "method", "rationale", "lastVerified")
ISO_DATE_PATTERN = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
TABLE_SEPARATOR_PATTERN = re.compile(r":?-+:?")
SEMANTIC_VERSION_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
SCORE_SKILL_NAME = "repository-quality-score"
AUDIT_FINDINGS_CONTRACT_HEADING = "## Repository Quality Score findings contract"
AUDIT_FINDINGS_CONTRACT_MARKERS = (
    "schema `2.0.0`",
    "`runIdentifier`",
    "`runStartedAt`",
    "`runFinishedAt`",
    "`checkCatalogVersion`",
    "`applicability`",
    "`evaluationState`",
    "`evidenceQuality`",
    "`metadata.json`",
)
SCORE_BUNDLE_FILES = (
    "SKILL.md",
    "score-policy.json",
    "scripts/calculate_repository_quality_score.py",
    "references/score-output-contract.md",
    "evals/evals.json",
)
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
        expected_keys = ["name", "description"]
        if keys[:2] != expected_keys:
            findings.append(Finding("error", skill_path, "frontmatter key order must start with name, description"))
        for key in expected_keys:
            if key not in frontmatter or not frontmatter[key].strip():
                findings.append(Finding("error", skill_path, f"frontmatter missing non-empty {key!r}"))
        if "\n" in frontmatter.get("description", ""):
            findings.append(Finding("error", skill_path, "frontmatter description must be one line"))
        expected_name = directory.name
        if frontmatter.get("name") != expected_name:
            findings.append(Finding("error", skill_path, f"frontmatter name must be {expected_name!r}"))
        validate_invocation_frontmatter(skill_path, frontmatter, keys, findings)
        header = text.split("\n---", 1)[0]
        if re.search(r"^(?:disable-model-invocation|user-invocable):\s*['\"]", header, flags=re.MULTILINE):
            findings.append(
                Finding("error", skill_path, "frontmatter invocation flags must be unquoted true or false; YAML reads a quoted value as text")
            )
        if re.search(r"^argument-hint:\s*[\[{]", header, flags=re.MULTILINE):
            findings.append(
                Finding("error", skill_path, "frontmatter argument-hint must be quoted; YAML reads an unquoted [ or { value as a list or map")
            )
        if is_stub(body):
            continue
        for section in REQUIRED_SECTIONS:
            if not section_exists(body, section):
                findings.append(Finding("error", skill_path, f"missing required section: {section}"))
        if directory.name.endswith("-audit"):
            validate_audit_contract(skill_path, body, findings)


def validate_invocation_frontmatter(
    skill_path: Path, frontmatter: dict[str, str], keys: list[str], findings: list[Finding]
) -> None:
    """Skills run only when a user asks, and say which arguments they take.

    `trigger` is not a Claude Code field: the slash command is the skill name.
    """
    if "trigger" in keys:
        findings.append(Finding("error", skill_path, "frontmatter must not use trigger; the slash command is the skill name"))
    for key in ("disable-model-invocation", "user-invocable"):
        if key in frontmatter and frontmatter[key] not in {"true", "false"}:
            findings.append(Finding("error", skill_path, f"frontmatter {key} must be true or false"))
    if frontmatter.get("disable-model-invocation") != "true":
        findings.append(
            Finding("error", skill_path, "frontmatter must set disable-model-invocation: true; playbook skills run only when a user invokes them")
        )
    if frontmatter.get("user-invocable") != "false" and not frontmatter.get("argument-hint", "").strip():
        findings.append(Finding("error", skill_path, "frontmatter must give an argument-hint for the slash command's flags"))


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


def audit_layer_slugs(body: str) -> set[str]:
    slugs: set[str] = set()
    for line in body.splitlines():
        layer_match = re.match(r"^###\s+Layer\s+[1-4]\s+\S+\s+(.+?)\s*$", line)
        if layer_match:
            slugs.add(github_anchor(layer_match.group(1)))
            continue
        stage_match = re.match(r"^###\s+Stage\s+[0-9]+\s+\S+\s+(.+?)\s*(?:\(|$)", line)
        if stage_match:
            slugs.add(github_anchor(stage_match.group(1)))
    return slugs


def canonical_check_title(value: str) -> str:
    """Normalize punctuation and insignificant articles in check titles."""
    return " ".join(token for token in re.findall(r"[a-z0-9]+", value.casefold()) if token != "the")


def markdown_table_rows(body: str) -> list[tuple[list[str], list[str], str]]:
    """Return (header cells, row cells, line) for every Markdown table body row."""
    rows: list[tuple[list[str], list[str], str]] = []
    header: list[str] | None = None
    for line in body.splitlines():
        if not line.startswith("|"):
            header = None
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if header is None:
            header = cells
        elif not all(TABLE_SEPARATOR_PATTERN.fullmatch(cell) for cell in cells):
            rows.append((header, cells, line))
    return rows


def canonical_check_rows(body: str, title: str) -> list[tuple[list[str], list[str], str]]:
    """Return the table rows whose first cell names a catalog title.

    An exact canonical match wins. Word-prefix matches tolerate a qualifier
    added or dropped on one side, but count only when no row matches exactly,
    so a title that is a word-prefix of another title still resolves.
    """
    expected = canonical_check_title(title)
    exact: list[tuple[list[str], list[str], str]] = []
    prefixed: list[tuple[list[str], list[str], str]] = []
    for row in markdown_table_rows(body):
        canonical_title = canonical_check_title(row[1][0])
        if canonical_title == expected:
            exact.append(row)
        elif canonical_title.startswith(f"{expected} ") or expected.startswith(f"{canonical_title} "):
            prefixed.append(row)
    return exact or prefixed


def canonical_check_row(body: str, title: str) -> str | None:
    """Return the unique canonical Markdown table row for a catalog title."""
    rows = canonical_check_rows(body, title)
    return rows[0][2] if len(rows) == 1 else None


def validate_check_metadata(root: Path, findings: list[Finding]) -> None:
    for directory in audit_directories(root):
        checks_path = directory / "checks.json"
        if not checks_path.exists():
            skill_text = (directory / "SKILL.md").read_text(encoding="utf-8")
            _, _, body = parse_frontmatter(skill_text)
            if not is_stub(body):
                findings.append(Finding("error", checks_path, "implemented audit must ship checks.json"))
            continue
        try:
            data = json.loads(checks_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            findings.append(Finding("error", checks_path, f"checks.json is invalid JSON: {error.msg}"))
            continue
        if not isinstance(data, dict):
            findings.append(Finding("error", checks_path, "checks.json root must be an object"))
            continue
        audit_name = directory.name
        schema_version = data.get("schemaVersion")
        if schema_version not in SUPPORTED_CHECK_SCHEMA_VERSIONS:
            findings.append(
                Finding(
                    "error",
                    checks_path,
                    f"schemaVersion must be one of {', '.join(SUPPORTED_CHECK_SCHEMA_VERSIONS)}",
                )
            )
        catalog_version = data.get("catalogVersion")
        if not isinstance(catalog_version, str) or not SEMANTIC_VERSION_PATTERN.fullmatch(catalog_version):
            findings.append(Finding("error", checks_path, "catalogVersion must be a semantic version such as '1.0.0'"))
        if data.get("skillName") != audit_name:
            findings.append(Finding("error", checks_path, f"skillName must be {audit_name!r}"))
        if data.get("humanCanonicalSource") != "SKILL.md":
            findings.append(Finding("error", checks_path, "humanCanonicalSource must be 'SKILL.md'"))
        status_taxonomy = data.get("statusTaxonomy")
        if not isinstance(status_taxonomy, dict) or not VALID_STATUSES.issubset(status_taxonomy):
            findings.append(Finding("error", checks_path, "statusTaxonomy must define present, partial, missing, and violation"))
        checks = data.get("checks")
        if not isinstance(checks, list) or not checks:
            findings.append(Finding("error", checks_path, "checks must be a non-empty list"))
            continue
        skill_text = (directory / "SKILL.md").read_text(encoding="utf-8")
        _, _, body = parse_frontmatter(skill_text)
        layer_slugs = audit_layer_slugs(body)
        seen_ids: set[str] = set()
        for index, check in enumerate(checks, start=1):
            if not isinstance(check, dict):
                findings.append(Finding("error", checks_path, f"check {index} must be an object"))
                continue
            for field in CHECK_REQUIRED_FIELDS:
                if not isinstance(check.get(field), str) or not check[field].strip():
                    findings.append(Finding("error", checks_path, f"check {index} missing non-empty {field}"))
            check_id = check.get("checkId")
            if isinstance(check_id, str):
                if not check_id.startswith(f"{audit_name}."):
                    findings.append(Finding("error", checks_path, f"checkId must start with {audit_name}.: {check_id}"))
                if check_id in seen_ids:
                    findings.append(Finding("error", checks_path, f"duplicate checkId: {check_id}"))
                seen_ids.add(check_id)
            layer = check.get("layer")
            if isinstance(layer, str) and layer not in layer_slugs:
                findings.append(Finding("error", checks_path, f"unknown layer for {check_id or f'check {index}'}: {layer}"))
            soft_check = check.get("softCheck")
            if soft_check is not None and not isinstance(soft_check, bool):
                findings.append(Finding("error", checks_path, f"softCheck must be a boolean for {check_id or f'check {index}'}"))
            if soft_check is True and isinstance(check.get("title"), str):
                row = canonical_check_row(body, check["title"])
                if row is None or "soft check" not in row.casefold():
                    findings.append(
                        Finding(
                            "error",
                            checks_path,
                            f"softCheck inventory flag must be documented as a soft check in the canonical SKILL row for {check_id or f'check {index}'}",
                        )
                    )
            if schema_version == SEVERITY_CHECK_SCHEMA_VERSION:
                validate_severity_fields(check, check_id or f"check {index}", body, checks_path, findings)
            allowed_statuses = check.get("allowedStatuses")
            if allowed_statuses is not None:
                if (
                    not isinstance(allowed_statuses, list)
                    or not allowed_statuses
                    or not all(isinstance(status, str) for status in allowed_statuses)
                ):
                    findings.append(Finding("error", checks_path, f"allowedStatuses must be a list of strings for {check_id or f'check {index}'}"))
                else:
                    invalid = sorted(set(allowed_statuses) - VALID_STATUSES)
                    if invalid:
                        findings.append(Finding("error", checks_path, f"invalid allowedStatuses for {check_id}: {', '.join(invalid)}"))
                    if len(set(allowed_statuses)) != len(allowed_statuses):
                        findings.append(Finding("error", checks_path, f"allowedStatuses contains duplicates for {check_id}"))


def is_calendar_date(value: str) -> bool:
    """True for a real calendar date written as YYYY-MM-DD."""
    if not ISO_DATE_PATTERN.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def validate_severity_fields(
    check: dict[str, object],
    label: str,
    body: str,
    checks_path: Path,
    findings: list[Finding],
) -> None:
    """Catalog schema 1.2.0 rates every check and keeps the SKILL row in sync."""
    for field in SEVERITY_CHECK_REQUIRED_FIELDS:
        value = check.get(field)
        if not isinstance(value, str) or not value.strip():
            findings.append(Finding("error", checks_path, f"{label} missing non-empty {field}"))
    severity = check.get("severity")
    method = check.get("method")
    if isinstance(severity, str) and severity not in VALID_SEVERITIES:
        findings.append(Finding("error", checks_path, f"{label} severity must be one of {', '.join(VALID_SEVERITIES)}"))
    if isinstance(method, str) and method not in VALID_METHODS:
        findings.append(Finding("error", checks_path, f"{label} method must be tool or model"))
    last_verified = check.get("lastVerified")
    if isinstance(last_verified, str):
        if not is_calendar_date(last_verified):
            findings.append(Finding("error", checks_path, f"{label} lastVerified must be a date such as 2026-10-07"))
        elif date.fromisoformat(last_verified) > datetime.now(timezone.utc).date():
            # A future date would hide the check from staleness review.
            findings.append(Finding("error", checks_path, f"{label} lastVerified cannot be later than today's UTC date"))
    title = check.get("title")
    if isinstance(title, str) and isinstance(severity, str) and isinstance(method, str):
        rows = canonical_check_rows(body, title)
        if not rows:
            findings.append(Finding("error", checks_path, f"{label} has no SKILL.md table row whose first cell matches its title"))
        elif len(rows) > 1:
            findings.append(
                Finding("error", checks_path, f"{label} title matches {len(rows)} SKILL.md table rows; exactly one must match")
            )
        else:
            header, cells, _ = rows[0]
            columns = [cell.strip("`*").casefold() for cell in header]
            values = [cell.strip("`*").casefold() for cell in cells]
            shown = True
            for column, value in (("severity", severity), ("method", method)):
                # A Severity or Method column must hold its value; without one,
                # the value must still be a cell of its own.
                if column in columns:
                    index = columns.index(column)
                    shown = shown and index < len(values) and values[index] == value
                else:
                    shown = shown and value in values
            if not shown:
                findings.append(
                    Finding(
                        "error",
                        checks_path,
                        f"{label} canonical SKILL row must show severity {severity!r} and method {method!r} as cells, "
                        "in the Severity and Method columns when the table has them",
                    )
                )


def validate_related_checks(root: Path, findings: list[Finding]) -> None:
    """relatedChecks must list distinct, existing checks in other audits.

    This runs for every catalog schema, so a malformed value in an unrated
    catalog is reported as clearly as one in a rated catalog.
    """
    catalogs: dict[Path, dict[str, object]] = {}
    known: set[str] = set()
    for directory in audit_directories(root):
        path = directory / "checks.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and isinstance(data.get("checks"), list):
            catalogs[path] = data
            known.update(
                check["checkId"]
                for check in data["checks"]
                if isinstance(check, dict) and isinstance(check.get("checkId"), str)
            )
    for path, data in catalogs.items():
        audit_name = path.parent.name
        for index, check in enumerate(data["checks"], start=1):
            if not isinstance(check, dict) or "relatedChecks" not in check:
                continue
            check_id = check.get("checkId")
            label = check_id if isinstance(check_id, str) else f"check {index}"
            related = check["relatedChecks"]
            if not isinstance(related, list) or not all(isinstance(item, str) and item for item in related):
                findings.append(Finding("error", path, f"{label} relatedChecks must be a list of checkId strings"))
                continue
            for item in dict.fromkeys(related):
                if related.count(item) > 1:
                    findings.append(Finding("error", path, f"{label} relatedChecks lists {item} more than once"))
                if item.startswith(f"{audit_name}."):
                    findings.append(Finding("error", path, f"{label} relatedChecks must name checks in other audits: {item}"))
                elif item not in known:
                    findings.append(Finding("error", path, f"{label} relatedChecks names an unknown check: {item}"))


def validate_audit_findings_contract(root: Path, findings: list[Finding]) -> None:
    if not (root / SCORE_SKILL_NAME / "SKILL.md").is_file():
        return
    for directory in audit_directories(root):
        skill_path = directory / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        if AUDIT_FINDINGS_CONTRACT_HEADING not in text:
            findings.append(
                Finding(
                    "error",
                    skill_path,
                    "audit must document the Repository Quality Score findings contract",
                )
            )
            continue
        for marker in AUDIT_FINDINGS_CONTRACT_MARKERS:
            if marker not in text:
                findings.append(
                    Finding(
                        "error",
                        skill_path,
                        f"Repository Quality Score findings contract missing marker: {marker}",
                    )
                )
        if re.search(r'"(?:check|gate)"\s*:', text):
            findings.append(
                Finding(
                    "error",
                    skill_path,
                    "canonical findings examples must use full checkId rather than check or gate",
                )
            )
        if re.search(r'"status"\s*:\s*"misconfigured"', text):
            findings.append(
                Finding(
                    "error",
                    skill_path,
                    "misconfigured is a classification; canonical status must be partial",
                )
            )
        checks_path = directory / "checks.json"
        if not checks_path.is_file():
            continue
        try:
            catalog_data = json.loads(checks_path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if not isinstance(catalog_data, dict) or not isinstance(
            catalog_data.get("checks"), list
        ):
            continue
        catalog_layers = {
            check.get("checkId"): check.get("layer")
            for check in catalog_data["checks"]
            if isinstance(check, dict)
            and isinstance(check.get("checkId"), str)
            and isinstance(check.get("layer"), str)
        }
        json_blocks = re.findall(
            r"```json\s*\n(.*?)```", text, flags=re.DOTALL | re.IGNORECASE
        )
        for block_index, block in enumerate(json_blocks, start=1):
            if '"checkId"' not in block:
                continue
            try:
                example = json.loads(block)
            except json.JSONDecodeError as error:
                findings.append(
                    Finding(
                        "error",
                        skill_path,
                        f"findings JSON example {block_index} is invalid: {error.msg}",
                    )
                )
                continue
            if (
                isinstance(example, dict)
                and example.get("schemaVersion") == "2.0.0"
            ):
                expected_shared = {
                    "skillName": directory.name,
                    "checkCatalogSchemaVersion": catalog_data.get("schemaVersion"),
                    "checkCatalogVersion": catalog_data.get("catalogVersion"),
                }
                for field_name, expected_value in expected_shared.items():
                    if example.get(field_name) != expected_value:
                        findings.append(
                            Finding(
                                "error",
                                skill_path,
                                f"findings example {field_name} must be {expected_value}",
                            )
                        )
            pending = [example]
            while pending:
                value = pending.pop()
                if isinstance(value, list):
                    pending.extend(value)
                    continue
                if not isinstance(value, dict):
                    continue
                pending.extend(value.values())
                check_id = value.get("checkId")
                layer = value.get("layer")
                if "checkId" not in value:
                    continue
                if not isinstance(check_id, str) or not check_id:
                    findings.append(
                        Finding(
                            "error",
                            skill_path,
                            "findings example checkId must be a non-empty string",
                        )
                    )
                    continue
                if not isinstance(layer, str) or not layer:
                    findings.append(
                        Finding(
                            "error",
                            skill_path,
                            f"findings example layer is missing for {check_id}",
                        )
                    )
                    continue
                expected_layer = catalog_layers.get(check_id)
                if expected_layer is None:
                    findings.append(
                        Finding(
                            "error",
                            skill_path,
                            f"findings example checkId is absent from checks.json: {check_id}",
                        )
                    )
                elif layer != expected_layer:
                    findings.append(
                        Finding(
                            "error",
                            skill_path,
                            f"findings example layer for {check_id} must be {expected_layer}",
                        )
                    )


def decimal_value(value: object) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def validate_score_policy(root: Path, findings: list[Finding]) -> None:
    skill_directory = root / SCORE_SKILL_NAME
    if not skill_directory.exists():
        return
    for relative_path in SCORE_BUNDLE_FILES:
        path = skill_directory / relative_path
        if not path.is_file():
            findings.append(Finding("error", path, f"Repository Quality Score bundle missing: {relative_path}"))

    policy_path = skill_directory / "score-policy.json"
    if not policy_path.is_file():
        return
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        findings.append(Finding("error", policy_path, f"score-policy.json is invalid JSON: {error.msg}"))
        return
    if not isinstance(policy, dict):
        findings.append(Finding("error", policy_path, "score-policy.json root must be an object"))
        return
    policy_schema = policy.get("schemaVersion")
    if policy_schema not in SUPPORTED_SCORE_POLICY_SCHEMA_VERSIONS:
        findings.append(
            Finding(
                "error",
                policy_path,
                f"schemaVersion must be one of {', '.join(SUPPORTED_SCORE_POLICY_SCHEMA_VERSIONS)}",
            )
        )
    severity_weights = policy.get("severityWeights")
    if policy_schema == SEVERITY_SCORE_POLICY_SCHEMA_VERSION:
        if not isinstance(severity_weights, dict) or set(severity_weights) != set(VALID_SEVERITIES):
            findings.append(Finding("error", policy_path, "severityWeights must define exactly critical, high, medium, and low"))
        else:
            for name, raw_value in severity_weights.items():
                value = decimal_value(raw_value)
                if value is None or value <= 0:
                    findings.append(Finding("error", policy_path, f"severityWeights.{name} must be a positive decimal string"))
    elif "severityWeights" in policy:
        # Presence alone is the error, as in the calculator, so null is rejected too.
        findings.append(Finding("error", policy_path, f"severityWeights requires schemaVersion {SEVERITY_SCORE_POLICY_SCHEMA_VERSION}"))
    policy_version = policy.get("policyVersion")
    if not isinstance(policy_version, str) or not SEMANTIC_VERSION_PATTERN.fullmatch(policy_version):
        findings.append(Finding("error", policy_path, "policyVersion must be a semantic version"))
    precision = policy.get("scorePrecision")
    if not isinstance(precision, int) or isinstance(precision, bool) or precision < 0 or precision > 6:
        findings.append(Finding("error", policy_path, "scorePrecision must be an integer from 0 through 6"))

    status_points = policy.get("statusPoints")
    if not isinstance(status_points, dict) or set(status_points) != VALID_STATUSES:
        findings.append(Finding("error", policy_path, "statusPoints must define exactly the four audit statuses"))
    else:
        for status, raw_value in status_points.items():
            value = decimal_value(raw_value)
            if value is None or value < 0 or value > 1:
                findings.append(Finding("error", policy_path, f"statusPoints.{status} must be a decimal string from 0 through 1"))

    check_weights = policy.get("checkWeights")
    if not isinstance(check_weights, dict) or set(check_weights) != {"standard", "soft"}:
        findings.append(Finding("error", policy_path, "checkWeights must define exactly standard and soft"))
    else:
        for name, raw_value in check_weights.items():
            value = decimal_value(raw_value)
            if value is None or value <= 0:
                findings.append(Finding("error", policy_path, f"checkWeights.{name} must be a positive decimal string"))

    audits = policy.get("audits")
    policy_audits: set[str] = set()
    if not isinstance(audits, list) or not audits:
        findings.append(Finding("error", policy_path, "audits must be a non-empty list"))
    else:
        for index, audit in enumerate(audits, start=1):
            if not isinstance(audit, dict):
                findings.append(Finding("error", policy_path, f"audit {index} must be an object"))
                continue
            name = audit.get("name")
            weight = decimal_value(audit.get("weight"))
            if not isinstance(name, str) or not name.endswith("-audit"):
                findings.append(Finding("error", policy_path, f"audit {index} has an invalid name"))
                continue
            if name in policy_audits:
                findings.append(Finding("error", policy_path, f"duplicate policy audit: {name}"))
            policy_audits.add(name)
            if weight is None or weight <= 0:
                findings.append(Finding("error", policy_path, f"audit weight must be a positive decimal string for {name}"))
    expected_audits = {path.name for path in audit_directories(root)}
    if policy_audits != expected_audits:
        missing = sorted(expected_audits - policy_audits)
        extra = sorted(policy_audits - expected_audits)
        detail = []
        if missing:
            detail.append(f"missing {', '.join(missing)}")
        if extra:
            detail.append(f"unknown {', '.join(extra)}")
        findings.append(Finding("error", policy_path, f"policy audit list is out of sync: {'; '.join(detail)}"))

    bands = policy.get("bands")
    if not isinstance(bands, list) or not bands:
        findings.append(Finding("error", policy_path, "bands must be a non-empty list"))
    else:
        prior_minimum: Decimal | None = None
        names: set[str] = set()
        for index, band in enumerate(bands, start=1):
            if not isinstance(band, dict):
                findings.append(Finding("error", policy_path, f"band {index} must be an object"))
                continue
            name = band.get("name")
            minimum = decimal_value(band.get("minimum"))
            if not isinstance(name, str) or not name.strip() or name in names:
                findings.append(Finding("error", policy_path, f"band {index} must have a unique non-empty name"))
            else:
                names.add(name)
            if minimum is None or minimum < 0 or minimum > 100:
                findings.append(Finding("error", policy_path, f"band {index} minimum must be a decimal string from 0 through 100"))
            elif prior_minimum is not None and minimum >= prior_minimum:
                findings.append(Finding("error", policy_path, "band minimums must be strictly descending"))
            if minimum is not None:
                prior_minimum = minimum
        final_minimum = decimal_value(bands[-1].get("minimum")) if isinstance(bands[-1], dict) else None
        if final_minimum != 0:
            findings.append(Finding("error", policy_path, "final quality band must start at 0"))


AUDIT_PROTOCOL_BUNDLE_FILES = (
    "SKILL.md",
    "scripts/audit_run.py",
    "references/run-protocol.md",
)
AUDIT_PROTOCOL_SCRIPT_REFERENCE = "audit-protocol/scripts/audit_run.py"
AUDIT_PROTOCOL_GUIDE_REFERENCE = "audit-protocol/references/run-protocol.md"


def validate_audit_protocol(root: Path, findings: list[Finding]) -> None:
    """Every audit must publish through the shared audit protocol (ADR 0003)."""
    audits = audit_directories(root)
    if not audits:
        return
    protocol = root / "audit-protocol"
    for relative_path in AUDIT_PROTOCOL_BUNDLE_FILES:
        path = protocol / relative_path
        if not path.is_file():
            findings.append(Finding("error", path, f"audit protocol bundle missing: {relative_path}"))
    for directory in audits:
        skill_path = directory / "SKILL.md"
        _, _, body = parse_frontmatter(skill_path.read_text(encoding="utf-8"))
        if is_stub(body):
            continue
        begin_command = re.compile(
            re.escape(AUDIT_PROTOCOL_SCRIPT_REFERENCE) + r'"?\s+begin\s+' + re.escape(directory.name) + r"(?![A-Za-z0-9-])"
        )
        if not begin_command.search(body):
            findings.append(
                Finding(
                    "error",
                    skill_path,
                    "audit must begin its run through the shared protocol script: "
                    f'python3 "${{CLAUDE_SKILL_DIR}}/../{AUDIT_PROTOCOL_SCRIPT_REFERENCE}" begin {directory.name}',
                )
            )
        if AUDIT_PROTOCOL_GUIDE_REFERENCE not in body:
            findings.append(
                Finding("error", skill_path, f"audit must link the run protocol ({AUDIT_PROTOCOL_GUIDE_REFERENCE})")
            )


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
        normalized = posixpath.normpath(path)
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
    bootstrap_entry = root / ".claude" / "skills" / "install-architect-playbook-globally"
    bootstrap = bootstrap_entry / "SKILL.md"
    if bootstrap_entry.is_symlink() or not bootstrap_entry.is_dir():
        findings.append(
            Finding(
                "error",
                bootstrap_entry,
                "bootstrap installer must be a real directory so it works when Git symlinks are disabled",
            )
        )
    if claim and not bootstrap.is_file():
        findings.append(Finding("error", root / "README.md", "README claims bootstrap global installer is committed, but .claude/skills/install-architect-playbook-globally/SKILL.md is missing"))
    if bootstrap.is_file():
        frontmatter, _, _ = parse_frontmatter(bootstrap.read_text(encoding="utf-8"))
        if frontmatter.get("name") != "install-architect-playbook-globally":
            findings.append(Finding("error", bootstrap, "bootstrap installer frontmatter name must match install-architect-playbook-globally"))
        source = root / "install-architect-playbook-globally" / "SKILL.md"
        if source.is_file() and bootstrap.read_text(
            encoding="utf-8"
        ) != source.read_text(encoding="utf-8"):
            findings.append(
                Finding(
                    "error",
                    bootstrap,
                    "bootstrap installer must exactly match install-architect-playbook-globally/SKILL.md",
                )
            )


def git_listed_files(root: Path) -> list[Path] | None:
    """Return tracked plus untracked-but-not-ignored files, or None outside Git.

    Listing through Git keeps ignored paths out of validation: agent worktrees
    under `.claude/worktrees/` or `.worktrees/`, local audit output, and other
    generated files. Nested worktrees appear as single directory entries, so
    their files are never scanned as if they belonged to this checkout.
    """
    try:
        toplevel = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--show-toplevel"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if toplevel.returncode != 0 or Path(os.fsdecode(toplevel.stdout.strip())).resolve() != root.resolve():
            # Outside Git, or a copy nested inside another repository that may
            # ignore it: walk the files instead of trusting that repository.
            return None
        result = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    entries = [os.fsdecode(entry) for entry in result.stdout.split(b"\0") if entry]
    return [root / entry for entry in entries]


def repository_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    """Return existing repository files with the given suffixes, sorted."""
    listed = git_listed_files(root)
    if listed is None:
        candidates = {path for suffix in suffixes for path in root.rglob(f"*{suffix}")}
        listed = [path for path in candidates if ".git" not in path.relative_to(root).parts]
    return sorted(path for path in set(listed) if path.suffix.lower() in suffixes and path.is_file())


def iter_markdown_files(root: Path) -> list[Path]:
    return repository_files(root, (".md",))


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
    for path in repository_files(root, (".md", ".json", ".yml", ".yaml", ".py")):
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


PLUGIN_MANIFEST = Path(".claude-plugin") / "plugin.json"
MARKETPLACE_MANIFEST = Path(".claude-plugin") / "marketplace.json"
INSTALLER_PREFIX = "install-architect-playbook-"
PLUGIN_NAME = "architect-playbook"
ALLOWED_PLUGIN_KEYS = {
    "$schema", "name", "displayName", "description", "author", "homepage", "repository", "license", "keywords", "skills",
}
ALLOWED_MARKETPLACE_ENTRY_KEYS = {"name", "source", "description", "category", "tags", "displayName"}
# Default component locations Claude Code loads from a plugin root. The
# repository root is the plugin root, so none of these may exist there.
PLUGIN_COMPONENT_PATHS = (
    ".mcp.json", ".lsp.json", "settings.json", "bin", "agents", "commands", "hooks", "monitors",
    "output-styles", "skills", "themes", "workflows",
)


def load_json_object(path: Path, label: str, findings: list[Finding]) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        findings.append(Finding("error", path, f"{label} is not readable JSON: {error}"))
        return None
    if not isinstance(value, dict):
        findings.append(Finding("error", path, f"{label} must be a JSON object"))
        return None
    return value


def validate_plugin_manifest(root: Path, findings: list[Finding]) -> None:
    """The repository is one plugin and its own marketplace, and ships only skills.

    The plugin lists every playbook skill except the installers, which a plugin
    install replaces. Neither manifest pins a version, so installs follow
    commits. Because the repository root is the plugin root, any default
    component location there would load for every plugin user.
    """
    manifest_path = root / PLUGIN_MANIFEST
    if not skill_directories(root):
        return
    if not manifest_path.is_file():
        findings.append(Finding("error", manifest_path, "plugin manifest is missing"))
        return
    manifest = load_json_object(manifest_path, "plugin manifest", findings)
    if manifest is None:
        return
    if manifest.get("name") != PLUGIN_NAME:
        findings.append(Finding("error", manifest_path, f"plugin manifest name must be {PLUGIN_NAME!r}"))
    for key in sorted(set(manifest) - ALLOWED_PLUGIN_KEYS):
        findings.append(
            Finding("error", manifest_path, f"plugin manifest must not declare {key}; the playbook ships only skills and pins no version")
        )
    listed = manifest.get("skills")
    if not isinstance(listed, list) or any(not isinstance(item, str) for item in listed):
        findings.append(Finding("error", manifest_path, "plugin manifest skills must be a list of ./<skill-folder> paths"))
    else:
        malformed = [item for item in listed if not item.startswith("./") or "/" in item[2:].rstrip("/")]
        for item in malformed:
            findings.append(Finding("error", manifest_path, f"plugin manifest skill path must be ./<skill-folder>: {item!r}"))
        if not malformed:
            names = [item[2:].rstrip("/") for item in listed]
            expected = sorted(
                directory.name for directory in skill_directories(root) if not directory.name.startswith(INSTALLER_PREFIX)
            )
            for name in sorted({name for name in names if names.count(name) > 1}):
                findings.append(Finding("error", manifest_path, f"plugin manifest skills lists ./{name} more than once"))
            for name in sorted(set(expected) - set(names)):
                findings.append(Finding("error", manifest_path, f"plugin manifest skills is missing ./{name}"))
            for name in sorted(set(names) - set(expected)):
                reason = "an installer, which a plugin install replaces" if name.startswith(INSTALLER_PREFIX) else "not a playbook skill"
                findings.append(Finding("error", manifest_path, f"plugin manifest skills lists ./{name}, which is {reason}"))
            if names != sorted(names):
                findings.append(Finding("error", manifest_path, "plugin manifest skills must be in alphabetical order"))
    for relative in PLUGIN_COMPONENT_PATHS:
        if (root / relative).exists():
            findings.append(
                Finding("error", root / relative, "the repository root is the plugin root, so this would load for every plugin user; configure it locally instead")
            )
    marketplace_path = root / MARKETPLACE_MANIFEST
    if not marketplace_path.is_file():
        findings.append(Finding("error", marketplace_path, "marketplace manifest is missing"))
        return
    marketplace = load_json_object(marketplace_path, "marketplace manifest", findings)
    if marketplace is None:
        return
    plugins = marketplace.get("plugins")
    if (
        not isinstance(plugins, list)
        or len(plugins) != 1
        or not isinstance(plugins[0], dict)
        or plugins[0].get("name") != PLUGIN_NAME
        or plugins[0].get("source") != "./"
    ):
        findings.append(
            Finding("error", marketplace_path, f"marketplace manifest must list one plugin named {PLUGIN_NAME!r} with source './'")
        )
    elif "version" in plugins[0] or "version" in marketplace:
        findings.append(Finding("error", marketplace_path, "marketplace manifest must not pin a version; installs follow commits"))
    else:
        # In strict mode Claude Code merges an entry's component fields into the
        # plugin, so the entry may carry only identity and listing fields.
        for key in sorted(set(plugins[0]) - ALLOWED_MARKETPLACE_ENTRY_KEYS):
            findings.append(
                Finding("error", marketplace_path, f"marketplace entry must not declare {key}; components belong in plugin.json")
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Architect Playbook skills and repository contracts.")
    parser.add_argument("root", nargs="?", default=Path(__file__).resolve().parents[1], type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    findings: list[Finding] = []
    validate_skills(root, findings)
    validate_check_metadata(root, findings)
    validate_related_checks(root, findings)
    validate_audit_findings_contract(root, findings)
    validate_score_policy(root, findings)
    validate_audit_protocol(root, findings)
    validate_no_standalone_worktree(root, findings)
    validate_readme_index(root, findings)
    validate_bootstrap_contract(root, findings)
    validate_plugin_manifest(root, findings)
    validate_markdown_links(root, findings)
    validate_trailing_whitespace(root, findings)
    print_findings(root, findings)
    return 1 if any(finding.severity == "error" for finding in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
