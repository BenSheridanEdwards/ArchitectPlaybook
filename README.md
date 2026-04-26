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
| [`/security-audit`](security-audit/SKILL.md)                   | stub  | Audit authentication, authorization, input validation, secrets, dependencies. |
| [`/accessibility-audit`](accessibility-audit/SKILL.md)         | ready | Audit a TypeScript and React frontend against an opinionated WCAG 2.2 AA baseline across tooling, component patterns, and application shell; optionally generate an implementation plan for the gaps. |
| [`/dependency-audit`](dependency-audit/SKILL.md)               | stub  | Audit dependencies for vulnerabilities, outdated versions, license risk, abandonment. |
| [`/performance-audit`](performance-audit/SKILL.md)             | stub  | Audit render hot paths, expensive computations, network waterfalls, caching gaps. |
| [`/architecture-audit`](architecture-audit/SKILL.md)           | ready | Audit a TypeScript codebase against opinionated invariants for module boundaries, coupling, state and data flow, and convention adherence using the Graphify knowledge graph; produces a diagnostic snapshot and a violations report; optionally generates an implementation plan. |
| [`/testing-audit`](testing-audit/SKILL.md)                     | stub  | Audit coverage gaps, weak assertions, flaky patterns, missing integration coverage. |
| [`/react-audit`](react-audit/SKILL.md)                         | stub  | Audit React component anti-patterns, hook misuse, unnecessary re-renders. |
| [`/linting-audit`](linting-audit/SKILL.md)                     | stub  | Audit lint configuration, ignored files, suppressed warnings, conflicting plugins. |
| [`/typescript-audit`](typescript-audit/SKILL.md)               | stub  | Audit TypeScript configuration and code: unsafe casts, weak types, strictness. |
| [`/bundle-build-audit`](bundle-build-audit/SKILL.md)           | ready | Audit a TypeScript frontend's build pipeline and bundle output across configuration, composition and size, asset hygiene, and build performance; static-first with optional `--with-stats` enrichment from existing bundle stats artefacts; optionally generates an implementation plan. |
| [`/error-handling-audit`](error-handling-audit/SKILL.md)       | stub  | Audit swallowed exceptions, missing context, inconsistent logging. |
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
