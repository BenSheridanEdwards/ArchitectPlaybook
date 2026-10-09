#!/usr/bin/env python3
"""Deterministic, read-only collector for /security-audit.

Decides the checks that code can decide reliably:

- secrets in tracked files, from high-confidence credential formats and from
  secret-named values in committed environment files, cited with the secret
  replaced by `<REDACTED>` so no value ever leaves this script;
- whether local environment files are ignored by Git, and by which rule;
- `eval`, `new Function`, and string-form timers in shipped source;
- with `--enrichment=--with-scan` and gitleaks installed, secrets anywhere in
  Git history, reported as counts, rule names, and commits only.

It also records Layer 0 facts the model reads first: the framework and router,
every Server Action and route handler, middleware, authentication, validation,
sanitizer, and AI libraries, and candidate sites for each dangerous sink.

The output follows the audit protocol's collector contract: a JSON object with
`checks` (results keyed by checkId) and `snapshot` (Layer 0 facts). The script
never writes to the repository. Standard library only; Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

AUDIT = "security-audit"
REDACTED = "<REDACTED>"
SOURCE_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")
SKIPPED_DIRECTORIES = {
    ".git", "node_modules", "dist", "build", "out", "coverage", ".next", ".nuxt",
    ".turbo", ".cache", ".vercel", ".output", "storybook-static", ".architect-audits",
    "graphify-out", ".worktrees",
}
MAX_FILES = 50_000
MAX_FILE_BYTES = 2_000_000
BINARY_SNIFF_BYTES = 8000
MAX_EVIDENCE = 12
MAX_SITES = 30
TEST_PATH_PATTERN = re.compile(
    r"(^|/)(__tests__|__mocks__|tests?|e2e|fixtures?|cypress|playwright)/|\.(test|spec|stories)\.[cm]?[jt]sx?$"
)

# High-confidence credential formats. `keep` is a non-secret prefix kept in the
# citation so a reader can tell what kind of credential it is; `secret` is the
# part that is always replaced by <REDACTED>.
SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key-id", re.compile(r"\b(?P<keep>A(?:KIA|SIA))(?P<secret>[0-9A-Z]{16})\b")),
    ("private-key", re.compile(r"-----BEGIN (?P<secret>(?:[A-Z]+ )?PRIVATE KEY)(?: BLOCK)?-----")),
    ("github-token", re.compile(r"\b(?P<keep>gh[pousr]_)(?P<secret>[A-Za-z0-9]{36,})\b")),
    ("github-fine-grained-token", re.compile(r"\b(?P<keep>github_pat_)(?P<secret>[A-Za-z0-9_]{50,})")),
    ("gitlab-token", re.compile(r"\b(?P<keep>glpat-)(?P<secret>[A-Za-z0-9_-]{20,})")),
    ("stripe-live-secret-key", re.compile(r"\b(?P<keep>[rs]k_live_)(?P<secret>[A-Za-z0-9]{20,})")),
    ("stripe-webhook-secret", re.compile(r"\b(?P<keep>whsec_)(?P<secret>[A-Za-z0-9+/=]{32,})")),
    ("slack-token", re.compile(r"\b(?P<keep>xox[abposr]-)(?P<secret>[A-Za-z0-9-]{10,})")),
    ("slack-webhook", re.compile(r"(?P<keep>https://hooks\.slack\.com/services/)(?P<secret>[A-Za-z0-9/_-]{20,})")),
    ("openai-api-key", re.compile(r"\b(?P<keep>sk-(?:proj|svcacct|admin)-)(?P<secret>[A-Za-z0-9_-]{40,})")),
    ("openai-legacy-api-key", re.compile(r"\b(?P<keep>sk-)(?P<secret>[A-Za-z0-9]{20}T3BlbkFJ[A-Za-z0-9]{20})\b")),
    ("anthropic-api-key", re.compile(r"\b(?P<keep>sk-ant-(?:api|admin)[0-9]{2}-)(?P<secret>[A-Za-z0-9_-]{80,})")),
    ("npm-token", re.compile(r"\b(?P<keep>npm_)(?P<secret>[A-Za-z0-9]{36})\b")),
    ("sendgrid-api-key", re.compile(r"\b(?P<keep>SG\.)(?P<secret>[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43})\b")),
    (
        "connection-string-password",
        re.compile(
            r"\b(?P<keep>(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?)://[^\s:/@'\"`]+:)"
            r"(?P<secret>[^\s@'\"`/]{6,})@(?P<host>[^\s/:'\"`?,;]+)"
        ),
    ),
)
# The audit protocol rejects any evidence or snapshot that matches these
# patterns. They mirror its list, so the collector never emits a citation the
# protocol would reject, and a value that slipped past redaction is caught here.
PROTOCOL_GUARD_PATTERNS = (
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
LOCAL_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "db", "database", "postgres",
    "postgresql", "mysql", "mariadb", "mongo", "mongodb", "redis", "rabbitmq",
}
PLACEHOLDER_PATTERN = re.compile(
    r"(?i)^(?:|changeme|change[-_]?me|password|passw(?:or)?d|pass|secret|root|admin|test|testing|example|"
    r"dummy|placeholder|todo|fixme|none|null|undefined|your[-_].*|.*example.*|x+|\*+|<.*>|\$\{.*\}|\{\{.*\}\}|%s)$"
)
ENV_LINE_PATTERN = re.compile(r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(?P<value>.*)$")
SECRET_NAME_PATTERN = re.compile(
    r"(?i)(?:SECRET|PASSWORD|PASSWD|PRIVATE|TOKEN|CREDENTIAL|API_?KEY|ACCESS_?KEY|SIGNING_?KEY|"
    r"ENCRYPTION_?KEY|SERVICE_ROLE|DATABASE_URL|DB_URL|CONNECTION_STRING)"
)
PUBLIC_PREFIXES = ("NEXT_PUBLIC_", "VITE_", "PUBLIC_", "EXPO_PUBLIC_", "REACT_APP_", "GATSBY_", "NUXT_PUBLIC_")
PUBLIC_NAME_PATTERN = re.compile(r"\b(?:NEXT_PUBLIC|VITE|EXPO_PUBLIC|REACT_APP|GATSBY|NUXT_PUBLIC|PUBLIC)_[A-Z0-9_]+\b")
PUBLIC_SECRET_WORDS = re.compile(r"SECRET|PRIVATE|PASSWORD|PASSWD|TOKEN|CREDENTIAL|SERVICE_ROLE|SERVICE_KEY|ADMIN_KEY|SIGNING")
ENV_TEMPLATE_PATTERN = re.compile(r"(?i)\.(?:example|sample|template|dist)(?:\.|$)")

DYNAMIC_CODE_PATTERN = re.compile(
    r"(?<![\w$.])(?:(?:window|globalThis|self)\.)?eval\s*\("
    r"|(?<![\w$.])new\s+Function\s*\("
    r"|(?<![\w$.])Function\s*\(\s*['\"`]"
    r"|(?<![\w$.])(?:(?:window|globalThis|self)\.)?set(?:Timeout|Interval)\s*\(\s*['\"`]"
)
USE_SERVER_PATTERN = re.compile(r"""^[ \t]*(['"])use server\1""", re.M)
USE_CLIENT_PATTERN = re.compile(r"""^\s*(['"])use client\1""")
EXPORTED_FUNCTION_PATTERN = re.compile(
    r"^[ \t]*export\s+(?:default\s+)?(?:async\s+)?function\s*\*?\s*(?P<function>[A-Za-z_$][\w$]*)"
    r"|^[ \t]*export\s+const\s+(?P<constant>[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=\s*(?:async\b|\()",
    re.M,
)
HTTP_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")
ROUTE_METHOD_PATTERN = re.compile(
    r"^[ \t]*export\s+(?:async\s+function\s+|function\s+|const\s+)(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b"
    r"|^[ \t]*export\s*\{(?P<list>[^}]*)\}",
    re.M,
)
LOADER_ACTION_PATTERN = re.compile(r"^[ \t]*export\s+(?:async\s+)?function\s+(?P<name>action|loader)\b", re.M)
NODE_ROUTE_PATTERN = re.compile(r"\b(?:app|router|server|fastify)\.(?:get|post|put|patch|delete|all|route)\s*\(\s*['\"`]/")
HTML_SINK_PATTERN = re.compile(
    r"\.(?:inner|outer)HTML\s*\+?=(?!=)|\.insertAdjacentHTML\s*\(|\bdocument\.write(?:ln)?\s*\(|\bcreateContextualFragment\s*\("
    r"|\brehype-raw\b|\ballowDangerousHtml\b|\bhtml\s*:\s*true\b|\bv-html\b"
)
DANGEROUS_HTML_PATTERN = re.compile(r"\bdangerouslySetInnerHTML\b")
FETCH_PATTERN = re.compile(r"(?<![\w$.])(?:fetch|got|ky|axios(?:\.(?:get|post|put|patch|delete|request|head))?)\s*\(\s*(?P<argument>[^\s)])")
REDIRECT_PATTERN = re.compile(
    r"(?<![\w$])(?:redirect|permanentRedirect|NextResponse\.redirect|Response\.redirect|res\.redirect|router\.(?:push|replace)|"
    r"(?:window\.)?location\.(?:assign|replace))\s*\(\s*(?P<argument>[^\s)])"
    r"|(?:(?<![\w$])(?:window|document|self|top)\.location(?:\.href)?|(?<![\w$.])location\.href)\s*=(?!=)\s*(?P<assigned>[^\s;])"
)
STORAGE_AUTH_PATTERN = re.compile(
    r"\b(?:localStorage|sessionStorage)\.setItem\s*\(\s*['\"`](?P<key>[^'\"`]*(?:token|jwt|session|auth|bearer|credential)[^'\"`]*)['\"`]",
    re.I,
)
MESSAGE_LISTENER_PATTERN = re.compile(r"addEventListener\s*\(\s*['\"`]message['\"`]|\bonmessage\s*=(?!=)")
SERVER_ONLY_PATTERN = re.compile(r"""^\s*import\s+['"]server-only['"]""", re.M)
AI_HOST_PATTERN = re.compile(
    r"\b(?:api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.mistral\.ai|"
    r"api\.groq\.com|api\.cohere\.(?:ai|com)|openrouter\.ai/api|api\.together\.xyz|api\.deepseek\.com)\b"
)
SECURITY_HEADERS = (
    "Content-Security-Policy", "Strict-Transport-Security", "X-Frame-Options", "X-Content-Type-Options",
    "Referrer-Policy", "Permissions-Policy",
)
DEPLOYMENT_FILES = (
    "vercel.json", "netlify.toml", "_headers", "public/_headers", "static/_headers", "wrangler.toml",
    "wrangler.json", "fly.toml", "Dockerfile", "nginx.conf", "firebase.json", "amplify.yml", "render.yaml",
    "app.yaml", "staticwebapp.config.json",
)

AUTH_LIBRARIES = (
    "next-auth", "@auth/core", "@auth/nextjs", "better-auth", "@clerk/nextjs", "@clerk/clerk-react", "@auth0/nextjs-auth0",
    "@auth0/auth0-react", "@supabase/ssr", "@supabase/auth-helpers-nextjs", "@supabase/supabase-js", "lucia",
    "iron-session", "jose", "jsonwebtoken", "firebase", "firebase-admin", "@kinde-oss/kinde-auth-nextjs",
    "@workos-inc/authkit-nextjs", "passport", "oidc-client-ts", "@azure/msal-browser", "@azure/msal-react",
)
VALIDATION_LIBRARIES = ("zod", "valibot", "yup", "joi", "@sinclair/typebox", "arktype", "superstruct", "class-validator", "@effect/schema", "next-safe-action")
SANITIZERS = ("dompurify", "isomorphic-dompurify", "sanitize-html", "xss", "rehype-sanitize", "@braintree/sanitize-url")
AI_PACKAGES = (
    "ai", "openai", "@anthropic-ai/sdk", "@google/generative-ai", "@google/genai", "langchain", "llamaindex",
    "@mistralai/mistralai", "cohere-ai", "groq-sdk", "ollama", "replicate", "@huggingface/inference",
    "@openai/agents", "@modelcontextprotocol/sdk", "@vercel/ai",
)
AI_PACKAGE_PREFIXES = ("@ai-sdk/", "@langchain/", "@llamaindex/", "@anthropic-ai/")
FRAMEWORK_PACKAGES = (
    "next", "react", "react-dom", "@remix-run/node", "@remix-run/react", "react-router", "@react-router/dev",
    "vite", "express", "fastify", "hono", "@nestjs/core", "@trpc/server", "astro", "@sveltejs/kit", "nuxt",
)
LLM_CHECKS = (
    "model-tool-calls-authorized", "model-output-rendered-as-text", "no-secrets-in-prompts", "model-usage-bounded",
)


# --------------------------------------------------------------------------- files


def git(root: Path, *arguments: str, timeout: int = 120) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return completed.stdout if completed.returncode == 0 else None


def listed_files(root: Path, tracked_only: bool) -> list[str]:
    """Repository-relative files: tracked, or tracked plus untracked files Git does not ignore."""
    arguments = ["ls-files", "-z", "--cached"] if tracked_only else ["ls-files", "-z", "--cached", "--others", "--exclude-standard"]
    output = git(root, "-c", "core.quotePath=false", *arguments)
    if output is None:
        if tracked_only:
            return []
        entries = [path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file()]
    else:
        entries = [entry for entry in output.split("\0") if entry]
    files = []
    for entry in sorted(set(entries)):
        parts = entry.split("/")
        if any(part in SKIPPED_DIRECTORIES for part in parts[:-1]):
            continue
        path = root / entry
        if path.is_symlink() or not path.is_file():
            continue
        files.append(entry)
    return files[:MAX_FILES]


def read_text(path: Path) -> str | None:
    """The file's text as the protocol reads it, or None for binary and oversized files."""
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\0" in data[:BINARY_SNIFF_BYTES]:
        return None
    return data.decode("utf-8", errors="replace")


def split_lines(text: str) -> list[str]:
    """Split on newlines only, as the protocol and grep do, ignoring a trailing carriage return."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def mask_comments(text: str) -> str:
    """Blank out comments, keeping strings, offsets, and line numbers unchanged."""
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


def is_test_path(path: str) -> bool:
    return bool(TEST_PATH_PATTERN.search(path))


def location(path: str) -> str:
    """A path the protocol can parse; paths with spaces go in backticks."""
    return f"`{path}`" if any(character.isspace() for character in path) else path


def safe_fragment(text: str) -> str:
    """Text that can sit inside a backtick quote."""
    return " ".join(text.replace("`", "'").split())


def line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class SourceFile:
    """A JavaScript or TypeScript file, read once, with comments masked."""

    def __init__(self, root: Path, path: str) -> None:
        self.path = path
        self.text = read_text(root / path) or ""
        self.masked = mask_comments(self.text)
        self.lines = split_lines(self.text)
        self.is_client = bool(USE_CLIENT_PATTERN.match(self.masked))

    def site(self, offset: int) -> str:
        return f"{self.path}:{line_of(self.masked, offset)}"


# --------------------------------------------------------------------------- secrets


def looks_like_placeholder(secret: str) -> bool:
    return len(set(secret)) < 5 or "EXAMPLE" in secret.upper() or bool(PLACEHOLDER_PATTERN.match(secret))


def secret_spans(line: str) -> list[tuple[int, int, str]]:
    """(start, end, rule) for every high-confidence secret on one line, without overlaps."""
    spans: list[tuple[int, int, str]] = []
    for rule, pattern in SECRET_RULES:
        for match in pattern.finditer(line):
            secret = match.group("secret")
            if rule == "connection-string-password":
                host = match.group("host").lower()
                if (
                    host in LOCAL_HOSTS
                    or host.endswith((".local", ".test", ".example", ".invalid", ".localhost", "example.com"))
                    or looks_like_placeholder(secret)
                    or any(character in secret for character in "${<*")
                ):
                    continue
            elif rule != "private-key" and looks_like_placeholder(secret):
                continue
            start, end = match.span("secret")
            if not any(start < other_end and other_start < end for other_start, other_end, _ in spans):
                spans.append((start, end, rule))
    return sorted(spans)


def redacted_citation(path: str, number: int, line: str, spans: list[tuple[int, int]], label: str) -> str:
    """A citation whose quote matches the line with every secret replaced by <REDACTED>.

    The protocol reads <REDACTED> inside a quote as "any text", so the citation
    verifies against the real line while the value never appears in the output.
    """
    pieces: list[str | None] = []
    position = 0
    for start, end in spans:
        pieces.append(line[position:start])
        pieces.append(None)
        position = end
    pieces.append(line[position:])
    prefix = str(pieces[0])
    before = prefix[-24:]
    if len(prefix) > 24 and " " in before:
        before = re.sub(r"^[=:,;+|&?]+\s*", "", before[before.index(" ") + 1:])
    before = before.lstrip()
    after = re.match(r"[^\w`]{0,3}", str(pieces[-1])).group(0)  # type: ignore[union-attr]
    middle = "".join(REDACTED if piece is None else piece for piece in pieces[1:-1])
    if "`" in before:
        before = before[before.rindex("`") + 1:]
    if "`" in middle:
        middle = REDACTED
        after = ""
    quote = " ".join((before + middle + after).split())
    entry = f"{location(path)}:{number} — {label}: `{quote}`"
    if quote.replace(REDACTED, "").strip() == "" or any_secret(entry):
        return f"{location(path)}:{number} — {label} (value redacted)"
    return entry


def any_secret(text: str) -> bool:
    """Whether text holds anything the protocol or this collector would read as a secret."""
    return any(pattern.search(text) for pattern in PROTOCOL_GUARD_PATTERNS) or any(
        not looks_like_placeholder(match.group("secret"))
        for _, pattern in SECRET_RULES
        for match in pattern.finditer(text)
    )


def is_env_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (name == ".env" or name.startswith(".env.") or name.endswith(".env")) and not ENV_TEMPLATE_PATTERN.search(name)


def env_secret_assignments(path: str, lines: list[str]) -> list[str]:
    """Citations for secret-named variables given a real-looking value in a committed environment file."""
    found = []
    for number, line in enumerate(lines, start=1):
        match = ENV_LINE_PATTERN.match(line)
        if not match:
            continue
        name = match.group("name")
        if name.startswith(PUBLIC_PREFIXES) or not SECRET_NAME_PATTERN.search(name):
            continue
        raw = match.group("value").strip()
        value = raw
        if value[:1] in "'\"" and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        lowered = value.lower()
        if (
            looks_like_placeholder(value)
            or value.startswith("$")
            or "localhost" in lowered
            or "127.0.0.1" in lowered
            or "host.docker.internal" in lowered
        ):
            continue
        start = match.start("value")
        found.append(redacted_citation(path, number, line, [(start, len(line))], f"secret-named variable {name}"))
    return found


def scan_secrets(root: Path, tracked: list[str]) -> tuple[list[str], Counter[str], dict[str, Any]]:
    """Evidence for secrets in tracked files, counts by rule, and committed environment-file facts."""
    evidence: list[str] = []
    rules: Counter[str] = Counter()
    env_files: list[str] = []
    local_env_files: list[str] = []
    scanned = 0
    for path in tracked:
        text = read_text(root / path)
        if text is None:
            continue
        scanned += 1
        lines = split_lines(text)
        for number, line in enumerate(lines, start=1):
            spans = secret_spans(line)
            if not spans:
                continue
            for _, _, rule in spans:
                rules[rule] += 1
            label = ", ".join(sorted({rule for _, _, rule in spans}))
            evidence.append(redacted_citation(path, number, line, [(start, end) for start, end, _ in spans], label))
        if is_env_file(path):
            env_files.append(path)
            if path.endswith(".local"):
                local_env_files.append(path)
            assignments = env_secret_assignments(path, lines)
            if assignments:
                rules["secret-named-environment-variable"] += len(assignments)
            evidence.extend(assignments)
    facts = {"trackedFilesScanned": scanned, "committedEnvironmentFiles": env_files}
    if local_env_files:
        facts["committedLocalEnvironmentFiles"] = local_env_files
    return evidence, rules, facts


def run_gitleaks(root: Path) -> tuple[list[dict[str, Any]] | None, str]:
    """Scan every commit with gitleaks. Returns (findings, command) or (None, reason)."""
    executable = shutil.which("gitleaks")
    if executable is None:
        return None, "gitleaks is not installed"
    with tempfile.TemporaryDirectory() as directory:
        report = Path(directory) / "gitleaks.json"
        common = [
            "--redact", "--no-banner", "--log-level", "error", "--exit-code", "0",
            "--report-format", "json", "--report-path", str(report), "--log-opts=--all",
        ]
        attempts = (
            ("gitleaks git --redact --log-opts=--all", [executable, "git", str(root), *common]),
            ("gitleaks detect --redact --log-opts=--all", [executable, "detect", "--source", str(root), *common]),
        )
        failure = "gitleaks did not run"
        for label, command in attempts:
            try:
                completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=400)
            except (OSError, subprocess.TimeoutExpired) as error:
                return None, f"gitleaks did not complete ({type(error).__name__})"
            if completed.returncode == 0 and report.is_file():
                try:
                    findings = json.loads(report.read_text(encoding="utf-8") or "[]")
                except ValueError:
                    return None, "gitleaks wrote a report that is not JSON"
                return (findings if isinstance(findings, list) else []), label
            failure = f"gitleaks exited {completed.returncode}"
    return None, failure


def summarise_history(findings: list[dict[str, Any]]) -> dict[str, Any]:
    """Counts, rule names, commits, and files only. Secret and Match fields are never read."""
    rules = Counter(str(item.get("RuleID", "unknown")) for item in findings if isinstance(item, dict))
    commits = sorted({str(item.get("Commit", ""))[:12] for item in findings if isinstance(item, dict) and item.get("Commit")})
    files = sorted({str(item.get("File", "")) for item in findings if isinstance(item, dict) and item.get("File")})
    summary = {"findings": sum(rules.values()), "rules": dict(sorted(rules.items())), "commits": commits[:20], "files": files[:20]}
    return summary if not any_secret(json.dumps(summary)) else {"findings": sum(rules.values()), "rules": dict(sorted(rules.items()))}


def secrets_check(root: Path, tracked: list[str], with_scan: bool, snapshot: dict[str, Any]) -> dict[str, Any]:
    evidence, rules, facts = scan_secrets(root, tracked)
    snapshot["environmentFiles"] = facts
    scan: dict[str, Any] = {"trackedFilesScanned": facts["trackedFilesScanned"], "findings": dict(sorted(rules.items())), "historyScanned": False}
    history: dict[str, Any] | None = None
    degraded = None
    history_entry = None
    if with_scan:
        findings, command = run_gitleaks(root)
        if findings is None:
            degraded = f"--with-scan was requested but Git history was not scanned: {command}"
            scan["historyScan"] = command
        else:
            history = summarise_history(findings)
            scan["historyScanned"] = True
            scan["history"] = history
            rule_list = ", ".join(f"{name} ×{count}" for name, count in history["rules"].items()) or "none"
            history_entry = (
                f"command: `{command}` → {history['findings']} finding(s) in {len(history.get('commits', []))} commit(s) "
                f"({rule_list}); values redacted"
            )
    snapshot["secretScan"] = scan
    if evidence or (history and history["findings"]):
        result: dict[str, Any] = {
            "status": "violation",
            "evidence": (evidence[:MAX_EVIDENCE] + ([history_entry] if history_entry else [])) or [history_entry],
            "evidenceTier": "direct" if evidence else "supported",
            "gap": (
                f"{len(evidence)} credential(s) in tracked files"
                + (f", and {history['findings']} finding(s) in Git history" if history and history["findings"] else "")
                + ". Anyone who can read the repository, or any clone of it, can use them."
            ),
            "remediation": (
                "Rotate each credential first, because removing it from the tree does not remove it from history. "
                "Then load it from the environment or a secret manager, keep it out of client code, and add a secret "
                "scan to the pre-commit hook and continuous integration."
            ),
            "judgement": "act-on",
        }
        if not evidence and history:
            generic_only = set(history["rules"]) <= {"generic-api-key"}
            result["judgement"] = "consider" if generic_only else "act-on"
            result["gap"] = (
                f"No secrets in the current tree, but gitleaks found {history['findings']} in Git history. "
                "A secret removed from the tree is still in every clone."
            )
        if degraded:
            result["degradedReason"] = degraded
        return result
    entry = f"command: `collect.py secret scan` → 0 high-confidence credentials and 0 secret-named values in {facts['trackedFilesScanned']} tracked files"
    result = {"status": "present", "evidence": [entry] + ([history_entry] if history_entry else []), "evidenceTier": "direct"}
    if degraded:
        result["degradedReason"] = degraded
    return result


# --------------------------------------------------------------------------- environment files


def app_roots(root: Path, files: list[str]) -> list[str]:
    """The repository root and every folder that holds a Next.js or Vite configuration."""
    roots = {""}
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if re.match(r"^(?:next|vite)\.config\.[cm]?[jt]s$", name) and "/" in path:
            roots.add(path.rsplit("/", 1)[0])
    return sorted(roots)


def env_ignore_check(root: Path, files: list[str], tracked: set[str]) -> dict[str, Any]:
    probes = []
    for folder in app_roots(root, files):
        prefix = f"{folder}/" if folder else ""
        for name in (".env", ".env.local", ".env.development.local", ".env.production.local"):
            if f"{prefix}{name}" not in tracked:
                probes.append(f"{prefix}{name}")
    if not probes:
        return {
            "status": "present",
            "evidence": ["command: `git ls-files` → every environment file name is tracked on purpose, so there is no local file to ignore"],
            "evidenceTier": "direct",
        }
    output = git(root, "check-ignore", "--no-index", "-v", "-n", *probes) or ""
    covered: list[str] = []
    local_only: list[str] = []
    uncovered: list[str] = []
    for raw in output.splitlines():
        rule, _, path = raw.partition("\t")
        source, _, rest = rule.partition(":")
        line, _, pattern = rest.partition(":")
        if not source or pattern.startswith("!"):
            uncovered.append(path)
        elif source.startswith(("/", ".git/")) or not (root / source).is_file() or not line.isdigit():
            local_only.append(path)
        else:
            covered.append(f"{location(source)}:{line} — `{safe_fragment(pattern)}` ignores {path}")
    if not output:
        uncovered = probes
    if not uncovered and not local_only:
        return {"status": "present", "evidence": sorted(set(covered))[:4], "evidenceTier": "direct"}
    missing_entry = f"command: `git check-ignore --no-index` → not ignored: {', '.join(uncovered + local_only)}"
    if not covered and not local_only:
        return {
            "status": "missing",
            "evidence": [missing_entry],
            "evidenceTier": "direct",
            "gap": "No ignore rule covers local environment files, so the first `git add .` after creating one commits its secrets.",
            "remediation": "Add `.env*` to `.gitignore` with `!.env.example` for the committed template, then confirm with `git check-ignore -v .env.local`.",
            "judgement": "act-on",
        }
    gap = "Some local environment files are not ignored by a rule in the repository"
    if local_only:
        gap += "; only a personal or global ignore file covers " + ", ".join(local_only) + ", which does not protect anyone else who clones it"
    return {
        "status": "partial",
        "evidence": [missing_entry] + sorted(set(covered))[:3],
        "evidenceTier": "direct",
        "gap": gap + ".",
        "remediation": "Add `.env*` to the repository's `.gitignore` with `!.env.example` for the committed template.",
        "judgement": "act-on",
    }


# --------------------------------------------------------------------------- dynamic code


def is_declaration(masked: str, match_end: int) -> bool:
    """Whether a matched `eval(` is a method or function declaration rather than a call.

    A declaration's parameter list is followed by a body or a return type, as in
    `eval(x: string): number` or `function eval(code) {`.
    """
    open_index = masked.rfind("(", 0, match_end)
    depth = 0
    for index in range(open_index, min(len(masked), open_index + 2000)):
        if masked[index] == "(":
            depth += 1
        elif masked[index] == ")":
            depth -= 1
            if depth == 0:
                following = masked[index + 1:index + 40].lstrip()
                return following[:1] in (":", "{")
    return False


def dynamic_code_check(sources: list[SourceFile], snapshot: dict[str, Any]) -> dict[str, Any]:
    production: list[str] = []
    tests = 0
    for source in sources:
        if source.path.endswith(".min.js"):
            continue
        for match in DYNAMIC_CODE_PATTERN.finditer(source.masked):
            if match.group(0).rstrip().endswith("(") and is_declaration(source.masked, match.end()):
                continue
            if is_test_path(source.path):
                tests += 1
                continue
            number = line_of(source.masked, match.start())
            call = source.text[match.start():match.end()]
            call = call[: call.index("(") + 1]
            production.append(f"{location(source.path)}:{number} — `{safe_fragment(call)}`")
    snapshot["dynamicCodeExecution"] = {"shippedSites": len(production), "testSites": tests}
    if production:
        return {
            "status": "violation",
            "evidence": production[:MAX_EVIDENCE],
            "evidenceTier": "direct",
            "gap": (
                f"{len(production)} site(s) run strings as code. Each is code injection if attacker-influenced text reaches it, "
                "and each forces a Content Security Policy to allow 'unsafe-eval'."
            ),
            "remediation": "Replace each with a parser, a lookup table, or a function reference. Trace any that remain to prove only constant text reaches them.",
            "judgement": "consider",
        }
    note = f"; {tests} in tests only" if tests else ""
    return {
        "status": "present",
        "evidence": [f"command: `collect.py dynamic-code scan` → 0 eval, new Function, or string timer calls in {len(sources)} source files{note}"],
        "evidenceTier": "direct",
    }


# --------------------------------------------------------------------------- snapshot


def dependencies(root: Path, files: list[str]) -> dict[str, str]:
    """Every dependency declared by any package.json, with the version from the shallowest manifest."""
    found: dict[str, str] = {}
    manifests = sorted((path for path in files if path.rsplit("/", 1)[-1] == "package.json"), key=lambda path: (path.count("/"), path))
    for manifest in manifests:
        package = load_json(root / manifest)
        for field in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            section = package.get(field)
            if isinstance(section, dict):
                for name, version in section.items():
                    found.setdefault(str(name), str(version))
    return found


def detect_router(files: list[str]) -> str | None:
    app = any(re.search(r"(^|/)app/(.*/)?(page|layout|route)\.[cm]?[jt]sx?$", path) for path in files)
    pages = any(re.search(r"(^|/)pages/.+\.[cm]?[jt]sx?$", path) for path in files)
    if app and pages:
        return "app+pages"
    return "app" if app else "pages" if pages else None


def server_actions(sources: list[SourceFile]) -> list[str]:
    """Every file-level and inline Server Action, as `path:line name`."""
    actions: list[str] = []
    for source in sources:
        masked = source.masked
        matches = list(USE_SERVER_PATTERN.finditer(masked))
        if not matches:
            continue
        file_level = masked[: matches[0].start()].strip() == ""
        if file_level:
            for export in EXPORTED_FUNCTION_PATTERN.finditer(masked):
                name = export.group("function") or export.group("constant")
                actions.append(f"{source.site(export.start())} {name}")
            if not any(entry.startswith(f"{source.path}:") for entry in actions):
                actions.append(f"{source.site(matches[0].start())} (file-level directive)")
        for match in matches[1:] if file_level else matches:
            actions.append(f"{source.site(match.start())} (inline action)")
    return actions


def route_handlers(sources: list[SourceFile]) -> list[str]:
    handlers: list[str] = []
    for source in sources:
        path = source.path
        name = path.rsplit("/", 1)[-1]
        if re.match(r"^route\.[cm]?[jt]sx?$", name) and re.search(r"(^|/)app/", path):
            methods = []
            for match in ROUTE_METHOD_PATTERN.finditer(source.masked):
                if match.group("method"):
                    methods.append(match.group("method"))
                else:
                    methods += [method for method in HTTP_METHODS if re.search(rf"\b{method}\b", match.group("list") or "")]
            handlers.append(f"{path} {','.join(dict.fromkeys(methods)) or '(no exported methods found)'}")
        elif re.search(r"(^|/)pages/api/", path):
            handlers.append(f"{path} (pages API route)")
        else:
            for match in LOADER_ACTION_PATTERN.finditer(source.masked):
                if re.search(r"(^|/)(app/)?routes/", path):
                    handlers.append(f"{source.site(match.start())} {match.group('name')}")
    return handlers


def sites(sources: Iterable[SourceFile], pattern: re.Pattern[str], accept: Any = None) -> list[str]:
    found = []
    for source in sources:
        for match in pattern.finditer(source.masked):
            if accept is None or accept(source, match):
                found.append(source.site(match.start()))
    return found


def is_dynamic_argument(source: SourceFile, match: re.Match[str], group: str = "argument") -> bool:
    """Whether a call's first argument is anything other than a plain string literal."""
    start = match.start(group) if match.group(group) else match.start("assigned")
    rest = source.masked[start:start + 200]
    literal = re.match(r"""(?:(['"])(?:(?!\1)[^\\\n])*\1|`[^`$]*`)[ \t]*(?:[,);]|$)""", rest, re.M)
    return literal is None


def summarise(entries: list[str]) -> dict[str, Any]:
    return {"count": len(entries), "sites": entries[:MAX_SITES]}


def build_snapshot(root: Path, files: list[str], sources: list[SourceFile], snapshot: dict[str, Any]) -> bool:
    """Record Layer 0 facts and return whether an AI SDK or model API is in use."""
    declared = dependencies(root, files)
    snapshot["frameworks"] = {name: declared[name] for name in FRAMEWORK_PACKAGES if name in declared}
    router = detect_router(files)
    if router and "next" in declared:
        snapshot["nextRouter"] = router
    snapshot["serverActions"] = summarise(server_actions(sources))
    snapshot["routeHandlers"] = summarise(route_handlers(sources))
    snapshot["otherServerRoutes"] = summarise(sites(sources, NODE_ROUTE_PATTERN, lambda source, _: not is_test_path(source.path)))
    snapshot["middleware"] = [
        path for path in files
        if re.match(r"^(?:src/)?(?:middleware|proxy)\.[cm]?[jt]s$", path)
        or re.match(r"^(?:apps|packages)/[^/]+/(?:src/)?(?:middleware|proxy)\.[cm]?[jt]s$", path)
    ]
    snapshot["authenticationLibraries"] = [name for name in AUTH_LIBRARIES if name in declared]
    snapshot["validationLibraries"] = [name for name in VALIDATION_LIBRARIES if name in declared]
    snapshot["sanitizers"] = [name for name in SANITIZERS if name in declared]
    ai_packages = sorted(name for name in declared if name in AI_PACKAGES or name.startswith(AI_PACKAGE_PREFIXES))
    ai_hosts = sites(sources, AI_HOST_PATTERN)
    snapshot["aiSdks"] = ai_packages
    if ai_hosts:
        snapshot["modelApiReferences"] = summarise(ai_hosts)
    production = [source for source in sources if not is_test_path(source.path)]
    snapshot["clientComponentFiles"] = sum(1 for source in production if source.is_client)
    snapshot["serverOnlyImports"] = summarise([source.path for source in production if SERVER_ONLY_PATTERN.search(source.masked)])
    snapshot["dangerouslySetInnerHTML"] = summarise(sites(production, DANGEROUS_HTML_PATTERN))
    snapshot["otherHtmlSinks"] = summarise(sites(production, HTML_SINK_PATTERN))
    snapshot["requestsWithDynamicUrl"] = summarise(
        sites([source for source in production if not source.is_client], FETCH_PATTERN, is_dynamic_argument)
    )
    snapshot["dynamicRedirects"] = summarise(sites(production, REDIRECT_PATTERN, is_dynamic_argument))
    storage = []
    for source in production:
        for match in STORAGE_AUTH_PATTERN.finditer(source.masked):
            storage.append(f"{source.site(match.start())} {safe_fragment(match.group('key'))}")
    snapshot["browserStorageAuthKeys"] = summarise(storage)
    snapshot["messageListeners"] = summarise(sites(production, MESSAGE_LISTENER_PATTERN))
    public_names: dict[str, str] = {}
    for path in files:
        if not (path.endswith(SOURCE_SUFFIXES) or is_env_file(path) or ENV_TEMPLATE_PATTERN.search(path.rsplit("/", 1)[-1])):
            continue
        text = read_text(root / path) or ""
        for match in PUBLIC_NAME_PATTERN.finditer(text):
            name = match.group(0)
            if PUBLIC_SECRET_WORDS.search(name) and "PUBLISHABLE" not in name:
                public_names.setdefault(name, f"{path}:{line_of(text, match.start())}")
    snapshot["publicEnvironmentNamesThatLookSecret"] = [f"{name} ({where})" for name, where in sorted(public_names.items())][:MAX_SITES]
    headers: dict[str, list[str]] = {}
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if not (path.endswith(SOURCE_SUFFIXES) or name in {"vercel.json", "netlify.toml", "_headers", "nginx.conf", "firebase.json", "staticwebapp.config.json"} or name.endswith((".conf", ".toml", ".yaml", ".yml"))):
            continue
        if is_test_path(path):
            continue
        text = (read_text(root / path) or "").lower()
        for header in SECURITY_HEADERS:
            if header.lower() in text:
                headers.setdefault(header, []).append(path)
    snapshot["securityHeaderSources"] = {header: paths[:5] for header, paths in headers.items()}
    snapshot["securityHeadersNotFound"] = [header for header in SECURITY_HEADERS if header not in headers]
    snapshot["deploymentConfiguration"] = [path for path in DEPLOYMENT_FILES if (root / path).is_file()]
    return bool(ai_packages or ai_hosts)


# --------------------------------------------------------------------------- collect


def collect(root: Path, enrichment: list[str] | None = None) -> dict[str, Any]:
    flags = {flag.lstrip("-") for flag in enrichment or []}
    with_scan = "with-scan" in flags
    files = listed_files(root, tracked_only=False)
    tracked = listed_files(root, tracked_only=True)
    sources = [SourceFile(root, path) for path in files if path.endswith(SOURCE_SUFFIXES) and not path.endswith(".d.ts")]
    checks: dict[str, Any] = {}
    snapshot: dict[str, Any] = {"sourceFiles": len(sources), "trackedFiles": len(tracked)}

    checks[f"{AUDIT}.no-secrets-in-source"] = secrets_check(root, tracked, with_scan, snapshot)
    checks[f"{AUDIT}.env-files-gitignored"] = env_ignore_check(root, files, set(tracked))
    checks[f"{AUDIT}.no-dynamic-code-execution"] = dynamic_code_check(sources, snapshot)

    uses_models = build_snapshot(root, files, sources, snapshot)
    if not uses_models:
        reason = (
            "No AI SDK in any package.json and no model API host in source, so the application has no "
            "large-language-model feature to audit."
        )
        for name in LLM_CHECKS:
            checks[f"{AUDIT}.{name}"] = {"applicability": "not-applicable", "reason": reason}
    if any_secret(json.dumps(snapshot)):
        snapshot = {"sourceFiles": len(sources), "snapshotWithheld": "a fact looked like a secret, so the snapshot was withheld"}
    return {"checks": checks, "snapshot": snapshot}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--repository", default=".")
    parser.add_argument("--enrichment", action="append", default=[], help="a recorded enrichment flag; --with-scan scans Git history with gitleaks")
    parser.add_argument("--threshold", action="append", default=[], help="a recorded key=value override; this collector defines none")
    arguments = parser.parse_args(argv)
    root = Path(arguments.repository).resolve()
    json.dump(collect(root, arguments.enrichment), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
