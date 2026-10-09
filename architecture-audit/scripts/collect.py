#!/usr/bin/env python3
"""Deterministic, read-only collector for /architecture-audit.

Builds a static import graph of the repository's TypeScript and JavaScript
sources (relative imports and tsconfig path aliases), then reports:

- import cycles, decided here as a tool check, with one cited import per edge;
- whether boundary rules are enforced by tooling, decided here;
- deep imports into another workspace's internals, decided here for monorepos;
- hubs, orphan candidates, Git hotspots, and change coupling, recorded as
  snapshot facts for the model's judgement checks.

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
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

AUDIT = "architecture-audit"
SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
RESOLVE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".d.ts", ".js", ".jsx", ".mjs", ".cjs")
SKIPPED_DIRECTORIES = {
    ".git", "node_modules", "dist", "build", "out", "coverage", ".next", ".nuxt",
    ".turbo", ".cache", ".vercel", ".output", "storybook-static", ".architect-audits",
    "graphify-out", ".worktrees", ".claude",
}
MAX_FILES = 50_000
MAX_FILE_BYTES = 2_000_000
IMPORT_PATTERN = re.compile(
    r"""(?mx)
    ^[ \t]*(?:import|export)\s+(?P<clause>(?:(?!\n[ \t]*(?:import|export)\b)[^'"`;=])*?)\bfrom\s*['"](?P<from>[^'"]+)['"]
    | ^[ \t]*import\s*['"](?P<side>[^'"]+)['"]
    | \bimport\s*\(\s*['"](?P<dynamic>[^'"]+)['"]\s*\)
    | \brequire\s*\(\s*['"](?P<require>[^'"]+)['"]\s*\)
    """
)
ASSET_SUFFIXES = (
    ".css", ".scss", ".sass", ".less", ".svg", ".png", ".jpg", ".jpeg", ".gif", ".webp",
    ".avif", ".ico", ".json", ".md", ".mdx", ".txt", ".woff", ".woff2", ".wasm",
)
BUILD_DIRECTORIES = {"dist", "build", "lib", "out", "esm", "cjs"}
CONFIG_FILE_PATTERN = re.compile(r"^(?:tsconfig(?:\.[\w.-]+)?|jsconfig)\.json$")
HOTSPOT_EXCLUDED_PATTERN = re.compile(r"(^|/)[^/]*\.config\.[cm]?[jt]s$")
BOUNDARY_TOOL_FILES = (
    ".dependency-cruiser.js", ".dependency-cruiser.cjs", ".dependency-cruiser.mjs",
    ".dependency-cruiser.json", "dependency-cruiser.config.js", "dependency-cruiser.config.cjs",
)


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


def source_files(root: Path) -> tuple[list[Path], list[Path]]:
    """Return the source files to read, and those left out by the size limits."""
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if listed:
        candidates = [root / entry for entry in listed.split("\0") if entry]
    else:
        candidates = [path for path in root.rglob("*") if path.is_file()]
    files = []
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if any(part in SKIPPED_DIRECTORIES for part in relative.parts[:-1]):
            continue
        if path.suffix in SOURCE_SUFFIXES and not path.name.endswith(".d.ts") and path.is_file():
            files.append(path)
    skipped = files[MAX_FILES:]
    files = files[:MAX_FILES]
    skipped += [path for path in files if path.stat().st_size > MAX_FILE_BYTES]
    return files, sorted(skipped)


def mask_comments_only(text: str) -> str:
    """Blank comments with the original filter, without suppressing import matches."""
    output = list(text)
    index = 0
    quote = ""
    while index < len(text):
        character = text[index]
        if quote:
            if character == "\\":
                index += 2
                continue
            if character == quote or (character == "\n" and quote != "`"):
                quote = ""
            index += 1
            continue
        if character in "'\"`":
            quote = character
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end == -1 else end + 2
        else:
            index += 1
            continue
        for position in range(index, end):
            if output[position] != "\n":
                output[position] = " "
        index = end
    return "".join(output)


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
        elif text.startswith("//", index):
            newline = text.find("\n", index)
            index = len(text) if newline == -1 else newline
            continue
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        elif character == "<":
            depth += 1
        elif character == ">" and text[index - 1] != "=":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    return None


def mask_comments(text: str, allow_markup: bool = False) -> tuple[str, bytearray]:
    """Blank comments and mark code positions without changing source offsets.

    Quoted specifiers remain readable by IMPORT_PATTERN. Literal text is not
    code; template and JSX brace expressions are. This is a lexical filter,
    not a complete JavaScript or TypeScript parser.
    """
    output = list(text)
    code_positions = bytearray(len(text))
    index = 0
    invalid_context = False
    contexts: list[dict[str, Any]] = [{"kind": "code", "depth": 0, "operand": True, "token": "", "brackets": [], "statement": True}]
    expression_keywords = {"return", "throw", "case", "await", "yield", "delete", "void", "typeof", "new", "in", "instanceof"}
    control_keywords = {"if", "while", "for", "with", "switch", "catch"}
    markup_start = re.compile(r"<(?:[A-Za-z]|>)")
    tag_name = re.compile(r"[A-Za-z_$][\w$-]*(?:[.:][A-Za-z_$][\w$-]*)*")
    closing_tag = re.compile(r"</(?:[A-Za-z_$][\w$.:-]*\s*)?>")
    generic_parameters = re.compile(r"<(?:const\s+)?[A-Za-z_$][\w$]*\s*(?:[,=]|\bextends\b)")
    while index < len(text):
        character = text[index]
        frame = contexts[-1]
        context = frame["kind"]
        depth = frame.get("depth", 0)
        if context in ("'", '"'):
            attribute = contexts[-2]["kind"] == "tag"
            if character == "\\" and not attribute:
                index += 2
                continue
            if character == context or (character == "\n" and not attribute):
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
                    invalid_context = True
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
            elif character == ">":
                contexts.pop()
                if depth == -1 or text[index - 1] != "/":
                    contexts[-1]["depth"] += depth
                if contexts[-1]["depth"] < 0:
                    invalid_context = True
                    break
                if contexts[-1]["depth"] == 0:
                    contexts.pop()
                    contexts[-1].update(operand=False, token="markup", statement=False)
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            end = len(text) if end == -1 else end
        elif text.startswith("/*", index):
            end = text.find("*/", index + 2)
            end = len(text) if end == -1 else end + 2
        else:
            if allow_markup and closing_tag.match(text, index):
                invalid_context = True
                break
            if character in "'\"":
                contexts.append({"kind": character})
            elif character == "`":
                contexts.append({"kind": "template"})
            elif allow_markup and character == "<" and frame["operand"] and markup_start.match(text, index) and not generic_parameters.match(text, index):
                contexts.append({"kind": "markup", "depth": 0})
                contexts.append({"kind": "tag", "depth": 1, "name": True})
            elif character == "/" and frame["operand"]:
                # Regex braces and quotes do not alter enclosing expressions.
                index += 1
                character_class = False
                while index < len(text):
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
                    elif text[index] == "\n":
                        break
                    index += 1
                frame.update(operand=False, token="literal", statement=False)
                continue
            else:
                code_positions[index] = 1
                if character.isalnum() or character in "_$":
                    end = index + 1
                    while end < len(text) and (text[end].isalnum() or text[end] in "_$"):
                        end += 1
                    code_positions[index:end] = b"\x01" * (end - index)
                    token = text[index:end]
                    if token == "function" and frame["token"] != ".":
                        frame["function_declaration"] = frame["statement"]
                    if token == "class" and frame["token"] != ".":
                        frame["class_declaration"] = frame["statement"]
                    for_separator = token == "of" and frame["token"] not in {"const", "let", "var"} and not frame["operand"] and frame["brackets"] and frame["brackets"][-1] == "for-control"
                    statement_keyword = token in {"else", "do", "try", "finally"} and frame["token"] != "."
                    frame["control"] = token if token in control_keywords and frame["token"] != "." else ""
                    declaration_prefix = frame["statement"] and token in {"export", "default", "async", "declare"}
                    frame.update(operand=(token in expression_keywords or statement_keyword or for_separator) and frame["token"] != ".", token=token, statement=declaration_prefix or statement_keyword)
                    index = end
                    continue
                if character == "(":
                    if "function_declaration" in frame:
                        frame["brackets"].append(("function", frame.pop("function_declaration")))
                    else:
                        frame["brackets"].append("for-control" if frame.get("control") == "for" else "control" if frame.get("control") else "parentheses")
                    frame.update(operand=True, statement=False)
                elif character == ")":
                    bracket = frame["brackets"].pop() if frame["brackets"] else None
                    if isinstance(bracket, tuple):
                        frame["body_declaration"] = bracket[1]
                    control = bracket in ("control", "for-control")
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
                elif not character.isspace():
                    frame.update(operand=character not in "].", statement=character == ";")
                    if text.startswith(("++", "--", "=>"), index):
                        code_positions[index + 1] = 1
                        frame.update(operand=text[index:index + 2] == "=>", token=text[index:index + 2])
                        index += 2
                        continue
                if not character.isspace():
                    frame["token"] = character
                    frame["control"] = False
            index += 1
            continue
        for position in range(index, end):
            if output[position] != "\n":
                output[position] = " "
        index = end
    if invalid_context or len(contexts) != 1:
        # An uncertain context must not hide all subsequent live imports.
        # Restore the original comment-only filter for this file.
        return mask_comments_only(text), bytearray(b"\x01" * len(text))
    return "".join(output), code_positions


def config_files(root: Path) -> list[Path]:
    """Every tsconfig*.json and jsconfig.json outside generated folders."""
    listed = git(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    candidates = [root / entry for entry in listed.split("\0") if entry] if listed else list(root.rglob("*.json"))
    return sorted(
        path
        for path in candidates
        if CONFIG_FILE_PATTERN.match(path.name)
        and not any(part in SKIPPED_DIRECTORIES for part in path.relative_to(root).parts[:-1])
        and path.is_file()
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


class Resolver:
    """Resolve import specifiers to repository files: relative paths and tsconfig paths.

    Every tsconfig*.json and jsconfig.json contributes its path aliases and
    baseUrl to the files under its folder, following `extends`. For each
    importer the nearest folder's configurations are tried first.
    """

    def __init__(self, root: Path, files: list[Path], configs: list[Path]) -> None:
        self.root = root
        self.files = set(files)
        self.unloaded_extends: list[str] = []
        merged: dict[Path, tuple[list[tuple[str, list[Path]]], Path | None]] = {}
        for config in configs:
            aliases, base_url = self._load_tsconfig(config.resolve(), depth=0)
            directory = config.parent.resolve()
            known_aliases, known_base = merged.get(directory, ([], None))
            merged[directory] = (known_aliases + aliases, known_base or base_url)
        self.scopes = sorted(
            ((directory, aliases, base_url) for directory, (aliases, base_url) in merged.items()),
            key=lambda scope: -len(scope[0].parts),
        )
        self.alias_prefixes = {pattern.split("*", 1)[0] for _, aliases, _ in self.scopes for pattern, _ in aliases}

    def _extends_target(self, path: Path, parent: str) -> Path | None:
        """A relative or package `extends` target, if the file can be found."""
        if parent.startswith("."):
            bases = [(path.parent / parent).resolve()]
        else:
            bases = [directory / "node_modules" / parent for directory in [path.parent, *path.parent.parents]]
        for base in bases:
            for candidate in (base, Path(str(base) + ".json"), base / "tsconfig.json"):
                if candidate.is_file():
                    return candidate
        return None

    def _load_tsconfig(self, path: Path, depth: int) -> tuple[list[tuple[str, list[Path]]], Path | None]:
        """Return the effective path aliases and baseUrl, letting the child override its parents."""
        if depth > 5 or not path.is_file():
            return [], None
        config = load_jsonc(path)
        aliases: list[tuple[str, list[Path]]] = []
        base_url: Path | None = None
        extends = config.get("extends")
        for parent in [extends] if isinstance(extends, str) else extends if isinstance(extends, list) else []:
            if not isinstance(parent, str):
                continue
            target = self._extends_target(path, parent)
            if target is None:
                self.unloaded_extends.append(parent)
                continue
            parent_aliases, parent_base = self._load_tsconfig(target, depth + 1)
            aliases = parent_aliases or aliases
            base_url = parent_base or base_url
        options = config.get("compilerOptions") or {}
        if isinstance(options.get("baseUrl"), str):
            base_url = (path.parent / options["baseUrl"]).resolve()
        paths = options.get("paths")
        if isinstance(paths, dict):
            base = base_url or path.parent.resolve()
            aliases = [
                (pattern, [(base / target) for target in targets if isinstance(target, str)])
                for pattern, targets in paths.items()
                if isinstance(targets, list)
            ]
        return aliases, base_url

    def _candidates(self, base: Path) -> list[Path]:
        options = [base]
        options += [Path(str(base) + suffix) for suffix in RESOLVE_SUFFIXES]
        options += [base / f"index{suffix}" for suffix in RESOLVE_SUFFIXES]
        if base.suffix in (".js", ".jsx", ".mjs", ".cjs"):
            stem = base.with_suffix("")
            options += [Path(str(stem) + suffix) for suffix in (".ts", ".tsx", ".mts", ".cts")]
        return options

    def _first_file(self, base: Path) -> Path | None:
        for candidate in self._candidates(base):
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in self.files:
                return resolved
        return None

    def looks_internal(self, specifier: str) -> bool:
        """Whether an unresolved specifier was meant to be a repository file."""
        if specifier.lower().endswith(ASSET_SUFFIXES):
            return False
        return specifier.startswith((".", "@/", "~/", "#")) or any(
            prefix and specifier.startswith(prefix) for prefix in self.alias_prefixes
        )

    def resolve(self, importer: Path, specifier: str) -> Path | None:
        if specifier.startswith("."):
            return self._first_file(importer.parent / specifier)
        for directory, aliases, base_url in self.scopes:
            if directory != importer.parent and directory not in importer.parents:
                continue
            for pattern, targets in aliases:
                if "*" in pattern:
                    prefix, _, suffix = pattern.partition("*")
                    if specifier.startswith(prefix) and specifier.endswith(suffix):
                        middle = specifier[len(prefix): len(specifier) - len(suffix) if suffix else None]
                        for target in targets:
                            found = self._first_file(Path(str(target).replace("*", middle)))
                            if found:
                                return found
                elif specifier == pattern:
                    for target in targets:
                        found = self._first_file(target)
                        if found:
                            return found
            if base_url is not None and not specifier.startswith("@") and "/" in specifier:
                found = self._first_file(base_url / specifier)
                if found:
                    return found
        return None


def build_graph(
    root: Path, files: list[Path]
) -> tuple[dict[Path, dict[Path, tuple[int, str, str]]], int, list[str]]:
    """Return edges importer -> {imported: (line, specifier, kind)}, the unresolved count,
    and any tsconfig `extends` targets that could not be loaded.

    The kind is `static`, `type` (type-only), or `lazy` (a dynamic `import()`).
    Only static edges load at start-up, so only they can form a runtime cycle.
    """
    resolver = Resolver(root, [file.resolve() for file in files], config_files(root))
    graph: dict[Path, dict[Path, tuple[int, str, str]]] = {file.resolve(): {} for file in files}
    unresolved = 0
    rank = {"static": 2, "lazy": 1, "type": 0}
    for file in files:
        importer = file.resolve()
        text, code_positions = mask_comments(read_text(file), allow_markup=file.suffix not in (".ts", ".mts", ".cts"))
        for match in IMPORT_PATTERN.finditer(text):
            keyword = match.start()
            while text[keyword].isspace():
                keyword += 1
            if not code_positions[keyword]:
                continue
            group = next((name for name in ("from", "side", "dynamic", "require") if match.group(name)), None)
            if group is None:
                continue
            specifier = match.group(group)
            if group == "dynamic":
                kind = "lazy"
            elif group == "from" and is_type_only(match.group("clause") or ""):
                kind = "type"
            else:
                kind = "static"
            target = resolver.resolve(importer, specifier)
            if target is None:
                if resolver.looks_internal(specifier):
                    unresolved += 1
                continue
            if target == importer:
                continue
            line = text.count("\n", 0, match.start(group)) + 1
            previous = graph[importer].get(target)
            if previous is None or rank[kind] > rank[previous[2]]:
                graph[importer][target] = (line, specifier, kind)
    return graph, unresolved, resolver.unloaded_extends


def strongly_connected_components(graph: dict[Path, set[Path]]) -> list[list[Path]]:
    """Iterative Tarjan; returns components with more than one node."""
    index_of: dict[Path, int] = {}
    low: dict[Path, int] = {}
    on_stack: set[Path] = set()
    stack: list[Path] = []
    components: list[list[Path]] = []
    counter = 0
    for start in sorted(graph):
        if start in index_of:
            continue
        work: list[tuple[Path, list[Path]]] = [(start, sorted(graph[start]))]
        index_of[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            if children:
                child = children.pop(0)
                if child not in graph:
                    continue
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, sorted(graph[child])))
                elif child in on_stack:
                    low[node] = min(low[node], index_of[child])
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                component = []
                while True:
                    member = stack.pop()
                    on_stack.discard(member)
                    component.append(member)
                    if member == node:
                        break
                if len(component) > 1:
                    components.append(sorted(component))
    return components


def cycle_path(component: list[Path], graph: dict[Path, set[Path]]) -> list[Path]:
    """Find one concrete cycle through the component's first node."""
    members = set(component)
    start = component[0]
    previous: dict[Path, Path] = {}
    queue = [start]
    seen = {start}
    while queue:
        node = queue.pop(0)
        for child in sorted(graph[node]):
            if child == start:
                path = [node]
                while path[-1] != start:
                    path.append(previous[path[-1]])
                return list(reversed(path)) + [start]
            if child in members and child not in seen:
                seen.add(child)
                previous[child] = node
                queue.append(child)
    return component + [start]


TEST_PATH_PATTERN = re.compile(r"(^|/)(__tests__|__mocks__|tests?|e2e|fixtures?)/|\.(test|spec|stories)\.[cm]?[jt]sx?$")


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH_PATTERN.search(path))


def relative(root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def cite(root: Path, path: Path, line: int, specifier: str) -> str:
    """A citation the audit protocol verifies; paths with spaces go in backticks."""
    location = relative(root, path)
    if any(character.isspace() for character in location):
        location = f"`{location}`"
    return f"{location}:{line} — `{specifier}`"


def detect_boundary_tooling(root: Path, directories: list[Path]) -> tuple[list[str], set[str]]:
    """Return verifiable evidence for boundary enforcement, and the tools configured to enforce it.

    Looks in the repository root and in every workspace package.
    """
    found: list[str] = []
    configured: set[str] = set()
    for directory in directories:
        for name in BOUNDARY_TOOL_FILES:
            if (directory / name).is_file():
                found.append(f"{relative(root, directory / name)} — dependency-cruiser configuration")
                configured.add("dependency-cruiser")
        manifest = directory / "package.json"
        package_text = read_text(manifest)
        package = load_jsonc(manifest)
        dependencies = {**(package.get("dependencies") or {}), **(package.get("devDependencies") or {})}
        for name in ("eslint-plugin-boundaries", "@nx/eslint-plugin", "dependency-cruiser"):
            if name in dependencies and f'"{name}"' in package_text:
                found.append(f'{relative(root, manifest)} — `"{name}"` declared')
        for config in sorted(directory.glob("eslint.config.*")) + sorted(directory.glob(".eslintrc*")):
            text = read_text(config)
            for marker in ("boundaries/element-types", "boundaries/", "enforce-module-boundaries", "no-restricted-paths", "import/no-cycle", "import-x/no-cycle"):
                if marker in text:
                    found.append(f"{relative(root, config)} — `{marker}` rule configured")
                    configured.add("eslint")
                    break
    return found, configured


TOOL_RUNNERS = {
    "dependency-cruiser": ("depcruise", "dependency-cruiser"),
    "eslint": ("eslint", "next lint", "nx lint", "nx affected", "nx run-many"),
}


def runner_text(root: Path, directories: list[Path]) -> str:
    """Every package script and workflow, where a boundary tool would be run."""
    scripts = " ".join(
        str(value)
        for directory in directories
        for value in (load_jsonc(directory / "package.json").get("scripts") or {}).values()
    )
    workflows = " ".join(read_text(path) for path in sorted((root / ".github" / "workflows").glob("*.y*ml")))
    return scripts + " " + workflows


def tooling_runs(root: Path, directories: list[Path], configured: set[str]) -> bool:
    """Whether a package script or workflow runs one of the configured boundary tools."""
    combined = runner_text(root, directories)
    return any(runner in combined for tool in configured for runner in TOOL_RUNNERS[tool])


def cruiser_resolves_aliases(config: Path, seen: frozenset[Path]) -> bool:
    """Whether a dependency-cruiser configuration, or one it extends, sets an active tsConfig option."""
    text = mask_comments_only(read_text(config))
    if re.search(r"""\btsConfig\b["']?\s*:""", text):
        return True
    extended = re.search(r"""\bextends\b["']?\s*:\s*["'](\.[^"']+)["']""", text)
    if extended:
        base = (config.parent / extended.group(1)).resolve()
        for candidate in (base, Path(str(base) + ".js"), Path(str(base) + ".cjs"), Path(str(base) + ".json")):
            if candidate.is_file() and candidate not in seen:
                return cruiser_resolves_aliases(candidate, seen | {candidate})
    return False


def folder_uses_aliases(directory: Path, root: Path) -> bool:
    """Whether a tsconfig in this folder or a parent up to the root has path aliases, including inherited ones."""
    resolver = Resolver(root, [], [])
    for folder in [directory, *directory.parents]:
        for config in sorted(folder.glob("tsconfig*.json")) + sorted(folder.glob("jsconfig.json")):
            aliases, _ = resolver._load_tsconfig(config.resolve(), depth=0)
            if aliases:
                return True
        if folder == root:
            break
    return False


def runner_commands(root: Path, directories: list[Path]) -> list[tuple[Path, str]]:
    """Each package script and workflow, with the folder whose package defines it."""
    commands = [
        (directory, str(value))
        for directory in directories
        for value in (load_jsonc(directory / "package.json").get("scripts") or {}).values()
    ]
    commands += [(root, read_text(path)) for path in sorted((root / ".github" / "workflows").glob("*.y*ml"))]
    return commands


def flag_resolves_aliases(root: Path, directory: Path, commands: list[tuple[Path, str]]) -> bool:
    """Whether a dependency-cruiser command that checks this folder passes --ts-config.

    A package's own scripts check that package. A root script or workflow
    checks the root, or a package it names by path.
    """
    folder = relative(root, directory)
    for owner, command in commands:
        if "depcruise" not in command or "--ts-config" not in command:
            continue
        if owner == directory or (owner == root and (directory == root or folder in command)):
            return True
    return False


def alias_blind_cruiser_configs(root: Path, directories: list[Path]) -> list[str]:
    """dependency-cruiser configurations that cannot resolve their package's path aliases.

    Without a tsConfig option, here, in a configuration it extends, or as the
    --ts-config command-line flag, dependency-cruiser leaves alias imports
    unresolved, so no rule ever matches them.
    """
    commands = runner_commands(root, directories)
    blind = []
    for directory in directories:
        for name in BOUNDARY_TOOL_FILES:
            config = directory / name
            if (
                config.is_file()
                and folder_uses_aliases(directory, root)
                and not cruiser_resolves_aliases(config, frozenset({config}))
                and not flag_resolves_aliases(root, directory, commands)
            ):
                blind.append(f"{relative(root, config)} — no tsConfig option, so path aliases are not resolved")
    return blind


def package_entries(package: Path, resolver_files: set[Path]) -> set[Path]:
    """Files a workspace package exposes: its manifest entry points and index files."""
    manifest = load_jsonc(package / "package.json")
    targets: list[str] = []

    def gather(value: Any) -> None:
        if isinstance(value, str):
            targets.append(value)
        elif isinstance(value, dict):
            for item in value.values():
                gather(item)
        elif isinstance(value, list):
            for item in value:
                gather(item)

    for field in ("main", "module", "types", "typings", "source", "exports"):
        gather(manifest.get(field))
    bases: list[Path] = []
    for target in targets:
        if target.startswith("#"):
            continue
        bases.append(package / target)
        # A published build file's source twin, for example dist/index.js and src/index.ts.
        parts = Path(target).parts
        trimmed = parts[1:] if parts and parts[0] == "." else parts
        if trimmed and trimmed[0] in BUILD_DIRECTORIES:
            bases.append(package.joinpath("src", *trimmed[1:]))
    if not targets:
        bases += [package / "index", package / "src" / "index"]
    entries: set[Path] = set()
    for base in bases:
        stem = base.with_suffix("") if base.suffix in SOURCE_SUFFIXES else base
        for candidate in [base, *(Path(str(stem) + suffix) for suffix in RESOLVE_SUFFIXES)]:
            try:
                resolved = candidate.resolve()
            except OSError:
                continue
            if resolved in resolver_files:
                entries.add(resolved)
    return entries


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
        if pattern.startswith("!"):
            continue
        for directory in sorted(root.glob(pattern)):
            if (directory / "package.json").is_file():
                found.append(directory.resolve())
    return sorted(set(found))


def churn(root: Path, months: int) -> tuple[Counter[str], list[set[str]]]:
    """Commits per source file over the window, and the files each commit changed.

    Paths are relative to the target folder, keep non-ASCII names, and leave out
    configuration files such as `next.config.ts`, whose churn is not design risk.
    Renamed files count only their history under the current name.
    """
    log = git(
        root, "-c", "core.quotePath=false", "log", f"--since={months}.months", "--no-merges",
        "--name-only", "--relative", "--pretty=tformat:@@commit",
    )
    counts: Counter[str] = Counter()
    commits: list[set[str]] = []
    current: set[str] = set()
    for line in log.splitlines():
        if line == "@@commit":
            if current:
                commits.append(current)
            current = set()
            continue
        path = line.strip()
        if (
            path
            and Path(path).suffix in SOURCE_SUFFIXES
            and not HOTSPOT_EXCLUDED_PATTERN.search(path)
            and (root / path).is_file()
        ):
            current.add(path)
    if current:
        commits.append(current)
    for files in commits:
        counts.update(files)
    return counts, commits


CONTAINER_FOLDERS = {"features", "modules", "packages", "apps", "domains", "services", "libs"}


def module_boundary(path: str) -> str:
    """The folder that owns a file.

    That is the child of the innermost container such as `features/` or
    `packages/`, so `apps/shop/src/features/cart` and
    `apps/shop/src/features/checkout` are separate modules. Without a container
    it is the top two folders. Files in one folder are one module, so coupling
    between them is not reported.
    """
    folders = path.split("/")[:-1]
    for index in range(len(folders) - 2, -1, -1):
        if folders[index] in CONTAINER_FOLDERS:
            return "/".join(folders[: index + 2])
    return "/".join(folders[:2])


def change_coupling(commits: list[set[str]], counts: Counter[str], minimum: int = 4) -> list[dict[str, Any]]:
    pairs: Counter[tuple[str, str]] = Counter()
    for files in commits:
        if 1 < len(files) <= 30:
            pairs.update(combinations(sorted(files), 2))
    results = []
    for (left, right), together in pairs.items():
        if together < minimum:
            continue
        if module_boundary(left) == module_boundary(right):
            continue
        strength = together / min(counts[left], counts[right])
        if strength >= 0.5:
            results.append({"files": [left, right], "changedTogether": together, "strength": round(strength, 2)})
    return sorted(results, key=lambda item: (-item["changedTogether"], item["files"]))[:15]


def collect(root: Path, months: int) -> dict[str, Any]:
    listed, skipped = source_files(root)
    files = [file for file in listed if file not in set(skipped)]
    graph, unresolved, unloaded_extends = build_graph(root, files)
    runtime = {node: {target for target, (_, _, kind) in edges.items() if kind == "static"} for node, edges in graph.items()}
    checks: dict[str, Any] = {}
    snapshot: dict[str, Any] = {
        "sourceFiles": len(files),
        "importEdges": sum(len(edges) for edges in graph.values()),
        "unresolvedRelativeImports": unresolved,
    }
    if unloaded_extends:
        snapshot["unloadedTsconfigExtends"] = unloaded_extends
    if skipped:
        snapshot["skippedSourceFiles"] = {"count": len(skipped), "examples": [relative(root, path) for path in skipped[:10]]}

    if not files:
        return {"checks": checks, "snapshot": snapshot}

    components = strongly_connected_components(runtime)
    if components:
        evidence = []
        cycles = []
        for component in components[:5]:
            path = cycle_path(component, runtime)
            cycles.append([relative(root, node) for node in path])
            for importer, imported in zip(path, path[1:]):
                line, specifier, _ = graph[importer][imported]
                evidence.append(cite(root, importer, line, specifier))
        snapshot["importCycles"] = {"count": len(components), "examples": cycles}
        checks[f"{AUDIT}.no-circular-dependencies"] = {
            "status": "violation",
            "evidence": evidence[:12],
            "evidenceTier": "direct",
            "gap": f"{len(components)} import cycle(s) among runtime imports; the first cycle is "
            + " → ".join(cycles[0]) + ".",
            "remediation": "Break each cycle at its weakest edge: move the shared code both sides need into a module neither imports from, or invert the dependency behind an interface the lower module owns.",
            "judgement": "act-on",
            "classification": "observed",
        }
    else:
        checks[f"{AUDIT}.no-circular-dependencies"] = {
            "status": "present",
            "evidence": [f"command: `collect.py import graph` → {len(files)} source files, {snapshot['importEdges']} resolved imports, 0 runtime cycles"],
            "evidenceTier": "supported" if unresolved else "direct",
        }
        if skipped:
            checks[f"{AUDIT}.no-circular-dependencies"]["degradedReason"] = (
                f"{len(skipped)} source files were too many or too large to read, so a cycle through them would be missed"
            )

    packages = workspaces(root)
    directories = [root, *packages]
    tooling, configured = detect_boundary_tooling(root, directories)
    snapshot["boundaryTooling"] = tooling
    key = f"{AUDIT}.boundaries-enforced-by-tooling"
    if tooling:
        evidence = tooling[:3]
        blind = alias_blind_cruiser_configs(root, directories)
        if blind and tooling_runs(root, directories, configured - {"dependency-cruiser"}):
            blind = []
        if tooling_runs(root, directories, configured) and blind:
            checks[key] = {
                "status": "partial",
                "evidence": blind[:3],
                "evidenceTier": "supported",
                "gap": "dependency-cruiser runs, but its configuration has no tsConfig option, so it cannot resolve the repository's path aliases and its rules never see alias imports.",
                "remediation": "Add `options: { tsConfig: { fileName: 'tsconfig.json' } }` and a `not-to-unresolvable` rule, then prove the check fails on a deliberate alias import that crosses a boundary.",
                "judgement": "act-on",
            }
        elif tooling_runs(root, directories, configured):
            checks[key] = {"status": "present", "evidence": evidence, "evidenceTier": "supported"}
        else:
            checks[key] = {
                "status": "partial",
                "evidence": evidence,
                "evidenceTier": "supported",
                "gap": "Boundary rules are configured but no package script or workflow runs them, so nothing fails when a boundary is crossed.",
                "remediation": "Run the boundary check in the lint script and in continuous integration, then prove it fails by adding a deliberate violation on a scratch branch.",
                "judgement": "act-on",
            }
    else:
        checks[key] = {
            "status": "missing",
            "evidence": ["command: `collect.py boundary detection` → no dependency-cruiser configuration, eslint-plugin-boundaries, Nx module boundary rule, restricted-path rule, or import-cycle rule found"],
            "evidenceTier": "direct",
            "gap": "No tool enforces module boundaries, so any file can import any other and the structure erodes one shortcut at a time.",
            "remediation": "Add dependency-cruiser (or eslint-plugin-boundaries) with a no-cycle rule and the repository's layer or feature rules, run it in continuous integration, and prove it fails on a deliberate violation.",
            "judgement": "act-on",
        }

    key = f"{AUDIT}.cross-workspace-contracts-respected"
    if len(packages) < 2:
        checks[key] = {"applicability": "not-applicable", "reason": "The repository is not a multi-package workspace."}
    else:
        def owner(path: Path) -> Path | None:
            for package in sorted(packages, key=lambda package: -len(package.parts)):
                if package in path.parents:
                    return package
            return None
        known_files = set(graph)
        entries = {package: package_entries(package, known_files) for package in packages}
        deep: list[tuple[str, str]] = []
        for importer, edges in graph.items():
            source = owner(importer)
            for target, (line, specifier, _) in edges.items():
                destination = owner(target)
                if not (source and destination and source != destination):
                    continue
                if specifier.startswith(".") or target not in entries[destination]:
                    deep.append((relative(root, importer), cite(root, importer, line, specifier)))
        snapshot["workspaces"] = [relative(root, package) for package in packages]
        production = [entry for path, entry in deep if not is_test_path(path)]
        tests_only = [entry for path, entry in deep if is_test_path(path)]
        if deep:
            only_tests = not production
            checks[key] = {
                "status": "partial" if only_tests else "violation",
                "evidence": (production or tests_only)[:10],
                "evidenceTier": "direct",
                "gap": (
                    f"{len(deep)} import(s) from test files reach into another workspace's internal files; tests share fixtures through internals."
                    if only_tests
                    else f"{len(production)} import(s) in production code reach into another workspace's internal files instead of its entry points."
                ),
                "remediation": "Import other workspaces by package name through their `exports` entry points; move shared test fixtures into a test-utilities package; add `exports` maps so internals cannot be imported.",
                "judgement": "consider" if only_tests else "act-on",
            }
        else:
            checks[key] = {
                "status": "present",
                "evidence": [f"command: `collect.py import graph` → 0 imports into another workspace's internal files across {len(packages)} workspaces"],
                "evidenceTier": "supported",
            }

    fan_in: Counter[Path] = Counter()
    for importer, edges in graph.items():
        for target in edges:
            fan_in[target] += 1
    fan_out = {node: len(edges) for node, edges in graph.items()}
    hubs = sorted(
        (node for node in graph if fan_in[node] >= 10 and fan_out[node] >= 10),
        key=lambda node: (-(fan_in[node] * fan_out[node]), relative(root, node)),
    )[:10]
    snapshot["hubs"] = [
        {"path": relative(root, node), "fanIn": fan_in[node], "fanOut": fan_out[node]} for node in hubs
    ]
    snapshot["mostImported"] = [
        {"path": relative(root, node), "fanIn": count} for node, count in fan_in.most_common(10)
    ]
    entry_markers = ("/app/", "/pages/", "/routes/", "main.", "index.", "server.", "cli.", ".test.", ".spec.", ".config.", ".stories.", "/scripts/", "/bin/")
    orphans = [
        relative(root, node)
        for node in sorted(graph)
        if fan_in[node] == 0
        and "/" in relative(root, node)
        and not any(marker in "/" + relative(root, node) for marker in entry_markers)
    ]
    snapshot["orphanCandidates"] = {"count": len(orphans), "examples": orphans[:15]}

    counts, commits = churn(root, months)
    snapshot["hotspots"] = [{"path": path, "commits": count} for path, count in counts.most_common(15)]
    snapshot["changeCoupling"] = change_coupling(commits, counts)
    snapshot["historyWindowMonths"] = months
    decision_directories = [name for name in ("docs/adr", "docs/decisions", "docs/architecture/decisions", "adr", "decisions") if (root / name).is_dir()]
    snapshot["decisionRecords"] = decision_directories
    snapshot["glossary"] = [name for name in ("GLOSSARY.md", "CONTEXT.md", "docs/glossary.md", "UBIQUITOUS_LANGUAGE.md") if (root / name).is_file()]
    return {"checks": checks, "snapshot": snapshot}


def history_months(thresholds: list[str], default: int) -> int:
    """Read `months=<n>` from the run's recorded threshold overrides."""
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
    parser.add_argument("--enrichment", action="append", default=[])
    parser.add_argument("--threshold", action="append", default=[], help="a recorded key=value override; months=<n> widens the history window")
    parser.add_argument("--months", type=int, default=6, help="history window when no months threshold is recorded")
    arguments = parser.parse_args(argv)
    root = Path(arguments.repository).resolve()
    json.dump(collect(root, history_months(arguments.threshold, arguments.months)), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
