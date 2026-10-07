#!/usr/bin/env python3
"""Stage, record, verify, and publish one Architect Playbook audit run.

Audits never write their findings files by hand. They begin a staged run, record
one decision for every catalog check, and finish the run. Finishing validates
the whole run with the Repository Quality Score calculator's own contract code,
re-verifies every cited file, line, and quoted fragment, renders the Markdown
reports from the JSON, and publishes all four files together. A run that fails
validation publishes nothing.

Standard library only. Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "1.0.0"
SKILLS_ROOT = Path(__file__).resolve().parents[2]
CALCULATOR_PATH = (
    SKILLS_ROOT / "repository-quality-score" / "scripts" / "calculate_repository_quality_score.py"
)
AUDITS_DIRECTORY = ".architect-audits"
STAGING_DIRECTORY = ".staging"
DECISIONS_FILE = "decisions.json"
PENDING_REASON = "pending"
STATUSES = ("present", "partial", "missing", "violation")
GRADED_STATUSES = ("partial", "missing", "violation")
TIERS = ("direct", "supported", "inferred")
JUDGEMENTS = ("act-on", "consider", "noted", "dismissed")
DECISION_KINDS = ("accepted-risk", "false-positive", "out-of-scope")
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_SEARCH_FILES = 20_000
MAX_SEARCH_FILE_BYTES = 2 * 1024 * 1024
SKIPPED_SEARCH_DIRECTORIES = {".git", "node_modules", AUDITS_DIRECTORY, "graphify-out", ".worktrees"}

CITATION_PATTERN = re.compile(
    r"^(?P<path>[^\s:`][^\s:]*):(?P<start>[0-9]+)(?:-(?P<end>[0-9]+))?(?:\s+(?:—|--)\s+(?P<note>.*))?$"
)
FILE_PATTERN = re.compile(r"^(?P<path>[^\s:`][^\s:]*)(?:\s+(?:—|--)\s+(?P<note>.*))?$")
COMMAND_PATTERN = re.compile(r"^command:\s+`(?P<command>[^`]+)`\s+(?:→|->)\s+(?P<result>.+)$")
SEARCH_PATTERN = re.compile(
    r"^search:\s+`(?P<pattern>[^`]+)`\s+in\s+(?P<scope>\S+)\s+(?:→|->)\s+(?P<count>[0-9]+)\s+match(?:es|ing lines?)?\b.*$"
)
QUOTE_PATTERN = re.compile(r"`([^`]+)`")
SECRET_PATTERNS = (
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bsk-(?:live|proj|ant)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
)


class ProtocolError(Exception):
    """A user-facing error that explains exactly what to fix."""


def load_calculator() -> Any:
    if not CALCULATOR_PATH.is_file():
        raise ProtocolError(
            "the Repository Quality Score calculator is missing; install the complete playbook "
            f"(expected {CALCULATOR_PATH.relative_to(SKILLS_ROOT)})"
        )
    spec = importlib.util.spec_from_file_location("architect_playbook_calculator", CALCULATOR_PATH)
    if spec is None or spec.loader is None:
        raise ProtocolError("cannot load the Repository Quality Score calculator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


CALCULATOR = load_calculator()


# --------------------------------------------------------------------------- helpers


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def write_json_atomic(path: Path, value: Any) -> None:
    write_text_atomic(path, json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ProtocolError(f"{label} not found: {path}") from error
    except json.JSONDecodeError as error:
        raise ProtocolError(f"{label} is not valid JSON: {error.msg} at line {error.lineno}") from error


def git(root: Path, *arguments: str) -> str | None:
    return CALCULATOR.git_output(root, list(arguments))


def repository_root(argument: str | None) -> Path:
    start = Path(argument) if argument else Path.cwd()
    if not start.exists():
        raise ProtocolError(f"repository path does not exist: {start}")
    return CALCULATOR.resolve_repository_root(start)


def repository_name(root: Path) -> str:
    remote = git(root, "remote", "get-url", "origin") or ""
    match = re.search(r"[:/]([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?$", remote)
    if match and "://" not in match.group(1):
        return f"{match.group(1)}/{match.group(2)}"
    name = re.sub(r"[^A-Za-z0-9_.-]", "-", root.name) or "repository"
    return name if name not in {".", ".."} else "repository"


def tree_fingerprint(root: Path) -> str:
    status = git(
        root,
        "status",
        "--porcelain",
        "--untracked-files=all",
        "--",
        ".",
        f":(exclude){AUDITS_DIRECTORY}/**",
    )
    head = git(root, "rev-parse", "HEAD") or "no-head"
    return hashlib.sha256(f"{head}\n{status or ''}".encode("utf-8")).hexdigest()


def audit_directory(audit: str) -> Path:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", audit):
        raise ProtocolError(f"invalid audit name: {audit!r}")
    directory = SKILLS_ROOT / audit
    if not (directory / "checks.json").is_file():
        raise ProtocolError(
            f"no catalog for {audit}: expected {audit}/checks.json beside this protocol skill"
        )
    return directory


def staging_path(root: Path, audit: str) -> Path:
    return root / AUDITS_DIRECTORY / audit / STAGING_DIRECTORY / "run.json"


def load_run(root: Path, audit: str) -> dict[str, Any]:
    path = staging_path(root, audit)
    if not path.is_file():
        raise ProtocolError(f"no staged run for {audit}; run `audit_run.py begin {audit}` first")
    run = read_json(path, "staged run")
    if not isinstance(run, dict) or run.get("protocolVersion") != PROTOCOL_VERSION:
        raise ProtocolError("the staged run was written by a different protocol version; begin again with --restart")
    return run


def save_run(root: Path, audit: str, run: dict[str, Any]) -> None:
    write_json_atomic(staging_path(root, audit), run)


def find_check(run: dict[str, Any], check_id: str) -> dict[str, Any]:
    for check in run["checks"]:
        if check["checkId"] == check_id:
            return check
    known = ", ".join(check["checkId"] for check in run["checks"])
    raise ProtocolError(f"unknown checkId {check_id!r}; catalog checks are: {known}")


def contains_secret(text: str) -> bool:
    return any(pattern.search(text) for pattern in SECRET_PATTERNS)


def normalize(text: str) -> str:
    return " ".join(text.split())


def safe_relative_path(root: Path, raw: str) -> Path:
    if raw.startswith(("/", "~")) or re.match(r"^[A-Za-z]:", raw) or "\\" in raw:
        raise ProtocolError(f"cite repository-relative paths only: {raw!r}")
    candidate = (root / raw).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as error:
        raise ProtocolError(f"cited path escapes the repository: {raw!r}") from error
    return candidate


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def verify_quotes(note: str | None, text: str, where: str) -> None:
    if not note:
        return
    haystack = normalize(text)
    for quote in QUOTE_PATTERN.findall(note):
        if normalize(quote) not in haystack:
            raise ProtocolError(f"quoted text `{quote}` does not appear in {where}")


def search_files(root: Path, scope: str) -> list[Path]:
    if any(character in scope for character in "*?["):
        matches = [
            path
            for path in iter_text_files(root)
            if fnmatch.fnmatch(path.relative_to(root).as_posix(), scope)
        ]
        return matches
    target = safe_relative_path(root, scope.rstrip("/") or ".")
    if target.is_file():
        return [target]
    if target.is_dir():
        return list(iter_text_files(target))
    raise ProtocolError(f"search scope does not exist: {scope!r}")


def iter_text_files(directory: Path) -> list[Path]:
    files: list[Path] = []
    for current, directories, names in os.walk(directory):
        directories[:] = [name for name in directories if name not in SKIPPED_SEARCH_DIRECTORIES]
        for name in names:
            path = Path(current) / name
            try:
                if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
                    continue
            except OSError:
                continue
            files.append(path)
            if len(files) > MAX_SEARCH_FILES:
                raise ProtocolError(
                    f"search scope is too large to verify (more than {MAX_SEARCH_FILES} files); narrow the scope"
                )
    return files


def count_matching_lines(files: list[Path], pattern: str) -> int:
    if pattern.startswith("re:"):
        try:
            expression = re.compile(pattern[3:])
        except re.error as error:
            raise ProtocolError(f"invalid search regular expression {pattern[3:]!r}: {error}") from error
        matcher = expression.search
    else:
        literal = pattern

        def matcher(line: str) -> bool:
            return literal in line

    total = 0
    for path in files:
        try:
            with path.open("r", encoding="utf-8", errors="strict") as stream:
                for line in stream:
                    if matcher(line):
                        total += 1
        except (UnicodeDecodeError, OSError):
            continue
    return total


def verify_evidence(root: Path, entry: str) -> str:
    """Verify one evidence entry and return its kind.

    Kinds: citation, file, command, search, observation. Citations, files, and
    searches are re-checked against the repository; commands are recorded as
    reported tool output; observations are free text.
    """
    if not isinstance(entry, str) or not entry.strip():
        raise ProtocolError("evidence entries must be non-empty strings")
    if contains_secret(entry):
        raise ProtocolError("evidence looks like it contains a secret; redact it as <REDACTED>")
    text = entry.strip()
    command = COMMAND_PATTERN.match(text)
    if command:
        return "command"
    search = SEARCH_PATTERN.match(text)
    if search:
        files = search_files(root, search.group("scope"))
        actual = count_matching_lines(files, search.group("pattern"))
        claimed = int(search.group("count"))
        if actual != claimed:
            raise ProtocolError(
                f"search evidence claims {claimed} matching lines for `{search.group('pattern')}` "
                f"in {search.group('scope')}, but the repository has {actual}"
            )
        return "search"
    if text.startswith(("search:", "command:")):
        raise ProtocolError(
            "malformed evidence; use `command: `<command>` → <result>` or "
            "`search: `<pattern>` in <scope> → <count> matches`"
        )
    citation = CITATION_PATTERN.match(text)
    if citation:
        path = safe_relative_path(root, citation.group("path"))
        if not path.is_file():
            raise ProtocolError(f"cited file does not exist: {citation.group('path')}")
        lines = read_lines(path)
        start = int(citation.group("start"))
        end = int(citation.group("end") or start)
        if start < 1 or end < start or end > len(lines):
            raise ProtocolError(
                f"cited lines {start}-{end} are outside {citation.group('path')} ({len(lines)} lines)"
            )
        verify_quotes(
            citation.group("note"),
            "\n".join(lines[start - 1 : end]),
            f"{citation.group('path')}:{start}-{end}",
        )
        return "citation"
    file_entry = FILE_PATTERN.match(text)
    if file_entry and looks_like_path(root, file_entry.group("path")):
        path = safe_relative_path(root, file_entry.group("path"))
        if path.is_file():
            verify_quotes(file_entry.group("note"), path.read_text(encoding="utf-8", errors="replace"), file_entry.group("path"))
            return "file"
        if path.is_dir():
            return "file"
        if file_entry.group("note") is not None:
            raise ProtocolError(f"cited file does not exist: {file_entry.group('path')}")
    return "observation"


def looks_like_path(root: Path, raw: str) -> bool:
    """A bare token is a path if it has a separator or extension, or names an existing file."""
    if "/" in raw or "." in raw:
        return True
    try:
        return safe_relative_path(root, raw).exists()
    except ProtocolError:
        return False


def decision_applies(decision: dict[str, Any], check_id: str, cited_paths: list[str]) -> bool:
    if decision.get("checkId") != check_id:
        return False
    scope = decision.get("scope") or ["**"]
    if scope == ["**"]:
        return True
    if not cited_paths:
        return False
    return all(any(fnmatch.fnmatch(path, pattern) for pattern in scope) for path in cited_paths)


def cited_paths(evidence: list[str]) -> list[str]:
    paths = []
    for entry in evidence:
        citation = CITATION_PATTERN.match(entry.strip())
        if citation:
            paths.append(citation.group("path"))
            continue
        file_entry = FILE_PATTERN.match(entry.strip())
        if file_entry and ("/" in file_entry.group("path") or "." in file_entry.group("path")):
            paths.append(file_entry.group("path"))
    return paths


def load_decisions(root: Path) -> list[dict[str, Any]]:
    path = root / AUDITS_DIRECTORY / DECISIONS_FILE
    if not path.is_file():
        return []
    data = read_json(path, "decisions file")
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
        raise ProtocolError(f"{AUDITS_DIRECTORY}/{DECISIONS_FILE} must be an object with a decisions array")
    return [item for item in data["decisions"] if isinstance(item, dict)]


# --------------------------------------------------------------------------- commands


def command_begin(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    audit = arguments.audit
    directory = audit_directory(audit)
    catalog_data = read_json(directory / "checks.json", f"{audit}/checks.json")
    catalog = CALCULATOR.load_catalog(SKILLS_ROOT, audit, {})
    path = staging_path(root, audit)
    if path.exists() and not arguments.restart:
        raise ProtocolError(
            f"a staged {audit} run already exists; continue it, or begin again with --restart to discard it"
        )
    commit = CALCULATOR.current_commit(root)
    if commit is None:
        raise ProtocolError("the target must be a Git repository with at least one commit")
    clean = CALCULATOR.is_source_clean(root)
    if clean is None:
        raise ProtocolError("cannot determine whether the working tree is clean")

    thresholds: dict[str, str] = {}
    for item in arguments.threshold or []:
        key, separator, value = item.partition("=")
        if not separator or not key.strip():
            raise ProtocolError(f"thresholds use key=value: {item!r}")
        thresholds[key.strip()] = value.strip()
    filters = list(arguments.filter or [])
    changed_files: list[str] = []
    if arguments.since:
        if not git(root, "rev-parse", "--verify", f"{arguments.since}^{{commit}}"):
            raise ProtocolError(f"--since reference does not resolve to a commit: {arguments.since}")
        diff = git(root, "diff", "--name-only", f"{arguments.since}...HEAD") or ""
        changed_files = [line for line in diff.splitlines() if line.strip()]
        filters.append(f"--since={arguments.since}")

    catalog_checks = {check["checkId"]: check for check in catalog_data.get("checks", [])}
    decisions = [decision for decision in load_decisions(root) if str(decision.get("checkId", "")).startswith(f"{audit}.")]
    run: dict[str, Any] = {
        "protocolVersion": PROTOCOL_VERSION,
        "runIdentifier": uuid.uuid4().hex,
        "skillName": audit,
        "skillVersion": catalog.catalog_version,
        "checkCatalogSchemaVersion": catalog.schema_version,
        "checkCatalogVersion": catalog.catalog_version,
        "runStartedAt": utc_now(),
        "target": {
            "repository": repository_name(root),
            "gitCommit": commit,
            "sourceWorkingTreeClean": clean,
        },
        "execution": {
            "filtersApplied": bool(filters),
            "filterArguments": filters,
            "thresholdOverrides": thresholds,
            "policyOverrides": {},
            "enrichmentArguments": list(arguments.enrichment or []),
            "graphAvailable": (root / "graphify-out" / "graph.json").is_file(),
        },
        "treeFingerprint": tree_fingerprint(root),
        "scope": {"since": arguments.since, "changedFiles": changed_files},
        "decisions": decisions,
        "snapshot": {},
        "hypotheses": [],
        "auditApplicability": None,
        "checks": [],
    }
    for check in catalog.checks:
        source = catalog_checks.get(check.check_id, {})
        run["checks"].append(
            {
                "checkId": check.check_id,
                "layer": check.layer,
                "title": check.title,
                "severity": source.get("severity"),
                "method": source.get("method"),
                "rationale": source.get("rationale"),
                "applicability": "applicable",
                "applicabilityReason": None,
                "evaluationState": "not-evaluated",
                "evaluationReason": PENDING_REASON,
                "evidenceQuality": "none",
                "classification": "observed",
                "status": None,
                "evidence": [],
                "gap": None,
                "remediation": None,
                "evidenceTier": None,
                "judgement": None,
                "judgementReason": None,
                "decisionId": None,
                "recordedBy": None,
            }
        )
    save_run(root, audit, run)

    collector = directory / "scripts" / "collect.py"
    collected = 0
    if collector.is_file():
        collected = apply_collector(root, audit, run, collector, arguments.enrichment or [])
        save_run(root, audit, run)

    pending = [check["checkId"] for check in run["checks"] if check["evaluationReason"] == PENDING_REASON]
    print(f"Began {audit} run {run['runIdentifier']} at {commit[:12]} ({'clean' if clean else 'dirty'} working tree).")
    if not clean:
        print("Note: the working tree has uncommitted changes, so this run cannot feed an official score.")
    if changed_files:
        print(f"Diff scope: {len(changed_files)} files changed since {arguments.since}.")
    if decisions:
        print(f"Recorded decisions that may apply: {', '.join(str(item.get('id')) for item in decisions)}")
    if collector.is_file():
        print(f"Collector resolved {collected} checks.")
    print(f"Pending checks ({len(pending)}): {', '.join(pending)}")
    return 0


def apply_collector(
    root: Path,
    audit: str,
    run: dict[str, Any],
    collector: Path,
    enrichment: list[str],
) -> int:
    command = [sys.executable, str(collector), "--repository", str(root)]
    for flag in enrichment:
        command.extend(["--enrichment", flag])
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=600,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"Warning: collector did not complete ({type(error).__name__}); its checks stay pending.")
        return 0
    if completed.returncode != 0:
        print(f"Warning: collector exited {completed.returncode}; its checks stay pending.")
        if completed.stderr.strip():
            print(completed.stderr.strip()[:2000])
        return 0
    try:
        output = json.loads(completed.stdout)
    except json.JSONDecodeError:
        print("Warning: collector output was not JSON; its checks stay pending.")
        return 0
    resolved = 0
    for check_id, result in (output.get("checks") or {}).items():
        try:
            check = find_check(run, check_id)
            apply_result(root, run, check, result, recorded_by="collector")
            resolved += 1
        except ProtocolError as error:
            print(f"Warning: collector result for {check_id} rejected: {error}")
    snapshot = output.get("snapshot")
    if isinstance(snapshot, dict):
        run["snapshot"].update(snapshot)
    return resolved


def apply_result(
    root: Path,
    run: dict[str, Any],
    check: dict[str, Any],
    result: dict[str, Any],
    recorded_by: str,
) -> None:
    if result.get("applicability") == "not-applicable":
        mark_not_applicable(check, require_reason(result.get("reason")), recorded_by)
        return
    if result.get("evaluationState") == "not-evaluated":
        mark_not_evaluated(check, require_reason(result.get("reason")), recorded_by)
        return
    record_evaluation(
        root,
        run,
        check,
        status=result.get("status"),
        evidence=list(result.get("evidence") or []),
        tier=result.get("evidenceTier", "direct"),
        gap=result.get("gap"),
        remediation=result.get("remediation"),
        judgement=result.get("judgement"),
        judgement_reason=result.get("judgementReason"),
        classification=result.get("classification"),
        degraded=result.get("degradedReason"),
        recorded_by=recorded_by,
    )


def require_reason(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ProtocolError("a reason is required")
    if value.strip() == PENDING_REASON:
        raise ProtocolError(f"{PENDING_REASON!r} is reserved for unrecorded checks")
    return value.strip()


def mark_not_applicable(check: dict[str, Any], reason: str, recorded_by: str) -> None:
    check.update(
        {
            "applicability": "not-applicable",
            "applicabilityReason": reason,
            "evaluationState": "not-evaluated",
            "evaluationReason": None,
            "evidenceQuality": "none",
            "status": None,
            "evidence": [],
            "gap": None,
            "remediation": None,
            "evidenceTier": None,
            "judgement": None,
            "judgementReason": None,
            "decisionId": None,
            "recordedBy": recorded_by,
        }
    )


def mark_not_evaluated(check: dict[str, Any], reason: str, recorded_by: str) -> None:
    check.update(
        {
            "applicability": "applicable",
            "applicabilityReason": None,
            "evaluationState": "not-evaluated",
            "evaluationReason": reason,
            "evidenceQuality": "none",
            "status": None,
            "evidence": [],
            "gap": None,
            "remediation": None,
            "evidenceTier": None,
            "judgement": None,
            "judgementReason": None,
            "decisionId": None,
            "recordedBy": recorded_by,
        }
    )


def record_evaluation(
    root: Path,
    run: dict[str, Any],
    check: dict[str, Any],
    *,
    status: Any,
    evidence: list[str],
    tier: Any,
    gap: Any,
    remediation: Any,
    judgement: Any,
    judgement_reason: Any,
    classification: Any,
    degraded: Any,
    recorded_by: str,
) -> None:
    catalog = CALCULATOR.load_catalog(SKILLS_ROOT, run["skillName"], {})
    catalog_check = next(item for item in catalog.checks if item.check_id == check["checkId"])
    if status not in STATUSES:
        raise ProtocolError(f"status must be one of {', '.join(STATUSES)}")
    if status not in catalog_check.allowed_statuses:
        raise ProtocolError(
            f"{check['checkId']} allows only {', '.join(sorted(catalog_check.allowed_statuses))}"
        )
    if not evidence:
        raise ProtocolError("every evaluated check needs at least one evidence entry")
    kinds = [verify_evidence(root, entry) for entry in evidence]
    if not any(kind != "observation" for kind in kinds):
        raise ProtocolError(
            "evidence must include at least one verifiable entry: `path:line — note`, a file path, "
            "`command: `<command>` → <result>`, or `search: `<pattern>` in <scope> → <count> matches`"
        )
    if tier not in TIERS:
        raise ProtocolError(f"evidence tier must be one of {', '.join(TIERS)}")
    if status == "violation" and tier == "inferred":
        raise ProtocolError(
            "a violation needs direct or supported evidence; record the suspicion as a hypothesis "
            "or mark the check not evaluated until you can verify it"
        )
    if status in GRADED_STATUSES:
        if not isinstance(gap, str) or not gap.strip():
            raise ProtocolError(f"a {status} result needs --gap describing what is wrong")
        if not isinstance(remediation, str) or not remediation.strip():
            raise ProtocolError(f"a {status} result needs --remediation describing the smallest fix")
    classification = classification or "observed"
    if classification == "misconfigured" and status not in {"partial", "violation"}:
        raise ProtocolError("the misconfigured classification requires partial or violation")

    decision_id = None
    if status in GRADED_STATUSES:
        applicable_decisions = [
            decision
            for decision in run.get("decisions", [])
            if decision_applies(decision, check["checkId"], cited_paths(evidence))
        ]
        if judgement is None:
            judgement = "noted" if applicable_decisions else "act-on"
        if judgement not in JUDGEMENTS:
            raise ProtocolError(f"judgement must be one of {', '.join(JUDGEMENTS)}")
        if applicable_decisions:
            decision_id = str(applicable_decisions[0].get("id"))
            if judgement in {"act-on", "consider"} and not (isinstance(judgement_reason, str) and judgement_reason.strip()):
                raise ProtocolError(
                    f"recorded decision {decision_id} covers this finding; keep it noted, or give "
                    "--reason explaining why the decision no longer holds"
                )
            if judgement in {"noted", "dismissed"} and not judgement_reason:
                judgement_reason = f"Covered by recorded decision {decision_id}."
        if judgement in {"noted", "dismissed"} and not (isinstance(judgement_reason, str) and judgement_reason.strip()):
            raise ProtocolError(f"a {judgement} finding needs --reason")
    else:
        judgement = None
        judgement_reason = None

    for value in (gap, remediation, judgement_reason):
        if isinstance(value, str) and contains_secret(value):
            raise ProtocolError("text looks like it contains a secret; redact it as <REDACTED>")
    check.update(
        {
            "applicability": "applicable",
            "applicabilityReason": None,
            "evaluationState": "evaluated",
            "evaluationReason": degraded.strip() if isinstance(degraded, str) and degraded.strip() else None,
            "evidenceQuality": "degraded" if isinstance(degraded, str) and degraded.strip() else "complete",
            "classification": classification,
            "status": status,
            "evidence": [entry.strip() for entry in evidence],
            "gap": gap.strip() if isinstance(gap, str) and gap.strip() else None,
            "remediation": remediation.strip() if isinstance(remediation, str) and remediation.strip() else None,
            "evidenceTier": tier,
            "judgement": judgement,
            "judgementReason": judgement_reason.strip() if isinstance(judgement_reason, str) and judgement_reason.strip() else None,
            "decisionId": decision_id,
            "recordedBy": recorded_by,
        }
    )


def command_record(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    run = load_run(root, arguments.audit)
    check = find_check(run, arguments.check)
    record_evaluation(
        root,
        run,
        check,
        status=arguments.status,
        evidence=arguments.evidence or [],
        tier=arguments.tier,
        gap=arguments.gap,
        remediation=arguments.remediation,
        judgement=arguments.judgement,
        judgement_reason=arguments.reason,
        classification=arguments.classification,
        degraded=arguments.degraded,
        recorded_by="model",
    )
    save_run(root, arguments.audit, run)
    print(f"Recorded {check['checkId']}: {check['status']} ({check['evidenceTier']} evidence, judgement {check['judgement'] or 'n/a'}).")
    return 0


def command_not_applicable(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    run = load_run(root, arguments.audit)
    check = find_check(run, arguments.check)
    mark_not_applicable(check, require_reason(arguments.reason), "model")
    save_run(root, arguments.audit, run)
    print(f"Recorded {check['checkId']} as not applicable.")
    return 0


def command_not_evaluated(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    run = load_run(root, arguments.audit)
    check = find_check(run, arguments.check)
    mark_not_evaluated(check, require_reason(arguments.reason), "model")
    save_run(root, arguments.audit, run)
    print(f"Recorded {check['checkId']} as not evaluated.")
    return 0


def command_audit_not_applicable(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    run = load_run(root, arguments.audit)
    reason = require_reason(arguments.reason)
    for check in run["checks"]:
        mark_not_applicable(check, reason, "model")
    run["auditApplicability"] = {"status": "not-applicable", "reason": reason}
    save_run(root, arguments.audit, run)
    print(f"Recorded the whole {arguments.audit} as not applicable: {reason}")
    return 0


def command_snapshot(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    run = load_run(root, arguments.audit)
    if arguments.json:
        value = read_json(Path(arguments.json), "snapshot file")
        if not isinstance(value, dict):
            raise ProtocolError("the snapshot file must contain a JSON object")
        run["snapshot"].update(value)
    for item in arguments.set or []:
        key, separator, raw = item.partition("=")
        if not separator or not key.strip():
            raise ProtocolError(f"snapshot entries use key=value: {item!r}")
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            value = raw
        run["snapshot"][key.strip()] = value
    if arguments.narrative:
        run["snapshot"]["narrative"] = arguments.narrative
    if contains_secret(json.dumps(run["snapshot"])):
        raise ProtocolError("the snapshot looks like it contains a secret; redact it as <REDACTED>")
    save_run(root, arguments.audit, run)
    print(f"Snapshot now has {len(run['snapshot'])} entries.")
    return 0


def command_hypothesis(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    run = load_run(root, arguments.audit)
    find_check(run, arguments.check)
    if contains_secret(arguments.note):
        raise ProtocolError("the note looks like it contains a secret; redact it as <REDACTED>")
    run["hypotheses"].append({"checkId": arguments.check, "note": arguments.note.strip()})
    save_run(root, arguments.audit, run)
    print("Recorded an unverified hypothesis; it appears in the report appendix, never in the score.")
    return 0


def command_status(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    run = load_run(root, arguments.audit)
    pending = [check for check in run["checks"] if check["evaluationReason"] == PENDING_REASON]
    print(f"{arguments.audit} run {run['runIdentifier']}: {len(run['checks']) - len(pending)} of {len(run['checks'])} checks recorded.")
    for check in pending:
        severity = check.get("severity") or "unrated"
        method = check.get("method") or "unspecified"
        print(f"  pending: {check['checkId']} [{severity}, {method}] {check['title']}")
    return 0


def build_documents(run: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    finished = utc_now()
    shared = {
        "schemaVersion": "2.0.0",
        "runIdentifier": run["runIdentifier"],
        "skillName": run["skillName"],
        "skillVersion": run["skillVersion"],
        "checkCatalogSchemaVersion": run["checkCatalogSchemaVersion"],
        "checkCatalogVersion": run["checkCatalogVersion"],
        "runStartedAt": run["runStartedAt"],
        "runFinishedAt": finished,
        "target": run["target"],
        "execution": run["execution"],
    }
    checks = []
    for check in run["checks"]:
        item = {key: value for key, value in check.items() if key not in {"title", "rationale"}}
        checks.append(item)
    summary = summarize(run["checks"])
    findings = {
        **shared,
        "protocolVersion": PROTOCOL_VERSION,
        "summary": summary,
        "snapshot": run["snapshot"],
        "scope": run["scope"],
        "hypotheses": run["hypotheses"],
        "checks": checks,
    }
    if run.get("auditApplicability"):
        findings["auditApplicability"] = run["auditApplicability"]
    metadata = {**shared, "protocolVersion": PROTOCOL_VERSION, "treeFingerprint": run["treeFingerprint"]}
    return findings, metadata


def summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {status: 0 for status in STATUSES}
    judgements = {judgement: 0 for judgement in JUDGEMENTS}
    not_applicable = 0
    not_evaluated = 0
    for check in checks:
        if check["applicability"] == "not-applicable":
            not_applicable += 1
        elif check["status"] is None:
            not_evaluated += 1
        else:
            counts[check["status"]] += 1
            if check.get("judgement") in judgements:
                judgements[check["judgement"]] += 1
    return {
        "statusCounts": counts,
        "notApplicable": not_applicable,
        "notEvaluated": not_evaluated,
        "judgementCounts": judgements,
    }


def ordered(checks: list[dict[str, Any]], judgement: str) -> list[dict[str, Any]]:
    selected = [check for check in checks if check.get("judgement") == judgement]
    return sorted(
        selected,
        key=lambda check: (SEVERITY_ORDER.get(check.get("severity") or "", 4), checks.index(check)),
    )


def render_findings(run: dict[str, Any], findings: dict[str, Any], snapshot_markdown: str) -> str:
    target = findings["target"]
    summary = findings["summary"]
    applicable = len(run["checks"]) - summary["notApplicable"]
    evaluated = applicable - summary["notEvaluated"]
    lines = [
        f"# {run['skillName']} findings",
        "",
        f"- Repository: `{target['repository']}` at `{target['gitCommit'][:12]}` "
        f"({'clean' if target['sourceWorkingTreeClean'] else 'dirty'} working tree)",
        f"- Run: `{findings['runIdentifier']}`, {findings['runStartedAt']} to {findings['runFinishedAt']}",
        f"- Catalog: version {findings['checkCatalogVersion']}, {len(run['checks'])} checks",
        f"- Coverage: {evaluated} of {applicable} applicable checks evaluated; {summary['notApplicable']} not applicable",
    ]
    execution = findings["execution"]
    if execution["filterArguments"] or execution["enrichmentArguments"] or execution["thresholdOverrides"]:
        details = execution["filterArguments"] + execution["enrichmentArguments"] + [
            f"{key}={value}" for key, value in execution["thresholdOverrides"].items()
        ]
        lines.append(f"- Execution options: {', '.join(details)}")
    if findings.get("auditApplicability"):
        lines += ["", f"**Not applicable to this repository:** {findings['auditApplicability']['reason']}"]
    titles = {check["checkId"]: check for check in run["checks"]}
    for judgement, heading in (
        ("act-on", "Act on"),
        ("consider", "Consider"),
        ("noted", "Noted"),
        ("dismissed", "Dismissed"),
    ):
        selected = ordered(run["checks"], judgement)
        if not selected:
            continue
        lines += ["", f"## {heading}", ""]
        for number, check in enumerate(selected, start=1):
            lines.append(
                f"{number}. **{check['title']}** — {check['status']}, {check.get('severity') or 'unrated'} severity (`{check['checkId']}`)"
            )
            lines.append(f"   - Gap: {check['gap']}")
            if check.get("rationale"):
                lines.append(f"   - Why it matters: {check['rationale']}")
            lines.append(f"   - Smallest fix: {check['remediation']}")
            if check.get("judgementReason"):
                lines.append(f"   - Judgement: {check['judgementReason']}")
            for entry in check["evidence"][:5]:
                lines.append(f"   - Evidence: {entry}")
            if len(check["evidence"]) > 5:
                lines.append(f"   - Evidence: {len(check['evidence']) - 5} more entries in findings.json")
    lines += ["", "## All checks", "", "| Layer | Check | Severity | Status | Judgement |", "| --- | --- | --- | --- | --- |"]
    for check in run["checks"]:
        if check["applicability"] == "not-applicable":
            status = "not applicable"
        elif check["status"] is None:
            status = "not evaluated"
        else:
            status = check["status"]
        lines.append(
            f"| {check['layer']} | {check['title']} | {check.get('severity') or 'unrated'} | {status} | {check.get('judgement') or ''} |"
        )
    present = [check for check in run["checks"] if check["status"] == "present"]
    if present:
        lines += ["", "## Present", ""]
        for check in present:
            lines.append(f"- **{check['title']}**: {check['evidence'][0]}")
    not_evaluated = [check for check in run["checks"] if check["applicability"] == "applicable" and check["status"] is None]
    if not_evaluated:
        lines += ["", "## Not evaluated", ""]
        lines += [f"- **{check['title']}**: {check['evaluationReason']}" for check in not_evaluated]
    not_applicable = [check for check in run["checks"] if check["applicability"] == "not-applicable"]
    if not_applicable and not findings.get("auditApplicability"):
        lines += ["", "## Not applicable", ""]
        lines += [f"- **{check['title']}**: {check['applicabilityReason']}" for check in not_applicable]
    if run["hypotheses"]:
        lines += ["", "## Unverified hypotheses", "", "These were not verified and do not affect any status or score.", ""]
        lines += [f"- `{item['checkId']}` ({titles[item['checkId']]['title']}): {item['note']}" for item in run["hypotheses"]]
    lines += ["", snapshot_markdown.rstrip(), ""]
    return "\n".join(lines)


def render_value(value: Any, indent: int = 0) -> list[str]:
    prefix = "  " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{prefix}- **{key}**:")
                lines += render_value(item, indent + 1)
            else:
                lines.append(f"{prefix}- **{key}**: {format_scalar(item)}")
        return lines
    if isinstance(value, list):
        lines = []
        for item in value:
            if isinstance(item, (dict, list)) and item:
                lines.append(f"{prefix}-")
                lines += render_value(item, indent + 1)
            else:
                lines.append(f"{prefix}- {format_scalar(item)}")
        return lines
    return [f"{prefix}- {format_scalar(value)}"]


def format_scalar(value: Any) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (dict, list)):
        return "none"
    return str(value)


def render_snapshot(run: dict[str, Any]) -> str:
    snapshot = dict(run["snapshot"])
    narrative = snapshot.pop("narrative", None)
    lines = ["## Snapshot", "", f"Layer 0 diagnostic snapshot for `{run['skillName']}`. Informational only; never graded.", ""]
    if narrative:
        lines += [str(narrative), ""]
    if snapshot:
        lines += render_value(snapshot)
    else:
        lines.append("- No snapshot facts were recorded.")
    if run["scope"].get("since"):
        lines += ["", f"Diff scope: {len(run['scope']['changedFiles'])} files changed since `{run['scope']['since']}`."]
    return "\n".join(lines) + "\n"


def command_finish(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    audit = arguments.audit
    run = load_run(root, audit)
    errors: list[str] = []
    pending = [check["checkId"] for check in run["checks"] if check["evaluationReason"] == PENDING_REASON]
    if pending:
        errors.append(
            f"{len(pending)} checks have no recorded result: {', '.join(pending)}. Record each one as evaluated, "
            "not applicable, or not evaluated with a reason."
        )
    if run["target"]["gitCommit"] != CALCULATOR.current_commit(root) or run["treeFingerprint"] != tree_fingerprint(root):
        errors.append(
            "the repository changed during the run (commit or working tree); begin again with --restart so the "
            "evidence matches one repository state"
        )
    for check in run["checks"]:
        for entry in check.get("evidence", []):
            try:
                verify_evidence(root, entry)
            except ProtocolError as error:
                errors.append(f"{check['checkId']}: {error}")
    if errors:
        print("Run not published:")
        for error in errors:
            print(f"- {error}")
        return 1

    findings, metadata = build_documents(run)
    catalog = CALCULATOR.load_catalog(SKILLS_ROOT, audit, {})
    try:
        CALCULATOR.parse_canonical_candidate(findings, metadata, catalog, staging_path(root, audit), "staging")
    except CALCULATOR.ScoreInputError as error:
        print(f"Run not published: the findings contract rejected the run: {error}")
        return 1

    snapshot_markdown = render_snapshot(run)
    findings_markdown = render_findings(run, findings, snapshot_markdown)
    output = root / AUDITS_DIRECTORY / audit
    for name, content in (
        ("snapshot.md", f"# {audit} snapshot\n\n" + snapshot_markdown),
        ("findings.md", findings_markdown),
        ("metadata.json", json.dumps(metadata, indent=2, ensure_ascii=False) + "\n"),
        ("findings.json", json.dumps(findings, indent=2, ensure_ascii=False) + "\n"),
    ):
        write_text_atomic(output / name, content)
    shutil.rmtree(output / STAGING_DIRECTORY, ignore_errors=True)

    print_chat_summary(run, findings)
    due = [
        decision
        for decision in run.get("decisions", [])
        if isinstance(decision.get("reviewAfter"), str) and decision["reviewAfter"] <= date.today().isoformat()
    ]
    for decision in due:
        print(f"Decision {decision.get('id')} for {decision.get('checkId')} is due for review ({decision['reviewAfter']}).")
    return 0


def print_chat_summary(run: dict[str, Any], findings: dict[str, Any]) -> None:
    summary = findings["summary"]
    counts = summary["statusCounts"]
    print(
        f"Published {run['skillName']} for {findings['target']['repository']} at {findings['target']['gitCommit'][:12]}: "
        f"{counts['violation']} violation, {counts['missing']} missing, {counts['partial']} partial, "
        f"{counts['present']} present; {summary['notEvaluated']} not evaluated; {summary['notApplicable']} not applicable."
    )
    act_on = ordered(run["checks"], "act-on")
    if act_on:
        print("Act on, highest severity first:")
        for number, check in enumerate(act_on[:5], start=1):
            print(f"{number}. {check['title']} [{check.get('severity') or 'unrated'}, {check['status']}] — {check['gap']} Smallest fix: {check['remediation']}")
        if len(act_on) > 5:
            print(f"   ({len(act_on) - 5} more act-on items are in findings.md; consider whether they are all act-on.)")
    else:
        print("Nothing to act on.")
    dismissed = summary["judgementCounts"]["dismissed"]
    if dismissed:
        print(f"Dismissed with reasons: {dismissed} (see findings.md).")
    print(f"Full report: {AUDITS_DIRECTORY}/{run['skillName']}/findings.md")


def command_decide(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    audit = arguments.check.split(".", 1)[0]
    catalog = CALCULATOR.load_catalog(SKILLS_ROOT, audit_directory(audit).name, {})
    if arguments.check not in {check.check_id for check in catalog.checks}:
        raise ProtocolError(f"unknown checkId: {arguments.check}")
    if arguments.decision not in DECISION_KINDS:
        raise ProtocolError(f"decision must be one of {', '.join(DECISION_KINDS)}")
    reason = require_reason(arguments.reason)
    owner = require_reason(arguments.owner)
    if arguments.review_after:
        try:
            date.fromisoformat(arguments.review_after)
        except ValueError as error:
            raise ProtocolError("--review-after must be a date such as 2027-01-31") from error
    path = root / AUDITS_DIRECTORY / DECISIONS_FILE
    data = read_json(path, "decisions file") if path.is_file() else {"schemaVersion": "1.0.0", "decisions": []}
    decisions = data.setdefault("decisions", [])
    identifier = f"decision-{len(decisions) + 1:03d}"
    decisions.append(
        {
            "id": identifier,
            "checkId": arguments.check,
            "decision": arguments.decision,
            "reason": reason,
            "owner": owner,
            "scope": arguments.scope or ["**"],
            "recordedAt": utc_now(),
            "reviewAfter": arguments.review_after,
        }
    )
    write_json_atomic(path, data)
    print(f"Recorded {identifier}: {arguments.decision} for {arguments.check}. Audits will keep reporting the status honestly but will not ask you to act on it again.")
    return 0


def command_hotspots(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    log = git(root, "log", f"--since={arguments.months}.months", "--name-only", "--format=") or ""
    counts: dict[str, int] = {}
    for line in log.splitlines():
        path = line.strip()
        if not path or path.startswith(AUDITS_DIRECTORY) or path.endswith(("lock.json", ".lock", "lock.yaml", "lockb")):
            continue
        if not (root / path).is_file():
            continue
        counts[path] = counts.get(path, 0) + 1
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))[: arguments.top]
    if arguments.json:
        print(json.dumps([{"path": path, "commits": count} for path, count in ranked], indent=2))
    else:
        for path, count in ranked:
            print(f"{count:5d}  {path}")
    return 0


# --------------------------------------------------------------------------- command line


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    root.add_argument("--repository", help="target repository path (default: the current directory's repository)")
    commands = root.add_subparsers(dest="command", required=True)

    begin = commands.add_parser("begin", help="stage a new run with every catalog check pending")
    begin.add_argument("audit")
    begin.add_argument("--restart", action="store_true", help="discard an existing staged run")
    begin.add_argument("--since", help="limit the run to changes since this Git reference")
    begin.add_argument("--filter", action="append", help="record another filter argument")
    begin.add_argument("--enrichment", action="append", help="record an enrichment flag such as --with-run")
    begin.add_argument("--threshold", action="append", help="record a threshold override as key=value")
    begin.set_defaults(handler=command_begin)

    record = commands.add_parser("record", help="record one evaluated check")
    record.add_argument("audit")
    record.add_argument("check")
    record.add_argument("--status", required=True, choices=STATUSES)
    record.add_argument("--evidence", action="append", help="repeatable; see references/run-protocol.md for the forms")
    record.add_argument("--tier", default="direct", choices=TIERS)
    record.add_argument("--gap")
    record.add_argument("--remediation")
    record.add_argument("--judgement", choices=JUDGEMENTS)
    record.add_argument("--reason", help="why a finding is noted, dismissed, or overrides a decision")
    record.add_argument("--classification")
    record.add_argument("--degraded", help="reason the evidence is incomplete, such as an enrichment tool failing")
    record.set_defaults(handler=command_record)

    not_applicable = commands.add_parser("not-applicable", help="record a check that does not apply")
    not_applicable.add_argument("audit")
    not_applicable.add_argument("check")
    not_applicable.add_argument("--reason", required=True)
    not_applicable.set_defaults(handler=command_not_applicable)

    not_evaluated = commands.add_parser("not-evaluated", help="record an applicable check you could not evaluate")
    not_evaluated.add_argument("audit")
    not_evaluated.add_argument("check")
    not_evaluated.add_argument("--reason", required=True)
    not_evaluated.set_defaults(handler=command_not_evaluated)

    audit_not_applicable = commands.add_parser("audit-not-applicable", help="record that the whole audit does not apply")
    audit_not_applicable.add_argument("audit")
    audit_not_applicable.add_argument("--reason", required=True)
    audit_not_applicable.set_defaults(handler=command_audit_not_applicable)

    snapshot = commands.add_parser("snapshot", help="record Layer 0 snapshot facts")
    snapshot.add_argument("audit")
    snapshot.add_argument("--set", action="append", help="key=value; the value may be JSON")
    snapshot.add_argument("--json", help="merge a JSON object from a file")
    snapshot.add_argument("--narrative", help="a short prose summary of the repository's state")
    snapshot.set_defaults(handler=command_snapshot)

    hypothesis = commands.add_parser("hypothesis", help="record an unverified suspicion for the appendix")
    hypothesis.add_argument("audit")
    hypothesis.add_argument("--check", required=True)
    hypothesis.add_argument("--note", required=True)
    hypothesis.set_defaults(handler=command_hypothesis)

    status = commands.add_parser("status", help="list checks that still need a result")
    status.add_argument("audit")
    status.set_defaults(handler=command_status)

    finish = commands.add_parser("finish", help="verify, render, and publish the run")
    finish.add_argument("audit")
    finish.set_defaults(handler=command_finish)

    decide = commands.add_parser("decide", help="record a decision so audits stop asking you to act on a finding")
    decide.add_argument("check")
    decide.add_argument("--decision", required=True, choices=DECISION_KINDS)
    decide.add_argument("--reason", required=True)
    decide.add_argument("--owner", required=True)
    decide.add_argument("--scope", action="append", help="repository-relative glob; repeatable; default is the whole repository")
    decide.add_argument("--review-after", help="date after which audits flag the decision for review")
    decide.set_defaults(handler=command_decide)

    hotspots = commands.add_parser("hotspots", help="list the files that changed most often")
    hotspots.add_argument("--months", type=int, default=6)
    hotspots.add_argument("--top", type=int, default=25)
    hotspots.add_argument("--json", action="store_true")
    hotspots.set_defaults(handler=command_hotspots)
    return root


def main(argv: list[str] | None = None) -> int:
    arguments = parser().parse_args(argv)
    try:
        return int(arguments.handler(arguments))
    except ProtocolError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    except CALCULATOR.ScoreInputError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
