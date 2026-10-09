---
name: security-audit
description: Audit a TypeScript, React, or Next.js codebase for exploitable weaknesses — server access control, injection and unsafe output, secrets and browser hardening, and large-language-model features — tracing each finding from an attacker's entry point.
disable-model-invocation: true
argument-hint: "[--worktree] [--since=<ref>] [--with-scan] [--learn|--teach]"
---

# /security-audit

Find the weaknesses an attacker can actually use in a TypeScript application, and what to fix first.

Server Actions, route handlers, and API routes are public HTTP endpoints, so this audit reads the server code in the repository as closely as the browser code. It has three sources:
- a deterministic collector, which finds committed secrets, environment-file ignore gaps, and `eval`, and inventories every entry point and dangerous sink;
- an optional history and pattern scan (`--with-scan`);
- the judgement of the model, spent tracing input from entry points to sinks.

It is static and read-only. It never runs the application or attempts an exploit.

**A supported finding** follows gstack's `/cso` bar. A `violation` of a model check needs four things:
- a concrete attacker-controlled entry point;
- a path across a security boundary (anonymous to user, user to another user, browser to server, user to model tool);
- a demonstrated impact;
- a challenge of the protective controls that the finding survives.

Missing hardening, such as an absent header, becomes a violation only with a concrete failure scenario. Otherwise it is `partial` and judged as hardening. Tool checks grade invariants the collector can decide, such as a committed secret or a call to `eval`, and you then trace their reach. There is no blanket exclusion list: every candidate is judged on its trace.

[Detection notes](references/detection.md) explain how to trace and evaluate each check.

## Usage

```
/security-audit                  # audit the current repository
/security-audit --worktree       # run in .worktrees/security-audit on its own branch
/security-audit --since=<ref>    # judge only what changed since <ref> (a provisional, filtered run)
/security-audit --with-scan      # also scan Git history with gitleaks and run installed Semgrep and ESLint security rules
/security-audit --learn          # teaching mode; --teach is an alias
```

`--since` makes the run provisional, because it changes what the audit measures. The three tool checks always cover the whole repository, because a committed secret is exposed wherever it sits.

## Ownership

| Concern | Owner |
| --- | --- |
| Exploitable weaknesses in the application's own code and configuration | `/security-audit` |
| Known-vulnerable dependencies, including the framework version | `/dependency-audit` |
| Whether secret scans, dependency scans, and other gates run in hooks and continuous integration | `/quality-gates-audit` |
| Error-reporter redaction configuration | `/error-handling-audit` |
| Secrets in Claude Code settings files | `/agentic-audit` |

When a vulnerable framework version makes a finding worse, as CVE-2025-29927 does for middleware-only authorization, cite it as context and leave the version itself to `/dependency-audit`.

## The baseline

Statuses follow the shared taxonomy: `present`, `partial`, `missing`, or `violation`. Checks that cannot apply are recorded as not applicable, and checks you could not evaluate as not evaluated. Severity follows Architecture Decision Record 0004, read for security in the detection notes: `critical` only for directly exploitable problems. The method column says whether the collector decides the check (`tool`) or you do (`model`).

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

The collector records:
- frameworks and versions, and the Next.js router;
- every Server Action, route handler, API route, loader, and action, and every page or layout that reads route or search parameters, with its path;
- middleware or proxy files;
- authentication, validation, sanitizer, rate-limit, and AI libraries, and direct model API calls;
- candidate sites for `dangerouslySetInnerHTML` and other HTML sinks, server requests with dynamic URLs, dynamic redirects, auth-like web-storage keys, message listeners, bound Server Action arguments, CORS headers, uploads, webhooks, and caching;
- public-prefixed variable names that look secret (names only, never values);
- where security headers are set, and deployment configuration files;
- committed environment files and secret-scan counts (never values).

Add the trust boundaries you identify (anonymous, user, other users or tenants, administrator, model), what is deliberately public, and the deployment platform's header and HTTPS defaults.

### Layer 1 — Access control and sessions

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Server entry points authenticate the caller | critical | model | Every non-public Server Action, route handler, API route, loader, action, and data-loading page or layout verifies the session itself. | An entry point that reads or changes non-public data with no session check of its own. |
| Each request is authorized for the records it touches | critical | model | Every lookup or mutation by a request-supplied identifier is scoped to the caller's ownership, tenant, or role, and no cache serves one user's data to another. | A query by request identifier with no ownership, tenant, or role condition, or per-user data in a shared cache. |
| Authorization does not rely on middleware alone | high | model | Middleware is an optimistic first pass; the entry point or its data-access layer enforces the rule again. | Protected data or mutations whose only check is in middleware or a proxy file. |
| State changes resist cross-site requests | high | model | No GET handler changes state; cookie-authenticated mutations rely on SameSite cookies or an origin check. | A state-changing GET, or a cookie-authenticated mutation reachable cross-site. |
| Cross-origin access is limited to trusted origins | critical | model | CORS allows only an exact origin allowlist, and never reflects the caller's origin with credentials. | A reflected or wildcard origin together with credentials on non-public data. |
| Webhooks verify their signature before acting | critical | model | Webhooks verify the provider's signature over the raw body, in constant time, and refuse replays. | A webhook acting on an unverified, re-serialised, or replayed body. |
| Hand-written sign-in flows use state, PKCE, and nonce | high | model | Hand-written OAuth sends and verifies `state` and uses PKCE; OpenID Connect also verifies `nonce`. Not applicable when a library runs the flow. | A hand-written authorization request or callback missing one of them. |
| Session tokens stay out of web storage | high | model | Session and access tokens live in HttpOnly cookies. | A token written to or read from localStorage or sessionStorage. |
| Session cookies are Secure, HttpOnly, and SameSite | high | model | Session and token cookies carry Secure, HttpOnly, and SameSite=Lax or Strict, explicitly or by library default. | A session cookie set without one of the flags. |
| Tokens and password hashes use strong cryptography | high | model | Tokens come from a secure generator; passwords use bcrypt, scrypt, or Argon2; verifiers pin the algorithm. | `Math.random()` tokens, fast password hashes, or an unpinned verifier. |
| Sign-in and recovery endpoints are rate-limited | high | model | Sign-in, one-time-code, reset, and sign-up attempts are limited per account and per client in a shared store. | A hand-written sign-in, code, or reset endpoint with no limit, or a limit in process memory on serverless. |

### Layer 2 — Untrusted input and output

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Server entry points validate their input | high | model | Every entry point parses its input, including `.bind` arguments, with a schema on the server and passes on only allowed fields. | Request data reaching a write or call unparsed, or a whole body spread into a write. |
| Queries and commands are parameterised | critical | model | Queries use parameters, commands take argument arrays, and request paths are confined. | Request data concatenated into SQL, a shell command, or a file path. |
| Server requests do not follow user-supplied URLs | critical | model | Server requests go to fixed hosts or an allowlist checked on the parsed URL, without cross-host redirects. | A user-supplied URL reaching a server-side `fetch` or renderer with no allowlist. |
| dangerouslySetInnerHTML renders only sanitized HTML | critical | model | Its HTML is constant or passed through a sanitizer. | User-controlled, stored, or fetched HTML with no sanitizer on the path. |
| Other HTML sinks receive only sanitized content | critical | model | `innerHTML`, `insertAdjacentHTML`, `document.write`, and raw-HTML Markdown get constant or sanitized content. | One of these sinks fed user-controlled content without sanitization. |
| Uploaded files cannot run or escape their storage | critical | model | Uploads are checked by content and size, stored under generated names, and served from another origin or as attachments. Not applicable without uploads. | An upload served inline from the application's origin, a client file name in a path, or no size limit. |
| User-supplied URLs use safe schemes | high | model | URLs reaching `href`, `src`, `window.open`, or `location` are limited to safe schemes. | A user URL reaching such a sink unchecked where nothing blocks `javascript:`. |
| Redirects go only to allowed destinations | high | model | Request-supplied redirect targets are same-origin paths or allowlisted, checked after parsing. | A redirect or callback URL taken from the request without such a check. |
| No eval or new Function | high | tool | Shipped source never runs strings as code. | `eval`, `new Function`, or a string timer outside tests. |
| Message listeners check the sender's origin | high | model | Listeners check `event.origin` exactly before using `event.data`. | A listener acting on `event.data` without an exact origin check. |

### Layer 3 — Secrets, data, and browser hardening

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| No secrets in source | critical | tool | No high-confidence credential in tracked files and no secret value in committed environment files; with `--with-scan`, none in history. | A credential in a tracked file (value redacted), or gitleaks findings in history. |
| Local environment files are ignored by Git | critical | tool | A tracked `.gitignore` covers `.env` and every `.local` variant in each application folder, and no local file is committed. | A committed `.env*.local` (violation), or no tracked rule (missing or partial). |
| Public environment variables hold no secrets | critical | model | Only values designed to be public use `NEXT_PUBLIC_`, `VITE_`, or another public prefix. | A secret or server token exposed through a public-prefixed variable. |
| Server-only data never reaches client code | critical | model | Secret-reading modules import `server-only`; secrets and private fields never reach props, responses, or the client build. | A secret or private field, such as a password hash, reaching client code or a response. |
| Logs record no secrets or tokens | high | model | No token, password, secret value, or whole header or cookie set is logged or reported. | A log or error-report call that includes one. |
| Personal data stays out of URLs and analytics | medium | model | URLs and analytics events carry no personal data or tokens. Soft check — mixed adherence is reported as partial. | Personal data or tokens in a URL or an analytics payload. |
| A Content Security Policy restricts scripts | medium | model | HTML responses send a policy whose `script-src` uses nonces, hashes, or `strict-dynamic`. | No policy, or `unsafe-inline`, `unsafe-eval`, or wildcard script sources (partial without an injection path). |
| Other sites cannot frame the application | medium | model | `frame-ancestors` or `X-Frame-Options` restricts framing, unless embedding is required. | No framing restriction on pages that perform actions. |
| HTTPS is enforced | medium | model | HTTPS redirect and HSTS from the application or a documented platform default; no `http://` requests in production. | Neither in place, or plain-HTTP requests in production code. |
| Baseline security headers are set | low | model | `nosniff`, a strict `Referrer-Policy`, and a `Permissions-Policy` are sent. | One of them missing or permissive. |
| Third-party scripts and frames are constrained | medium | model | Pinned third-party scripts carry `integrity`; third-party iframes use a narrow `sandbox`. | A pinned script without `integrity`, or an unsandboxed third-party iframe. |

### Layer 4 — Large-language-model features

When the collector finds no AI SDK, model host, or chat-completions path, it says so in the snapshot. Search for a model call yourself before recording these checks not applicable.

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Model tool calls act only with the user's permissions | critical | model | Tools validate arguments, run as the end user with the same ownership checks, and confirm destructive or financial actions. | A tool acting on a model-supplied identifier without user scoping, or acting irreversibly without confirmation. |
| Model output is never rendered as raw HTML | critical | model | Output is rendered as text, or Markdown with raw HTML off and link schemes restricted. | Model output reaching an HTML sink or raw-HTML Markdown. |
| Prompts carry no secrets or other users' data | critical | model | Prompts and retrieval hold nothing the current user may not see. | A secret or another user's data in a prompt or retrieval context. |
| Model usage is bounded per user | medium | model | Model endpoints need a session, rate-limit per user, and cap tokens, steps, and input size. | No rate limit, no output cap, or unbounded tool loops. |

## What this skill does

1. Stages a run through the shared audit protocol. The collector decides the three tool checks and records the Layer 0 inventory.
2. Traces every entry point in the inventory to the data and sinks it reaches, instead of grepping for patterns.
3. Grades each model check against the supported-finding bar, challenges every critical and high candidate before recording it, and keeps disproved candidates as coverage evidence.
4. Publishes `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` through the protocol, and summarises the act-on findings.
5. Offers an implementation plan of agent briefs.

## Implementation steps

1. **Begin.** Follow [the run protocol](../audit-protocol/references/run-protocol.md). With `--worktree`, create the worktree as it describes.

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../audit-protocol/scripts/audit_run.py" begin security-audit
   ```

   - Pass `--since <ref>` for a diff-scoped run, and judge the entry points and sinks in the changed files.
   - Pass `--enrichment with-scan` to record `--with-scan`. The collector then scans history with gitleaks.
2. **Read the inventory.** Read the `snapshot` in `.architect-audits/security-audit/.staging/run.json`. Record the trust boundaries, the deliberately public entry points, and the platform defaults with `snapshot --set`.
3. **Run the scanners when asked.** With `--with-scan`, run Semgrep (`semgrep scan --config p/owasp-top-ten --config p/typescript --json --metrics=off`) and the project's own ESLint when it configures security plugins (`npx --no-install eslint . --format json`), if installed. Treat their hits as candidates to trace, not as findings. Cite a scanner in a `command:` entry beside the citation it led to.
4. **Trace every entry point.** For each Server Action, route handler, API route, loader, action, data-loading page or layout, webhook, and model tool:
   - who can call it;
   - what it reads from the request;
   - which session, ownership, and validation checks run, including in the data-access functions it calls;
   - which queries, requests, redirects, and responses its input reaches.

   Then follow the client sinks in the snapshot back to their sources. The detection notes give the method per check.
5. **Evaluate every model check.**
   - Record each with `record`, citing `path:line` with quoted fragments, and give every non-present result a `--tier`.
   - Quote secrets only as `<REDACTED>`.
   - A violation needs the full trace from entry point to impact. Missing hardening without a concrete failure scenario is `partial`.
   - Use `hypothesis` for a path you suspect but cannot complete from the code.
   - Use `not-applicable` when the code has no such surface, such as no hand-written OAuth flow.
6. **Challenge before recording.** For each critical or high candidate, try to refute it: look for the protective control in middleware, the data-access layer, framework defaults, React's escaping, or cookie defaults. When a subagent tool is available, give a fresh subagent the candidate and the trace and ask it to refute the failure scenario. Record a refuted candidate as present, citing the control that stops it.
7. **Judge.** Keep at most five findings `act-on` (`finish` refuses more; demote the rest with `judge`), ranked by severity, then by how reachable the entry point is (anonymous before authenticated). Dismiss with a reason anything a recorded decision covers.
8. **Finish.** Run `audit_run.py finish security-audit`, fix anything it rejects, and present the chat summary the protocol defines.

## Phase 2: the implementation plan

Ask once: "Generate an implementation plan for the act-on findings? (yes/no)". On yes, write `.architect-audits/security-audit/implementation-plan.md` as agent briefs, in the format the run protocol defines.
- Order the briefs: rotate exposed secrets first, then close anonymous entry points, then cross-user access, then everything else.
- Every change to authentication, authorization, sessions, or data flow is ASK. Only mechanical configuration, such as an ignore rule or a header, is AUTO-FIX.
- Each brief's acceptance criteria name an exploit check that succeeds at the current commit and fails after the fix. Examples: a request as another user returns 404; a request to `http://169.254.169.254/` is refused; the collector reports no secret.
- Prefer the highest enforcement level: a data-access layer that cannot query without a user, a schema that rejects extra fields, a lint rule, a test, then documentation.

## Repository Quality Score findings contract

The protocol publishes findings schema `2.0.0`. It writes one `runIdentifier`, `runStartedAt` and `runFinishedAt`, and the `checkCatalogVersion`. It records `applicability`, `evaluationState`, and `evidenceQuality` for every catalog check, and repeats the run identity in `metadata.json`. See `.agents/AUDIT_FINDINGS_CONTRACT.md` in the playbook repository.

## What this skill explicitly does NOT do

- Modify, move, or delete any project file. The only writes are under `.architect-audits/security-audit/`, through the protocol.
- Run the application, send requests, or attempt an exploit. Every finding comes from reading code and configuration.
- Print, store, or quote a secret value. Every citation replaces it with `<REDACTED>`.
- Audit dependency vulnerabilities or whether gates run in continuous integration. `/dependency-audit` and `/quality-gates-audit` own those.
- Report missing hardening as a violation without a concrete failure scenario, or skip a candidate because of its category.
- Review cryptographic protocol design, cloud or network infrastructure, or compliance with any regulatory framework.
- Prove the absence of vulnerabilities. A clean run means none of these checks found one.
