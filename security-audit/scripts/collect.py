#!/usr/bin/env python3
"""Deterministic, read-only collector for /security-audit.

Decides the checks that code can decide reliably:

- secrets in tracked files, from high-confidence credential formats, keystore
  files, and secret-named values in committed environment files. Every tracked
  file is scanned, line by line, whatever its folder or size. Citations quote
  only a fixed, non-secret prefix (such as `sk_live_`) or a variable name
  before `<REDACTED>`, so no part of a value ever leaves this script;
- whether local environment files in every application folder are ignored by
  a rule the repository itself tracks, and whether any is committed;
- `eval`, `new Function`, and string-form timers in shipped code, ignoring
  strings, comments, regular expressions, prose, and declarations;
- with `--enrichment=--with-scan` and gitleaks installed, secrets anywhere in
  Git history, reported as counts, rule names, and commits only.

It also records Layer 0 facts the model reads first: the framework and router,
every Server Action, route handler, and data-loading page, middleware,
authentication, validation, sanitizer, rate-limit, and AI libraries, and
candidate sites for each dangerous sink.

The output follows the audit protocol's collector contract: a JSON object with
`checks` (results keyed by checkId) and `snapshot` (Layer 0 facts). The whole
output is checked for credential-looking text before it is printed. The script
never writes to the repository. Standard library only; Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, deque
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import urlsplit

AUDIT = "security-audit"
REDACTED = "<REDACTED>"
WITHHELD = "<withheld: looked like a credential>"
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

# High-confidence credential formats. `keep` is a fixed, non-secret prefix that a
# citation may quote so a reader can tell what kind of credential it is; the
# `secret` group is never quoted, and neither is any other text on the line.
SECRET_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws-access-key-id", re.compile(r"\b(?P<keep>A(?:KIA|SIA))(?P<secret>[0-9A-Z]{16})\b")),
    ("private-key", re.compile(r"(?P<keep>-----BEGIN )(?P<secret>(?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?)-----")),
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
            r"\b(?P<keep>(?:postgres(?:ql)?|mysql|mariadb|mongodb(?:\+srv)?|rediss?|amqps?)://)"
            r"(?P<secret>[^\s:/@'\"`]+:(?P<password>[^\s@'\"`/]{6,}))@(?P<host>[^\s/:'\"`?,;]+)"
        ),
    ),
)
RULES = dict(SECRET_RULES)
KEYSTORE_SUFFIXES = (".p12", ".pfx", ".jks", ".keystore", ".ppk")
PRIVATE_KEY_FILE_NAMES = {"id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
PEM_BODY_PATTERN = re.compile(r"^[A-Za-z0-9+/]{40,}={0,2}$")
PEM_INLINE_BODY_PATTERN = re.compile(r"PRIVATE KEY(?: BLOCK)?-----(?:\\r)?\\n[A-Za-z0-9+/]{40,}")
# Only these shapes may be quoted beside <REDACTED>: fixed rule prefixes and identifiers.
SAFE_PREFIX_PATTERN = re.compile(r"^[A-Za-z0-9_.:/+ -]{1,40}$")
SAFE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,79}$")
# The audit protocol rejects any evidence or snapshot that matches these
# patterns. They mirror its list, so the collector never emits text the
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
# A long run of mixed-case letters and digits that switches character class
# often looks like a generated credential, wherever it appears: in a file or
# folder name, a storage key, or any other text the collector prints.
TOKEN_RUN = re.compile(r"[A-Za-z0-9+=_-]{20,}")
LOCAL_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "host.docker.internal", "db", "database", "postgres",
    "postgresql", "mysql", "mariadb", "mongo", "mongodb", "redis", "rabbitmq",
}
DOCUMENTATION_EXAMPLES = {"AKIAIOSFODNN7EXAMPLE", "AKIAI44QH8DHBEXAMPLE", "ASIAIOSFODNN7EXAMPLE"}
# Whole values that are placeholders, never substrings: `CorrectHorseExample42!` is a secret.
PLACEHOLDER_VALUE_PATTERN = re.compile(
    r"(?i)^(?:|changeme|change[-_ ]?me|replace[-_ ]?me|placeholder|dummy|example|sample|todo|fixme|none|null|"
    r"undefined|secret|password|passw(?:or)?d|pass|test|testing|root|admin|xxx+|\*+|\.{3,}|<[^<>]*>|\$\{[^}]*\}|"
    r"\{\{[^}]*\}\}|%s|your[-_ ][\w -]*|[\w-]*[-_]here)$"
)
ENV_LINE_PATTERN = re.compile(r"^\s*(?:export\s+)?(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?P<separator>\s*=\s*)(?P<value>.*)$")
SECRET_NAME_PATTERN = re.compile(
    r"(?i)(?:SECRET|PASSWORD|PASSWD|PRIVATE|TOKEN|CREDENTIAL|API_?KEY|ACCESS_?KEY|SIGNING_?KEY|"
    r"ENCRYPTION_?KEY|SERVICE_ROLE|DATABASE_URL|DB_URL|CONNECTION_STRING|(?:^|_)KEY$|(?:^|_)PAT$)"
)
PUBLIC_PREFIXES = ("NEXT_PUBLIC_", "VITE_", "PUBLIC_", "EXPO_PUBLIC_", "REACT_APP_", "GATSBY_", "NUXT_PUBLIC_")
# A public-prefixed name is still a secret when it says so outright.
PUBLIC_BUT_SECRET_PATTERN = re.compile(r"SECRET|PRIVATE|PASSWORD|PASSWD|SERVICE_ROLE|CREDENTIAL")
PUBLIC_NAME_PATTERN = re.compile(r"\b(?:NEXT_PUBLIC|VITE|EXPO_PUBLIC|REACT_APP|GATSBY|NUXT_PUBLIC|PUBLIC)_[A-Z0-9_]+\b")
PUBLIC_SECRET_WORDS = re.compile(r"SECRET|PRIVATE|PASSWORD|PASSWD|TOKEN|CREDENTIAL|SERVICE_ROLE|SERVICE_KEY|ADMIN_KEY|SIGNING")
ENV_TEMPLATE_PATTERN = re.compile(r"(?i)\.(?:example|sample|template|dist)(?:\.|$)")
# `.env.any-mode.local` stands for every other mode: only a general `.env*.local`
# or `.env*` rule covers it, so it shows whether a pattern protects variants not listed here.
ENV_PROBES = (".env", ".env.local", ".env.development.local", ".env.test.local", ".env.production.local", ".env.any-mode.local")

DYNAMIC_CODE_PATTERN = re.compile(
    r"(?<![\w$.])(?:(?:window|globalThis|self)\.)?eval\s*\("
    r"|(?<![\w$.])new\s+Function\s*\("
    r"|(?<![\w$.])Function\s*\(\s*['\"`]"
    r"|(?<![\w$.])(?:(?:window|globalThis|self)\.)?set(?:Timeout|Interval)\s*\(\s*['\"`]"
)
# Words after which an expression, and so a call, may begin.
EXPRESSION_KEYWORDS = {
    "return", "await", "typeof", "void", "yield", "case", "else", "in", "of", "throw", "new", "delete", "do",
    "default", "instanceof", "export",
}
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
PAGE_FILE_PATTERN = re.compile(r"(^|/)app/(?:.*/)?(?:page|layout|template|default)\.[cm]?[jt]sx?$")
PAGE_INPUT_PATTERN = re.compile(r"\bsearchParams\b|\bparams\b|\bgenerateMetadata\b")
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
BOUND_ARGUMENT_PATTERN = re.compile(r"\.bind\s*\(\s*null\s*,")
CORS_PATTERN = re.compile(r"Access-Control-Allow-(?:Origin|Credentials)|(?<![\w$.])cors\s*\(", re.I)
UPLOAD_PATTERN = re.compile(
    r"\b(?:multer|formidable|busboy|createPresignedPost|getSignedUrl|PutObjectCommand|createUploadthing|handleUpload)\b"
    r"|\binstanceof\s+File\b|@vercel/blob|\.arrayBuffer\s*\(\s*\)"
)
WEBHOOK_PATTERN = re.compile(
    r"\bconstructEvent(?:Async)?\b|\bverifyWebhook\b|\bWebhook\s*\(|stripe-signature|x-hub-signature|svix-signature|"
    r"x-slack-signature|webhook-signature",
    re.I,
)
CACHE_PATTERN = re.compile(
    r"\bunstable_cache\b|['\"]use cache(?:: \w+)?['\"]|\bforce-static\b|\bforce-cache\b|\bexport\s+const\s+revalidate\b|"
    r"Cache-Control['\"]?\s*[:,]\s*['\"][^'\"]*\b(?:public|s-maxage)"
)
AI_HOST_PATTERN = re.compile(
    r"\b(?:api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|aiplatform\.googleapis\.com|"
    r"[a-z0-9-]+\.openai\.azure\.com|bedrock-runtime\.[a-z0-9-]+\.amazonaws\.com|api\.mistral\.ai|api\.groq\.com|"
    r"api\.cohere\.(?:ai|com)|openrouter\.ai/api|api\.together\.xyz|api\.deepseek\.com|api\.fireworks\.ai|"
    r"api\.perplexity\.ai|api\.x\.ai|localhost:11434)\b|/chat/completions\b"
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
RATE_LIMIT_LIBRARIES = ("@upstash/ratelimit", "rate-limiter-flexible", "express-rate-limit", "@arcjet/next", "@nestjs/throttler", "hono-rate-limiter")
AI_PACKAGES = (
    "ai", "openai", "@anthropic-ai/sdk", "@google/generative-ai", "@google/genai", "@google-cloud/vertexai",
    "@aws-sdk/client-bedrock-runtime", "@aws-sdk/client-bedrock-agent-runtime", "@azure/openai", "@azure-rest/ai-inference",
    "langchain", "llamaindex", "@mistralai/mistralai", "cohere-ai", "groq-sdk", "ollama", "replicate",
    "@huggingface/inference", "@huggingface/transformers", "@xenova/transformers", "node-llama-cpp", "together-ai",
    "portkey-ai", "genkit", "@openai/agents", "@modelcontextprotocol/sdk", "@vercel/ai", "workers-ai-provider",
    "@cloudflare/ai", "openai-edge",
)
AI_PACKAGE_PREFIXES = ("@ai-sdk/", "@langchain/", "@llamaindex/", "@anthropic-ai/", "@mastra/", "@genkit-ai/", "@openrouter/")
FRAMEWORK_PACKAGES = (
    "next", "react", "react-dom", "@remix-run/node", "@remix-run/react", "react-router", "@react-router/dev",
    "vite", "express", "fastify", "hono", "@nestjs/core", "@trpc/server", "astro", "@sveltejs/kit", "nuxt",
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


def tracked_files(root: Path) -> list[str] | None:
    """Every tracked file, with no folder exclusions or count limit, or None outside Git."""
    output = git(root, "-c", "core.quotePath=false", "ls-files", "-z", "--cached")
    if output is None:
        return None
    return sorted({entry for entry in output.split("\0") if entry})


def listed_files(root: Path) -> list[str]:
    """Tracked and untracked files Git does not ignore, outside generated folders, for code facts."""
    output = git(root, "-c", "core.quotePath=false", "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if output is None:
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


def stream_lines(path: Path) -> Iterator[str] | None:
    """The file's lines, read one at a time so size never limits the scan, or None for binary files."""
    handle = path.open("rb")
    head = handle.read(BINARY_SNIFF_BYTES)
    if b"\0" in head:
        handle.close()
        return None
    handle.seek(0)

    def lines() -> Iterator[str]:
        with handle:
            for raw in handle:
                line = raw.decode("utf-8", errors="replace")
                if line.endswith("\n"):
                    line = line[:-1]
                yield line[:-1] if line.endswith("\r") else line

    return lines()


def split_lines(text: str) -> list[str]:
    """Split on newlines only, as the protocol and grep do, ignoring a trailing carriage return."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines]


def mask(text: str, strings: bool) -> str:
    """Blank out comments, and with `strings` the contents of strings and regular expressions.

    Offsets and line numbers are unchanged, and string delimiters are kept, so a
    pattern can still see that a call's argument is a string literal.
    """
    output = list(text)
    index = 0
    length = len(text)
    previous = ""  # the last significant character of code

    def blank(start: int, end: int) -> None:
        for position in range(start, min(end, length)):
            if output[position] != "\n":
                output[position] = " "

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
        if character == "`":
            index = template_end(text, index, strings, output, blank)
            previous = "`"
            continue
        if character in "'\"":
            end = index + 1
            while end < length:
                if text[end] == "\\":
                    end += 2
                    continue
                if text[end] == character or (text[end] == "\n" and character != "`"):
                    break
                end += 1
            if strings:
                blank(index + 1, end)
            index = end + 1
            previous = character
            continue
        if strings and character == "/" and starts_regular_expression(text, index, previous):
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
            blank(index + 1, end)
            index = end + 1
            previous = "/"
            continue
        if not character.isspace():
            previous = character
        index += 1
    return "".join(output)


def template_end(text: str, start: int, strings: bool, output: list[str], blank: Any) -> int:
    """Mask a template literal from its opening backtick and return the index after it.

    Its text is blanked with `strings`; each `${...}` interpolation is code, so
    it is masked like any other code, recursively.
    """
    length = len(text)
    index = start + 1
    segment = index
    while index < length:
        character = text[index]
        if character == "\\":
            index += 2
            continue
        if character == "`":
            if strings:
                blank(segment, index)
            return index + 1
        if text.startswith("${", index):
            if strings:
                blank(segment, index)
            close = closing_brace(text, index + 1)
            output[index + 2:close] = list(mask(text[index + 2:close], strings))
            index = close + 1
            segment = index
            continue
        index += 1
    if strings:
        blank(segment, length)
    return length


def closing_brace(text: str, open_index: int) -> int:
    """The index of the `}` that closes the `{` at `open_index`, skipping strings."""
    depth = 0
    index = open_index
    while index < len(text):
        character = text[index]
        if character in "'\"`":
            end = index + 1
            while end < len(text) and text[end] != character:
                end += 2 if text[end] == "\\" else 1
            index = end + 1
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return len(text)


def starts_regular_expression(text: str, index: int, previous: str) -> bool:
    """Whether a `/` begins a regular expression literal rather than a division."""
    if previous == "" or previous in "(,=:[!&|?{};+-*%<>~^":
        return True
    word = re.search(r"([A-Za-z_$][\w$]*)\s*$", text[:index])
    return bool(word and word.group(1) in EXPRESSION_KEYWORDS)


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
    """A JavaScript or TypeScript file, read once, with comments masked, and a code-only view."""

    def __init__(self, root: Path, path: str) -> None:
        self.path = path
        self.text = read_text(root / path) or ""
        self.masked = mask(self.text, strings=False)
        self._code: str | None = None
        self.is_client = bool(USE_CLIENT_PATTERN.match(self.masked))

    @property
    def code(self) -> str:
        """Comments, strings, and regular expressions blanked: only code is left."""
        if self._code is None:
            self._code = mask(self.text, strings=True)
        return self._code

    def site(self, offset: int) -> str:
        return f"{self.path}:{line_of(self.masked, offset)}"


# --------------------------------------------------------------------------- secrets


def any_secret(text: str) -> bool:
    """Whether text holds anything the protocol or this collector would read as a credential."""
    return any(pattern.search(text) for pattern in PROTOCOL_GUARD_PATTERNS) or any(
        not is_placeholder(match) for _, pattern in SECRET_RULES for match in pattern.finditer(text)
    )


def is_placeholder_value(value: str) -> bool:
    """Whole-value placeholders, documentation examples, and filler such as `xxxx` or `0000`."""
    return (
        bool(PLACEHOLDER_VALUE_PATTERN.match(value))
        or value in DOCUMENTATION_EXAMPLES
        or len(set(value)) < 4
    )


def is_placeholder(match: re.Match[str]) -> bool:
    if match.re is RULES["private-key"]:
        return False
    if match.re is RULES["connection-string-password"]:
        host = match.group("host").lower()
        password = match.group("password")
        return (
            host in LOCAL_HOSTS
            or host.endswith((".local", ".test", ".example", ".invalid", ".localhost", "example.com", "example.org"))
            or is_placeholder_value(password)
            or any(character in password for character in "${<*")
        )
    return is_placeholder_value(match.group("secret")) or is_placeholder_value(match.group(0))


def character_class(character: str) -> str:
    if character.isdigit():
        return "digit"
    if character.isupper():
        return "upper"
    return "lower" if character.islower() else "other"


def credential_like(text: str) -> bool:
    """Whether text holds a run shaped like a generated credential, such as a random password."""
    for run in TOKEN_RUN.findall(text):
        classes = [character_class(character) for character in run]
        if classes.count("digit") < 2 or classes.count("upper") < 2 or classes.count("lower") < 2:
            continue
        transitions = sum(1 for left, right in zip(classes, classes[1:]) if left != right)
        if transitions >= 0.4 * (len(run) - 1):
            return True
    return False


def sensitive(text: str) -> bool:
    """The one test every printed string passes: no credential format and no credential-shaped run."""
    return any_secret(text) or credential_like(text)


def path_is_safe(path: str) -> bool:
    """Whether a path can be printed: no component of it is or contains something credential-shaped."""
    return not any(sensitive(component) for component in path.split("/"))


def quoted(prefix: str, safe: re.Pattern[str]) -> str | None:
    """`prefix<REDACTED>` when the prefix is an approved, non-sensitive fragment."""
    if not prefix or not safe.match(prefix) or any_secret(prefix):
        return None
    return f"{prefix}{REDACTED}"


def citation(path: str, number: int, label: str, quote: str | None) -> str:
    """A citation of one line. The quote, when there is one, holds only an approved fragment and <REDACTED>."""
    if quote is None:
        return f"{location(path)}:{number} — {label} (value redacted)"
    return f"{location(path)}:{number} — {label}: `{quote}`"


def with_lookahead(lines: Iterable[str], size: int = 2) -> Iterator[tuple[int, str, list[str]]]:
    """Yield (line number, line, the next `size` lines)."""
    window: deque[str] = deque()
    iterator = iter(lines)
    number = 0
    for line in iterator:
        window.append(line)
        if len(window) > size:
            number += 1
            current = window.popleft()
            yield number, current, list(window)
    while window:
        number += 1
        current = window.popleft()
        yield number, current, list(window)


def line_secrets(line: str, following: list[str]) -> list[tuple[str, re.Match[str]]]:
    """Every high-confidence credential on one line, without overlaps."""
    found: list[tuple[str, re.Match[str]]] = []
    taken: list[tuple[int, int]] = []
    for rule, pattern in SECRET_RULES:
        for match in pattern.finditer(line):
            if is_placeholder(match):
                continue
            if rule == "private-key" and not (
                any(PEM_BODY_PATTERN.match(next_line.strip()) for next_line in following)
                or PEM_INLINE_BODY_PATTERN.search(line[match.start():])
            ):
                continue
            start, end = match.span("secret")
            if any(start < other_end and other_start < end for other_start, other_end in taken):
                continue
            taken.append((start, end))
            found.append((rule, match))
    return sorted(found, key=lambda item: item[1].start("secret"))


def is_env_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (name == ".env" or name.startswith(".env.") or name.endswith(".env")) and not ENV_TEMPLATE_PATTERN.search(name)


def is_secret_name(name: str) -> bool:
    upper = name.upper()
    if upper.startswith(PUBLIC_PREFIXES):
        return bool(PUBLIC_BUT_SECRET_PATTERN.search(upper))
    return bool(SECRET_NAME_PATTERN.search(upper))


def parse_env_value(raw: str) -> str:
    """The value of a dotenv assignment: quoted values end at their closing quote, others at ` #`."""
    text = raw.strip()
    if text[:1] in ("'", '"'):
        quote = text[0]
        index = 1
        while index < len(text):
            if quote == '"' and text[index] == "\\":
                index += 2
                continue
            if text[index] == quote:
                return text[1:index]
            index += 1
        return text[1:]
    return re.split(r"\s#", text, maxsplit=1)[0].strip()


def is_local_connection(value: str) -> bool:
    """Whether a value is a URL whose host is a local or container-only machine."""
    if "://" not in value:
        return False
    try:
        host = (urlsplit(value).hostname or "").lower()
    except ValueError:
        return False
    return host in LOCAL_HOSTS or host.endswith(".localhost")


def env_secret_value(raw: str) -> str | None:
    """The value of an environment assignment, unless it is empty, a placeholder, or a local connection."""
    value = parse_env_value(raw)
    if is_placeholder_value(value) or value.startswith("$") or is_local_connection(value):
        return None
    return value


def scan_secrets(root: Path, tracked: list[str]) -> dict[str, Any]:
    """Scan every tracked file for credentials. Values never leave this function."""
    evidence: list[str] = []
    rules: Counter[str] = Counter()
    files_with_findings: set[str] = set()
    hidden_paths = 0
    unreadable: list[str] = []
    env_files: list[str] = []
    scanned = 0
    for path in tracked:
        full = root / path
        if full.is_symlink() or not full.is_file():
            continue
        name = path.rsplit("/", 1)[-1]
        printable = path_is_safe(path)
        if name.endswith(KEYSTORE_SUFFIXES) or name in PRIVATE_KEY_FILE_NAMES:
            rules["keystore-file"] += 1
            files_with_findings.add(path)
            if printable:
                evidence.append(f"{location(path)} — committed keystore or private key file")
            else:
                hidden_paths += 1
            continue
        try:
            lines = stream_lines(full)
        except OSError:
            unreadable.append(path)
            continue
        if lines is None:
            continue
        scanned += 1
        environment = is_env_file(path)
        if environment:
            env_files.append(path)
        for number, line, following in with_lookahead(lines):
            hits = line_secrets(line, following)
            for rule, _ in hits:
                rules[rule] += 1
            if hits:
                files_with_findings.add(path)
                first = hits[0][1]
                label = ", ".join(dict.fromkeys(rule for rule, _ in hits))
                if printable:
                    evidence.append(citation(path, number, label, quoted(first.group("keep") or "", SAFE_PREFIX_PATTERN)))
                else:
                    hidden_paths += 1
            if environment:
                match = ENV_LINE_PATTERN.match(line)
                if match and is_secret_name(match.group("name")) and env_secret_value(match.group("value")) is not None:
                    rules["secret-named-environment-variable"] += 1
                    files_with_findings.add(path)
                    name_variable = match.group("name")
                    label = f"secret-named variable {name_variable}" if SAFE_NAME_PATTERN.match(name_variable) else "secret-named variable"
                    separator = " ".join(match.group("separator").split()) or "="
                    prefix = name_variable + separator if SAFE_NAME_PATTERN.match(name_variable) else ""
                    if printable:
                        evidence.append(citation(path, number, label, quoted(prefix, re.compile(r"^[A-Za-z0-9_]{1,80} ?= ?$"))))
                    else:
                        hidden_paths += 1
    return {
        "evidence": evidence,
        "rules": rules,
        "files": files_with_findings,
        "hiddenPaths": hidden_paths,
        "unreadable": unreadable,
        "envFiles": env_files,
        "scanned": scanned,
    }


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
    """Counts, rule names, commits, and printable files only. Secret and Match fields are never read."""
    items = [item for item in findings if isinstance(item, dict)]
    rules = Counter(str(item.get("RuleID", "unknown")) for item in items)
    commits = sorted({str(item.get("Commit", ""))[:12] for item in items if item.get("Commit")})
    paths = sorted({str(item.get("File", "")) for item in items if item.get("File")})
    return {
        "findings": sum(rules.values()),
        "rules": {name: count for name, count in sorted(rules.items()) if SAFE_NAME_PATTERN.match(name.replace("-", "_"))},
        "commits": [commit for commit in commits if re.fullmatch(r"[0-9a-f]{1,12}", commit)][:20],
        "files": [path for path in paths if path_is_safe(path)][:20],
    }


def secrets_check(root: Path, tracked: list[str] | None, with_scan: bool, snapshot: dict[str, Any]) -> dict[str, Any]:
    if tracked is None:
        return {"evaluationState": "not-evaluated", "reason": "The target is not a Git repository, so there are no tracked files to scan."}
    scan = scan_secrets(root, tracked)
    evidence: list[str] = scan["evidence"]
    rules: Counter[str] = scan["rules"]
    local_env = [path for path in scan["envFiles"] if path.endswith(".local") and path_is_safe(path)]
    snapshot["environmentFiles"] = {
        "committed": [path for path in scan["envFiles"] if path_is_safe(path)],
        **({"committedLocal": local_env} if local_env else {}),
    }
    facts: dict[str, Any] = {
        "trackedFilesScanned": scan["scanned"],
        "findings": dict(sorted(rules.items())),
        "historyScanned": False,
    }
    if scan["unreadable"]:
        facts["unreadableFiles"] = len(scan["unreadable"])
    degraded = []
    if scan["unreadable"]:
        degraded.append(f"{len(scan['unreadable'])} tracked files could not be read, so a secret in them would be missed")
    history: dict[str, Any] | None = None
    history_entry = None
    if with_scan:
        findings, command = run_gitleaks(root)
        if findings is None:
            degraded.append(f"--with-scan was requested but Git history was not scanned: {command}")
            facts["historyScan"] = command
        else:
            history = summarise_history(findings)
            facts["historyScanned"] = True
            facts["history"] = history
            rule_list = ", ".join(f"{name} ×{count}" for name, count in history["rules"].items()) or "none"
            history_entry = (
                f"command: `{command}` → {history['findings']} finding(s) in {len(history['commits'])} commit(s) "
                f"({rule_list}); values redacted"
            )
    snapshot["secretScan"] = facts
    found = sum(rules.values())
    withheld_entry = None
    if scan["hiddenPaths"]:
        withheld_entry = (
            f"command: `collect.py secret scan` → {scan['hiddenPaths']} finding(s) in files whose paths look like "
            "credentials; the paths are withheld"
        )
    if found or (history and history["findings"]):
        listed = evidence[:MAX_EVIDENCE] + [entry for entry in (withheld_entry, history_entry) if entry]
        only_tests = bool(scan["files"]) and all(is_test_path(path) for path in scan["files"])
        result: dict[str, Any] = {
            "status": "violation",
            "evidence": listed,
            "evidenceTier": "direct" if found else "supported",
            "gap": (
                f"{found} credential(s) in {len(scan['files'])} tracked file(s)"
                + (f", and {history['findings']} finding(s) in Git history" if history and history["findings"] else "")
                + ". Anyone who can read the repository, or any clone of it, can use them."
            ),
            "remediation": (
                "Rotate each credential first, because removing it from the tree does not remove it from history. "
                "Then load it from the environment or a secret manager, keep it out of client code, and add a secret "
                "scan to the pre-commit hook and continuous integration."
            ),
            "judgement": "consider" if only_tests else "act-on",
        }
        if not found and history:
            generic_only = set(history["rules"]) <= {"generic-api-key"}
            result["judgement"] = "consider" if generic_only else "act-on"
            result["gap"] = (
                f"No secrets in the current tree, but gitleaks found {history['findings']} in Git history. "
                "A secret removed from the tree is still in every clone."
            )
        if degraded:
            result["degradedReason"] = "; ".join(degraded)
        return result
    entry = (
        f"command: `collect.py secret scan` → 0 high-confidence credentials, keystore files, or secret-named values "
        f"in {scan['scanned']} tracked text files"
    )
    result = {"status": "present", "evidence": [entry] + ([history_entry] if history_entry else []), "evidenceTier": "direct"}
    if degraded:
        result["degradedReason"] = "; ".join(degraded)
    return result


# --------------------------------------------------------------------------- environment files


def app_roots(files: list[str]) -> list[str]:
    """The repository root, every folder with a package manifest, and every folder with an environment file."""
    roots = {""}
    for path in files:
        parts = path.split("/")
        if any(part in SKIPPED_DIRECTORIES for part in parts[:-1]):
            continue
        name = parts[-1]
        folder = "/".join(parts[:-1])
        if name == "package.json" or name.startswith(".env") or re.match(r"^(?:next|vite)\.config\.[cm]?[jt]s$", name):
            roots.add(folder)
    return sorted(roots)


def env_ignore_check(root: Path, files: list[str], tracked: set[str]) -> dict[str, Any]:
    committed_local = sorted(
        path for path in tracked
        if path.rsplit("/", 1)[-1].startswith(".env") and path.endswith(".local") and path_is_safe(path)
    )
    probes = []
    for folder in app_roots(files + sorted(tracked)):
        prefix = f"{folder}/" if folder else ""
        for name in ENV_PROBES:
            candidate = f"{prefix}{name}"
            if candidate in tracked:
                continue  # A committed `.env` holds defaults on purpose; a committed `.local` file is reported below.
            probes.append(candidate)
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if name.startswith(".env") and not ENV_TEMPLATE_PATTERN.search(name) and path not in tracked and path not in probes:
            probes.append(path)  # An environment file on disk that Git would add.
    output = (git(root, "check-ignore", "--no-index", "-v", "-n", *probes) or "") if probes else ""
    covered: list[str] = []
    local_only: list[str] = []
    uncovered: list[str] = []
    for raw in output.splitlines():
        rule, _, path = raw.partition("\t")
        source, _, rest = rule.partition(":")
        line, _, pattern = rest.partition(":")
        if not source or pattern.startswith("!"):
            uncovered.append(path)
        elif source not in tracked or not line.isdigit():
            local_only.append(path)
        else:
            covered.append(f"{location(source)}:{line} — `{safe_fragment(pattern)}` ignores {path}")
    if probes and not output:
        uncovered = probes
    covered = sorted(set(covered))
    if committed_local:
        return {
            "status": "violation",
            "evidence": [f"{location(path)} — committed local environment file" for path in committed_local[:MAX_EVIDENCE]],
            "evidenceTier": "direct",
            "gap": (
                f"{len(committed_local)} local environment file(s) are committed. An ignore rule does not untrack a "
                "file, so every clone receives whatever it holds now or later."
            ),
            "remediation": (
                "Run `git rm --cached` on each file, rotate anything it ever held, and keep `.env*` in `.gitignore` "
                "with `!.env.example` for the committed template."
            ),
            "judgement": "act-on",
        }
    if not probes:
        return {
            "status": "present",
            "evidence": ["command: `git ls-files` → every environment file name is tracked on purpose, so there is no local file to ignore"],
            "evidenceTier": "direct",
        }
    if not uncovered and not local_only:
        return {"status": "present", "evidence": covered[:4], "evidenceTier": "direct"}
    missing_entry = f"command: `git check-ignore --no-index` → not ignored by a tracked rule: {', '.join((uncovered + local_only)[:12])}"
    if not covered:
        result = {
            "status": "missing",
            "evidence": [missing_entry],
            "evidenceTier": "direct",
            "gap": "No ignore rule in the repository covers local environment files, so the first `git add .` after creating one commits its secrets.",
            "remediation": "Add `.env*` to a committed `.gitignore` with `!.env.example` for the template, then confirm with `git check-ignore -v .env.local`.",
            "judgement": "act-on",
        }
        if local_only:
            result["gap"] += " Only an untracked, personal, or global ignore file covers some of them, which protects no one else."
        return result
    gap = "Some local environment files are not ignored by a rule the repository tracks"
    if local_only:
        gap += "; only an untracked, personal, or global ignore file covers " + ", ".join(local_only[:6]) + ", which protects no one else who clones it"
    return {
        "status": "partial",
        "evidence": [missing_entry] + covered[:3],
        "evidenceTier": "direct",
        "gap": gap + ".",
        "remediation": "Add `.env*` to the committed `.gitignore` of each application folder, with `!.env.example` for the template.",
        "judgement": "act-on",
    }


# --------------------------------------------------------------------------- dynamic code


def previous_token(code: str, start: int) -> str:
    """The significant token before an offset: a word, `=>`, or one character."""
    index = start - 1
    while index >= 0 and code[index].isspace():
        index -= 1
    if index < 0:
        return ""
    if code[index] == ">" and index > 0 and code[index - 1] == "=":
        return "=>"
    if re.match(r"[\w$]", code[index]):
        word = re.search(r"[A-Za-z_$][\w$]*$", code[: index + 1])
        return word.group(0) if word else code[index]
    return code[index]


def is_declaration(code: str, match_end: int, before: str) -> bool:
    """Whether a matched `eval(` declares a method or function rather than calling one.

    A declaration starts a member or statement, and its parameter list is
    followed on the same line by a body or a return type, as in
    `eval(x: string): number` or `eval(code) {`.
    """
    if before not in ("", "{", ";", "}", ","):
        return False
    open_index = code.rfind("(", 0, match_end)
    depth = 0
    for index in range(open_index, min(len(code), open_index + 2000)):
        if code[index] == "(":
            depth += 1
        elif code[index] == ")":
            depth -= 1
            if depth == 0:
                following = re.match(r"[ \t]*(.)", code[index + 1:index + 40])
                return bool(following and following.group(1) in (":", "{"))
    return False


JSX_TAG_PATTERN = re.compile(r"<(?:/?[A-Za-z][\w.:-]*(?:\s[^<>]*)?/?)?$")


def ends_jsx_tag(code: str, start: int) -> bool:
    """Whether the `>` before an offset closes a JSX tag such as `<p>` or `</b>`, not a comparison."""
    closing = code.rfind(">", 0, start)
    opening = code.rfind("<", 0, closing)
    return opening != -1 and bool(JSX_TAG_PATTERN.match(code[opening:closing]))


def is_code_call(source: SourceFile, match: re.Match[str]) -> bool:
    """Whether a match is a call in code, not prose in JSX text or a declaration."""
    code = source.code
    before = previous_token(code, match.start())
    if before == ">" and ends_jsx_tag(code, match.start()):
        return False
    if re.match(r"[A-Za-z_$]", before) and before not in EXPRESSION_KEYWORDS:
        return False
    if match.group(0).rstrip().endswith("(") and is_declaration(code, match.end(), before):
        return False
    return True


def dynamic_code_check(sources: list[SourceFile], snapshot: dict[str, Any]) -> dict[str, Any]:
    production: list[str] = []
    tests = 0
    for source in sources:
        if source.path.endswith(".min.js"):
            continue
        for match in DYNAMIC_CODE_PATTERN.finditer(source.code):
            if not is_code_call(source, match):
                continue
            if is_test_path(source.path):
                tests += 1
                continue
            number = line_of(source.code, match.start())
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


def page_entry_points(sources: list[SourceFile]) -> list[str]:
    """Server-rendered pages and layouts that take route parameters, search parameters, or build metadata."""
    entries = []
    for source in sources:
        if source.is_client or not PAGE_FILE_PATTERN.search(source.path):
            continue
        reasons = []
        if "[" in source.path:
            reasons.append("dynamic segment")
        if re.search(r"\bsearchParams\b", source.code):
            reasons.append("searchParams")
        if re.search(r"\bgenerateMetadata\b", source.code):
            reasons.append("generateMetadata")
        if reasons:
            entries.append(f"{source.path} ({', '.join(reasons)})")
    return entries


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


def build_snapshot(root: Path, files: list[str], sources: list[SourceFile], snapshot: dict[str, Any]) -> None:
    """Record Layer 0 facts."""
    declared = dependencies(root, files)
    snapshot["frameworks"] = {name: declared[name] for name in FRAMEWORK_PACKAGES if name in declared}
    router = detect_router(files)
    if router and "next" in declared:
        snapshot["nextRouter"] = router
    production = [source for source in sources if not is_test_path(source.path)]
    snapshot["serverActions"] = summarise(server_actions(sources))
    snapshot["routeHandlers"] = summarise(route_handlers(sources))
    snapshot["pageEntryPoints"] = summarise(page_entry_points(production))
    snapshot["otherServerRoutes"] = summarise(sites(production, NODE_ROUTE_PATTERN))
    snapshot["middleware"] = [
        path for path in files
        if re.match(r"^(?:src/)?(?:middleware|proxy)\.[cm]?[jt]s$", path)
        or re.match(r"^(?:apps|packages)/[^/]+/(?:src/)?(?:middleware|proxy)\.[cm]?[jt]s$", path)
    ]
    snapshot["authenticationLibraries"] = [name for name in AUTH_LIBRARIES if name in declared]
    snapshot["validationLibraries"] = [name for name in VALIDATION_LIBRARIES if name in declared]
    snapshot["sanitizers"] = [name for name in SANITIZERS if name in declared]
    snapshot["rateLimitLibraries"] = [name for name in RATE_LIMIT_LIBRARIES if name in declared]
    ai_packages = sorted(name for name in declared if name in AI_PACKAGES or name.startswith(AI_PACKAGE_PREFIXES))
    ai_hosts = sites(sources, AI_HOST_PATTERN)
    snapshot["aiSdks"] = ai_packages
    snapshot["modelApiReferences"] = summarise(ai_hosts)
    if not ai_packages and not ai_hosts:
        snapshot["aiDetection"] = (
            "No AI SDK, model API host, or chat-completions path found. Confirm there is no model call before "
            "recording the Layer 4 checks as not applicable."
        )
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
            key = match.group("key")
            shown = key if re.fullmatch(r"[A-Za-z0-9_.:-]{1,40}", key) and not sensitive(key) else "(key not shown)"
            storage.append(f"{source.site(match.start())} {shown}")
    snapshot["browserStorageAuthKeys"] = summarise(storage)
    snapshot["messageListeners"] = summarise(sites(production, MESSAGE_LISTENER_PATTERN))
    snapshot["boundActionArguments"] = summarise(sites(production, BOUND_ARGUMENT_PATTERN))
    snapshot["corsSites"] = summarise(sites(production, CORS_PATTERN))
    snapshot["uploadSites"] = summarise(sites(production, UPLOAD_PATTERN))
    snapshot["webhookSites"] = summarise(sites(production, WEBHOOK_PATTERN))
    snapshot["cachingSites"] = summarise(sites(production, CACHE_PATTERN))
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


# --------------------------------------------------------------------------- output safety


def scrub(value: Any) -> tuple[Any, int]:
    """Replace any credential-looking string in a value, returning the number replaced."""
    if isinstance(value, str):
        return (WITHHELD, 1) if sensitive(value) else (value, 0)
    if isinstance(value, list):
        total = 0
        items = []
        for item in value:
            cleaned, count = scrub(item)
            items.append(cleaned)
            total += count
        return items, total
    if isinstance(value, dict):
        total = 0
        result = {}
        for key, item in value.items():
            cleaned_key, key_count = scrub(key)
            cleaned, count = scrub(item)
            result[cleaned_key if not key_count else f"{WITHHELD} {total}"] = cleaned
            total += count + key_count
        return result, total
    return value, 0


def make_safe(output: dict[str, Any]) -> dict[str, Any]:
    """Check the complete output, so no credential-looking text is ever printed.

    Every evidence entry and snapshot string, whichever check or fact produced
    it, passes the same test, so a credential-shaped file or folder name is
    withheld everywhere. Evidence entries that fail are dropped, because a
    replaced entry would no longer verify; other text is replaced with a marker.
    """
    for result in output["checks"].values():
        evidence = result.get("evidence")
        if not isinstance(evidence, list):
            continue
        kept = [entry for entry in evidence if not sensitive(entry)]
        if len(kept) < len(evidence):
            kept.append(
                f"command: `collect.py output check` → {len(evidence) - len(kept)} evidence entries withheld because they looked like credentials"
            )
        result["evidence"] = kept
    output, _ = scrub(output)
    return output


# --------------------------------------------------------------------------- collect


def collect(root: Path, enrichment: list[str] | None = None) -> dict[str, Any]:
    flags = {flag.lstrip("-") for flag in enrichment or []}
    with_scan = "with-scan" in flags
    files = listed_files(root)
    tracked = tracked_files(root)
    sources = [SourceFile(root, path) for path in files if path.endswith(SOURCE_SUFFIXES) and not path.endswith(".d.ts")]
    checks: dict[str, Any] = {}
    snapshot: dict[str, Any] = {"sourceFiles": len(sources), "trackedFiles": len(tracked or [])}

    checks[f"{AUDIT}.no-secrets-in-source"] = secrets_check(root, tracked, with_scan, snapshot)
    checks[f"{AUDIT}.env-files-gitignored"] = env_ignore_check(root, files, set(tracked or []))
    checks[f"{AUDIT}.no-dynamic-code-execution"] = dynamic_code_check(sources, snapshot)
    build_snapshot(root, files, sources, snapshot)
    return make_safe({"checks": checks, "snapshot": snapshot})


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
