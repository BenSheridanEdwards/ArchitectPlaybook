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
import subprocess
import sys
from collections import Counter
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
MAX_CANDIDATES = 40
MUTATION_TARGETS = 5
MUTATION_TIMEOUT_SECONDS = 480
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
TEST_DIRECTORY_PATTERN = re.compile(r"(^|/)(__tests__|tests?|e2e|cypress|playwright|integration)/")
STORY_PATTERN = re.compile(r"\.stories\.[cm]?[jt]sx?$")
NON_SOURCE_PATTERN = re.compile(
    r"(^|/)(__tests__|__mocks__|tests?|e2e|cypress|playwright|fixtures?|mocks?|scripts|\.storybook|stories)/"
    r"|\.(test|spec|cy|e2e|stories|d)\.[cm]?[jt]sx?$|(^|/)[^/]*\.config\.[cm]?[jt]s$|(^|/)[^/]*\.setup\.[cm]?[jt]sx?$"
)
TEST_SUPPORT_PATTERN = re.compile(r"(^|/)(__tests__|__mocks__|tests?|fixtures?|mocks?)/")
TEST_API_PATTERN = re.compile(r"(?<![\w$.])(?:describe|it|test)\s*\(")
IMPORT_PATTERN = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(\s*|\brequire\s*\(\s*|^[ \t]*import\s*)(['"])(?P<specifier>[^'"\n]+)\1""",
    re.M,
)
RUNNER_COMMANDS = {
    "vitest": re.compile(r"(?<![\w-])vitest\b"),
    "jest": re.compile(r"(?<![\w-])jest\b(?![\w-])"),
    "node:test": re.compile(r"\b(?:node|tsx|bun)\b[^&|;\n]*\s--test\b"),
    "playwright": re.compile(r"\bplaywright\s+test\b"),
    "cypress": re.compile(r"\bcypress\s+run\b"),
    "storybook-test-runner": re.compile(r"\btest-storybook\b"),
    "mocha": re.compile(r"(?<![\w-])mocha\b"),
    "ava": re.compile(r"(?<![\w-])ava\b(?![\w-])"),
}
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


def line_of(text: str, index: int) -> int:
    return text.count("\n", 0, index) + 1


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
    for pattern in globs:
        if not isinstance(pattern, str) or pattern.startswith("!"):
            continue
        for directory in sorted(root.glob(pattern)):
            if (directory / "package.json").is_file() and "node_modules" not in directory.parts:
                found.append(directory.resolve())
    return sorted(set(found))


class Manifests:
    """Every package.json in the root and its workspaces."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.directories = [root.resolve(), *workspaces(root)]
        self.packages: list[tuple[str, dict[str, Any]]] = []
        for directory in self.directories:
            path = directory / "package.json"
            if path.is_file():
                self.packages.append((relative(root, path), load_jsonc(path)))

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


def is_test_file(path: str, masked: str | None) -> bool:
    if not path.endswith(SOURCE_SUFFIXES) or path.endswith(".d.ts"):
        return False
    if TEST_NAME_PATTERN.search(path) or "/__tests__/" in f"/{path}":
        return True
    if TEST_DIRECTORY_PATTERN.search(path) and masked is not None:
        return bool(TEST_API_PATTERN.search(masked))
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
    r"\.\s*should\s*\(|\bcy\s*\.\s*contains\s*\(|(?<![\w$.])(?:(?:screen|canvas)\s*\.\s*)?(?:get|find)(?:All)?By[A-Z]\w*\s*\("
    r"|\bexpect\s*\.\s*(?:assertions|hasAssertions)\s*\(|\bt\s*\.\s*(?:is|not|deepEqual|true|false|truthy|falsy|throws|snapshot|plan)\s*\("
)
MOCK_MATCHERS = {
    "toHaveBeenCalled", "toHaveBeenCalledWith", "toHaveBeenCalledTimes", "toHaveBeenLastCalledWith",
    "toHaveBeenNthCalledWith", "toBeCalled", "toBeCalledWith", "toBeCalledTimes", "lastCalledWith",
    "nthCalledWith", "toHaveReturned", "toHaveReturnedWith", "toHaveReturnedTimes", "toHaveLastReturnedWith",
    "toHaveNthReturnedWith", "toHaveBeenCalledOnce", "toHaveBeenCalledExactlyOnceWith",
}
SNAPSHOT_MATCHERS = {
    "toMatchSnapshot", "toMatchInlineSnapshot", "toMatchFileSnapshot", "toThrowErrorMatchingSnapshot",
    "toThrowErrorMatchingInlineSnapshot",
}
EQUALITY_MATCHERS = {"toBe", "toEqual", "toStrictEqual", "toMatchObject"}
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


def guarded(masked: str, body_start: int, position: int) -> bool:
    """Whether the assertion at `position` runs only on some paths: in an if, loop, callback, or catch."""
    statement_start = max(masked.rfind(";", body_start, position), masked.rfind("{", body_start, position), masked.rfind("}", body_start, position))
    if GUARD_STATEMENT.search(masked[statement_start + 1:position]):
        return True
    openers: list[int] = []
    for index in range(body_start, position):
        character = masked[index]
        if character in "({[":
            openers.append(index)
        elif character in ")}]" and openers:
            openers.pop()
    for opener in openers:
        before = masked[max(body_start, opener - 120):opener]
        if masked[opener] == "{" and GUARD_BEFORE_BRACE.search(before):
            return True
        if masked[opener] == "(" and GUARD_BEFORE_PARENTHESIS.search(before):
            return True
        if masked[opener] == "{" and re.search(r"=>\s*$", before):
            # An arrow function body: guarded when the arrow is an argument to a guarding call.
            arrow_start = masked.rfind("(", body_start, opener)
            if arrow_start != -1 and GUARD_BEFORE_PARENTHESIS.search(masked[max(body_start, arrow_start - 60):arrow_start]):
                return True
    return False


def awaited(masked: str, body_start: int, position: int) -> bool:
    statement_start = max(masked.rfind(";", body_start, position), masked.rfind("{", body_start, position), masked.rfind("}", body_start, position), masked.rfind("\n", body_start, position))
    return bool(re.search(r"\b(?:await|return)\b", masked[statement_start + 1:position])) or bool(
        re.search(r"=>\s*$", masked[body_start:position])
    )


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
                return self.matcher not in ("toBeUndefined", "toBeFalsy")
            if self.matcher in ("toBeUndefined", "toBeFalsy"):
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


def product_derived(expression: str, text: str, masked: str, start: int, end: int, names: set[str]) -> bool:
    """Whether an expression is, or names a variable set to, a call into the code under test."""
    if not names:
        return False
    call = re.compile(rf"^\s*(?:await\s+)?(?:new\s+)?(?:{'|'.join(re.escape(name) for name in sorted(names))})\b[\w$.]*\s*\(")
    expression = expression.strip()
    if call.match(expression):
        return True
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", expression):
        return False
    for scope_start, scope_end in ((start, end), (0, len(masked))):
        declaration = re.search(rf"\b(?:const|let|var)\s+{re.escape(expression)}\s*(?::[^=;]+)?=(?!=)", masked[scope_start:scope_end])
        if declaration:
            initializer_start = scope_start + declaration.end()
            initializer_end = masked.find("\n", initializer_start)
            return bool(call.match(text[initializer_start:initializer_end if initializer_end != -1 else len(text)]))
    return False


def assertions_in(text: str, masked: str, start: int, end: int, web_first: bool = False, names: set[str] | None = None) -> list[Assertion]:
    found: list[Assertion] = []
    for match in EXPECT_CALL.finditer(masked, start, end):
        opening = match.end() - 1
        closing = matching(masked, opening)
        if closing == -1 or closing > end:
            continue
        subject = text[opening + 1:closing]
        chain = re.match(r"(?P<chain>(?:\s*\.\s*(?:not|resolves|rejects))*)\s*\.\s*(?P<matcher>\w+)\s*\(", masked[closing + 1:end])
        matcher = ""
        negated = False
        expected = None
        is_async = False
        if chain:
            matcher = chain.group("matcher")
            negated = "not" in chain.group("chain")
            is_async = bool(re.search(r"resolves|rejects", chain.group("chain")))
            argument_open = closing + 1 + chain.end() - 1
            argument_close = matching(masked, argument_open)
            if argument_close != -1:
                spans = split_arguments(masked, argument_open + 1, argument_close)
                expected = text[spans[0][0]:spans[0][1]] if spans else None
        line = line_of(masked, match.start())
        asynchronous = is_async or "poll" in match.group(0) or (web_first and matcher in WEB_FIRST_MATCHERS)
        assertion = Assertion(
            line, "expect", matcher, negated, subject, expected,
            guarded(masked, start, match.start()),
            asynchronous and not awaited(masked, start, match.start()),
        )
        assertion.expected_from_product = bool(expected) and product_derived(expected, text, masked, start, end, names or set())
        found.append(assertion)
    for match in ASSERT_CALL.finditer(masked, start, end):
        opening = match.end() - 1
        closing = matching(masked, opening)
        if closing == -1 or closing > end:
            continue
        spans = split_arguments(masked, opening + 1, closing)
        subject = text[spans[0][0]:spans[0][1]] if spans else ""
        expected = text[spans[1][0]:spans[1][1]] if len(spans) > 1 else None
        method = match.group("method") or "ok"
        is_async = method in ("rejects", "doesNotReject")
        assertion = Assertion(
            line_of(masked, match.start()), "assert", method, method.startswith("not") or method.startswith("doesNot"),
            subject, expected, guarded(masked, start, match.start()),
            is_async and not awaited(masked, start, match.start()),
        )
        assertion.expected_from_product = bool(expected) and product_derived(expected, text, masked, start, end, names or set())
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


def prefilter(
    path: str, text: str, masked: str, product: Callable[[str], bool] = lambda specifier: specifier.startswith("."),
    web_first: bool = False,
) -> tuple[list[TestBlock], dict[str, int]]:
    """Flag tests whose shape suggests they cannot fail. A flag is a place to look, not a verdict.

    `product` says whether an import specifier names the repository's own code.
    """
    blocks = test_blocks(path, text, masked)
    names = imported_bindings(mask(text, strings=False), product)
    helpers = assertion_helpers(masked, names)
    fed = mock_values(text, masked)
    counts: Counter[str] = Counter()
    equality = EQUALITY_MATCHERS | {"equal", "strictEqual", "deepEqual", "deepStrictEqual"}
    for block in blocks:
        segment = masked[block.start:block.end]
        found = assertions_in(text, masked, block.start, block.end, web_first, names)
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
    near_duplicates(blocks, counts)
    return blocks, dict(counts)


def near_duplicates(blocks: list[TestBlock], counts: Counter[str]) -> None:
    """Flag tests that make the same assertions on nearly the same setup: one of them checks nothing new."""
    bodies = []
    asserted = []
    for block in blocks:
        body = block.body
        if block.title is not None:
            body = body.replace(block.title, "", 1)
        bodies.append(re.sub(r"\s+", " ", body).strip())
        asserted.append(sorted(re.sub(r"\s+", "", line) for line in body.split("\n") if re.search(r"\bexpect\b|\bassert\b", line)))
    for left in range(len(blocks)):
        if len(bodies[left]) < 60:
            continue
        for right in range(left + 1, min(len(blocks), left + 200)):
            if len(bodies[right]) < 60 or "each" in blocks[left].modifiers or "each" in blocks[right].modifiers:
                continue
            if not asserted[left] or asserted[left] != asserted[right]:
                continue
            matcher = difflib.SequenceMatcher(None, bodies[left], bodies[right], autojunk=False)
            if matcher.real_quick_ratio() < 0.9 or matcher.quick_ratio() < 0.9 or matcher.ratio() < 0.9:
                continue
            for block in (blocks[left], blocks[right]):
                if not any(flag == "near-duplicate" for flag, _ in block.flags):
                    block.flags.append(("near-duplicate", block.line))
                    counts["near-duplicate"] += 1


# ---------------------------------------------------------------------------
# Import graph, churn, and risk


class Resolver:
    """Resolve relative imports and the root tsconfig's path aliases to repository files."""

    def __init__(self, root: Path, files: set[str]) -> None:
        self.root = root
        self.files = files
        self.aliases: list[tuple[str, list[str]]] = []
        for name in ("tsconfig.json", "jsconfig.json"):
            options = load_jsonc(root / name).get("compilerOptions") or {}
            base = str(options.get("baseUrl") or ".")
            paths = options.get("paths")
            if isinstance(paths, dict):
                for pattern, targets in paths.items():
                    if isinstance(targets, list):
                        self.aliases.append((pattern, [str(Path(base) / target) for target in targets if isinstance(target, str)]))

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

    def resolve(self, importer: str, specifier: str) -> str | None:
        if specifier.startswith("."):
            return self._first(str(Path(importer).parent / specifier))
        for pattern, targets in self.aliases:
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
        return None


def churn(root: Path, months: int) -> Counter[str]:
    log = git(root, "-c", "core.quotePath=false", "log", f"--since={months}.months", "--no-merges", "--name-only", "--relative", "--pretty=tformat:")
    return Counter(line.strip() for line in log.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# Tool checks


def runner_facts(manifests: Manifests, files: list[str], test_imports: Counter[str]) -> dict[str, list[str]]:
    """Each detected runner, with verifiable evidence."""
    runners: dict[str, list[str]] = {}

    def add(name: str, entry: str) -> None:
        runners.setdefault(name, []).append(entry)

    for runner, package in (
        ("vitest", "vitest"), ("jest", "jest"), ("playwright", "@playwright/test"), ("cypress", "cypress"),
        ("storybook-test-runner", "@storybook/test-runner"), ("storybook-test-runner", "@storybook/addon-vitest"),
        ("mocha", "mocha"), ("ava", "ava"),
    ):
        manifest = manifests.dependency(package)
        if manifest:
            add(runner, cite_file(manifest, f'"{package}"'))
    config_patterns = {
        "vitest": r"(^|/)vitest\.(?:config|workspace)\.[cm]?[jt]s$",
        "jest": r"(^|/)jest\.config\.[cm]?[jt]s(?:on)?$",
        "playwright": r"(^|/)playwright(?:-ct)?\.config\.[cm]?[jt]s$",
        "cypress": r"(^|/)cypress\.config\.[cm]?[jt]s$",
    }
    for runner, pattern in config_patterns.items():
        for path in files:
            if re.search(pattern, path) and runner not in runners:
                add(runner, cite_file(path))
    if test_imports.get("node:test"):
        add("node:test", f"command: `collect.py test-file scan` → {test_imports['node:test']} test files import node:test")
    for path, name, command in manifests.scripts():
        match = RUNNER_COMMANDS["node:test"].search(command)
        if match and "node:test" not in runners:
            add("node:test", cite_file(path, match.group(0)))
    return runners


def scripts_running_tests(manifests: Manifests) -> list[tuple[str, str, str]]:
    """(manifest, script name, the fragment that runs tests) for each script that runs a test runner."""
    found = []
    for path, name, command in manifests.scripts():
        match = next((match for pattern in RUNNER_COMMANDS.values() for match in [pattern.search(command)] if match), None)
        if match is None and name == "test":
            match = DELEGATING_TEST_COMMAND.search(command)
        if match:
            found.append((path, name, match.group(0).strip()))
    return found


def object_block(text: str, key: str) -> str | None:
    """The balanced `{...}` value of the first `key: {` in comment-masked text."""
    match = re.search(rf"\b{re.escape(key)}['\"]?\s*:\s*\{{", text)
    if not match:
        return None
    opening = match.end() - 1
    closing = matching(text, opening)
    return text[opening:closing + 1] if closing != -1 else text[opening:]


def coverage_facts(root: Path, files: list[str], manifests: Manifests, workflows: list[str]) -> dict[str, Any]:
    thresholds: list[str] = []
    enabled: list[str] = []
    for path in files:
        name = Path(path).name
        if re.fullmatch(r"(?:vitest|vite)\.config\.[cm]?[jt]s|vitest\.workspace\.[cm]?[jt]s", name):
            text = mask(read_text(root / path), strings=False)
            block = object_block(text, "coverage")
            if block:
                if re.search(r"\bthresholds\s*:", block):
                    thresholds.append(cite_file(path, "thresholds"))
                elif re.search(r"\b(?:lines|branches|functions|statements)\s*:\s*\d", block):
                    thresholds.append(cite_file(path, "coverage", "with per-metric minimums"))
                if re.search(r"\benabled\s*:\s*true\b", block):
                    enabled.append(cite_file(path, "enabled: true"))
        elif re.fullmatch(r"jest\.config\.[cm]?[jt]s(?:on)?", name):
            text = mask(read_text(root / path), strings=False)
            if re.search(r"\bcoverageThreshold['\"]?\s*:", text):
                thresholds.append(cite_file(path, "coverageThreshold"))
            if re.search(r"\bcollectCoverage['\"]?\s*:\s*true\b", text):
                enabled.append(cite_file(path, "collectCoverage"))
        elif name in (".c8rc", ".c8rc.json", ".nycrc", ".nycrc.json"):
            text = read_text(root / path)
            if re.search(r"check-coverage|checkCoverage", text) and re.search(r"\b(?:lines|branches|functions|statements)\b", text):
                thresholds.append(cite_file(path, "check-coverage" if "check-coverage" in text else "checkCoverage"))
    for path, package in manifests.packages:
        jest = package.get("jest")
        if isinstance(jest, dict) and jest.get("coverageThreshold"):
            thresholds.append(cite_file(path, '"coverageThreshold"'))
        if isinstance(jest, dict) and jest.get("collectCoverage") is True:
            enabled.append(cite_file(path, '"collectCoverage"'))
        for tool in ("c8", "nyc"):
            settings = package.get(tool)
            if isinstance(settings, dict) and (settings.get("check-coverage") or settings.get("checkCoverage")):
                thresholds.append(cite_file(path, f'"{tool}"'))
    for path, name, command in manifests.scripts():
        fragment = command.strip()
        match = re.search(r"--coverage\.thresholds\.\w+=\d+|--test-coverage-(?:lines|branches|functions)=\d+|\bc8\b[^&|;]*--check-coverage|\bnyc\b[^&|;]*--check-coverage", fragment)
        if match:
            thresholds.append(cite_file(path, match.group(0)))
        match = re.search(r"--coverage\b(?!\.thresholds)|--experimental-test-coverage|(?<![\w-])(?:c8|nyc)\s", fragment)
        if match:
            enabled.append(cite_file(path, match.group(0).strip()))
    for path in workflows:
        for number, line in enumerate(read_text(root / path).split("\n"), start=1):
            match = re.search(r"--coverage\b|--experimental-test-coverage", line.split("#", 1)[0])
            if match:
                enabled.append(cite(path, number, match.group(0)))
    return {"thresholds": thresholds, "enabled": enabled}


def eslint_configuration(root: Path, files: list[str], manifests: Manifests) -> dict[str, Any]:
    """Read every ESLint configuration as text, for the testing-library and jest-dom plugins."""
    configs = [path for path in files if Path(path).name in ESLINT_CONFIG_NAMES]
    result: dict[str, Any] = {"files": configs, "testing-library": None, "jest-dom": None}
    for plugin, prefix, preset_pattern, presets in (
        ("testing-library", "testing-library", r"(?:flat/)?(react|vue|svelte|angular|marko|dom)", TESTING_LIBRARY_PRESETS),
        ("jest-dom", "jest-dom", r"(?:flat/)?(recommended|all)", {"recommended": set(JEST_DOM_RULES), "all": set(JEST_DOM_RULES)}),
    ):
        enabled_in: list[str] = []
        preset_rules: set[str] = set()
        preset_names: list[str] = []
        rules: dict[str, str] = {}
        rule_evidence: dict[str, str] = {}
        sources: list[tuple[str, str]] = [(path, mask(read_text(root / path), strings=False)) for path in configs]
        for path, package in manifests.packages:
            if isinstance(package.get("eslintConfig"), dict):
                sources.append((path, json.dumps(package["eslintConfig"])))
        for path, text in sources:
            package_name = f"eslint-plugin-{plugin}"
            bindings = re.findall(rf"(?:import\s+(?:\*\s+as\s+)?([\w$]+)\s+from|(?:const|let|var)\s+([\w$]+)\s*=\s*require\s*\()\s*['\"]{re.escape(package_name)}['\"]", text)
            names = [name for pair in bindings for name in pair if name]
            uses_plugin = bool(re.search(rf"['\"]{re.escape(package_name)}['\"]", text)) or bool(
                re.search(rf"['\"]plugin:{re.escape(prefix)}/", text)
            ) or bool(re.search(rf"['\"]plugins['\"]?\s*:\s*\[[^\]]*['\"]{re.escape(prefix)}['\"]", text))
            for name in names:
                for match in re.finditer(rf"\b{re.escape(name)}\s*\.\s*configs\s*(?:\[\s*['\"]{preset_pattern}['\"]\s*\]|\.\s*{preset_pattern.replace('(?:flat/)?', '')}\b)", text):
                    preset = match.group(1) or match.group(2)
                    preset_names.append(preset)
                    preset_rules |= presets.get(preset, set())
                    enabled_in.append(cite_file(path, match.group(0)))
            for match in re.finditer(rf"['\"]plugin:{re.escape(prefix)}/{preset_pattern.replace('(?:flat/)?', '')}['\"]", text):
                preset = match.group(1)
                preset_names.append(preset)
                preset_rules |= presets.get(preset, set())
                enabled_in.append(cite_file(path, match.group(0).strip("'\"")))
            for match in re.finditer(
                rf"['\"]{re.escape(prefix)}/([\w-]+)['\"]\s*:\s*(\[\s*)?['\"]?(off|warn|error|0|1|2)\b", text
            ):
                level = {"0": "off", "1": "warn", "2": "error"}.get(match.group(3), match.group(3))
                rules[match.group(1)] = level
                rule_evidence[match.group(1)] = cite_file(path, f"{prefix}/{match.group(1)}")
            if uses_plugin and not enabled_in and (names or rules):
                enabled_in.append(cite_file(path, package_name if package_name in text else f"{prefix}/"))
        if not enabled_in and not rules:
            continue
        effective = set(preset_rules) | {rule for rule, level in rules.items() if level != "off"}
        effective -= {rule for rule, level in rules.items() if level == "off"}
        result[plugin] = {
            "presets": sorted(set(preset_names)),
            "rules": rules,
            "effective": sorted(effective),
            "evidence": enabled_in[:2] + [rule_evidence[rule] for rule in sorted(rule_evidence)][:4],
        }
    return result


def lint_check(plugin: str, required: tuple[str, ...], facts: dict[str, Any], config_files: list[str], manifests: Manifests) -> dict[str, Any]:
    found = facts.get(plugin)
    package = f"eslint-plugin-{plugin}"
    if not found:
        evidence = [f"command: `collect.py ESLint configuration scan` → {len(config_files)} configuration file(s); none enables {package}"]
        evidence += [cite_file(path) for path in config_files[:2]]
        return {
            "status": "missing",
            "evidence": evidence,
            "evidenceTier": "direct",
            "gap": f"No ESLint configuration enables {package}, so nothing stops the test mistakes its rules catch from being committed.",
            "remediation": (
                f"Install {package} and add its shared configuration for test files"
                + (", plus `testing-library/prefer-user-event` and `testing-library/prefer-explicit-assert`, which no shared configuration enables" if plugin == "testing-library" else "")
                + "; run ESLint over test files in continuous integration."
            ),
            "judgement": "act-on" if plugin == "testing-library" else "consider",
        }
    missing = [rule for rule in required if rule not in found["effective"]]
    if not missing:
        return {"status": "present", "evidence": found["evidence"], "evidenceTier": "direct"}
    installed = manifests.dependency(package)
    evidence = found["evidence"] + ([cite_file(installed, f'"{package}"')] if installed else [])
    return {
        "status": "partial",
        "evidence": evidence[:6],
        "evidenceTier": "direct",
        "gap": f"{package} is enabled, but {len(missing)} of the required rules are not: " + ", ".join(f"{plugin}/{rule}" for rule in missing) + ".",
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
    overrides = {
        "mutate": targets,
        "reporters": ["json"],
        "jsonReporter": {"fileName": f"{directory}/mutation.json"},
        "tempDirName": f"{directory}/sandbox",
        "cleanTempDir": True,
        "incremental": False,
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
    detected = statuses["Killed"] + statuses["Timeout"]
    undetected = statuses["Survived"] + statuses["NoCoverage"]
    if detected + undetected == 0:
        return None, statuses, survivors
    return round(100 * detected / (detected + undetected), 1), statuses, survivors


Runner = Callable[..., Any]


def mutation_check(root: Path, requested: bool, targets: list[str], run: Runner = subprocess.run) -> dict[str, Any]:
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
            check=False, timeout=MUTATION_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return {"evaluationState": "not-evaluated", "reason": f"Stryker did not finish on the {len(targets)} riskiest files within {MUTATION_TIMEOUT_SECONDS} seconds."}
    except OSError as error:
        return {"evaluationState": "not-evaluated", "reason": f"Stryker failed to start: {type(error).__name__}."}
    finally:
        config_path.unlink(missing_ok=True)
    if not report_path.is_file():
        tail = (completed.stderr or completed.stdout or "").strip().splitlines()[-1:] if completed else []
        detail = f": {tail[0][:200]}" if tail else ""
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


def collect(root: Path, months: int = 6, with_mutation: bool = False, run: Runner = subprocess.run) -> dict[str, Any]:
    files = listed_files(root)
    manifests = Manifests(root)
    source_paths = [path for path in files if path.endswith(SOURCE_SUFFIXES) and not path.endswith(".d.ts")]
    checks: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}

    if not manifests.packages and not source_paths:
        for check in ALL_CHECKS:
            checks[check] = {"applicability": "not-applicable", "reason": "No package.json and no JavaScript or TypeScript sources were found."}
        snapshot["projectDetected"] = False
        return {"checks": checks, "snapshot": snapshot}

    texts: dict[str, str] = {path: read_text(root / path) for path in source_paths}
    comment_masked = {path: mask(text, strings=False) for path, text in texts.items()}
    fully_masked: dict[str, str] = {}
    test_paths: list[str] = []
    for path in source_paths:
        if TEST_NAME_PATTERN.search(path) or TEST_DIRECTORY_PATTERN.search(path) or "/__tests__/" in f"/{path}" or STORY_PATTERN.search(path):
            fully_masked[path] = mask(texts[path], strings=True)
        if is_test_file(path, fully_masked.get(path)):
            test_paths.append(path)
    stories = [path for path in source_paths if STORY_PATTERN.search(path)]
    interaction_stories = [path for path in stories if re.search(r"\bplay\s*[:=(]|\bplay\s*\(", fully_masked[path])]

    resolver = Resolver(root, set(source_paths))

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
        blocks, counts = prefilter(path, text, masked, product_module(path), web_first=kind in ("end-to-end", "playwright-component"))
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
            if path in test_paths or TEST_SUPPORT_PATTERN.search(path):
                tested_by.setdefault(target, set()).add(path)
            else:
                fan_in[target] += 1
    production = [path for path in source_paths if not NON_SOURCE_PATTERN.search(path) and path not in test_paths]
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
    snapshot["projectDetected"] = True
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
    unit_runners = {"vitest", "jest", "node:test", "mocha", "ava"} & set(runners)
    if not test_paths and not interaction_stories:
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
            "evidence": [f"command: `collect.py runner detection` → {len(test_paths)} test files but no Vitest, Jest, node:test, Playwright, Cypress, Storybook test-runner, Mocha, or AVA"] + [cite_file(path) for path in test_paths[:2]],
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
        checks[RUNNER] = {"status": "present", "evidence": evidence, "evidenceTier": "direct"}

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

    # Coverage thresholds.
    coverage = coverage_facts(root, files, manifests, workflows)
    snapshot["coverage"] = {"thresholds": coverage["thresholds"][:4], "collectedBy": coverage["enabled"][:4]}
    if not unit_runners:
        checks[COVERAGE] = {"applicability": "not-applicable", "reason": "No unit or integration test runner (Vitest, Jest, node:test, Mocha, or AVA) was found."}
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
    eslint = eslint_configuration(root, files, manifests)
    snapshot["eslint"] = {
        "configurationFiles": eslint["files"],
        "testingLibrary": eslint["testing-library"] and {key: eslint["testing-library"][key] for key in ("presets", "effective")},
        "jestDom": eslint["jest-dom"] and {key: eslint["jest-dom"][key] for key in ("presets", "effective")},
    }
    if not testing_library:
        checks[TESTING_LIBRARY_LINT] = {"applicability": "not-applicable", "reason": "Testing Library is not a dependency, so its lint rules have nothing to check."}
    else:
        checks[TESTING_LIBRARY_LINT] = lint_check("testing-library", TESTING_LIBRARY_RULES, eslint, eslint["files"], manifests)
    if not manifests.dependency("@testing-library/jest-dom"):
        checks[JEST_DOM_LINT] = {"applicability": "not-applicable", "reason": "@testing-library/jest-dom is not a dependency."}
    else:
        checks[JEST_DOM_LINT] = lint_check("jest-dom", JEST_DOM_RULES, eslint, eslint["files"], manifests)

    # Applicability of the model checks.
    if not test_paths:
        for check in (FAILS, MOCKS, DETERMINISTIC, TITLES):
            checks[check] = {"applicability": "not-applicable", "reason": "The repository has no test files; tests-run-under-configured-runner records the gap."}
    if not ui:
        checks[COMPONENTS] = {"applicability": "not-applicable", "reason": "No user-interface framework is a dependency."}
    if not databases:
        checks[DATABASE] = {"applicability": "not-applicable", "reason": "No database client or ORM is a dependency."}

    checks[MUTATION] = mutation_check(root, with_mutation, mutation_targets, run)
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
