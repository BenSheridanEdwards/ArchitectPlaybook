#!/usr/bin/env python3
"""Stage, record, verify, and publish one Architect Playbook audit run.

Audits never write their findings files by hand. They begin a staged run, record
one decision for every catalog check, and finish the run. Finishing validates
the whole run with the Repository Quality Score calculator's own contract code,
re-verifies every cited file, line, quoted fragment, and search count, renders
the Markdown reports from the JSON, and publishes all four files together. A
run that fails validation publishes nothing.

Commands that change a run hold a lock on the audit's output directory, so
parallel commands for one audit wait for each other instead of losing updates.

Standard library only. Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import importlib.util
import json
import math
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

try:
    import fcntl
except ImportError:  # Windows: exclusive_lock falls back to msvcrt.
    fcntl = None  # type: ignore[assignment]

PROTOCOL_VERSION = "1.0.0"
SKILLS_ROOT = Path(__file__).resolve().parents[2]
CALCULATOR_PATH = (
    SKILLS_ROOT / "repository-quality-score" / "scripts" / "calculate_repository_quality_score.py"
)
AUDITS_DIRECTORY = ".architect-audits"
STAGING_DIRECTORY = ".staging"
DECISIONS_FILE = "decisions.json"
PENDING_REASON = "pending"
REDACTED = "<REDACTED>"
STATUSES = ("present", "partial", "missing", "violation")
GRADED_STATUSES = ("partial", "missing", "violation")
TIERS = ("direct", "supported", "inferred")
JUDGEMENTS = ("act-on", "consider", "noted", "dismissed")
DECISION_KINDS = ("accepted-risk", "false-positive", "out-of-scope")
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
MAX_SEARCH_FILES = 50_000
MAX_SEARCH_FILE_BYTES = 8 * 1024 * 1024
BINARY_SNIFF_BYTES = 8000
LINE_CACHE_BUDGET_BYTES = 64 * 1024 * 1024
LOCK_TIMEOUT_SECONDS = 15 * 60
UNSEARCHED_PREFIXES = (f"{AUDITS_DIRECTORY}/", ".worktrees/", "graphify-out/")
UNSEARCHED_DIRECTORY_NAMES = {"node_modules", ".git"}

EVIDENCE_PREFIXES = ("note:", "command:", "search:", "files:")
VERIFIED_KINDS = {"citation", "file", "search", "files"}
REFERENCE_PATTERN = re.compile(
    r"^(?:`(?P<quoted>[^`]+)`|(?P<bare>[^\s:`]+))"
    r"(?::(?P<start>[0-9]+)(?:-(?P<end>[0-9]+))?(?::[0-9]+)?)?"
    r"(?:(?:\s+(?:—|–|--|-)\s+|:\s+|\s+)(?P<note>\S.*))?$"
)
COMMAND_PATTERN = re.compile(r"^command:\s+`(?P<command>[^`]+)`\s+(?:→|->)\s+(?P<result>\S.*)$")
SEARCH_PATTERN = re.compile(
    r"^search:\s+`(?P<pattern>[^`]+)`\s+in\s+(?:`(?P<quoted>[^`]+)`|(?P<scope>\S+))\s+(?:→|->)\s+"
    r"(?P<count>[0-9]+)\s+(?:match(?:es)?|matching\s+lines?)\b.*$"
)
FILES_PATTERN = re.compile(r"^files:\s+`(?P<scope>[^`]+)`\s+(?:→|->)\s+(?P<count>[0-9]+)\s+files?\b.*$")
QUOTE_PATTERN = re.compile(r"`([^`]+)`")
SECRET_PATTERNS = (
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{30,}"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bnpm_[A-Za-z0-9]{36}\b"),
    re.compile(r"\b[rs]k_(?:live|test)_[A-Za-z0-9]{16,}"),
    re.compile(r"\bwhsec_[A-Za-z0-9]{24,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    re.compile(r"\bsk-(?:live|proj|ant)?[A-Za-z0-9_-]{20,}"),
    re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(
        r"(?i)(?:secret|token|passw(?:or)?d|api_?key|private_?key|access_?key)[A-Za-z0-9_]*[\"']?\s*[:=]\s*"
        r"[\"'](?=[^\"'\s]*[0-9])(?=[^\"'\s]*[A-Za-z])[A-Za-z0-9+/_.=-]{20,}[\"']"
    ),
)
HOSTED_REMOTE_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
SCP_REMOTE_PATTERN = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\\\s]{2,}):(?P<path>[^\\\s]+)$")
NAME_PART_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class ProtocolError(Exception):
    """A user-facing error that explains exactly what to fix."""


def load_calculator() -> Any:
    if not CALCULATOR_PATH.is_file():
        raise ProtocolError(
            "the Repository Quality Score calculator is missing; reinstall the audit with the playbook "
            f"installer, which copies {CALCULATOR_PATH.relative_to(SKILLS_ROOT).as_posix()}"
        )
    spec = importlib.util.spec_from_file_location("architect_playbook_calculator", CALCULATOR_PATH)
    if spec is None or spec.loader is None:
        raise ProtocolError("cannot load the Repository Quality Score calculator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# Loading the calculator must not write __pycache__ beside it: in a project that
# commits its playbook install, that would make the working tree dirty.
sys.dont_write_bytecode = True
CALCULATOR = load_calculator()


# --------------------------------------------------------------------------- helpers


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def current_umask() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return mask


FILE_MODE = 0o666 & ~current_umask()


def dump_json(value: Any) -> str:
    try:
        return json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    except ValueError as error:
        raise ProtocolError("a recorded value is not a finite number; JSON cannot represent NaN or Infinity") from error


def write_json_atomic(path: Path, value: Any) -> None:
    write_text_atomic(path, dump_json(value))


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.chmod(temporary, FILE_MODE)
        os.replace(temporary, path)
    except BaseException:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


class NonFiniteNumber(ValueError):
    pass


def reject_constant(value: str) -> None:
    raise NonFiniteNumber(value)


def ensure_finite(value: Any, label: str) -> Any:
    if isinstance(value, float) and not math.isfinite(value):
        raise ProtocolError(f"{label} is not a finite number; JSON cannot represent NaN or Infinity")
    if isinstance(value, dict):
        for key, item in value.items():
            ensure_finite(item, f"{label}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            ensure_finite(item, f"{label}[{index}]")
    return value


def parse_json_text(text: str, label: str) -> Any:
    try:
        value = json.loads(text, parse_constant=reject_constant)
    except NonFiniteNumber as error:
        raise ProtocolError(f"{label} contains {error}, which is not valid JSON; use a finite number") from error
    return ensure_finite(value, label)


def read_json(path: Path, label: str) -> Any:
    try:
        return parse_json_text(path.read_text(encoding="utf-8"), label)
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
    """Name the target `owner/name` from a hosted remote, or by its folder name.

    Local remotes are ignored, so no local path or credential reaches the
    findings.
    """
    remote = (git(root, "remote", "get-url", "origin") or "").strip()
    path: str | None = None
    if HOSTED_REMOTE_PATTERN.match(remote):
        parsed = urllib.parse.urlsplit(remote)
        if parsed.scheme.lower() != "file" and parsed.hostname:
            path = parsed.path
    else:
        scp = SCP_REMOTE_PATTERN.match(remote)
        if scp:
            path = scp.group("path")
    if path is not None:
        parts = [part for part in path.split("/") if part and part != "_git"]
        if parts and parts[-1].endswith(".git"):
            parts[-1] = parts[-1][: -len(".git")]
        parts = [part for part in parts if part]
        tail = parts[-2:]
        if tail and all(NAME_PART_PATTERN.match(part) and part not in {".", ".."} for part in tail):
            return "/".join(tail)
    return re.sub(r"[^A-Za-z0-9_.-]", "-", root.name).strip(".-") or "repository"


def tree_fingerprint(root: Path) -> str:
    exclude = f":(exclude){AUDITS_DIRECTORY}/**"
    status = git(root, "status", "--porcelain", "--untracked-files=all", "--", ".", exclude)
    head = git(root, "rev-parse", "HEAD") or "no-head"
    digest = hashlib.sha256(f"{head}\n{status or ''}".encode("utf-8"))
    if status:
        # A dirty tree can change without changing its status lines, so hash the content too.
        diff = git(root, "diff", "HEAD", "--no-ext-diff", "--no-color", "--binary", "--", ".", exclude)
        digest.update((diff or "").encode("utf-8"))
        for line in status.splitlines():
            if line.startswith("?? "):
                digest.update(f"\n{line[3:]}:{content_digest(root / line[3:])}".encode("utf-8"))
    return digest.hexdigest()


def content_digest(path: Path) -> str:
    """Hash an untracked file's content, so an edit that keeps its size and time is still seen."""
    try:
        if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
            details = path.stat()
            return f"large:{details.st_size}:{details.st_mtime_ns}"
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return "unreadable"


def require_audit_name(audit: str) -> str:
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", audit):
        raise ProtocolError(f"invalid audit name: {audit!r}")
    return audit


def audit_directory(audit: str) -> Path:
    directory = SKILLS_ROOT / require_audit_name(audit)
    if not (directory / "checks.json").is_file():
        raise ProtocolError(
            f"no catalog for {audit}: expected {audit}/checks.json beside this protocol skill"
        )
    return directory


def output_directory(root: Path, audit: str) -> Path:
    return root / AUDITS_DIRECTORY / require_audit_name(audit)


def staging_path(root: Path, audit: str) -> Path:
    return output_directory(root, audit) / STAGING_DIRECTORY / "run.json"


@contextlib.contextmanager
def exclusive_lock(directory: Path) -> Iterator[None]:
    """Hold an exclusive lock on a directory until the block ends.

    The operating system releases the lock if the process dies, so a killed
    command never leaves a stale lock behind.
    """
    directory.mkdir(parents=True, exist_ok=True)
    if fcntl is None:
        with windows_lock(directory):
            yield
        return
    descriptor = os.open(str(directory), os.O_RDONLY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError:
        # Some network filesystems do not support flock. A lock file still
        # serialises writers there.
        os.close(descriptor)
        with lock_file(directory):
            yield
        return
    try:
        yield
    finally:
        os.close(descriptor)


@contextlib.contextmanager
def lock_file(directory: Path) -> Iterator[None]:
    """An exclusive lock file holding its owner's process ID, for filesystems without flock."""
    path = directory / ".lock"
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            descriptor = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            break
        except FileExistsError:
            if lock_owner_has_exited(path):
                with contextlib.suppress(FileNotFoundError):
                    path.unlink()
                continue
            if time.monotonic() > deadline:
                raise ProtocolError(
                    f"another protocol command still holds {directory.name}/.lock; if none is running, delete that file"
                )
            time.sleep(0.2)
    try:
        os.write(descriptor, str(os.getpid()).encode("ascii"))
        os.close(descriptor)
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            path.unlink()


def lock_owner_has_exited(path: Path) -> bool:
    try:
        owner = int(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False  # The owner may still be writing its process ID.
    try:
        os.kill(owner, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    return False


@contextlib.contextmanager
def windows_lock(directory: Path) -> Iterator[None]:
    import msvcrt

    with open(directory / ".lock", "a+b") as handle:
        handle.seek(0)
        while True:
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                break
            except OSError:
                continue  # LK_LOCK gives up after about ten seconds; keep waiting.
        try:
            yield
        finally:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)


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


@contextlib.contextmanager
def staged_run(root: Path, audit: str) -> Iterator[dict[str, Any]]:
    """Load the staged run under the audit's lock, and save it if the block succeeds."""
    with exclusive_lock(output_directory(root, audit)):
        run = load_run(root, audit)
        yield run
        save_run(root, audit, run)


def is_pending(check: dict[str, Any]) -> bool:
    return check.get("recordedBy") is None


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


def split_lines(text: str) -> list[str]:
    """Split on newlines only, as grep and ripgrep do, ignoring a trailing carriage return."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def read_lines(path: Path) -> list[str]:
    return split_lines(path.read_bytes().decode("utf-8", errors="replace"))


def quote_appears(quote: str, haystack: str) -> bool:
    if REDACTED not in quote:
        return quote in haystack
    parts = quote.split(REDACTED)
    if not "".join(parts).strip():
        raise ProtocolError(f"a quote needs some text around {REDACTED} so it can be verified")
    return re.search(".+?".join(re.escape(part) for part in parts), haystack) is not None


def verify_quotes(note: str | None, text: str, where: str) -> None:
    if not note:
        return
    haystack = normalize(text)
    for quote in QUOTE_PATTERN.findall(note):
        if not quote_appears(normalize(quote), haystack):
            raise ProtocolError(f"quoted text `{quote}` does not appear in {where}")


# --------------------------------------------------------------------------- scopes and searches

_FILE_LISTS: dict[Path, list[str]] = {}
_LINE_CACHE: dict[Path, list[str] | None] = {}
_line_cache_bytes = 0


def clear_caches() -> None:
    global _line_cache_bytes
    _FILE_LISTS.clear()
    _LINE_CACHE.clear()
    _line_cache_bytes = 0


def repository_files(root: Path) -> list[str]:
    """Tracked and untracked files that Git does not ignore, as repository-relative paths.

    Generated directories (`.architect-audits/`, `.worktrees/`, `graphify-out/`,
    and any `node_modules/`) and symbolic links are left out, so searches see
    what ripgrep and `git grep` see.
    """
    cached = _FILE_LISTS.get(root)
    if cached is not None:
        return cached
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            check=False,
            capture_output=True,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ProtocolError(f"cannot list repository files with Git ({type(error).__name__})") from error
    if completed.returncode != 0:
        raise ProtocolError("cannot list repository files with Git")
    files: list[str] = []
    seen: set[str] = set()
    for raw in completed.stdout.split(b"\0"):
        if not raw:
            continue
        relative = os.fsdecode(raw)
        if relative in seen:
            continue
        seen.add(relative)
        if relative.startswith(UNSEARCHED_PREFIXES) or UNSEARCHED_DIRECTORY_NAMES.intersection(relative.split("/")):
            continue
        path = root / relative
        if path.is_symlink() or not path.is_file():
            continue
        files.append(relative)
    _FILE_LISTS[root] = files
    return files


def glob_expression(pattern: str) -> re.Pattern[str]:
    """Translate a glob with Git `:(glob)` semantics into a regular expression."""
    parts: list[str] = []
    index = 0
    length = len(pattern)
    while index < length:
        if pattern.startswith("**", index):
            at_boundary = index == 0 or pattern[index - 1] == "/"
            end = index + 2
            if at_boundary and end == length:
                parts.append(".*")
                index = end
                continue
            if at_boundary and pattern.startswith("/", end):
                parts.append("(?:.*/)?")
                index = end + 1
                continue
            while index < length and pattern[index] == "*":
                index += 1
            parts.append("[^/]*")
            continue
        character = pattern[index]
        if character == "*":
            parts.append("[^/]*")
        elif character == "?":
            parts.append("[^/]")
        elif character == "[":
            close = character_class_end(pattern, index)
            if close is None:
                parts.append(re.escape(character))
            else:
                body = pattern[index + 1 : close]
                negate = body[:1] in {"!", "^"}
                if negate:
                    body = body[1:]
                body = body.replace("\\", "\\\\").replace("[", "\\[")
                if body.startswith("]"):
                    body = "\\" + body
                parts.append(f"(?!/)[{'^' if negate else ''}{body}]")
                index = close + 1
                continue
        else:
            parts.append(re.escape(character))
        index += 1
    return re.compile("".join(parts), re.DOTALL)


def character_class_end(pattern: str, start: int) -> int | None:
    index = start + 1
    if index < len(pattern) and pattern[index] in "!^":
        index += 1
    if index < len(pattern) and pattern[index] == "]":
        index += 1
    while index < len(pattern) and pattern[index] != "]":
        index += 1
    return index if index < len(pattern) else None


class Scope:
    """A repository-relative search or decision scope.

    `.` matches everything. A scope that names an existing path, or has no
    wildcard, matches that file or everything under that folder. Otherwise it
    is a glob: `*` and `?` stay inside one folder, `**/` spans folders, a
    trailing `/**` matches everything inside, and `[...]` is a character class.
    """

    def __init__(self, root: Path, raw: str) -> None:
        cleaned = raw.strip()
        while cleaned.startswith("./"):
            cleaned = cleaned[2:]
        cleaned = cleaned.rstrip("/")
        self.raw = raw
        self.everything = cleaned in {"", "."}
        self.literal: str | None = None
        self.expression: re.Pattern[str] | None = None
        if self.everything:
            return
        safe_relative_path(root, cleaned)
        if (root / cleaned).exists() or not any(character in cleaned for character in "*?["):
            self.literal = posixpath.normpath(cleaned)
        else:
            self.expression = glob_expression(cleaned)

    def matches(self, path: str) -> bool:
        if self.everything:
            return True
        if self.literal is not None:
            return path == self.literal or path.startswith(self.literal + "/")
        assert self.expression is not None
        return self.expression.fullmatch(path) is not None

    def files(self, root: Path) -> list[str]:
        files = [path for path in repository_files(root) if self.matches(path)]
        if len(files) > MAX_SEARCH_FILES:
            raise ProtocolError(
                f"scope {self.raw!r} has more than {MAX_SEARCH_FILES} files to verify; narrow the scope"
            )
        return files


def search_files(root: Path, raw_scope: str) -> list[str]:
    scope = Scope(root, raw_scope)
    files = scope.files(root)
    if not files and scope.literal is not None:
        if not (root / scope.literal).exists():
            raise ProtocolError(f"search scope does not exist: {raw_scope!r}")
        raise ProtocolError(
            f"search scope {raw_scope!r} has no searchable files; searches skip files Git ignores, "
            "symbolic links, and generated folders"
        )
    return files


def searchable_lines(path: Path) -> list[str] | None:
    """The file's lines, or None for binary and oversized files, which searches skip."""
    global _line_cache_bytes
    if path in _LINE_CACHE:
        return _LINE_CACHE[path]
    try:
        if path.stat().st_size > MAX_SEARCH_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    lines = None if b"\0" in data[:BINARY_SNIFF_BYTES] else split_lines(data.decode("utf-8", errors="replace"))
    cost = len(data) if lines is not None else 0
    if _line_cache_bytes + cost <= LINE_CACHE_BUDGET_BYTES:
        _LINE_CACHE[path] = lines
        _line_cache_bytes += cost
    return lines


def line_matcher(pattern: str) -> Callable[[str], Any]:
    if not pattern.startswith("re:"):
        return lambda line: pattern in line
    source = pattern[3:]
    if "[:" in source and ":]" in source:
        raise ProtocolError(
            "search regular expressions use Python syntax; replace POSIX classes such as [[:space:]] with \\s"
        )
    try:
        return re.compile(source).search
    except re.error as error:
        raise ProtocolError(f"invalid search regular expression {source!r}: {error}") from error


def count_matching_lines(root: Path, files: list[str], pattern: str) -> int:
    matcher = line_matcher(pattern)
    total = 0
    for relative in files:
        lines = searchable_lines(root / relative)
        if lines is not None:
            total += sum(1 for line in lines if matcher(line))
    return total


# --------------------------------------------------------------------------- evidence


EVIDENCE_FORMS = (
    "`path:line — note` (a citation), `path — note` (a file), search: `<pattern>` in <scope> → <count> matches, "
    "files: `<glob>` → <count> files, command: `<command>` → <result>, or note: <text>"
)


def verify_evidence(root: Path, entry: str) -> str:
    """Verify one evidence entry and return its kind.

    Kinds: citation, file, search, files, command, note. Citations, files,
    searches, and file counts are re-checked against the repository. Commands
    are recorded as reported tool output, and notes are free text.
    """
    if not isinstance(entry, str) or not entry.strip():
        raise ProtocolError("evidence entries must be non-empty strings")
    text = entry.strip()
    if "\n" in text or "\r" in text:
        raise ProtocolError("evidence entries are single lines; split them into several --evidence entries")
    if contains_secret(text):
        raise ProtocolError(f"evidence looks like it contains a secret; replace the secret with {REDACTED}")
    if text.startswith("note:"):
        if not text[len("note:") :].strip():
            raise ProtocolError("a note needs text after `note:`")
        return "note"
    if text.startswith("command:"):
        if not COMMAND_PATTERN.match(text):
            raise ProtocolError("malformed command evidence; use command: `<command>` → <result>")
        return "command"
    if text.startswith("search:"):
        search = SEARCH_PATTERN.match(text)
        if not search:
            raise ProtocolError("malformed search evidence; use search: `<pattern>` in <scope> → <count> matches")
        scope = search.group("quoted") or search.group("scope")
        actual = count_matching_lines(root, search_files(root, scope), search.group("pattern"))
        claimed = int(search.group("count"))
        if actual != claimed:
            raise ProtocolError(
                f"search evidence claims {claimed} matching lines for `{search.group('pattern')}` "
                f"in {scope}, but the repository has {actual}"
            )
        return "search"
    if text.startswith("files:"):
        listing = FILES_PATTERN.match(text)
        if not listing:
            raise ProtocolError("malformed files evidence; use files: `<glob>` → <count> files")
        actual = len(Scope(root, listing.group("scope")).files(root))
        claimed = int(listing.group("count"))
        if actual != claimed:
            raise ProtocolError(
                f"files evidence claims {claimed} files in {listing.group('scope')}, but the repository has {actual}"
            )
        return "files"
    reference = REFERENCE_PATTERN.match(text)
    if not reference:
        raise ProtocolError(f"unrecognised evidence {text[:80]!r}; use {EVIDENCE_FORMS}")
    raw_path = reference.group("quoted") or reference.group("bare")
    path = safe_relative_path(root, raw_path)
    if path.is_dir():
        raise ProtocolError(
            f"{raw_path!r} is a folder; cite a file, or make a claim about the folder with `search:` or `files:`"
        )
    if not path.is_file():
        raise ProtocolError(
            f"cited file does not exist: {raw_path}. If this entry is an observation rather than a file, "
            "start it with `note:`"
        )
    lines = read_lines(path)
    note = reference.group("note")
    if reference.group("start") is None:
        verify_quotes(note, "\n".join(lines), raw_path)
        return "file"
    start = int(reference.group("start"))
    end = int(reference.group("end") or start)
    if start < 1 or end < start or end > len(lines):
        raise ProtocolError(f"cited lines {start}-{end} are outside {raw_path} ({len(lines)} lines)")
    verify_quotes(note, "\n".join(lines[start - 1 : end]), f"{raw_path}:{start}-{end}")
    return "citation"


def render_evidence(entry: str) -> str:
    """Label an entry for findings.md, so readers can see what the script verified."""
    if entry.startswith("note:"):
        return f"Note: {entry[len('note:') :].strip()}"
    if entry.startswith("command:"):
        return f"Reported: {entry[len('command:') :].strip()}"
    return f"Evidence: {entry}"


def cited_paths(evidence: list[str]) -> list[str]:
    paths = []
    for entry in evidence:
        text = entry.strip()
        if text.startswith(EVIDENCE_PREFIXES):
            continue
        reference = REFERENCE_PATTERN.match(text)
        if reference:
            paths.append(posixpath.normpath(reference.group("quoted") or reference.group("bare")))
    return paths


# --------------------------------------------------------------------------- decisions


def validate_decision(root: Path, item: Any, label: str) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ProtocolError(f"{label} must be an object")
    for field in ("id", "checkId", "decision", "reason"):
        if not isinstance(item.get(field), str) or not item[field].strip():
            raise ProtocolError(f"{label} needs a non-empty {field}")
    if item["decision"] not in DECISION_KINDS:
        raise ProtocolError(f"{label}.decision must be one of {', '.join(DECISION_KINDS)}")
    scope = item.get("scope", ["**"])
    if not isinstance(scope, list) or not scope or any(not isinstance(pattern, str) for pattern in scope):
        raise ProtocolError(f"{label}.scope must be a non-empty list of repository-relative globs")
    for pattern in scope:
        Scope(root, pattern)
    return item


def load_decisions(root: Path) -> list[dict[str, Any]]:
    path = root / AUDITS_DIRECTORY / DECISIONS_FILE
    if not path.is_file():
        return []
    data = read_json(path, "decisions file")
    if not isinstance(data, dict) or not isinstance(data.get("decisions"), list):
        raise ProtocolError(f"{AUDITS_DIRECTORY}/{DECISIONS_FILE} must be an object with a decisions array")
    return [
        validate_decision(root, item, f"{AUDITS_DIRECTORY}/{DECISIONS_FILE} decisions[{index}]")
        for index, item in enumerate(data["decisions"])
    ]


def decision_applies(root: Path, decision: dict[str, Any], check_id: str, paths: list[str]) -> bool:
    if decision.get("checkId") != check_id:
        return False
    scopes = [Scope(root, pattern) for pattern in decision.get("scope") or ["**"]]
    if any(scope.everything or scope.raw.strip() == "**" for scope in scopes):
        return True
    if not paths:
        return False
    return all(any(scope.matches(path) for scope in scopes) for path in paths)


# --------------------------------------------------------------------------- commands


def command_begin(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    audit = arguments.audit
    directory = audit_directory(audit)
    with exclusive_lock(output_directory(root, audit)):
        return begin_run(root, audit, directory, arguments)


def begin_run(root: Path, audit: str, directory: Path, arguments: argparse.Namespace) -> int:
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
    enrichment = [flag if flag.startswith("-") else f"--{flag}" for flag in arguments.enrichment or []]
    filters = list(arguments.filter or [])
    changed_files: list[str] = []
    if arguments.since:
        if not git(root, "rev-parse", "--verify", f"{arguments.since}^{{commit}}"):
            raise ProtocolError(f"--since reference does not resolve to a commit: {arguments.since}")
        diff = git(root, "diff", "--name-only", f"{arguments.since}...HEAD")
        if diff is None:
            raise ProtocolError(
                f"cannot list the files changed since {arguments.since}: Git found no merge base with HEAD. "
                "In a shallow clone, fetch more history first; otherwise pass a reference that shares history with HEAD."
            )
        changed_files = [line for line in diff.splitlines() if line.strip()]
        filters.append(f"--since={arguments.since}")

    catalog_checks = {check["checkId"]: check for check in catalog_data.get("checks", [])}
    decisions = [decision for decision in load_decisions(root) if decision["checkId"].startswith(f"{audit}.")]
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
            "enrichmentArguments": enrichment,
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
        collected = apply_collector(root, run, collector, enrichment, thresholds)
        save_run(root, audit, run)

    pending = [check["checkId"] for check in run["checks"] if is_pending(check)]
    print(f"Began {audit} run {run['runIdentifier']} at {commit[:12]} ({'clean' if clean else 'dirty'} working tree).")
    if not clean:
        print("Note: the working tree has uncommitted changes, so this run cannot feed an official score.")
    if arguments.since:
        if changed_files:
            print(f"Diff scope: {len(changed_files)} files changed since {arguments.since}.")
        else:
            print(
                f"No files changed since {arguments.since}, so every check is outside the diff scope. Record them with "
                f'`not-evaluated {audit} --remaining --reason "outside the --since scope"`.'
            )
    if decisions:
        print(f"Recorded decisions that may apply: {', '.join(item['id'] for item in decisions)}")
    if collector.is_file():
        print(f"Collector resolved {collected} checks.")
    print(f"Pending checks ({len(pending)}): {', '.join(pending)}")
    return 0


def apply_collector(
    root: Path,
    run: dict[str, Any],
    collector: Path,
    enrichment: list[str],
    thresholds: dict[str, str],
) -> int:
    command = [sys.executable, "-B", str(collector), "--repository", str(root)]
    command += [f"--enrichment={flag}" for flag in enrichment]
    command += [f"--threshold={key}={value}" for key, value in thresholds.items()]
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
        output = parse_json_text(completed.stdout, "collector output")
    except (json.JSONDecodeError, ProtocolError) as error:
        print(f"Warning: collector output was not valid JSON ({error}); its checks stay pending.")
        return 0
    if not isinstance(output, dict):
        print("Warning: collector output was not a JSON object; its checks stay pending.")
        return 0
    resolved = 0
    checks = output.get("checks")
    for check_id, result in (checks.items() if isinstance(checks, dict) else []):
        try:
            check = find_check(run, check_id)
            if not isinstance(result, dict):
                raise ProtocolError("a collector result must be an object")
            apply_result(root, run, check, result, recorded_by="collector")
            resolved += 1
        except ProtocolError as error:
            print(f"Warning: collector result for {check_id} rejected: {error}")
    snapshot = output.get("snapshot")
    if isinstance(snapshot, dict):
        if contains_secret(json.dumps(snapshot)):
            print("Warning: collector snapshot looks like it contains a secret; it was not recorded.")
        else:
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
        tier=result.get("evidenceTier") or "direct",
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


def clear_audit_applicability(run: dict[str, Any]) -> None:
    """A per-check record replaces an earlier whole-audit not-applicable decision."""
    if run.get("auditApplicability"):
        run["auditApplicability"] = None
        print("Note: cleared the earlier audit-not-applicable record; every other check keeps its recorded result.")


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
    # The collector is deterministic code that read the repository itself, so its
    # reported results stand alone. A model's result must rest on something the
    # script can check.
    if recorded_by != "collector" and not VERIFIED_KINDS.intersection(kinds):
        raise ProtocolError(
            "evidence needs at least one entry the script can verify against the repository: a citation "
            "`path:line — note`, a file `path — note`, search: ..., or files: .... Commands and notes support a "
            "result but never stand alone."
        )
    if tier is None:
        if status != "present":
            raise ProtocolError(f"a {status} result needs --tier: direct, supported, or inferred")
        tier = "direct"
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
    degraded_reason = None if degraded is None else require_reason(degraded)

    decision_id = None
    if status in GRADED_STATUSES:
        paths = cited_paths(evidence)
        applicable_decisions = [
            decision
            for decision in run.get("decisions", [])
            if decision_applies(root, decision, check["checkId"], paths)
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

    for value in (gap, remediation, judgement_reason, degraded_reason):
        if isinstance(value, str) and contains_secret(value):
            raise ProtocolError(f"text looks like it contains a secret; replace the secret with {REDACTED}")
    check.update(
        {
            "applicability": "applicable",
            "applicabilityReason": None,
            "evaluationState": "evaluated",
            "evaluationReason": degraded_reason,
            "evidenceQuality": "degraded" if degraded_reason else "complete",
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
    with staged_run(root, arguments.audit) as run:
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
        clear_audit_applicability(run)
    print(f"Recorded {check['checkId']}: {check['status']} ({check['evidenceTier']} evidence, judgement {check['judgement'] or 'n/a'}).")
    return 0


def command_not_applicable(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    with staged_run(root, arguments.audit) as run:
        check = find_check(run, arguments.check)
        mark_not_applicable(check, require_reason(arguments.reason), "model")
        clear_audit_applicability(run)
    print(f"Recorded {check['checkId']} as not applicable.")
    return 0


def command_not_evaluated(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    reason = require_reason(arguments.reason)
    if arguments.remaining == bool(arguments.check):
        raise ProtocolError("name one check, or pass --remaining to record every still-pending check")
    with staged_run(root, arguments.audit) as run:
        if arguments.remaining:
            pending = [check for check in run["checks"] if is_pending(check)]
            for check in pending:
                mark_not_evaluated(check, reason, "model")
            message = f"Recorded {len(pending)} pending checks as not evaluated: {reason}"
        else:
            check = find_check(run, arguments.check)
            mark_not_evaluated(check, reason, "model")
            message = f"Recorded {check['checkId']} as not evaluated."
        clear_audit_applicability(run)
    print(message)
    return 0


def command_audit_not_applicable(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    reason = require_reason(arguments.reason)
    with staged_run(root, arguments.audit) as run:
        for check in run["checks"]:
            mark_not_applicable(check, reason, "model")
        run["auditApplicability"] = {"status": "not-applicable", "reason": reason}
    print(f"Recorded the whole {arguments.audit} as not applicable: {reason}")
    return 0


def command_snapshot(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    updates: dict[str, Any] = {}
    if arguments.json:
        value = read_json(Path(arguments.json), "snapshot file")
        if not isinstance(value, dict):
            raise ProtocolError("the snapshot file must contain a JSON object")
        updates.update(value)
    for item in arguments.set or []:
        key, separator, raw = item.partition("=")
        if not separator or not key.strip():
            raise ProtocolError(f"snapshot entries use key=value: {item!r}")
        try:
            updates[key.strip()] = parse_json_text(raw, f"snapshot value {key.strip()}")
        except json.JSONDecodeError:
            updates[key.strip()] = raw
    if arguments.narrative:
        updates["narrative"] = arguments.narrative
    if contains_secret(json.dumps(updates)):
        raise ProtocolError(f"the snapshot looks like it contains a secret; replace the secret with {REDACTED}")
    with staged_run(root, arguments.audit) as run:
        run["snapshot"].update(updates)
        count = len(run["snapshot"])
    print(f"Snapshot now has {count} entries.")
    return 0


def command_hypothesis(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    if contains_secret(arguments.note):
        raise ProtocolError(f"the note looks like it contains a secret; replace the secret with {REDACTED}")
    with staged_run(root, arguments.audit) as run:
        find_check(run, arguments.check)
        run["hypotheses"].append({"checkId": arguments.check, "note": arguments.note.strip()})
    print("Recorded an unverified hypothesis; it appears in the report appendix, never in the score.")
    return 0


def command_status(arguments: argparse.Namespace) -> int:
    root = repository_root(arguments.repository)
    run = load_run(root, arguments.audit)
    pending = [check for check in run["checks"] if is_pending(check)]
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
                lines.append(f"   - {render_evidence(entry)}")
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
            lines.append(f"- **{check['title']}**: {render_evidence(check['evidence'][0])}")
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
    with exclusive_lock(output_directory(root, audit)):
        return finish_run(root, audit)


def finish_run(root: Path, audit: str) -> int:
    run = load_run(root, audit)
    errors: list[str] = []
    pending = [check["checkId"] for check in run["checks"] if is_pending(check)]
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
    findings_text = dump_json(findings)
    metadata_text = dump_json(metadata)
    # Validate the exact bytes that will be published, read back the way the
    # score calculator reads them.
    staging = staging_path(root, audit).parent
    write_text_atomic(staging / "findings.json", findings_text)
    write_text_atomic(staging / "metadata.json", metadata_text)
    catalog = CALCULATOR.load_catalog(SKILLS_ROOT, audit, {})
    try:
        CALCULATOR.parse_canonical_candidate(
            CALCULATOR.strict_json_load(staging / "findings.json", {}),
            CALCULATOR.strict_json_load(staging / "metadata.json", {}),
            catalog,
            staging / "findings.json",
            "staging",
        )
    except CALCULATOR.ScoreInputError as error:
        print(f"Run not published: the findings contract rejected the run: {error}")
        return 1

    snapshot_markdown = render_snapshot(run)
    findings_markdown = render_findings(run, findings, snapshot_markdown)
    output = output_directory(root, audit)
    publish(
        output,
        (
            ("snapshot.md", f"# {audit} snapshot\n\nRun `{findings['runIdentifier']}`.\n\n" + snapshot_markdown),
            ("findings.md", findings_markdown),
            ("metadata.json", metadata_text),
            ("findings.json", findings_text),
        ),
    )
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


def publish(output: Path, files: tuple[tuple[str, str], ...]) -> None:
    """Replace the published files, writing findings.json last as the completion marker.

    Each file is replaced atomically. If any write fails, every file already
    replaced is restored to its previous content, so a failed publication
    leaves the previous run intact. If the process is killed part-way, the
    set can mix two runs; consumers detect that because findings.json and
    metadata.json must carry the same run identity, and findings.json is
    written last.
    """
    previous: dict[str, str | None] = {}
    for name, _ in files:
        path = output / name
        previous[name] = path.read_text(encoding="utf-8") if path.is_file() else None
    written: list[str] = []
    try:
        for name, content in files:
            write_text_atomic(output / name, content)
            written.append(name)
    except OSError as error:
        for name in reversed(written):
            old = previous[name]
            if old is None:
                (output / name).unlink(missing_ok=True)
            else:
                write_text_atomic(output / name, old)
        raise ProtocolError(f"publication failed and the previous run was restored: {error}") from error


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
    if contains_secret(reason) or contains_secret(owner):
        raise ProtocolError(f"text looks like it contains a secret; replace the secret with {REDACTED}")
    if arguments.review_after:
        try:
            date.fromisoformat(arguments.review_after)
        except ValueError as error:
            raise ProtocolError("--review-after must be a date such as 2027-01-31") from error
    scope = arguments.scope or ["**"]
    for pattern in scope:
        Scope(root, pattern)
    with exclusive_lock(root / AUDITS_DIRECTORY):
        path = root / AUDITS_DIRECTORY / DECISIONS_FILE
        data: dict[str, Any] = {"schemaVersion": "1.0.0", "decisions": []}
        if path.is_file():
            load_decisions(root)
            data = read_json(path, "decisions file")
        used = set()
        for item in data["decisions"]:
            match = re.fullmatch(r"decision-([0-9]+)", str(item.get("id", "")))
            if match:
                used.add(int(match.group(1)))
        identifier = f"decision-{max(used, default=0) + 1:03d}"
        data["decisions"].append(
            {
                "id": identifier,
                "checkId": arguments.check,
                "decision": arguments.decision,
                "reason": reason,
                "owner": owner,
                "scope": scope,
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
    begin.add_argument("--filter", action="append", help="record another filter argument; use --filter=<value>")
    begin.add_argument(
        "--enrichment",
        action="append",
        help="record an enrichment flag such as with-run (or --enrichment=--with-run)",
    )
    begin.add_argument("--threshold", action="append", help="record a threshold override as key=value")
    begin.set_defaults(handler=command_begin)

    record = commands.add_parser("record", help="record one evaluated check")
    record.add_argument("audit")
    record.add_argument("check")
    record.add_argument("--status", required=True, choices=STATUSES)
    record.add_argument("--evidence", action="append", help="repeatable; see references/run-protocol.md for the forms")
    record.add_argument(
        "--tier",
        choices=TIERS,
        help="how strongly the evidence shows the status; required for partial, missing, and violation",
    )
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
    not_evaluated.add_argument("check", nargs="?")
    not_evaluated.add_argument(
        "--remaining",
        action="store_true",
        help="record every still-pending check, for example the checks outside a --since scope",
    )
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
    decide.add_argument("--scope", action="append", help="repository-relative path or glob; repeatable; default is the whole repository")
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
    clear_caches()
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
