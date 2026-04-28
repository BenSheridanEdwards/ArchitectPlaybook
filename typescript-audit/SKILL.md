---
name: typescript-audit
description: Audit a TypeScript project's type discipline against an opinionated baseline spanning compiler configuration, type quality in source, type system usage, and type safety at IO boundaries. Static-first with optional --with-run enrichment from tsc --noEmit. Optionally generates an implementation plan for the gaps.
trigger: /typescript-audit
---

# /typescript-audit

Audit a TypeScript project's type discipline against an opinionated baseline organised in four layers — **compiler configuration**, **type quality in source**, **type system usage**, **type safety at boundaries** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

The default mental model is TypeScript and React. Layers 1, 2, 3 apply to any TypeScript codebase; layer 4's IO-boundary checks lean toward frontends and full-stack applications, but most checks apply equally to backend TypeScript.

## How this differs from neighbouring audits

| Concern | Owner |
| --- | --- |
| Whether `tsc --noEmit` *runs* at every lifecycle stage | `/quality-gates-audit` |
| `@typescript-eslint` plugin coverage and rule selection | `/linting-audit` |
| `incremental` and project references for *build performance* | `/bundle-build-audit` |
| `useUnknownInCatchVariables` *as it affects catch typing* | `/error-handling-audit` |
| **Whether the compiler is configured strictly** | `/typescript-audit` |
| **Quality of types written in source** (`any`, assertions, `@ts-ignore`) | `/typescript-audit` |
| **Type system usage** (discriminated unions, branded types, utility types) | `/typescript-audit` |
| **Type safety at IO boundaries** (runtime validation of network/form/storage data) | `/typescript-audit` |

When a single fix passes multiple audits — for example, enabling `useUnknownInCatchVariables` satisfies both `/error-handling-audit` (which checks the resulting catch typing) and `/typescript-audit` (which checks the compiler flag) — every relevant audit surfaces the same gap so the user sees it once and resolves it once.

## Static-first design with optional run enrichment

This skill is read-only and never modifies anything. Two modes:

- **Static (default).** Read `tsconfig.json` (and any `extends`-chained configs), `package.json`, and source files. Pattern-detect type-quality signals across `.ts` and `.tsx` files.
- **Static plus opt-in `--with-run`.** Invoke `npx tsc --noEmit` (no emit, no side effects) and parse the diagnostics. Provides a real error count and the actual locations of every type error, which sharpens layer 2 substantially.

`tsc --noEmit` is genuinely read-only — it does not write to `dist/`, `.next/`, or anywhere else. The skill never invokes the compiler with any other flag, never installs anything, and never edits configuration.

## Usage

```
/typescript-audit                                 # default: concise Top 5 + full report saved + ask about plan
/typescript-audit --learn                         # mid-level engineer teaching mode (detailed explanations + file/line examples)
/typescript-audit --teach                         # alias for --learn
/typescript-audit --with-run                      # static plus enrichment from tsc --noEmit
/typescript-audit --threshold-any-per-file=5      # override default 3
/typescript-audit --threshold-as-per-file=10      # override default 5 (excluding `as const`)
/typescript-audit --threshold-non-null-assertion-per-file=5  # override default 3
/typescript-audit --threshold-conditional-type-depth=4       # override default 3
```

**💡 Pro tip**: Use `/worktree typescript` to run this in an isolated worktree.

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

**💡 Pro tip**: Run `/preflight --audit=typescript` first to detect — and optionally install — the development dependency that makes `--with-run` useful (`typescript`, which provides `tsc --noEmit`). Skip if you already know the tooling is wired up.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the codebase shows mixed adherence to a soft check.
- **missing** — a structural prerequisite is absent (no `tsconfig.json`, for example — the skill stops earlier in that case, but the status is reserved).
- **violation** — the audit identified concrete configuration or source that breaks the invariant.

Layer 0 is informational only and has no status.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- TypeScript version (resolved from the lockfile).
- `tsconfig.json` path(s); when `extends` is used, the resolved final configuration is recorded.
- Detected runtime validation library: zod, valibot, arktype, io-ts, yup, joi, superstruct, runtypes — or none.
- Strict-flag adoption: which of `strict`, `noImplicitAny`, `strictNullChecks`, `strictFunctionTypes`, `strictBindCallApply`, `strictPropertyInitialization`, `noImplicitThis`, `alwaysStrict`, `useUnknownInCatchVariables`, `noUncheckedIndexedAccess`, `exactOptionalPropertyTypes`, `noImplicitReturns`, `noFallthroughCasesInSwitch`, `noImplicitOverride` are enabled.
- Type-quality counts across source: `any` annotations, `unknown` annotations, `as` assertions (excluding `as const`), `as const` assertions, non-null assertions (`!`), `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`.
- Top 10 files by `any` count, top 10 by `as` count, top 10 by `!` count.
- Project references count (composite projects in monorepos).
- `tsc --noEmit` total error count when `--with-run`.

### Layer 1 — Compiler configuration

| Check | Expectation | Violation signal |
| --- | --- | --- |
| `strict: true` | The strict family is enabled wholesale via the `strict` flag. | `strict: false`, or `strict` absent and individual strict-family flags missing. |
| `noUncheckedIndexedAccess` enabled | Distinguishes `array[i]: T` from `array[i]: T \| undefined`, catching a major source of runtime errors that the compiler otherwise ignores. | Flag missing or `false`. |
| `exactOptionalPropertyTypes` enabled | Distinguishes `prop?: T` from `prop: T \| undefined`. Soft check — reported as `partial` because some codebases legitimately can't enable it without significant refactoring. | Flag missing or `false`. |
| `noImplicitReturns` enabled | Catches functions that fall off the end without returning when their type says they should. | Flag missing or `false`. |
| `noFallthroughCasesInSwitch` enabled | Catches missing `break` or `return` in switch cases. | Flag missing or `false`. |
| `useUnknownInCatchVariables` enabled | Catch variables default to `unknown`, not `any`. (Overlap with `/error-handling-audit`; both surface so a single fix passes both.) | Flag missing or `false`. |
| `noImplicitOverride` enabled | Class methods that override a parent must use the `override` keyword. Soft check — reported as `partial` for codebases with no classes. | Flag missing in a class-using codebase. |
| `isolatedModules` enabled | Required by every modern bundler (Vite, esbuild, swc, Bun) for safe per-file transpilation. | Flag missing or `false` in a project using one of those bundlers. |
| `skipLibCheck` is a deliberate choice | The flag is either explicitly `true` (with the awareness that third-party `.d.ts` files won't be type-checked) or explicitly `false`. The check verifies the choice was made; it doesn't pick a side. Soft check. | The flag is absent and the default behaviour applies (which differs across TypeScript versions). |
| `target` and `module` appropriate | `target` matches the runtime (`ES2022` or newer for modern Node and modern browsers). `module` is `ESNext`, `NodeNext`, or `Preserve` — not `CommonJS` for browser code. | `target: 'ES5'` or older in a project that ships modern environments only; or `module` mismatched against the resolution model. |
| `moduleResolution` set explicitly | `moduleResolution` is `Bundler` (Vite, esbuild) or `NodeNext` (Node), set explicitly rather than defaulting. | Flag absent. |
| Composite or project references for monorepos | Monorepos use TypeScript project references (composite projects) so type-checks scale across packages. Skipped silently outside monorepos. Soft check. | Multi-package project without project references. |

### Layer 2 — Type quality in source

| Check | Expectation | Violation signal |
| --- | --- | --- |
| `any` usage is rare | The total `any` annotation count per file is at or below the threshold (default 3; tunable via `--threshold-any-per-file`). The audit counts both scalar `any` and `any[]`. | Files exceeding the per-file threshold. |
| `unknown` preferred over `any` for opaque values | `unknown` appears in the codebase as the safe alternative to `any` when typing genuinely opaque shapes. Soft check — reported as `partial` when no `unknown` appears at all in a non-trivial codebase. | A codebase with substantial `any` usage and zero `unknown`. |
| `@ts-ignore` replaced by `@ts-expect-error` with description | No `@ts-ignore` directives remain; all suppressions are `@ts-expect-error` with a free-text justification on the same or adjacent line. | Any `@ts-ignore` in source. |
| No `@ts-nocheck` at file level | No file disables type checking entirely. | Any `@ts-nocheck` at the top of a source file. |
| Type assertions (`as`) are rare | The total `as` assertion count per file is at or below the threshold (default 5; tunable via `--threshold-as-per-file`). The audit recognises that `as const` is not a type assertion in the dangerous sense and excludes it from the count. | Files exceeding the per-file threshold. |
| Non-null assertions (`!`) are rare | The total non-null assertion count per file is at or below the threshold (default 3; tunable via `--threshold-non-null-assertion-per-file`). | Files exceeding the per-file threshold. |
| No `Function` type | Use specific function signatures, not the unsafe `Function` type (which permits any callable). | Any `Function` annotation in source. |
| No `Object` type | Use `Record<string, unknown>`, a specific shape, or `object` (lowercase). | Any `Object` annotation in source. |
| No primitive wrapper types | Use `string`, `number`, `boolean` — not `String`, `Number`, `Boolean` (the wrapper-object types). | Any wrapper-type annotation in source. |
| No empty interfaces | Interfaces declare at least one member, or extend another type. An empty interface either does nothing or is a same-shape alias for what it extends. | `interface Foo {}` declarations. |

### Layer 3 — Type system usage

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Discriminated unions for state machines | Multi-stage state is modelled as a discriminated union (`{ status: 'idle' } \| { status: 'loading' } \| { status: 'success'; data: T } \| { status: 'error'; error: E }`), not as multiple booleans or wide objects with optional fields. (Overlap with `/react-audit`'s "status enums over multiple booleans"; both surface.) Soft check — reported as `partial`. | Multi-state shapes hand-rolled with several optional fields and no discriminant. |
| Branded or nominal types for IDs | Identifier types (`UserId`, `OrderId`, `WorkspaceId`) are branded so the compiler distinguishes `UserId` from `OrderId` even though both carry `string` at runtime. Soft check — reported as `partial`. | Identifier types declared as bare `string` aliases. |
| `as const` for literal preservation | Configuration objects, route lists, and similar literal values use `as const` so the compiler infers the narrow literal type rather than widening to `string` or `number`. Soft check — reported as `partial`. | Patterns where `as const` would tighten inference and is missing. |
| Utility types preferred over hand-rolled | Use `Pick`, `Omit`, `Partial`, `Required`, `Readonly`, `Record`, `NonNullable`, `Awaited`, `ReturnType`, `Parameters` rather than hand-rolling equivalents. | Hand-rolled mapped types that duplicate a built-in utility. |
| Generic constraints used | Generic parameters that are operated on with `.length`, indexing, or property access have `extends` constraints, not bare `T`. | Bare `T` generics whose body operates on the parameter assuming a shape. |
| Conditional types used judiciously | Conditional types in source are bounded — no nested-deeper-than-threshold conditional chains in application code. The threshold is tunable via `--threshold-conditional-type-depth=N` (default 3). The audit isn't strict about this; library code can warrant complexity. Soft check — reported as `partial` when the depth threshold is exceeded. | Nested conditional types deeper than the threshold. |

### Layer 4 — Type safety at boundaries

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Runtime validation library installed | One of zod, valibot, arktype, io-ts, yup, joi, superstruct, runtypes is in `dependencies`. Soft check — reported as `partial` when absent (a project may legitimately have no untyped IO boundaries beyond a fully-typed framework like tRPC end-to-end). | None present in a project that performs network requests, parses JSON, or reads form/URL/storage data. |
| Network response data validated | Calls to `fetch`, `axios`, framework data primitives have their JSON parsed through a validator (zod schema, etc.) before being typed. The audit recognises framework-level type-safe layers (tRPC, GraphQL Code Generator outputs) as equivalent. | Network responses cast directly with `as` or implicitly typed without runtime validation. |
| `JSON.parse` results validated | `JSON.parse(...)` outputs are validated, not typed by direct assertion. | `JSON.parse(...) as MyType` patterns. |
| Form data validated | Forms validate input with the form library's schema integration (react-hook-form + resolver, formik + Yup, equivalent) or a standalone validator. (Overlap with `/react-audit`'s form-library check; both surface.) | Form data passed straight to a typed handler with no validator. |
| URL parameters validated | Router-extracted params (`useSearchParams`, `useParams`, `router.query`) are validated before being typed as anything stricter than `string` or `string[]`. | URL parameters cast to specific types or assumed narrower than their actual runtime type. |
| `localStorage`/`sessionStorage` reads validated | Storage reads are parsed *and* validated before being typed. | `JSON.parse(localStorage.getItem('key')!)` patterns with no validator. |
| Environment variables validated | Environment-variable access (`process.env`, `import.meta.env`) goes through a validated configuration module — not direct reads scattered across the codebase. Soft check — reported as `partial`. | Direct `process.env.X` reads in feature code rather than a validated config module. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the audit cross-references type-quality offenders (high-`any`, high-`as`, high-`!` files) against graph centrality and prioritises god nodes in the implementation plan. The audit still runs in full when the graph is absent.
2. **Confirms a TypeScript project.** Detects `package.json`, `tsconfig.json`, and `typescript` in dependencies. If any are absent, the skill stops and tells the user it currently supports TypeScript projects only.
3. **Resolves the effective `tsconfig.json`** by following any `extends` chain. Records the final resolved configuration in metadata.
4. **Detects the runtime validation library** (zod, valibot, arktype, io-ts, yup, joi, superstruct, runtypes) for the diagnostic snapshot and for the layer 4 checks.
5. **When `--with-run` is set**, invokes `npx tsc --noEmit` and parses the diagnostics. If `tsc` fails to start (not installed in the project), records the failure, prints to the chat and prepends to `findings.md`: "`--with-run` was requested but `typescript` is not installed. Run `/preflight --audit=typescript --install` to install it, then re-run this audit. The static analysis has been completed; only the run-dependent enrichment degraded to `partial`." Records `recoveryHint: "/preflight --audit=typescript --install"` on each run-dependent check that degraded in `findings.json`. Continues with run-dependent enrichment degrading to `partial`.
6. **Walks every source file** to compute the type-quality counts, locating each offending pattern with file and line. Uses Graphify communities to sample broadly when the graph is present; otherwise sweeps all `.ts` and `.tsx` files under `src/` (or the framework equivalent).
7. **Writes Layer 0 — the diagnostic snapshot** to `.architect-audits/typescript-audit/snapshot.md` and prepends the same content to `findings.md`.
8. **Walks each check in the active layer list**, applying any `--include`, `--exclude`, and threshold overrides. Records a status, evidence, and (where relevant) sample file references per check.
9. **Writes phase 1 outputs** to `.architect-audits/typescript-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), TypeScript version, validation library, applied thresholds, applied filters, run-mode flag.
10. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

    > "Generate an implementation plan for the TypeScript discipline gaps? (yes/no)"

    On `yes`, writes `.architect-audits/typescript-audit/implementation-plan.md` describing exactly which compiler flags to flip on, which files to clean up (graph-prioritised when Graphify is present), which type-system patterns to introduce, and which IO boundaries to wrap with validation. The plan does not modify any project files.

    On `no`, exits cleanly. 

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "typescript-audit: no package.json detected. This skill currently supports TypeScript projects only."; exit 1; }
test -f tsconfig.json || { echo "typescript-audit: no tsconfig.json detected. This skill currently supports TypeScript projects only."; exit 1; }
```

Verify TypeScript is installed: `typescript` in `dependencies` or `devDependencies`. When absent, stop with a friendly message.

### Step 2 — Resolve the effective tsconfig

Follow the `extends` chain in `tsconfig.json`. Build the merged `compilerOptions` object that the compiler would actually use. Record the resolved configuration in `metadata.json` so the user can audit which flags the audit graded against.

### Step 3 — Detect the runtime validation library

Scan `dependencies` for `zod`, `valibot`, `arktype`, `io-ts`, `yup`, `joi`, `superstruct`, `runtypes`. Record the first match (or all matches) in metadata. The detection seeds the layer 4 checks: when a library is present, the IO-boundary checks look for its usage at validation sites; when absent, they look for type-safe framework layers (tRPC, GraphQL Code Generator outputs) before reporting.

### Step 4 — Optionally run `tsc --noEmit`

When `--with-run` is set, invoke:

```bash
npx tsc --noEmit
```

Capture the exit code and the diagnostics output. If exit non-zero is the result of type errors (the expected case), parse the diagnostics. If exit non-zero is the result of `tsc` not being installed or the configuration being invalid, record the failure in metadata and degrade run-dependent enrichment to `partial`.

### Step 5 — Walk source files for type-quality counts

Enumerate `.ts` and `.tsx` files. Detection patterns:

- `any` annotations: identifiers with `: any` or `: any[]` annotations, or `as any` assertions.
- `unknown` annotations: identifiers with `: unknown` annotations, or `as unknown` assertions.
- `as` assertions: `expr as Type` patterns. Excludes `as const`.
- `as const` assertions: counted separately for the snapshot.
- Non-null assertions: `expr!` patterns. Excludes the boolean `!` operator.
- `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck` comments.
- Dangerous types: `Function`, `Object`, `String`, `Number`, `Boolean` annotations.
- Empty interfaces: `interface Foo {}` (no members and no `extends`).

For each pattern, record file, line, and surrounding context.

### Step 6 — Build the diagnostic snapshot

Aggregate the per-file data into the items listed in Layer 0. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 7 — Resolve each check

For each check in the active layer list, walk its detection logic. Threshold-bearing checks compare aggregated counts to the configured threshold (per file, not per project — a project with three files at the threshold isn't worse than a project with one file at the threshold; offenders are flagged individually).

For each check, record evidence and up to ten representative samples plus a total count.

### Step 8 — Write phase 1 outputs

Create `.architect-audits/typescript-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 9 — Print the concise chat summary and offer phase 2

Print a human-first, scannable summary in the chat. Do not print the full layered findings — those are written to disk in Step 8. The chat output has exactly this shape:

1. **Short header** — audit name, timestamp, and a one-line summary of the codebase state.
2. **Top 5 Highest-Leverage Recommendations** — ordered by architectural principles: test philosophy, maintainability, risk reduction, velocity, long-term health. For fewer than five findings, print what exists. For each recommendation (numbered 1–5):
   - **Title** (one clear line).
   - **Why it matters** (explain the principle in 1–2 sentences).
   - **Real consequences if ignored** (honest downside for the team or project).
   - **Smallest high-leverage fix** (exact next step, effort level, and which files to touch).
   - At the end, add a lettered sub-list of concrete actions if useful (e.g. 2a, 2b) so the user can reply with "2b" or "1 and 3" to trigger implementation.
3. **Bottom line**: `Full detailed audit report (layered findings, snapshot, metadata, implementation plan) → .architect-audits/typescript-audit/findings.md`

When `--learning` or `--teach` is set, expand each recommendation into mid-level engineer teaching mode:
- For every item, explain as if teaching a mid-level engineer, pointing to specific files and line numbers from the current codebase.
- Use educational language: "Here's why this pattern bites teams in the long run…", "This is the exact mistake I see in most codebases at your stage…", "The fix is small but pays off huge because…".
- Include a short "What you'll learn from fixing this" section for each recommendation.
- Keep the numbered/lettered structure so the user can still reply with "2b" or "1 and 3".
- End with the same bottom-line link to the full report.

After printing, ask the single yes-or-no question: *"Generate an implementation plan for the gaps identified above? (yes/no)"* Do not proceed to phase 2 without an explicit affirmative.

### Step 10 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, TypeScript version, validation library, timestamp, total counts per layer.
2. **Layer 1 — compiler configuration plan**: per missing flag, the JSON snippet to add to `tsconfig.json` plus a short note on what the flag catches and what (if any) follow-on cleanup is likely required.
3. **Layer 2 — type-quality plan**: per offender file, the count and the suggested next steps. Files are prioritised by Graphify centrality when the graph is present (god nodes first); otherwise by violation count. Per pattern (`@ts-ignore`, `@ts-nocheck`, `Function`, `Object`, wrapper types, empty interfaces), a global removal recommendation.
4. **Layer 3 — type-system-usage plan**: discriminated-union conversion proposals for state-machine sites, branded-type introductions for ID types, `as const` additions for literal-preservation sites, utility-type swaps for hand-rolled mapped types.
5. **Layer 4 — type-safety-at-boundaries plan**: validation-library installation snippet when missing; per IO call site, the validator wrapping snippet; environment-variable configuration-module scaffold.
6. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not edit `tsconfig.json`, install validators, or refactor source.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:13Z",
  "typescriptVersion": "5.4.5",
  "validationLibrary": "zod",
  "withRun": true,
  "thresholds": {
    "anyPerFile": 3,
    "asPerFile": 5,
    "nonNullAssertionPerFile": 3,
    "conditionalTypeDepth": 3
  },
  "snapshot": {
    "tsconfigPath": "tsconfig.json",
    "resolvedCompilerOptions": { "strict": true, "noUncheckedIndexedAccess": false },
    "strictFamily": {
      "strict": true,
      "noUncheckedIndexedAccess": false,
      "exactOptionalPropertyTypes": false,
      "noImplicitReturns": true,
      "noFallthroughCasesInSwitch": true,
      "useUnknownInCatchVariables": true,
      "noImplicitOverride": false,
      "isolatedModules": true
    },
    "typeQuality": {
      "any": 71,
      "unknown": 18,
      "as": 132,
      "asConst": 47,
      "nonNullAssertion": 54,
      "tsIgnore": 3,
      "tsExpectError": 11,
      "tsNocheck": 0
    },
    "topByAny": [
      { "path": "src/lib/api-client.ts", "count": 14 }
    ],
    "topByAs": [
      { "path": "src/features/inbox/transformers.ts", "count": 23 }
    ],
    "topByNonNullAssertion": [
      { "path": "src/legacy/migrationShim.ts", "count": 19 }
    ],
    "projectReferences": 0,
    "tscNoEmitErrors": 7
  },
  "summary": {
    "compilerConfiguration":   { "present": 7, "partial": 2, "missing": 0, "violation": 3 },
    "typeQualityInSource":     { "present": 4, "partial": 2, "missing": 0, "violation": 4 },
    "typeSystemUsage":         { "present": 3, "partial": 3, "missing": 0, "violation": 0 },
    "typeSafetyAtBoundaries":  { "present": 3, "partial": 1, "missing": 0, "violation": 3 }
  },
  "checks": [
    {
      "layer": "compiler-configuration",
      "check": "no-unchecked-indexed-access",
      "status": "violation",
      "evidence": ["tsconfig.json"],
      "expectation": "noUncheckedIndexedAccess is enabled so array[i] is typed as T | undefined.",
      "gap": "Flag missing; runtime undefined values from index access silently slip through the type system.",
      "remediation": "Add \"noUncheckedIndexedAccess\": true to compilerOptions. Expect to add explicit guards or non-null assertions at indexing sites; the audit reports those as new findings on re-run, prioritised by graph centrality."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), the TypeScript version, the resolved compiler options, the detected validation library, applied thresholds, applied filters, and the `withRun` flag plus any captured exit-status from `tsc`.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags, threshold overrides, and the `--with-run` flag are recorded in `metadata.json` so a partial run can be reproduced.
- Run-derived data (`tsc --noEmit` error count) is timestamp-tagged in metadata; staleness is the user's responsibility to manage by re-running.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| `no tsconfig.json detected` | JavaScript-only project, or `tsconfig.json` lives at a non-standard path. | Stop. Inform the user that the skill currently supports TypeScript projects only. |
| TypeScript not installed | `typescript` is not in `dependencies` or `devDependencies`. | Stop with a friendly message recommending `npm install --save-dev typescript`. |
| `tsconfig.json` extends a missing or unparseable file | Broken `extends` chain. | Continue with the configuration that successfully parsed. Record the broken extension in metadata. The "single configuration source" intent is otherwise met. |
| `--with-run` set but `tsc` fails to start | Binary missing, invalid configuration, monorepo path issue. | Record the failure and the captured stderr in metadata. Run-dependent enrichment of the diagnostic snapshot degrades to `partial`. The static analysis still runs. **Recovery:** run `/preflight --audit=typescript --install` to install `typescript`, then re-run with `--with-run`. |
| `--with-run` set and `tsc` exits with type errors | Expected behaviour when there are real type errors. | Parse the diagnostics, record the count in the snapshot, and continue normally. Type errors are reported but do not cause the audit to fail. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The implementation plan loses centrality-based prioritisation; offenders are still surfaced, ordered by per-file violation count. |
| Multiple `tsconfig.*.json` files in a monorepo | Each workspace has its own configuration. | Run the audit at the repository root against the root `tsconfig.json`. When workspace-level configs are detected, the snapshot records the workspace count and the implementation plan recommends per-workspace follow-up audits. |
| Validation library not in `dependencies` but boundary data is validated by a framework layer | The project uses tRPC end-to-end, GraphQL Code Generator outputs, or similar. | The "runtime validation library installed" check reports `partial` rather than `violation`. The IO-boundary checks recognise the framework-level layer and grade individual call sites against it. |

## What this skill explicitly does NOT do

- Run `tsc` with any flag other than `--noEmit`. The audit never invokes the build.
- Modify `tsconfig.json`, source files, or any other project file.
- Install any package (including the validation library, even when missing).
- Open pull requests or commit anything to git.
- Audit JavaScript-only projects.
- Audit type definitions in `node_modules` or third-party `.d.ts` files. The audit grades the project's own source.
- Audit runtime behaviour. The audit checks the *types* the developer wrote; it cannot verify that those types match runtime reality. Runtime validation at boundaries is the prescription for that gap; the audit checks that it's in place, not that it's correct.
- Replace human review of nuanced type design. The audit catches structural patterns; trade-offs like "should this be a union or a discriminated union" still require judgement when the heuristic is ambiguous.
- Audit `@typescript-eslint` rule selection. That is owned by `/linting-audit`; this audit only checks the compiler-level flags. Both surface gaps when relevant.
