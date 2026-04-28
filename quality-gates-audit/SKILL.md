---
name: quality-gates-audit
description: Audit pre-commit, pre-push, and CI/CD quality gates against an opinionated baseline. Reports present/missing/misconfigured gates and optionally generates an implementation plan.
trigger: /quality-gates-audit
---

# /quality-gates-audit

Compare the current project against an opinionated baseline of quality gates organised by lifecycle stage (pre-commit, pre-push, continuous integration), report what is present, missing, or misconfigured, and then offer to generate an implementation plan for closing the gaps. The skill is read-only — it never modifies the project. The implementation plan is a Markdown document, not an applied change set.

The default mental model is a TypeScript and React frontend project. The detection logic is structured per ecosystem so it can be extended later, but the baked-in baseline below assumes Node.js and the npm-style tooling family.

## Usage

```
/quality-gates-audit                            # default: concise Top 5 + full report saved + ask about plan
/quality-gates-audit --learn                    # mid-level engineer teaching mode (detailed explanations + file/line examples)
/quality-gates-audit --teach                    # alias for --learn
```

**💡 Pro tip**: Use `/worktree quality-gates` to run this in an isolated worktree.

This skill never accepts `--apply`. Applying a plan is a separate concern — the user reviews the generated `implementation-plan.md` and either implements it manually or runs a fix-oriented skill against it.

## The opinionated baseline

The skill audits against this exact list. A gate is **present** if every detection signal listed for it resolves; **misconfigured** if some but not all signals resolve; **missing** if none resolve.

### Stage 1 — Pre-commit (fast, runs on every commit attempt)

| Gate | Expectation | Primary detection signals |
| --- | --- | --- |
| Hook runner installed | Husky or Lefthook is configured for the repository. | `.husky/` directory with hooks; or `lefthook.yml` / `lefthook.yaml`. |
| Staged-file formatter | Prettier or Biome formats staged files via lint-staged. | `package.json` devDependency on `prettier` or `@biomejs/biome`; `lint-staged` configuration block; matching entries inside `.husky/pre-commit` or `lefthook.yml`. |
| Staged-file linter | ESLint or Biome lints staged files via lint-staged, with `--fix` allowed. | `eslint` or `@biomejs/biome` devDependency; `lint-staged` block invoking it; matching entries in the pre-commit hook script. |
| Type check on staged files | `tsc --noEmit` (or equivalent) runs against staged TypeScript files. | `typescript` devDependency; `tsconfig.json`; lint-staged or hook script entry invoking `tsc --noEmit` (often via `tsc-files`). |
| Commit-message lint | commitlint enforces Conventional Commits on the subject line. | `@commitlint/cli` and `@commitlint/config-conventional` devDependencies; `commitlint.config.*` configuration file; `.husky/commit-msg` hook invoking commitlint. |
| Secret scan on staged content | gitleaks (`protect`) or detect-secrets runs against staged content before commit. | `.gitleaks.toml` or `.pre-commit-config.yaml` referencing detect-secrets; matching entry in the pre-commit hook script. |

### Stage 2 — Pre-push (slower, runs once before pushing)

| Gate | Expectation | Primary detection signals |
| --- | --- | --- |
| Pre-push hook configured | A pre-push hook exists and is wired into the hook runner. | `.husky/pre-push`; or `pre-push:` block in `lefthook.yml`. |
| Full type check | `tsc --noEmit` runs over the entire project. | Pre-push hook script invokes `tsc --noEmit` or a `package.json` script that does. |
| Full lint | The project's lint command runs the whole codebase, fails on warnings. | Pre-push hook invokes `eslint .` (or Biome equivalent) with no `--fix` and a non-zero exit on warnings (`--max-warnings=0`). |
| Unit tests | Vitest or Jest runs the unit-test suite to completion. | Pre-push hook invokes `vitest run` or `jest --ci`; matching `package.json` script. |
| Build smoke | The production build command runs and exits cleanly. | Pre-push hook invokes the framework build (`next build`, `vite build`, `react-scripts build`, etc.). |

### Stage 3 — Continuous Integration (comprehensive, runs on every pull request and merge)

| Gate | Expectation | Primary detection signals |
| --- | --- | --- |
| Continuous-integration workflow file present | At least one workflow definition exists. | `.github/workflows/*.yml`, `.gitlab-ci.yml`, `.circleci/config.yml`, or equivalent. |
| All pre-push gates re-run on a clean checkout | The workflow installs dependencies fresh and runs type-check, lint, unit tests, and build. | Workflow file contains steps invoking the same scripts as the pre-push hook. |
| End-to-end tests | Playwright or Cypress runs against a build of the application. | `@playwright/test` or `cypress` devDependency; workflow step invoking `playwright test` or `cypress run`. |
| Coverage threshold enforced | The test runner is configured with a coverage threshold and the workflow fails when it is not met. | `coverage` configuration in `vitest.config.*` or `jest.config.*` with `thresholds`; workflow step invoking the coverage run. |
| Bundle-size budget | A bundle-size check runs and fails when the budget is exceeded. | `size-limit`, `bundlewatch`, `@next/bundle-analyzer` plus a budget check, or equivalent; workflow step running it. |
| Accessibility checks | Automated accessibility checks run against components or pages. | `@axe-core/*`, `pa11y`, or Storybook `@storybook/addon-a11y`; workflow step invoking the check. |
| Dependency vulnerability scan | A vulnerability scanner runs against the lockfile. | `npm audit` / `pnpm audit` / `yarn npm audit` step in the workflow; or `Snyk`, `osv-scanner`, or Dependabot configuration (`.github/dependabot.yml`). |
| License compliance check | A license check runs and enforces an allow-list or deny-list. | `license-checker`, `license-compliance`, or `@inquirer/license-checker-rs`; workflow step invoking it with allow-list or deny-list. |
| Lighthouse continuous integration | Lighthouse CI runs against a deployed or built version of the application and asserts thresholds. | `@lhci/cli` devDependency; `lighthouserc.*` configuration file; workflow step invoking `lhci autorun`. Recommended for user-facing applications; flagged but not always required. |

## What this skill does

1. **Reads the knowledge graph first.** If `graphify-out/graph.json` exists, read `graphify-out/GRAPH_REPORT.md` to orient before searching raw files. The PreToolUse hook installed by `/pre-audit-setup` reminds you of this on every Glob and Grep — respect it.
2. **Detects ecosystem.** Confirms a Node.js project by checking for `package.json`. If absent, the skill stops and tells the user it currently supports Node.js only.
3. **Enumerates gates.** Walks the baseline above, applying any `--stage`, `--include`, or `--exclude` filters.
4. **Resolves each gate's status** by inspecting the project for the signals listed in the baseline tables. Never executes any gate — this is a static audit.
5. **Writes phase 1 outputs** to `.architect-audits/quality-gates-audit/`:
   - `findings.md` — grouped by stage, one section per gate.
   - `findings.json` — machine-readable, see schema below.
   - `metadata.json` — skill version, run timestamp, graphify revision hash, ecosystem.
6. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the missing or misconfigured gates? (yes/no)"

   On `yes`, writes `.architect-audits/quality-gates-audit/implementation-plan.md` describing exactly which packages to install, which configuration files to add, and which hook or workflow entries to wire up — ordered by stage. The plan does not modify any project files; it is a checklist for the user.

   On `no`, exits cleanly. 

## Implementation steps

### Step 1 — Confirm the working directory and graph

```bash
test -f package.json || { echo "quality-gates-audit: no package.json detected. This skill currently supports Node.js projects only."; exit 1; }
test -f graphify-out/graph.json && echo "graphify: knowledge graph present" || echo "graphify: knowledge graph missing — run /pre-audit-setup first for richer context"
```

The skill does not require the knowledge graph, but the audit is more accurate when it exists. If absent, recommend running `/pre-audit-setup` and continue with reduced confidence.

### Step 2 — Detect the package manager and hook runner

Read `package.json` and check for the lockfile (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, `bun.lockb`). Record the manager — it will be referenced in the generated implementation plan.

Detect the hook runner:
- Husky: presence of `.husky/` directory.
- Lefthook: presence of `lefthook.yml` / `lefthook.yaml`.
- Neither: the pre-commit and pre-push stages will mostly resolve as missing.

### Step 3 — Resolve each gate

For each gate in the active stage list, walk its detection signals:

- **All signals resolve →** `status: "present"`.
- **Some signals resolve →** `status: "misconfigured"`. Record exactly which signals matched and which did not.
- **No signals resolve →** `status: "missing"`.

Capture every matching path or configuration key in the `evidence` array so the report is auditable. Never guess — when a signal is ambiguous, prefer `misconfigured` over `present`.

### Step 4 — Write phase 1 outputs

Create `.architect-audits/quality-gates-audit/` if it does not exist. Write the three files:

```
.architect-audits/quality-gates-audit/
  findings.md
  findings.json
  metadata.json
```

If a previous run exists, overwrite the three files. Phase 2's `implementation-plan.md` is preserved unless the user agrees to regenerate it.

### Step 5 — Print the concise chat summary and offer phase 2

Print a human-first, scannable summary in the chat. Do not print the full layered findings — those are written to disk in Step 4. The chat output has exactly this shape:

1. **Short header** — audit name, timestamp, and a one-line summary of the codebase state.
2. **Top 5 Highest-Leverage Recommendations** — ordered by architectural principles: test philosophy, maintainability, risk reduction, velocity, long-term health. For fewer than five findings, print what exists. For each recommendation (numbered 1–5):
   - **Title** (one clear line).
   - **Why it matters** (explain the principle in 1–2 sentences).
   - **Real consequences if ignored** (honest downside for the team or project).
   - **Smallest high-leverage fix** (exact next step, effort level, and which files to touch).
   - At the end, add a lettered sub-list of concrete actions if useful (e.g. 2a, 2b) so the user can reply with "2b" or "1 and 3" to trigger implementation.
3. **Bottom line**: `Full detailed audit report (layered findings, snapshot, metadata, implementation plan) → .architect-audits/quality-gates-audit/findings.md`

When `--learning` or `--teach` is set, expand each recommendation into mid-level engineer teaching mode:
- For every item, explain as if teaching a mid-level engineer, pointing to specific files and line numbers from the current codebase.
- Use educational language: "Here's why this pattern bites teams in the long run…", "This is the exact mistake I see in most codebases at your stage…", "The fix is small but pays off huge because…".
- Include a short "What you'll learn from fixing this" section for each recommendation.
- Keep the numbered/lettered structure so the user can still reply with "2b" or "1 and 3".
- End with the same bottom-line link to the full report.

After printing, ask the single yes-or-no question: *"Generate an implementation plan for the gaps identified above? (yes/no)"* Do not proceed to phase 2 without an explicit affirmative.

### Step 6 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, timestamp, list of detected gaps grouped by stage.
2. **Per-stage plan**, ordered pre-commit → pre-push → continuous integration. For each missing or misconfigured gate include:
   - The package(s) to install, with the exact command for the detected package manager.
   - The configuration file(s) to create or modify, with full content snippets.
   - The hook or workflow entry to add, with full content snippets.
   - A one-line note on why this gate matters (so the user can decide whether to skip it).
3. **Closing checklist** — a flat checkbox list that mirrors the gaps, suitable for pasting into a pull request description.

The plan is descriptive, not executable. It does not run the install commands and does not create the configuration files.

## Findings file shape

`findings.json` is an object with this top-level structure:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:09Z",
  "ecosystem": "node",
  "packageManager": "pnpm",
  "hookRunner": "husky",
  "summary": {
    "preCommit":              { "present": 4, "misconfigured": 1, "missing": 1 },
    "prePush":                { "present": 2, "misconfigured": 0, "missing": 3 },
    "continuousIntegration":  { "present": 4, "misconfigured": 1, "missing": 4 }
  },
  "gates": [
    {
      "stage": "pre-commit",
      "gate": "commit-message-lint",
      "status": "missing",
      "evidence": [],
      "expectation": "commitlint with @commitlint/config-conventional and a .husky/commit-msg hook",
      "gap": "no commitlint configuration or commit-msg hook detected",
      "remediation": "install @commitlint/cli and @commitlint/config-conventional, add commitlint.config.cjs, add .husky/commit-msg invoking npx --no -- commitlint --edit $1"
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, grouped by stage with a one-line status per gate and an "Evidence / Gap" sub-block when relevant.

`metadata.json` is small and stable:

```json
{
  "skillName": "quality-gates-audit",
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:09Z",
  "graphifyRevision": "<hash from graphify-out/metadata if present>",
  "filtersApplied": { "stages": ["pre-commit", "pre-push", "continuous-integration"], "include": [], "exclude": [] }
}
```

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, and `metadata.json` in place. The previous report is not preserved — the audit is intended to reflect current state.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it. This is to avoid losing notes the user may have added.
- Filters (`--stage`, `--include`, `--exclude`) are recorded in `metadata.json` so a partial run can be reproduced.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is being run outside a Node.js project root. | Change directory into the project root and re-run. |
| Workflow file present but cannot be parsed | Malformed YAML, or a templating system the skill does not understand. | Treat every gate that depends on workflow detection as `misconfigured` and record the parse error in the gate's evidence field. Do not crash. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue, but tag every gate's evidence with a `noGraphify: true` flag so the user knows the audit ran with reduced context. |
| Monorepo with multiple workspaces | One `package.json` at the root plus several inside `packages/*` or `apps/*`. | Resolve the root manager and root-level gates as usual. Recommend a follow-up audit per workspace and surface the recommendation in `findings.md`. |
| Conflicting tools (both ESLint and Biome present) | Both are configured. | Mark the gate as `misconfigured` with a gap that explains the conflict. The implementation plan should propose picking one. |

## What this skill explicitly does NOT do

- Execute any gate. The audit is fully static.
- Install any package or dependency.
- Create, modify, or delete any configuration file outside `.architect-audits/quality-gates-audit/`.
- Modify hooks, workflow files, or `package.json`.
- Open pull requests or commit anything to git.
- Audit any ecosystem other than Node.js. The opinionated baseline above is currently Node.js-only by design.
- Audit individual workspaces in a monorepo. The skill audits the repository root only and recommends per-workspace follow-up.
