#!/usr/bin/env python3
"""Validate Architect Playbook repository contracts.

The playbook is mostly Markdown, so this validator intentionally uses only the
Python standard library. It enforces the contracts that keep the skill set
installable, auditable, and safe to run in parallel sessions.
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
import urllib.parse
from dataclasses import dataclass
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
SUPPORTED_CHECK_SCHEMA_VERSION = "1.1.0"
SUPPORTED_SCORE_POLICY_SCHEMA_VERSION = "1.0.0"
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
        if data.get("schemaVersion") != SUPPORTED_CHECK_SCHEMA_VERSION:
            findings.append(
                Finding(
                    "error",
                    checks_path,
                    f"schemaVersion must be {SUPPORTED_CHECK_SCHEMA_VERSION!r}",
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
    if policy.get("schemaVersion") != SUPPORTED_SCORE_POLICY_SCHEMA_VERSION:
        findings.append(
            Finding(
                "error",
                policy_path,
                f"schemaVersion must be {SUPPORTED_SCORE_POLICY_SCHEMA_VERSION!r}",
            )
        )
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
    if not bootstrap.is_file() and bootstrap_entry.is_file():
        # Git clients with symlink support disabled materialize a tracked
        # directory symlink as a small text file containing its relative target.
        raw_target = bootstrap_entry.read_text(encoding="utf-8").strip()
        if raw_target and "\n" not in raw_target:
            resolved_target: Path | None = (bootstrap_entry.parent / raw_target).resolve()
            try:
                resolved_target.relative_to(root.resolve())
            except ValueError:
                resolved_target = None
            if resolved_target is not None:
                materialized_target = resolved_target / "SKILL.md"
                if materialized_target.is_file():
                    bootstrap = materialized_target
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
    validate_check_metadata(root, findings)
    validate_audit_findings_contract(root, findings)
    validate_score_policy(root, findings)
    validate_no_standalone_worktree(root, findings)
    validate_readme_index(root, findings)
    validate_bootstrap_contract(root, findings)
    validate_markdown_links(root, findings)
    validate_trailing_whitespace(root, findings)
    print_findings(root, findings)
    return 1 if any(finding.severity == "error" for finding in findings) else 0


if __name__ == "__main__":
    sys.exit(main())
