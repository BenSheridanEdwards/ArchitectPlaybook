#!/usr/bin/env python3
"""Deterministic, read-only collector for /react-audit.

Reads a React repository's package manifests, lockfiles, ESLint, Biome, and
React Compiler configuration, its source files, and its Git history, then
reports:

- the tool checks: the hooks lint rules that apply to component files, a
  maintained toolchain (Create React App is deprecated), justified
  exhaustive-deps suppressions, `ref` as a prop on React 19, named exports
  outside framework-required files, and client modules that pull server-only
  code into the browser;
- applicability: no React, no server components, React below 19, or the React
  Compiler on everywhere, each with its reason;
- Layer 0 facts: React versions per package, frameworks, compiler adoption,
  directive inventory, effect and state candidates for the model, and a risk
  ranking from Git churn and import fan-in.

Configuration is read as text. Nothing is installed or evaluated, and
node_modules is never read. Comments, strings, template literals, regular
expressions, and JSX text are masked before any code pattern is matched.

The output follows the audit protocol's collector contract: a JSON object with
`checks` (results keyed by checkId) and `snapshot` (Layer 0 facts). The script
never writes to the repository. Standard library only; Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from bisect import bisect_left
from collections import Counter, deque
from pathlib import Path
from typing import Any

AUDIT = "react-audit"
SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
RESOLVE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
MARKUP_SUFFIXES = (".tsx", ".jsx", ".js", ".mjs", ".cjs")
SKIPPED_DIRECTORIES = {
    ".git", "node_modules", "dist", "build", "out", "coverage", ".next", ".nuxt", ".expo",
    ".turbo", ".cache", ".vercel", ".output", "storybook-static", ".architect-audits",
    "graphify-out", ".worktrees", ".claude", ".react-router", "playwright-report", "test-results",
}
MAX_FILES = 50_000
MAX_FILE_BYTES = 2_000_000
# Lockfiles of large monorepos run to tens of megabytes; they are read as data, never lexed.
MAX_LOCKFILE_BYTES = 100_000_000
MAX_CANDIDATES = 60
MAX_EFFECTS_PER_FILE = 200
MAX_TAGS_PER_FILE = 2_000
LINT_SAMPLE_SIZE = 20


def check_id(name: str) -> str:
    return f"{AUDIT}.{name}"


HOOKS_LINT = check_id("react-hooks-plugin-enabled")
TOOLCHAIN = check_id("maintained-toolchain")
SUPPRESSIONS = check_id("no-silenced-exhaustive-deps")
DERIVED = check_id("no-state-synchronisation")
CLEANUP = check_id("cleanup-functions-present")
DEPENDENCIES = check_id("effect-dependencies-honest")
STABLE = check_id("memoization-for-correctness")
HOOK_DESIGN = check_id("custom-hook-design")
SOURCE_OF_TRUTH = check_id("no-state-synchronisation-between-sources-of-truth")
STATUS_VALUES = check_id("status-enums-over-multiple-booleans")
PLACEMENT = check_id("state-at-the-right-level")
URL_STATE = check_id("url-state-for-shareable-state")
REF_PROP = check_id("ref-passed-as-a-prop")
NAMED_EXPORTS = check_id("named-exports-for-components")
FORMS = check_id("forms-use-actions")
SERVER_IMPORTS = check_id("client-modules-import-no-server-code")
SERIALISABLE = check_id("serialisable-props-cross-the-boundary")
CLIENT_LEAVES = check_id("use-client-minimal-and-deliberate")
ALL_CHECKS = (
    HOOKS_LINT, TOOLCHAIN, SUPPRESSIONS, DERIVED, CLEANUP, DEPENDENCIES, STABLE, HOOK_DESIGN,
    SOURCE_OF_TRUTH, STATUS_VALUES, PLACEMENT, URL_STATE, REF_PROP, NAMED_EXPORTS, FORMS,
    SERVER_IMPORTS, SERIALISABLE, CLIENT_LEAVES,
)
SERVER_COMPONENT_CHECKS = (SERVER_IMPORTS, SERIALISABLE, CLIENT_LEAVES)

REACT_PACKAGES = ("react", "react-dom", "react-native", "next", "expo", "@react-router/dev", "react-router",
                  "react-router-dom", "@remix-run/react", "gatsby", "react-scripts")
SERVER_COMPONENT_PACKAGES = ("waku", "@vitejs/plugin-rsc", "react-server-dom-webpack", "react-server-dom-turbopack",
                             "react-server-dom-parcel", "@parcel/rsc")
NODE_BUILTINS = {
    "fs", "fs/promises", "child_process", "net", "tls", "dns", "dgram", "cluster", "worker_threads", "os", "v8", "vm",
    "async_hooks", "http2", "inspector", "repl", "readline", "module", "perf_hooks",
}
SERVER_PACKAGES = {
    "server-only", "next/headers", "pg", "postgres", "mysql2", "mysql", "@prisma/client", "mongodb", "mongoose",
    "better-sqlite3", "sqlite3", "ioredis", "redis", "@vercel/postgres", "@neondatabase/serverless", "knex",
    "drizzle-orm/node-postgres", "drizzle-orm/postgres-js", "drizzle-orm/mysql2", "drizzle-orm/better-sqlite3",
    "@aws-sdk/client-s3", "nodemailer", "bcrypt", "argon2", "stripe", "firebase-admin",
}
PUBLIC_ENVIRONMENT_PREFIXES = ("NEXT_PUBLIC_", "VITE_", "EXPO_PUBLIC_", "PUBLIC_", "REACT_APP_")
ALWAYS_INLINED_ENVIRONMENT = {"NODE_ENV", "NEXT_RUNTIME"}

# eslint-plugin-react-hooks rules. `recommended` in 7.x adds the React Compiler rules to the two classic hook rules.
CORE_HOOK_RULES = ("rules-of-hooks", "exhaustive-deps")
COMPILER_HOOK_RULES = (
    "config", "error-boundaries", "gating", "globals", "immutability", "incompatible-library",
    "preserve-manual-memoization", "purity", "refs", "set-state-in-effect", "set-state-in-render",
    "static-components", "unsupported-syntax", "use-memo",
)
REQUIRED_HOOK_RULES = CORE_HOOK_RULES + COMPILER_HOOK_RULES
ESLINT_CONFIG_NAMES = (
    "eslint.config.js", "eslint.config.mjs", "eslint.config.cjs", "eslint.config.ts", "eslint.config.mts",
    "eslint.config.cts", ".eslintrc", ".eslintrc.js", ".eslintrc.cjs", ".eslintrc.json", ".eslintrc.yaml",
    ".eslintrc.yml",
)
LEVELS = {"0": "off", "1": "warn", "2": "error", "off": "off", "warn": "warn", "error": "error"}
# Shared configurations that cannot turn a react-hooks rule on or off.
NEUTRAL_CONFIG_PACKAGE = re.compile(
    r"^(?:@eslint/js|@eslint/compat|@eslint/eslintrc|@eslint/config-helpers|typescript-eslint|@typescript-eslint/[\w-]+|globals"
    r"|eslint|eslint/config|eslint-config-prettier(?:/[\w-]+)?|eslint-config-next/typescript|node:[\w/]+|path|url"
    r"|eslint-plugin-(?!react-hooks$|react-compiler$)[\w-]+(?:/[\w./-]+)?|@[\w-]+/eslint-plugin(?:-[\w-]+)?(?:/[\w./-]+)?)$"
)
NEUTRAL_LEGACY_EXTENDS = re.compile(
    r"^(?:eslint:[\w-]+|plugin:(?!react-hooks/|react-compiler/)[@\w/-]+/[\w-]+|prettier(?:/[\w-]+)?|next/typescript"
    r"|react-app/jest|airbnb(?:-base|-typescript)?|standard)$"
)

NON_SOURCE_PATTERN = re.compile(
    r"(^|/)(__tests__|__mocks__|tests?|e2e|cypress|playwright|fixtures?|mocks?|\.storybook|stories)/"
    r"|\.(test|spec|cy|e2e|stories|d)\.[cm]?[jt]sx?$|(^|/)[^/]*\.config\.[cm]?[jt]s$|(^|/)[^/]*\.setup\.[cm]?[jt]sx?$"
    r"|(^|/)\.[^/]+$"
)
TOOLING_FOLDERS = ("scripts", "tools", "tooling")
STATIC_IMPORT = re.compile(
    r"""(?m)^[ \t]*(?P<keyword>import|export)\b(?P<clause>[^;'"`()]*?)\bfrom\s*(?P<quote>['"])(?P<specifier>[^'"\n]+)(?P=quote)"""
)
SIDE_EFFECT_IMPORT = re.compile(r"""(?m)^[ \t]*import\s*(?P<quote>['"])(?P<specifier>[^'"\n]+)(?P=quote)""")
DYNAMIC_IMPORT = re.compile(r"""\b(?:import|require)\s*\(\s*(?P<quote>['"])(?P<specifier>[^'"\n]+)(?P=quote)\s*\)""")
DIRECTIVE = re.compile(r"""(['"])([^'"\n]*)\1[ \t]*;?""")


# ---------------------------------------------------------------------------
# Reading files


def read_text(path: Path, limit: int = MAX_FILE_BYTES) -> str:
    """A file's text without newline translation, so a lone carriage return never becomes a line; empty when too big."""
    try:
        if path.stat().st_size > limit:
            return ""
        return path.read_bytes().decode("utf-8", errors="replace")
    except OSError:
        return ""


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


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
        candidates = []
        for path in root.rglob("*"):
            parts = path.relative_to(root).parts
            if any(part in SKIPPED_DIRECTORIES for part in parts[:-1]):
                continue
            if path.is_file():
                candidates.append(path.relative_to(root).as_posix())
    files = []
    for entry in sorted(set(candidates)):
        parts = entry.split("/")
        if any(part in SKIPPED_DIRECTORIES for part in parts[:-1]):
            continue
        if (root / entry).is_file() and not (root / entry).is_symlink():
            files.append(entry)
    return files[:MAX_FILES]


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def folder(path: str) -> str:
    return path.rsplit("/", 1)[0] if "/" in path else ""


def location(path: str) -> str:
    """A path the audit protocol can parse; paths with spaces go in backticks."""
    return f"`{path}`" if any(character.isspace() for character in path) else path


def one_line(value: str | None) -> str | None:
    return " ".join(value.split()) if value else value


def cite(path: str, line: int, quote: str | None = None, note: str | None = None) -> str:
    """A citation the audit protocol verifies against the cited line."""
    quote, note = one_line(quote), one_line(note)
    text = f"{location(path)}:{line}"
    if quote:
        text += f" — `{quote}`"
        if note:
            text += f" {note}"
    elif note:
        text += f" — {note}"
    return text


def cite_file(path: str, quote: str | None = None, note: str | None = None) -> str:
    quote, note = one_line(quote), one_line(note)
    text = location(path)
    if quote:
        text += f" — `{quote}`"
        if note:
            text += f" {note}"
    elif note:
        text += f" — {note}"
    return text


def newline_offsets(text: str) -> list[int]:
    return [index for index, character in enumerate(text) if character == "\n"]


class Lines:
    """Line lookups for one text, built once."""

    def __init__(self, text: str) -> None:
        self.text = text
        self.offsets = newline_offsets(text)

    def number(self, index: int) -> int:
        return bisect_left(self.offsets, index) + 1

    def line(self, number: int) -> str:
        start = 0 if number <= 1 else self.offsets[number - 2] + 1
        end = self.offsets[number - 1] if number - 1 < len(self.offsets) else len(self.text)
        return self.text[start:end]


def fragment(text: str, limit: int = 60) -> str:
    """A quotable piece of one line: no backticks, trimmed."""
    return text.split("`", 1)[0].strip()[:limit].strip()


def quotable(haystack: str, *candidates: str) -> str | None:
    """The first candidate that appears verbatim in the text, compared as the audit protocol compares quotes."""
    text = " ".join(haystack.split())
    for candidate in candidates:
        if candidate and "`" not in candidate and " ".join(candidate.split()) in text:
            return candidate
    return None


# ---------------------------------------------------------------------------
# Lexing: comments, strings, templates, regular expressions, and JSX


def line_end(text: str, index: int) -> int:
    """Where the line starting at or before `index` ends: the next newline or carriage return."""
    ends = [position for position in (text.find("\n", index), text.find("\r", index)) if position != -1]
    return min(ends) if ends else len(text)


def mask_comments_only(text: str) -> tuple[str, list[tuple[int, int]]]:
    """Blank comments only, as a fallback when the full lexer loses its place."""
    masked, _, comments = mask_fallback(text)
    return masked, comments


def mask_fallback(text: str) -> tuple[str, str, list[tuple[int, int]]]:
    """Blank comments, and in a second view also string and template contents, without tracking JSX or regular expressions."""
    output = list(text)
    strings = list(text)
    comments: list[tuple[int, int]] = []
    index = 0
    quote = ""
    while index < len(text):
        character = text[index]
        if quote:
            if character == "\\":
                for position in (index, index + 1):
                    if position < len(text) and strings[position] not in "\r\n":
                        strings[position] = " "
                index += 2
                continue
            if character == quote or (character in "\r\n" and quote != "`"):
                quote = ""
            elif character not in "\r\n":
                strings[index] = " "
            index += 1
            continue
        if character in "'\"`":
            quote = character
            index += 1
            continue
        if text.startswith("//", index):
            end = line_end(text, index)
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end == -1 else end + 2
        else:
            index += 1
            continue
        comments.append((index, end))
        for position in range(index, end):
            if output[position] not in "\r\n":
                output[position] = strings[position] = " "
        index = end
    return "".join(output), "".join(strings), comments


def type_arguments_end(text: str, index: int) -> int | None:
    """Skip balanced JSX component type arguments, including nested types."""
    depth = 0
    quote = ""
    while index < len(text):
        character = text[index]
        if quote:
            if character == "\\":
                index += 2
                continue
            if character == quote:
                quote = ""
        elif character in "'\"`":
            quote = character
        elif character == "<":
            depth += 1
        elif character == ">" and text[index - 1] != "=":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


class Source:
    """One source file, lexed once.

    `comment_masked` blanks comments and keeps everything else, so import
    specifiers and JSX stay readable. `code` keeps only code: comments, string
    and template text, regular expressions, and JSX text and tags are blanked,
    while `${...}` and JSX `{...}` expressions stay. Offsets and line numbers
    never change, so a match maps back to the original text.
    """

    def __init__(self, path: str, text: str) -> None:
        self.path = path
        self.text = text
        allow_markup = path.endswith(MARKUP_SUFFIXES)
        self.comment_masked, self.code, self.comments, self.has_markup = lex(text, allow_markup)
        self.lines = Lines(text)
        self._pairs: dict[int, int] | None = None

    def line_of(self, index: int) -> int:
        return self.lines.number(index)

    def pairs(self) -> dict[int, int]:
        """Matching brackets in the code view, computed once in linear time."""
        if self._pairs is None:
            pairs: dict[int, int] = {}
            stack: list[int] = []
            closers = {")": "(", "]": "[", "}": "{"}
            for index, character in enumerate(self.code):
                if character in "([{":
                    stack.append(index)
                elif character in closers:
                    while stack and self.code[stack[-1]] != closers[character]:
                        stack.pop()
                    if stack:
                        pairs[stack.pop()] = index
            self._pairs = pairs
        return self._pairs

    def close(self, index: int) -> int:
        return self.pairs().get(index, -1)


def lex(text: str, allow_markup: bool) -> tuple[str, str, list[tuple[int, int]], bool]:
    """Return the comment-masked text, the code-only view, the comment spans, and whether JSX was seen.

    A `/` after an operator, a keyword such as `return`, or the `)` that closes
    an `if`, `while`, or `for` condition starts a regular expression; after a
    value it is division. This is a lexical filter, not a full parser: when it
    loses its place, it falls back to masking comments only.
    """
    output = list(text)
    code_positions = bytearray(len(text))
    comments: list[tuple[int, int]] = []
    has_markup = False
    index = 0
    invalid = False
    contexts: list[dict[str, Any]] = [{"kind": "code", "depth": 0, "operand": True, "token": "", "brackets": [], "statement": True}]
    expression_keywords = {"return", "throw", "case", "await", "yield", "delete", "void", "typeof", "new", "in", "instanceof", "of", "else", "do"}
    control_keywords = {"if", "while", "for", "with", "switch", "catch"}
    markup_start = re.compile(r"<(?:[A-Za-z]|>)")
    tag_name = re.compile(r"[A-Za-z_$][\w$-]*(?:[.:][A-Za-z_$][\w$-]*)*")
    generic_parameters = re.compile(r"<(?:const\s+)?[A-Za-z_$][\w$]*\s*(?:[,=]|\bextends\b)")
    generic_arrow = re.compile(r"<([A-Za-z_$][\w$]*)>\s*\(")
    length = len(text)
    while index < length:
        character = text[index]
        frame = contexts[-1]
        context = frame["kind"]
        depth = frame.get("depth", 0)
        if context in ("'", '"'):
            attribute = contexts[-2]["kind"] == "tag"
            if character == "\\" and not attribute:
                index += 2
                continue
            if character == context or (character in "\r\n" and not attribute):
                contexts.pop()
                if contexts[-1]["kind"] in ("code", "expression"):
                    contexts[-1].update(operand=False, token="literal", statement=False)
            index += 1
            continue
        if context == "template":
            if character == "\\":
                index += 2
                continue
            if character == "`":
                contexts.pop()
                contexts[-1].update(operand=False, token="literal", statement=False)
            elif text.startswith("${", index):
                contexts.append({"kind": "expression", "depth": 1, "operand": True, "token": "", "brackets": [], "statement": False})
                code_positions[index] = code_positions[index + 1] = 1
                index += 2
                continue
            index += 1
            continue
        if context == "markup":
            if character == "<":
                closing = text.startswith("</", index)
                contexts.append({"kind": "tag", "depth": -1 if closing else 1, "name": True})
                index += 2 if closing else 1
            elif character == "{":
                contexts.append({"kind": "expression", "depth": 1, "operand": True, "token": "", "brackets": [], "statement": False})
                code_positions[index] = 1
                index += 1
            else:
                index += 1
            continue
        if context == "tag":
            if frame["name"]:
                name = tag_name.match(text, index)
                frame.update(name=False, type_allowed=depth == 1)
                if name:
                    index = name.end()
                    continue
            if character == "<" and frame.get("type_allowed"):
                end = type_arguments_end(text, index)
                if end is None:
                    invalid = True
                    break
                index = end
                frame["type_allowed"] = False
                continue
            if not character.isspace():
                frame["type_allowed"] = False
            if character in "'\"":
                contexts.append({"kind": character})
            elif character == "{":
                contexts.append({"kind": "expression", "depth": 1, "operand": True, "token": "", "brackets": [], "statement": False})
                code_positions[index] = 1
            elif character == ">":
                contexts.pop()
                if depth == -1 or text[index - 1] != "/":
                    contexts[-1]["depth"] += depth
                if contexts[-1]["depth"] < 0:
                    invalid = True
                    break
                if contexts[-1]["depth"] == 0:
                    contexts.pop()
                    contexts[-1].update(operand=False, token="markup", statement=False)
            index += 1
            continue
        if text.startswith("//", index):
            end = line_end(text, index)
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = length if end == -1 else end + 2
        else:
            generic = generic_arrow.match(text, index) if allow_markup and character == "<" else None
            if generic and f"</{generic.group(1)}>" in text[index:]:
                generic = None  # an element such as <Trans>(optional)</Trans>, not type parameters
            if character in "'\"":
                contexts.append({"kind": character})
            elif character == "`":
                contexts.append({"kind": "template"})
            elif allow_markup and character == "<" and frame["operand"] and markup_start.match(text, index) and not generic_parameters.match(text, index) and not generic:
                has_markup = True
                contexts.append({"kind": "markup", "depth": 0})
                contexts.append({"kind": "tag", "depth": 1, "name": True})
                index += 1
                continue
            elif character == "/" and frame["operand"]:
                # A regular expression: its brackets and quotes do not change the enclosing code.
                index += 1
                character_class = False
                while index < length:
                    if text[index] == "\\":
                        index += 2
                        continue
                    if text[index] == "[":
                        character_class = True
                    elif text[index] == "]":
                        character_class = False
                    elif text[index] == "/" and not character_class:
                        index += 1
                        break
                    elif text[index] in "\r\n":
                        break
                    index += 1
                while index < length and text[index].isalpha():
                    index += 1
                frame.update(operand=False, token="literal", statement=False)
                continue
            else:
                code_positions[index] = 1
                if character.isalnum() or character in "_$":
                    end = index + 1
                    while end < length and (text[end].isalnum() or text[end] in "_$"):
                        end += 1
                    code_positions[index:end] = b"\x01" * (end - index)
                    token = text[index:end]
                    if token == "function" and frame["token"] != ".":
                        frame["function_declaration"] = frame["statement"]
                    if token == "class" and frame["token"] != ".":
                        frame["class_declaration"] = frame["statement"]
                    frame["control"] = token if token in control_keywords and frame["token"] != "." else ""
                    declaration_prefix = frame["statement"] and token in {"export", "default", "async", "declare"}
                    frame.update(
                        operand=token in expression_keywords and frame["token"] != ".",
                        token=token,
                        statement=declaration_prefix or (token in {"else", "do", "try", "finally"} and frame["token"] != "."),
                    )
                    index = end
                    continue
                if character == "(":
                    if "function_declaration" in frame:
                        frame["brackets"].append(("function", frame.pop("function_declaration")))
                    else:
                        frame["brackets"].append("control" if frame.get("control") else "parentheses")
                    frame.update(operand=True, statement=False)
                elif character == ")":
                    bracket = frame["brackets"].pop() if frame["brackets"] else None
                    if isinstance(bracket, tuple):
                        frame["body_declaration"] = bracket[1]
                    control = bracket == "control"
                    frame.update(operand=control, statement=control)
                elif character == "{":
                    if "class_declaration" in frame:
                        frame["brackets"].append("declaration" if frame.pop("class_declaration") else "class-expression")
                    elif "body_declaration" in frame:
                        frame["brackets"].append("declaration" if frame.pop("body_declaration") else "function-expression")
                    else:
                        frame["brackets"].append("block" if frame["statement"] or frame["token"] == "=>" else "object")
                    frame.update(operand=True, statement=True)
                    if context == "expression":
                        frame["depth"] = depth + 1
                elif character == "}":
                    if context == "expression":
                        if depth == 1:
                            contexts.pop()
                            index += 1
                            continue
                        frame["depth"] = depth - 1
                    block = bool(frame["brackets"]) and frame["brackets"].pop() in {"block", "declaration"}
                    frame.update(operand=block, statement=block)
                elif character == "!" and not frame["operand"] and not text.startswith("!=", index):
                    pass  # a TypeScript non-null assertion such as `total!`: a value still ends here, so `/` is division
                elif not character.isspace():
                    frame.update(operand=character not in "].", statement=character == ";")
                    if text.startswith(("++", "--", "=>"), index):
                        code_positions[index + 1] = 1
                        frame.update(operand=text[index:index + 2] == "=>", token=text[index:index + 2])
                        index += 2
                        continue
                if not character.isspace():
                    frame["token"] = character
                    frame["control"] = ""
            index += 1
            continue
        comments.append((index, end))
        for position in range(index, end):
            if output[position] not in "\r\n":
                output[position] = " "
        index = end
    if invalid or len(contexts) != 1:
        # An uncertain context must not hide the rest of the file: fall back to masking comments only.
        masked, code, comments = mask_fallback(text)
        return masked, code, comments, has_markup
    comment_masked = "".join(output)
    code = "".join(
        character if code_positions[position] or character in "\r\n" else " "
        for position, character in enumerate(comment_masked)
    )
    return comment_masked, code, comments, has_markup


def mask(text: str, path: str = "file.tsx") -> str:
    """The code-only view of a text: what pattern matching sees."""
    return Source(path, text).code


# ---------------------------------------------------------------------------
# Manifests, workspaces, and versions


def workspace_patterns(root: Path) -> tuple[list[str], bool]:
    """Workspace globs from package.json and pnpm-workspace.yaml, and whether any workspace list is declared."""
    package = load_jsonc(root / "package.json")
    patterns = package.get("workspaces")
    if isinstance(patterns, dict):
        patterns = patterns.get("packages")
    declared = isinstance(patterns, list)
    globs = [item for item in patterns if isinstance(item, str)] if isinstance(patterns, list) else []
    text = read_text(root / "pnpm-workspace.yaml").replace("\r\n", "\n")
    key = re.search(r"(?m)^packages[ \t]*:[ \t]*(.*)$", text)
    if key:
        declared = True
        inline = key.group(1).split("#", 1)[0].strip()
        if inline.startswith("["):
            closing = text.find("]", key.start(1))
            items = text[key.start(1) + text[key.start(1):].index("[") + 1:closing if closing != -1 else len(text)].split(",")
        else:
            items = []
            for line in text[key.end():].split("\n")[1:]:
                item = re.match(r"[ \t]*-[ \t]*(.*)$", line)
                if item:
                    items.append(item.group(1))
                elif line.strip() and not line.strip().startswith("#"):
                    break
        globs += [item.split("#", 1)[0].strip().strip("'\"") for item in items]
    return [item.strip() for item in globs if item.strip()], declared


def workspaces(root: Path, files: list[str] | None = None) -> list[Path]:
    """Workspace package folders, matched against the folders Git lists, so node_modules is never walked."""
    if files is None:
        files = listed_files(root)
    candidates = sorted({
        folder(path) for path in files
        if Path(path).name == "package.json" and folder(path)
        and not NON_SOURCE_PATTERN.search(folder(path) + "/") and "node_modules" not in path.split("/")
    })
    patterns, declared = workspace_patterns(root)
    if not declared:
        # No workspace list: every nested package counts, as in a repository with frontend/ and backend/ folders.
        return [(root / candidate).resolve() for candidate in candidates]
    include: list[str] = []
    exclude: list[str] = []
    for pattern in patterns:
        negated = pattern.startswith("!")
        pattern = pattern[1:].strip() if negated else pattern
        while pattern.startswith("./"):
            pattern = pattern[2:]
        pattern = pattern.rstrip("/")
        # Only relative patterns inside the repository: no empty, absolute, or parent patterns.
        if not pattern or pattern.startswith(("/", "~")) or ".." in pattern.split("/") or re.match(r"^[A-Za-z]:", pattern):
            continue
        (exclude if negated else include).append(glob_regex(pattern))
    found = [
        candidate for candidate in candidates
        if any(re.fullmatch(expression, candidate) for expression in include)
        and not any(re.fullmatch(expression, candidate) for expression in exclude)
    ]
    return [(root / candidate).resolve() for candidate in found]


DEPENDENCY_FIELDS = ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies")


class Package:
    def __init__(self, root: Path, directory: Path) -> None:
        self.directory = directory
        self.path = relative(root, directory) if directory != root.resolve() else ""
        self.manifest_path = f"{self.path}/package.json" if self.path else "package.json"
        self.raw = read_text(directory / "package.json")
        self.data = load_jsonc(directory / "package.json")

    def spec(self, name: str, fields: tuple[str, ...] = DEPENDENCY_FIELDS) -> str | None:
        for field in fields:
            values = self.data.get(field)
            if isinstance(values, dict) and isinstance(values.get(name), str):
                return values[name]
        return None

    def declares(self, name: str) -> bool:
        return self.spec(name) is not None

    def declares_prefix(self, prefix: str) -> list[str]:
        names = []
        for field in DEPENDENCY_FIELDS:
            values = self.data.get(field)
            if isinstance(values, dict):
                names += [name for name in values if isinstance(name, str) and name.startswith(prefix)]
        return sorted(set(names))

    def quote_dependency(self, name: str) -> str | None:
        return quotable(self.raw, f'"{name}"')

    def contains(self, path: str) -> bool:
        return not self.path or path.startswith(self.path + "/")


class Manifests:
    """Every package.json in the root and its workspaces."""

    def __init__(self, root: Path, files: list[str] | None = None) -> None:
        self.root = root
        directories = [root.resolve(), *workspaces(root, files)]
        self.packages = [Package(root, directory) for directory in directories if (directory / "package.json").is_file()]

    def owner(self, path: str) -> Package | None:
        """The deepest package whose folder contains the file."""
        candidates = [package for package in self.packages if package.contains(path)]
        return max(candidates, key=lambda package: len(package.path)) if candidates else None

    def declares(self, name: str) -> list[Package]:
        return [package for package in self.packages if package.declares(name)]


VERSION_NUMBER = re.compile(r"(\d+)(?:\.(\d+|x|\*))?(?:\.(\d+|x|\*))?")


def lowest_version(spec: str) -> tuple[int, int] | None:
    """The lowest major.minor a range admits, or None when the range names no version."""
    spec = spec.strip()
    if spec.startswith("npm:"):
        spec = spec.rsplit("@", 1)[-1]
    if not spec or spec.startswith(("workspace:", "catalog:", "file:", "link:", "git", "http", "github:")) or spec in ("*", "latest", "next", "canary", "rc", "beta", "x"):
        return None
    found = []
    for alternative in spec.split("||"):
        match = VERSION_NUMBER.search(alternative)
        if match:
            major = int(match.group(1))
            minor = int(match.group(2)) if match.group(2) and match.group(2).isdigit() else 0
            if re.match(r"\s*<", alternative):
                # Only an upper bound: the range admits the previous major, so gate as that.
                major, minor = (major, 0) if minor else (max(major - 1, 0), 0)
            found.append((major, minor))
    return min(found) if found else None


def pnpm_catalog(root: Path, spec: str, name: str) -> str | None:
    """The version a pnpm `catalog:` protocol names, from pnpm-workspace.yaml."""
    text = read_text(root / "pnpm-workspace.yaml")
    if not text:
        return None
    catalog = spec.split(":", 1)[1].strip() or "default"
    if catalog == "default":
        block = re.search(r"(?ms)^catalog:\s*\n((?:[ \t]+.*\n?)*)", text)
    else:
        named = re.search(r"(?ms)^catalogs:\s*\n((?:[ \t]+.*\n?)*)", text)
        block = None
        if named:
            block = re.search(rf"(?ms)^[ \t]+['\"]?{re.escape(catalog)}['\"]?:\s*\n((?:[ \t]{{4,}}.*\n?)*)", named.group(1))
    if not block:
        return None
    entry = re.search(rf"(?m)^[ \t]+['\"]?{re.escape(name)}['\"]?:\s*['\"]?([^'\"\n#]+)", block.group(1))
    return entry.group(1).strip() if entry else None


class Lockfiles:
    """Resolved versions from npm, pnpm, Yarn, and Bun lockfiles, read as text."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.npm: dict[str, Any] = {}
        if (root / "package-lock.json").is_file():
            try:
                value = json.loads(read_text(root / "package-lock.json", MAX_LOCKFILE_BYTES) or "{}")
            except json.JSONDecodeError:
                value = {}
            self.npm = value if isinstance(value, dict) else {}
        self.pnpm = read_text(root / "pnpm-lock.yaml", MAX_LOCKFILE_BYTES).replace("\r\n", "\n")
        self.yarn = read_text(root / "yarn.lock", MAX_LOCKFILE_BYTES).replace("\r\n", "\n")
        self.bun = read_text(root / "bun.lock", MAX_LOCKFILE_BYTES)

    def resolve(self, package: Package, name: str, spec: str) -> str | None:
        packages = self.npm.get("packages")
        if isinstance(packages, dict):
            for key in ([f"{package.path}/node_modules/{name}"] if package.path else []) + [f"node_modules/{name}"]:
                entry = packages.get(key)
                if isinstance(entry, dict) and isinstance(entry.get("version"), str):
                    return entry["version"]
        if self.pnpm:
            importer = package.path or "."
            block = re.search(rf"(?ms)^  {re.escape(importer)}:\s*\n((?:    .*\n?)*)", self.pnpm)
            if block:
                entry = re.search(rf"(?m)^      ['\"]?{re.escape(name)}['\"]?:\s*\n        specifier:.*\n        version:\s*['\"]?([\d][^\s('\"]*)", block.group(1))
                if entry:
                    return entry.group(1)
        if self.yarn:
            for match in re.finditer(r'(?m)^"?([^\s"][^\n]*?)"?:\s*\n\s+version:?\s+"?([^"\s]+)"?', self.yarn):
                descriptors = [item.strip().strip('"') for item in match.group(1).split(",")]
                if any(item in (f"{name}@{spec}", f"{name}@npm:{spec}") for item in descriptors):
                    return match.group(2)
        if self.bun:
            entry = re.search(rf'"{re.escape(name)}":\s*\[\s*"{re.escape(name)}@([^"]+)"', self.bun)
            if entry:
                return entry.group(1)
        return None


def resolved_version(root: Path, lockfiles: Lockfiles, package: Package, name: str) -> tuple[str | None, str | None, tuple[int, int] | None]:
    """(declared spec, resolved version, lowest major.minor) for one dependency of one package."""
    spec = package.spec(name)
    if spec is None:
        return None, None, None
    effective = spec
    if spec.startswith("catalog:"):
        effective = pnpm_catalog(root, spec, name) or spec
    resolved = lockfiles.resolve(package, name, spec)
    peer = package.spec(name, ("peerDependencies",))
    if peer is not None:
        # A library supports every React its peer range admits, so the lowest one gates it.
        return spec, resolved, lowest_version(peer) or (lowest_version(resolved) if resolved else None)
    version = lowest_version(resolved) if resolved else None
    return spec, resolved, version or lowest_version(effective)


# ---------------------------------------------------------------------------
# Frameworks and the React Compiler


def framework_facts(root: Path, package: Package, files: set[str]) -> dict[str, Any]:
    base = f"{package.path}/" if package.path else ""

    def has_directory(*names: str) -> str | None:
        for name in names:
            prefix = f"{base}{name}/"
            if any(path.startswith(prefix) for path in files):
                return f"{base}{name}"
        return None

    facts: dict[str, Any] = {"package": package.manifest_path, "frameworks": []}
    if package.declares("next"):
        app = has_directory("app", "src/app")
        pages = has_directory("pages", "src/pages")
        app_routes = bool(app) and any(
            re.search(r"(^|/)(page|layout|route)\.[cm]?[jt]sx?$", path) for path in files if path.startswith(app + "/")
        )
        if app_routes:
            facts["frameworks"].append("next-app-router")
            facts["appDirectory"] = app
        if pages:
            facts["frameworks"].append("next-pages-router")
            facts["pagesDirectory"] = pages
        if not app_routes and not pages:
            facts["frameworks"].append("next")
    if package.declares("@react-router/dev"):
        facts["frameworks"].append("react-router-framework")
        config = next((f"{base}react-router.config.{suffix}" for suffix in ("ts", "js", "mjs") if f"{base}react-router.config.{suffix}" in files), None)
        directory = "app"
        if config:
            match = re.search(r"""\bappDirectory\s*:\s*(['"])([^'"]+)\1""", mask_comments_only(read_text(root / config))[0])
            if match:
                directory = match.group(2).strip("./") or "app"
        facts["routeDirectory"] = f"{base}{directory}"
    elif package.declares("react-router") or package.declares("react-router-dom"):
        facts["frameworks"].append("react-router-library")
    if package.declares_prefix("@remix-run/"):
        facts["frameworks"].append("remix-2")
        facts["routeDirectory"] = f"{base}app"
    if package.declares("expo-router"):
        facts["frameworks"].append("expo-router")
        facts["expoRouterDirectory"] = has_directory("src/app", "app")
    elif package.declares("react-native") or package.declares("expo"):
        facts["frameworks"].append("react-native")
    if package.declares("gatsby"):
        facts["frameworks"].append("gatsby")
    if package.declares("waku"):
        facts["frameworks"].append("waku")
        facts["wakuPagesDirectory"] = f"{base}src/pages"
    if package.declares("react-scripts"):
        facts["frameworks"].append("create-react-app")
    if package.declares("@tanstack/react-start"):
        facts["frameworks"].append("tanstack-start")
    if package.declares("vite") and not facts["frameworks"]:
        facts["frameworks"].append("vite")
    rsc = [name for name in SERVER_COMPONENT_PACKAGES if package.declares(name)]
    facts["serverComponents"] = "next-app-router" in facts["frameworks"] or bool(rsc)
    if rsc:
        facts["serverComponentPackages"] = rsc
    return facts


COMPILER_CONFIG_PATTERN = re.compile(
    r"(^|/)(?:next\.config\.[cm]?[jt]s|vite\.config\.[cm]?[jt]s|babel\.config\.(?:[cm]?js|json)|\.babelrc(?:\.json|\.js|\.cjs)?"
    r"|rsbuild\.config\.[cm]?[jt]s|rspack\.config\.[cm]?[jt]s|webpack\.config\.[cm]?[jt]s|app\.json|app\.config\.[cm]?[jt]s"
    r"|react-router\.config\.[cm]?[jt]s|metro\.config\.[cm]?js)$"
)
COMPILER_REFERENCE = re.compile(r"""babel-plugin-react-compiler|\breactCompilerPreset\b|\breactCompiler\b""")


def conditional_context(code: str, index: int) -> bool:
    """Whether a configuration key sits inside a conditional object or a function body, whose value depends on runtime."""
    depth = 0
    position = index - 1
    while position >= 0:
        character = code[position]
        if character in ")]}":
            depth += 1
        elif character in "([{":
            if depth:
                depth -= 1
            elif character != "[":
                before = code[max(0, position - 40):position].rstrip()
                if character == "{" and re.search(r"(?:\?|&&|\|\||=>|\)|\breturn|[)\]}]\s*:)$", before):
                    return True
                if character == "(" and re.search(r"(?:\?|&&|\|\||=>|\breturn)$", before):
                    return True
        position -= 1
    return False


def read_options(block: str, facts: dict[str, Any], where: str) -> None:
    """Read `compilationMode`, `sources`, and `gating` from the start of a compiler options object."""
    closing = matching(block, 0) if block.startswith("{") else -1
    block = block[:closing + 1] if closing != -1 else block
    mode = re.search(r"""\bcompilationMode\s*:\s*(['"])(\w+)\1""", block)
    if mode:
        facts["mode"] = mode.group(2)
    elif re.search(r"\bcompilationMode\s*:", block):
        facts["unread"].append(f"{where} sets compilationMode to a value the collector cannot read")
    if re.search(r"\b(?:sources|gating)\s*:", block):
        facts["scoped"] = True


def statement_end(code: str, start: int) -> int:
    """The end of the expression starting at `start`: the first `;`, or newline, at depth zero."""
    depth = 0
    for index in range(start, len(code)):
        character = code[index]
        if character in "([{":
            depth += 1
        elif character in ")]}":
            if depth == 0:
                return index
            depth -= 1
        elif depth == 0 and (character == ";" or (character in "\r\n" and code[start:index].strip())):
            return index
    return len(code)


def exported_spans(code: str) -> list[tuple[int, int]] | None:
    """The spans of a configuration file's exported value and the top-level variables it uses, or None without an export."""
    export = re.search(r"\bexport\s+default\s+|\bmodule\s*\.\s*exports\s*=\s*", code)
    if not export:
        return None
    spans = [(export.end(), statement_end(code, export.end()))]
    variables = {
        match.group(1): (match.end(), statement_end(code, match.end()))
        for match in re.finditer(r"(?m)^[ \t]*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=(?!=)\s*", code)
    }
    seen: set[str] = set()
    for _ in range(6):
        added = False
        for start, end in list(spans):
            for name in re.findall(r"(?<![\w$.])([A-Za-z_$][\w$]*)", code[start:end].replace("...", "   ")):
                if name in variables and name not in seen:
                    seen.add(name)
                    spans.append(variables[name])
                    added = True
        if not added:
            break
    return spans


def enclosing_key(code: str, index: int) -> str | None:
    """The object key whose value encloses `index`, such as `experimental` for `experimental: { reactCompiler: true }`."""
    depth = 0
    position = index - 1
    while position >= 0:
        character = code[position]
        if character in ")]}":
            depth += 1
        elif character in "([{":
            if depth:
                depth -= 1
            elif character == "{":
                key = re.search(r"""['"]?([A-Za-z_$][\w$]*)['"]?\s*:\s*$""", code[max(0, position - 60):position])
                return key.group(1) if key else None
            else:
                return None
        position -= 1
    return None


def compiler_facts(root: Path, package: Package, files: list[str], manifests: Manifests, lockfiles: Lockfiles | None = None) -> dict[str, Any]:
    """Whether the React Compiler is on for a package's components, and in which mode.

    `enabled` is True only when a configuration file turns it on with a literal
    value, False when nothing configures it, and None when a reference exists
    but its value cannot be read.
    """
    own = [path for path in files if COMPILER_CONFIG_PATTERN.search(path) and manifests.owner(path) is package]
    # A Babel configuration at the repository root applies to every package.
    own += [path for path in files if re.fullmatch(r"babel\.config\.(?:[cm]?js|json)", path) and package.path and path not in own]
    facts: dict[str, Any] = {"package": package.manifest_path, "enabled": False, "mode": None, "evidence": [], "unread": []}
    installed = package.declares("babel-plugin-react-compiler") or any(
        other.declares("babel-plugin-react-compiler") for other in manifests.packages if not other.path
    )
    facts["pluginDeclared"] = bool(installed)
    expo_default = False
    expo = package.spec("expo")
    if expo is not None:
        version = lowest_version(expo)
        expo_default = bool(version and version[0] >= 54)
    for path in own:
        text = read_text(root / path)
        if not text:
            continue
        content = mask_comments_only(text)[0]
        lines = Lines(text)
        if path.endswith(("app.json",)):
            data = load_jsonc(root / path)
            experiments = data["expo"].get("experiments") if isinstance(data.get("expo"), dict) else None
            if isinstance(experiments, dict) and isinstance(experiments.get("reactCompiler"), bool):
                facts["enabled"] = experiments["reactCompiler"]
                facts["evidence"].append(cite_file(path, quotable(text, '"reactCompiler": true', '"reactCompiler":true') or '"reactCompiler"'))
            continue
        if path.endswith(".json") or Path(path).name.startswith(".babelrc") and not path.endswith(("js", "cjs")):
            data = load_jsonc(root / path)
            plugins = data.get("plugins") if isinstance(data.get("plugins"), list) else []
            nested = json.dumps({key: data.get(key) for key in ("overrides", "env")})
            if "react-compiler" in nested:
                # The plugin is configured only for some files or environments, so it does not cover every component.
                facts["enabled"] = True
                facts["scoped"] = True
                facts["evidence"].append(cite_file(path, "babel-plugin-react-compiler" if "babel-plugin-react-compiler" in text else "react-compiler"))
            for plugin in plugins:
                name, options = (plugin[0], plugin[1] if len(plugin) > 1 else {}) if isinstance(plugin, list) and plugin else (plugin, {})
                if isinstance(name, str) and name in ("babel-plugin-react-compiler", "react-compiler"):
                    facts["enabled"] = True
                    facts["evidence"].append(cite_file(path, "babel-plugin-react-compiler" if "babel-plugin-react-compiler" in text else "react-compiler"))
                    if isinstance(options, dict):
                        facts["mode"] = options.get("compilationMode", facts["mode"])
                        if "sources" in options or "gating" in options:
                            facts["scoped"] = True
            continue
        source = Source(path if path.endswith(SOURCE_SUFFIXES) else path + ".js", text)
        babel = Path(path).name.startswith(("babel.config", ".babelrc"))
        if babel and re.search(r"\b(?:overrides|env|test|include|exclude|only|ignore)\s*:", content):
            facts["scoped"] = True  # Babel applies the plugin to some files only
        spans = exported_spans(source.code)
        next_config = Path(path).name.startswith("next.config")
        next_version = resolved_version(root, lockfiles, package, "next")[2] if next_config and lockfiles else None
        # A plugin imported under a name counts where the exported configuration uses that name.
        for imported in re.finditer(r"""\bimport\s+([A-Za-z_$][\w$]*)\s+from\s*['"]babel-plugin-react-compiler['"]|\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*['"]babel-plugin-react-compiler['"]""", content):
            name = imported.group(1) or imported.group(2)
            for start, end in spans or []:
                use = re.search(rf"(?<![\w$.]){re.escape(name)}(?![\w$])", source.code[start:end])
                if use:
                    use_line = lines.number(start + use.start())
                    facts["enabled"] = True
                    facts["evidence"].append(cite(path, use_line, name))
                    break
        for match in COMPILER_REFERENCE.finditer(content):
            line = lines.number(match.start())
            token = match.group(0)
            after = content[match.end():match.end() + 400]
            if re.match(r"[ \t]*(?:import|export\s+\{)", content[content.rfind("\n", 0, match.start()) + 1:match.start()]) or (
                    token == "babel-plugin-react-compiler" and re.search(r"\bfrom\s*$|\brequire\s*\(\s*$", content[max(0, match.start() - 20):match.start() - 1])):
                continue  # an import statement, not configuration
            if token == "reactCompilerPreset" and not re.match(r"\s*\(", after):
                continue  # named but never called
            if spans is None:
                facts["unread"].append(f"{path}:{line} has no export the collector can follow")
                continue
            if not any(start <= match.start() < end for start, end in spans):
                continue  # in a value the exported configuration never uses
            if conditional_context(source.code, match.start()):
                facts["unread"].append(f"{path}:{line} configures the compiler inside a condition or a function, which the collector does not evaluate")
                continue
            if token == "reactCompiler" and next_config:
                # Next.js 16 reads the top-level key; Next.js 15 reads only experimental.reactCompiler.
                nested = enclosing_key(source.code, match.start()) == "experimental"
                if next_version is None:
                    facts["unread"].append(f"{path}:{line} sets reactCompiler, but the Next.js version, which decides where it is read, is unknown")
                    continue
                if nested != (next_version < (16, 0)):
                    facts["unread"].append(
                        f"{path}:{line} sets {'experimental.' if nested else 'top-level '}reactCompiler, which Next.js "
                        f"{next_version[0]} does not read"
                    )
                    continue
            if token == "reactCompiler":
                value = re.match(r"""\s*[:=]\s*(true|false|\{)""", after)
                if not value:
                    if re.match(r"\s*[:=](?!=)", after):
                        facts["unread"].append(f"{path}:{line} sets reactCompiler to a value the collector cannot read")
                    continue
                if value.group(1) == "false":
                    facts["evidence"].append(cite(path, line, fragment(lines.line(line))))
                    continue
                facts["enabled"] = True
                facts["evidence"].append(cite(path, line, fragment(lines.line(line)) or "reactCompiler"))
                if value.group(1) == "{":
                    start_block = match.end() + after.index("{")
                    read_options(content[start_block:start_block + 400], facts, f"{path}:{line}")
                continue
            facts["enabled"] = True
            facts["evidence"].append(cite(path, line, token))
            # Options follow the plugin name (`["babel-plugin-react-compiler", options]`) or fill the preset call.
            options = re.match(r"""\s*['"]?\s*,\s*(\{|[A-Za-z_$][\w$]*)|\s*\(\s*(\{|[A-Za-z_$][\w$]*)""", after)
            if options:
                value = options.group(1) or options.group(2)
                if value == "{":
                    block_start = match.end() + options.end() - 1
                    read_options(content[block_start:block_start + 400], facts, f"{path}:{line}")
                else:
                    declared = re.search(rf"""\b(?:const|let|var)\s+{re.escape(value)}\s*(?::[^=]+)?=\s*\{{""", content)
                    if declared:
                        read_options(content[declared.end() - 1:declared.end() + 400], facts, f"{path}:{line}")
                    else:
                        facts["unread"].append(f"{path}:{line} passes compiler options in `{value}`, which the collector cannot read")
    if facts["enabled"] is False and expo_default and not any("reactCompiler" in entry for entry in facts["evidence"]):
        # Expo SDK 54 and later enable the compiler in the default template; the app.json flag records it.
        facts["note"] = "Expo SDK 54 or later: the default template enables the compiler in app.json."
    if facts["enabled"] is False and facts["unread"]:
        facts["enabled"] = None
    facts["everywhere"] = facts["enabled"] is True and facts["mode"] in (None, "infer", "all") and not facts.get("scoped") and not facts["unread"]
    return facts


# ---------------------------------------------------------------------------
# ESLint, Biome, and Oxlint: which hook rules apply to component files


class Layer:
    """One configuration object: where it applies, and the react-hooks rule levels it sets."""

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
        if character in "?*+@!" and pattern.startswith("(", index + 1):
            closing = pattern.find(")", index + 2)
            if closing != -1:
                alternatives = "|".join(glob_regex(part) for part in pattern[index + 2:closing].split("|"))
                if character == "!":
                    output += f"(?:(?!(?:{alternatives})(?:/|$))[^/]*)"
                else:
                    output += f"(?:{alternatives})" + {"?": "?", "*": "*", "+": "+", "@": ""}[character]
                index = closing + 1
                continue
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


class PresetCatalog:
    """The react-hooks rules each known shared configuration enables, by the installed version.

    eslint-plugin-react-hooks 7 puts the React Compiler rules in `recommended`
    and `recommended-latest`. In 6.1, `recommended` became a flat configuration
    with the two classic rules, `recommended-legacy` the eslintrc one, and
    `recommended-latest` gained the compiler rules; a 6.1 eslintrc that extends
    `plugin:react-hooks/recommended` does not load, so it is unknown. 6.0 and
    earlier enable the two classic rules in every preset. eslint-config-next 16
    and eslint-config-expo 57 spread the 7.x `recommended` rules; earlier
    versions spread or extend the 5.x ones. An unresolved version is read as the
    latest and recorded, so the result can be degraded.
    """

    def __init__(self, versions: dict[str, tuple[int, int] | None]) -> None:
        self.versions = versions
        self.assumed: set[str] = set()

    def version(self, package: str) -> tuple[int, int]:
        version = self.versions.get(package)
        if version is None:
            self.assumed.add(package)
            return (99, 0)
        return version

    def major(self, package: str) -> int:
        return self.version(package)[0]

    def hooks_preset(self, name: str, legacy: bool = False) -> set[str] | None:
        """The rules a react-hooks preset enables, or None when that preset does not load in this format and version."""
        name = name.split("/")[-1]
        version = self.version("eslint-plugin-react-hooks")
        full = set(REQUIRED_HOOK_RULES)
        core = set(CORE_HOOK_RULES)
        if version >= (7, 0):
            return full if name in ("recommended", "recommended-latest", "recommended-legacy") else set()
        if version >= (6, 1):
            if legacy and name in ("recommended", "recommended-latest"):
                return None
            if name in ("recommended-latest", "recommended-latest-legacy"):
                return full
            return core if name in ("recommended", "recommended-legacy") else set()
        return core if name in ("recommended", "recommended-legacy", "recommended-latest") else set()

    def next_preset(self) -> set[str]:
        return set(REQUIRED_HOOK_RULES) if self.major("eslint-config-next") >= 16 else set(CORE_HOOK_RULES)

    def expo_preset(self) -> set[str]:
        return set(REQUIRED_HOOK_RULES) if self.major("eslint-config-expo") >= 57 else set(CORE_HOOK_RULES)


def preset_layer(base: str, rules: set[str], label: str, evidence: str, plugin: str = "react-hooks") -> Layer:
    layer = Layer(base, rules={f"{plugin}/{rule}": "error" for rule in rules})
    layer.keys.add("rules")
    layer.presets.append(label)
    if evidence:
        layer.evidence.append(evidence)
    return layer


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


PRESET_EXPRESSION = re.compile(
    r"""([A-Za-z_$][\w$]*)\s*\.\s*configs\s*(?:\.\s*flat\s*)?(?:\[\s*(['"])([^'"]+)\2\s*\]|\.\s*([\w-]+))"""
)


class FlatConfig:
    """Read an `eslint.config.*` file as text and list its configuration objects in order.

    Only objects reachable from the default export count. Anything that could
    change a react-hooks rule but cannot be read, such as a shared
    configuration package or a helper function, is recorded as unresolved.
    """

    def __init__(self, path: str, text: str, presets: PresetCatalog, resolver: Resolver | None = None,
                 root: Path | None = None, nesting: int = 0) -> None:
        self.path = path
        self.base = folder(path)
        self.presets = presets
        self.resolver = resolver
        self.root = root
        self.nesting = nesting
        self.imported: dict[str, str] = {}
        source = Source(path if path.endswith(SOURCE_SUFFIXES) else path + ".js", text)
        self.content = source.comment_masked
        # The code view, with string delimiters kept so a string value is still an expression.
        self.structure = "".join(
            original if original in "'\"`" else code for original, code in zip(self.content, source.code)
        )
        self.unresolved: list[str] = []
        self.bindings: dict[str, str] = {}
        for match in re.finditer(r"""\bimport\s+(?P<clause>[^;]*?)\s+from\s*(['"])(?P<source>[^'"]+)\2""", self.content):
            clause = match.group("clause")
            for name in re.findall(r"(?:^|,)\s*([A-Za-z_$][\w$]*)\s*(?=,|$)", clause) + re.findall(r"\*\s*as\s+([A-Za-z_$][\w$]*)", clause):
                self.bindings[name] = match.group("source")
                self.imported[name] = "default"
            braced = re.search(r"\{([^}]*)\}", clause)
            for item in (braced.group(1).split(",") if braced else []):
                if item.strip():
                    imported, _, local = item.partition(" as ")
                    self.bindings[(local or imported).strip()] = match.group("source")
                    self.imported[(local or imported).strip()] = imported.strip()
        for match in re.finditer(r"""\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*require\s*\(\s*(['"])([^'"]+)\2\s*\)""", self.content):
            self.bindings[match.group(1)] = match.group(3)
            self.imported[match.group(1)] = "default"
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

    def package_preset(self, name: str, expression: str) -> list[Layer] | None:
        """Layers for a binding imported from a package whose react-hooks settings are known, or None."""
        source = self.bindings.get(name, "")
        if source in ("eslint-config-next", "eslint-config-next/core-web-vitals"):
            return [preset_layer(self.base, self.presets.next_preset(), f"{source}", cite_file(self.path, expression))]
        if source in ("eslint-config-expo/flat", "eslint-config-expo/flat.js"):
            return [preset_layer(self.base, self.presets.expo_preset(), "eslint-config-expo", cite_file(self.path, expression))]
        return self.workspace_layers(name)

    def workspace_layers(self, name: str) -> list[Layer] | None:
        """Layers from a configuration imported from a file in the repository, such as a workspace `@repo/eslint-config`."""
        source = self.bindings.get(name)
        if not source or self.resolver is None or self.nesting >= 4:
            return None
        target = self.resolver.resolve(self.path, source)
        if target is None or self.root is None:
            return None
        other = FlatConfig(target, read_text(self.root / target), self.presets, self.resolver, self.root, self.nesting + 1)
        layers = other.export_layers(self.imported.get(name, "default"))
        self.unresolved += other.unresolved
        for layer in layers:
            # Flat configuration globs are relative to the configuration ESLint loads, not to the shared file.
            layer.base = self.base
        return layers

    def export_layers(self, name: str) -> list[Layer]:
        if name == "default":
            return self.layers()
        if name in self.variables:
            return self.evaluate(*self.variables[name], 0)
        self.unresolved.append(f"{self.path} has no export `{name}` the collector can read")
        return []

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
        literal = re.fullmatch(r"""(['"])([\w@./-]+)\1""", expression)
        if literal:
            # A string in a flat `extends` names a plugin's configuration, such as "react-hooks/recommended".
            value = literal.group(2)
            plugin, _, preset = value.rpartition("/")
            if plugin == "react-hooks":
                return [preset_layer(self.base, self.presets.hooks_preset(preset) or set(), f"react-hooks:{preset}", cite_file(self.path, value))]
            if plugin == "react-compiler":
                return [preset_layer(self.base, {"react-compiler"}, "react-compiler", cite_file(self.path, value), "react-compiler")]
            return []
        preset = PRESET_EXPRESSION.fullmatch(expression)
        if preset:
            source = self.bindings.get(preset.group(1), "")
            name = preset.group(3) or preset.group(4)
            if source == "eslint-plugin-react-hooks":
                return [preset_layer(self.base, self.presets.hooks_preset(name) or set(), f"react-hooks:{name}", cite_file(self.path, expression))]
            if source == "eslint-plugin-react-compiler":
                return [preset_layer(self.base, {"react-compiler"}, "react-compiler", cite_file(self.path, expression), "react-compiler")]
        call = re.match(r"([A-Za-z_$][\w$.]*)\s*\(", self.structure[start:end])
        if call and matching(self.structure, start + call.end() - 1) == end - 1:
            callee = call.group(1)
            arguments_start = start + call.end()
            owner = callee.split(".")[0]
            if owner in self.compat and callee.endswith(".config"):
                return self.compat_config(arguments_start, end - 1)
            if callee.endswith(".config") or callee in ("defineConfig", "tseslint.config"):
                return self.elements(arguments_start, end - 1, depth)
            if callee == "globalIgnores":
                values = self.strings(*split_arguments(self.structure, arguments_start, end - 1)[0], 0) if self.structure[arguments_start:end - 1].strip() else []
                layer = Layer(self.base, ignores=values or [], global_ignores=True)
                return [layer]
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
            known = self.package_preset(base, expression) if not name.group(2) else None
            if known is not None:
                return known
            if self.neutral(base) or self.bindings.get(base) in ("eslint-plugin-react-hooks", "eslint-plugin-react-compiler"):
                return []
        self.unresolved.append(f"{self.path}: `{fragment(expression, 50)}` cannot be read statically")
        return []

    def neutral(self, name: str) -> bool:
        source = self.bindings.get(name)
        return source is not None and bool(NEUTRAL_CONFIG_PACKAGE.match(source))

    def compat_config(self, start: int, end: int) -> list[Layer]:
        """`compat.config({ extends, rules })`: a legacy configuration object inside a flat one."""
        start, end = self.strip_span(start, end)
        if self.structure[start:start + 1] != "{":
            self.unresolved.append(f"{self.path}: `compat.config` is given a value the collector cannot read")
            return []
        found: list[Layer] = []
        rules = Layer(self.base)
        for item_start, item_end in split_arguments(self.structure, start + 1, matching(self.structure, start)):
            item_start, item_end = self.strip_span(item_start, item_end)
            key = re.match(r"""(['"]?)([\w$-]+)\1\s*:\s*""", self.text(item_start, item_end))
            if not key:
                if self.text(item_start, item_end).startswith("..."):
                    self.unresolved.append(f"{self.path}: `compat.config` spreads a value the collector cannot read")
                continue
            value_start = item_start + key.end()
            if key.group(2) == "extends":
                values = self.strings(value_start, item_end, 0)
                if values is None:
                    self.unresolved.append(f"{self.path}: the compat `extends` value cannot be read")
                for value in values or []:
                    found += legacy_extends(self.path, self.base, value, self.unresolved, self.presets)
            elif key.group(2) == "rules":
                rules.rules = self.rules_object(value_start, item_end, 0)
            elif key.group(2) == "overrides":
                self.unresolved.append(f"{self.path}: compat `overrides` scopes are not read")
        return found + [rules]

    def compat_extends(self, start: int, end: int) -> list[Layer]:
        found = []
        for item_start, item_end in split_arguments(self.structure, start, end):
            value = self.text(item_start, item_end).strip("'\"")
            found += legacy_extends(self.path, self.base, value, self.unresolved, self.presets)
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
                    # Layers before the copied object came from its own `extends`, which travel with the spread.
                    before += [Layer(extended.base, extended.files, extended.ignores, dict(extended.rules)) for extended in spread[:-1]]
                    for extended, original in zip(before[len(before) - len(spread) + 1:], spread[:-1]):
                        extended.presets, extended.evidence, extended.keys = list(original.presets), list(original.evidence), set(original.keys)
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

    def preset_rules(self, expression: str) -> dict[str, str] | None:
        """The rules of `<plugin>.configs.<preset>.rules`, or None when it is not a react-hooks preset."""
        source = re.fullmatch(PRESET_EXPRESSION.pattern + r"""\s*\.\s*rules""", expression)
        if not source:
            return None
        package = self.bindings.get(source.group(1), "")
        name = source.group(3) or source.group(4)
        if package == "eslint-plugin-react-hooks":
            return preset_layer(self.base, self.presets.hooks_preset(name) or set(), "", "").rules
        if package == "eslint-plugin-react-compiler":
            return {"react-compiler/react-compiler": "error"}
        return None

    def rules_object(self, start: int, end: int, depth: int) -> dict[str, str]:
        start, end = self.strip_span(start, end)
        if self.structure[start] != "{":
            expression = self.text(start, end)
            if expression in self.variables:
                return self.rules_object(*self.variables[expression], depth + 1)
            known = self.preset_rules(expression)
            if known is not None:
                return known
            owner = expression.split(".")[0].strip()
            if not self.neutral(owner):
                self.unresolved.append(f"{self.path}: the rules value `{fragment(expression, 40)}` cannot be read")
            return {}
        rules: dict[str, str] = {}
        for item_start, item_end in split_arguments(self.structure, start + 1, matching(self.structure, start)):
            item = self.text(item_start, item_end)
            if item.startswith("..."):
                known = self.preset_rules(item[3:].strip())
                if known is not None:
                    rules.update(known)
                elif not self.neutral(item[3:].split(".")[0].strip()):
                    self.unresolved.append(f"{self.path}: rules spread `{fragment(item, 40)}` cannot be read")
                continue
            entry = re.match(r"""(['"])((?:react-hooks|react-compiler)/[\w-]+)\1\s*:\s*(.*)""", item, re.S)
            if entry:
                level = rule_level(entry.group(3))
                if level:
                    rules[entry.group(2)] = level
        return rules


def legacy_extends(path: str, base: str, value: str, unresolved: list[str], presets: PresetCatalog) -> list[Layer]:
    preset = re.fullmatch(r"plugin:react-hooks/([\w/-]+)", value)
    if preset:
        rules = presets.hooks_preset(preset.group(1), legacy=True)
        if rules is None:
            unresolved.append(f"{path} extends `{value}`, which eslint-plugin-react-hooks 6.1 and later ship as a flat configuration that an eslintrc file cannot load")
            return []
        return [preset_layer(base, rules, f"react-hooks:{preset.group(1)}", cite_file(path, value))]
    if value in ("expo", "eslint-config-expo"):
        return [preset_layer(base, presets.expo_preset(), "eslint-config-expo", cite_file(path, value))]
    if value == "plugin:react-compiler/recommended":
        return [preset_layer(base, {"react-compiler"}, "react-compiler", cite_file(path, value), "react-compiler")]
    if value in ("next", "next/core-web-vitals"):
        return [preset_layer(base, presets.next_preset(), f"eslint-config-{value}", cite_file(path, value))]
    if value in ("react-app", "airbnb/hooks"):
        return [preset_layer(base, set(CORE_HOOK_RULES), value, cite_file(path, value))]
    if not NEUTRAL_LEGACY_EXTENDS.match(value):
        unresolved.append(f"{path} extends `{value}`, which the collector cannot read")
    return []


def legacy_json_layers(path: str, base: str, config: dict[str, Any], unresolved: list[str], presets: PresetCatalog,
                       files: list[str] | None = None, excluded: list[str] | None = None, raw: str = "") -> list[Layer]:
    layers: list[Layer] = []
    extends = config.get("extends")
    for value in [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []:
        if isinstance(value, str):
            for layer in legacy_extends(path, base, value, unresolved, presets):
                layer.files, layer.ignores, layer.match_base = files, excluded, True
                layers.append(layer)
    rules = config.get("rules")
    if isinstance(rules, dict):
        layer = Layer(base, files=files, ignores=excluded, match_base=True)
        for rule, value in rules.items():
            if re.match(r"(?:react-hooks|react-compiler)/", str(rule)):
                level = rule_level(json.dumps(value) if not isinstance(value, str) else value)
                if level:
                    layer.rules[rule] = level
                    if f'"{rule}"' in raw:
                        layer.evidence.append(cite_file(path, f'"{rule}"'))
        layers.append(layer)
    overrides = config.get("overrides")
    for override in overrides if isinstance(overrides, list) else []:
        if isinstance(override, dict):
            patterns = override.get("files")
            patterns = [patterns] if isinstance(patterns, str) else [item for item in patterns if isinstance(item, str)] if isinstance(patterns, list) else []
            ignored = override.get("excludedFiles")
            ignored = [ignored] if isinstance(ignored, str) else [item for item in ignored if isinstance(item, str)] if isinstance(ignored, list) else []
            layers += legacy_json_layers(path, base, override, unresolved, presets, patterns or None, ignored or None, raw)
    return layers


def legacy_extends_values(content: str, unresolved: list[str] | None = None, path: str = "") -> list[str]:
    """The `extends` entries of a YAML or JavaScript legacy configuration: a list, one value, or (YAML only) a block list.

    The key is found wherever it sits, at the start of a line or after `{` or `,` on one line. A value it cannot
    read, such as a variable, is recorded as unresolved rather than read as no shared configuration.
    """
    match = re.search(r"""(?m)(?:^|[{,])[ \t]*['"]?extends['"]?[ \t]*:[ \t]*(.*)$""", content)
    if not match:
        return []
    if path.endswith((".js", ".cjs")):
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


def override_scopes(content: str) -> list[list[str]] | None:
    """The literal `files` lists of each override in a YAML or JavaScript legacy configuration, or None if one is unreadable."""
    key = re.search(r"""\boverrides['"]?\s*:""", content)
    if not key:
        return []
    rest = content[key.end():]
    scopes: list[list[str]] = []
    for match in re.finditer(r"""\bfiles['"]?[ \t]*:[ \t]*(.*)""", rest):
        value = match.group(1).strip()
        if value.startswith("["):
            closing = rest.find("]", match.start(1))
            items = re.findall(r"""(['"])([^'"]+)\1""", rest[match.start(1):closing])
            if closing == -1 or not items:
                return None
            scopes.append([item for _, item in items])
        elif value[:1] in ("'", '"'):
            scopes.append([value[1:value.find(value[0], 1)]])
        elif re.fullmatch(r"[^\s,{}\[\]'\"]+", value):
            scopes.append([value])  # an unquoted YAML value
        else:
            return None
    overrides = len(re.findall(r"(?m)^[ \t]*-[ \t]*files\b|\{\s*files\b", rest))
    return scopes if scopes and len(scopes) >= overrides else None


def legacy_text_layers(path: str, base: str, text: str, unresolved: list[str], presets: PresetCatalog,
                       samples: list[str] | None = None) -> list[Layer]:
    """YAML and JavaScript legacy configuration, read as text: shared configurations first, then rules."""
    content = mask_comments_only(text)[0] if path.endswith((".js", ".cjs")) else re.sub(r"(?m)(^|\s)#.*$", r"\1", text)
    layers: list[Layer] = []
    if path.endswith((".js", ".cjs")) and re.search(r"\bmodule\s*\.\s*exports\s*=", content) and not re.search(r"\bmodule\s*\.\s*exports\s*=\s*\{", content):
        unresolved.append(f"{path} exports something other than an object literal, which the collector cannot read")
    if path.endswith((".js", ".cjs")) and re.search(r"\.\.\.\s*[A-Za-z_$(]", content):
        unresolved.append(f"{path} spreads a value, such as a required shared configuration, that the collector cannot read")
    # Settings inside `overrides` apply only to their files, so they are not top-level settings.
    key = re.search(r"""\boverrides['"]?\s*:\s*""", content)
    overrides_start = key.start() if key else len(content)
    overrides_end = len(content)
    if key and content[key.end():key.end() + 1] == "[":
        closing = matching(content, key.end())
        overrides_end = closing if closing != -1 else len(content)
    elif key and not path.endswith((".js", ".cjs")):
        block_end = re.search(r"(?m)^\S", content[key.end():])
        overrides_end = key.end() + block_end.start() if block_end else len(content)
    top_level = content[:overrides_start] + re.sub(r"[^\r\n]", " ", content[overrides_start:overrides_end]) + content[overrides_end:]
    for entry in legacy_extends_values(top_level, unresolved, path):
        layers += legacy_extends(path, base, entry, unresolved, presets)
    layer = Layer(base)
    for match in re.finditer(r"""(['"]?)((?:react-hooks|react-compiler)/[\w-]+)\1[ \t]*:[ \t]*(?:\r?\n[ \t]*-[ \t]*)?([^\r\n,}]+)""", content):
        if overrides_start <= match.start() < overrides_end:
            continue
        level = rule_level(match.group(3))
        if level:
            layer.rules[match.group(2)] = level
            layer.evidence.append(cite_file(path, match.group(2)))
    overrides: list[Layer] = []
    if re.search(r"\boverrides\b", content):
        javascript = javascript_overrides(path, base, content, unresolved, presets) if path.endswith((".js", ".cjs")) else None
        if javascript is not None:
            overrides = javascript
        else:
            scopes = override_scopes(content)
            local = [sample[len(base) + 1:] if base else sample for sample in samples or [] if not base or sample.startswith(base + "/")]
            if scopes is None:
                unresolved.append(f"{path} has overrides whose file scopes the collector cannot read")
            elif any(glob_matches(pattern, sample, True) for scope in scopes for pattern in scope for sample in local):
                unresolved.append(f"{path} has overrides for component files, whose settings the collector does not read in this format")
    return layers + [layer] + overrides


def javascript_overrides(path: str, base: str, content: str, unresolved: list[str], presets: PresetCatalog) -> list[Layer] | None:
    """The overrides of a JavaScript legacy configuration as scoped layers, or None when one cannot be read."""
    key = re.search(r"""\boverrides['"]?\s*:\s*\[""", content)
    if not key:
        return None
    closing = matching(content, key.end() - 1)
    if closing == -1:
        return None
    layers: list[Layer] = []
    for item_start, item_end in split_arguments(content, key.end(), closing):
        item = content[item_start:item_end].strip()
        if not item.startswith("{"):
            return None
        files = re.search(r"""\bfiles['"]?\s*:\s*(\[[^\]]*\]|(['"])[^'"]*\2)""", item)
        if not files:
            return None
        patterns = [value for _, value in re.findall(r"""(['"])([^'"]+)\1""", files.group(1))]
        if not patterns:
            return None
        if re.search(r"\.\.\.\s*[A-Za-z_$(]", item):
            return None
        for entry in legacy_extends_values(item, unresolved, path):
            for layer in legacy_extends(path, base, entry, unresolved, presets):
                layer.files, layer.match_base = patterns, True
                layers.append(layer)
        scoped = Layer(base, files=patterns, match_base=True)
        for match in re.finditer(r"""(['"])((?:react-hooks|react-compiler)/[\w-]+)\1\s*:\s*([^\r\n,}]+)""", item):
            level = rule_level(match.group(3))
            if level:
                scoped.rules[match.group(2)] = level
                scoped.evidence.append(cite_file(path, match.group(2)))
        layers.append(scoped)
    return layers


def eslint_configuration(root: Path, files: list[str], manifests: Manifests, samples: list[str], presets: PresetCatalog,
                         resolver: Resolver | None = None) -> dict[str, Any]:
    """Decide which react-hooks rules apply to every sampled component file, reading configuration as text."""
    flat = [path for path in files if re.fullmatch(r"eslint\.config\.[cm]?[jt]s", Path(path).name)]
    legacy = [path for path in files if Path(path).name in ESLINT_CONFIG_NAMES and path not in flat]
    unresolved: list[str] = []
    layers_by_config: dict[str, list[Layer]] = {}
    for path in flat:
        config = FlatConfig(path, read_text(root / path), presets, resolver, root)
        layers_by_config[path] = config.layers()
        unresolved += config.unresolved
    roots: set[str] = set()
    for path in legacy:
        text = read_text(root / path)
        parsed: Any = None
        if path.endswith(".json") or Path(path).name == ".eslintrc":
            try:
                parsed = json.loads(strip_json_comments(text))
            except json.JSONDecodeError:
                parsed = None
        layers_by_config[path] = (
            legacy_json_layers(path, folder(path), parsed, unresolved, presets, raw=text) if isinstance(parsed, dict)
            else legacy_text_layers(path, folder(path), text, unresolved, presets, samples)
        )
        if (parsed.get("root") is True) if isinstance(parsed, dict) else re.search(r"""(?m)^[ \t]*['"]?root['"]?[ \t]*:[ \t]*true\b|[{,][ \t]*root[ \t]*:[ \t]*true\b""", mask_comments_only(text)[0]):
            roots.add(path)
    for package in manifests.packages:
        if isinstance(package.data.get("eslintConfig"), dict):
            layers_by_config[package.manifest_path] = legacy_json_layers(
                package.manifest_path, package.path, package.data["eslintConfig"], unresolved, presets, raw=package.raw
            )
            legacy.append(package.manifest_path)
            if package.data["eslintConfig"].get("root") is True:
                roots.add(package.manifest_path)

    def layers_for(sample: str) -> list[Layer]:
        """The nearest flat configuration, or else the cascade of legacy ones from the root down."""
        nearest = sorted((path for path in flat if not folder(path) or sample.startswith(folder(path) + "/")), key=lambda path: -len(folder(path)))
        if nearest:
            return layers_by_config[nearest[0]]
        cascade = sorted((path for path in legacy if not folder(path) or sample.startswith(folder(path) + "/")), key=lambda path: len(folder(path)))
        # A configuration with `root: true` stops ESLint from reading the ones above it.
        stops = [index for index, path in enumerate(cascade) if path in roots]
        if stops:
            cascade = cascade[stops[-1]:]
        return [layer for path in cascade for layer in layers_by_config[path]]

    effective: set[str] | None = None
    evidence: list[str] = []
    preset_names: set[str] = set()
    mentioned = False
    uncovered: list[str] = []
    for sample in samples:
        state: dict[str, str] = {}
        ignored = False
        sample_evidence: list[str] = []
        sample_presets: set[str] = set()
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
                state[rule] = level
                sample_evidence += [entry for entry in layer.evidence if entry not in sample_evidence]
            sample_presets |= set(layer.presets)
        if not ignored and state:
            # A globally ignored file is never linted, so nothing configured for it counts.
            mentioned = True
            evidence += [entry for entry in sample_evidence if entry not in evidence]
            preset_names |= sample_presets
        enabled = set() if ignored else {rule.split("/", 1)[1] for rule, level in state.items() if level != "off" and rule.startswith("react-hooks/")}
        if not ignored and state.get("react-compiler/react-compiler", "off") != "off":
            # The retired standalone compiler plugin reports what the compiler rules in react-hooks 7 now report.
            enabled |= set(COMPILER_HOOK_RULES)
        if not ({"rules-of-hooks", "exhaustive-deps"} <= enabled):
            uncovered.append(sample)
        effective = enabled if effective is None else effective & enabled
    return {
        "files": flat + legacy,
        "unresolved": sorted(set(unresolved)),
        "mentioned": mentioned,
        "presets": sorted(name for name in preset_names if name),
        "effective": sorted(effective or set()),
        "evidence": evidence[:6],
        "uncovered": uncovered,
    }


def biome_rules(root: Path, files: list[str]) -> dict[str, Any] | None:
    """The Biome equivalents of the two classic hook rules, from the root biome.json or biome.jsonc."""
    path = next((name for name in ("biome.json", "biome.jsonc") if name in files), None)
    if not path:
        return None
    data = load_jsonc(root / path)
    linter = data.get("linter") if isinstance(data.get("linter"), dict) else {}
    if linter.get("enabled") is False:
        return {"path": path, "enabled": set(), "evidence": cite_file(path, '"enabled": false')}
    rules = linter.get("rules") if isinstance(linter.get("rules"), dict) else {}
    recommended = rules.get("recommended", True) is not False
    correctness = rules.get("correctness") if isinstance(rules.get("correctness"), dict) else {}
    enabled = set()
    for biome_rule, hook_rule in (("useHookAtTopLevel", "rules-of-hooks"), ("useExhaustiveDependencies", "exhaustive-deps")):
        value = correctness.get(biome_rule)
        level = value.get("level") if isinstance(value, dict) else value
        if level is None:
            if recommended and correctness.get("recommended", True) is not False:
                enabled.add(hook_rule)
        elif level != "off":
            enabled.add(hook_rule)
    return {"path": path, "enabled": enabled, "evidence": cite_file(path)}


def hooks_lint_check(root: Path, files: list[str], manifests: Manifests, samples: list[str], lockfiles: Lockfiles,
                     resolver: Resolver | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    versions: dict[str, tuple[int, int] | None] = {}
    for name in ("eslint-plugin-react-hooks", "eslint-config-next", "eslint-config-expo"):
        declaring = manifests.declares(name)
        if declaring:
            versions[name] = resolved_version(root, lockfiles, declaring[0], name)[2]
    presets = PresetCatalog(versions)
    facts = eslint_configuration(root, files, manifests, samples, presets, resolver)
    biome = biome_rules(root, files)
    oxlint = next((path for path in files if Path(path).name in (".oxlintrc.json", "oxlintrc.json", "oxlint.config.ts")), None)
    snapshot = {
        "configurationFiles": facts["files"][:10],
        "checkedAgainst": samples[:5],
        "presets": facts["presets"],
        "effectiveRules": facts["effective"],
        "unresolved": facts["unresolved"][:5],
        "pluginVersion": ".".join(str(part) for part in versions["eslint-plugin-react-hooks"]) if versions.get("eslint-plugin-react-hooks") else None,
    }
    scope = f"the {len(samples)} component file(s) it was checked against"
    missing = [rule for rule in REQUIRED_HOOK_RULES if rule not in facts["effective"]]
    unresolved = facts["unresolved"]
    if not samples:
        return {"applicability": "not-applicable", "reason": "No component or hook files were found to lint."}, snapshot
    if missing and unresolved:
        return {
            "evaluationState": "not-evaluated",
            "reason": "ESLint configuration the collector cannot read may set the react-hooks rules: "
            + "; ".join(unresolved[:3]) + f". Rules found on for {scope}: " + (", ".join(facts["effective"]) or "none") + ".",
        }, snapshot
    if not facts["mentioned"] and not facts["files"] and biome:
        enabled = biome["enabled"]
        if not enabled:
            result = {
                "status": "missing",
                "evidence": [biome["evidence"], "command: `collect.py Biome configuration scan` → neither useHookAtTopLevel nor useExhaustiveDependencies is on"],
                "evidenceTier": "direct",
                "gap": "Biome lints the project, but its two hook rules are off, so hook-order and dependency mistakes reach production.",
                "remediation": "Turn on `correctness.useHookAtTopLevel` and `correctness.useExhaustiveDependencies` in biome.json, or add eslint-plugin-react-hooks 7 with its `recommended` configuration for component files.",
                "judgement": "consider",
            }
        else:
            result = {
                "status": "partial",
                "evidence": [biome["evidence"], f"command: `collect.py Biome configuration scan` → on: {', '.join(sorted(enabled))}; Biome has no React Compiler rules"],
                "evidenceTier": "direct",
                "gap": "Biome covers the classic hook rules but none of the React Compiler rules (purity, refs, set-state-in-effect, immutability, and the rest), so render-time mistakes the compiler would bail out on go unreported.",
                "remediation": "Run eslint-plugin-react-hooks 7 with `reactHooks.configs.flat.recommended` on component files beside Biome, scoped to the hooks plugin only.",
                "judgement": "consider",
            }
        return result, snapshot
    if not facts["mentioned"] and not facts["files"] and oxlint:
        return {
            "evaluationState": "not-evaluated",
            "reason": f"The project lints with Oxlint ({oxlint}), whose configuration the collector does not read; check by hand that `react/rules-of-hooks` (a pedantic rule, off by default) and `react/exhaustive-deps` are on.",
        }, snapshot
    if not facts["mentioned"]:
        evidence = [f"command: `collect.py ESLint configuration scan` → {len(facts['files'])} configuration file(s); no react-hooks rule applies to {scope}"]
        evidence += [cite_file(path) for path in facts["files"][:2]]
        return {
            "status": "missing",
            "evidence": evidence,
            "evidenceTier": "direct",
            "gap": "No lint configuration enables eslint-plugin-react-hooks for component files, so conditional hooks, stale effect dependencies, and the render-purity mistakes the React Compiler rules catch are committed unchecked.",
            "remediation": "Install eslint-plugin-react-hooks 7 and add `reactHooks.configs.flat.recommended` for component files (it includes rules-of-hooks, exhaustive-deps, and the React Compiler rules); run ESLint in continuous integration.",
            "judgement": "consider",
        }, snapshot
    evidence = facts["evidence"] or [f"command: `collect.py ESLint configuration scan` → react-hooks rules found for {scope}"]
    assumed = sorted(presets.assumed)
    if not missing:
        result: dict[str, Any] = {"status": "present", "evidence": evidence, "evidenceTier": "direct"}
        reasons = []
        if unresolved:
            reasons.append("Unreadable ESLint configuration may turn some of these rules off: " + "; ".join(unresolved[:2]))
        if assumed:
            reasons.append("The installed version of " + ", ".join(assumed) + " could not be resolved, so its preset was read as the latest")
        if reasons:
            result["degradedReason"] = "; ".join(reasons)
            result["evidenceTier"] = "supported"
        return result, snapshot
    core_missing = [rule for rule in CORE_HOOK_RULES if rule in missing]
    compiler_missing = [rule for rule in COMPILER_HOOK_RULES if rule in missing]
    if core_missing:
        gap = f"The hooks plugin is configured, but {', '.join('react-hooks/' + rule for rule in core_missing)} is not on for every component file"
        if facts["uncovered"]:
            gap += f" (for example {facts['uncovered'][0]})"
        gap += "."
    else:
        gap = (
            f"rules-of-hooks and exhaustive-deps are on, but {len(compiler_missing)} of the React Compiler rules in eslint-plugin-react-hooks 7's "
            f"`recommended` are not: {', '.join(compiler_missing[:6])}{', and more' if len(compiler_missing) > 6 else ''}."
        )
    return {
        "status": "partial",
        "evidence": evidence[:6],
        "evidenceTier": "direct",
        "gap": gap,
        "remediation": "Use eslint-plugin-react-hooks 7 and apply `reactHooks.configs.flat.recommended` to every component file; remove any later setting that turns one of its rules off.",
        "judgement": "consider",
    }, snapshot


# ---------------------------------------------------------------------------
# Import resolution


def tsconfig_paths(config: Path, root: Path, depth: int = 0) -> tuple[list[tuple[str, list[str]]], bool]:
    """A tsconfig's effective path aliases, following `extends`; targets are relative to the repository root."""
    if depth > 5 or not config.is_file():
        return [], False
    data = load_jsonc(config)
    aliases: list[tuple[str, list[str]]] = []
    defined = False
    extends = data.get("extends")
    for parent in [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []:
        if not isinstance(parent, str) or not parent.startswith("."):
            continue
        candidate = config.parent / parent
        target = next((item for item in (candidate, Path(f"{candidate}.json"), candidate / "tsconfig.json") if item.is_file()), None)
        if target:
            parent_aliases, parent_defined = tsconfig_paths(target, root, depth + 1)
            if parent_defined:
                aliases, defined = parent_aliases, True
    options = data.get("compilerOptions") if isinstance(data.get("compilerOptions"), dict) else {}
    base_url = (config.parent / options["baseUrl"]).resolve() if isinstance(options.get("baseUrl"), str) else None
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


# Conditions a browser bundle applies when it resolves a package's `exports`; `types` and server conditions never apply.
BROWSER_CONDITIONS = {"browser", "import", "module", "default", "require", "development", "production"}


def browser_target(value: Any) -> str | None:
    """The file a browser bundle takes from an `exports` value: the first applicable condition, in object order."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return next((target for item in value if (target := browser_target(item))), None)
    if isinstance(value, dict):
        for condition, nested in value.items():
            if condition in BROWSER_CONDITIONS:
                target = browser_target(nested)
                if target:
                    return target
    return None


def export_map(exports: Any) -> dict[str, Any] | None:
    """A package's `exports` as subpath to value, or None when it declares none."""
    if exports is None:
        return None
    if isinstance(exports, dict) and any(isinstance(key, str) and key.startswith(".") for key in exports):
        return {key: value for key, value in exports.items() if isinstance(key, str) and key.startswith(".")}
    return {".": exports}


class Resolver:
    """Resolve relative imports, tsconfig path aliases, and workspace package names to repository files."""

    def __init__(self, root: Path, files: set[str], packages: list[Package]) -> None:
        self.files = files
        self.scopes: list[tuple[str, list[tuple[str, list[str]]]]] = []
        for package in packages:
            for name in ("tsconfig.json", "tsconfig.base.json", "tsconfig.app.json", "jsconfig.json"):
                aliases, _ = tsconfig_paths(package.directory / name, root)
                if aliases:
                    self.scopes.append((package.path, aliases))
        self.scopes.sort(key=lambda scope: -len(scope[0]))
        # name -> (folder, legacy entry fields, exports map or None)
        self.packages: dict[str, tuple[str, list[str], dict[str, Any] | None]] = {}
        for package in packages:
            name = package.data.get("name")
            if isinstance(name, str) and name and package.path:
                browser = package.data.get("browser")
                entries = [browser] if isinstance(browser, str) else []
                entries += [value for field in ("module", "main", "source") if isinstance(value := package.data.get(field), str)]
                self.packages[name] = (package.path, entries, export_map(package.data.get("exports")))

    def first(self, base: str) -> str | None:
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

    def entry(self, directory: str, entry: str) -> str | None:
        entry = entry[2:] if entry.startswith("./") else entry
        found = self.first(f"{directory}/{entry}")
        if not found and entry.split("/")[0] in ("dist", "build", "lib", "out"):
            found = self.first(f"{directory}/src/{'/'.join(entry.split('/')[1:])}")
        return found

    def workspace_package(self, specifier: str) -> str | None:
        """The workspace package name a specifier imports, if any."""
        return next((name for name in self.packages if specifier == name or specifier.startswith(name + "/")), None)

    def package(self, specifier: str) -> str | None:
        name = self.workspace_package(specifier)
        if name is None:
            return None
        directory, entries, exports = self.packages[name]
        subpath = "." if specifier == name else "./" + specifier[len(name) + 1:]
        if exports is not None:
            # With `exports`, only exported subpaths resolve, through the conditions a browser bundle applies.
            value = exports.get(subpath)
            if value is None:
                for pattern, candidate in exports.items():
                    if pattern.count("*") == 1:
                        prefix, _, suffix = pattern.partition("*")
                        if subpath.startswith(prefix) and subpath.endswith(suffix) and len(subpath) >= len(prefix) + len(suffix):
                            middle = subpath[len(prefix):len(subpath) - len(suffix) if suffix else None]
                            target = browser_target(candidate)
                            return self.entry(directory, target.replace("*", middle)) if target else None
                return None
            target = browser_target(value)
            return self.entry(directory, target) if target else None
        if subpath == ".":
            for entry in entries:
                found = self.entry(directory, entry)
                if found:
                    return found
            return self.first(f"{directory}/src/index") or self.first(f"{directory}/index")
        rest = subpath[2:]
        return self.first(f"{directory}/src/{rest}") or self.first(f"{directory}/{rest}")

    def resolve(self, importer: str, specifier: str) -> str | None:
        if specifier.startswith("."):
            return self.first(f"{folder(importer)}/{specifier}" if folder(importer) else specifier)
        for scope, aliases in self.scopes:
            if scope and not importer.startswith(scope + "/"):
                continue
            for pattern, targets in aliases:
                if "*" in pattern:
                    prefix, _, suffix = pattern.partition("*")
                    if specifier.startswith(prefix) and specifier.endswith(suffix):
                        middle = specifier[len(prefix): len(specifier) - len(suffix) if suffix else None]
                        for target in targets:
                            found = self.first(target.replace("*", middle))
                            if found:
                                return found
                elif specifier == pattern:
                    for target in targets:
                        found = self.first(target)
                        if found:
                            return found
        return self.package(specifier)

    def looks_local(self, specifier: str) -> bool:
        """Whether an unresolved specifier probably names a repository file rather than a package."""
        return specifier.startswith((".", "@/", "~/", "#")) or self.workspace_package(specifier) is not None or any(
            "*" in pattern and specifier.startswith(pattern.partition("*")[0]) for _, aliases in self.scopes for pattern, _ in aliases
        )


def is_type_only(clause: str) -> bool:
    """`import type {A}`, or a braced list in which every binding is `type X`."""
    clause = " ".join(clause.split())
    if clause.startswith(("type ", "type{")):
        return True
    braced = re.fullmatch(r"\{(.*)\}", clause)
    if not braced:
        return False
    names = [name.strip() for name in braced.group(1).split(",") if name.strip()]
    return bool(names) and all(name.startswith("type ") for name in names)


class Import:
    def __init__(self, specifier: str, line: int, clause: str, kind: str) -> None:
        self.specifier = specifier
        self.line = line
        self.clause = clause
        self.kind = kind  # static, side-effect, or dynamic

    def bindings(self) -> dict[str, str]:
        """Local name to imported name for a static import clause."""
        found: dict[str, str] = {}
        clause = " ".join(self.clause.split())
        default = re.match(r"^(?:type\s+)?([A-Za-z_$][\w$]*)\s*(?:,|$)", clause)
        if default and default.group(1) != "type":
            found[default.group(1)] = "default"
        braced = re.search(r"\{([^}]*)\}", clause)
        for item in (braced.group(1).split(",") if braced else []):
            item = item.strip()
            if not item or item.startswith("type "):
                continue
            imported, _, local = item.partition(" as ")
            found[(local or imported).strip()] = imported.strip()
        return found


def imports_of(source: Source) -> list[Import]:
    """Runtime imports in a file: static, side-effect, and dynamic; type-only imports are left out."""
    found: list[Import] = []
    text = source.comment_masked
    code = source.code
    for pattern, kind in ((STATIC_IMPORT, "static"), (SIDE_EFFECT_IMPORT, "side-effect"), (DYNAMIC_IMPORT, "dynamic")):
        for match in pattern.finditer(text):
            start = match.start() + (len(match.group(0)) - len(match.group(0).lstrip()))
            if not code[start:start + 1].strip():
                continue  # inside a string, template, or JSX text
            clause = match.groupdict().get("clause") or ""
            if kind == "static" and is_type_only(clause):
                continue
            found.append(Import(match.group("specifier"), source.line_of(match.start("specifier")), clause, kind))
    return found


# ---------------------------------------------------------------------------
# Directives, exports, and components


def directives(source: Source) -> list[tuple[str, int]]:
    """The directive prologue of a file: string statements before any other code, comments allowed."""
    found: list[tuple[str, int]] = []
    text = source.comment_masked
    index = 1 if text.startswith("\ufeff") else 0
    while True:
        while index < len(text) and text[index].isspace():
            index += 1
        match = DIRECTIVE.match(text, index)
        if not match:
            return found
        after = match.end()
        rest = text[after:after + 200].lstrip(" \t")
        if rest and not rest.startswith(("\n", "\r")) and match.group(0).rstrip()[-1:] != ";" and rest[0] not in "'\"":
            return found  # an expression that starts with a string, not a directive
        found.append((match.group(2), source.line_of(index)))
        index = after


MISPLACED_DIRECTIVE = re.compile(r"""(?m)^[ \t]*(['"])use (client|server)\1[ \t]*;?[ \t]*\r?$""")


def misplaced_directives(source: Source, prologue: list[tuple[str, int]]) -> list[tuple[str, int]]:
    """Module-level `'use client'` or `'use server'` strings that sit below code, where React ignores them."""
    found = []
    effective_lines = {line for _, line in prologue}
    depth = 0
    scanned = 0
    for match in MISPLACED_DIRECTIVE.finditer(source.comment_masked):
        # The brace depth is carried forward from the previous match, so the scan stays linear.
        for character in source.code[scanned:match.start()]:
            if character == "{":
                depth += 1
            elif character == "}":
                depth = max(0, depth - 1)
        scanned = match.start()
        line = source.line_of(match.start(1))
        if line in effective_lines:
            continue
        # Inside a function body it is a Server Function directive, which is valid: only top-level ones are flagged.
        if match.start() > 0 and depth == 0:
            found.append((f"use {match.group(2)}", line))
    return found


def is_component_file(path: str, source: Source) -> bool:
    return path.endswith((".tsx", ".jsx")) or (path.endswith((".js", ".mjs")) and source.has_markup)


def in_tooling_folder(path: str, package_roots: list[str]) -> bool:
    """Whether a file sits in a `scripts/`, `tools/`, or `tooling/` folder at the repository root or a workspace package's root."""
    for base in ["", *package_roots]:
        local = path[len(base) + 1:] if base and path.startswith(base + "/") else (path if not base else None)
        if local is not None and local.split("/", 1)[0] in TOOLING_FOLDERS and "/" in local:
            return True
    return False


NEXT_SPECIAL_FILES = re.compile(
    r"(^|/)(page|layout|template|default|not-found|global-not-found|error|global-error|loading|forbidden|unauthorized"
    r"|opengraph-image|twitter-image|icon|apple-icon|sitemap|robots|manifest)\.[cm]?[jt]sx?$"
)


def framework_required_default(path: str, frameworks: list[dict[str, Any]], route_files: set[str], lazy_targets: set[str]) -> str | None:
    """Why a file must default-export, or None. Framework conventions and lazy-loading targets require it."""
    if path in lazy_targets:
        return "a React.lazy or next/dynamic target"
    if path in route_files:
        return "a route module listed in routes.ts"
    for facts in frameworks:
        app = facts.get("appDirectory")
        if app and path.startswith(app + "/") and NEXT_SPECIAL_FILES.search(path):
            return "a Next.js App Router file"
        pages = facts.get("pagesDirectory")
        if pages and path.startswith(pages + "/"):
            return "a Next.js Pages Router page"
        if "next" in "".join(facts["frameworks"]) and re.search(r"(^|/)(?:src/)?(?:proxy|middleware)\.[cm]?[jt]s$", path):
            return "a Next.js proxy or middleware file"
        routes = facts.get("routeDirectory")
        if routes and (path.startswith(routes + "/routes/") or re.fullmatch(re.escape(routes) + r"/(?:root|entry\.client|entry\.server)\.[cm]?[jt]sx?", path)):
            return "a React Router or Remix route module"
        expo = facts.get("expoRouterDirectory")
        if expo and path.startswith(expo + "/"):
            return "an Expo Router route"
        if "gatsby" in facts["frameworks"] and re.search(r"(^|/)src/(?:pages|templates)/", path):
            return "a Gatsby page or template"
        waku = facts.get("wakuPagesDirectory")
        if waku and path.startswith(waku + "/"):
            return "a Waku page or layout"
    return None


def route_module_files(root: Path, frameworks: list[dict[str, Any]], files: set[str]) -> set[str]:
    """Files named in a React Router `routes.ts` with route(), index(), or layout()."""
    found: set[str] = set()
    for facts in frameworks:
        routes = facts.get("routeDirectory")
        if not routes:
            continue
        for suffix in ("ts", "js", "mts", "mjs"):
            config = f"{routes}/routes.{suffix}"
            if config in files:
                text = mask_comments_only(read_text(root / config))[0]
                for match in re.finditer(r"""\b(?:route|index|layout)\s*\(\s*(?:(['"])[^'"]*\1\s*,\s*)?(['"])([^'"]+\.[cm]?[jt]sx?)\2""", text):
                    target = f"{routes}/{match.group(3).lstrip('./')}"
                    if target in files:
                        found.add(target)
    return found


LAZY_IMPORT = re.compile(r"""\b(?:React\s*\.\s*)?(?:lazy|dynamic)\s*\(\s*(?:async\s*)?\(\s*\)\s*=>\s*import\s*\(\s*(['"])([^'"]+)\1""")


# ---------------------------------------------------------------------------
# Effects, state, and suppressions


EFFECT_CALL = re.compile(r"(?<![\w$.])(?:React\s*\.\s*)?(use(?:Layout|Insertion)?Effect)\s*\(")
STATE_CALL = re.compile(
    r"\b(?:const|let)\s*\[\s*([A-Za-z_$][\w$]*)\s*(?:,\s*([A-Za-z_$][\w$]*)\s*)?\]\s*=\s*(?:React\s*\.\s*)?useState\s*(?:<[^()]*?>)?\s*\("
)
NESTED_FUNCTION = re.compile(r"=>\s*\{|\bfunction\b[^{(]*\([^)]*\)\s*\{")
SUBSCRIPTION = re.compile(
    r"\b(?:addEventListener|setInterval|setTimeout|requestAnimationFrame|subscribe|observe|listen|watch)\s*\("
    r"|\bnew\s+(?:WebSocket|EventSource|IntersectionObserver|ResizeObserver|MutationObserver|BroadcastChannel|Worker)\b"
    r"|\.\s*on\s*\("
)
ASYNC_WORK = re.compile(r"\bfetch\s*\(|\baxios\b|\bawait\b|\.\s*then\s*\(|\b(?:fetch|load|get|request|query)[A-Z]\w*\s*\(")
RACE_GUARD = re.compile(r"\bAbortController\b|\bsignal\b|\b(?:ignore|cancell?ed|active|stale|isMounted|mounted|current)\b")
SIDE_EFFECT_MEMBERS = {
    "addEventListener", "removeEventListener", "subscribe", "observe", "then", "catch", "json", "focus", "blur",
    "scrollIntoView", "scrollTo", "play", "pause", "postMessage", "send", "open", "close", "setItem", "getItem",
    "removeItem", "push", "replace", "track", "log", "info", "warn", "error", "emit", "dispatch", "connect",
}
CALL_KEYWORDS = {"if", "for", "while", "switch", "return", "typeof", "catch", "function", "await", "new", "void"}


def blank_nested_functions(code: str, start: int, end: int, source: Source) -> str:
    """The body text with the bodies of functions nested inside it blanked, so only its own statements remain."""
    body = list(code[start:end])
    for match in NESTED_FUNCTION.finditer(code, start, end):
        opening = match.end() - 1
        closing = source.close(opening)
        if closing == -1 or closing > end:
            continue
        for position in range(opening + 1 - start, closing - start):
            if body[position] != "\n":
                body[position] = " "
    return "".join(body)


def preceding(text: str, position: int) -> str:
    """The last non-space character before `position`, looking back at most a line."""
    index = position - 1
    while index >= 0 and position - index < 200 and text[index].isspace():
        index -= 1
    return text[index] if index >= 0 else ""


def effects_in(source: Source, setters: set[str]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    code = source.code
    # Functions from useEffectEvent are meant to be called from effects; they are not parent callbacks.
    effect_events = set(re.findall(r"\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:React\s*\.\s*)?useEffectEvent\s*\(", code))
    for match in EFFECT_CALL.finditer(code):
        if len(found) >= MAX_EFFECTS_PER_FILE:
            break
        opening = match.end() - 1
        closing = source.close(opening)
        if closing == -1:
            continue
        arguments = split_arguments(code, opening + 1, closing)
        if not arguments:
            continue
        callback = code[arguments[0][0]:arguments[0][1]]
        arrow = re.match(r"\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>\s*", callback)
        function = re.match(r"\s*(?:async\s+)?function\b[^(]*\([^)]*\)\s*", callback)
        prefix = arrow or function
        if not prefix:
            continue
        body_start = arguments[0][0] + prefix.end()
        if code[body_start:body_start + 1] == "{":
            body_end = source.close(body_start)
            if body_end == -1:
                continue
            inner_start, inner_end = body_start + 1, body_end
        else:
            inner_start, inner_end = body_start, arguments[0][1]
        body = code[inner_start:inner_end]
        own = blank_nested_functions(code, inner_start, inner_end, source)
        dependencies = "none" if len(arguments) < 2 else " ".join(code[arguments[1][0]:arguments[1][1]].split())
        # A concise body returns its value, which may be a cleanup such as an unsubscribe function.
        cleanup = bool(re.search(r"\breturn\b\s*(?![\s;}]|$)", own)) or code[body_start:body_start + 1] != "{"
        flags = []
        calls = [(call.group(1), call.start()) for call in re.finditer(r"(?<![\w$])([A-Za-z_$][\w$]*)\s*(?:\?\.\s*)?\(", own) if call.group(1) not in CALL_KEYWORDS]
        free_calls = [name for name, position in calls if preceding(own, position) != "."]
        member_calls = [name for name, position in calls if preceding(own, position) == "."]
        setter_calls = [name for name in free_calls if name in setters]
        callbacks = [name for name in free_calls if re.fullmatch(r"on[A-Z]\w*", name) and name not in effect_events]
        prop_callbacks = re.findall(r"\bprops\s*(?:\?\.|\.)\s*(on[A-Z]\w*)\s*(?:\?\.\s*)?\(", own)
        callbacks += prop_callbacks
        member_calls = [name for name in member_calls if name not in prop_callbacks]
        async_work = bool(ASYNC_WORK.search(body))
        subscribes = bool(SUBSCRIPTION.search(own))
        if (setter_calls or callbacks) and not async_work and not subscribes and set(free_calls) <= set(setter_calls) | set(callbacks) | effect_events | {"Boolean", "Number", "String"} and not set(member_calls) & SIDE_EFFECT_MEMBERS and not re.search(r"\b(?:window|document|localStorage|sessionStorage|navigator)\b", own):
            if setter_calls:
                flags.append("only-sets-state")
            if callbacks:
                flags.append("notifies-parent")
        if async_work and not cleanup:
            flags.append("async-without-cleanup" if not RACE_GUARD.search(body) else "async-with-guard-but-no-cleanup")
        if subscribes and not cleanup:
            flags.append("subscribes-without-cleanup")
        if dependencies == "[]" and setter_calls and not async_work and not subscribes:
            flags.append("runs-once-and-sets-state")
        if dependencies == "none":
            flags.append("no-dependency-array")
        found.append({
            "path": source.path,
            "line": source.line_of(match.start()),
            "hook": match.group(1),
            "dependencies": dependencies[:80],
            "flags": flags,
        })
    return found


def state_facts(source: Source) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
    """Setter names, boolean state groups, and state initialised from a prop, for one file."""
    code = source.code
    setters: set[str] = set()
    booleans: list[tuple[str, int]] = []
    from_props: list[dict[str, Any]] = []
    parameters: set[str] = set()
    for match in re.finditer(r"(?:\bfunction\s+[A-Z][\w$]*\s*|\b(?:const|let)\s+[A-Z][\w$]*\s*(?::[^=]+)?=\s*(?:\w+\s*\(\s*)?)\(\s*\{([^{}()]*)\}", code):
        for name in re.findall(r"(?:^|,)\s*([A-Za-z_$][\w$]*)", match.group(1)):
            parameters.add(name)
    for match in STATE_CALL.finditer(code):
        if match.group(2):
            setters.add(match.group(2))
        opening = match.end() - 1
        closing = source.close(opening)
        argument = " ".join(source.text[opening + 1:closing].split()) if closing != -1 else ""
        line = source.line_of(match.start())
        if argument in ("true", "false"):
            booleans.append((match.group(1), line))
        # A prop, or a member read from one, such as `props.user.name` or `user.name` with `user` destructured.
        root_name = re.match(r"[A-Za-z_$][\w$]*", argument)
        if re.fullmatch(r"props(?:\??\.[A-Za-z_$][\w$]*)+", argument) or (
                root_name and root_name.group(0) in parameters and re.fullmatch(r"[A-Za-z_$][\w$]*(?:\??\.[A-Za-z_$][\w$]*)*", argument)):
            from_props.append({"path": source.path, "line": line, "state": match.group(1), "initialisedFrom": argument})
    for match in re.finditer(r"\b(?:const|let)\s*\[\s*[A-Za-z_$][\w$]*\s*,\s*([A-Za-z_$][\w$]*)\s*\]\s*=\s*(?:React\s*\.\s*)?useReducer\b", code):
        setters.add(match.group(1))
    groups = []
    if len(booleans) >= 3:
        groups.append({"path": source.path, "line": booleans[0][1], "booleans": [name for name, _ in booleans][:8]})
    return setters, groups, from_props


def specific(reason: str) -> bool:
    """Whether a comment gives a reason: at least three words once markers such as TODO and FIXME are removed."""
    words = re.sub(r"(?i)\b(?:todo|fixme|xxx|hack|note)\b[:(\w)]*", " ", reason.strip("/* \r\n\t"))
    return len(re.findall(r"[A-Za-z]{2,}", words)) >= 3


SUPPRESSION = re.compile(r"^(?://|/\*)\s*\**\s*(?:eslint-disable(?:-next-line|-line)?|biome-ignore\s+lint/correctness/useExhaustiveDependencies)\b")


def suppressions_in(source: Source) -> list[dict[str, Any]]:
    """exhaustive-deps suppressions, and whether each carries a reason."""
    found = []
    text = source.text
    comment_lines = {}
    for start, end in source.comments:
        comment_lines.setdefault(source.line_of(start), []).append((start, end))
    for start, end in source.comments:
        body = text[start:end]
        directive = SUPPRESSION.match(body)
        if not directive:
            continue
        if "biome-ignore" in directive.group(0):
            explanation = body[directive.end():].strip(" */\n\t")
            reason = explanation.lstrip(":").strip()
            justified = reason != "<explanation>" and specific(reason)
        else:
            if "react-hooks/exhaustive-deps" not in body:
                continue  # a disable without rule names is a blanket disable, which /linting-audit owns
            # ESLint's own form puts the reason after ` -- `; a separate comment on the line above or the same line also counts.
            description = re.search(r"\s--\s*(.*)", body, re.S)
            justified = bool(description) and specific(description.group(1))
        line = source.line_of(start)
        if not justified:
            for neighbour in (line - 1, line):
                for other_start, other_end in comment_lines.get(neighbour, []):
                    if other_start != start and not SUPPRESSION.match(text[other_start:other_end]) and specific(text[other_start:other_end]):
                        justified = True
        whole_file = bool(re.match(r"\s*/\*\s*eslint-disable\s", body))
        quote = "react-hooks/exhaustive-deps" if "react-hooks/exhaustive-deps" in source.lines.line(line) else fragment(source.lines.line(line).strip().lstrip("/* "), 50)
        found.append({"path": source.path, "line": line, "justified": justified, "wholeFile": whole_file, "quote": quote})
    return found


# ---------------------------------------------------------------------------
# Server and client boundaries


FUNCTION_OPT_OUT = re.compile(r"""\{(\s*)(['"])use no (memo|forget)\2""")
DESTRUCTURED_ENVIRONMENT = re.compile(r"\{([^{}]*)\}\s*=\s*process\s*\.\s*env\b")
ENVIRONMENT_READ = re.compile(r"\bprocess\s*\.\s*env\s*\.\s*([A-Z_][A-Z0-9_]*)|\bprocess\s*\.\s*env\s*\[\s*['\"]([A-Z_][A-Z0-9_]*)['\"]\s*\]")


def next_inlined_environment(root: Path, files: list[str]) -> set[str]:
    """Variable names a Next.js configuration's `env` option inlines into the client bundle."""
    names: set[str] = set()
    for path in files:
        if not re.search(r"(^|/)next\.config\.[cm]?[jt]s$", path):
            continue
        source = Source(path, read_text(root / path))
        for match in re.finditer(r"\benv\s*:\s*\{", source.code):
            closing = source.close(match.end() - 1)
            if closing == -1:
                continue
            block = source.comment_masked[match.end():closing]
            names |= set(re.findall(r"""(?:^|[,{\s])['"]?([A-Za-z_][A-Za-z0-9_]*)['"]?\s*:""", block))
    return names


def server_markers(source: Source, imports: list[Import], inlined: frozenset[str] = frozenset()) -> list[tuple[int, str, str]]:
    """Lines that make a module server-only: server-only imports, Node built-ins, server packages, and private environment variables."""
    markers: list[tuple[int, str, str]] = []
    for item in imports:
        specifier = item.specifier
        bare = specifier[5:] if specifier.startswith("node:") else specifier
        if specifier.startswith("node:") or bare in NODE_BUILTINS or specifier in SERVER_PACKAGES or specifier.split("/")[0] in ("pg", "mongoose", "mongodb"):
            line_text = source.lines.line(item.line)
            markers.append((item.line, quotable(line_text, f'"{specifier}"', f"'{specifier}'", specifier) or specifier, f"imports {specifier}"))
    reads = [(match.start(), match.group(1) or match.group(2), match.start(1) if match.group(1) else match.start(2))
             for match in ENVIRONMENT_READ.finditer(source.comment_masked)]
    for match in DESTRUCTURED_ENVIRONMENT.finditer(source.comment_masked):
        for name in re.finditer(r"(?:^|,)\s*([A-Z_][A-Z0-9_]*)\b", match.group(1)):
            reads.append((match.start(), name.group(1), match.start(1) + name.start(1)))
    for start, name, name_start in reads:
        if not source.code[start:start + 1].strip():
            continue  # inside a string or template text
        if name in ALWAYS_INLINED_ENVIRONMENT or name in inlined or name.startswith(PUBLIC_ENVIRONMENT_PREFIXES):
            continue
        line = source.line_of(name_start)
        markers.append((line, name, f"reads the private environment variable {name}"))
    return sorted(set(markers))


JSX_OPENING = re.compile(r"<([A-Z][\w$]*(?:\.[A-Z][\w$]*)?)(?=[\s/>])")
SERIALISABLE_CONSTRUCTORS = {"Date", "Map", "Set", "Array", "Uint8Array", "Int8Array", "Uint16Array", "Int16Array",
                             "Uint32Array", "Int32Array", "Float32Array", "Float64Array", "BigInt64Array", "BigUint64Array",
                             "ArrayBuffer", "Promise", "String", "Number", "Boolean"}


def jsx_attributes(text: str, start: int) -> tuple[list[tuple[str, str, int]], int]:
    """The `name={expression}` attributes of the JSX opening tag starting at `start`, and where the tag ends."""
    attributes: list[tuple[str, str, int]] = []
    index = start
    depth = 0
    quote = ""
    name_start = -1
    while index < len(text):
        character = text[index]
        if quote:
            if character == quote:
                quote = ""
        elif depth == 0 and character in "'\"":
            quote = character
        elif character == "{":
            if depth == 0:
                name = re.search(r"([A-Za-z_$][\w$-]*)\s*=\s*$", text[max(start, index - 80):index])
                name_start = index if name else -1
                current_name = name.group(1) if name else ""
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0 and name_start != -1:
                attributes.append((current_name, text[name_start + 1:index], name_start))
                name_start = -1
        elif character == ">" and depth == 0 and text[index - 1] != "=":
            return attributes, index
        index += 1
    return attributes, index


def local_functions(source: Source) -> dict[str, bool]:
    """Functions declared in a file, mapped to whether each is a Server Function (`'use server'` at the top of its body)."""
    found: dict[str, bool] = {}
    text = source.comment_masked
    for match in re.finditer(r"\b(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*(?::[^{]+)?\{|\b(?:const|let)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>\s*\{?", text):
        if not source.code[match.start():match.start() + 1].strip():
            continue
        name = match.group(1) or match.group(2)
        body = text[match.end():match.end() + 200].lstrip()
        found[name] = bool(re.match(r"""(['"])use server\1""", body))
    return found


def non_serialisable(expression: str, functions: dict[str, bool], server_imports: set[str]) -> str | None:
    value = " ".join(expression.split())
    if re.match(r"^(?:async\s*)?(?:\([^()]*\)|[A-Za-z_$][\w$]*)\s*=>", value) or re.match(r"^(?:async\s+)?function\b", value):
        return "an inline function"
    if value in functions and not functions[value] and value not in server_imports:
        return f"the function `{value}`"
    constructed = re.match(r"^new\s+([A-Za-z_$][\w$.]*)", value)
    if constructed and constructed.group(1) not in SERIALISABLE_CONSTRUCTORS:
        return f"an instance of `{constructed.group(1)}`"
    return None


# ---------------------------------------------------------------------------
# History and risk


def churn(root: Path, months: int) -> Counter[str]:
    log = git(root, "-c", "core.quotePath=false", "log", f"--since={months}.months", "--no-merges", "--name-only", "--relative", "--pretty=tformat:")
    return Counter(line.strip() for line in log.splitlines() if line.strip())


# ---------------------------------------------------------------------------
# The collection


def not_applicable(reason: str) -> dict[str, Any]:
    return {"applicability": "not-applicable", "reason": reason}


def degrade(result: dict[str, Any], large: list[str]) -> dict[str, Any]:
    if large and "status" in result:
        result["degradedReason"] = (
            f"{len(large)} source file(s) over {MAX_FILE_BYTES // 1_000_000} MB were not read: " + ", ".join(large[:3])
        )
        result["evidenceTier"] = "supported"
    return result


def collect(root: Path, months: int = 6) -> dict[str, Any]:
    files = listed_files(root)
    file_set = set(files)
    manifests = Manifests(root, files)
    lockfiles = Lockfiles(root)
    checks: dict[str, Any] = {}
    snapshot: dict[str, Any] = {}

    react_packages = [package for package in manifests.packages if any(package.declares(name) for name in REACT_PACKAGES)]
    if not react_packages:
        reason = (
            "React is not a dependency of any package.json in this repository"
            + (f" ({len(manifests.packages)} manifest(s) read)" if manifests.packages else "; no package.json was found")
            + ". Record the whole audit with `audit-not-applicable`."
        )
        for check in ALL_CHECKS:
            checks[check] = not_applicable(reason)
        snapshot["reactDetected"] = False
        return {"checks": checks, "snapshot": snapshot}

    package_roots = [package.path for package in manifests.packages if package.path]
    source_paths = [path for path in files if path.endswith(SOURCE_SUFFIXES) and not path.endswith(".d.ts")]
    large = [path for path in source_paths if file_size(root / path) > MAX_FILE_BYTES]
    product_paths = [
        path for path in source_paths
        if path not in large and not NON_SOURCE_PATTERN.search(path) and not in_tooling_folder(path, package_roots)
    ]
    sources = {path: Source(path, read_text(root / path)) for path in product_paths}
    resolver = Resolver(root, set(source_paths), manifests.packages)

    # Versions, frameworks, and the compiler, per package.
    packages_facts = []
    frameworks = []
    compilers = []
    for package in react_packages:
        spec, resolved, version = resolved_version(root, lockfiles, package, "react")
        if spec is None:
            # React arrives through a framework such as Next.js or Expo; the lockfile may still name it.
            resolved = lockfiles.resolve(package, "react", "")
            version = lowest_version(resolved) if resolved else None
        facts = framework_facts(root, package, file_set)
        frameworks.append(facts)
        compiler = compiler_facts(root, package, files, manifests, lockfiles)
        compilers.append(compiler)
        packages_facts.append({
            "package": package.manifest_path,
            "react": spec,
            "resolved": resolved,
            "gatingVersion": f"{version[0]}.{version[1]}" if version else None,
            "peerRange": package.spec("react", ("peerDependencies",)),
            "frameworks": facts["frameworks"],
            "_version": version,
            "_package": package,
        })
    snapshot["reactDetected"] = True
    snapshot["packages"] = [{key: value for key, value in item.items() if not key.startswith("_")} for item in packages_facts]
    snapshot["compiler"] = [{key: value for key, value in item.items() if key != "unread" or value} for item in compilers]
    if large:
        snapshot["skippedLargeFiles"] = {"limitBytes": MAX_FILE_BYTES, "count": len(large), "examples": large[:10]}
    if any(package.declares_prefix("@remix-run/") for package in react_packages):
        snapshot["remixVersion2"] = "Remix 2 continues as React Router 7 and later; Remix 3 is a separate framework that does not use React."

    def package_of(path: str) -> dict[str, Any] | None:
        owner = manifests.owner(path)
        for item in packages_facts:
            if item["_package"] is owner:
                return item
        # A file outside every React package, such as a root-level folder in a single-package repository.
        return packages_facts[0] if len(packages_facts) == 1 else None

    # Per-file facts.
    component_files: list[str] = []
    hook_files: list[str] = []
    imports_by_file: dict[str, list[Import]] = {}
    prologues: dict[str, list[tuple[str, int]]] = {}
    effects: list[dict[str, Any]] = []
    boolean_groups: list[dict[str, Any]] = []
    from_props: list[dict[str, Any]] = []
    suppressions: list[dict[str, Any]] = []
    forward_refs: list[tuple[str, int]] = []
    ref_props = 0
    default_exports: list[tuple[str, int]] = []
    misplaced: list[tuple[str, str, int]] = []
    custom_hooks: Counter[str] = Counter()
    fan_in: Counter[str] = Counter()
    lazy_targets: set[str] = set()
    for path, source in sources.items():
        imports = imports_of(source)
        imports_by_file[path] = imports
        prologue = directives(source)
        prologues[path] = prologue
        misplaced += [(path, name, line) for name, line in misplaced_directives(source, prologue)]
        uses_react = any(item.specifier in ("react", "react-native", "preact/hooks") for item in imports) or source.has_markup
        if is_component_file(path, source):
            component_files.append(path)
        if re.search(r"(^|/)use[A-Z][\w-]*\.[cm]?[jt]sx?$", path):
            hook_files.append(path)
        for match in re.finditer(r"\b(?:export\s+)?(?:function\s+|const\s+)(use[A-Z][\w$]*)\b", source.code):
            custom_hooks[match.group(1)] += 1
        for match in LAZY_IMPORT.finditer(source.comment_masked):
            target = resolver.resolve(path, match.group(2))
            if target:
                lazy_targets.add(target)
        if uses_react:
            setters, groups, initialised = state_facts(source)
            boolean_groups += groups
            from_props += initialised
            effects += effects_in(source, setters)
        suppressions += suppressions_in(source)
        for match in re.finditer(r"\bforwardRef\b", source.code):
            after = match.end()
            while after < len(source.code) and source.code[after].isspace():
                after += 1
            if source.code[after:after + 1] == "<":
                depth = 0
                while after < len(source.code):
                    if source.code[after] == "<":
                        depth += 1
                    elif source.code[after] == ">":
                        depth -= 1
                        if depth == 0:
                            after += 1
                            break
                    after += 1
                while after < len(source.code) and source.code[after].isspace():
                    after += 1
            if source.code[after:after + 1] == "(":
                forward_refs.append((path, source.line_of(match.start())))
        ref_props += len(re.findall(r"\(\s*\{[^{}()]*\bref\b[^{}()]*\}", source.code))
        if is_component_file(path, source):
            for match in re.finditer(r"(?m)^[ \t]*export\s+default\b", source.code):
                default_exports.append((path, source.line_of(match.start())))
        for item in imports:
            target = resolver.resolve(path, item.specifier)
            if target and target != path:
                fan_in[target] += 1

    # Risk ranking: churn plus fan-in, over component and hook files.
    commits = churn(root, months)
    ranked_paths = sorted(
        set(component_files) | set(hook_files),
        key=lambda path: (-(commits.get(path, 0) + fan_in.get(path, 0)), -commits.get(path, 0), path),
    )
    ranking = [
        {"path": path, "commits": commits.get(path, 0), "fanIn": fan_in.get(path, 0)}
        for path in ranked_paths[:15] if commits.get(path, 0) + fan_in.get(path, 0) > 0
    ]
    risk_of = {path: index for index, path in enumerate(ranked_paths)}

    def by_risk(item: dict[str, Any]) -> tuple[int, str, int]:
        return risk_of.get(item["path"], len(risk_of)), item["path"], item.get("line", 0)

    snapshot["components"] = {
        "componentFiles": len(component_files),
        "hookFiles": len(hook_files),
        "customHooks": len(custom_hooks),
    }
    snapshot["riskRanking"] = ranking
    snapshot["historyWindowMonths"] = months
    effect_candidates = sorted((item for item in effects if item["flags"] and item["flags"] != ["no-dependency-array"]), key=by_risk)
    flag_counts = Counter(flag for item in effects for flag in item["flags"])
    snapshot["effects"] = {
        "total": len(effects),
        "byFlag": dict(sorted(flag_counts.items())),
        "candidates": effect_candidates[:MAX_CANDIDATES],
    }
    snapshot["state"] = {
        "booleanFlagGroups": sorted(boolean_groups, key=by_risk)[:20],
        "stateInitialisedFromProps": sorted(from_props, key=by_risk)[:20],
    }
    snapshot["exhaustiveDepsSuppressions"] = {
        "total": len(suppressions),
        "unjustified": sum(1 for item in suppressions if not item["justified"]),
        "examples": [{key: item[key] for key in ("path", "line", "justified")} for item in sorted(suppressions, key=by_risk)[:20]],
    }

    # Applicability shared by several checks.
    versions = [item["_version"] for item in packages_facts]
    react19_packages = [item for item in packages_facts if item["_version"] and item["_version"] >= (19, 0)]
    unknown_version = [item for item in packages_facts if item["_version"] is None]
    below19_reason = "React 19 APIs need React 19, and every React package here resolves an older React: " + ", ".join(
        f"{item['package']} ({item['resolved'] or item['react'] or 'unknown'}{', peer ' + item['peerRange'] if item['peerRange'] else ''})" for item in packages_facts
    ) + "."
    server_component_packages = [facts for facts in frameworks if facts["serverComponents"]]

    # Layer 1: the hooks lint rules.
    samples = sorted(set(component_files) | set(hook_files), key=lambda path: (risk_of.get(path, len(risk_of)), path))
    by_package: dict[str, list[str]] = {}
    for path in samples:
        owner = manifests.owner(path)
        by_package.setdefault(owner.path if owner else "", []).append(path)
    chosen: list[str] = []
    while len(chosen) < LINT_SAMPLE_SIZE and any(by_package.values()):
        for key in sorted(by_package):
            if by_package[key] and len(chosen) < LINT_SAMPLE_SIZE:
                chosen.append(by_package[key].pop(0))
    checks[HOOKS_LINT], snapshot["hooksLint"] = hooks_lint_check(root, files, manifests, chosen, lockfiles, resolver)

    # Layer 1: a maintained toolchain.
    create_react_app = [package for package in manifests.packages if package.declares("react-scripts")]
    wrappers = [package for package in manifests.packages if package.declares("@craco/craco") or package.declares("react-app-rewired")]
    if create_react_app:
        evidence = [cite_file(package.manifest_path, package.quote_dependency("react-scripts")) for package in create_react_app[:3]]
        evidence += [cite_file(package.manifest_path, package.quote_dependency("@craco/craco") or package.quote_dependency("react-app-rewired")) for package in wrappers[:1]]
        checks[TOOLCHAIN] = {
            "status": "violation",
            "evidence": evidence,
            "evidenceTier": "direct",
            "gap": f"{len(create_react_app)} package(s) build with Create React App (react-scripts), which the React team deprecated on 14 February 2025; it gets no new features, and its dependency tree carries known vulnerabilities.",
            "remediation": "Migrate to Vite (the smallest step for a client-only app) or to a framework such as Next.js or React Router, following react.dev's migration guide; then remove react-scripts.",
        }
    else:
        tools = [name for name in ("next", "vite", "@react-router/dev", "expo", "react-native", "gatsby", "@rsbuild/core", "parcel", "webpack", "@tanstack/react-start", "esbuild", "waku")
                 if any(package.declares(name) for package in manifests.packages)]
        evidence = [f"command: `collect.py manifest scan` → no react-scripts in {len(manifests.packages)} package.json file(s); build tooling: {', '.join(tools) or 'none declared'}"]
        for name in tools[:2]:
            package = manifests.declares(name)[0]
            quote = package.quote_dependency(name)
            if quote:
                evidence.append(cite_file(package.manifest_path, quote))
        checks[TOOLCHAIN] = {"status": "present", "evidence": evidence, "evidenceTier": "direct"}

    # Layer 1: exhaustive-deps suppressions.
    unjustified = [item for item in sorted(suppressions, key=by_risk) if not item["justified"]]
    if not suppressions:
        checks[SUPPRESSIONS] = degrade({
            "status": "present",
            "evidence": [f"command: `collect.py suppression scan` → no exhaustive-deps suppressions in {len(sources)} source files (comments read, strings ignored)"],
            "evidenceTier": "direct",
        }, large)
    elif not unjustified:
        checks[SUPPRESSIONS] = degrade({
            "status": "present",
            "evidence": [cite(item["path"], item["line"], item["quote"], "carries a reason") for item in suppressions[:5]],
            "evidenceTier": "direct",
        }, large)
    else:
        checks[SUPPRESSIONS] = degrade({
            "status": "partial",
            "evidence": [cite(item["path"], item["line"], item["quote"], "has no reason") for item in unjustified[:8]],
            "evidenceTier": "direct",
            "gap": f"{len(unjustified)} of {len(suppressions)} exhaustive-deps suppression(s) give no reason, so a stale value read by the effect cannot be told apart from a deliberate choice.",
            "remediation": "For each, move logic that should not re-run the effect into `useEffectEvent` (React 19.2) or out of the effect, then remove the suppression; keep one only with a reason after ` -- `.",
            "judgement": "consider",
        }, large)

    # Layer 2: the compiler is context for judging effect dependencies, never an applicability gate. React keeps
    # useMemo and useCallback as the escape hatch for effect dependencies, and the compiler skips what it cannot compile.
    opt_outs = []
    for path, source in sources.items():
        for name, line in prologues.get(path, []):
            if name in ("use no memo", "use no forget"):
                opt_outs.append(cite(path, line, name))
        for match in FUNCTION_OPT_OUT.finditer(source.comment_masked):
            if source.code[match.start():match.start() + 1] == "{":
                opt_outs.append(cite(path, source.line_of(match.start(2)), f"use no {match.group(3)}"))
    if opt_outs:
        snapshot["compilerOptOuts"] = opt_outs[:20]
        for item in compilers:
            owner_path = item["package"].rsplit("/", 1)[0] if "/" in item["package"] else ""
            if any(not owner_path or entry.startswith(owner_path + "/") or entry.startswith("`" + owner_path + "/") for entry in opt_outs):
                item["everywhere"] = False
                item["optOuts"] = True
        snapshot["compiler"] = [{key: value for key, value in item.items() if key != "unread" or value} for item in compilers]

    # Layer 4: ref as a prop.
    react19_forward_refs = [(path, line) for path, line in forward_refs if (item := package_of(path)) is not None and item["_version"] and item["_version"] >= (19, 0)]
    unknown_forward_refs = [(path, line) for path, line in forward_refs if (item := package_of(path)) is None or item["_version"] is None]
    snapshot["refs"] = {"forwardRef": len(forward_refs), "refAsPropComponents": ref_props}
    if not react19_packages and not unknown_version:
        checks[REF_PROP] = not_applicable(below19_reason)
        checks[FORMS] = not_applicable(below19_reason)
    elif not react19_packages:
        checks[REF_PROP] = {
            "evaluationState": "not-evaluated",
            "reason": "The React version could not be resolved from the manifests or a lockfile ("
            + ", ".join(f"{item['package']}: {item['react'] or 'not declared'}" for item in unknown_version) + "), so whether `ref` can be a prop is unknown.",
        }
    elif react19_forward_refs:
        checks[REF_PROP] = degrade({
            "status": "partial",
            "evidence": [cite(path, line, "forwardRef") for path, line in react19_forward_refs[:8]],
            "evidenceTier": "direct",
            "gap": f"{len(react19_forward_refs)} forwardRef wrapper(s) remain in React 19 code; React 19 passes `ref` as an ordinary prop and plans to deprecate forwardRef.",
            "remediation": "Write new components with `ref` as a prop, and convert existing wrappers as you touch them (react.dev's codemod handles most of them); keep forwardRef only in packages whose peer range still supports React 18.",
            "judgement": "consider",
        }, large)
    else:
        checks[REF_PROP] = degrade({
            "status": "present",
            "evidence": [f"command: `collect.py forwardRef scan` → 0 forwardRef wrappers in React 19 packages; {ref_props} component(s) take `ref` as a prop"],
            "evidenceTier": "direct",
        }, large)
    if unknown_forward_refs and "status" in checks.get(REF_PROP, {}):
        checks[REF_PROP]["degradedReason"] = (
            f"{len(unknown_forward_refs)} forwardRef wrapper(s) are in packages whose React version could not be resolved"
        )
        checks[REF_PROP]["evidenceTier"] = "supported"

    # Layer 4: named exports outside framework-required files.
    route_files = route_module_files(root, frameworks, file_set)
    flagged_defaults = [(path, line) for path, line in default_exports if not framework_required_default(path, frameworks, route_files, lazy_targets)]
    exempt = len(default_exports) - len(flagged_defaults)
    snapshot["defaultExports"] = {"total": len(default_exports), "frameworkRequired": exempt, "elsewhere": len(flagged_defaults)}
    if not component_files:
        checks[NAMED_EXPORTS] = not_applicable("No component files (.jsx, .tsx, or JavaScript with JSX) were found.")
    elif flagged_defaults:
        ranked = sorted(flagged_defaults, key=lambda item: (risk_of.get(item[0], len(risk_of)), item))
        checks[NAMED_EXPORTS] = degrade({
            "status": "partial",
            "evidence": [cite(path, line, "export default") for path, line in ranked[:8]],
            "evidenceTier": "direct",
            "gap": f"{len(flagged_defaults)} component file(s) default-export outside files a framework or React.lazy requires ({exempt} framework-required default export(s) were exempted), so the same component is imported under different names.",
            "remediation": "Export these components by name and import them by that name; keep default exports only where the framework requires them (pages, layouts, route modules) or for React.lazy targets.",
            "judgement": "consider",
        }, large)
    else:
        checks[NAMED_EXPORTS] = degrade({
            "status": "present",
            "evidence": [f"command: `collect.py export scan` → {len(component_files)} component files; {exempt} default export(s), all in framework-required files or lazy-loading targets"],
            "evidenceTier": "direct",
        }, large)

    # Layer 4: server and client boundaries.
    client_entries = sorted(path for path, prologue in prologues.items() if any(name == "use client" for name, _ in prologue))
    server_modules = {path for path, prologue in prologues.items() if any(name == "use server" for name, _ in prologue)}
    inline_server_functions = sum(len(re.findall(r"""\{\s*(['"])use server\1""", source.comment_masked)) for source in sources.values())
    snapshot["directives"] = {
        "useClientFiles": len(client_entries),
        "useServerFiles": len(server_modules),
        "inlineServerFunctions": inline_server_functions,
        "useClientExamples": client_entries[:15],
        "misplaced": [cite(path, line, f"use {name.split()[-1]}", "is below other code, so React ignores it") for path, name, line in misplaced[:10]],
    }
    if not server_component_packages:
        reason = "No React Server Components framework (Next.js App Router, Waku, or a react-server-dom bundler) is used, so nothing crosses a server-client boundary."
        if client_entries or server_modules:
            reason += f" {len(client_entries) + len(server_modules)} file(s) still carry a 'use client' or 'use server' directive, which only a server-components bundler reads."
        for check in SERVER_COMPONENT_CHECKS:
            checks[check] = not_applicable(reason)
    else:
        # The folder exclusions only choose candidates for the model; the boundary follows every runtime import,
        # into tooling, fixture, or test folders too, and a module it cannot read degrades the result.
        loaded: dict[str, Source] = dict(sources)
        loaded_imports: dict[str, list[Import]] = dict(imports_by_file)
        unreadable: list[str] = []
        large_set = set(large)

        def module_source(path: str) -> Source | None:
            if path in loaded:
                return loaded[path]
            if path in large_set or not (root / path).is_file():
                if path not in unreadable:
                    unreadable.append(path)
                return None
            source = Source(path, read_text(root / path))
            loaded[path] = source
            loaded_imports[path] = imports_of(source)
            prologues.setdefault(path, directives(source))
            return source

        edges: dict[str, list[tuple[str, Import]]] = {}
        unresolved_local: list[tuple[str, str]] = []
        reached: dict[str, tuple[str, Import] | None] = {entry: None for entry in client_entries}
        queue = deque(client_entries)
        while queue:
            current = queue.popleft()
            if module_source(current) is None:
                continue
            for item in loaded_imports.get(current, []):
                target = resolver.resolve(current, item.specifier)
                if target is None:
                    if resolver.looks_local(item.specifier):
                        unresolved_local.append((current, item.specifier))
                    continue
                if target == current:
                    continue
                edges.setdefault(current, []).append((target, item))
                if target in reached:
                    continue
                if module_source(target) is not None and any(name == "use server" for name, _ in prologues.get(target, [])):
                    continue  # a Server Function module: the client receives references, not its code
                reached[target] = (current, item)
                queue.append(target)
        inlined = frozenset(next_inlined_environment(root, files))
        leaks = []
        for module in sorted(reached, key=lambda path: (risk_of.get(path, len(risk_of)), path)):
            if module not in loaded:
                continue
            markers = server_markers(loaded[module], loaded_imports.get(module, []), inlined)
            if not markers:
                continue
            chain = [module]
            step = reached[module]
            while step is not None and len(chain) < 12:
                chain.append(step[0])
                step = reached[step[0]]
            leaks.append((module, list(reversed(chain)), markers))
        unresolved_in_graph = unresolved_local
        snapshot["directives"]["clientGraphModules"] = len(reached)
        snapshot["directives"]["serverOnlyReachedFromClient"] = [
            {"module": module, "chain": chain, "markers": [description for _, _, description in markers][:3]} for module, chain, markers in leaks[:10]
        ]
        if leaks:
            evidence = []
            for module, chain, markers in leaks[:4]:
                # The client modules that import the leaking module directly, the first one on the chain first.
                importers = [chain[-2]] if len(chain) > 1 else []
                importers += sorted(path for path in reached if path not in importers and any(target == module for target, _ in edges.get(path, [])))
                for importer in importers[:2]:
                    item = next((item for target, item in edges.get(importer, []) if target == module), None)
                    if item:
                        note = "in a 'use client' module" if importer in client_entries else "in a module a 'use client' file imports"
                        evidence.append(cite(importer, item.line, quotable(loaded[importer].lines.line(item.line), item.specifier) or item.specifier, note))
                line, quote, description = markers[0]
                evidence.append(cite(module, line, quote, f"{description}, and it is bundled for the browser"))
            checks[SERVER_IMPORTS] = {
                "status": "violation",
                "evidence": evidence[:8],
                "evidenceTier": "direct",
                "gap": f"{len(leaks)} module(s) reachable from a 'use client' file import server-only code or read private environment variables, so the code ships to the browser, where those values are undefined, or the build fails. "
                + "Chain: " + " → ".join(leaks[0][1]) + ".",
                "remediation": "Keep server code behind the boundary: move the server-only part into a module that imports `server-only`, call it from a Server Component or Server Function, and pass only plain data to the client component.",
            }
            reasons = []
            if unresolved_in_graph:
                reasons.append(f"{len(unresolved_in_graph)} import(s) in the client graph could not be resolved, such as `{unresolved_in_graph[0][1]}` in {unresolved_in_graph[0][0]}")
            if unreadable:
                reasons.append(f"{len(unreadable)} module(s) in the client graph could not be read, such as {unreadable[0]}")
            if reasons:
                checks[SERVER_IMPORTS]["degradedReason"] = "; ".join(reasons)
                checks[SERVER_IMPORTS]["evidenceTier"] = "supported"
        else:
            result = {
                "status": "present",
                "evidence": [f"command: `collect.py client graph` → {len(client_entries)} 'use client' file(s) reach {len(reached)} module(s); none imports server-only code or reads a private environment variable"],
                "evidenceTier": "direct",
            }
            reasons = []
            if unresolved_in_graph:
                reasons.append(f"{len(unresolved_in_graph)} import(s) in the client graph could not be resolved, such as `{unresolved_in_graph[0][1]}` in {unresolved_in_graph[0][0]}")
            if unreadable:
                reasons.append(f"{len(unreadable)} module(s) in the client graph could not be read, such as {unreadable[0]}")
            if reasons:
                result["degradedReason"] = "; ".join(reasons)
                result["evidenceTier"] = "supported"
            checks[SERVER_IMPORTS] = result

        # Candidates for the model: function or class-instance props passed from server files into client components.
        candidates = []
        for path, source in sources.items():
            if path in reached or path in server_modules or not source.has_markup:
                continue
            owner = package_of(path)
            facts = next((item for item in frameworks if owner and item["package"] == owner["package"]), None)
            if facts is None or not facts["serverComponents"]:
                continue
            client_components: dict[str, str] = {}
            server_imported: set[str] = set()
            for item in imports_by_file.get(path, []):
                target = resolver.resolve(path, item.specifier)
                if target in client_entries:
                    for local in item.bindings():
                        client_components[local] = target
                if target in server_modules:
                    server_imported |= set(item.bindings())
            if not client_components:
                continue
            functions = local_functions(source)
            text = source.comment_masked
            for count, match in enumerate(JSX_OPENING.finditer(text)):
                if count >= MAX_TAGS_PER_FILE:
                    break
                if source.code[match.start()].strip():
                    continue  # a type argument such as useState<User>, not a JSX tag
                tag = match.group(1)
                if tag.split(".")[0] not in client_components:
                    continue
                attributes, _ = jsx_attributes(text, match.end())
                for name, expression, position in attributes:
                    problem = non_serialisable(expression, functions, server_imported)
                    if problem:
                        line = source.line_of(position)
                        candidates.append({"path": path, "line": line, "component": tag, "prop": name, "value": problem,
                                           "clientModule": client_components[tag.split(".")[0]]})
        snapshot["directives"]["nonSerialisablePropCandidates"] = sorted(candidates, key=by_risk)[:MAX_CANDIDATES]
        app_root_clients = [path for path in client_entries if re.search(r"(^|/)(?:src/)?app/(?:layout|page|template)\.[cm]?[jt]sx?$", path)]
        snapshot["directives"]["clientRootFiles"] = app_root_clients

    snapshot["stack"] = {
        "formLibraries": sorted({name for package in react_packages for name in ("react-hook-form", "formik", "@tanstack/react-form", "@conform-to/react") if package.declares(name)}),
        "dataLibraries": sorted({name for package in react_packages for name in ("@tanstack/react-query", "swr", "@apollo/client", "@reduxjs/toolkit", "urql", "@trpc/react-query") if package.declares(name)}),
        "stateLibraries": sorted({name for package in react_packages for name in ("zustand", "jotai", "redux", "@reduxjs/toolkit", "mobx", "valtio", "xstate", "@xstate/react") if package.declares(name)}),
        "urlStateLibraries": sorted({name for package in react_packages for name in ("nuqs", "use-query-params") if package.declares(name)}),
    }
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
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repository", default=".")
    parser.add_argument("--enrichment", action="append", default=[], help="a recorded enrichment flag; this audit has none")
    parser.add_argument("--threshold", action="append", default=[], help="a recorded key=value override; months=<n> widens the history window")
    arguments = parser.parse_args(argv)
    root = Path(arguments.repository).resolve()
    result = collect(root, history_months(arguments.threshold, 6))
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
