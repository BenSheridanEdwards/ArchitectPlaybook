---
name: error-handling-audit
description: Audit a TypeScript codebase's error-handling discipline against an opinionated baseline spanning throw and catch hygiene, async and network error handling, React error boundaries, and logging and observability. Static-only by design. Optionally generates an implementation plan for the gaps.
trigger: /error-handling-audit
---

# /error-handling-audit

Audit a TypeScript codebase's error-handling discipline against an opinionated baseline organised in four layers — **throw and catch hygiene**, **async and network error handling**, **React error boundaries**, **logging and observability** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

The default mental model is TypeScript and React. Layers 1, 2, and 4 apply to any TypeScript codebase; layer 3 is React-specific and is silently skipped when React is not detected.

## Posture: static-only, no opt-in modes

Unlike `/bundle-build-audit` (`--with-stats`) and `/dependency-audit` (`--with-network`), this skill has no opt-in enrichment mode. Error handling is a pure code-shape audit and there is no external data source that improves it.

The actual errors happening in production belong to **observability**, not to an architectural audit. They are the responsibility of the user's existing error-reporting tool (Sentry, Datadog, Bugsnag, etc.) or a separate skill. This audit reads code; it never queries any reporting service, and it never executes the codebase.

## Usage

```
/error-handling-audit                             # default: concise Top 5 + full report saved + ask about plan
/error-handling-audit --learn                     # mid-level engineer teaching mode (detailed explanations + file/line examples)
/error-handling-audit --teach                     # alias for --learn
```

**💡 Pro tip**: Use `/worktree error-handling` to run this in an isolated worktree.

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

This audit deliberately has no numeric threshold flags. Most checks are zero-tolerance (an empty catch is an empty catch); the rest report `partial` based on qualitative pattern detection. The canonical path to evolving the baseline itself is `/system-self-improve`.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the check is qualitative and the codebase shows mixed adherence.
- **missing** — a structural prerequisite is absent (no error-reporting service installed, for example).
- **violation** — the audit identified concrete code that breaks the invariant.

Layer 0 is informational only and has no status. Layer 3 reports `skipped: "react-not-detected"` (and no per-check entries) when React is absent.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Total `try`/`catch` block count and the average per source file.
- Total `throw` statement count.
- Asynchronous style breakdown: `async`/`await` count vs raw `.then()`/`.catch()` chain count.
- Detected error-reporting service (Sentry, Bugsnag, Rollbar, Datadog RUM, Honeybadger, Highlight, custom — or none).
- Detected logger (`console`, `pino`, `winston`, `loglevel`, framework-provided, custom — or none beyond `console`).
- Error-boundary component count and placement breakdown (root, route-level, feature-level, leaf — when React is detected).
- Top 10 distinct error types thrown by the project (Error, TypeError, SyntaxError, custom subclasses), with occurrence counts.

### Layer 1 — Throw and catch hygiene

| Check | Expectation | Violation signal |
| --- | --- | --- |
| No empty catch blocks | No `catch (e) {}` or `catch {}` with an empty body. | Any empty catch. Reported with file and line. |
| No silently swallowed errors | Every `catch` either logs/reports the error, returns a typed result (Result/Either pattern), or rethrows with context. | A catch that does none of the three. Reported with file and line. |
| No string or non-Error throws | Every `throw` throws an `Error` or subclass. | `throw "message"`, `throw 42`, `throw { code: 'X' }`. Reported with file and line. |
| TypeScript catch typing | `useUnknownInCatchVariables` is enabled in `tsconfig.json`, OR every catch parameter in source is annotated `: unknown`. | The compiler option is off and untyped or `any`-typed catch parameters appear in source. |
| Error chaining preserves cause | When rethrowing or wrapping, the original is attached via the `cause` option (`new WrappedError(message, { cause: e })`). | A catch whose body throws a fresh error without referencing the original. |
| Domain errors use custom classes | At least one custom error class hierarchy exists for domain errors. Soft check — reported as `partial` if absent. | All thrown errors are bare `Error`. |

### Layer 2 — Async and network error handling

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Network calls have error paths | Every `fetch` / `axios` / data-layer call site is inside a `try`/`catch` (when awaited) or has `.catch` (when chained), OR is in a function whose return type explicitly propagates the failure (TanStack Query function, Server Action, etc.). | Awaited `fetch`/`axios` with no surrounding error handling and no upward propagation. |
| Network calls have timeout configuration | Each network primitive uses `AbortSignal.timeout()`, a `signal` from a query layer, or an explicit timeout option. | Network calls without any timeout. |
| Retry logic for retriable failures | Retry behaviour is centralised in the data layer with explicit retriable conditions (5xx responses, 429, network errors), not duplicated per call site. Soft check. | Mixed: some call sites retry inline, others don't; or no retry strategy at all in an application that depends on network reliability. |
| Status-code branching is explicit | Network responses distinguish 4xx (client) from 5xx (server) and from network errors. Single-string error messages don't conflate them. | `if (!response.ok) throw new Error('Request failed')` with no status discrimination. |
| Top-level async errors captured | Async functions invoked from non-await contexts (`useEffect`, `setTimeout`, `addEventListener`, event handlers, promise constructors) capture rejections explicitly. | An `async` arrow passed as an event handler with no internal `try`/`catch`; an awaited call inside `useEffect` with no error path. |
| AbortController for cancellable requests | Components that issue requests and may unmount while a request is pending use `AbortController` (or the data layer's signal) to cancel on unmount. | Bare `fetch` inside `useEffect` with no cleanup. |

### Layer 3 — React error boundaries (skipped silently when React not detected)

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Root error boundary present | At least one error boundary wraps the application root. | No root boundary detected. |
| Route- or feature-level boundaries | Boundaries exist at route level (or feature-cluster level for a large application), not only at the root. | Only a single root boundary present in an application with multiple routes or features. |
| Fallback UI offers recovery | Boundary fallback components include at least one recovery affordance: retry, reload, navigate home. | Fallback that only renders an error message with no action. |
| Boundary forwards to reporting service | The boundary's `componentDidCatch` (or `react-error-boundary`'s `onError`) calls the detected error-reporting service. | Boundary that only renders the fallback without reporting. |
| Suspense paired with error boundary | Every `<Suspense>` that wraps async-loaded data has an error boundary as an ancestor. | A `<Suspense>` with no error boundary upstream. |
| No try/catch around JSX render | Render functions and components don't wrap return values in `try`/`catch` (it does not catch render errors anyway — that is the boundary's job). | A `try`/`catch` whose `try` block is a `return <Foo />`. |
| Mutation errors handled at call site | TanStack Query mutations, RTK Query mutations, and equivalents have explicit `onError` handlers, OR the consuming component handles `isError`. | Mutations with no error handling and no consuming `isError` branch. |

### Layer 4 — Logging and observability

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Error-reporting service integrated | A recognised error-reporting service is installed as a dependency. | None detected. |
| Reporting initialised at entry point | Initialisation happens before any application code runs (in `main.tsx`, `app/layout.tsx`, `pages/_app.tsx`, or the framework equivalent). | Service installed but not initialised in the entry point. |
| Release versioning attached | Reporting client is configured with a release identifier (git SHA, npm version, deploy ID). | No release configuration. |
| Source maps uploaded | A continuous-integration step uploads source maps to the reporting service after a successful build. | No upload step detected in any workflow file. |
| Production sample rate configured | The reporting client has an explicit `tracesSampleRate` (or equivalent) in production — not the SDK default. | Default sample rate in production. |
| No `console.log` in production code paths | `console.log`, `console.debug`, `console.warn` are absent from production code (allowed in tests, build scripts, dev-only files, and when guarded by `process.env.NODE_ENV !== 'production'`). `console.error` is acceptable when also reporting to the error service. | Unguarded `console.*` calls in source under `src/` (or the framework equivalent). |
| Structured logging | Logger calls pass a context object alongside the message, not a single string-concatenated argument. Soft check — reported as `partial` when adherence is mixed. | Logger calls that only pass a string. |
| Sensitive-data redaction configured | The reporting client has a `beforeSend` (or equivalent) configured to scrub passwords, tokens, and PII keys, OR a documented allowlist of safe fields. | No `beforeSend` or redaction configured. |
| Single log per error | An error that is caught, logged, and rethrown is not logged again at every layer above. Soft check. | Patterns where the same error path logs at multiple layers. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the audit uses the graph to trace which functions throw and how errors propagate, sharpening the top-level async-capture check. The audit still runs in full when the graph is absent.
2. **Confirms a TypeScript project.** Detects `package.json`, `tsconfig.json`, and a TypeScript dependency. If absent, the skill stops and tells the user it currently supports TypeScript projects only.
3. **Detects React.** Layer 3 is enabled only when `react` is in dependencies (directly or via a meta-framework that brings it in). When absent, layer 3 is recorded as skipped in `findings.json` and omitted from the chat summary.
4. **Detects the error-reporting service and the logger** for the diagnostic snapshot and for the layer 4 checks.
5. **Writes Layer 0 — the diagnostic snapshot** to `.architect-audits/error-handling-audit/snapshot.md` and prepends the same content to `findings.md`.
6. **Walks each check in the active layer list**, applying any `--include` and `--exclude` filters. Records a status, evidence, and (where relevant) sample file references per check.
7. **Writes phase 1 outputs** to `.architect-audits/error-handling-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), framework variant, detected reporting service, detected logger, applied filters, layer 3 skipped status when applicable.
8. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the error-handling gaps? (yes/no)"

   On `yes`, writes `.architect-audits/error-handling-audit/implementation-plan.md` describing exactly which catch blocks to fix, which network call sites to wrap, which React error boundaries to add, and which observability primitives to wire up — ordered by layer and then by severity. The plan does not modify any project files.

   On `no`, exits cleanly. 

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "error-handling-audit: no package.json detected. This skill currently supports TypeScript projects only."; exit 1; }
test -f tsconfig.json || { echo "error-handling-audit: no tsconfig.json detected. This skill currently supports TypeScript projects only."; exit 1; }
```

### Step 2 — Detect framework and observability stack

Read `package.json`. Resolve:

- React presence: `react` in dependencies (directly or via Next.js, Remix, etc.). Determines whether layer 3 runs.
- Framework variant: Next.js (App Router or Pages Router), Remix, Vite-React, Create React App, plain React, plain TypeScript. Influences entry-point detection in layer 4.
- Error-reporting service: scan `dependencies` for known packages — `@sentry/*`, `@bugsnag/*`, `rollbar`, `@datadog/browser-rum`, `@honeybadger-io/js`, `@highlight-run/*`. Record the first match, or `none`.
- Logger: scan for `pino`, `winston`, `loglevel`, framework-provided. When none of these is in dependencies, record `console` as the implicit logger.

### Step 3 — Build the diagnostic snapshot

Compute the items listed in Layer 0 by reading source files. Use Graphify's communities to sample broadly when present; otherwise sweep all `.ts`/`.tsx` files under `src/` (or the framework equivalent). Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 4 — Resolve each check

For each check in the active layer list, walk its detection logic:

- **All required signals indicate the invariant holds →** `present`.
- **Most signals hold; a small number of exceptions →** `partial`. For checks marked "soft", mixed adherence is the typical `partial` case.
- **The structural prerequisite is absent →** `missing` (e.g., layer 4 "error-reporting service integrated" when no service is installed).
- **Concrete violators exist →** `violation`. Always include sample file references.

For checks that can produce many offenders (empty catches, swallowed errors, unguarded `console.*`), record up to ten representative samples plus a total count rather than the exhaustive list.

### Step 5 — Write phase 1 outputs

Create `.architect-audits/error-handling-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 6 — Print the concise chat summary and offer phase 2

Print a human-first, scannable summary in the chat. Do not print the full layered findings — those are written to disk in Step 5. The chat output has exactly this shape:

1. **Short header** — audit name, timestamp, and a one-line summary of the codebase state.
2. **Top 5 Highest-Leverage Recommendations** — ordered by architectural principles: test philosophy, maintainability, risk reduction, velocity, long-term health. For fewer than five findings, print what exists. For each recommendation (numbered 1–5):
   - **Title** (one clear line).
   - **Why it matters** (explain the principle in 1–2 sentences).
   - **Real consequences if ignored** (honest downside for the team or project).
   - **Smallest high-leverage fix** (exact next step, effort level, and which files to touch).
   - At the end, add a lettered sub-list of concrete actions if useful (e.g. 2a, 2b) so the user can reply with "2b" or "1 and 3" to trigger implementation.
3. **Bottom line**: `Full detailed audit report (layered findings, snapshot, metadata, implementation plan) → .architect-audits/error-handling-audit/findings.md`

When `--learn` or `--teach` is set, expand each recommendation into mid-level engineer teaching mode:
- For every item, explain as if teaching a mid-level engineer, pointing to specific files and line numbers from the current codebase.
- Use educational language: "Here's why this pattern bites teams in the long run…", "This is the exact mistake I see in most codebases at your stage…", "The fix is small but pays off huge because…".
- Include a short "What you'll learn from fixing this" section for each recommendation.
- Keep the numbered/lettered structure so the user can still reply with "2b" or "1 and 3".
- End with the same bottom-line link to the full report.

After printing, ask the single yes-or-no question: *"Generate an implementation plan for the gaps identified above? (yes/no)"* Do not proceed to phase 2 without an explicit affirmative.

### Step 7 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, framework variant, detected reporting service, timestamp, total counts per layer.
2. **Layer 1 — throw and catch hygiene plan**: per finding, the file and line, the offending pattern, and the recommended replacement (typed result, rethrow with cause, custom error class).
3. **Layer 2 — async and network plan**: per finding, the call site, the wrapping pattern (try/catch with timeout, AbortController for components, query-layer extraction). Centralisation suggestions for retry strategy when scattered patterns are detected.
4. **Layer 3 — React error boundaries plan** (when React detected): boundary placement recommendations driven by route/feature structure (informed by Graphify communities when present), fallback-UI snippets with recovery affordances, reporting wiring snippet.
5. **Layer 4 — logging and observability plan**: installation snippet for the recommended reporting service when absent, initialisation snippet for the framework variant, source-map upload step for the continuous-integration workflow, sample-rate and `beforeSend` configuration, console-log replacements.
6. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not edit source files and it does not install packages.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:13Z",
  "framework": "next-app-router",
  "reactDetected": true,
  "reportingService": "sentry",
  "logger": "console",
  "snapshot": {
    "tryCatchCount": 142,
    "tryCatchPerFileMean": 1.8,
    "throwCount": 67,
    "asyncStyle": { "asyncAwait": 318, "thenChain": 42 },
    "errorBoundaryCount": 3,
    "errorBoundaryPlacement": { "root": 1, "route": 2, "feature": 0, "leaf": 0 },
    "topErrorTypes": [
      { "type": "Error", "count": 51 },
      { "type": "ApiError", "count": 14 }
    ]
  },
  "summary": {
    "throwAndCatchHygiene":     { "present": 4, "partial": 1, "missing": 0, "violation": 1 },
    "asyncAndNetwork":          { "present": 2, "partial": 2, "missing": 0, "violation": 2 },
    "reactErrorBoundaries":     { "present": 3, "partial": 1, "missing": 1, "violation": 2 },
    "loggingAndObservability":  { "present": 5, "partial": 1, "missing": 1, "violation": 2 }
  },
  "checks": [
    {
      "layer": "throw-and-catch-hygiene",
      "check": "no-empty-catch-blocks",
      "status": "violation",
      "evidence": [],
      "samples": [
        { "path": "src/features/inbox/syncMessages.ts", "line": 87 },
        { "path": "src/lib/storage.ts", "line": 23 }
      ],
      "totalCount": 4,
      "expectation": "No catch (e) {} or catch {} with an empty body.",
      "gap": "4 empty catch blocks detected.",
      "remediation": "Either log the error to the reporting service, return a typed Result, or rethrow with cause attached. Empty catches lose information that is impossible to reconstruct later."
    }
  ]
}
```

When React is not detected, `reactErrorBoundaries` summary becomes `"reactErrorBoundaries": { "skipped": "react-not-detected" }` and the corresponding entries do not appear in the `checks` array.

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), the framework, the detected reporting service and logger, the React-detected flag, and the configuration of the run.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags are recorded in `metadata.json` so a partial run can be reproduced.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| `no tsconfig.json detected` | JavaScript-only project. | Stop. Inform the user that the skill currently supports TypeScript projects only. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in `metadata.json`. The async-capture and propagation analysis falls back to broader sweeps with reduced precision. |
| React detected but no entry point found | Custom application bootstrap that the skill cannot recognise. | Continue layer 3 with the boundary-placement checks; record the framework as `react-custom` and skip the entry-point-specific layer 4 check. |
| Multiple error-reporting services detected | Migration in progress or accidental dual-install. | Record both in the snapshot; treat both for the layer 4 checks (each must be initialised, configured, etc.). Surface the dual-install as a `partial` finding on a synthetic check "single-reporting-service". |
| `useUnknownInCatchVariables` is true but catches still use `any` annotations | The compiler option does not retroactively change explicit annotations. | The TypeScript-catch-typing check still reports `violation` for explicit `any` annotations, with a remediation note explaining the option-vs-annotation distinction. |

## What this skill explicitly does NOT do

- Execute any code, run any test, or query any error-reporting service.
- Read production error data. Runtime error frequency and impact are out of scope; that belongs to the user's existing observability tooling.
- Install any package or dependency.
- Create, modify, or delete any file outside `.architect-audits/error-handling-audit/`.
- Modify source files, configuration, or continuous-integration workflows.
- Open pull requests or commit anything to git.
- Audit JavaScript-only projects.
- Replace error-handling code review by a human. The audit catches structural issues; nuanced "is this the right level to handle this error" decisions remain a human judgment.
- Encode an opinion about Result/Either patterns vs idiomatic throw/catch. Both are accepted as valid responses to the swallowed-errors check.
