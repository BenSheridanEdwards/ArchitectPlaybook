---
name: linting-audit
description: Audit a TypeScript project's lint configuration against an opinionated baseline spanning configuration shape, rule coverage, strictness and enforcement, and suppressions hygiene. Auto-detects ESLint or Biome. Static-first with optional --with-run enrichment. Optionally generates an implementation plan for the gaps.
trigger: /linting-audit
---

# /linting-audit

Audit a TypeScript project's lint configuration against an opinionated baseline organised in four layers — **configuration shape**, **rule coverage**, **strictness and enforcement**, **suppressions hygiene** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

The default mental model is TypeScript and React. The skill auto-detects and supports both ESLint (with the modern flat config) and Biome. It audits the linter actually in use; dual-installation is itself a Layer 1 violation.

## How this differs from `/quality-gates-audit`

`/quality-gates-audit` checks whether linting *runs* in pre-commit, pre-push, and continuous integration — that's a lifecycle question. `/linting-audit` checks whether the linting *itself* is well-configured — coverage, strictness, suppressions hygiene. They complement; they do not duplicate. A project can pass quality-gates-audit (the linter runs at every stage) and still fail linting-audit (the linter is misconfigured to ignore most things), and vice versa.

## Static-first design with optional run enrichment

This skill is read-only. ESLint and Biome are themselves read-only when invoked for linting (no side effects, no file changes), so opting into a real run is safe — just slow on large codebases. Two modes:

- **Static (default).** Read the lint configuration files, `package.json`, source files (for suppression detection), and continuous-integration workflow files. Counts and rule-coverage analysis are based on configuration shape; suppressions are counted from comment text.
- **Static plus opt-in `--with-run`.** Additionally invoke the project's lint command in JSON-reporter mode (`eslint . --format json`, or `biome lint --reporter json`), capture the output, and use the actual warnings and errors to enrich the diagnostic snapshot and a small number of run-required checks.

The skill never invokes any linter with `--fix` or any other mutating flag. Running with `--fix` is the user's job (or a separate fix skill); keeping this audit fully read-only is what makes it safe to run in any working tree at any time.

## Usage

```
/linting-audit                                              # full two-phase static run
/linting-audit --with-run                                   # static plus enrichment from a real lint run
/linting-audit --report-only                                # phase 1 only: write findings, do not ask about a plan
/linting-audit --plan                                       # skip phase 1 if findings already exist; jump to plan generation
/linting-audit --layer=configuration-shape                  # restrict the audit to a single layer (repeatable)
/linting-audit --layer=rule-coverage
/linting-audit --layer=strictness-and-enforcement
/linting-audit --layer=suppressions-hygiene
/linting-audit --include=<check-name>                       # include only the named check (repeatable)
/linting-audit --exclude=<check-name>                       # skip the named check (repeatable)
/linting-audit --threshold-suppressions-per-file=10         # override default 5
/linting-audit --target=<path>                              # operate on this directory instead of cwd (default: cwd)
```

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

When `--target=<path>` is set, the skill operates on that path instead of the current working directory. File reads, file globbing, regex searches, and the `--with-run` linter invocation (`eslint . --format json` or `biome lint --reporter json`) all run scoped to the target. Findings land at `<target>/audits/linting-audit/`. The default is the current working directory. This is the building block that lets `/worktree <name>` create a worktree and audit it from a chat opened elsewhere — all in one chat.

**💡 Pro tip**: Use `/worktree linting` to run this against a Git worktree (creates the worktree and runs the audit in this same chat). Useful for branch isolation and for running multiple audits in true parallel across separate chats.

**💡 Pro tip**: Run `/preflight --audit=linting` first to detect — and optionally install — the development dependency that makes `--with-run` useful (`eslint` or `@biomejs/biome`, depending on which linter your project uses). Skip if you already know the tooling is wired up.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the codebase shows mixed adherence to a soft check.
- **missing** — a structural prerequisite is absent (no linter installed, for example).
- **violation** — the audit identified concrete configuration that breaks the invariant.

Layer 0 is informational only and has no status.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Detected linter and version (ESLint 9.x, Biome 1.x, or none).
- Configuration files and their format (flat config `eslint.config.{js,ts,mjs}`, legacy `.eslintrc.*`, `biome.json`/`biome.jsonc`).
- Approximate enabled-rule count: sum of rules introduced by `extends` plus rules explicitly set in the configuration.
- Source-file count in scope, post-ignore patterns.
- Total suppression count: `eslint-disable*` and `biome-ignore` comments across the codebase.
- Top 10 most-suppressed rules. Sourced from the actual lint output when running with `--with-run`; otherwise inferred by parsing the rule names from suppression comments.
- Top 10 files with the most suppressions, with their suppression count.
- Total warnings and errors from the actual lint run (only when `--with-run`).

### Layer 1 — Configuration shape

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Exactly one linter installed | Either ESLint or Biome, not both. | Both `eslint` and `@biomejs/biome` present in `package.json` dependencies. |
| Modern configuration format | ESLint uses flat config (`eslint.config.{js,ts,mjs}`); Biome uses `biome.json` or `biome.jsonc`. | ESLint legacy format (`.eslintrc.*`) in use, deprecated as of ESLint 9. |
| Single configuration source | Exactly one configuration file at the repository root (or workspace package root). | Multiple top-level configuration files, or both flat and legacy formats present simultaneously. |
| TypeScript parser configured (ESLint only) | The configuration registers `@typescript-eslint/parser` and either `parserOptions.project` or `parserOptions.projectService` so type-aware rules can run. Biome's TypeScript support is implicit. | ESLint configuration without a TypeScript parser despite `tsconfig.json` being present. |
| Declarative configuration | The configuration does not require side-effectful evaluation: no environment-dependent rule selection, no asynchronous configuration loaders. Soft check — reported as `partial`. | Configuration that branches on `process.env` in non-trivial ways. |

### Layer 2 — Rule coverage

The audit dispatches based on the detected linter. Whichever path runs, every check below is scoped to that linter.

#### When ESLint is detected

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Base recommended set extended | The base recommended ruleset is extended (in flat config: `js.configs.recommended` from `@eslint/js`; in legacy: `eslint:recommended`). | No base ruleset extended. |
| `@typescript-eslint` recommended-type-checked or stronger | At least `recommended-type-checked` is extended; `strict-type-checked` is preferred. The plain `recommended` set is reported as `partial` because it skips the type-aware rules. | No `@typescript-eslint` configuration; or `recommended` only (reported as `partial`). |
| `eslint-plugin-react/recommended` (React only) | When React is detected, the React recommended config is extended. | React project without the plugin configured. |
| `eslint-plugin-react-hooks/recommended` (React only) | When React is detected, the hooks plugin's recommended rules are enabled. | React project without it. |
| `eslint-plugin-jsx-a11y/recommended` (React only) | When React is detected, the accessibility plugin's recommended rules are enabled. (Surfaces the gap here as well as in `/accessibility-audit`, so a single fix passes both.) | React project without it. |
| `eslint-plugin-import` configured | The import plugin is configured with the TypeScript resolver (`eslint-import-resolver-typescript`) plus rules for unresolved imports, import order, and no-cycle. | Plugin missing, or installed without the TypeScript resolver. |
| `eslint-config-prettier` last in extends | When Prettier is in the project, `eslint-config-prettier` is the last entry in the extends list so it disables formatting rules that would conflict with Prettier. | Prettier installed but config not added; or added but not last. |
| Node best-practices plugin | When the project has Node-only entry points (server code, build scripts), `eslint-plugin-n`'s recommended rules are enabled. Skipped silently for browser-only frontends. | Node entry points present without the plugin. |

#### When Biome is detected

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Linter enabled | `linter.enabled` is `true` in `biome.json`. | Linter disabled. |
| Recommended rules enabled | `linter.rules.recommended` is `true`. | Recommended rules off. |
| Domain rule sets enabled | Each of `correctness`, `suspicious`, `complexity`, `performance`, `security`, `style`, plus `a11y` (when React detected) has `recommended: true`, or rule-by-rule coverage of equivalent strength. | Any of the above set off without rule-by-rule replacement. |
| Type-aware rules enabled | The configuration does not opt out of TypeScript-specific rules. | `javascript.parser.disabled` or analogues set in a way that disables TypeScript-aware checks. |

### Layer 3 — Strictness and enforcement

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Lint script in `package.json` | A `lint` script exists and invokes the detected linter against the project. | No `lint` script, or the script invokes a different tool. |
| Continuous integration enforces zero warnings | The continuous-integration lint step uses `--max-warnings=0` for ESLint, or `--error-on-warnings` for Biome. | Workflow runs lint without the zero-warnings flag, or lint failures don't fail the build. |
| Type-aware rules actually enabled (ESLint only) | When `@typescript-eslint/parser` is configured, `parserOptions.project` (or the flat-config `parserOptions.projectService`) is set so type-aware rules can run. | Parser configured but the project pointer is missing — type-aware rules silently no-op. |
| No globally disabled rules without justification | The configuration does not turn off rules from extended recommended sets globally without an inline comment explaining why. | A rule overridden to `'off'` (or `0`) at the top level of the configuration without a comment. |
| Lint errors fail the build | Continuous integration treats lint errors as build failures: no `|| true`, no `continue-on-error: true` on the lint step. | Workflow patterns that swallow lint failures. |
| Editor integration available | A workspace-level recommendation exists for the linter's editor extension (`.vscode/extensions.json` for VS Code, equivalent for other editors), so the linter runs locally in the same configuration as continuous integration. Soft check — reported as `partial`. | No editor-recommendation configuration. |

### Layer 4 — Suppressions hygiene

| Check | Expectation | Violation signal |
| --- | --- | --- |
| No blanket file-level disables without justification | `/* eslint-disable */` (no rule list) at the top of a file is reported as `violation` unless the same line (or the line above) carries a free-text justification. Equivalent: `// biome-ignore-all` at the top of a file in Biome. | Blanket disable with no following justification. |
| Each disable specifies rules | `eslint-disable-next-line` and inline disable comments specify which rules they disable, never blanket. Same expectation for Biome's `biome-ignore` comments. | Bare `eslint-disable-next-line` (or analogous) with no rule names. |
| Each suppression carries a justification | Every suppression comment is followed by free-text justification (on the same line or the line above). Soft check — reported as `partial` when adherence is mixed. | Suppression comments with no surrounding explanation. |
| `ignores`/`.eslintignore` does not exclude production source | The ignore patterns do not exclude entire production source directories, individual feature folders, or `*.ts`/`*.tsx` blanketly. | Patterns that ignore production code under `src/` (or the framework equivalent). |
| No stale ignore entries | Every entry in the ignore list resolves to at least one existing file or directory. | Entries pointing at paths that no longer exist. |
| Suppression density is reasonable | Files contain at most the threshold suppressions each (default 5; tunable via `--threshold-suppressions-per-file`). Soft check — reported as `partial` above the threshold. | Files exceeding the threshold. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the implementation plan can rank suppression-heavy files by graph centrality — a suppression in a god node is more consequential than one in a leaf utility, and the plan recommends those for cleanup first. The audit still runs in full when the graph is absent.
2. **Confirms a TypeScript project.** Detects `package.json`, `tsconfig.json`, and a TypeScript dependency. If absent, the skill stops and tells the user it currently supports TypeScript projects only.
3. **Detects the linter.** Looks for `eslint` and/or `@biomejs/biome` in `package.json` dependencies, then for the configuration files of each. When neither is detected, the audit stops with a clear message: a project with no linter is a `/quality-gates-audit` problem first.
4. **Detects React** for the React-conditional rule-coverage checks.
5. **When `--with-run` is set**, invokes the detected linter in JSON-reporter mode, captures the output, and parses it. If the lint command fails to start (binary missing, configuration syntactically invalid), records the failure and degrades run-dependent checks to `partial`.
6. **Writes Layer 0 — the diagnostic snapshot** to `audits/linting-audit/snapshot.md` and prepends the same content to `findings.md`.
7. **Walks each check in the active layer list**, dispatching to the ESLint or Biome variant where the layer 2 checks differ. Records a status, evidence, and (where relevant) sample file references per check.
8. **Writes phase 1 outputs** to `audits/linting-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), detected linter and version, React-detected flag, applied thresholds, applied filters, run output presence.
9. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the linting gaps? (yes/no)"

   On `yes`, writes `audits/linting-audit/implementation-plan.md` describing exactly which configuration entries to add or change, which plugins to install, which suppressions to clean up first (graph-prioritised when the graph is present), and which continuous-integration steps to tighten — ordered by layer. The plan does not modify any project files.

   On `no`, exits cleanly. The user can re-run with `--plan` later to skip phase 1.

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "linting-audit: no package.json detected. This skill currently supports TypeScript projects only."; exit 1; }
test -f tsconfig.json || { echo "linting-audit: no tsconfig.json detected. This skill currently supports TypeScript projects only."; exit 1; }
```

### Step 2 — Detect the linter

Read `package.json` dependencies:

- `eslint` present → ESLint path.
- `@biomejs/biome` present → Biome path.
- Both present → record both, run the appropriate path for whichever is configured (presence of `eslint.config.*`/`.eslintrc.*` vs `biome.json`), and surface the dual-installation as a `violation` on the layer 1 "exactly one linter installed" check.
- Neither present → stop with a clear message:

  > `linting-audit` requires a linter (ESLint or Biome). No linter detected. Install one and configure it before running this audit; `/quality-gates-audit` will surface the gap as well.

### Step 3 — Detect React

Look for `react` in dependencies (directly or via Next.js, Remix, etc.). Determines whether the React-specific rule-coverage checks run.

### Step 4 — Optionally run the linter

When `--with-run` is set, invoke the detected linter:

- ESLint: `npx eslint . --format json` (capture stdout, parse JSON).
- Biome: `npx biome lint --reporter json .` (capture stdout, parse JSON).

If the command exits non-zero **because of lint findings**, that is the expected output and the JSON is still parseable. If the command exits non-zero **because of a configuration error or because the linter binary is missing**, record the failure, print to the chat and prepend to `findings.md`: "`--with-run` was requested but the linter failed to start. Run `/preflight --audit=linting --install` to install `eslint` or `@biomejs/biome`, then re-run this audit. The static analysis has been completed; only the run-dependent enrichment degraded to `partial`." Record `recoveryHint: "/preflight --audit=linting --install"` on each run-dependent check that degraded in `findings.json`. Degrade run-dependent diagnostic snapshot fields (top-suppressed rules, total warnings/errors) and any run-required check to `partial`.

### Step 5 — Build the diagnostic snapshot

Compute the items listed in Layer 0. Suppression detection scans `.ts`, `.tsx`, `.js`, `.jsx`, `.mjs`, and `.cjs` files for `eslint-disable*` and `biome-ignore` comment patterns. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 6 — Resolve each check

For each check in the active layer list, walk its detection logic. Layer 2 dispatches to the ESLint or Biome variant based on the detected linter. Record evidence and sample file references; cap suppression-heavy file lists at ten samples plus a total count.

### Step 7 — Write phase 1 outputs

Create `audits/linting-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 8 — Print the chat summary and offer phase 2

Print the snapshot's headline numbers (linter and version, configuration format, total suppressions, lint warnings and errors when run) plus a short table mirroring the four layers with counts of present, partial, missing, and violation per layer. Highlight the top three violations and the three suppression-heaviest files. Then ask the single yes-or-no question. Do not proceed to phase 2 without an explicit affirmative.

### Step 9 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, detected linter, React-detected flag, timestamp, total counts per layer.
2. **Layer 1 — configuration shape plan**: linter installation/removal commands when dual-installed; migration snippet from legacy to flat config; TypeScript parser wiring snippet.
3. **Layer 2 — rule coverage plan**: per missing rule set, the install command and the configuration entry to add. Recommendation order respects layer 1 dependencies (parser before type-aware rules, etc.).
4. **Layer 3 — strictness and enforcement plan**: continuous-integration step snippets, `package.json` script edits, type-aware-rule activation snippet.
5. **Layer 4 — suppressions hygiene plan**: list of files to clean up, prioritised by graph centrality when the Graphify graph is present (god nodes first), then by suppression count. Per file, suggested next steps (annotate justifications, narrow blanket disables, or fix the underlying issue).
6. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not install plugins, modify configuration, or add suppressions.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:09Z",
  "linter": { "name": "eslint", "version": "9.10.0", "configFile": "eslint.config.mjs", "configFormat": "flat" },
  "reactDetected": true,
  "withRun": true,
  "thresholds": { "suppressionsPerFile": 5 },
  "snapshot": {
    "enabledRuleCountApproximate": 287,
    "filesInScope": 612,
    "totalSuppressions": 41,
    "topSuppressedRules": [
      { "rule": "@typescript-eslint/no-explicit-any", "count": 14 }
    ],
    "topSuppressedFiles": [
      { "path": "src/legacy/migrationShim.ts", "count": 9 }
    ],
    "lintRun": { "warnings": 73, "errors": 0 }
  },
  "summary": {
    "configurationShape":      { "present": 4, "partial": 1, "missing": 0, "violation": 0 },
    "ruleCoverage":            { "present": 5, "partial": 1, "missing": 1, "violation": 1 },
    "strictnessAndEnforcement":{ "present": 4, "partial": 1, "missing": 0, "violation": 1 },
    "suppressionsHygiene":     { "present": 3, "partial": 2, "missing": 0, "violation": 1 }
  },
  "checks": [
    {
      "layer": "rule-coverage",
      "check": "eslint-config-prettier-last-in-extends",
      "status": "violation",
      "evidence": ["eslint.config.mjs"],
      "expectation": "When Prettier is in use, eslint-config-prettier is the last entry in the extends list.",
      "gap": "Prettier installed; eslint-config-prettier appears in extends but is followed by 2 entries that re-enable formatting rules.",
      "remediation": "Move eslint-config-prettier to the last position in the extends list so it disables formatting rules introduced by earlier entries."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), the detected linter and version, the React-detected flag, applied thresholds, applied filters, and the `withRun` flag plus any captured exit-status from the lint command.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags, threshold overrides, and the `--with-run` flag are recorded in `metadata.json` so a partial run can be reproduced.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| `no tsconfig.json detected` | JavaScript-only project. | Stop. Inform the user that the skill currently supports TypeScript projects only. |
| Neither ESLint nor Biome installed | The project has no linter. | Stop with a friendly message recommending `/quality-gates-audit` to surface the broader gap, and `/install` of either ESLint or Biome before re-running. |
| Both ESLint and Biome installed | Mid-migration or accidental dual-install. | Continue. Run the audit against whichever has a configuration file. Surface dual-installation as a `violation` on the layer 1 "exactly one linter installed" check. |
| `--with-run` set but lint command fails to start | Binary missing, configuration invalid, monorepo path resolution issue. | Record the failure and the captured stderr in metadata. Run-dependent checks degrade to `partial`. Continue with the static analysis. **Recovery:** run `/preflight --audit=linting --install` to install `eslint` or `@biomejs/biome` (whichever your project already uses), then re-run with `--with-run`. |
| ESLint legacy configuration format | Project still on `.eslintrc.*`. | The layer 1 modern-format check reports `violation`. Continue auditing — the legacy format is still parseable; the implementation plan includes a migration snippet to flat config. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The implementation plan's suppression-heavy-file ranking falls back to suppression count alone (no centrality weighting). |
| Multiple workspaces in a monorepo | Each workspace has its own configuration. | Run the audit at the repository root. When workspace configurations are detected, the skill records the workspace count in the snapshot and recommends per-workspace follow-up audits. |

## What this skill explicitly does NOT do

- Run any linter with `--fix` or any other mutating flag.
- Install, remove, or upgrade any linting plugin.
- Modify lint configuration, `package.json`, suppression comments, ignore files, or continuous-integration workflows.
- Open pull requests or commit anything to git.
- Audit projects without a linter installed. That is `/quality-gates-audit`'s job (it surfaces the missing-lint-step gap), not this skill's.
- Audit JavaScript-only projects.
- Audit linters other than ESLint and Biome. Other linters (rome, dprint as a linter, deno lint, etc.) are out of scope for v1.
- Replace human review of suppression justifications. The audit verifies the justification *exists*; whether it is a *good* justification is a code-review concern.
