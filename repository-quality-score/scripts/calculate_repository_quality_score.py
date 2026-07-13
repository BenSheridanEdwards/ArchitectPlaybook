#!/usr/bin/env python3
"""Calculate a deterministic Repository Quality Score from audit findings.

The calculator intentionally uses only the Python standard library. Audit skills
produce evidence; this script validates, selects, and aggregates that evidence.
It never runs audits and never guesses a result for a missing check.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any

FINDINGS_SCHEMA_VERSION = "2.0.0"
SUPPORTED_CATALOG_SCHEMA_VERSION = "1.1.0"
SUPPORTED_POLICY_SCHEMA_VERSION = "1.0.0"
MAX_JSON_BYTES = 10 * 1024 * 1024
VALID_STATUSES = {"present", "partial", "missing", "violation"}
VALID_APPLICABILITY = {"applicable", "not-applicable"}
VALID_EVALUATION_STATES = {"evaluated", "not-evaluated"}
VALID_EVIDENCE_QUALITY = {"complete", "degraded", "none"}
COMMIT_PATTERN = re.compile(r"^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$")
SEMVER_PATTERN = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")


class ScoreInputError(ValueError):
    """Raised when a trusted scoring input violates its contract."""


class UnstableInputError(ScoreInputError):
    """Raised when audit writers change inputs during both score attempts."""


def safe_exception_message(exc: BaseException) -> str:
    """Return diagnostics without persisting local absolute paths."""
    if isinstance(exc, OSError):
        return f"{type(exc).__name__}: {exc.strerror or 'filesystem operation failed'}"
    return str(exc)


@dataclass(frozen=True)
class CatalogCheck:
    check_id: str
    layer: str
    title: str
    soft: bool
    allowed_statuses: frozenset[str]


@dataclass(frozen=True)
class AuditCatalog:
    name: str
    schema_version: str
    catalog_version: str
    path: Path
    checks: tuple[CatalogCheck, ...]


@dataclass(frozen=True)
class NormalizedCheck:
    check_id: str
    layer: str
    applicability: str
    evaluation_state: str
    evidence_quality: str
    status: str | None
    classification: str | None
    evidence: tuple[str, ...]


@dataclass
class Candidate:
    audit: str
    source_path: Path
    source_label: str
    schema_version: str
    run_identifier: str
    timestamp: str
    git_commit: str | None
    source_working_tree_clean: bool | None
    checks: list[NormalizedCheck]
    canonical: bool
    filters_applied: bool
    threshold_overrides: bool
    policy_overrides: bool
    graph_available: bool
    provisional_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Worktree:
    root: Path
    label: str
    current: bool


def _reject_duplicate_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ScoreInputError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ScoreInputError(f"non-finite JSON number is not allowed: {value}")


def file_fingerprint(path: Path) -> dict[str, Any]:
    stat = path.stat()
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return {
        "sha256": digest.hexdigest(),
        "size": stat.st_size,
        "modifiedNanoseconds": stat.st_mtime_ns,
    }


def strict_json_load(path: Path, fingerprints: dict[Path, dict[str, Any]]) -> Any:
    try:
        resolved = path.resolve(strict=True)
        if resolved.stat().st_size > MAX_JSON_BYTES:
            raise ScoreInputError(f"JSON input exceeds {MAX_JSON_BYTES} bytes")
        raw = resolved.read_text(encoding="utf-8")
        value = json.loads(
            raw,
            object_pairs_hook=_reject_duplicate_pairs,
            parse_constant=_reject_constant,
        )
        fingerprints[resolved] = file_fingerprint(resolved)
        return value
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ScoreInputError(
            f"cannot read valid UTF-8 JSON: {safe_exception_message(exc)}"
        ) from exc


def require_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ScoreInputError(f"{label} must be a JSON object")
    return value


def require_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ScoreInputError(f"{label} must be a JSON array")
    return value


def require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ScoreInputError(f"{label} must be a non-empty string")
    return value


def require_bool(value: Any, label: str) -> bool:
    if not isinstance(value, bool):
        raise ScoreInputError(f"{label} must be a Boolean")
    return value


def decimal_value(value: Any, label: str) -> Decimal:
    if isinstance(value, bool):
        raise ScoreInputError(f"{label} must be a decimal value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ScoreInputError(f"{label} must be a decimal value") from exc
    if not result.is_finite():
        raise ScoreInputError(f"{label} must be finite")
    return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_timestamp(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def require_rfc3339_timestamp(value: Any, label: str) -> tuple[str, datetime]:
    text = require_string(value, label)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ScoreInputError(f"{label} must be an RFC 3339 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ScoreInputError(f"{label} must include a timezone")
    return text, parsed


def ensure_within(path: Path, root: Path, label: str) -> Path:
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root.resolve(strict=True))
    except ValueError as exc:
        raise ScoreInputError(f"{label} resolves outside its allowed root") from exc
    return resolved


def git_output(root: Path, arguments: list[str]) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()


def resolve_repository_root(target: Path) -> Path:
    target = target.expanduser().resolve(strict=True)
    probe = target if target.is_dir() else target.parent
    root = git_output(probe, ["rev-parse", "--show-toplevel"])
    return Path(root).resolve() if root else probe


def current_commit(root: Path) -> str | None:
    value = git_output(root, ["rev-parse", "HEAD"])
    return value.lower() if value and COMMIT_PATTERN.fullmatch(value) else None


def is_source_clean(root: Path) -> bool | None:
    output = git_output(
        root,
        [
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
            ".",
            ":(exclude).architect-audits/**",
        ],
    )
    return None if output is None else not bool(output)


def registered_worktrees(root: Path, current_only: bool) -> list[Worktree]:
    current_root = root.resolve()
    if current_only:
        return [Worktree(current_root, "current", True)]
    output = git_output(root, ["worktree", "list", "--porcelain"])
    if not output:
        return [Worktree(current_root, "current", True)]

    worktrees: list[Worktree] = []
    record: dict[str, str] = {}
    records: list[dict[str, str]] = []
    for line in [*output.splitlines(), ""]:
        if not line:
            if record:
                records.append(record)
                record = {}
            continue
        key, _, value = line.partition(" ")
        record[key] = value

    for index, item in enumerate(records, start=1):
        raw_path = item.get("worktree")
        if not raw_path:
            continue
        candidate = Path(raw_path).resolve()
        if not candidate.is_dir():
            continue
        is_current = candidate == current_root
        branch = item.get("branch", "").removeprefix("refs/heads/")
        label = "current" if is_current else f"worktree:{branch or index}"
        worktrees.append(Worktree(candidate, label, is_current))

    if not any(item.current for item in worktrees):
        worktrees.append(Worktree(current_root, "current", True))
    return sorted(worktrees, key=lambda item: (not item.current, item.label))


def load_policy(
    skills_root: Path, fingerprints: dict[Path, dict[str, Any]]
) -> tuple[dict[str, Any], Path]:
    path = ensure_within(
        skills_root / "repository-quality-score" / "score-policy.json",
        skills_root,
        "score policy",
    )
    policy = require_object(strict_json_load(path, fingerprints), "score policy")
    schema_version = require_string(
        policy.get("schemaVersion"), "score policy schemaVersion"
    )
    if schema_version != SUPPORTED_POLICY_SCHEMA_VERSION:
        raise ScoreInputError(
            f"unsupported score policy schemaVersion: {schema_version}"
        )
    policy_version = require_string(policy.get("policyVersion"), "score policy policyVersion")
    if not SEMVER_PATTERN.fullmatch(policy_version):
        raise ScoreInputError("score policy policyVersion must use semantic versioning")
    precision = policy.get("scorePrecision")
    if not isinstance(precision, int) or isinstance(precision, bool) or not 0 <= precision <= 6:
        raise ScoreInputError("score policy scorePrecision must be an integer from 0 to 6")

    status_points = require_object(policy.get("statusPoints"), "score policy statusPoints")
    if set(status_points) != VALID_STATUSES:
        raise ScoreInputError("score policy statusPoints must define the four canonical statuses")
    for status, value in status_points.items():
        point = decimal_value(value, f"statusPoints.{status}")
        if point < 0 or point > 1:
            raise ScoreInputError(f"statusPoints.{status} must be between 0 and 1")

    weights = require_object(policy.get("checkWeights"), "score policy checkWeights")
    if set(weights) != {"standard", "soft"}:
        raise ScoreInputError("score policy checkWeights must define standard and soft")
    for name, value in weights.items():
        if decimal_value(value, f"checkWeights.{name}") <= 0:
            raise ScoreInputError(f"checkWeights.{name} must be positive")

    audits = require_list(policy.get("audits"), "score policy audits")
    seen: set[str] = set()
    for index, raw in enumerate(audits):
        audit = require_object(raw, f"audits[{index}]")
        name = require_string(audit.get("name"), f"audits[{index}].name")
        if name in seen:
            raise ScoreInputError(f"score policy contains duplicate audit {name}")
        seen.add(name)
        if decimal_value(audit.get("weight"), f"audits[{index}].weight") <= 0:
            raise ScoreInputError(f"audits[{index}].weight must be positive")

    bands = require_list(policy.get("bands"), "score policy bands")
    previous: Decimal | None = None
    for index, raw in enumerate(bands):
        band = require_object(raw, f"bands[{index}]")
        require_string(band.get("name"), f"bands[{index}].name")
        minimum = decimal_value(band.get("minimum"), f"bands[{index}].minimum")
        if minimum < 0 or minimum > 100 or (previous is not None and minimum >= previous):
            raise ScoreInputError("score policy bands must descend from at most 100")
        previous = minimum
    if not bands or decimal_value(bands[-1].get("minimum"), "final band minimum") != 0:
        raise ScoreInputError("score policy final band minimum must be zero")
    return policy, path


def load_catalog(
    skills_root: Path,
    audit_name: str,
    fingerprints: dict[Path, dict[str, Any]],
) -> AuditCatalog:
    audit_root = ensure_within(skills_root / audit_name, skills_root, f"{audit_name} directory")
    path = ensure_within(audit_root / "checks.json", audit_root, f"{audit_name} catalog")
    data = require_object(strict_json_load(path, fingerprints), f"{audit_name} catalog")
    schema_version = require_string(data.get("schemaVersion"), f"{audit_name}.schemaVersion")
    if schema_version != SUPPORTED_CATALOG_SCHEMA_VERSION:
        raise ScoreInputError(
            f"{audit_name} uses unsupported check catalog schema {schema_version}"
        )
    catalog_version = require_string(data.get("catalogVersion"), f"{audit_name}.catalogVersion")
    if not SEMVER_PATTERN.fullmatch(catalog_version):
        raise ScoreInputError(f"{audit_name}.catalogVersion must use semantic versioning")
    if data.get("skillName") != audit_name:
        raise ScoreInputError(f"{audit_name} catalog skillName must match its directory")

    checks: list[CatalogCheck] = []
    seen: set[str] = set()
    for index, raw in enumerate(require_list(data.get("checks"), f"{audit_name}.checks")):
        check = require_object(raw, f"{audit_name}.checks[{index}]")
        check_id = require_string(check.get("checkId"), f"{audit_name}.checks[{index}].checkId")
        if check_id in seen or not check_id.startswith(f"{audit_name}."):
            raise ScoreInputError(f"invalid or duplicate checkId in {audit_name}: {check_id}")
        seen.add(check_id)
        layer = require_string(check.get("layer"), f"{check_id}.layer")
        title = require_string(check.get("title"), f"{check_id}.title")
        soft = check.get("softCheck", False)
        if not isinstance(soft, bool):
            raise ScoreInputError(f"{check_id}.softCheck must be a Boolean")
        allowed_raw = check.get("allowedStatuses", sorted(VALID_STATUSES))
        allowed = require_list(allowed_raw, f"{check_id}.allowedStatuses")
        if (
            not allowed
            or any(not isinstance(item, str) or item not in VALID_STATUSES for item in allowed)
            or len(allowed) != len(set(allowed))
        ):
            raise ScoreInputError(f"{check_id}.allowedStatuses is invalid")
        checks.append(CatalogCheck(check_id, layer, title, soft, frozenset(allowed)))
    if not checks:
        raise ScoreInputError(f"{audit_name} catalog contains no checks")
    return AuditCatalog(audit_name, schema_version, catalog_version, path, tuple(checks))


def _canonical_check(
    raw: Any,
    catalog_check: CatalogCheck,
    label: str,
) -> NormalizedCheck:
    item = require_object(raw, label)
    required_fields = (
        "checkId",
        "layer",
        "applicability",
        "applicabilityReason",
        "evaluationState",
        "evaluationReason",
        "evidenceQuality",
        "classification",
        "status",
        "evidence",
        "gap",
        "remediation",
    )
    missing_fields = [field for field in required_fields if field not in item]
    if missing_fields:
        raise ScoreInputError(
            f"{label} is missing canonical fields: {', '.join(missing_fields)}"
        )
    check_id = require_string(item.get("checkId"), f"{label}.checkId")
    if check_id != catalog_check.check_id:
        raise ScoreInputError(f"{label}.checkId must be {catalog_check.check_id}")
    layer = require_string(item.get("layer"), f"{check_id}.layer")
    if layer != catalog_check.layer:
        raise ScoreInputError(f"{check_id}.layer does not match checks.json")
    applicability = require_string(item.get("applicability"), f"{check_id}.applicability")
    evaluation = require_string(item.get("evaluationState"), f"{check_id}.evaluationState")
    evidence_quality = require_string(item.get("evidenceQuality"), f"{check_id}.evidenceQuality")
    if applicability not in VALID_APPLICABILITY:
        raise ScoreInputError(f"{check_id}.applicability is invalid")
    if evaluation not in VALID_EVALUATION_STATES:
        raise ScoreInputError(f"{check_id}.evaluationState is invalid")
    if evidence_quality not in VALID_EVIDENCE_QUALITY:
        raise ScoreInputError(f"{check_id}.evidenceQuality is invalid")

    raw_status = item.get("status")
    status = None if raw_status is None else require_string(raw_status, f"{check_id}.status")
    classification = require_string(item.get("classification"), f"{check_id}.classification")
    applicability_reason = item.get("applicabilityReason")
    evaluation_reason = item.get("evaluationReason")
    for reason, reason_label in (
        (applicability_reason, "applicabilityReason"),
        (evaluation_reason, "evaluationReason"),
    ):
        if reason is not None and (not isinstance(reason, str) or not reason.strip()):
            raise ScoreInputError(f"{check_id}.{reason_label} must be a non-empty string or null")
    for optional_name in ("gap", "remediation"):
        optional_value = item.get(optional_name)
        if optional_value is not None and not isinstance(optional_value, str):
            raise ScoreInputError(f"{check_id}.{optional_name} must be a string or null")
    evidence_raw = require_list(item.get("evidence", []), f"{check_id}.evidence")
    if any(not isinstance(entry, str) for entry in evidence_raw):
        raise ScoreInputError(f"{check_id}.evidence entries must be strings")

    if applicability == "not-applicable":
        if (
            evaluation != "not-evaluated"
            or status is not None
            or evidence_quality != "none"
            or not isinstance(applicability_reason, str)
            or not applicability_reason.strip()
            or evaluation_reason is not None
        ):
            raise ScoreInputError(
                f"{check_id} not-applicable checks need a reason and must be not-evaluated with no status/evidence"
            )
    elif applicability_reason is not None:
        raise ScoreInputError(f"{check_id} applicable checks cannot have applicabilityReason")
    elif evaluation == "evaluated":
        if status not in catalog_check.allowed_statuses or evidence_quality == "none":
            raise ScoreInputError(
                f"{check_id} evaluated checks need an allowed status and evidence quality"
            )
        if evidence_quality == "degraded" and not (
            isinstance(evaluation_reason, str) and evaluation_reason.strip()
        ):
            raise ScoreInputError(f"{check_id} degraded evidence needs evaluationReason")
        if evidence_quality == "complete" and evaluation_reason is not None:
            raise ScoreInputError(f"{check_id} complete evidence cannot have evaluationReason")
    elif (
        status is not None
        or evidence_quality != "none"
        or not isinstance(evaluation_reason, str)
        or not evaluation_reason.strip()
    ):
        raise ScoreInputError(
            f"{check_id} applicable not-evaluated checks need a reason, no status, and no evidence quality"
        )
    if classification == "misconfigured" and status not in {"partial", "violation"}:
        raise ScoreInputError(
            f"{check_id} misconfigured classification requires partial or violation status"
        )

    return NormalizedCheck(
        check_id,
        layer,
        applicability,
        evaluation,
        evidence_quality,
        status,
        classification,
        tuple(evidence_raw),
    )


def parse_canonical_candidate(
    data: dict[str, Any],
    metadata_data: Any,
    catalog: AuditCatalog,
    source_path: Path,
    source_label: str,
) -> Candidate:
    if data.get("schemaVersion") != FINDINGS_SCHEMA_VERSION:
        raise ScoreInputError("not a canonical findings document")
    metadata = require_object(metadata_data, "metadata.json")
    shared_fields = (
        "schemaVersion",
        "runIdentifier",
        "skillName",
        "skillVersion",
        "checkCatalogSchemaVersion",
        "checkCatalogVersion",
        "runStartedAt",
        "runFinishedAt",
        "target",
        "execution",
    )
    for field_name in shared_fields:
        if field_name not in data or field_name not in metadata:
            raise ScoreInputError(f"findings.json and metadata.json must contain {field_name}")
        if data[field_name] != metadata[field_name]:
            raise ScoreInputError(f"findings.json and metadata.json disagree on {field_name}")

    run_identifier = require_string(data.get("runIdentifier"), "runIdentifier")
    if len(run_identifier) > 128 or not re.fullmatch(r"[A-Za-z0-9._-]+", run_identifier):
        raise ScoreInputError("runIdentifier contains unsafe characters")
    skill_version = require_string(data.get("skillVersion"), "skillVersion")
    if not SEMVER_PATTERN.fullmatch(skill_version):
        raise ScoreInputError("skillVersion must use semantic versioning")
    if data.get("skillName") != catalog.name:
        raise ScoreInputError("findings skillName does not match the selected audit")
    if data.get("checkCatalogSchemaVersion") != catalog.schema_version:
        raise ScoreInputError("findings checkCatalogSchemaVersion does not match checks.json")
    if data.get("checkCatalogVersion") != catalog.catalog_version:
        raise ScoreInputError("findings checkCatalogVersion does not match checks.json")

    run_started_at, started = require_rfc3339_timestamp(data.get("runStartedAt"), "runStartedAt")
    run_finished_at, finished = require_rfc3339_timestamp(
        data.get("runFinishedAt"), "runFinishedAt"
    )
    if finished < started:
        raise ScoreInputError("runFinishedAt cannot precede runStartedAt")
    target = require_object(data.get("target"), "findings target")
    repository_name = require_string(target.get("repository"), "target.repository")
    if (
        Path(repository_name).is_absolute()
        or re.match(r"^[A-Za-z]:[\\/]", repository_name)
        or repository_name.startswith(("\\\\", "//"))
        or "\\" in repository_name
        or "://" in repository_name
        or "@" in repository_name
    ):
        raise ScoreInputError("target.repository cannot contain a local path or credentials")
    git_commit = require_string(target.get("gitCommit"), "target.gitCommit").lower()
    if not COMMIT_PATTERN.fullmatch(git_commit):
        raise ScoreInputError("target.gitCommit must be a Git object identifier")
    source_clean = require_bool(
        target.get("sourceWorkingTreeClean"), "target.sourceWorkingTreeClean"
    )
    execution = require_object(data.get("execution"), "findings execution")
    execution_fields = (
        "filtersApplied",
        "filterArguments",
        "thresholdOverrides",
        "policyOverrides",
        "enrichmentArguments",
        "graphAvailable",
    )
    missing_execution_fields = [
        field for field in execution_fields if field not in execution
    ]
    if missing_execution_fields:
        raise ScoreInputError(
            "execution is missing canonical fields: "
            + ", ".join(missing_execution_fields)
        )
    filters_applied = require_bool(execution.get("filtersApplied"), "filtersApplied")
    filter_arguments = require_list(execution.get("filterArguments"), "filterArguments")
    if any(not isinstance(item, str) for item in filter_arguments):
        raise ScoreInputError("filterArguments entries must be strings")
    if not filters_applied and filter_arguments:
        raise ScoreInputError("filterArguments must be empty when filtersApplied is false")
    if filters_applied and not filter_arguments:
        raise ScoreInputError("filterArguments must identify applied filters")
    threshold_overrides = bool(
        require_object(execution.get("thresholdOverrides"), "thresholdOverrides")
    )
    policy_overrides = bool(
        require_object(execution.get("policyOverrides"), "policyOverrides")
    )
    graph_available = require_bool(execution.get("graphAvailable"), "graphAvailable")
    enrichment = require_list(
        execution.get("enrichmentArguments"), "execution.enrichmentArguments"
    )
    if any(not isinstance(item, str) for item in enrichment):
        raise ScoreInputError("execution.enrichmentArguments entries must be strings")

    checks_raw = require_list(data.get("checks"), "findings checks")
    if len(checks_raw) != len(catalog.checks):
        raise ScoreInputError("canonical findings must contain every catalog check exactly once")
    raw_by_id: dict[str, Any] = {}
    for raw in checks_raw:
        item = require_object(raw, "findings check")
        check_id = require_string(item.get("checkId"), "findings checkId")
        if check_id in raw_by_id:
            raise ScoreInputError(f"duplicate findings checkId: {check_id}")
        raw_by_id[check_id] = item
    catalog_ids = {check.check_id for check in catalog.checks}
    if set(raw_by_id) != catalog_ids:
        raise ScoreInputError("canonical findings check IDs must exactly match checks.json")
    checks = [
        _canonical_check(raw_by_id[check.check_id], check, check.check_id)
        for check in catalog.checks
    ]
    audit_applicability = data.get("auditApplicability")
    if audit_applicability is not None:
        applicability_object = require_object(
            audit_applicability, "auditApplicability"
        )
        applicability_status = require_string(
            applicability_object.get("status"), "auditApplicability.status"
        )
        if applicability_status not in VALID_APPLICABILITY:
            raise ScoreInputError("auditApplicability.status is invalid")
        reason = applicability_object.get("reason")
        if applicability_status == "not-applicable":
            if not isinstance(reason, str) or not reason.strip():
                raise ScoreInputError("a not-applicable audit needs a reason")
            if any(check.applicability != "not-applicable" for check in checks):
                raise ScoreInputError(
                    "a not-applicable audit must mark every catalog check not-applicable"
                )

    reasons: list[str] = []
    if filters_applied:
        reasons.append("filtered-run")
    if threshold_overrides:
        reasons.append("threshold-overrides")
    if policy_overrides:
        reasons.append("policy-overrides")
    if not graph_available:
        reasons.append("graph-unavailable")
    if not source_clean:
        reasons.append("source-worktree-dirty-at-audit-time")
    if any(
        check.applicability == "applicable"
        and check.evaluation_state == "not-evaluated"
        for check in checks
    ):
        reasons.append("checks-not-evaluated")
    if any(check.evidence_quality == "degraded" for check in checks):
        reasons.append("degraded-evidence")

    return Candidate(
        catalog.name,
        source_path,
        source_label,
        FINDINGS_SCHEMA_VERSION,
        run_identifier,
        run_finished_at,
        git_commit,
        source_clean,
        checks,
        True,
        filters_applied,
        threshold_overrides,
        policy_overrides,
        graph_available,
        list(dict.fromkeys(reasons)),
    )


def _legacy_identifier(item: dict[str, Any]) -> str | None:
    for key in ("checkId", "id", "check", "gate"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def parse_legacy_candidate(
    data: dict[str, Any],
    catalog: AuditCatalog,
    source_path: Path,
    source_label: str,
) -> Candidate:
    raw_checks = data.get("checks", data.get("gates"))
    entries = require_list(raw_checks, "legacy findings checks/gates")
    catalog_by_id = {check.check_id: check for check in catalog.checks}
    normalized: dict[str, NormalizedCheck] = {}
    for index, raw in enumerate(entries):
        item = require_object(raw, f"legacy finding {index}")
        identifier = _legacy_identifier(item)
        if identifier is None:
            raise ScoreInputError(f"legacy finding {index} has no check identifier")
        if identifier in catalog_by_id:
            check_id = identifier
        else:
            matches = sorted(
                check_id
                for check_id in catalog_by_id
                if check_id.endswith(f".{identifier}")
            )
            if len(matches) != 1:
                raise ScoreInputError(f"legacy check identifier is not an exact unique match: {identifier}")
            check_id = matches[0]
        if check_id in normalized:
            raise ScoreInputError(f"legacy findings contain duplicate check {check_id}")
        catalog_check = catalog_by_id[check_id]
        raw_status = require_string(item.get("status"), f"legacy {check_id}.status").lower()
        classification = item.get("classification")
        if raw_status == "misconfigured":
            status = "partial"
            classification = classification or "misconfigured"
        else:
            status = raw_status
        if status not in catalog_check.allowed_statuses:
            raise ScoreInputError(f"legacy {check_id}.status is not allowed by checks.json")
        evidence_raw = item.get("evidence", [])
        if isinstance(evidence_raw, str):
            evidence = (evidence_raw,)
        elif isinstance(evidence_raw, list) and all(isinstance(value, str) for value in evidence_raw):
            evidence = tuple(evidence_raw)
        else:
            evidence = ()
        normalized[check_id] = NormalizedCheck(
            check_id,
            catalog_check.layer,
            "applicable",
            "evaluated",
            "degraded" if not evidence else "complete",
            status,
            classification if isinstance(classification, str) else None,
            evidence,
        )

    for catalog_check in catalog.checks:
        if catalog_check.check_id not in normalized:
            normalized[catalog_check.check_id] = NormalizedCheck(
                catalog_check.check_id,
                catalog_check.layer,
                "applicable",
                "not-evaluated",
                "none",
                None,
                "absent-from-legacy-output",
                (),
            )

    target = data.get("target", {})
    git_commit: str | None = None
    source_clean: bool | None = None
    if isinstance(target, dict):
        raw_commit = target.get("gitCommit")
        if isinstance(raw_commit, str) and COMMIT_PATTERN.fullmatch(raw_commit):
            git_commit = raw_commit.lower()
        if isinstance(target.get("sourceWorkingTreeClean"), bool):
            source_clean = target["sourceWorkingTreeClean"]
    raw_commit = data.get("gitCommit", data.get("commit"))
    if git_commit is None and isinstance(raw_commit, str) and COMMIT_PATTERN.fullmatch(raw_commit):
        git_commit = raw_commit.lower()
    timestamp_value = data.get("timestamp", data.get("generatedAt", "1970-01-01T00:00:00Z"))
    timestamp = timestamp_value if isinstance(timestamp_value, str) else "1970-01-01T00:00:00Z"
    run_identifier = data.get("runIdentifier", f"legacy-{hashlib.sha256(str(source_path).encode()).hexdigest()[:12]}")
    if not isinstance(run_identifier, str):
        run_identifier = str(run_identifier)

    return Candidate(
        catalog.name,
        source_path,
        source_label,
        str(data.get("schemaVersion", "legacy")),
        run_identifier,
        timestamp,
        git_commit,
        source_clean,
        [normalized[check.check_id] for check in catalog.checks],
        False,
        False,
        False,
        False,
        True,
        ["legacy-findings-schema", "legacy-evidence-is-provisional"],
    )


def parse_candidate(
    data: Any,
    metadata_data: Any,
    catalog: AuditCatalog,
    source_path: Path,
    source_label: str,
) -> Candidate:
    document = require_object(data, "findings document")
    if document.get("schemaVersion") == FINDINGS_SCHEMA_VERSION:
        return parse_canonical_candidate(
            document, metadata_data, catalog, source_path, source_label
        )
    return parse_legacy_candidate(document, catalog, source_path, source_label)


def candidate_priority(candidate: Candidate) -> tuple[Any, ...]:
    return (
        1 if candidate.canonical else 0,
        1 if not candidate.filters_applied else 0,
        1 if not candidate.threshold_overrides and not candidate.policy_overrides else 0,
        1 if candidate.graph_available else 0,
        -len(candidate.provisional_reasons),
        parse_timestamp(candidate.timestamp),
        candidate.run_identifier,
        candidate.source_label,
        candidate.source_path.as_posix(),
    )


def findings_reference(source_path: Path, source_label: str, audit: str) -> str:
    """Return a stable relative pointer without exposing a local worktree path."""
    parts = source_path.parts
    for index in range(len(parts) - 1):
        if parts[index : index + 2] == (".architect-audits", audit):
            suffix = Path(*parts[index:]).as_posix()
            return f"{source_label}/{suffix}"
    return f"{source_label}/.architect-audits/{audit}/findings.json"


def discover_candidates(
    worktrees: list[Worktree],
    catalog: AuditCatalog,
    expected_commit: str | None,
    fingerprints: dict[Path, dict[str, Any]],
) -> tuple[list[Candidate], list[dict[str, str]]]:
    accepted: list[Candidate] = []
    excluded: list[dict[str, str]] = []
    for worktree in worktrees:
        findings_root = worktree.root / ".architect-audits" / catalog.name
        paths: list[Path] = []
        direct = findings_root / "findings.json"
        if direct.is_file():
            paths.append(direct)
        if findings_root.is_dir():
            paths.extend(
                path
                for path in sorted(findings_root.rglob("findings.json"))
                if path != direct
            )
        seen_paths: set[Path] = set()
        for raw_path in paths:
            try:
                path = ensure_within(raw_path, worktree.root, "findings file")
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                findings_data = strict_json_load(path, fingerprints)
                metadata_data: Any = None
                if (
                    isinstance(findings_data, dict)
                    and findings_data.get("schemaVersion") == FINDINGS_SCHEMA_VERSION
                ):
                    metadata_path = ensure_within(
                        path.with_name("metadata.json"),
                        worktree.root,
                        "metadata file",
                    )
                    metadata_data = strict_json_load(metadata_path, fingerprints)
                candidate = parse_candidate(
                    findings_data,
                    metadata_data,
                    catalog,
                    path,
                    worktree.label,
                )
                if expected_commit:
                    if candidate.git_commit is None:
                        excluded.append(
                            {
                                "audit": catalog.name,
                                "source": findings_reference(
                                    path, worktree.label, catalog.name
                                ),
                                "reason": "missing-commit-identity",
                            }
                        )
                        continue
                    if candidate.git_commit != expected_commit:
                        excluded.append(
                            {
                                "audit": catalog.name,
                                "source": findings_reference(
                                    path, worktree.label, catalog.name
                                ),
                                "reason": "commit-mismatch",
                            }
                        )
                        continue
                accepted.append(candidate)
            except (ScoreInputError, OSError) as exc:
                excluded.append(
                    {
                        "audit": catalog.name,
                        "source": findings_reference(
                            raw_path, worktree.label, catalog.name
                        ),
                        "reason": f"invalid-findings: {safe_exception_message(exc)}",
                    }
                )
    return accepted, excluded


def select_candidates(
    candidates: dict[str, list[Candidate]],
    audit_order: list[str],
) -> tuple[dict[str, Candidate], list[dict[str, str]]]:
    selected: dict[str, Candidate] = {}
    excluded: list[dict[str, str]] = []
    for audit in audit_order:
        choices = candidates.get(audit, [])
        if not choices:
            continue
        winner = max(choices, key=candidate_priority)
        selected[audit] = winner
        for choice in choices:
            if choice is winner:
                continue
            excluded.append(
                {
                    "audit": audit,
                    "source": findings_reference(
                        choice.source_path, choice.source_label, audit
                    ),
                    "reason": "lower-priority-run",
                }
            )
    return selected, excluded


def quantize(value: Decimal, precision: int) -> Decimal:
    unit = Decimal(1).scaleb(-precision)
    return value.quantize(unit, rounding=ROUND_HALF_UP)


def json_number(value: Decimal, precision: int) -> int | float:
    rounded = quantize(value, precision)
    return int(rounded) if rounded == rounded.to_integral() else float(rounded)


def display_number(value: int | float | None, precision: int = 2) -> str:
    return "unavailable" if value is None else f"{value:.{precision}f}"


def calculate(
    repository_root: Path,
    policy: dict[str, Any],
    policy_path: Path,
    catalogs: dict[str, AuditCatalog],
    catalog_failures: dict[str, str],
    selected: dict[str, Candidate],
    excluded: list[dict[str, str]],
    commit: str | None,
    current_clean: bool | None,
    worktrees: list[Worktree],
    current_worktree_only: bool,
    fingerprints: dict[Path, dict[str, Any]],
) -> dict[str, Any]:
    run_started_at = utc_now()
    precision = int(policy["scorePrecision"])
    status_points = {
        name: decimal_value(value, f"statusPoints.{name}")
        for name, value in policy["statusPoints"].items()
    }
    check_weights = {
        name: decimal_value(value, f"checkWeights.{name}")
        for name, value in policy["checkWeights"].items()
    }
    audit_weights = {
        audit["name"]: decimal_value(audit["weight"], f"{audit['name']}.weight")
        for audit in policy["audits"]
    }
    audit_order = list(audit_weights)

    categories: list[dict[str, Any]] = []
    weighted_score = Decimal(0)
    scored_audit_weight = Decimal(0)
    evaluated_checks = 0
    applicable_checks = 0
    total_catalog_checks = sum(len(catalog.checks) for catalog in catalogs.values())
    deductions: list[dict[str, Any]] = []
    reasons: list[dict[str, str]] = []
    non_applicable_audits: list[str] = []

    if commit is None:
        reasons.append({"code": "repository-commit-unavailable", "message": "Git commit identity is unavailable."})
    if current_clean is False:
        reasons.append({"code": "current-source-dirty", "message": "The target source working tree is dirty."})
    if current_clean is None:
        reasons.append({"code": "current-source-cleanliness-unknown", "message": "Source cleanliness could not be verified."})
    for audit, message in sorted(catalog_failures.items()):
        reasons.append(
            {
                "code": "catalog-unavailable",
                "audit": audit,
                "message": message,
            }
        )

    for audit in audit_order:
        if audit not in catalogs:
            continue
        catalog = catalogs[audit]
        candidate = selected.get(audit)
        if candidate is None:
            continue
        catalog_by_id = {check.check_id: check for check in catalog.checks}
        possible = Decimal(0)
        earned = Decimal(0)
        audit_applicable = 0
        audit_evaluated = 0
        status_counts = {status: 0 for status in sorted(VALID_STATUSES)}
        not_applicable = 0
        not_evaluated = 0
        audit_deductions: list[dict[str, Any]] = []

        for finding in candidate.checks:
            catalog_check = catalog_by_id[finding.check_id]
            if finding.applicability == "not-applicable":
                not_applicable += 1
                continue
            audit_applicable += 1
            applicable_checks += 1
            if finding.evaluation_state != "evaluated" or finding.status is None:
                not_evaluated += 1
                continue
            weight = check_weights["soft" if catalog_check.soft else "standard"]
            possible += weight
            audit_evaluated += 1
            evaluated_checks += 1
            status_counts[finding.status] += 1
            points = status_points[finding.status]
            earned += weight * points
            deduction = weight * (Decimal(1) - points)
            if deduction > 0:
                audit_deductions.append(
                    {
                        "audit": audit,
                        "checkId": finding.check_id,
                        "title": catalog_check.title,
                        "status": finding.status,
                        "pointsLost": deduction,
                    }
                )

        if audit_applicable == 0:
            non_applicable_audits.append(audit)
            category_score: Decimal | None = None
        elif possible <= 0 or audit_evaluated == 0:
            reasons.append(
                {
                    "code": "audit-not-evaluated",
                    "audit": audit,
                    "message": "The audit has applicable checks but none were evaluated.",
                }
            )
            category_score = None
        else:
            category_score = earned / possible * Decimal(100)
            weighted_score += category_score * audit_weights[audit]
            scored_audit_weight += audit_weights[audit]

        for deduction in audit_deductions:
            deduction["categoryPossible"] = possible
            deduction["auditWeight"] = audit_weights[audit]
            deduction["sourceReport"] = findings_reference(
                candidate.source_path, candidate.source_label, audit
            )
            deductions.append(deduction)

        for reason in candidate.provisional_reasons:
            reasons.append(
                {
                    "code": reason,
                    "audit": audit,
                    "message": f"{audit} is provisional because of {reason.replace('-', ' ')}.",
                }
            )
        categories.append(
            {
                "audit": audit,
                "score": None if category_score is None else json_number(category_score, precision),
                "earnedWeight": json_number(earned, precision),
                "possibleWeight": json_number(possible, precision),
                "coverage": (
                    100
                    if audit_applicable == 0
                    else json_number(
                        Decimal(audit_evaluated)
                        / Decimal(audit_applicable)
                        * Decimal(100),
                        2,
                    )
                ),
                "counts": {
                    **status_counts,
                    "notApplicable": not_applicable,
                    "notEvaluated": not_evaluated,
                },
                "selectedRun": {
                    "runIdentifier": candidate.run_identifier,
                    "schemaVersion": candidate.schema_version,
                    "runFinishedAt": candidate.timestamp,
                    "source": findings_reference(
                        candidate.source_path, candidate.source_label, audit
                    ),
                },
            }
        )

    missing_audits = [audit for audit in audit_order if audit not in selected]
    for audit in missing_audits:
        reasons.append(
            {
                "code": "audit-missing",
                "audit": audit,
                "message": f"No eligible {audit} findings were found for the target commit.",
            }
        )

    if scored_audit_weight == 0:
        status = "unavailable"
        overall: Decimal | None = None
        band: str | None = None
        reasons.append(
            {
                "code": "no-scoreable-categories",
                "message": "No selected audit contains an applicable evaluated check.",
            }
        )
    else:
        overall = weighted_score / scored_audit_weight
        rounded_overall = quantize(overall, precision)
        band = next(
            item["name"]
            for item in policy["bands"]
            if rounded_overall >= decimal_value(item["minimum"], "band minimum")
        )
        official = (
            not missing_audits
            and not catalog_failures
            and commit is not None
            and current_clean is True
            and all(candidate.canonical and not candidate.provisional_reasons for candidate in selected.values())
        )
        status = "official" if official else "provisional"

    unique_reasons: list[dict[str, str]] = []
    seen_reason_keys: set[tuple[str, str]] = set()
    for reason in reasons:
        key = (reason["code"], reason.get("audit", ""))
        if key not in seen_reason_keys:
            seen_reason_keys.add(key)
            unique_reasons.append(reason)

    scored_deductions: list[dict[str, Any]] = []
    if scored_audit_weight > 0:
        for item in deductions:
            category_possible = item["categoryPossible"]
            if category_possible <= 0:
                continue
            impact = (
                item["pointsLost"]
                / category_possible
                * Decimal(100)
                * item["auditWeight"]
                / scored_audit_weight
            )
            scored_deductions.append(
                {
                    "audit": item["audit"],
                    "checkId": item["checkId"],
                    "title": item["title"],
                    "status": item["status"],
                    "pointsLost": json_number(item["pointsLost"], precision),
                    "overallImpact": json_number(impact, precision),
                    "sourceReport": item["sourceReport"],
                    "_impact": impact,
                }
            )
    scored_deductions.sort(
        key=lambda item: (-item["_impact"], item["audit"], item["checkId"])
    )
    top_deductions = [
        {key: value for key, value in item.items() if key != "_impact"}
        for item in scored_deductions[:10]
    ]

    input_catalogs = [
        {
            "audit": audit,
            "schemaVersion": catalogs[audit].schema_version,
            "catalogVersion": catalogs[audit].catalog_version,
            "sha256": fingerprints[catalogs[audit].path.resolve()]["sha256"],
        }
        for audit in audit_order
        if audit in catalogs
    ]
    input_findings: list[dict[str, Any]] = []
    for audit in audit_order:
        candidate = selected.get(audit)
        if candidate is None:
            continue
        source_fingerprint = fingerprints.get(candidate.source_path.resolve())
        metadata_fingerprint = fingerprints.get(
            candidate.source_path.with_name("metadata.json").resolve()
        )
        item: dict[str, Any] = {
            "audit": audit,
            "runIdentifier": candidate.run_identifier,
            "source": findings_reference(
                candidate.source_path, candidate.source_label, audit
            ),
            "findingsSha256": (
                source_fingerprint["sha256"] if source_fingerprint else None
            ),
            "canonical": candidate.canonical,
            "filtersApplied": candidate.filters_applied,
            "thresholdOverrides": candidate.threshold_overrides,
            "policyOverrides": candidate.policy_overrides,
            "graphAvailable": candidate.graph_available,
            "sourceWorkingTreeClean": candidate.source_working_tree_clean,
            "qualificationReasons": candidate.provisional_reasons,
        }
        if metadata_fingerprint:
            item["metadataSha256"] = metadata_fingerprint["sha256"]
        input_findings.append(item)

    run_finished_at = utc_now()
    return {
        "schemaVersion": "1.0.0",
        "policyVersion": policy["policyVersion"],
        "runIdentifier": str(uuid.uuid4()),
        "runStartedAt": run_started_at,
        "runFinishedAt": run_finished_at,
        "target": {
            "repository": repository_root.name,
            "gitCommit": commit,
            "sourceWorkingTreeClean": current_clean,
        },
        "status": status,
        "statusReasons": unique_reasons,
        "overallScore": None if overall is None else json_number(overall, precision),
        "qualityBand": band,
        "coverage": {
            "catalogsLoaded": len(catalogs),
            "catalogsExpected": len(audit_order),
            "auditsSelected": len(selected),
            "auditsExpected": len(audit_order),
            "auditsScored": sum(category["score"] is not None for category in categories),
            "auditsNonApplicable": len(non_applicable_audits),
            "checksEvaluated": evaluated_checks,
            "checksApplicable": applicable_checks,
            "checksInCatalogs": total_catalog_checks,
            "evaluationPercent": (
                0
                if applicable_checks == 0
                else json_number(
                    Decimal(evaluated_checks)
                    / Decimal(applicable_checks)
                    * Decimal(100),
                    2,
                )
            ),
        },
        "categories": categories,
        "highestImpactDeductions": top_deductions,
        "missingAudits": missing_audits,
        "excludedCandidates": sorted(
            excluded, key=lambda item: (item["audit"], item["source"], item["reason"])
        ),
        "nonApplicableAudits": non_applicable_audits,
        "inputCatalogs": input_catalogs,
        "inputFindings": input_findings,
        "scorePolicy": {
            "path": "repository-quality-score/score-policy.json",
            "sha256": fingerprints[policy_path.resolve()]["sha256"],
        },
        "discovery": {
            "currentWorktreeOnly": current_worktree_only,
            "worktreesInspected": [worktree.label for worktree in worktrees],
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    score = display_number(result["overallScore"])
    band = result["qualityBand"] or "unavailable"
    lines = [
        "# Repository Quality Score",
        "",
        f"- Repository: **{result['target']['repository']}**",
        f"- Commit: `{result['target']['gitCommit'] or 'unavailable'}`",
        f"- Status: **{result['status']}**",
        f"- Score: **{score} / 100**",
        f"- Quality band: **{band}**",
    ]

    lines.extend(
        [
            "",
            "## Status warnings" if result["status"] != "official" else "## Qualification",
            "",
        ]
    )
    if result["status"] == "official":
        lines.append(
            "Every policy audit supplied compatible canonical evidence for the current clean commit."
        )
    elif result["statusReasons"]:
        for reason in result["statusReasons"]:
            scope = f" ({reason['audit']})" if reason.get("audit") else ""
            lines.append(f"- `{reason['code']}`{scope}: {reason['message']}")
    else:
        lines.append("No scoreable evidence was found.")

    coverage = result["coverage"]
    lines.extend(
        [
            "",
            "## Coverage",
            "",
            "| Measure | Result |",
            "| --- | ---: |",
            f"| Catalogs loaded | {coverage['catalogsLoaded']}/{coverage['catalogsExpected']} |",
            f"| Audits selected | {coverage['auditsSelected']}/{coverage['auditsExpected']} |",
            f"| Audits scored | {coverage['auditsScored']} |",
            f"| Audits non-applicable | {coverage['auditsNonApplicable']} |",
            f"| Applicable checks evaluated | {coverage['checksEvaluated']}/{coverage['checksApplicable']} ({display_number(coverage['evaluationPercent'])}%) |",
            "",
            "## Category scores",
            "",
            "| Audit | Score | Coverage | Present | Partial | Missing | Violation | N/A | Not evaluated |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    category_by_audit = {category["audit"]: category for category in result["categories"]}
    for audit in [item["audit"] for item in result["categories"]] + result["missingAudits"]:
        category = category_by_audit.get(audit)
        if category is None:
            lines.append(f"| {audit} | unavailable | 0.00% | 0 | 0 | 0 | 0 | 0 | 0 |")
            continue
        counts = category["counts"]
        lines.append(
            f"| {audit} | {display_number(category['score'])} | {display_number(category['coverage'])}% | "
            f"{counts['present']} | {counts['partial']} | {counts['missing']} | "
            f"{counts['violation']} | {counts['notApplicable']} | {counts['notEvaluated']} |"
        )

    lines.extend(["", "## Highest-impact deductions", ""])
    if result["highestImpactDeductions"]:
        for item in result["highestImpactDeductions"]:
            lines.append(
                f"- `{item['checkId']}` — {item['title']}: {item['status']}; "
                f"-{display_number(item['overallImpact'])} overall points. "
                f"Source: `{item['sourceReport']}`"
            )
    else:
        lines.append("No scored deductions were found.")

    lines.extend(["", "## Missing and excluded inputs", ""])
    if result["missingAudits"]:
        lines.append(f"- Missing audits: {', '.join(result['missingAudits'])}")
    if result["nonApplicableAudits"]:
        lines.append(
            f"- Explicitly non-applicable audits: {', '.join(result['nonApplicableAudits'])}"
        )
    for item in result["excludedCandidates"]:
        lines.append(f"- Excluded `{item['source']}`: {item['reason']}")
    if (
        not result["missingAudits"]
        and not result["nonApplicableAudits"]
        and not result["excludedCandidates"]
    ):
        lines.append("No audit inputs were missing or excluded.")

    lines.extend(
        [
            "",
            "## Scoring method",
            "",
            f"Policy version `{result['policyVersion']}` assigns present=1, partial=0.5, "
            "missing=0, and violation=0. Standard checks weigh 1.0 and soft checks 0.5. "
            "Each audit is normalized before equally weighted audit categories are averaged. "
            "Missing and unevaluated evidence reduces coverage instead of becoming a guessed pass or failure.",
            "",
            "## Obtain an official score",
            "",
            (
                "This result is already official."
                if result["status"] == "official"
                else "Run or rerun the named audits without filters or policy overrides on the current clean commit, then calculate again."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def render_snapshot(result: dict[str, Any]) -> str:
    lines = [
        "# Repository Quality Score input snapshot",
        "",
        f"- Commit: `{result['target']['gitCommit'] or 'unavailable'}`",
        f"- Source tree clean: `{result['target']['sourceWorkingTreeClean']}`",
        f"- Current worktree only: `{result['discovery']['currentWorktreeOnly']}`",
        f"- Worktrees inspected: {', '.join(result['discovery']['worktreesInspected'])}",
        f"- Policy: `{result['policyVersion']}` (`{result['scorePolicy']['sha256']}`)",
        "",
        "## Catalogs",
        "",
    ]
    for catalog in result["inputCatalogs"]:
        lines.append(
            f"- {catalog['audit']}: schema {catalog['schemaVersion']}, "
            f"catalog {catalog['catalogVersion']}, SHA-256 `{catalog['sha256']}`"
        )
    lines.extend(["", "## Selected runs", ""])
    if result["inputFindings"]:
        for item in result["inputFindings"]:
            lines.append(
                f"- {item['audit']}: `{item['runIdentifier']}` from `{item['source']}` "
                f"(findings SHA-256 `{item['findingsSha256']}`; canonical "
                f"`{item['canonical']}`; filters `{item['filtersApplied']}`; "
                f"threshold overrides `{item['thresholdOverrides']}`; policy overrides "
                f"`{item['policyOverrides']}`; graph `{item['graphAvailable']}`; "
                f"qualification `{', '.join(item['qualificationReasons']) or 'none'}`)"
            )
    else:
        lines.append("No audit run was selected.")
    lines.extend(["", "## Exclusions", ""])
    if result["excludedCandidates"]:
        for item in result["excludedCandidates"]:
            lines.append(f"- `{item['source']}`: {item['reason']}")
    else:
        lines.append("No candidate was excluded.")
    lines.append("")
    return "\n".join(lines)


def verify_fingerprints(fingerprints: dict[Path, dict[str, Any]]) -> bool:
    for path, expected in fingerprints.items():
        try:
            if file_fingerprint(path) != expected:
                return False
        except OSError:
            return False
    return True


def acquire_lock(lock_path: Path) -> int:
    try:
        return os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        try:
            started = datetime.fromtimestamp(
                lock_path.stat().st_mtime, timezone.utc
            ).isoformat(timespec="seconds")
        except OSError:
            started = "unknown"
        raise ScoreInputError(
            "another score calculation owns "
            ".architect-audits/repository-quality-score/.lock "
            f"(lock timestamp: {started})"
        ) from exc


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as handle:
        handle.write(content)
        temp_path = Path(handle.name)
    try:
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def write_reports(repository_root: Path, result: dict[str, Any]) -> Path:
    repository_root = repository_root.resolve(strict=True)
    audit_root = repository_root / ".architect-audits"
    if audit_root.exists():
        resolved_audit_root = audit_root.resolve(strict=True)
        try:
            resolved_audit_root.relative_to(repository_root)
        except ValueError as exc:
            raise ScoreInputError(
                ".architect-audits resolves outside the target repository"
            ) from exc
    output_root = audit_root / "repository-quality-score"
    if output_root.exists():
        resolved_output_root = output_root.resolve(strict=True)
        try:
            resolved_output_root.relative_to(repository_root)
        except ValueError as exc:
            raise ScoreInputError(
                "Repository Quality Score output resolves outside the target repository"
            ) from exc
    output_root.mkdir(parents=True, exist_ok=True)
    lock_path = output_root / ".lock"
    lock_fd = acquire_lock(lock_path)
    try:
        metadata = {
            "schemaVersion": result["schemaVersion"],
            "skillName": "repository-quality-score",
            "skillVersion": "1.0.0",
            "policyVersion": result["policyVersion"],
            "runIdentifier": result["runIdentifier"],
            "runStartedAt": result["runStartedAt"],
            "runFinishedAt": result["runFinishedAt"],
            "target": result["target"],
            "currentWorktreeOnly": result["discovery"]["currentWorktreeOnly"],
            "scorePolicy": result["scorePolicy"],
            "inputCatalogs": result["inputCatalogs"],
            "inputFindings": result["inputFindings"],
            "status": result["status"],
            "statusReasonCodes": [
                reason["code"] for reason in result["statusReasons"]
            ],
        }
        rendered = {
            "score.md": render_markdown(result),
            "snapshot.md": render_snapshot(result),
            "metadata.json": json.dumps(metadata, indent=2) + "\n",
            "score.json": json.dumps(result, indent=2) + "\n",
        }
        atomic_write(output_root / "score.md", rendered["score.md"])
        atomic_write(output_root / "snapshot.md", rendered["snapshot.md"])
        atomic_write(output_root / "metadata.json", rendered["metadata.json"])
        # score.json is the completion marker and is intentionally published last.
        atomic_write(output_root / "score.json", rendered["score.json"])
    finally:
        os.close(lock_fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
    return output_root


def build_result(
    target: Path,
    skills_root: Path,
    current_worktree_only: bool,
) -> tuple[Path, dict[str, Any], dict[Path, dict[str, Any]]]:
    repository_root = resolve_repository_root(target)
    skills_root = skills_root.expanduser().resolve(strict=True)
    fingerprints: dict[Path, dict[str, Any]] = {}
    policy, policy_path = load_policy(skills_root, fingerprints)
    audit_order = [item["name"] for item in policy["audits"]]
    catalogs: dict[str, AuditCatalog] = {}
    catalog_failures: dict[str, str] = {}
    for audit in audit_order:
        try:
            catalogs[audit] = load_catalog(skills_root, audit, fingerprints)
        except (ScoreInputError, OSError) as exc:
            catalog_failures[audit] = (
                f"The {audit} catalog is unavailable: {safe_exception_message(exc)}"
            )
    commit = current_commit(repository_root)
    clean = is_source_clean(repository_root)
    worktrees = registered_worktrees(repository_root, current_worktree_only)
    candidates: dict[str, list[Candidate]] = {}
    excluded: list[dict[str, str]] = []
    for audit in audit_order:
        if audit not in catalogs:
            continue
        accepted, rejected = discover_candidates(
            worktrees, catalogs[audit], commit, fingerprints
        )
        candidates[audit] = accepted
        excluded.extend(rejected)
    selected, not_selected = select_candidates(candidates, audit_order)
    excluded.extend(not_selected)
    result = calculate(
        repository_root,
        policy,
        policy_path,
        catalogs,
        catalog_failures,
        selected,
        excluded,
        commit,
        clean,
        worktrees,
        current_worktree_only,
        fingerprints,
    )
    return repository_root, result, fingerprints


def parse_args(argv: list[str]) -> argparse.Namespace:
    default_skills_root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(
        description="Calculate Repository Quality Score from existing Architect Playbook findings."
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=Path.cwd(),
        help="Repository to score (default: current directory).",
    )
    parser.add_argument(
        "--skills-root",
        type=Path,
        default=default_skills_root,
        help="Architect Playbook root containing audit skills and score policy.",
    )
    parser.add_argument(
        "--current-worktree-only",
        action="store_true",
        help="Ignore findings in other registered Git worktrees.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        for attempt in range(2):
            repository_root, result, fingerprints = build_result(
                args.target, args.skills_root, args.current_worktree_only
            )
            if verify_fingerprints(fingerprints):
                break
            if attempt == 1:
                raise UnstableInputError(
                    "scoring inputs changed during calculation; retry after audit writers finish"
                )
        output_root = write_reports(repository_root, result)
        score = display_number(result["overallScore"])
        print(
            f"RQS {result['status']}: {score} "
            f"({result['coverage']['auditsSelected']}/{result['coverage']['auditsExpected']} audits)"
        )
        print(f"Reports: {output_root}")
        return 2 if result["status"] == "unavailable" else 0
    except UnstableInputError as exc:
        print(f"RQS unavailable: {exc}", file=sys.stderr)
        return 2
    except ScoreInputError as exc:
        print(f"RQS error: {exc}", file=sys.stderr)
        return 1
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"RQS error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
