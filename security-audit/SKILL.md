---
name: security-audit
description: Audit a TypeScript and React frontend against an opinionated security baseline spanning authentication/sessions, input handling and XSS prevention, transport/headers/cookies, and secrets/data protection/third-party integrations. Frontend-only in v1. Static-first with optional --with-scan enrichment. Optionally generates an implementation plan for the gaps.
trigger: /security-audit
---

# /security-audit

Audit a TypeScript and React frontend against an opinionated security baseline organised in four layers — **authentication, authorization, and sessions**, **input handling and XSS prevention**, **transport, headers, and cookies**, **secrets, data protection, and third-party integrations** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

## Scope: frontend-only in v1

This skill targets browser-shipped code and the frontend-relevant infrastructure that surrounds it. It is **deliberately not** a full-stack security audit. The user should know exactly what they are getting before they trust the report.

### In scope

- React and TypeScript code shipped to browsers.
- Frontend-relevant security configuration wherever it lives: framework configuration (Next.js `headers()`, Remix `loader`/`action` headers, Vite plugins) and deployment configuration (`vercel.json`, `netlify.toml`, Cloudflare Pages `_headers`, Apache/Nginx config when checked into the repository).
- Frontend-side OAuth and OpenID Connect flow patterns (PKCE, `state`, `nonce`, redirect URI handling).
- Client-side data-protection patterns (`localStorage` hygiene, PII in URLs and analytics events, secrets in source).
- Third-party integration safety (Subresource Integrity for external scripts, iframe sandboxing, `postMessage` origin validation).

### Out of scope

- Backend application security: SQL injection, server-side request forgery, command injection, server-side authorization logic, server-side rate limiting. A future `/backend-security-audit` is the home for those concerns.
- Penetration testing or runtime attack simulation. This audit reads code; it does not exploit anything.
- Cryptography scheme review. The audit flags use of weak primitives (MD5, SHA-1) when found in source; it does not review whole protocols, key rotation, or certificate handling.
- Compliance frameworks (PCI-DSS, HIPAA, GDPR, SOC 2). These are legal and policy frameworks; the audit cannot certify compliance.
- Infrastructure security (cloud IAM, network segmentation, secret managers themselves).
- Dependency vulnerabilities and supply-chain analysis. Those are owned by `/dependency-audit`. This skill mentions Subresource Integrity for externally-loaded `<script>` tags and notes the existence of `postinstall` scripts because both are frontend-bundle concerns; it does not re-audit the dependency tree.

## Static-first design with optional scan enrichment

This skill is read-only and never modifies anything. Two modes:

- **Static (default).** Pattern detection across source files, framework configuration, and deployment configuration. Catches the majority of the baseline.
- **Static plus opt-in `--with-scan`.** When the flag is passed, the skill additionally invokes any installed security-focused linter in JSON-reporter mode: `eslint-plugin-security`, `eslint-plugin-no-unsanitized`, `eslint-plugin-react-security`. If `semgrep` is on PATH, it runs Semgrep with the `p/owasp-top-ten` and `p/javascript` rule packs. The captured findings enrich the relevant checks. No installs, no network requests beyond what those tools issue themselves.

The skill never invokes any tool with `--fix` or any other mutating flag.

## Usage

```
/security-audit                                                     # full two-phase static run
/security-audit --with-scan                                         # static plus enrichment from installed scanners
/security-audit --report-only                                       # phase 1 only: write findings, do not ask about a plan
/security-audit --plan                                              # skip phase 1 if findings already exist; jump to plan generation
/security-audit --layer=authentication-and-sessions                 # restrict the audit to a single layer (repeatable)
/security-audit --layer=input-handling-and-xss
/security-audit --layer=transport-headers-and-cookies
/security-audit --layer=secrets-data-and-third-party
/security-audit --include=<check-name>                              # include only the named check (repeatable)
/security-audit --exclude=<check-name>                              # skip the named check (repeatable)
```

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

This audit deliberately has no numeric threshold flags. Most checks are zero-tolerance (any open redirect, any `eval`, any unsandboxed third-party iframe is a finding); soft checks report `partial` based on qualitative pattern detection. The canonical path to evolving the baseline itself is `/system-self-improve`.

**💡 Pro tip**: Spin this up in its own Git worktree with `/worktree security` (or just `/worktree` to pick from a list) so you can run multiple audits in true parallel without any conflicts.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions; or the check is heuristic and the codebase shows mixed adherence (PII detection in particular is reported as `partial` rather than `violation` because the heuristic is imperfect).
- **missing** — a structural prerequisite is absent (no Content Security Policy detected anywhere, for example).
- **violation** — the audit identified concrete code or configuration that breaks the invariant.

Layer 0 is informational only and has no status.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Detected authentication library: NextAuth/Auth.js, Clerk, Auth0 SDK, Supabase Auth, AWS Cognito, Firebase Auth, custom, or none.
- Detected sanitization library: DOMPurify, sanitize-html, isomorphic-dompurify, or none.
- Content Security Policy: detected yes/no, source location (header, meta tag, framework config), summary of directives.
- Security headers detected with source location: X-Frame-Options or `frame-ancestors`, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, Strict-Transport-Security.
- Cookie-handling primitive: framework session helpers, `cookies-next`, `js-cookie`, manual `document.cookie`, or none.
- `dangerouslySetInnerHTML` usage count and file references.
- `eval` / `new Function` / string-form `setTimeout`/`setInterval` usage count.
- `localStorage` and `sessionStorage` usage count, with a flag for keys whose names match auth-related patterns (`token`, `session`, `auth`, `jwt`).
- Inline `<script>` tag count.
- External `<script>` tag count and Subresource-Integrity coverage rate.
- Detected deployment platform: Vercel, Netlify, Cloudflare Pages, custom, or local-only. Used to resolve where security headers are expected to live.

### Layer 1 — Authentication, authorization, and sessions

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Session tokens not in `localStorage` | Long-lived session and authentication tokens (JWT, session ID) live in HttpOnly cookies, not in `localStorage` or `sessionStorage` (which are accessible to any script running on the page). | `localStorage.setItem`/`getItem` calls keyed on names matching auth patterns. |
| Logout invalidates server session | Logout calls a server endpoint (and the call is awaited or its failure is handled), not just clearing local state. | A logout flow that only clears local store/state with no server request. |
| Authentication state is server-derived | The "is the user logged in" check resolves against a server source of truth on each navigation or load — not from client-cached state alone. | Authentication state read only from client storage with no server validation step (route loader, middleware, or `useEffect` fetch). |
| OAuth and OIDC flows use PKCE | Public clients (browser apps) use Proof Key for Code Exchange: a `code_verifier` is generated and a `code_challenge` is sent in the authorization request. | OAuth flow without PKCE for a public client. Skipped silently when no OAuth flow is detected. |
| OAuth flows use a `state` parameter | Every OAuth authorization request includes a CSRF-resistant `state`, validated on callback. | `state` absent from the request, or absent from callback validation. |
| OIDC flows use a `nonce` | OpenID Connect flows include a `nonce` in the authorization request, validated on the ID token. | `nonce` absent or unvalidated. Skipped silently when no OIDC flow is detected. |
| Redirect URIs are explicitly handled | Post-authentication redirect destinations are validated against an allowlist or restricted to relative paths. They are never reflected from a query parameter without a check. | An `?next=...`/`?returnTo=...` parameter passed straight to `router.push` or `window.location` with no validation. |
| Server-side authorization referenced | UI-only authorization checks (component guards, conditional rendering by role) are paired with server-enforced checks at the data-fetching layer. The audit cannot verify the server-side check exists; it flags reliance on UI-only patterns. Soft check — reported as `partial`. | Permission checks appear only in components and route guards; data-fetching layer has no role/permission references. |

### Layer 2 — Input handling and XSS prevention

| Check | Expectation | Violation signal |
| --- | --- | --- |
| `dangerouslySetInnerHTML` only with sanitization | Every use of `dangerouslySetInnerHTML` either consumes content from a trusted, server-rendered source explicitly marked safe, or runs the input through DOMPurify (or equivalent). | Any `dangerouslySetInnerHTML` whose payload is reachable from user input, untrusted props, or fetched data with no sanitization. The Graphify graph (when present) materially improves accuracy here by tracing the data source. |
| No `eval` or dynamic code execution | No `eval`, `new Function`, `Function(...)`, or string-form `setTimeout`/`setInterval` in source. | Any of the above. |
| No raw `innerHTML`/`outerHTML` writes | DOM mutations go through React, not via `element.innerHTML = userInput`. | Direct `innerHTML` or `outerHTML` writes with non-literal right-hand sides. |
| URL `href` and `src` validated | User-controlled URLs used in `href`, `src`, or `formAction` are validated to allow only `http://`, `https://`, or relative paths — never `javascript:` or `data:`. | An `href={someInput}` or analogous expression with no protocol check. |
| Open-redirect patterns guarded | Code that performs a redirect (`router.push`, `window.location.assign`, server response redirect) does not pass user-controlled URLs through unchecked. | A redirect call site whose destination originates from a query parameter or form input with no allowlist. |
| Markdown and rich-text rendering use a safe pipeline | Projects rendering Markdown or rich text use a library configured to disallow HTML by default, or sanitize after rendering. | Markdown rendered with HTML pass-through enabled and no sanitization. Skipped silently when no Markdown or rich-text rendering is detected. |
| No bypassing React's escaping | `React.createElement` with raw HTML strings, custom JSX runtimes that disable escaping, or third-party "render unsafe" components are flagged. | Any of the above. |

### Layer 3 — Transport, headers, and cookies

| Check | Expectation | Violation signal |
| --- | --- | --- |
| HTTPS enforced | The deployed application redirects HTTP to HTTPS at the platform/edge layer, or carries a Strict-Transport-Security response header with a meaningful `max-age`. | Neither configuration detected. For projects with no detected deployment configuration (local-only), this check reports `partial` rather than `violation`. |
| Strict-Transport-Security set | An HSTS header is configured with `max-age >= 15768000`; `includeSubDomains` is recommended; `preload` is recommended for production. | Header missing, or `max-age` shorter than the threshold. |
| Content Security Policy defined | A Content Security Policy is set, either via response header (deployment configuration, framework `headers()`) or `<meta http-equiv="Content-Security-Policy">`. | No Content Security Policy detected anywhere. |
| Content Security Policy not overly permissive | The Content Security Policy does not use `unsafe-inline` or `unsafe-eval` for `script-src`, and does not use `*` as a source. Soft check — reported as `partial` if `unsafe-inline` is present alongside a documented nonce/hash strategy. | Direct `unsafe-eval`, `script-src *`, or `unsafe-inline` without a documented mitigation. |
| Frame-ancestors restricted | `Content-Security-Policy: frame-ancestors` is set, or `X-Frame-Options: DENY`/`SAMEORIGIN` is configured. Prevents clickjacking. | Neither detected. |
| `X-Content-Type-Options: nosniff` set | The header is configured. | Header missing. |
| Referrer-Policy set | A non-default Referrer-Policy is configured (`strict-origin-when-cross-origin` or stricter). | Header missing or set to `unsafe-url`. |
| Permissions-Policy set | A Permissions-Policy header restricts dangerous features (geolocation, camera, microphone, payment, USB, etc.) to the origins that need them. | Header missing. |
| Cookies use Secure, HttpOnly, SameSite | Authentication and session cookies set by client code (rare) or by detected server adapters carry `Secure`, `HttpOnly`, and `SameSite=Lax` (or stricter). | Cookies set without these flags. |
| No mixed content | Asset URLs and `fetch` URLs in source use `https://` or relative paths, not `http://`. | Any `http://` literal in production source. |

### Layer 4 — Secrets, data protection, and third-party integrations

| Check | Expectation | Violation signal |
| --- | --- | --- |
| No secrets in source | No API keys, tokens, passwords, private keys, or signing secrets present as string literals in source. Detection uses regex patterns for common secret formats (AWS access keys, Stripe keys, Google API keys, GitHub tokens, JWT, PEM blocks). | Any regex match in source files. |
| `.env*` files git-ignored | `.gitignore` excludes `.env`, `.env.local`, `.env.*.local`. | Any `.env*` file tracked, or pattern not present in `.gitignore`. |
| Public-prefix env vars are actually public | Variables prefixed with `VITE_*`, `NEXT_PUBLIC_*`, or `PUBLIC_*` are inlined into the client bundle and therefore published. The check verifies their names don't suggest secret content (`*_SECRET`, `*_PRIVATE`, `*_TOKEN` matching known secret-key patterns, `*_KEY` where the value is a real secret rather than a public identifier). | A variable like `NEXT_PUBLIC_API_SECRET` or `VITE_PRIVATE_KEY` shipping to every client. |
| No PII in URL paths or query strings | Routing and link generation don't put email addresses, phone numbers, government IDs, or API tokens in URLs (URLs land in browser history, server logs, and analytics). Soft check — heuristic detection by parameter-name patterns. Reported as `partial` rather than `violation` because the heuristic is imperfect. | Routes or link calls with parameter names matching PII patterns (`email`, `phone`, `ssn`, `creditCard`, `taxId`). |
| No PII in client-side analytics | Analytics event payloads (`gtag`, `posthog`, `mixpanel`, `segment`, `amplitude`, `heap`) don't include obvious PII fields. Soft check — heuristic, reported as `partial`. | Event property names matching PII patterns being passed to analytics. |
| Subresource Integrity on external scripts | `<script src="https://...">` tags from non-first-party origins carry an `integrity` attribute. | External scripts without SRI hashes. |
| Third-party iframes are sandboxed | `<iframe>` elements pointing at non-first-party origins use the `sandbox` attribute (with the most restrictive set of permissions the embed actually needs). | Unsandboxed third-party iframes. |
| `postMessage` origin validation | `window.addEventListener('message', ...)` handlers check `event.origin` against an allowlist before acting on `event.data`. | Listeners that read `event.data` without an origin check. |
| `target="_blank"` paired with `rel="noopener noreferrer"` | External links opening in new tabs include the `rel` attribute to prevent reverse-tab-nabbing. | `<a target="_blank">` without `rel="noopener noreferrer"`. |
| No console-logged secrets | The codebase doesn't pass auth tokens, environment-variable values, or full request-header objects directly to `console.*` or to third-party loggers. (Some overlap with `/error-handling-audit`'s redaction check; both surface the same gap so a single fix passes both.) | Patterns matching the above. |
| Weak cryptographic primitives flagged | Use of MD5 or SHA-1 for any security-relevant purpose (hashing passwords, signing, fingerprinting tokens) is reported. The audit does **not** flag MD5/SHA-1 used for non-security purposes (cache busting, content fingerprinting); the user-visible recommendation makes that distinction. Soft check. | `crypto.createHash('md5')` or `'sha1'` calls in code paths that look security-relevant. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the audit performs lightweight taint-flow analysis — tracing user-input sources to dangerous sinks (`dangerouslySetInnerHTML`, redirects, `eval`, raw URL handling) — which sharpens the input-handling layer's findings. The audit still runs in full when the graph is absent.
2. **Confirms a TypeScript or React project.** Detects `package.json` and (for layer 1's React-conditional checks) `react` in dependencies. If `package.json` is absent, the skill stops.
3. **Detects framework, deployment platform, and authentication library** for the diagnostic snapshot and for resolving where security headers are expected to live.
4. **When `--with-scan` is set**, invokes any installed security scanners and captures their output:
   - ESLint plugins via `npx eslint --no-eslintrc --rulesdir ... --format json` (or by reading the project's existing ESLint configuration if those plugins are already enabled there).
   - Semgrep via `semgrep --config=p/owasp-top-ten --config=p/javascript --json` if `semgrep` is on PATH.
5. **Writes Layer 0 — the diagnostic snapshot** to `audits/security-audit/snapshot.md` and prepends the same content to `findings.md`.
6. **Walks each check in the active layer list**, applying any `--include` and `--exclude` filters. Records a status, evidence, and (where relevant) sample file references per check. Heuristic checks (PII detection) explicitly mark `confidence: "heuristic"` in `findings.json` and report `partial`.
7. **Writes phase 1 outputs** to `audits/security-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), framework, deployment platform, detected authentication library, sanitization library, scanner output presence, applied filters.
8. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the security gaps? (yes/no)"

   On `yes`, writes `audits/security-audit/implementation-plan.md` describing exactly which configuration entries to add (security headers, Content Security Policy directives, cookie flags), which source-level changes to make (sanitization wrapping, redirect allowlists, SRI hashes), and which third-party integrations to harden. The plan is ordered by **severity** rather than by layer, because security findings cross layers and the user wants to fix the highest-impact issues first.

   On `no`, exits cleanly. The user can re-run with `--plan` later to skip phase 1.

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "security-audit: no package.json detected. This skill currently supports TypeScript and React frontend projects only."; exit 1; }
```

### Step 2 — Detect framework, deployment platform, and security stack

Read `package.json`, framework configuration, and deployment configuration. Resolve:

- Framework variant: Next.js (App Router or Pages Router), Remix, Vite-React, Create React App, plain React, plain TypeScript.
- Deployment platform (where security headers are likely defined): Vercel (`vercel.json`), Netlify (`netlify.toml`, `_headers`), Cloudflare Pages (`_headers`), or custom/local-only.
- Authentication library: scan `dependencies` for `next-auth`, `@auth/*`, `@clerk/*`, `@auth0/*`, `@supabase/auth-helpers-*`, `aws-amplify`, `firebase`, custom-marker. Record the first match or `none`.
- Sanitization library: scan for `dompurify`, `isomorphic-dompurify`, `sanitize-html`. Record or `none`.

### Step 3 — Optionally run scanners

When `--with-scan` is set:

- Detect `eslint-plugin-security`, `eslint-plugin-no-unsanitized`, `eslint-plugin-react-security` in `devDependencies`. If present and already configured in the project's ESLint configuration, run `npx eslint . --format json` and parse the output (filter to only the security-plugin rules). If present but not configured, skip with a metadata note.
- Detect `semgrep` on PATH (`command -v semgrep`). If present, run `semgrep --config=p/owasp-top-ten --config=p/javascript --json` (no `--autofix`) and parse the output.

If any scanner fails to start, record the failure and continue. Scan-dependent enrichment of layer-2 and layer-4 checks degrades gracefully.

### Step 4 — Build the diagnostic snapshot

Compute the items listed in Layer 0 by reading source files, framework configuration, and deployment configuration. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 5 — Resolve each check

For each check in the active layer list, walk its detection logic. Use the standard status taxonomy. Heuristic checks explicitly carry `confidence: "heuristic"` in their `findings.json` entry and report `partial`. Layer 2 and layer 4 checks that have additional scanner-derived findings record those alongside the static evidence.

### Step 6 — Write phase 1 outputs

Create `audits/security-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 7 — Print the chat summary and offer phase 2

Print the snapshot's headline numbers (framework, deployment platform, auth library, Content Security Policy presence, dangerouslySetInnerHTML count) plus a short table mirroring the four layers with counts of present, partial, missing, and violation per layer. Highlight the **top five** violations across all layers, ordered by severity (anything in layer 1 — auth/session — and layer 2 — XSS — outranks layer 3 and layer 4 by default; within a layer, hard violations outrank partials). Then ask the single yes-or-no question. Do not proceed to phase 2 without an explicit affirmative.

### Step 8 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`, **ordered by severity** rather than by layer:

1. **Header** — repository name, baseline version, framework, deployment platform, authentication library, timestamp, total counts per layer.
2. **Critical: authentication and XSS gaps** — every layer 1 violation and every layer 2 violation goes here, with file references and concrete remediation snippets (sanitization wrapping, redirect allowlist, PKCE wiring, etc.).
3. **High: transport and header gaps** — every layer 3 violation. Per missing header, the exact configuration snippet for the detected deployment platform (vercel.json fragment, netlify.toml fragment, `_headers` fragment, framework `headers()` function fragment).
4. **Medium: secrets and third-party integration gaps** — every layer 4 violation. Per finding, the file or configuration to change.
5. **Soft findings** — every `partial` from heuristic checks (PII detection, server-side authorization reliance), with recommendations but no urgent action expected.
6. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not modify configuration, install packages, or edit source.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:18Z",
  "framework": "next-app-router",
  "deploymentPlatform": "vercel",
  "authLibrary": "next-auth",
  "sanitizationLibrary": "dompurify",
  "withScan": true,
  "scannersExecuted": ["eslint-plugin-security", "semgrep p/owasp-top-ten"],
  "snapshot": {
    "contentSecurityPolicy": { "detected": true, "source": "vercel.json", "directives": ["default-src 'self'", "script-src 'self' 'nonce-...'"] },
    "securityHeaders": {
      "strictTransportSecurity": { "present": true, "source": "vercel.json", "maxAge": 31536000, "includeSubDomains": true, "preload": false },
      "xFrameOptions": { "present": true, "source": "vercel.json", "value": "DENY" },
      "xContentTypeOptions": { "present": false },
      "referrerPolicy": { "present": true, "source": "vercel.json", "value": "strict-origin-when-cross-origin" },
      "permissionsPolicy": { "present": false }
    },
    "dangerouslySetInnerHtmlCount": 6,
    "evalCount": 0,
    "localStorageAuthKeys": ["accessToken"],
    "externalScriptCount": 4,
    "externalScriptSriCoverage": 0.5
  },
  "summary": {
    "authenticationAndSessions":   { "present": 4, "partial": 1, "missing": 0, "violation": 2 },
    "inputHandlingAndXss":         { "present": 4, "partial": 0, "missing": 0, "violation": 1 },
    "transportHeadersAndCookies":  { "present": 6, "partial": 1, "missing": 2, "violation": 1 },
    "secretsDataAndThirdParty":    { "present": 7, "partial": 2, "missing": 0, "violation": 2 }
  },
  "checks": [
    {
      "layer": "authentication-and-sessions",
      "check": "session-tokens-not-in-localstorage",
      "status": "violation",
      "confidence": "high",
      "evidence": [],
      "samples": [
        { "path": "src/lib/auth/session.ts", "line": 14, "key": "accessToken" }
      ],
      "expectation": "Long-lived session and authentication tokens live in HttpOnly cookies, not in localStorage or sessionStorage.",
      "gap": "accessToken is written to localStorage; any script running on the page can read it.",
      "remediation": "Move token storage to an HttpOnly, Secure, SameSite=Lax cookie set by the server; refactor the client to call /api/me for the session rather than reading from localStorage."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), the framework, the deployment platform, the detected authentication and sanitization libraries, the `withScan` flag, the list of scanners that actually executed, and the applied filters.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags and the `--with-scan` flag are recorded in `metadata.json` so a partial run can be reproduced.
- Scanner-derived findings are timestamp-tagged; staleness is the user's responsibility to manage by re-running.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The taint-flow analysis on layer 2 falls back to per-file pattern matching with reduced precision; everything else is unaffected. |
| `--with-scan` set but no scanners installed | None of the recognised security scanners is in `devDependencies` and `semgrep` is not on PATH. | Continue with the static analysis. Record `scannersExecuted: []` in metadata. The implementation plan recommends installing one of the scanners. |
| Scanner fails to start | Configuration mismatch, version incompatibility, or path resolution issue. | Record the failure in metadata and continue. Scanner-derived enrichment of relevant checks is omitted; the static-derived findings still report. |
| Multiple deployment platforms detected (e.g., both `vercel.json` and `netlify.toml` present) | Project supports multiple deployment targets. | Record both in the snapshot. Header checks resolve against whichever has the most security headers configured; the user is recommended to harmonise both in the implementation plan. |
| Content Security Policy split across header and meta tag | CSP defined in both `<meta http-equiv>` and a response header. | Record both sources. Treat the union of directives as the effective policy for permissiveness checks. Surface the dual-definition as a `partial` finding on a synthetic "single-csp-source" check. |
| `.env` files exist but `.gitignore` is missing entirely | New project, never initialised. | The "`.env*` files git-ignored" check reports `violation` regardless of whether `.env` files happen to be tracked yet (the protection is missing). |
| Heuristic check matches a false positive | Parameter name like `email` is used for a non-PII purpose (newsletter campaign identifier, etc.). | The check reports `partial` not `violation`, and the remediation guidance asks the user to confirm the parameter does not actually carry PII before acting. |

## What this skill explicitly does NOT do

- Run any code, exploit any vulnerability, or interact with the running application.
- Audit backend application security. SQL injection, server-side request forgery, command injection, server-side authorization logic, server-rate-limiting are out of scope for this skill.
- Audit dependency vulnerabilities or supply chain. That is owned by `/dependency-audit`.
- Modify any source file, configuration file, or continuous-integration workflow.
- Install any package or dependency.
- Open pull requests or commit anything to git.
- Certify compliance with PCI-DSS, HIPAA, GDPR, SOC 2, or any other regulatory framework. The audit's findings can support a compliance effort but are not themselves a compliance assessment.
- Replace human security review for high-risk changes (payment flows, authentication code, data export, admin tooling). The audit catches structural issues; nuanced threat modelling remains a human responsibility.
- Audit cryptography schemes. The audit flags use of weak primitives in source; it does not review key rotation, certificate handling, or protocol design.
- Detect every possible secret. Regex-based detection has a false-negative tail; the audit recommends pairing it with a dedicated secrets-scanning tool (gitleaks, trufflehog) in continuous integration.
