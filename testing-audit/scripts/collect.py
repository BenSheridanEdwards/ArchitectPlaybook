#!/usr/bin/env python3
"""Deterministic, read-only collector for /testing-audit.

Reads a JavaScript or TypeScript repository's test files, package manifests,
runner and ESLint configuration, continuous-integration workflows, and Git
history, then reports:

- the tool checks: a test runner that runs, focused and skipped tests,
  coverage thresholds, the Testing Library and jest-dom lint rules, and, with
  --with-mutation, a Stryker mutation score on the five riskiest files;
- applicability for the checks that need a user interface, Testing Library,
  jest-dom, a database client, or any test at all;
- Layer 0 facts: runners and test kinds, mocks, flake signals, query usage,
  a risk ranking from Git churn and import fan-in, and a mechanical pre-filter
  that flags tests whose shape suggests they cannot fail.

ESLint configuration is read as text. Nothing is installed or evaluated, and
node_modules is read only to find an installed Stryker. Comments, strings, and
regular expressions are masked before any code pattern is matched.

The output follows the audit protocol's collector contract: a JSON object with
`checks` (results keyed by checkId) and `snapshot` (Layer 0 facts). The script
never writes to the repository; with --with-mutation, Stryker's configuration,
sandbox, and report go under `.architect-audits/testing-audit/mutation/`.
Standard library only; Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import shlex
import subprocess
import sys
import time
from bisect import bisect_left
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

AUDIT = "testing-audit"
SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
RESOLVE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
SKIPPED_DIRECTORIES = {
    ".git", "node_modules", "dist", "build", "out", "coverage", ".next", ".nuxt",
    ".turbo", ".cache", ".vercel", ".output", "storybook-static", ".architect-audits",
    "graphify-out", ".worktrees", ".claude", ".stryker-tmp", "playwright-report", "test-results",
}
MAX_FILES = 50_000
MAX_FILE_BYTES = 2_000_000
MAX_CANDIDATES = 100
MAX_ASSERTIONS_PER_TEST = 100
# The near-duplicate scan compares test bodies up to this many characters, with at most this many full
# comparisons per file and per run, so it stays well inside the protocol's collector time limit.
MAX_DUPLICATE_BODY = 2_000
MAX_DUPLICATE_COMPARISONS_PER_FILE = 300
MAX_DUPLICATE_COMPARISONS_PER_RUN = 3_000
MUTATION_TARGETS = 5
# The audit protocol stops a collector after 600 seconds (audit_run.py, apply_collector). Stryker gets
# what is left after the scan, less a margin for reading its report, and never more than the cap.
PROTOCOL_COLLECTOR_TIMEOUT_SECONDS = 600
MUTATION_MARGIN_SECONDS = 90
MUTATION_TIMEOUT_CAP_SECONDS = 420
MUTATION_MINIMUM_SECONDS = 60
MUTATION_DIRECTORY = Path(".architect-audits") / AUDIT / "mutation"


def check_id(name: str) -> str:
    return f"{AUDIT}.{name}"


RUNNER = check_id("tests-run-under-configured-runner")
FOCUSED = check_id("no-focused-or-skipped-tests")
COVERAGE = check_id("coverage-thresholds-configured")
FAILS = check_id("tests-fail-when-behaviour-breaks")
MOCKS = check_id("mocks-only-at-boundaries")
DETERMINISTIC = check_id("tests-are-deterministic")
TITLES = check_id("test-titles-state-behaviour")
MUTATION = check_id("mutations-in-riskiest-files-caught")
CRITICAL = check_id("critical-paths-covered-by-integration-tests")
DATABASE = check_id("data-access-tested-against-real-database")
TESTING_LIBRARY_LINT = check_id("testing-library-lint-rules-enforced")
JEST_DOM_LINT = check_id("jest-dom-lint-rules-enforced")
COMPONENTS = check_id("components-tested-as-users-perceive-them")
ALL_CHECKS = (
    RUNNER, FOCUSED, COVERAGE, FAILS, MOCKS, DETERMINISTIC, TITLES, MUTATION,
    CRITICAL, DATABASE, TESTING_LIBRARY_LINT, JEST_DOM_LINT, COMPONENTS,
)

UI_PACKAGES = (
    "react", "react-dom", "preact", "vue", "svelte", "solid-js", "@angular/core", "lit",
    "next", "nuxt", "@remix-run/react", "react-router", "astro", "@builder.io/qwik",
)
TESTING_LIBRARY_PACKAGES = (
    "@testing-library/react", "@testing-library/dom", "@testing-library/vue", "@testing-library/svelte",
    "@testing-library/angular", "@testing-library/preact", "@testing-library/user-event",
    "@testing-library/react-native", "@marko/testing-library", "@solidjs/testing-library",
)
DATABASE_PACKAGES = (
    "pg", "postgres", "mysql", "mysql2", "better-sqlite3", "sqlite3", "@prisma/client", "prisma",
    "drizzle-orm", "typeorm", "sequelize", "knex", "kysely", "mongodb", "mongoose", "@libsql/client",
    "@neondatabase/serverless", "@planetscale/database", "@vercel/postgres", "@supabase/supabase-js",
    "mssql", "oracledb", "@mikro-orm/core", "objection",
)
INTEGRATION_PACKAGES = (
    "testcontainers", "@testcontainers/postgresql", "@testcontainers/mysql", "@testcontainers/mongodb",
    "@testcontainers/redis", "pg-mem", "@electric-sql/pglite", "mongodb-memory-server", "supertest",
    "msw", "nock", "@mswjs/data",
)
TESTING_LIBRARY_PRESET_RULES = (
    "prefer-screen-queries", "render-result-naming-convention", "no-container", "no-node-access",
    "prefer-find-by", "prefer-presence-queries", "no-wait-for-multiple-assertions",
    "no-wait-for-side-effects", "no-unnecessary-act", "no-manual-cleanup",
)
TESTING_LIBRARY_OPT_IN_RULES = ("prefer-user-event", "prefer-explicit-assert")
TESTING_LIBRARY_RULES = TESTING_LIBRARY_PRESET_RULES + TESTING_LIBRARY_OPT_IN_RULES
# Rules each eslint-plugin-testing-library shared configuration enables, among those this audit requires.
TESTING_LIBRARY_PRESETS = {
    "react": set(TESTING_LIBRARY_PRESET_RULES),
    "vue": set(TESTING_LIBRARY_PRESET_RULES) - {"no-unnecessary-act"},
    "svelte": set(TESTING_LIBRARY_PRESET_RULES) - {"no-unnecessary-act"},
    "marko": set(TESTING_LIBRARY_PRESET_RULES) - {"no-manual-cleanup"},
    "angular": set(TESTING_LIBRARY_PRESET_RULES) - {"no-manual-cleanup", "no-unnecessary-act"},
    "dom": set(TESTING_LIBRARY_PRESET_RULES) - {
        "render-result-naming-convention", "no-container", "no-unnecessary-act", "no-manual-cleanup",
    },
}
JEST_DOM_RULES = (
    "prefer-checked", "prefer-empty", "prefer-enabled-disabled", "prefer-focus", "prefer-in-document",
    "prefer-required", "prefer-to-have-attribute", "prefer-to-have-class", "prefer-to-have-style",
    "prefer-to-have-text-content", "prefer-to-have-value",
)
ESLINT_CONFIG_NAMES = (
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts", "eslint.config.mts",
    "eslint.config.cts", ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yaml",
    ".eslintrc.yml",
)
CONTINUOUS_INTEGRATION_FILES = (
    ".gitlab-ci.yml", ".circleci/config.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml",
    ".buildkite/pipeline.yml", "Jenkinsfile",
)
STRYKER_CONFIG_NAMES = tuple(
    f"{prefix}.{suffix}"
    for prefix in ("stryker.config", "stryker.conf", ".stryker.config", ".stryker.conf")
    for suffix in ("mjs", "js", "cjs", "json")
)

TEST_NAME_PATTERN = re.compile(r"\.(?:test|spec|cy|e2e)\.[cm]?[jt]sx?$")
# Names like `parse_test.js`, `ab-test.ts`, or `test-helpers.ts` are tests only when the file imports a test API or
# declares tests at the top level, as Jest and Mocha globals allow.
LOOSE_TEST_NAME_PATTERN = re.compile(r"[_-]test\.[cm]?[jt]sx?$|(^|/)test-[^/]*\.[cm]?[jt]sx?$")
# A titled `describe(`, `it(`, `test(`, `suite(`, or `context(` call at the start of a line, in string-masked text.
TOP_LEVEL_TEST_DECLARATION = re.compile(r"""(?m)^(?:describe|it|test|suite|context)(?:\s*\.\s*\w+)*\s*\(\s*['"`]""")
# Test APIs a file can import; such a file is a test wherever it lives (node --test and bun test find them anywhere).
TEST_API_IMPORT = re.compile(r"""(?:from|require\s*\()\s*['"](?:node:test|bun:test|vitest|@jest/globals|uvu|tap|ava|@playwright/test)['"]""")
TOOLING_PATTERN = re.compile(
    r"(^|/)\.|(^|/)[^/]*\.config\.[cm]?[jt]s$|(^|/)[^/]*rc\.[cm]?[jt]s$"
    r"|(^|/)(?:gulpfile|gruntfile|Gruntfile|dangerfile|Jakefile)\.[cm]?[jt]s$"
)
# Tooling folders count only at the repository root or a workspace package's root, so `src/tools/` stays product code.
TOOLING_FOLDERS = ("scripts", "tools", "tooling")
TEST_DIRECTORY_PATTERN = re.compile(r"(^|/)(__tests__|tests?|e2e|cypress|playwright|integration)/")
STORY_PATTERN = re.compile(r"\.stories\.[cm]?[jt]sx?$")
NON_SOURCE_PATTERN = re.compile(
    r"(^|/)(__tests__|__mocks__|tests?|e2e|cypress|playwright|fixtures?|mocks?|\.storybook|stories)/"
    r"|\.(test|spec|cy|e2e|stories|d)\.[cm]?[jt]sx?$|(^|/)[^/]*\.config\.[cm]?[jt]s$|(^|/)[^/]*\.setup\.[cm]?[jt]sx?$"
)
TEST_SUPPORT_PATTERN = re.compile(r"(^|/)(__tests__|__mocks__|tests?|fixtures?|mocks?)/")
TEST_API_PATTERN = re.compile(r"(?<![\w$.])(?:describe|it|test)\s*\(")
IMPORT_PATTERN = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^[ \t]*import\s*)(['"])(?P<specifier>[^'"\n]+)\1""",
    re.M,
)
# Runners that execute unit or integration tests, so a coverage floor can apply to them.
UNIT_RUNNERS = frozenset({
    "vitest", "jest", "react-scripts", "node:test", "bun:test", "angular-cli", "karma", "jasmine",
    "mocha", "ava", "tap", "uvu",
})
RUNNER_COMMANDS = {
    "vitest": re.compile(r"(?<![\w-])vitest\b"),
    "jest": re.compile(r"(?<![\w-])jest\b(?![\w-])"),
    "react-scripts": re.compile(r"\breact-scripts\s+test\b"),
    "node:test": re.compile(r"\b(?:node|tsx)\b[^&|;\n]*\s--test\b"),
    "bun:test": re.compile(r"\bbun\s+test\b"),
    "angular-cli": re.compile(r"(?<![\w-])ng\s+test\b"),
    "karma": re.compile(r"(?<![\w-])karma\s+start\b"),
    "jasmine": re.compile(r"(?<![\w-])jasmine\b(?![\w-])"),
    "tap": re.compile(r"(?<![\w=/-])tap(?=\s|$)"),
    "uvu": re.compile(r"(?<![\w-])uvu\b"),
    "playwright": re.compile(r"\bplaywright\s+test\b"),
    "cypress": re.compile(r"\bcypress\s+run\b"),
    "storybook-test-runner": re.compile(r"\btest-storybook\b"),
    "mocha": re.compile(r"(?<![\w-])mocha\b"),
    "ava": re.compile(r"(?<![\w-])ava\b(?![\w-])"),
}
PLACEHOLDER_TEST_SCRIPT = re.compile(r"no test specified")
DELEGATING_TEST_COMMAND = re.compile(
    r"\b(?:turbo(?:\s+run)?|nx\s+(?:run-many|affected|run)|lerna\s+run|(?:pnpm|npm|yarn|bun)\s+(?:-r\s+|--recursive\s+|--workspaces\s+|workspaces\s+(?:foreach\s+)?)?(?:run\s+)?)[^&|;\n]*\btest\b"
)
CONTINUOUS_INTEGRATION_TEST_COMMAND = re.compile(
    r"\b(?:(?:npm|pnpm|yarn|bun)\s+(?:run\s+)?test[\w:-]*|npx\s+(?:vitest|jest|playwright\s+test|cypress\s+run)|vitest\s+run|jest\b|playwright\s+test|cypress\s+run|node\s+--test|turbo\s+(?:run\s+)?test|nx\s+(?:run-many|affected)[^\n]*\btest\b)"
)


# ---------------------------------------------------------------------------
# Reading files


def read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def strip_json_comments(text: str) -> str:
    """Remove // and /* */ comments and trailing commas outside strings (JSONC)."""
    output: list[str] = []
    index = 0
    in_string = False
    while index < len(text):
        character = text[index]
        if in_string:
            output.append(character)
            if character == "\\" and index + 1 < len(text):
                output.append(text[index + 1])
                index += 2
                continue
            if character == '"':
                in_string = False
            index += 1
            continue
        if character == '"':
            in_string = True
            output.append(character)
            index += 1
            continue
        if text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        output.append(character)
        index += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(output))


def load_jsonc(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(strip_json_comments(read_text(path)))
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def git(root: Path, *arguments: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return completed.stdout if completed.returncode == 0 else ""


def listed_files(root: Path) -> list[str]:
    """Files Git does not ignore, relative to the root, outside generated folders."""
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed:
        candidates = [entry for entry in listed.split("\0") if entry]
    else:
        candidates = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]
    files = []
    for entry in sorted(set(candidates)):
        parts = entry.split("/")
        if any(part in SKIPPED_DIRECTORIES for part in parts[:-1]):
            continue
        if (root / entry).is_file():
            files.append(entry)
    return files[:MAX_FILES]


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def location(path: str) -> str:
    """A path the audit protocol can parse; paths with spaces go in backticks."""
    return f"`{path}`" if any(character.isspace() for character in path) else path


def cite(path: str, line: int, fragment: str | None = None, note: str | None = None) -> str:
    """A citation the audit protocol verifies against the cited line."""
    text = f"{location(path)}:{line}"
    if fragment:
        text += f" — `{fragment}`"
        if note:
            text += f" {note}"
    elif note:
        text += f" — {note}"
    return text


def cite_file(path: str, fragment: str | None = None, note: str | None = None) -> str:
    text = location(path)
    if fragment:
        text += f" — `{fragment}`"
        if note:
            text += f" {note}"
    elif note:
        text += f" — {note}"
    return text


@lru_cache(maxsize=32)
def newline_offsets(text: str) -> list[int]:
    return [index for index, character in enumerate(text) if character == "\n"]


def line_of(text: str, index: int) -> int:
    return bisect_left(newline_offsets(text), index) + 1


def line_text(text: str, line: int) -> str:
    lines = text.split("\n")
    return lines[line - 1] if 0 < line <= len(lines) else ""


def fragment(text: str, limit: int = 60) -> str:
    """A quotable piece of one line: no backticks, trimmed."""
    return text.split("`", 1)[0].strip()[:limit].strip()


# ---------------------------------------------------------------------------
# Masking


REGEX_KEYWORDS = {"return", "typeof", "case", "do", "else", "in", "of", "new", "delete", "void", "throw", "await", "yield"}


def mask(text: str, strings: bool = True) -> str:
    """Blank out comments and, with `strings`, the contents of strings, templates, and regular expressions.

    Offsets, line numbers, and delimiters are unchanged, so code patterns can be
    matched without being fooled by text, and a match maps back to the original.
    The code inside a template's `${...}` stays readable.
    """
    output = list(text)
    length = len(text)

    def blank(start: int, end: int) -> None:
        for position in range(start, min(end, length)):
            if output[position] != "\n":
                output[position] = " "

    index = 0
    previous = ""  # last significant character of code
    previous_word = ""
    stack: list[int] = []  # brace depth inside each open `${`
    while index < length:
        character = text[index]
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = length if end == -1 else end
            blank(index, end)
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
            blank(index, end)
            index = end
            continue
        if character == "`" or (character == "}" and stack and stack[-1] == 0):
            if character == "}":
                stack.pop()
            index += 1
            start = index
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == "`":
                    break
                if text.startswith("${", index):
                    break
                index += 1
            if strings:
                blank(start, index)
            if index < length and text[index] == "`":
                index += 1
            elif index < length:
                stack.append(0)
                index += 2
            previous, previous_word = "`", ""
            continue
        if character in "'\"":
            end = index + 1
            while end < length and text[end] != character and text[end] != "\n":
                end += 2 if text[end] == "\\" else 1
            if strings:
                blank(index + 1, min(end, length))
            index = min(end, length - 1) + 1
            previous, previous_word = character, ""
            continue
        if character == "/" and (previous == "" or previous in "(,=:[!&|?{};+-*%<>~^" or previous_word in REGEX_KEYWORDS):
            end = index + 1
            in_class = False
            while end < length and text[end] != "\n":
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == "[":
                    in_class = True
                elif text[end] == "]":
                    in_class = False
                elif text[end] == "/" and not in_class:
                    break
                end += 1
            if end < length and text[end] == "/":
                if strings:
                    blank(index + 1, end)
                index = end + 1
                while index < length and text[index].isalpha():
                    index += 1
                previous, previous_word = "/", ""
                continue
        if character == "{" and stack:
            stack[-1] += 1
        elif character == "}" and stack:
            stack[-1] -= 1
        if character.isalnum() or character in "_$":
            end = index
            while end < length and (text[end].isalnum() or text[end] in "_$"):
                end += 1
            previous_word = text[index:end]
            previous = text[end - 1]
            index = end
            continue
        if not character.isspace():
            previous, previous_word = character, ""
        index += 1
    return "".join(output)


def matching(text: str, open_index: int) -> int:
    """The index of the bracket closing the one at `open_index`, in masked text, or -1."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    opener = text[open_index]
    closer = pairs[opener]
    depth = 0
    for index in range(open_index, len(text)):
        character = text[index]
        if character == opener:
            depth += 1
        elif character == closer:
            depth -= 1
            if depth == 0:
                return index
    return -1


def split_arguments(masked: str, start: int, end: int) -> list[tuple[int, int]]:
    """Spans of the top-level, comma-separated arguments between `start` and `end`."""
    spans = []
    depth = 0
    begin = start
    for index in range(start, end):
        character = masked[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            depth -= 1
        elif character == "," and depth == 0:
            spans.append((begin, index))
            begin = index + 1
    if masked[begin:end].strip():
        spans.append((begin, end))
    return spans


# ---------------------------------------------------------------------------
# Manifests and configuration


def workspaces(root: Path) -> list[Path]:
    package = load_jsonc(root / "package.json")
    patterns = package.get("workspaces")
    if isinstance(patterns, dict):
        patterns = patterns.get("packages")
    globs: list[str] = list(patterns) if isinstance(patterns, list) else []
    pnpm = root / "pnpm-workspace.yaml"
    if pnpm.is_file():
        globs += re.findall(r"^\s*-\s*['\"]?([^'\"\n]+)['\"]?\s*$", read_text(pnpm), flags=re.M)
    found: list[Path] = []
    resolved_root = root.resolve()
    for pattern in globs:
        pattern = pattern.strip() if isinstance(pattern, str) else ""
        # Only relative patterns inside the repository: no empty, negated, absolute, or parent patterns.
        if not pattern or pattern.startswith(("!", "/", "~")) or ".." in pattern.split("/") or re.match(r"^[A-Za-z]:", pattern):
            continue
        try:
            matches = sorted(root.glob(pattern.rstrip("/")))
        except (ValueError, NotImplementedError, OSError):
            continue
        for directory in matches:
            resolved = directory.resolve()
            if resolved != resolved_root and resolved_root not in resolved.parents:
                continue
            if (directory / "package.json").is_file() and "node_modules" not in directory.parts:
                found.append(resolved)
    return sorted(set(found))


class Manifests:
    """Every package.json in the root and its workspaces."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directories = [root.resolve(), *workspaces(root)]
        self.packages: list[tuple[str, dict[str, Any]]] = []
        self.raw: dict[str, str] = {}
        for directory in self.directories:
            path = directory / "package.json"
            if path.is_file():
                self.packages.append((relative(root, path), load_jsonc(path)))
                self.raw[relative(root, path)] = read_text(path)

    def dependency(self, name: str) -> str | None:
        """The manifest that declares a dependency, if any."""
        for path, package in self.packages:
            for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
                if isinstance(package.get(field), dict) and name in package[field]:
                    return path
        return None

    def declared(self, names: tuple[str, ...]) -> list[str]:
        return [name for name in names if self.dependency(name)]

    def scripts(self) -> list[tuple[str, str, str]]:
        """(manifest path, script name, command) for every package script."""
        found = []
        for path, package in self.packages:
            scripts = package.get("scripts")
            if isinstance(scripts, dict):
                found += [(path, str(name), str(command)) for name, command in scripts.items()]
        return found


def continuous_integration_files(root: Path, files: list[str]) -> list[str]:
    workflows = [path for path in files if re.match(r"\.github/workflows/[^/]+\.ya?ml$", path)]
    return workflows + [path for path in CONTINUOUS_INTEGRATION_FILES if path in files]


def continuous_integration_test_runs(root: Path, files: list[str]) -> list[str]:
    """Citations of continuous-integration lines that run tests."""
    runs = []
    for path in continuous_integration_files(root, files):
        for number, line in enumerate(read_text(root / path).split("\n"), start=1):
            stripped = line.split("#", 1)[0]
            match = CONTINUOUS_INTEGRATION_TEST_COMMAND.search(stripped)
            if match:
                runs.append(cite(path, number, match.group(0).strip()))
    return runs


# ---------------------------------------------------------------------------
# Test files


def is_test_file(path: str, masked: str | None, imports_test_api: bool = False) -> bool:
    if not path.endswith(SOURCE_SUFFIXES) or path.endswith(".d.ts"):
        return False
    if TEST_NAME_PATTERN.search(path) or "/__tests__/" in f"/{path}":
        return True
    if LOOSE_TEST_NAME_PATTERN.search(path) and (imports_test_api or (masked is not None and TOP_LEVEL_TEST_DECLARATION.search(masked))):
        return True
    if (TEST_DIRECTORY_PATTERN.search(path) or imports_test_api) and masked is not None:
        # tap writes `t.test(...)`; count that form only in files that import a test API.
        return bool(TEST_API_PATTERN.search(masked) or (imports_test_api and re.search(r"\b[A-Za-z_$][\w$]*\s*\.\s*test\s*\(", masked)))
    return False


def in_tooling_folder(path: str, package_roots: list[str]) -> bool:
    """Whether a file sits in a `scripts/`, `tools/`, or `tooling/` folder at the repository root or a workspace package's root."""
    for base in ["", *package_roots]:
        local = path[len(base) + 1:] if base and path.startswith(base + "/") else (path if not base else None)
        if local is not None and local.split("/", 1)[0] in TOOLING_FOLDERS and "/" in local:
            return True
    return False


def imports(text: str) -> list[tuple[str, int]]:
    """Import specifiers in comment-masked text, with their lines."""
    return [(match.group("specifier"), line_of(text, match.start("specifier"))) for match in IMPORT_PATTERN.finditer(text)]


def test_kind(path: str, specifiers: set[str], masked: str) -> str:
    if any(specifier.startswith("@playwright/experimental-ct-") for specifier in specifiers):
        return "playwright-component"
    if "@playwright/test" in specifiers or "playwright/test" in specifiers:
        return "end-to-end"
    if re.search(r"\.cy\.[cm]?[jt]sx?$", path) or path.startswith("cypress/"):
        return "cypress-component" if re.search(r"\bcy\s*\.\s*mount\s*\(", masked) else "end-to-end"
    if any(
        specifier.startswith(("vitest-browser-", "@vitest/browser")) or specifier in ("vitest/browser", "@vitest/browser/context")
        for specifier in specifiers
    ):
        return "vitest-browser"
    if any(specifier.startswith("@testing-library/") and specifier != "@testing-library/jest-dom" for specifier in specifiers):
        return "component"
    if re.search(r"(^|/)integration/|\.(?:integration|int)\.(?:test|spec)\.", path) or specifiers & {
        "supertest", "testcontainers", "@testcontainers/postgresql", "@testcontainers/mysql",
    } or re.search(r"\.inject\s*\(\s*\{", masked):
        return "integration"
    return "unit"


# ---------------------------------------------------------------------------
# Focused and skipped tests

TEST_ROOTS = r"(?:describe|context|suite|it|test|specify|bench)"
FOCUS_PATTERN = re.compile(
    rf"(?<![\w$.]){TEST_ROOTS}(?:\s*\.\s*\w+)*?\s*\.\s*only\b|(?<![\w$.])(?:fit|fdescribe|ftest)\s*\("
)
SKIP_PATTERN = re.compile(
    rf"(?<![\w$.]){TEST_ROOTS}(?:\s*\.\s*\w+)*?\s*\.\s*(?P<kind>skip|todo|fixme)\b(?P<rest>\s*(?:\.\s*\w+\s*)?\()"
    r"|(?<![\w$.])(?:xit|xdescribe|xtest|xcontext|xspecify)\s*\("
)


def focus_and_skips(path: str, text: str, masked: str) -> tuple[list[str], list[str]]:
    focused: list[str] = []
    skipped: list[str] = []
    for match in FOCUS_PATTERN.finditer(masked):
        fragment = match.group(0).rstrip("(").strip()
        if "\n" in fragment:
            fragment = ".only"
        focused.append(cite(path, line_of(masked, match.start()), fragment))
    for match in SKIP_PATTERN.finditer(masked):
        if match.group("kind") in ("skip", "fixme") and ".each" not in match.group(0):
            # A declared skip names the test; `test.skip(condition, ...)` inside a test is a conditional skip.
            after = match.end()
            while after < len(text) and text[after].isspace():
                after += 1
            if after < len(text) and text[after] not in "'\"`)":
                continue
        fragment = match.group(0).rstrip("(").strip()
        if "\n" in fragment:
            fragment = match.group("kind") or "x"
        skipped.append(cite(path, line_of(masked, match.start()), fragment))
    return focused, skipped


# ---------------------------------------------------------------------------
# Test blocks and the mechanical pre-filter

TEST_CALL = re.compile(r"(?<![\w$.])(?P<name>it|test|specify|xit|xtest|fit|ftest)(?P<modifiers>(?:\s*\.\s*[A-Za-z]+)*)\s*(?P<open>[(`])")
TEST_MODIFIERS = {"only", "skip", "todo", "concurrent", "sequential", "fails", "each", "for", "fixme", "failing", "skipIf", "runIf", "serial"}
SECOND_CALL_MODIFIERS = {"each", "for", "skipIf", "runIf"}
EXPECT_CALL = re.compile(r"(?<![\w$.])expect\s*(?:\.\s*(?:soft|poll)\s*)?\(")
ASSERT_CALL = re.compile(r"(?<![\w$.])(?:t\s*\.\s*)?assert(?:\s*\.\s*(?P<method>\w+))?\s*\(")
IMPLICIT_ASSERTION = re.compile(
    r"\.\s*should\s*[(.]|\bcy\s*\.\s*contains\s*\(|(?<![\w$.])(?:(?:screen|canvas)\s*\.\s*)?(?:get|find)(?:All)?By[A-Z]\w*\s*\("
    r"|\bexpect\s*\.\s*(?:assertions|hasAssertions)\s*\(|\bt\s*\.\s*(?:is|not|deepEqual|true|false|truthy|falsy|throws|snapshot|plan)\s*\("
)
MOCK_MATCHERS = {
    "toHaveBeenCalled", "toHaveBeenCalledWith", "toHaveBeenCalledTimes", "toHaveBeenLastCalledWith",
    "toHaveBeenNthCalledWith", "toBeCalled", "toBeCalledWith", "toBeCalledTimes", "lastCalledWith",
    "nthCalledWith", "toHaveReturned", "toHaveReturnedWith", "toHaveReturnedTimes", "toHaveLastReturnedWith",
    "toHaveNthReturnedWith", "toHaveBeenCalledOnce", "toHaveBeenCalledExactlyOnceWith",
    # sinon-chai
    "called", "calledOnce", "calledTwice", "calledThrice", "calledWith", "calledOnceWith", "calledWithExactly",
    "calledOnceWithExactly", "calledWithMatch", "callCount", "calledBefore", "calledAfter", "returned",
}
SNAPSHOT_MATCHERS = {
    "toMatchSnapshot", "toMatchInlineSnapshot", "toMatchFileSnapshot", "toThrowErrorMatchingSnapshot",
    "toThrowErrorMatchingInlineSnapshot",
}
EQUALITY_MATCHERS = {"toBe", "toEqual", "toStrictEqual", "toMatchObject", "equal", "equals", "eq", "eql"}
# Words Chai chains between `expect(value)` and its assertion; `not` negates, and the async words defer it.
CHAIN_WORDS = (
    "to", "be", "been", "is", "that", "which", "and", "has", "have", "with", "at", "of", "same", "but", "does",
    "still", "also", "deep", "nested", "own", "ordered", "any", "all", "itself", "not", "resolves", "rejects", "eventually",
)
EXPECT_CHAIN = re.compile(
    r"(?P<chain>(?:\s*\.\s*(?:" + "|".join(CHAIN_WORDS) + r")\b(?!\s*\())*)\s*\.\s*(?P<matcher>[A-Za-z_$][\w$]*)(?P<call>\s*\()?"
)
# Matchers, Jest's and Chai's, that pass when the value is undefined.
UNDEFINED_PASSING = {"toBeUndefined", "toBeFalsy", "undefined"}
MOCK_VALUE = re.compile(
    r"\.\s*mock(?:Resolved|Returned|Rejected)Value(?:Once)?\s*\(|\.\s*mockImplementation(?:Once)?\s*\(|"
    r"(?<![\w$.])(?:vi|jest)\s*\.\s*fn\s*\(|\bmock\s*\.\s*fn\s*\("
)
GUARD_BEFORE_BRACE = re.compile(r"(?:\b(?:if|for|while|catch)\s*\([^;{}]*\)|\belse|\bcatch)\s*$")
GUARD_BEFORE_PARENTHESIS = re.compile(
    r"(?:\.\s*(?:forEach|map|flatMap|filter|some|every|catch)|\b(?:setTimeout|setInterval|setImmediate|queueMicrotask))\s*$"
)
# Playwright's web-first assertions return promises; unawaited, they never fail the test.
WEB_FIRST_MATCHERS = {
    "toBeVisible", "toBeHidden", "toHaveText", "toContainText", "toHaveCount", "toHaveValue", "toHaveURL",
    "toHaveTitle", "toBeEnabled", "toBeDisabled", "toBeChecked", "toHaveAttribute", "toHaveClass",
    "toBeAttached", "toBeFocused", "toBeEditable", "toBeEmpty", "toHaveCSS", "toHaveScreenshot",
    "toBeInViewport", "toHaveAccessibleName", "toHaveRole", "toPass",
}
GUARD_STATEMENT = re.compile(r"^\s*(?:if|else|for|while)\b|&&\s*$|\|\|\s*$|\?\s*$")
LITERAL = re.compile(r"""^(?:-?\d[\d_.e]*n?|true|false|null|undefined|NaN|(['"`]).*\1|\[\s*\]|\{\s*\})$""", re.S)
HELPER_DEFINITION = re.compile(
    r"(?:\bfunction\s+(?P<function>[A-Za-z_$][\w$]*)\s*\(|\b(?:const|let)\s+(?P<arrow>[A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^()]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]*)?=>)"
)
ASSERTING_HELPER_NAME = re.compile(r"^(?:expect|assert|verify|check|should)[A-Z_]")


def normalise(text: str) -> str:
    return re.sub(r"\s+", "", text).rstrip(",;")


def assertion_helpers(masked: str, imported: set[str]) -> set[str]:
    """Names of functions whose bodies assert, so calling one counts as asserting.

    Local functions are read; imported ones count when their name says they assert.
    """
    helpers = {name for name in imported if ASSERTING_HELPER_NAME.match(name)}
    for match in HELPER_DEFINITION.finditer(masked):
        name = match.group("function") or match.group("arrow")
        start = match.end()
        if match.group("function"):
            closing = matching(masked, start - 1)
            start = closing + 1 if closing != -1 else start
        following = re.match(r"\s*(?::[^{=;]*)?\{", masked[start:])
        if following:
            brace = start + following.end() - 1
            end = matching(masked, brace)
            body = masked[brace:end] if end != -1 else masked[brace:]
        else:
            end = masked.find("\n", start)
            body = masked[start:end if end != -1 else len(masked)]
        if EXPECT_CALL.search(body) or ASSERT_CALL.search(body) or IMPLICIT_ASSERTION.search(body):
            helpers.add(name)
    return helpers


def imported_bindings(masked: str, product: Callable[[str], bool]) -> set[str]:
    """Names a test file imports from the repository's own modules (not packages)."""
    names: set[str] = set()
    for match in re.finditer(r"\bimport\s+(?:type\s+)?(?P<clause>[^;]*?)\s+from\s*(['\"])(?P<specifier>[^'\"]+)\2", masked):
        if match.group("clause").startswith("type ") or not product(match.group("specifier")):
            continue
        clause = match.group("clause")
        names |= set(re.findall(r"\*\s*as\s+([A-Za-z_$][\w$]*)", clause))
        braced = re.search(r"\{([^}]*)\}", clause)
        if braced:
            for item in braced.group(1).split(","):
                item = item.strip()
                if item and not item.startswith("type "):
                    names.add(item.split(" as ")[-1].strip())
        default = re.match(r"\s*([A-Za-z_$][\w$]*)\s*(?:,|$)", clause)
        if default:
            names.add(default.group(1))
    return {name for name in names if name}


def mock_values(text: str, masked: str) -> set[str]:
    """Normalised values a file feeds into its mocks."""
    values: set[str] = set()
    for match in MOCK_VALUE.finditer(masked):
        opening = match.end() - 1
        closing = matching(masked, opening)
        if closing == -1:
            continue
        argument = text[opening + 1:closing].strip()
        arrow = re.match(r"^(?:async\s*)?\([^()]*\)\s*=>\s*(.*)$", argument, re.S)
        if arrow:
            argument = arrow.group(1).strip()
            if argument.startswith("(") and argument.endswith(")"):
                argument = argument[1:-1].strip()
        value = normalise(argument)
        if len(value) >= 3 and not LITERAL.match(argument) and not argument.startswith("{"):
            values.add(value)
        elif argument.startswith(("{", "[")) and len(value) >= 6:
            values.add(value)
    return values


DECLARATION = re.compile(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=;]+)?=(?!=)")


def declarations(text: str, masked: str, start: int, end: int) -> dict[str, str]:
    """Each variable declared between `start` and `end`, with its initializer's first line; the first one wins."""
    found: dict[str, str] = {}
    for match in DECLARATION.finditer(masked, start, end):
        if match.group(1) not in found:
            line_end = masked.find("\n", match.end())
            found[match.group(1)] = text[match.end():line_end if line_end != -1 else len(text)]
    return found


def product_derived(expression: str, names_pattern: re.Pattern[str] | None, local: dict[str, str], file: dict[str, str]) -> bool:
    """Whether an expression is, or names a variable set to, a call into the code under test."""
    if names_pattern is None:
        return False
    expression = expression.strip()
    if names_pattern.match(expression):
        return True
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", expression):
        return False
    initializer = local.get(expression, file.get(expression))
    return initializer is not None and bool(names_pattern.match(initializer))


def opener_stacks(masked: str, start: int, end: int, positions: list[int]) -> dict[int, list[int]]:
    """The open brackets enclosing each position, from one pass over the block."""
    stacks: dict[int, list[int]] = {}
    wanted = sorted(set(positions))
    stack: list[int] = []
    cursor = 0
    for index in range(start, end):
        while cursor < len(wanted) and wanted[cursor] == index:
            stacks[index] = list(stack)
            cursor += 1
        if cursor >= len(wanted):
            break
        character = masked[index]
        if character in "({[":
            stack.append(index)
        elif character in ")}]" and stack:
            stack.pop()
    return stacks


def guarded(masked: str, body_start: int, position: int, openers: list[int]) -> bool:
    """Whether the assertion at `position` runs only on some paths: in an if, loop, callback, or catch."""
    statement_start = max(masked.rfind(";", body_start, position), masked.rfind("{", body_start, position), masked.rfind("}", body_start, position))
    if GUARD_STATEMENT.search(masked[statement_start + 1:position]):
        return True
    for opener in openers:
        window = max(body_start, opener - 120)
        if masked[opener] == "{" and GUARD_BEFORE_BRACE.search(masked[window:opener]):
            return True
        if masked[opener] == "(" and GUARD_BEFORE_PARENTHESIS.search(masked[window:opener]):
            return True
    return False


def awaited(masked: str, body_start: int, position: int) -> bool:
    statement_start = max(masked.rfind(";", body_start, position), masked.rfind("{", body_start, position), masked.rfind("}", body_start, position), masked.rfind("\n", body_start, position))
    prefix = masked[statement_start + 1:position]
    return bool(re.search(r"\b(?:await|return)\b", prefix)) or bool(re.search(r"=>\s*$", masked[max(body_start, position - 40):position]))


class Assertion:
    def __init__(self, line: int, kind: str, matcher: str, negated: bool, subject: str, expected: str | None,
                 conditional: bool, unawaited: bool) -> None:
        self.line = line
        self.kind = kind  # expect or assert
        self.matcher = matcher
        self.negated = negated
        self.subject = subject
        self.expected = expected
        self.conditional = conditional
        self.unawaited = unawaited
        self.expected_from_product = False  # the expected value is computed by the code under test

    def is_mock(self) -> bool:
        return self.matcher in MOCK_MATCHERS or bool(re.search(r"\.mock\.(?:calls|results)|\.callCount\b|\.calledWith\b", self.subject))

    def is_snapshot(self) -> bool:
        return self.matcher in SNAPSHOT_MATCHERS or (self.kind == "assert" and self.matcher == "snapshot")

    def literal_subject(self) -> bool:
        return bool(LITERAL.match(self.subject.strip()))

    def passes_if_undefined(self) -> bool:
        """Whether this assertion still passes when every function the test imports returns undefined."""
        if self.conditional or self.unawaited or self.literal_subject():
            return True
        expected = (self.expected or "").strip()
        if self.kind == "expect":
            if not self.matcher:
                return True  # `expect(value)` with no matcher asserts nothing
            if self.negated:
                return self.matcher not in UNDEFINED_PASSING
            if self.matcher in UNDEFINED_PASSING:
                return True
            if self.matcher in EQUALITY_MATCHERS and expected == "undefined":
                return True
            if self.matcher in EQUALITY_MATCHERS and expected and normalise(expected) == normalise(self.subject):
                return True
            return self.matcher in EQUALITY_MATCHERS and self.expected_from_product
        if self.matcher in ("notEqual", "notStrictEqual", "notDeepEqual", "notDeepStrictEqual", "doesNotThrow", "doesNotReject", "doesNotMatch"):
            return True
        if self.matcher in ("equal", "strictEqual", "deepEqual", "deepStrictEqual") and expected:
            return expected == "undefined" or normalise(expected) == normalise(self.subject) or self.expected_from_product
        return False


def assertions_in(text: str, masked: str, start: int, end: int, web_first: bool = False, names: set[str] | None = None,
                  file_declarations: dict[str, str] | None = None) -> list[Assertion]:
    """The assertions in one test, at most MAX_ASSERTIONS_PER_TEST of them, read in one pass over the block."""
    calls = [("expect", match) for match in EXPECT_CALL.finditer(masked, start, end)]
    calls += [("assert", match) for match in ASSERT_CALL.finditer(masked, start, end)]
    calls = sorted(calls, key=lambda item: item[1].start())[:MAX_ASSERTIONS_PER_TEST]
    stacks = opener_stacks(masked, start, end, [match.start() for _, match in calls])
    names_pattern = re.compile(rf"^\s*(?:await\s+)?(?:new\s+)?(?:{'|'.join(re.escape(name) for name in sorted(names))})\b[\w$.]*\s*\(") if names else None
    local = declarations(text, masked, start, end) if names else {}
    file = file_declarations if file_declarations is not None else {}
    found: list[Assertion] = []
    for kind, match in calls:
        opening = match.end() - 1
        closing = matching(masked, opening)
        if closing == -1 or closing > end:
            continue
        openers = stacks.get(match.start(), [])
        conditional = guarded(masked, start, match.start(), openers)
        if kind == "expect":
            subject = text[opening + 1:closing]
            chain = EXPECT_CHAIN.match(masked, closing + 1, end)
            matcher, negated, expected, asynchronous = "", False, None, "poll" in match.group(0)
            if chain:
                # A Jest-style matcher read but never called, `expect(x).toBeTruthy;`, asserts nothing; Chai's
                # property assertions, such as `.to.be.true`, are not named `toX` and do assert.
                matcher = "" if not chain.group("call") and re.match(r"to[A-Z]", chain.group("matcher")) else chain.group("matcher")
                words = re.findall(r"\w+", chain.group("chain"))
                negated = "not" in words
                asynchronous = asynchronous or bool({"resolves", "rejects", "eventually"} & set(words))
                if chain.group("call"):
                    argument_open = chain.end() - 1
                    argument_close = matching(masked, argument_open)
                    if argument_close != -1:
                        spans = split_arguments(masked, argument_open + 1, argument_close)
                        expected = text[spans[0][0]:spans[0][1]] if spans else None
            asynchronous = asynchronous or (web_first and matcher in WEB_FIRST_MATCHERS)
            assertion = Assertion(
                line_of(masked, match.start()), "expect", matcher, negated, subject, expected, conditional,
                asynchronous and not awaited(masked, start, match.start()),
            )
        else:
            spans = split_arguments(masked, opening + 1, closing)
            subject = text[spans[0][0]:spans[0][1]] if spans else ""
            expected = text[spans[1][0]:spans[1][1]] if len(spans) > 1 else None
            method = match.group("method") or "ok"
            assertion = Assertion(
                line_of(masked, match.start()), "assert", method, method.startswith("not") or method.startswith("doesNot"),
                subject, expected, conditional,
                method in ("rejects", "doesNotReject") and not awaited(masked, start, match.start()),
            )
        assertion.expected_from_product = bool(assertion.expected) and product_derived(assertion.expected or "", names_pattern, local, file)
        found.append(assertion)
    return sorted(found, key=lambda item: item.line)


class TestBlock:
    def __init__(self, path: str, line: int, title: str | None, start: int, end: int, modifiers: str) -> None:
        self.path = path
        self.line = line
        self.title = title
        self.start = start
        self.end = end
        self.modifiers = modifiers
        self.flags: list[tuple[str, int]] = []  # (flag, line)
        self.body = ""


def string_at(text: str, index: int) -> str | None:
    """The string literal starting at `index` (after whitespace), if any."""
    while index < len(text) and text[index].isspace():
        index += 1
    if index >= len(text) or text[index] not in "'\"`":
        return None
    quote = text[index]
    end = index + 1
    while end < len(text) and text[end] != quote:
        end += 2 if text[end] == "\\" else 1
    return text[index + 1:end]


def test_blocks(path: str, text: str, masked: str) -> list[TestBlock]:
    blocks: list[TestBlock] = []
    for match in TEST_CALL.finditer(masked):
        modifiers = {word for word in re.findall(r"[A-Za-z]+", match.group("modifiers"))}
        if not modifiers <= TEST_MODIFIERS:
            continue
        opening = match.start("open")
        if match.group("open") == "`":
            closing = masked.find("`", opening + 1)
            opening = masked.find("(", closing) if closing != -1 else -1
            if opening == -1 or masked[closing + 1:opening].strip():
                continue
        elif modifiers & SECOND_CALL_MODIFIERS:
            closing = matching(masked, opening)
            if closing == -1:
                continue
            following = closing + 1
            while following < len(masked) and masked[following].isspace():
                following += 1
            if following >= len(masked) or masked[following] != "(":
                continue
            opening = following
        title = string_at(text, opening + 1)
        if title is None and modifiers & {"skip", "fixme"}:
            continue  # a conditional skip inside a test, not a test
        closing = matching(masked, opening)
        if closing == -1:
            continue
        block = TestBlock(path, line_of(masked, match.start()), title, opening, closing, match.group("modifiers"))
        block.body = text[opening + 1:closing]
        if "todo" in modifiers or match.group("name").startswith("x") or "skip" in modifiers:
            continue
        blocks.append(block)
    return blocks


class ComparisonBudget:
    """How many full near-duplicate comparisons the rest of the run may make."""

    def __init__(self, remaining: int = MAX_DUPLICATE_COMPARISONS_PER_RUN) -> None:
        self.remaining = remaining


def prefilter(
    path: str, text: str, masked: str, product: Callable[[str], bool] = lambda specifier: specifier.startswith("."),
    web_first: bool = False, budget: ComparisonBudget | None = None,
) -> tuple[list[TestBlock], dict[str, int]]:
    """Flag tests whose shape suggests they cannot fail. A flag is a place to look, not a verdict.

    `product` says whether an import specifier names the repository's own code.
    """
    blocks = test_blocks(path, text, masked)
    names = imported_bindings(mask(text, strings=False), product)
    helpers = assertion_helpers(masked, names)
    fed = mock_values(text, masked)
    file_declarations = declarations(text, masked, 0, len(masked)) if names else {}
    counts: Counter[str] = Counter()
    equality = EQUALITY_MATCHERS | {"strictEqual", "deepEqual", "deepStrictEqual"}
    for block in blocks:
        segment = masked[block.start:block.end]
        found = assertions_in(text, masked, block.start, block.end, web_first, names, file_declarations)
        helper_calls = [name for name in helpers if re.search(rf"(?<![\w$.]){re.escape(name)}\s*\(", segment)]
        implicit = bool(IMPLICIT_ASSERTION.search(segment))
        if not found and not helper_calls and not implicit:
            block.flags.append(("no-assertion", block.line))
        if found and not helper_calls:
            def echo(item: Assertion) -> bool:
                return item.matcher in equality and bool(item.expected and normalise(item.expected) in fed)
            if all(item.is_mock() or echo(item) for item in found):
                block.flags.append(("mock-only", found[0].line))
            if all(item.is_snapshot() for item in found):
                block.flags.append(("snapshot-only", found[0].line))
            if not implicit and all(item.passes_if_undefined() for item in found):
                block.flags.append(("passes-if-undefined", next((item.line for item in found), block.line)))
        counts.update(flag for flag, _ in block.flags)
    near_duplicates(blocks, counts, budget)
    return blocks, dict(counts)


def near_duplicates(blocks: list[TestBlock], counts: Counter[str], budget: ComparisonBudget | None = None) -> None:
    """Flag tests that make the same assertions on nearly the same setup: one of them checks nothing new.

    Bodies are compared as token sequences, and the scan is bounded so a file of
    many near-identical tests stays fast: bodies over MAX_DUPLICATE_BODY characters
    are skipped, a pair must share half its token trigrams before the full
    comparison, a pair whose tests are both flagged already is not compared, and
    the full comparisons are capped per file and per run.
    """
    budget = budget or ComparisonBudget()
    tokens: list[list[str] | None] = []
    shingles: list[set[tuple[str, ...]]] = []
    asserted = []
    for block in blocks:
        body = block.body
        if block.title is not None:
            body = body.replace(block.title, "", 1)
        compact = re.sub(r"\s+", " ", body).strip()
        eligible = 60 <= len(compact) <= MAX_DUPLICATE_BODY and "each" not in block.modifiers
        words = re.findall(r"[\w$]+|[^\w\s]", compact) if eligible else None
        tokens.append(words)
        shingles.append({tuple(words[index:index + 3]) for index in range(max(len(words) - 2, 1))} if words else set())
        asserted.append(sorted(re.sub(r"\s+", "", line) for line in body.split("\n") if re.search(r"\bexpect\b|\bassert\b", line)))
    flagged = [any(flag == "near-duplicate" for flag, _ in block.flags) for block in blocks]
    comparisons = 0
    for left in range(len(blocks)):
        if tokens[left] is None or not asserted[left]:
            continue
        for right in range(left + 1, min(len(blocks), left + 200)):
            if tokens[right] is None or (flagged[left] and flagged[right]) or asserted[left] != asserted[right]:
                continue
            shared = len(shingles[left] & shingles[right])
            if shared < 0.5 * len(shingles[left] | shingles[right]):
                continue
            matcher = difflib.SequenceMatcher(None, tokens[left], tokens[right], autojunk=False)
            if matcher.real_quick_ratio() < 0.9 or matcher.quick_ratio() < 0.9:
                continue
            if comparisons >= MAX_DUPLICATE_COMPARISONS_PER_FILE or budget.remaining <= 0:
                return
            comparisons += 1
            budget.remaining -= 1
            if matcher.ratio() < 0.9:
                continue
            for index in (left, right):
                if not flagged[index]:
                    flagged[index] = True
                    blocks[index].flags.append(("near-duplicate", blocks[index].line))
                    counts["near-duplicate"] += 1


# ---------------------------------------------------------------------------
# Import graph, churn, and risk


def tsconfig_paths(config: Path, root: Path, depth: int = 0) -> tuple[list[tuple[str, list[str]]], bool]:
    """A tsconfig's effective path aliases, following `extends`; targets are relative to the repository root.

    Returns the aliases and whether this file or one it extends defines `paths` at all.
    """
    if depth > 5 or not config.is_file():
        return [], False
    data = load_jsonc(config)
    aliases: list[tuple[str, list[str]]] = []
    defined = False
    base_url: Path | None = None
    extends = data.get("extends")
    for parent in [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []:
        if not isinstance(parent, str):
            continue
        candidates = (
            [(config.parent / parent)] if parent.startswith(".")
            else [folder / "node_modules" / parent for folder in [config.parent, *config.parent.parents]]
        )
        for candidate in candidates:
            target = next((item for item in (candidate, Path(f"{candidate}.json"), candidate / "tsconfig.json") if item.is_file()), None)
            if target:
                parent_aliases, parent_defined = tsconfig_paths(target, root, depth + 1)
                if parent_defined:
                    aliases, defined = parent_aliases, True
                break
    options = data.get("compilerOptions") or {}
    if isinstance(options.get("baseUrl"), str):
        base_url = (config.parent / options["baseUrl"]).resolve()
    paths = options.get("paths")
    if isinstance(paths, dict):
        base = base_url or config.parent.resolve()
        aliases, defined = [], True
        for pattern, targets in paths.items():
            if isinstance(targets, list):
                resolved = []
                for target in targets:
                    if isinstance(target, str):
                        try:
                            resolved.append((base / target).resolve().relative_to(root.resolve()).as_posix())
                        except ValueError:
                            continue
                aliases.append((pattern, resolved))
    return aliases, defined


class Resolver:
    """Resolve relative imports, tsconfig path aliases, and workspace package names to repository files."""

    def __init__(self, root: Path, files: set[str], packages: list[Path] | None = None) -> None:
        self.root = root
        self.files = files
        self.scopes: list[tuple[str, list[tuple[str, list[str]]]]] = []
        folders = [root.resolve(), *(packages or [])]
        for folder in folders:
            for name in ("tsconfig.json", "tsconfig.base.json", "jsconfig.json"):
                aliases, _ = tsconfig_paths(folder / name, root)
                if aliases:
                    self.scopes.append((relative(root, folder) if folder != root.resolve() else "", aliases))
        self.scopes.sort(key=lambda scope: -len(scope[0]))
        self.packages: dict[str, tuple[str, list[str]]] = {}
        for folder in packages or []:
            manifest = load_jsonc(folder / "package.json")
            name = manifest.get("name")
            if isinstance(name, str) and name:
                directory = relative(root, folder)
                entries = []
                for field in ("source", "types", "module", "main"):
                    value = manifest.get(field)
                    if isinstance(value, str):
                        entries.append(value)
                exports = manifest.get("exports")
                if isinstance(exports, str):
                    entries.append(exports)
                elif isinstance(exports, dict):
                    main = exports.get(".", exports)
                    if isinstance(main, str):
                        entries.append(main)
                    elif isinstance(main, dict):
                        entries += [value for value in main.values() if isinstance(value, str)]
                self.packages[name] = (directory, entries)

    def _first(self, base: str) -> str | None:
        base = str(Path(base).as_posix())
        parts: list[str] = []
        for part in base.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if not parts:
                    return None
                parts.pop()
            else:
                parts.append(part)
        stem = "/".join(parts)
        candidates = [stem] + [stem + suffix for suffix in RESOLVE_SUFFIXES] + [f"{stem}/index{suffix}" for suffix in RESOLVE_SUFFIXES]
        if Path(stem).suffix in (".js", ".jsx", ".mjs", ".cjs"):
            bare = stem[: -len(Path(stem).suffix)]
            candidates += [bare + suffix for suffix in (".ts", ".tsx", ".mts", ".cts")]
        return next((candidate for candidate in candidates if candidate in self.files), None)

    def _package(self, specifier: str) -> str | None:
        for name, (directory, entries) in self.packages.items():
            if specifier == name:
                for entry in entries:
                    entry = entry[2:] if entry.startswith("./") else entry
                    found = self._first(f"{directory}/{entry}")
                    if not found and entry.split("/")[0] in ("dist", "build", "lib", "out"):
                        found = self._first(f"{directory}/src/{'/'.join(entry.split('/')[1:])}")
                    if found:
                        return found
                return self._first(f"{directory}/src/index") or self._first(f"{directory}/index")
            if specifier.startswith(name + "/"):
                rest = specifier[len(name) + 1:]
                return self._first(f"{directory}/src/{rest}") or self._first(f"{directory}/{rest}")
        return None

    def resolve(self, importer: str, specifier: str) -> str | None:
        if specifier.startswith("."):
            return self._first(str(Path(importer).parent / specifier))
        for scope, aliases in self.scopes:
            if scope and not importer.startswith(scope + "/"):
                continue
            for pattern, targets in aliases:
                if "*" in pattern:
                    prefix, _, suffix = pattern.partition("*")
                    if specifier.startswith(prefix) and specifier.endswith(suffix):
                        middle = specifier[len(prefix): len(specifier) - len(suffix) if suffix else None]
                        for target in targets:
                            found = self._first(target.replace("*", middle))
                            if found:
                                return found
                elif specifier == pattern:
                    for target in targets:
                        found = self._first(target)
                        if found:
                            return found
        return self._package(specifier)


def churn(root: Path, months: int) -> Counter[str]:
    log = git(root, "-c", "core.quotePath=false", "log", f"--since={months}.months", "--no-merges", "--name-only", "--relative", "--pretty=tformat:")
    return Counter(line.strip() for line in log.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Tool checks


def quotable(haystack: str, *candidates: str) -> str | None:
    """The first candidate that appears verbatim in the text, compared as the audit protocol compares quotes."""
    text = " ".join(haystack.split())
    for candidate in candidates:
        if candidate and "`" not in candidate and " ".join(candidate.split()) in text:
            return candidate
    return None


def script_quote(manifests: Manifests, path: str, name: str, command: str, match: re.Match[str], runner: str | None) -> str:
    """A verifiable quote for a script that runs tests: its key and command up to the runner, else the runner's own token.

    The JSON form of the command matches package.json even when the script has escaped quotes. The quote is never
    just `pnpm` or `node`, which can appear anywhere in the file.
    """
    encoded = json.dumps(command[:match.end()], ensure_ascii=False)[:-1]
    key = json.dumps(name, ensure_ascii=False)
    token = "--test" if runner == "node:test" else match.group(0) if not re.search(r"[\\\"']", match.group(0)) else match.group(0).split()[-1]
    candidates = [f"{key}: {encoded}", f"{key}:{encoded}"] if len(encoded) <= 100 else []
    return quotable(manifests.raw.get(path, ""), *candidates, token, f"{key}:") or token


def runner_facts(manifests: Manifests, files: list[str], test_imports: Counter[str]) -> dict[str, list[str]]:
    """Each detected runner, with verifiable evidence."""
    runners: dict[str, list[str]] = {}

    def add(name: str, entry: str) -> None:
        if entry not in runners.get(name, []):
            runners.setdefault(name, []).append(entry)

    for runner, package in (
        ("vitest", "vitest"), ("jest", "jest"), ("playwright", "@playwright/test"), ("cypress", "cypress"),
        ("storybook-test-runner", "@storybook/test-runner"), ("storybook-test-runner", "@storybook/addon-vitest"),
        ("mocha", "mocha"), ("ava", "ava"), ("karma", "karma"), ("jasmine", "jasmine"), ("jasmine", "jasmine-core"),
        ("tap", "tap"), ("uvu", "uvu"),
    ):
        manifest = manifests.dependency(package)
        if manifest:
            add(runner, cite_file(manifest, f'"{package}"'))
    config_patterns = {
        "vitest": r"(^|/)vitest\.(?:config|workspace)\.[cm]?[jt]s$",
        "jest": r"(^|/)jest\.config\.[cm]?[jt]s(?:on)?$",
        "playwright": r"(^|/)playwright(?:-ct)?\.config\.[cm]?[jt]s$",
        "cypress": r"(^|/)cypress\.config\.[cm]?[jt]s$",
        "karma": r"(^|/)karma\.conf\.[cm]?[jt]s$",
    }
    for runner, pattern in config_patterns.items():
        for path in files:
            if re.search(pattern, path) and runner not in runners:
                add(runner, cite_file(path))
    for module, runner in (("node:test", "node:test"), ("bun:test", "bun:test")):
        if test_imports.get(module):
            add(runner, f"command: `collect.py test-file scan` → {test_imports[module]} test files import {module}")
    for path, name, command in manifests.scripts():
        for runner, pattern in RUNNER_COMMANDS.items():
            match = pattern.search(command)
            if match and runner not in runners:
                add(runner, cite_file(path, script_quote(manifests, path, name, command, match, runner)))
    return runners


def scripts_running_tests(manifests: Manifests) -> list[tuple[str, str, str]]:
    """(manifest, script name, a verifiable quote) for each script that runs a test runner."""
    found = []
    for path, name, command in manifests.scripts():
        runner, match = next(((runner, match) for runner, pattern in RUNNER_COMMANDS.items() for match in [pattern.search(command)] if match), (None, None))
        if match is None and name == "test":
            match = DELEGATING_TEST_COMMAND.search(command)
        if match:
            found.append((path, name, script_quote(manifests, path, name, command, match, runner)))
    return found


def unrecognised_test_scripts(manifests: Manifests) -> list[tuple[str, str]]:
    """`test` scripts that run something the collector does not recognise, so it cannot say whether tests run."""
    found = []
    for path, name, command in manifests.scripts():
        if name != "test" or not command.strip() or PLACEHOLDER_TEST_SCRIPT.search(command):
            continue
        if any(pattern.search(command) for pattern in RUNNER_COMMANDS.values()) or DELEGATING_TEST_COMMAND.search(command):
            continue
        found.append((path, command.strip()))
    return found


def object_block(text: str, key: str) -> str | None:
    """The balanced `{...}` value of the first `key: {` in comment-masked text."""
    match = re.search(rf"\b{re.escape(key)}['\"]?\s*:\s*\{{", text)
    if not match:
        return None
    opening = match.end() - 1
    closing = matching(text, opening)
    return text[opening:closing + 1] if closing != -1 else text[opening:]


def coverage_collection_flag(command: str) -> str | None:
    """Find an enabled collection option, not a coverage sub-option or false value."""
    fragments: list[str] = []
    start = 0
    quote = ""
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote:
            if character == quote:
                quote = ""
        elif character in "'\"":
            quote = character
        elif character in ";&|\n":
            fragments.append(command[start:index])
            start = index + 1
    fragments.append(command[start:])
    for fragment in fragments:
        try:
            tokens = shlex.split(fragment, comments=True)
        except ValueError:
            continue
        flags: list[tuple[str, str]] = []
        for index, token in enumerate(tokens):
            name, assignment, value = token.partition("=")
            if name not in ("--coverage", "--coverage.enabled", "--experimental-test-coverage"):
                continue
            flag = token
            if not assignment:
                value = "true"
                if index + 1 < len(tokens) and tokens[index + 1] in ("true", "false"):
                    value = tokens[index + 1]
                    flag += " " + value
            flags.append((flag, value))
        # Duplicate flags can be rejected by the runner; do not invent precedence.
        if len(flags) == 1 and flags[0][1] == "true":
            return flags[0][0]
    return None


# A coverage metric set above zero; a negative number is a maximum count of uncovered items, also a floor.
NONZERO_METRIC = re.compile(
    r"""\b(?:lines|branches|functions|statements)['"]?\s*:\s*(?:-\s*)?(?:0*[1-9]\d*(?:\.\d*)?|0*\.\d*[1-9])|\b100['"]?\s*:\s*true\b"""
)
NONZERO_VALUE = r"-?(?:0*[1-9]\d*(?:\.\d*)?|0*\.\d*[1-9])"
# Runners whose own coverage floor settings the collector does not read: bunfig.toml, karma.conf.js, and .taprc.
FLOOR_UNREAD_RUNNERS = frozenset({"bun:test", "karma", "angular-cli", "tap"})


def json_floor(value: Any) -> bool:
    """Whether a Jest `coverageThreshold` value from JSON sets at least one metric above zero."""
    if isinstance(value, dict):
        return any(
            (key in ("lines", "branches", "functions", "statements") and isinstance(item, (int, float)) and not isinstance(item, bool) and item != 0)
            or json_floor(item)
            for key, item in value.items()
        )
    return False


def coverage_facts(root: Path, files: list[str], manifests: Manifests, workflows: list[str]) -> dict[str, Any]:
    """Where coverage floors are set, and what collects coverage. A floor needs at least one metric above zero."""
    thresholds: list[str] = []
    enabled: list[str] = []
    for path in files:
        name = Path(path).name
        if re.fullmatch(r"(?:vitest|vite)\.config\.[cm]?[jt]s|vitest\.workspace\.[cm]?[jt]s", name):
            text = mask(read_text(root / path), strings=False)
            block = object_block(text, "coverage")
            if block:
                limits = object_block(block, "thresholds")
                if limits is not None:
                    if NONZERO_METRIC.search(limits):
                        thresholds.append(cite_file(path, "thresholds"))
                elif NONZERO_METRIC.search(block):
                    thresholds.append(cite_file(path, "coverage", "with per-metric minimums"))
                if re.search(r"\benabled\s*:\s*true\b", block):
                    enabled.append(cite_file(path, "enabled: true"))
        elif re.fullmatch(r"jest\.config\.[cm]?[jt]s(?:on)?", name):
            text = mask(read_text(root / path), strings=False)
            if re.search(r"\bcoverageThreshold['\"]?\s*:", text):
                limits = object_block(text, "coverageThreshold")
                # An object literal must set a metric above zero; any other value cannot be read, so it is credited as before.
                if limits is None or NONZERO_METRIC.search(limits):
                    thresholds.append(cite_file(path, "coverageThreshold"))
            if re.search(r"\bcollectCoverage['\"]?\s*:\s*true\b", text):
                enabled.append(cite_file(path, "collectCoverage"))
        elif name in (".c8rc", ".c8rc.json", ".nycrc", ".nycrc.json"):
            text = read_text(root / path)
            if re.search(r"check-coverage|checkCoverage", text) and re.search(r"\b(?:lines|branches|functions|statements)\b", text):
                thresholds.append(cite_file(path, "check-coverage" if "check-coverage" in text else "checkCoverage"))
    for path, package in manifests.packages:
        jest = package.get("jest")
        if isinstance(jest, dict) and json_floor(jest.get("coverageThreshold")):
            thresholds.append(cite_file(path, '"coverageThreshold"'))
        if isinstance(jest, dict) and jest.get("collectCoverage") is True:
            enabled.append(cite_file(path, '"collectCoverage"'))
        for tool in ("c8", "nyc"):
            settings = package.get(tool)
            if isinstance(settings, dict) and (settings.get("check-coverage") or settings.get("checkCoverage")):
                thresholds.append(cite_file(path, f'"{tool}"'))
    for path, name, command in manifests.scripts():
        fragment = command.strip()
        raw = manifests.raw.get(path, "")
        match = re.search(
            rf"--coverage\.thresholds\.\w+={NONZERO_VALUE}(?![\d.])|--coverage\.thresholds\.100(?:=true)?(?![\w.=])"
            rf"|--test-coverage-(?:lines|branches|functions)={NONZERO_VALUE}(?![\d.])"
            r"|\bc8\b[^&|;]*--check-coverage|\bnyc\b[^&|;]*--check-coverage",
            fragment,
        )
        if match:
            # Quote text that appears verbatim in package.json, even when the script has escaped quotes.
            thresholds.append(cite_file(path, quotable(raw, match.group(0), "--check-coverage") or match.group(0)))
        flag = coverage_collection_flag(fragment)
        if not flag:
            wrapper = re.search(r"(?<![\w-])(?:c8|nyc)\s", fragment)
            flag = wrapper.group(0).strip() if wrapper else None
        if flag:
            enabled.append(cite_file(path, quotable(raw, flag, flag.partition("=")[0].split()[0]) or flag))
    for path in workflows:
        for number, line in enumerate(read_text(root / path).split("\n"), start=1):
            fragment = line.split("#", 1)[0].strip()
            inline_run = re.match(r"(?:-\s*)?run:\s*(.+)", fragment)
            if inline_run:
                fragment = inline_run.group(1)
                if fragment[:1] in ("'", '"') and fragment[-1:] == fragment[:1]:
                    fragment = fragment[1:-1]
            flag = coverage_collection_flag(fragment)
            if flag:
                enabled.append(cite(path, number, quotable(line, flag, flag.partition("=")[0].split()[0]) or flag))
    return {"thresholds": thresholds, "enabled": enabled}


LINT_PLUGINS = {
    "testing-library": {
        "package": "eslint-plugin-testing-library",
        "required": TESTING_LIBRARY_RULES,
        "presets": TESTING_LIBRARY_PRESETS,
    },
    "jest-dom": {
        "package": "eslint-plugin-jest-dom",
        "required": JEST_DOM_RULES,
        "presets": {"recommended": set(JEST_DOM_RULES), "all": set(JEST_DOM_RULES)},
    },
}
# Packages whose shared configurations cannot enable or disable a testing-library or jest-dom rule.
NEUTRAL_CONFIG_PACKAGE = re.compile(
    r"^(?:@eslint/js|@eslint/compat|@eslint/eslintrc|@eslint/config-helpers|typescript-eslint|@typescript-eslint/[\w-]+|globals"
    r"|eslint|eslint/config|eslint-config-prettier(?:/[\w-]+)?|node:[\w/]+|path|url"
    r"|eslint-plugin-(?!testing-library$|jest-dom$)[\w-]+(?:/[\w./-]+)?|@[\w-]+/eslint-plugin(?:-[\w-]+)?(?:/[\w./-]+)?)$"
)
NEUTRAL_LEGACY_EXTENDS = re.compile(r"^(?:eslint:[\w-]+|plugin:(?!testing-library/|jest-dom/)[@\w/-]+/[\w-]+|prettier(?:/[\w-]+)?)$")
LEVELS = {"0": "off", "1": "warn", "2": "error", "off": "off", "warn": "warn", "error": "error"}


class Layer:
    """One configuration object: where it applies, and the testing-library and jest-dom rule levels it sets."""

    def __init__(self, base: str, files: list[str] | None = None, ignores: list[str] | None = None,
                 rules: dict[str, str] | None = None, match_base: bool = False, global_ignores: bool = False) -> None:
        self.base = base  # folder the patterns are relative to, relative to the repository root
        self.files = files
        self.ignores = ignores
        self.rules = rules or {}
        self.match_base = match_base  # legacy overrides match patterns without a slash against the file name
        self.global_ignores = global_ignores
        self.presets: list[str] = []
        self.evidence: list[str] = []
        self.keys: set[str] = set()  # the properties a configuration object sets, which a spread copies


def glob_regex(pattern: str) -> str:
    """A regular expression for an ESLint (minimatch) glob."""
    output = ""
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            output += "(?:.*/)?"
            index += 3
            continue
        if pattern.startswith("**", index):
            output += ".*"
            index += 2
            continue
        character = pattern[index]
        if character == "*":
            output += "[^/]*"
        elif character == "?":
            output += "[^/]"
        elif character == "{":
            end = pattern.find("}", index)
            if end == -1:
                output += re.escape(character)
            else:
                output += "(?:" + "|".join(glob_regex(part) for part in pattern[index + 1:end].split(",")) + ")"
                index = end
        elif character == "[":
            end = pattern.find("]", index)
            if end == -1:
                output += re.escape(character)
            else:
                output += pattern[index:end + 1].replace("[!", "[^")
                index = end
        else:
            output += re.escape(character)
        index += 1
    return output


def glob_matches(pattern: str, path: str, match_base: bool = False) -> bool:
    pattern = pattern[2:] if pattern.startswith("./") else pattern
    if pattern.startswith("!"):
        return False
    if match_base and "/" not in pattern:
        path = path.rsplit("/", 1)[-1]
    return re.fullmatch(glob_regex(pattern), path) is not None


def layer_applies(layer: Layer, path: str) -> bool:
    if layer.base and not path.startswith(layer.base + "/"):
        return False
    local = path[len(layer.base) + 1:] if layer.base else path
    if layer.files is not None and not any(glob_matches(pattern, local, layer.match_base) for pattern in layer.files):
        return False
    if layer.ignores and any(glob_matches(pattern, local, layer.match_base) for pattern in layer.ignores):
        return False
    return True


def rule_level(value: str) -> str | None:
    match = re.match(r"""\s*\[?\s*['"]?(off|warn|error|[012])\b""", value)
    return LEVELS[match.group(1)] if match else None


def preset_layer(base: str, plugin: str, preset: str, evidence: str) -> Layer:
    rules = {f"{plugin}/{rule}": "error" for rule in LINT_PLUGINS[plugin]["presets"].get(preset.split("/")[-1], set())}
    layer = Layer(base, rules=rules)
    layer.keys.add("rules")
    layer.presets.append(f"{plugin}:{preset}")
    layer.evidence.append(evidence)
    return layer


class FlatConfig:
    """Read an `eslint.config.*` file as text and list its configuration objects in order.

    Only objects reachable from the default export count. Anything that could
    change a testing-library or jest-dom rule but cannot be read, such as a
    shared configuration package or a helper function, is recorded as unresolved.
    """

    def __init__(self, path: str, text: str) -> None:
        self.path = path
        self.base = path.rsplit("/", 1)[0] if "/" in path else ""
        self.content = mask(text, strings=False)
        self.structure = mask(text, strings=True)
        self.unresolved: list[str] = []
        self.bindings: dict[str, str] = {}
        for match in re.finditer(r"""\bimport\s+(?P<clause>[^;]*?)\s+from\s*(['"])(?P<source>[^'"]+)\2""", self.content):
            clause = match.group("clause")
            for name in re.findall(r"(?:^|,)\s*([A-Za-z_$][\w$]*)\s*(?=,|$)", clause) + re.findall(r"\*\s*as\s+([A-Za-z_$][\w$]*)", clause):
                self.bindings[name] = match.group("source")
            braced = re.search(r"\{([^}]*)\}", clause)
            for item in (braced.group(1).split(",") if braced else []):
                if item.strip():
                    self.bindings[item.split(" as ")[-1].strip()] = match.group("source")
        for match in re.finditer(r"""\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*(['"])([^'"]+)\2\s*\)""", self.content):
            self.bindings[match.group(1)] = match.group(3)
        self.variables: dict[str, tuple[int, int]] = {}
        for match in re.finditer(r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=;]+)?=(?!=)\s*", self.structure):
            if match.group(1) not in self.bindings:
                self.variables[match.group(1)] = (match.end(), self.expression_end(match.end()))
        self.compat = {
            name for name, (start, end) in self.variables.items()
            if re.match(r"new\s+FlatCompat\s*\(", self.structure[start:end])
        }

    def expression_end(self, start: int) -> int:
        """The end of the expression starting at `start`: the first `;`, or newline, at depth zero."""
        depth = 0
        for index in range(start, len(self.structure)):
            character = self.structure[index]
            if character in "([{":
                depth += 1
            elif character in ")]}":
                if depth == 0:
                    return index
                depth -= 1
            elif depth == 0 and (character == ";" or (character == "\n" and self.structure[start:index].strip())):
                return index
        return len(self.structure)

    def layers(self) -> list[Layer]:
        export = re.search(r"\bexport\s+default\s+|\bmodule\s*\.\s*exports\s*=\s*", self.structure)
        if not export:
            self.unresolved.append(f"{self.path} has no default export the collector can read")
            return []
        return self.evaluate(export.end(), self.expression_end(export.end()), 0)

    def text(self, start: int, end: int) -> str:
        return self.content[start:end].strip()

    def strip_span(self, start: int, end: int) -> tuple[int, int]:
        while start < end and self.structure[start].isspace():
            start += 1
        while end > start and self.structure[end - 1] in " \t\r\n;":
            end -= 1
        return start, end

    def elements(self, start: int, end: int, depth: int) -> list[Layer]:
        found: list[Layer] = []
        for item_start, item_end in split_arguments(self.structure, start, end):
            found += self.evaluate(item_start, item_end, depth + 1)
        return found

    def evaluate(self, start: int, end: int, depth: int) -> list[Layer]:
        start, end = self.strip_span(start, end)
        expression = self.text(start, end)
        if not expression or depth > 25:
            return []
        if expression.startswith("..."):
            return self.evaluate(start + 3, end, depth + 1)
        opener = self.structure[start]
        if opener in "[{" and matching(self.structure, start) == end - 1:
            if opener == "[":
                return self.elements(start + 1, end - 1, depth)
            return self.object_layers(start, end, depth)
        preset = re.fullmatch(r"""([A-Za-z_$][\w$]*)\s*\.\s*configs\s*(?:\[\s*(['"])([^'"]+)\2\s*\]|\.\s*([\w-]+))""", expression)
        if preset:
            plugin = self.plugin_of(preset.group(1))
            if plugin:
                return [preset_layer(self.base, plugin, preset.group(3) or preset.group(4), cite_file(self.path, expression))]
        call = re.match(r"([A-Za-z_$][\w$.]*)\s*\(", self.structure[start:end])
        if call and matching(self.structure, start + call.end() - 1) == end - 1:
            callee = call.group(1)
            arguments_start = start + call.end()
            if callee.endswith(".config") or callee in ("defineConfig", "tseslint.config"):
                return self.elements(arguments_start, end - 1, depth)
            owner = callee.split(".")[0]
            if owner in self.compat and callee.endswith(".extends"):
                return self.compat_extends(arguments_start, end - 1)
            if self.neutral(owner):
                return []
            self.unresolved.append(f"{self.path}: `{fragment(expression, 50)}` is a call the collector cannot evaluate")
            return []
        name = re.fullmatch(r"([A-Za-z_$][\w$]*)((?:\s*\.\s*[\w$]+|\s*\[[^\]]*\])*)", expression)
        if name:
            base = name.group(1)
            if base in self.variables and not name.group(2):
                variable_start, variable_end = self.variables[base]
                return self.evaluate(variable_start, variable_end, depth + 1)
            if self.neutral(base) or self.plugin_of(base):
                return []
        self.unresolved.append(f"{self.path}: `{fragment(expression, 50)}` cannot be read statically")
        return []

    def neutral(self, name: str) -> bool:
        source = self.bindings.get(name)
        return source is not None and bool(NEUTRAL_CONFIG_PACKAGE.match(source))

    def plugin_of(self, name: str) -> str | None:
        source = self.bindings.get(name, "")
        for plugin, settings in LINT_PLUGINS.items():
            if source == settings["package"]:
                return plugin
        return None

    def compat_extends(self, start: int, end: int) -> list[Layer]:
        found = []
        for item_start, item_end in split_arguments(self.structure, start, end):
            value = self.text(item_start, item_end).strip("'\"")
            found += legacy_extends(self.path, self.base, value, self.unresolved)
        return found

    def strings(self, start: int, end: int, depth: int) -> list[str] | None:
        """The string values of an array expression, following a variable; None if unreadable."""
        start, end = self.strip_span(start, end)
        expression = self.text(start, end)
        if expression in self.variables and depth < 5:
            return self.strings(*self.variables[expression], depth + 1)
        if not expression.startswith("["):
            if re.fullmatch(r"""(['"])[^'"]*\1""", expression):
                return [expression[1:-1]]
            return None
        values = []
        for item_start, item_end in split_arguments(self.structure, start + 1, end - 1):
            item = self.text(item_start, item_end)
            if item.startswith("..."):
                spread = self.strings(item_start + item.index("...") + 3, item_end, depth + 1)
                if spread is None:
                    return None
                values += spread
            elif re.fullmatch(r"""(['"`])[^'"`]*\1""", item):
                values.append(item[1:-1])
            else:
                return None
        return values

    def object_layers(self, start: int, end: int, depth: int) -> list[Layer]:
        layer = Layer(self.base)
        before: list[Layer] = []
        keys: set[str] = set()
        for item_start, item_end in split_arguments(self.structure, start + 1, end - 1):
            item_start, item_end = self.strip_span(item_start, item_end)
            item = self.text(item_start, item_end)
            if item.startswith("..."):
                # A spread copies the spread object's own properties, in order: a later key overrides it, and it
                # overrides an earlier one. `files`, `ignores`, and `rules` are the ones that decide the result.
                spread = self.evaluate(item_start + 3, item_end, depth + 1)
                if spread:
                    copied = spread[-1]
                    for name in ("files", "ignores"):
                        if name in copied.keys:
                            setattr(layer, name, None if getattr(copied, name) is None else list(getattr(copied, name)))
                    if "rules" in copied.keys:
                        layer.rules = dict(copied.rules)
                    layer.presets += [name for part in spread for name in part.presets]
                    layer.evidence += [entry for part in spread for entry in part.evidence]
                    keys |= copied.keys
                continue
            key = re.match(r"""(['"]?)([\w$-]+)\1\s*:\s*""", item)
            if not key:
                continue
            value_start = item_start + key.end()
            keys.add(key.group(2))
            if key.group(2) in ("files", "ignores"):
                values = self.strings(value_start, item_end, 0)
                if values is None:
                    self.unresolved.append(f"{self.path}: the `{key.group(2)}` value `{fragment(self.text(value_start, item_end), 40)}` cannot be read")
                    values = []
                setattr(layer, key.group(2), values)
            elif key.group(2) == "rules":
                layer.rules = self.rules_object(value_start, item_end, depth)
            elif key.group(2) == "extends":
                before += self.evaluate(value_start, item_end, depth + 1)
        for extended in before:
            extended.files = layer.files if extended.files is None else extended.files
            extended.ignores = layer.ignores if extended.ignores is None else extended.ignores
        if keys <= {"ignores", "name"} and "ignores" in keys:
            layer.global_ignores = True
        layer.keys = keys
        return before + [layer]

    def rules_object(self, start: int, end: int, depth: int) -> dict[str, str]:
        start, end = self.strip_span(start, end)
        if self.structure[start] != "{":
            expression = self.text(start, end)
            if expression in self.variables:
                return self.rules_object(*self.variables[expression], depth + 1)
            self.unresolved.append(f"{self.path}: the rules value `{fragment(expression, 40)}` cannot be read")
            return {}
        rules: dict[str, str] = {}
        for item_start, item_end in split_arguments(self.structure, start + 1, matching(self.structure, start)):
            item = self.text(item_start, item_end)
            if item.startswith("..."):
                source = re.fullmatch(r"""\.\.\.\s*([A-Za-z_$][\w$]*)\s*\.\s*configs\s*(?:\[\s*(['"])([^'"]+)\2\s*\]|\.\s*([\w-]+))\s*\.\s*rules""", item)
                plugin = self.plugin_of(source.group(1)) if source else None
                if plugin and source:
                    rules.update(preset_layer(self.base, plugin, source.group(3) or source.group(4), "").rules)
                elif not (source and self.neutral(source.group(1))) and not self.neutral(item[3:].split(".")[0].strip()):
                    self.unresolved.append(f"{self.path}: rules spread `{fragment(item, 40)}` cannot be read")
                continue
            entry = re.match(r"""(['"])((?:testing-library|jest-dom)/[\w-]+)\1\s*:\s*(.*)""", item, re.S)
            if entry:
                level = rule_level(entry.group(3))
                if level:
                    rules[entry.group(2)] = level
        return rules


def legacy_extends(path: str, base: str, value: str, unresolved: list[str]) -> list[Layer]:
    preset = re.fullmatch(r"plugin:(testing-library|jest-dom)/([\w/-]+)", value)
    if preset:
        return [preset_layer(base, preset.group(1), preset.group(2), cite_file(path, value))]
    if not NEUTRAL_LEGACY_EXTENDS.match(value):
        unresolved.append(f"{path} extends `{value}`, which the collector cannot read")
    return []


def legacy_json_layers(path: str, base: str, config: dict[str, Any], unresolved: list[str], files: list[str] | None = None,
                       excluded: list[str] | None = None, raw: str = "") -> list[Layer]:
    layers: list[Layer] = []
    extends = config.get("extends")
    for value in [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []:
        if isinstance(value, str):
            for layer in legacy_extends(path, base, value, unresolved):
                layer.files, layer.ignores, layer.match_base = files, excluded, True
                layers.append(layer)
    rules = config.get("rules")
    if isinstance(rules, dict):
        layer = Layer(base, files=files, ignores=excluded, match_base=True)
        for rule, value in rules.items():
            if re.match(r"(?:testing-library|jest-dom)/", str(rule)):
                level = rule_level(json.dumps(value) if not isinstance(value, str) else value)
                if level:
                    layer.rules[rule] = level
                    if f'"{rule}"' in raw:
                        layer.evidence.append(cite_file(path, f'"{rule}"'))
        layers.append(layer)
    for override in config.get("overrides") or []:
        if isinstance(override, dict):
            patterns = override.get("files")
            patterns = [patterns] if isinstance(patterns, str) else [item for item in patterns or [] if isinstance(item, str)]
            ignored = override.get("excludedFiles")
            ignored = [ignored] if isinstance(ignored, str) else [item for item in ignored or [] if isinstance(item, str)]
            layers += legacy_json_layers(path, base, override, unresolved, patterns or None, ignored or None, raw)
    return layers


def legacy_extends_values(content: str, unresolved: list[str] | None = None, path: str = "") -> list[str]:
    """The `extends` entries of a YAML or JavaScript legacy configuration: a list, one value, or (YAML only) a block list.

    The key is found wherever it sits, at the start of a line or after `{` or `,` on one line, as in
    `module.exports = { extends: ['plugin:jest-dom/recommended'] };`. A value it cannot read, such as a variable,
    is recorded as unresolved rather than read as no shared configuration.
    """
    # The first `extends` is the top-level one; those inside overrides are reported as unread scopes.
    match = re.search(r"""(?m)(?:^|[{,])[ \t]*['"]?extends['"]?[ \t]*:[ \t]*(.*)$""", content)
    if not match:
        return []
    if path.endswith((".js", ".cjs")):
        # JavaScript: the value may start on a later line, so skip whitespace and newlines before classifying it.
        start = match.start(1)
        while start < len(content) and content[start].isspace():
            start += 1
        rest = content[start:]
        if rest.startswith("["):
            closing = rest.find("]")
            items = rest[1:closing if closing != -1 else len(rest)].split(",")
        elif rest[:1] in ("'", '"'):
            closing = rest.find(rest[0], 1)
            items = [rest[:closing + 1] if closing != -1 else rest]
        else:
            if unresolved is not None:
                shown = fragment(rest.split("\n", 1)[0].rstrip(",;} \t"), 40) or "an empty value"
                unresolved.append(f"{path} extends `{shown}`, which the collector cannot read")
            items = []
        return [item.strip().strip(",").strip().strip("'\"") for item in items if item.strip().strip(",").strip()]
    value = match.group(1).strip()
    if value.startswith("["):
        closing = content.find("]", match.start(1))
        items = content[match.start(1) + 1:closing if closing != -1 else len(content)].split(",")
    elif value[:1] in ("'", '"'):
        closing = value.find(value[0], 1)
        items = [value[:closing + 1] if closing != -1 else value]
    elif value:
        items = [value]
    else:
        items = []
        for line in content[match.end():].split("\n")[1:]:
            item = re.match(r"\s*-\s*(.+)$", line)
            if not item:
                if line.strip():
                    break
                continue
            items.append(item.group(1))
    return [item.strip().strip(",").strip().strip("'\"") for item in items if item.strip().strip(",").strip()]


def legacy_text_layers(path: str, base: str, text: str, unresolved: list[str]) -> list[Layer]:
    """YAML and JavaScript legacy configuration, read as text: shared configurations first, then rules."""
    content = mask(text, strings=False) if path.endswith((".js", ".cjs")) else re.sub(r"(?m)(^|\s)#.*$", r"\1", text)
    layers: list[Layer] = []
    if path.endswith((".js", ".cjs")) and re.search(r"\bmodule\s*\.\s*exports\s*=", content) and not re.search(r"\bmodule\s*\.\s*exports\s*=\s*\{", content):
        unresolved.append(f"{path} exports something other than an object literal, which the collector cannot read")
    for entry in legacy_extends_values(content, unresolved, path):
        layers += legacy_extends(path, base, entry, unresolved)
    layer = Layer(base)
    for match in re.finditer(r"""(['"]?)((?:testing-library|jest-dom)/[\w-]+)\1\s*:\s*([^\n,}]+)""", content):
        level = rule_level(match.group(3))
        if level:
            layer.rules[match.group(2)] = level
            layer.evidence.append(cite_file(path, match.group(2)))
    if re.search(r"\boverrides\b", content):
        unresolved.append(f"{path} has overrides whose file scopes the collector does not read in this format")
    return layers + [layer]


def eslint_configuration(root: Path, files: list[str], manifests: Manifests, sample_files: list[str]) -> dict[str, Any]:
    """Decide which testing-library and jest-dom rules apply to the sample test files, reading configuration as text."""
    flat = [path for path in files if re.fullmatch(r"eslint\.config\.[cm]?[jt]s", Path(path).name)]
    legacy = [path for path in files if Path(path).name in ESLINT_CONFIG_NAMES and path not in flat]
    folder = lambda path: path.rsplit("/", 1)[0] if "/" in path else ""  # noqa: E731
    unresolved: list[str] = []
    layers_by_config: dict[str, list[Layer]] = {}
    for path in flat:
        config = FlatConfig(path, read_text(root / path))
        layers_by_config[path] = config.layers()
        unresolved += config.unresolved
    for path in legacy:
        text = read_text(root / path)
        parsed: Any = None
        if path.endswith((".json", "/.eslintrc")) or Path(path).name == ".eslintrc":
            try:
                parsed = json.loads(strip_json_comments(text))
            except json.JSONDecodeError:
                parsed = None
        layers_by_config[path] = (
            legacy_json_layers(path, folder(path), parsed, unresolved, raw=text) if isinstance(parsed, dict)
            else legacy_text_layers(path, folder(path), text, unresolved)
        )
    for path, package in manifests.packages:
        if isinstance(package.get("eslintConfig"), dict):
            layers_by_config[path] = legacy_json_layers(path, folder(path), package["eslintConfig"], unresolved, raw=read_text(root / path))
            legacy.append(path)

    def layers_for(sample: str) -> list[Layer]:
        """The nearest flat configuration, or else the cascade of legacy ones from the root down."""
        nearest = sorted((path for path in flat if not folder(path) or sample.startswith(folder(path) + "/")), key=lambda path: -len(folder(path)))
        if nearest:
            return layers_by_config[nearest[0]]
        cascade = sorted((path for path in legacy if not folder(path) or sample.startswith(folder(path) + "/")), key=lambda path: len(folder(path)))
        return [layer for path in cascade for layer in layers_by_config[path]]

    result: dict[str, Any] = {"files": flat + legacy, "unresolved": sorted(set(unresolved)), "samples": sample_files}
    for plugin, settings in LINT_PLUGINS.items():
        effective: set[str] | None = None
        evidence: list[str] = []
        presets: set[str] = set()
        mentioned = False
        for sample in sample_files:
            state: dict[str, str] = {}
            ignored = False
            for layer in layers_for(sample):
                if layer.global_ignores:
                    local = sample[len(layer.base) + 1:] if layer.base else sample
                    parts = local.split("/")
                    prefixes = ["/".join(parts[:index]) for index in range(1, len(parts) + 1)]
                    if any(glob_matches(pattern.rstrip("/"), prefix) for pattern in layer.ignores or [] for prefix in prefixes):
                        ignored = True
                    continue
                if not layer_applies(layer, sample):
                    continue
                for rule, level in layer.rules.items():
                    if rule.startswith(plugin + "/"):
                        state[rule] = level
                        mentioned = True
                        evidence += [entry for entry in layer.evidence if entry not in evidence]
                presets |= {name.split(":", 1)[1] for name in layer.presets if name.startswith(plugin + ":")}
            enabled = set() if ignored else {rule.split("/", 1)[1] for rule, level in state.items() if level != "off"}
            effective = enabled if effective is None else effective & enabled
        result[plugin] = {
            "mentioned": mentioned,
            "presets": sorted(presets),
            "effective": sorted(effective or set()),
            "evidence": evidence[:6],
        }
    return result


def lint_check(plugin: str, facts: dict[str, Any], manifests: Manifests) -> dict[str, Any]:
    settings = LINT_PLUGINS[plugin]
    package = settings["package"]
    found = facts[plugin]
    unresolved = facts["unresolved"]
    missing = [rule for rule in settings["required"] if rule not in found["effective"]]
    scope = f"the {len(facts['samples'])} test file(s) it was checked against"
    if missing and unresolved:
        return {
            "evaluationState": "not-evaluated",
            "reason": f"ESLint configuration the collector cannot read may set the {plugin} rules: "
            + "; ".join(unresolved[:3]) + f". Rules found on for {scope}: " + (", ".join(found["effective"]) or "none") + ".",
        }
    if not found["mentioned"]:
        evidence = [f"command: `collect.py ESLint configuration scan` → {len(facts['files'])} configuration file(s); no {plugin} rule applies to {scope}"]
        evidence += [cite_file(path) for path in facts["files"][:2]]
        return {
            "status": "missing",
            "evidence": evidence,
            "evidenceTier": "direct",
            "gap": f"No ESLint configuration enables {package} for test files, so nothing stops the test mistakes its rules catch from being committed.",
            "remediation": (
                f"Install {package} and add its shared configuration for test files"
                + (", plus `testing-library/prefer-user-event` and `testing-library/prefer-explicit-assert`, which no shared configuration enables" if plugin == "testing-library" else "")
                + "; run ESLint over test files in continuous integration."
            ),
            "judgement": "consider",
        }
    evidence = found["evidence"] or [f"command: `collect.py ESLint configuration scan` → {plugin} rules found for {scope}"]
    if not missing:
        result = {"status": "present", "evidence": evidence, "evidenceTier": "direct"}
        if unresolved:
            result["degradedReason"] = "Unreadable ESLint configuration may turn some of these rules off: " + "; ".join(unresolved[:2])
            result["evidenceTier"] = "supported"
        return result
    return {
        "status": "partial",
        "evidence": evidence[:6],
        "evidenceTier": "direct",
        "gap": f"{package} is enabled, but {len(missing)} of the required rules are not on for {scope}: " + ", ".join(f"{plugin}/{rule}" for rule in missing) + ".",
        "remediation": "Turn on the missing rules for test files: " + ", ".join(f"'{plugin}/{rule}': 'error'" for rule in missing) + ".",
        "judgement": "consider",
    }


# ---------------------------------------------------------------------------
# Mutation testing


def stryker_binary(root: Path) -> Path | None:
    for name in ("stryker", "stryker.cmd"):
        candidate = root / "node_modules" / ".bin" / name
        if candidate.is_file():
            return candidate
    return None


def mutation_configuration(root: Path, targets: list[str]) -> str:
    """A one-off Stryker configuration that keeps the project's own settings and mutates only the targets."""
    directory = MUTATION_DIRECTORY.as_posix()
    # Forced after the project's own settings: mutate a sandbox copy, never the working tree (`inPlace`),
    # and send every file Stryker writes (report, log, incremental state) under the audit's directory.
    overrides = {
        "mutate": targets,
        "inPlace": False,
        "reporters": ["json"],
        "jsonReporter": {"fileName": f"{directory}/mutation.json"},
        "htmlReporter": {"fileName": f"{directory}/mutation.html"},
        "tempDirName": f"{directory}/sandbox",
        "cleanTempDir": True,
        "incremental": False,
        "incrementalFile": f"{directory}/incremental.json",
        "fileLogLevel": "off",
        "dryRunOnly": False,
    }
    base = "const base = {};"
    for name in STRYKER_CONFIG_NAMES:
        path = root / name
        if path.is_file():
            if name.endswith(".json"):
                base = f"const base = {json.dumps(load_jsonc(path))};"
            else:
                base = f"import * as loaded from {json.dumps(path.resolve().as_uri())};\nconst base = loaded.default ?? loaded;"
            break
    return (
        "// Written by /testing-audit --with-mutation for one run; safe to delete.\n"
        f"{base}\n"
        f"const overrides = {json.dumps(overrides)};\n"
        "export default { ...base, ...overrides, thresholds: { ...(base.thresholds ?? {}), break: null } };\n"
    )


def mutation_score(report: dict[str, Any], root: Path) -> tuple[float | None, Counter[str], list[tuple[str, int, str]]]:
    statuses: Counter[str] = Counter()
    survivors: list[tuple[str, int, str]] = []
    for path, file in (report.get("files") or {}).items():
        name = relative(root, Path(path)) if Path(path).is_absolute() else Path(path).as_posix()
        for mutant in file.get("mutants") or []:
            status = mutant.get("status")
            statuses[status] += 1
            if status in ("Survived", "NoCoverage"):
                line = ((mutant.get("location") or {}).get("start") or {}).get("line")
                if isinstance(line, int):
                    survivors.append((name, line, f"{mutant.get('mutatorName', 'a')} mutant {'survived' if status == 'Survived' else 'ran no test'}"))
    survivors.sort(key=lambda item: 0 if item[2].endswith("survived") else 1)  # survived mutants before uncovered ones
    detected = statuses["Killed"] + statuses["Timeout"]
    undetected = statuses["Survived"] + statuses["NoCoverage"]
    if detected + undetected == 0:
        return None, statuses, survivors
    return round(100 * detected / (detected + undetected), 1), statuses, survivors


Runner = Callable[..., Any]


def mutation_budget(elapsed: float) -> int:
    """Seconds Stryker may run so the whole collector finishes inside the protocol's timeout."""
    return int(min(MUTATION_TIMEOUT_CAP_SECONDS, PROTOCOL_COLLECTOR_TIMEOUT_SECONDS - MUTATION_MARGIN_SECONDS - elapsed))


def failure_line(output: str) -> str:
    """The line most likely to explain a failed run: the first one mentioning ERROR, else the last."""
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    flagged = [line for line in lines if "ERROR" in line]
    return (flagged or lines[-1:] or [""])[0][:200]


def mutation_check(root: Path, requested: bool, targets: list[str], run: Runner = subprocess.run, budget: int = MUTATION_TIMEOUT_CAP_SECONDS) -> dict[str, Any]:
    if not requested:
        return {"evaluationState": "not-evaluated", "reason": "Mutation testing runs only with --with-mutation."}
    binary = stryker_binary(root)
    if binary is None:
        return {
            "evaluationState": "not-evaluated",
            "reason": "--with-mutation was requested, but Stryker is not installed (no node_modules/.bin/stryker); install @stryker-mutator/core and re-run.",
        }
    if not targets:
        return {"evaluationState": "not-evaluated", "reason": "--with-mutation was requested, but no source files were found to mutate."}
    if budget < MUTATION_MINIMUM_SECONDS:
        return {"evaluationState": "not-evaluated", "reason": f"The scan left only {max(budget, 0)} seconds of the protocol's {PROTOCOL_COLLECTOR_TIMEOUT_SECONDS}-second collector limit, too little for Stryker."}
    directory = root / MUTATION_DIRECTORY
    directory.mkdir(parents=True, exist_ok=True)
    report_path = directory / "mutation.json"
    if report_path.exists():
        report_path.unlink()
    config_path = directory / "stryker.audit.config.mjs"
    config_path.write_text(mutation_configuration(root, targets), encoding="utf-8")
    command = f"stryker run {MUTATION_DIRECTORY.as_posix()}/stryker.audit.config.mjs"
    try:
        completed = run(
            [str(binary), "run", str(config_path)],
            cwd=str(root), capture_output=True, text=True, encoding="utf-8", errors="replace",
            check=False, timeout=budget,
        )
    except subprocess.TimeoutExpired:
        return {"evaluationState": "not-evaluated", "reason": f"Stryker did not finish on the {len(targets)} riskiest files within {budget} seconds."}
    except OSError as error:
        return {"evaluationState": "not-evaluated", "reason": f"Stryker failed to start: {type(error).__name__}."}
    finally:
        config_path.unlink(missing_ok=True)
    if not report_path.is_file():
        line = failure_line(completed.stderr or completed.stdout or "")
        detail = f": {line}" if line else ""
        return {"evaluationState": "not-evaluated", "reason": f"Stryker exited {completed.returncode} without a report{detail}."}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"evaluationState": "not-evaluated", "reason": "Stryker's JSON report could not be read."}
    score, statuses, survivors = mutation_score(report, root)
    if score is None:
        return {"evaluationState": "not-evaluated", "reason": "Stryker produced no testable mutants in the riskiest files."}
    summary = (
        f"command: `{command}` → mutation score {score}% on {len(targets)} files "
        f"({statuses['Killed'] + statuses['Timeout']} detected, {statuses['Survived']} survived, {statuses['NoCoverage']} without coverage)"
    )
    evidence = [summary] + [cite(path, line, note=note) for path, line, note in survivors[:8] if (root / path).is_file()]
    if score >= 80:
        return {"status": "present", "evidence": evidence, "evidenceTier": "direct"}
    return {
        "status": "partial" if score >= 60 else "violation",
        "evidence": evidence,
        "evidenceTier": "direct",
        "gap": f"Only {score}% of the mutants Stryker made in the riskiest files made a test fail; the survivors are behaviour no test pins down.",
        "remediation": "For each surviving mutant, add or strengthen a test whose Given would produce a different result under that mutant, starting with the most-changed file.",
        "judgement": "act-on" if score < 60 else "consider",
    }


# ---------------------------------------------------------------------------
# Collection


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def collect(root: Path, months: int = 6, with_mutation: bool = False, run: Runner = subprocess.run) -> dict[str, Any]:
    started = time.monotonic()
    files = listed_files(root)
    manifests = Manifests(root)
    source_paths = [path for path in files if path.endswith(SOURCE_SUFFIXES) and not path.endswith(".d.ts")]
    large = [path for path in source_paths if file_size(root / path) > MAX_FILE_BYTES]
    checks: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}

    texts: dict[str, str] = {path: read_text(root / path) for path in source_paths}
    comment_masked = {path: mask(text, strings=False) for path, text in texts.items()}
    fully_masked: dict[str, str] = {}
    test_paths: list[str] = []
    for path in source_paths:
        imports_test_api = bool(TEST_API_IMPORT.search(comment_masked[path]))
        if (imports_test_api or TEST_NAME_PATTERN.search(path) or LOOSE_TEST_NAME_PATTERN.search(path) or TEST_DIRECTORY_PATTERN.search(path)
                or "/__tests__/" in f"/{path}" or STORY_PATTERN.search(path)):
            fully_masked[path] = mask(texts[path], strings=True)
        if is_test_file(path, fully_masked.get(path), imports_test_api):
            test_paths.append(path)
    stories = [path for path in source_paths if STORY_PATTERN.search(path)]
    interaction_stories = [path for path in stories if re.search(r"\bplay\s*[:=(]|\bplay\s*\(", fully_masked[path])]
    test_set = set(test_paths)
    package_roots = [relative(root, directory) for directory in manifests.directories[1:]]
    product = [
        path for path in source_paths
        if path not in test_set and not STORY_PATTERN.search(path) and not NON_SOURCE_PATTERN.search(path)
        and not TOOLING_PATTERN.search(path) and not in_tooling_folder(path, package_roots)
    ]

    if not product and not test_paths and not interaction_stories:
        reason = (
            "No JavaScript or TypeScript product code or tests were found"
            + (f"; the {len(source_paths)} script file(s) present are configuration or tooling" if source_paths else "")
            + ". Record the whole audit with `audit-not-applicable`."
        )
        for check in ALL_CHECKS:
            checks[check] = {"applicability": "not-applicable", "reason": reason}
        snapshot["projectDetected"] = False
        snapshot["toolingFiles"] = source_paths[:20]
        return {"checks": checks, "snapshot": snapshot}

    resolver = Resolver(root, set(source_paths), manifests.directories[1:])

    def product_module(importer: str) -> Callable[[str], bool]:
        return lambda specifier: specifier.startswith(".") or resolver.resolve(importer, specifier) is not None

    # Test kinds, imports, and the pre-filter.
    test_imports: Counter[str] = Counter()
    kinds: Counter[str] = Counter()
    candidates: list[TestBlock] = []
    flag_counts: Counter[str] = Counter()
    tests_counted = 0
    focused: list[str] = []
    skipped: list[str] = []
    file_imports: dict[str, set[str]] = {}
    module_mocks = {"internal": 0, "external": 0, "internalExamples": []}
    flake: Counter[str] = Counter()
    flake_examples: list[str] = []
    queries: Counter[str] = Counter()
    interactions: Counter[str] = Counter()
    msw_files: list[str] = []
    duplicate_budget = ComparisonBudget()
    for path in test_paths:
        text = texts[path]
        masked = fully_masked[path]
        specifiers = {specifier for specifier, _ in imports(comment_masked[path])}
        file_imports[path] = specifiers
        for specifier in specifiers:
            test_imports[specifier] += 1
        kind = test_kind(path, specifiers, masked)
        kinds[kind] += 1
        file_focused, file_skipped = focus_and_skips(path, text, masked)
        focused += file_focused
        skipped += file_skipped
        blocks, counts = prefilter(path, text, masked, product_module(path), web_first=kind in ("end-to-end", "playwright-component"), budget=duplicate_budget)
        tests_counted += len(blocks)
        flag_counts.update(counts)
        candidates += [block for block in blocks if block.flags]
        for match in re.finditer(r"(?<![\w$.])(?:vi|jest)\s*\.\s*(?:mock|doMock|unstable_mockModule)\s*\(\s*(['\"])([^'\"]+)\1", comment_masked[path]):
            specifier = match.group(2)
            internal = specifier.startswith((".", "@/", "~/", "#")) or resolver.resolve(path, specifier) is not None
            module_mocks["internal" if internal else "external"] += 1
            if internal and len(module_mocks["internalExamples"]) < 10:
                module_mocks["internalExamples"].append(cite(path, line_of(comment_masked[path], match.start()), match.group(0)))
        for label, pattern in (
            ("fixedWaits", r"(?<![\w$.])(?:setTimeout\s*\([^,()]*(?:\([^()]*\))?[^,()]*,\s*\d{2,}|sleep\s*\(\s*\d|delay\s*\(\s*\d|(?:page|cy)\s*\.\s*(?:waitForTimeout|wait)\s*\(\s*\d)"),
            ("realClock", r"(?<![\w$.])(?:Date\s*\.\s*now\s*\(|new\s+Date\s*\(\s*\))"),
            ("randomness", r"(?<![\w$.])Math\s*\.\s*random\s*\("),
        ):
            for match in re.finditer(pattern, masked):
                if label == "realClock" and re.search(r"useFakeTimers|setSystemTime|clock\s*\.\s*install", masked):
                    break
                flake[label] += 1
                if len(flake_examples) < 10:
                    number = line_of(masked, match.start())
                    flake_examples.append(cite(path, number, fragment(line_text(text, number)) or None))
        for match in re.finditer(r"(?<![\w$])(?:get|query|find)(?:All)?By(Role|LabelText|PlaceholderText|Text|DisplayValue|AltText|Title|TestId)\s*\(", masked):
            queries[match.group(1)] += 1
        queries["containerQuerySelector"] += len(re.findall(r"\b(?:container|document)\s*\.\s*querySelector(?:All)?\s*\(", masked))
        interactions["fireEvent"] += len(re.findall(r"(?<![\w$.])fireEvent\s*(?:\.\s*\w+\s*)?\(", masked))
        interactions["userEvent"] += len(re.findall(r"(?<![\w$.])(?:userEvent|user)\s*\.\s*(?:click|type|keyboard|clear|selectOptions|hover|tab|upload|paste|dblClick|tripleClick|pointer)\s*\(", masked))
        if re.search(r"\b(?:setupServer|setupWorker)\s*\(", masked) or "msw" in specifiers or "msw/node" in specifiers:
            msw_files.append(path)
    for path in source_paths:
        if path not in test_paths and re.search(r"\b(?:setupServer|setupWorker)\s*\(", comment_masked[path]):
            msw_files.append(path)

    # Import graph for fan-in, and which tests reach which modules.
    fan_in: Counter[str] = Counter()
    tested_by: dict[str, set[str]] = {}
    for path in source_paths:
        for specifier, _ in imports(comment_masked[path]):
            target = resolver.resolve(path, specifier)
            if not target or target == path:
                continue
            if path in test_set or TEST_SUPPORT_PATTERN.search(path):
                tested_by.setdefault(target, set()).add(path)
            else:
                fan_in[target] += 1
    production = product
    commits = churn(root, months)
    ranked = sorted(
        production,
        key=lambda path: (-(commits.get(path, 0) + fan_in.get(path, 0)), -commits.get(path, 0), path),
    )
    ranking = [
        {"path": path, "commits": commits.get(path, 0), "fanIn": fan_in.get(path, 0), "importedByTests": sorted(tested_by.get(path, set()))[:5]}
        for path in ranked[:15]
        if commits.get(path, 0) + fan_in.get(path, 0) > 0
    ]
    risk_of = {item["path"]: index for index, item in enumerate(ranking)}

    def candidate_rank(block: TestBlock) -> tuple[int, str, int]:
        reached = [resolver.resolve(block.path, specifier) for specifier in file_imports.get(block.path, set())]
        best = min((risk_of[target] for target in reached if target in risk_of), default=len(risk_of) + 1)
        return best, block.path, block.line

    candidates.sort(key=candidate_rank)

    # Snapshot facts.
    ui = manifests.declared(UI_PACKAGES)
    testing_library = manifests.declared(TESTING_LIBRARY_PACKAGES)
    databases = manifests.declared(DATABASE_PACKAGES)
    workflows = continuous_integration_files(root, files)
    ci_runs = continuous_integration_test_runs(root, files)
    runners = runner_facts(manifests, files, test_imports)
    script_runs = scripts_running_tests(manifests)
    unrecognised = unrecognised_test_scripts(manifests)
    snapshot["projectDetected"] = True
    if large:
        snapshot["skippedLargeFiles"] = {"limitBytes": MAX_FILE_BYTES, "count": len(large), "examples": large[:10]}
    snapshot["runners"] = sorted(runners)
    snapshot["testFiles"] = {"total": len(test_paths), "byKind": dict(sorted(kinds.items())), "storiesWithPlayFunctions": len(interaction_stories)}
    snapshot["testsFound"] = tests_counted
    snapshot["userInterfaceFrameworks"] = ui
    snapshot["testingLibraryPackages"] = testing_library
    snapshot["databaseClients"] = databases
    snapshot["integrationTooling"] = manifests.declared(INTEGRATION_PACKAGES)
    snapshot["firstClassTooling"] = {
        "vitestBrowserMode": bool(manifests.declared(("@vitest/browser", "@vitest/browser-playwright", "@vitest/browser-webdriverio", "vitest-browser-react", "vitest-browser-vue", "vitest-browser-svelte"))) or kinds.get("vitest-browser", 0) > 0,
        "playwrightComponentTests": kinds.get("playwright-component", 0) > 0 or any(manifests.dependency(name) for name in ("@playwright/experimental-ct-react", "@playwright/experimental-ct-vue", "@playwright/experimental-ct-svelte")),
        "storybookInteractionTests": len(interaction_stories),
        "mockServiceWorker": sorted(set(msw_files))[:10],
        "testcontainers": bool(manifests.declared(("testcontainers", "@testcontainers/postgresql", "@testcontainers/mysql", "@testcontainers/mongodb", "@testcontainers/redis"))),
    }
    snapshot["scriptsRunningTests"] = [f"{path} {name}: {command}" for path, name, command in script_runs][:10]
    snapshot["continuousIntegration"] = {"files": workflows, "testRuns": ci_runs[:10]}
    snapshot["moduleMocks"] = module_mocks
    snapshot["flakeSignals"] = {**dict(flake), "examples": flake_examples}
    if ui or testing_library:
        snapshot["queryUsage"] = dict(queries)
        snapshot["interactions"] = dict(interactions)
    snapshot["riskRanking"] = ranking
    snapshot["historyWindowMonths"] = months
    snapshot["untestedRiskyFiles"] = [item["path"] for item in ranking if not item["importedByTests"]][:10]
    snapshot["preFilter"] = {
        "testsScanned": tests_counted,
        "flaggedTests": len(candidates),
        "byFlag": dict(sorted(flag_counts.items())),
        "candidates": [
            {"path": block.path, "line": block.line, "title": (block.title or "")[:120], "flags": [flag for flag, _ in block.flags], "lines": sorted({line for _, line in block.flags})}
            for block in candidates[:MAX_CANDIDATES]
        ],
    }
    mutation_targets = [item["path"] for item in ranking[:MUTATION_TARGETS]]
    snapshot["mutationTargets"] = mutation_targets

    # Runner check.
    if unrecognised and (not test_paths or not runners or not script_runs):
        # The wrapper may run the suite correctly, so the collector cannot call the runner missing or partial.
        path, command = unrecognised[0]
        checks[RUNNER] = {
            "evaluationState": "not-evaluated",
            "reason": f"The `test` script in {path} runs `{command[:80]}`, which the collector does not recognise as a test runner"
            + (f", and it found {len(test_paths)} test files" if test_paths else ", and it found no test files it recognises")
            + (f" and the runner(s) {', '.join(sorted(runners))}" if runners else "")
            + "; check by hand whether that command runs tests.",
        }
    elif not test_paths and not interaction_stories:
        checks[RUNNER] = {
            "status": "missing",
            "evidence": [f"command: `collect.py test-file scan` → 0 test files among {len(source_paths)} source files"] + [entry for values in runners.values() for entry in values][:2],
            "evidenceTier": "direct",
            "gap": "The repository has no tests, so nothing fails when its behaviour breaks.",
            "remediation": "Add Vitest (or node:test for a small library), then write the first tests for the riskiest module in the snapshot's risk ranking, through its public interface.",
            "judgement": "act-on",
        }
    elif not runners:
        checks[RUNNER] = {
            "status": "missing",
            "evidence": [f"command: `collect.py runner detection` → {len(test_paths)} test files but no recognised runner in dependencies, configuration, imports, or scripts"] + [cite_file(path) for path in test_paths[:2]],
            "evidenceTier": "direct",
            "gap": f"{len(test_paths)} test files exist, but no test runner is declared, so they cannot run.",
            "remediation": "Declare the runner the tests are written for as a development dependency and add a `test` script that runs it.",
            "judgement": "act-on",
        }
    elif not script_runs:
        checks[RUNNER] = {
            "status": "partial",
            "evidence": [entry for values in runners.values() for entry in values][:3] + ["command: `collect.py script scan` → no package script runs a test runner"],
            "evidenceTier": "direct",
            "gap": "A runner and tests exist, but no package script runs them, so contributors and continuous integration have no standard way to run the suite.",
            "remediation": "Add a `test` script that runs the suite once without watching, such as `vitest run`, and call it from continuous integration.",
            "judgement": "act-on",
        }
    else:
        evidence = [entry for values in runners.values() for entry in values][:3]
        evidence += [cite_file(path, command) for path, _, command in script_runs[:2]]
        checks[RUNNER] = {"status": "present", "evidence": list(dict.fromkeys(evidence)), "evidenceTier": "direct"}

    # Focused and skipped tests.
    if not test_paths:
        checks[FOCUSED] = {"applicability": "not-applicable", "reason": "The repository has no test files."}
    elif focused:
        checks[FOCUSED] = {
            "status": "violation",
            "evidence": focused[:8] + skipped[:4],
            "evidenceTier": "direct",
            "gap": f"{len(focused)} focused test(s) are committed, so the runner skips the other tests in their files"
            + (f", and {len(skipped)} test(s) are skipped or left as to-do" if skipped else "") + ".",
            "remediation": "Remove every `.only`, `fit`, and `fdescribe`, and make continuous integration refuse them (Vitest refuses them in CI by default; set `forbidOnly: !!process.env.CI` in Playwright, or enable `vitest/no-focused-tests` or `jest/no-focused-tests`).",
            "judgement": "act-on",
        }
    elif skipped:
        checks[FOCUSED] = {
            "status": "partial",
            "evidence": skipped[:10],
            "evidenceTier": "direct",
            "gap": f"{len(skipped)} test(s) are skipped or left as to-do, so the behaviour they describe is unchecked.",
            "remediation": "Fix or delete each skipped test, and track deliberate gaps in the issue tracker instead of in the suite.",
            "judgement": "consider",
        }
    else:
        checks[FOCUSED] = {
            "status": "present",
            "evidence": [f"command: `collect.py focus scan` → 0 focused and 0 skipped tests in {len(test_paths)} test files (comments and strings masked)"],
            "evidenceTier": "direct",
        }
    if large and "status" in checks.get(FOCUSED, {}):
        checks[FOCUSED]["degradedReason"] = (
            f"{len(large)} source file(s) over {MAX_FILE_BYTES // 1_000_000} MB were not read, so a focused or skipped test in them would be missed: "
            + ", ".join(large[:3])
        )
        checks[FOCUSED]["evidenceTier"] = "supported"

    # Coverage thresholds.
    coverage = coverage_facts(root, files, manifests, workflows)
    snapshot["coverage"] = {"thresholds": coverage["thresholds"][:4], "collectedBy": coverage["enabled"][:4]}
    unit_runners = UNIT_RUNNERS & set(runners)
    # Jasmine run by Karma takes its floor from karma.conf.js, which is not read either.
    unread = FLOOR_UNREAD_RUNNERS | ({"jasmine"} if unit_runners & {"karma", "angular-cli"} else set())
    if not unit_runners:
        checks[COVERAGE] = {"applicability": "not-applicable", "reason": "No unit or integration test runner (such as Vitest, Jest, node:test, Bun, Karma, Mocha, AVA, tap, or uvu) was found."}
    elif not coverage["thresholds"] and unit_runners <= unread:
        checks[COVERAGE] = {
            "evaluationState": "not-evaluated",
            "reason": f"The unit test runner(s) found ({', '.join(sorted(unit_runners))}) set coverage floors in settings the collector does not read"
            " (`coverageThreshold` in bunfig.toml, `coverageReporter.check` in karma.conf.js, tap's defaults and .taprc), and no Vitest, Jest, c8, nyc,"
            " or node:test floor was found; check those settings by hand.",
        }
    elif not coverage["thresholds"]:
        config_files = [path for path in files if re.search(r"(^|/)(?:vitest|vite|jest)\.config\.[cm]?[jt]s(?:on)?$", path)]
        checks[COVERAGE] = {
            "status": "missing",
            "evidence": [f"command: `collect.py coverage scan` → no coverage thresholds in runner configuration, package manifests, or scripts"] + [cite_file(path) for path in config_files[:2]],
            "evidenceTier": "direct",
            "gap": "No coverage threshold is configured, so untested new code lowers coverage without failing anything.",
            "remediation": "Set `coverage.thresholds` (Vitest) or `coverageThreshold` (Jest) at the current level, collect coverage in the script continuous integration runs, and raise the floor as tests are added.",
            "judgement": "consider",
        }
    elif not coverage["enabled"]:
        checks[COVERAGE] = {
            "status": "partial",
            "evidence": coverage["thresholds"][:3] + ["command: `collect.py coverage scan` → no configuration or script collects coverage"],
            "evidenceTier": "direct",
            "gap": "Coverage thresholds are configured, but no configuration or script collects coverage, so they never fail a run.",
            "remediation": "Collect coverage in the test script continuous integration runs (`vitest run --coverage`, `jest --coverage`), or set `coverage.enabled: true`.",
            "judgement": "consider",
        }
    else:
        checks[COVERAGE] = {"status": "present", "evidence": coverage["thresholds"][:2] + coverage["enabled"][:2], "evidenceTier": "direct"}

    # Lint rules.
    component_tests = [path for path in test_paths if any(specifier.startswith("@testing-library/") for specifier in file_imports.get(path, set()))]
    samples = (component_tests or test_paths or ["src/example.test.tsx"])[:20]
    eslint = eslint_configuration(root, files, manifests, samples)
    snapshot["eslint"] = {
        "configurationFiles": eslint["files"],
        "checkedAgainst": samples[:5],
        "unresolved": eslint["unresolved"][:5],
        "testingLibrary": {key: eslint["testing-library"][key] for key in ("presets", "effective")},
        "jestDom": {key: eslint["jest-dom"][key] for key in ("presets", "effective")},
    }
    if not testing_library:
        checks[TESTING_LIBRARY_LINT] = {"applicability": "not-applicable", "reason": "Testing Library is not a dependency, so its lint rules have nothing to check."}
    else:
        checks[TESTING_LIBRARY_LINT] = lint_check("testing-library", eslint, manifests)
    if not manifests.dependency("@testing-library/jest-dom"):
        checks[JEST_DOM_LINT] = {"applicability": "not-applicable", "reason": "@testing-library/jest-dom is not a dependency."}
    else:
        checks[JEST_DOM_LINT] = lint_check("jest-dom", eslint, manifests)

    # Applicability of the model checks.
    if not test_paths:
        for check in (FAILS, MOCKS, DETERMINISTIC, TITLES):
            checks[check] = {"applicability": "not-applicable", "reason": "The repository has no test files; tests-run-under-configured-runner records the gap."}
    if not ui:
        checks[COMPONENTS] = {"applicability": "not-applicable", "reason": "No user-interface framework is a dependency."}
    if not databases:
        checks[DATABASE] = {"applicability": "not-applicable", "reason": "No database client or ORM is a dependency."}

    checks[MUTATION] = mutation_check(root, with_mutation, mutation_targets, run, mutation_budget(time.monotonic() - started))
    return {"checks": checks, "snapshot": snapshot}


def history_months(thresholds: list[str], default: int) -> int:
    months = default
    for item in thresholds:
        key, _, value = item.partition("=")
        if key.strip() == "months":
            try:
                months = int(value)
            except ValueError as error:
                raise SystemExit(f"collect.py: --threshold months must be a whole number, not {value!r}") from error
            if months < 1:
                raise SystemExit("collect.py: --threshold months must be at least 1")
    return months


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--repository", default=".")
    parser.add_argument("--enrichment", action="append", default=[], help="a recorded enrichment flag; --with-mutation runs Stryker")
    parser.add_argument("--threshold", action="append", default=[], help="a recorded key=value override; months=<n> widens the history window")
    arguments = parser.parse_args(argv)
    root = Path(arguments.repository).resolve()
    with_mutation = any(flag.lstrip("-") == "with-mutation" for flag in arguments.enrichment)
    result = collect(root, history_months(arguments.threshold, 6), with_mutation)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
