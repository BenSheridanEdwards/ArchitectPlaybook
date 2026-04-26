# Architect Playbook

A self-contained, self-improving collection of Claude Code slash-command skills for auditing any codebase. Walk onto a project, install the skills, run multiple audits in parallel from separate chat sessions, fix what they find, review the fixes in a Git worktree, and let `/self-system-heal` patch the audits themselves whenever a review surfaces a gap.

## The workflow

This is the headline feature — the playbook is designed around one specific way of working.

1. **Install the skills** into the target project.
   ```
   /install-skills-locally
   ```
   Or once, machine-wide:
   ```
   /install-skills-globally
   ```
2. **Prepare the project** for audits.
   ```
   /pre-audit-setup
   ```
   Verifies graphify is installed, builds the project knowledge graph, and merges the graphify-aware PreToolUse hook into `.claude/settings.json`.
3. **Open one Claude Code chat per audit and run the audit slash command.** Each audit writes its findings to `audits/<audit-name>/findings.md` (and `findings.json`, and `metadata.json`).
   ```
   /security-audit
   /performance-audit
   /accessibility-audit
   ...
   ```
   Run as many as you want in parallel — they are independent chat windows.
4. **Fix the findings in the same chat that produced them.** Ask Claude to read `audits/<audit-name>/findings.md` and implement the fixes. Stay in that chat.
5. **Open a new chat against a Git worktree** containing the fix branch and run the matching review.
   ```
   git worktree add ../security-review security-review
   cd ../security-review
   # open a fresh Claude Code chat here
   /security-audit              # re-run as a review pass
   ```
6. **If the review surfaces something the audit missed, evolve the audit.**
   ```
   /self-system-heal
   ```
   This patches the originating audit's own `SKILL.md` so the same class of issue is caught next time. The playbook gets sharper every time you use it.

## Quick start

```bash
# 1. Get the playbook
git clone <this-repository> ~/architect-playbook

# 2. Install once, machine-wide
cd ~/architect-playbook
# in a Claude Code chat:
/install-skills-globally

# 3. Walk onto any project
cd ~/Coding/some-project
# in a fresh Claude Code chat:
/pre-audit-setup
/security-audit
```

## Running audits in parallel with Git worktrees

Audits do not modify the codebase, so you can run several at once against the main checkout. Reviews and fixes are easier to keep separate when each lives in its own worktree.

```bash
git worktree add ../security-audit       -b security-audit
git worktree add ../performance-audit    -b performance-audit
git worktree add ../accessibility-audit  -b accessibility-audit
git worktree add ../dependency-audit     -b dependency-audit
```

Open one Claude Code chat per worktree, run the matching audit slash command in each, and let them work in parallel. When you are ready to apply fixes, do it in the same chat that produced the audit (each chat has the most context about its own findings). When the fixes are in, spin up a fresh chat against the worktree to run the review pass.

## The findings-file contract

Audits, fixes, and reviews each run in different chat sessions, so they cannot share in-memory state. The protocol between them is a deterministic on-disk shape:

```
audits/
  <audit-name>/
    findings.md       human-readable report you can read at a glance
    findings.json     machine-readable list of issues for downstream skills
    metadata.json     skill version, run timestamp, graphify revision hash
```

- A **fix** skill reads `findings.json` and produces code changes.
- A **review** skill reads the audit findings plus the resulting diff and produces its own gap report.
- `/self-system-heal` reads the review's gap report and rewrites the originating audit's `SKILL.md`.

## The full skill list

| Trigger | Status | Purpose |
| --- | --- | --- |
| [`/install-skills-locally`](install-skills-locally/SKILL.md)   | ready | Copy every playbook skill into the current project's `.claude/skills/`. |
| [`/install-skills-globally`](install-skills-globally/SKILL.md) | ready | Copy every playbook skill into `~/.claude/skills/` for machine-wide use. |
| [`/pre-audit-setup`](pre-audit-setup/SKILL.md)                 | ready | Verify graphify, build the project knowledge graph, merge the PreToolUse hook. |
| [`/quality-gates-audit`](quality-gates-audit/SKILL.md)         | ready | Audit the project against an opinionated baseline of pre-commit, pre-push, and continuous integration gates; optionally generate an implementation plan for the gaps. |
| [`/security-audit`](security-audit/SKILL.md)                   | ready | Audit a TypeScript and React frontend across authentication and sessions, input handling and XSS prevention, transport and headers and cookies, and secrets/data/third-party integrations; **frontend-only in v1**; static-first with optional `--with-scan` enrichment from installed security scanners; optionally generates an implementation plan ordered by severity. |
| [`/accessibility-audit`](accessibility-audit/SKILL.md)         | ready | Audit a TypeScript and React frontend against an opinionated WCAG 2.2 AA baseline across tooling, component patterns, and application shell; optionally generate an implementation plan for the gaps. |
| [`/dependency-audit`](dependency-audit/SKILL.md)               | ready | Audit a Node.js project's dependency tree across security, health, compliance, and hygiene; static-first with optional `--with-network` enrichment for vulnerability, outdated, and abandonment data; surfaces license signals without enforcing policy; optionally generates an implementation plan. |
| [`/performance-audit`](performance-audit/SKILL.md)             | ready | Audit a TypeScript and React frontend's runtime performance across render performance, network and data, assets/media/Core Web Vitals, and main-thread work and measurement; static-first with optional `--with-lighthouse-results` enrichment from existing Lighthouse JSON; never runs Lighthouse itself; implementation plan ordered by Core Web Vitals impact. |
| [`/architecture-audit`](architecture-audit/SKILL.md)           | ready | Audit a TypeScript codebase against opinionated invariants for module boundaries, coupling, state and data flow, and convention adherence using the Graphify knowledge graph; produces a diagnostic snapshot and a violations report; optionally generates an implementation plan. |
| [`/testing-audit`](testing-audit/SKILL.md)                     | stub  | Audit coverage gaps, weak assertions, flaky patterns, missing integration coverage. |
| [`/react-audit`](react-audit/SKILL.md)                         | ready | Audit a TypeScript and React project for idiomatic React across hooks correctness, component design, state management, and React 18/19 idioms; static-only by design (use `/linting-audit --with-run` for fresh hook-violation capture); Layer 4 auto-skipped on React below 18; class components reported as `partial` rather than `violation`; optionally generates an implementation plan. |
| [`/linting-audit`](linting-audit/SKILL.md)                     | ready | Audit a TypeScript project's lint configuration across configuration shape, rule coverage, strictness and enforcement, and suppressions hygiene; auto-detects ESLint or Biome (dual-install is itself a violation); static-first with optional `--with-run` enrichment from a real lint pass; optionally generates an implementation plan. |
| [`/typescript-audit`](typescript-audit/SKILL.md)               | stub  | Audit TypeScript configuration and code: unsafe casts, weak types, strictness. |
| [`/bundle-build-audit`](bundle-build-audit/SKILL.md)           | ready | Audit a TypeScript frontend's build pipeline and bundle output across configuration, composition and size, asset hygiene, and build performance; static-first with optional `--with-stats` enrichment from existing bundle stats artefacts; optionally generates an implementation plan. |
| [`/error-handling-audit`](error-handling-audit/SKILL.md)       | ready | Audit a TypeScript codebase's error-handling discipline across throw and catch hygiene, async and network error handling, React error boundaries, and logging and observability; static-only by design with no opt-in modes; React layer auto-skipped when not detected; optionally generates an implementation plan. |
| [`/documentation-audit`](documentation-audit/SKILL.md)         | stub  | Audit project documentation for accuracy, drift, missing onboarding paths. |
| [`/self-system-heal`](self-system-heal/SKILL.md)               | stub  | Read a review gap report and patch the originating audit's `SKILL.md`. |

A skill marked **stub** has valid YAML frontmatter and a clear "not yet implemented" notice. It will not run an audit. The implementation pass for each stub is added one at a time.

## Implemented skills in detail

A short summary of every skill currently marked **ready**. Each new skill commit adds its own entry here in the same change as the SKILL.md.

### `/install-skills-locally`
Copies every skill folder from this playbook into `<current-project>/.claude/skills/`. Self-contained `cp -R` — no symlinks, no submodules, no network. Idempotent: re-running only updates files when the source is newer (override with `--force`). Excludes the two installer skills themselves (they belong to the playbook, not its targets). Use this when you want the playbook pinned per project; use the global installer when you want one canonical copy on the machine.

### `/install-skills-globally`
Same mechanics as the local installer, but the destination is `~/.claude/skills/`. Asks for confirmation before overwriting, and never touches skills that are not part of the playbook (graphify is the canonical example). Use this when you audit lots of different codebases and do not want to re-install per project.

### `/pre-audit-setup`
One-time, idempotent project preparation that every audit assumes has been run. Verifies graphify is present at `~/.claude/skills/graphify` (does not install it), runs `/graphify .` to build `graphify-out/`, merges the graphify-aware PreToolUse hook into the project's `.claude/settings.json`, and creates the `audits/` directory. Refuses to run unless graphify is present; never touches global Claude Code settings.

### `/quality-gates-audit`
Compares the project against an opinionated three-stage baseline — pre-commit, pre-push, continuous integration — and reports which gates are present, misconfigured, or missing. Two-phase flow: report findings, then ask whether to generate an implementation plan for the gaps. Fully static (never executes a gate) and Node.js-only in v1. The baseline is opinionated by design — drift from it is the audit's signal. Findings land in `audits/quality-gates-audit/`.

### `/accessibility-audit`
Audits a TypeScript and React frontend against an opinionated WCAG 2.2 AA baseline organised in three layers — tooling and automation, component patterns, application shell. Targets WCAG 2.2 AA specifically (AAA is out of scope by design). Fully static: never starts the dev server, never runs a live axe scan. Screen-reader behaviour and focus-order verification are explicitly deferred to humans. Framework-aware across Next.js (App Router and Pages Router), Remix, Vite-React, and plain React. Status taxonomy is `present | partial | missing | violation`.

### `/architecture-audit`
The most graphify-aligned audit. Compares the codebase against opinionated invariants across four layers — module boundaries, coupling and complexity, state and data flow, convention adherence — preceded by a Layer 0 diagnostic snapshot (god nodes, communities, framework, inferred pattern). Hard requirement on the Graphify knowledge graph: refuses to run if `graphify-out/graph.json` is missing, because half the checks are unimplementable without it. Architectural pattern is inferred (feature folders, layered, atomic design, monorepo workspaces, no clear pattern) with a `--pattern=` override. Thresholds for god modules, god components, file size, and component fan-out are baked-in defaults overridable via flags.

### `/bundle-build-audit`
Audits the build pipeline and bundle output across four layers — build configuration, bundle composition and size, asset and dependency hygiene, build performance — plus a Layer 0 snapshot. **Static-first by design**: by default, the skill reads only configuration files, `package.json`, lockfiles, and continuous-integration workflows. Pass `--with-stats` to additionally read any existing `dist/stats.json`, `.next/analyze/*`, or webpack/rollup analyser artefact for real bundle numbers. The skill never runs the build itself — running builds is a separate concern from auditing them, and keeping the audit fully read-only is what makes it safe to run in any working tree at any time. Soft Graphify dependency: when present, community structure informs the implementation plan's split-plane suggestions. Supports Vite, Next.js (both routers), Remix, Create React App, plain Webpack, plain Rollup, and Turbopack.

### `/dependency-audit`
Audits a Node.js project's dependency tree across four layers — security, health, compliance, hygiene — plus a Layer 0 snapshot. **Static-first by design** with three explicit input tiers: lockfile only, lockfile plus `node_modules`, and lockfile plus `node_modules` plus network. Pass `--with-network` to enrich with vulnerability, outdated, and abandonment data via the package manager's own read-only audit and outdated commands — no install, no update, no lockfile rewrite. The skill **never enforces a license policy**: copyleft and source-available licenses surface as `partial` for human review (with optional allowlist via `.architect-playbook/licenses.json`), never as automatic violations. Soft Graphify dependency: when present, the unused-dependency and misplaced-dependency checks use the import graph as truth (high confidence); without it they fall back to a regex/AST sweep (low confidence). Supports npm, pnpm, yarn (Berry), and bun.

### `/error-handling-audit`
Audits a TypeScript codebase's error-handling discipline across four layers — throw and catch hygiene, async and network error handling, React error boundaries, logging and observability — plus a Layer 0 snapshot. **Static-only by design**, with no opt-in modes: error handling is a pure code-shape concern, and runtime error data belongs to a separate observability tool (Sentry, Datadog, etc.), not to this audit. Layer 3 is automatically skipped when React is not detected, so the skill is equally useful on plain TypeScript backends. Most checks are zero-tolerance (empty catches, string throws, swallowed errors); soft checks like custom-error-class adoption and structured logging report `partial` based on qualitative pattern detection — there are no numeric threshold flags here. Soft Graphify dependency: when present, the graph sharpens analysis of error propagation through async chains. Detects a wide range of error-reporting services (Sentry, Bugsnag, Rollbar, Datadog RUM, Honeybadger, Highlight) and loggers (`pino`, `winston`, `loglevel`, framework-provided).

### `/linting-audit`
Audits a TypeScript project's lint configuration across four layers — configuration shape, rule coverage, strictness and enforcement, suppressions hygiene — plus a Layer 0 snapshot. Complements `/quality-gates-audit`: the gates audit asks "does linting *run* in pre-commit, pre-push, and CI?", this one asks "is the linting *itself* well-configured?". Auto-detects ESLint (flat config preferred) or Biome and dispatches the rule-coverage layer to whichever is in use; **dual-installation is itself a Layer 1 violation**. Static-first by default; `--with-run` opt-in invokes the linter in JSON-reporter mode (no `--fix`, no mutating flags) for real warning/error counts and accurate top-suppressed-rules reporting. Soft Graphify dependency: when present, suppression-heavy files get prioritised by graph centrality so the implementation plan addresses god nodes first. Suppression density is the only numeric threshold (`--threshold-suppressions-per-file=N`, default 5); other checks are zero-tolerance or qualitative.

### `/security-audit`
Audits a TypeScript and React frontend across four layers — authentication and sessions, input handling and XSS prevention, transport/headers/cookies, secrets/data/third-party — plus a Layer 0 snapshot. **Frontend-only in v1 by deliberate design**: server-side concerns (SQL injection, SSRF, command injection, server-side authorization logic) are out of scope and reserved for a future `/backend-security-audit`. Static-first with opt-in `--with-scan` that runs any installed security scanners (`eslint-plugin-security`, `eslint-plugin-no-unsanitized`, `eslint-plugin-react-security`, Semgrep with `p/owasp-top-ten`) in their JSON-reporter modes. Soft Graphify dependency: when present, the graph supports lightweight taint-flow analysis (user input → dangerous sinks like `dangerouslySetInnerHTML`, redirects, `eval`). PII detection is heuristic-only and reported as `partial`, never `violation`. Dependency vulnerabilities are owned by `/dependency-audit`; this audit only touches Subresource Integrity and `postinstall` awareness as frontend-bundle concerns. The implementation plan is ordered by **severity** rather than by layer, so the user fixes the highest-impact issues first.

### `/performance-audit`
Audits a TypeScript and React frontend's runtime performance across four layers — render performance, network and data, assets/media/Core Web Vitals, main-thread work and measurement — plus a Layer 0 snapshot. **Boundary discipline**: bundle and build configuration belongs to `/bundle-build-audit`, architectural invariants like god components belong to `/architecture-audit`, hook correctness belongs to `/react-audit` — this skill owns runtime cost levers (memoization, virtualization, context churn, fetch waterfalls, image priority hints, layout shift, Core Web Vitals practices). Static-first with opt-in `--with-lighthouse-results` reading existing Lighthouse JSON for real LCP/INP/CLS numbers; the skill **never runs Lighthouse itself** (that's a separate concern, just like never running the build). Soft Graphify dependency: when present, hot render paths (god components rendered every navigation, central data hooks) get prioritised in the implementation plan. List virtualization threshold tunable via `--threshold-virtualization=N` (default 50); other checks zero-tolerance or qualitative. The implementation plan is ordered by **Core Web Vitals impact** (LCP-impacting fixes first, then INP, then CLS, then code-quality items).

### `/react-audit`
Audits a TypeScript and React project for **idiomatic React** across four layers — hooks correctness, component design, state management, React 18/19 idioms — plus a Layer 0 snapshot. The most overlap-heavy audit in the playbook; the SKILL body carries a verbatim boundary table mapping every React-adjacent concern to the audit that owns it (memoization-for-cost lives in `/performance-audit`, single-state-library lives in `/architecture-audit`, jsx-a11y lives in `/accessibility-audit`, error boundaries live in `/error-handling-audit`, and so on). Memoization-for-correctness (referential stability that downstream hooks depend on) is uniquely this audit's domain. Static-only by design — `/linting-audit --with-run` is the right tool for fresh hook-violation capture. Layer 4 (React 18/19 idioms: `useId`, `useTransition`, Suspense, `useOptimistic`, `'use client'`, server actions) is automatically skipped on React below 18. Class components are reported as `partial` rather than `violation`; the implementation plan flags class components newer than the most recent function component as the strongest migration signal. Soft Graphify dependency: when present, widely-consumed custom hooks get centrality-prioritised in the implementation plan.

## Conventions

- **Conventional Commits** for every commit.
- **No abbreviations** in skill names, triggers, descriptions, headings, identifiers, or prose.
- **Read-only by default.** Mutating runs require an explicit `--apply` flag and print a dry-run summary first.
- **Frontmatter shape** is fixed: `name`, `description`, `trigger`. The `trigger` value equals `/<folder-name>`.
- **No hard-coded absolute paths** in skill bodies. Derive from the current working directory or `$HOME`.

The full set of project-wide rules lives in [CLAUDE.md](CLAUDE.md).

## Contributing

To add a new skill:

1. Create a folder named after the slash command. Use full words.
2. Add a `SKILL.md` with the standard frontmatter.
3. Follow the body structure described in [CLAUDE.md](CLAUDE.md): purpose, usage, what-it-does, implementation steps, what-it-does-NOT-do.
4. Add a row to the skill table above.
5. Commit with a Conventional Commits subject:
   ```
   feat: add new <skill-name> skill
   ```

To improve an existing skill, the preferred path is the playbook's own self-improvement loop: run the audit, run a review against the resulting fix, then run `/self-system-heal` so the patch to the audit body is grounded in a real gap rather than speculation.

## Related

- [graphify](https://graphify.net/graphify-claude-code-integration.html) — the knowledge-graph skill that `/pre-audit-setup` assumes is installed at `~/.claude/skills/graphify`.
