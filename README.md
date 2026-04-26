# Architect Playbook

A self-contained, self-improving collection of Claude Code slash-command skills for auditing any codebase. Walk onto a project, install the skills, run multiple audits in parallel from separate chat sessions, fix what they find, review the fixes in a Git worktree, and let `/system-self-improve` patch the audits themselves whenever a review surfaces a gap.

## Core principles

The playbook is opinionated by design. These principles shape every audit and every recommendation it produces.

- **Opinionated baselines.** Each audit grades against a specific opinionated baseline, not whatever happens to be in the codebase. Drift from the baseline is the audit's signal.
- **Static-first with optional enrichment.** Every audit is read-only by default. Network, runtime, and live-tool data are opt-in via explicit flags (`--with-network`, `--with-stats`, `--with-run`, `--with-lighthouse-results`, `--with-link-check`, `--with-scan`).
- **Two-phase flow.** Every audit reports findings, then asks whether to generate an implementation plan. Mutation is never automatic; even `/system-self-improve --apply` prompts for explicit confirmation.
- **Boundary discipline.** Cross-cutting concerns surface in every audit they touch with the explicit "single fix passes both" framing. The boundary tables in `/react-audit`, `/performance-audit`, and `/typescript-audit` are the canonical reference for who owns what.
- **Self-improving.** When a review surfaces a gap an audit missed, `/system-self-improve` reads the gap and patches the originating audit so the same class of issue is more likely to be caught next time.
- **Test behaviour, not implementation.** Tests describe what the user perceives — what they see, click, type, and read — not internal state shape, prop names, or call sequences.
- **Snapshots are a smell.** Small bounded snapshots are tolerated; large or implementation-coupled snapshots are flagged as violations. Explicit assertions are the default.
- **Semantic tokens over utility classes.** Class assertions reference design tokens (`bg-primary`) that survive theme changes — never raw utility classes (`bg-gray-100`) that shatter the moment the theme moves.

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
3. **Spin up a worktree per audit and run them in parallel.** Use `/worktree <name>` to create the worktree, then open a fresh Claude Code chat in it and run the audit. Each audit writes its findings to `audits/<audit-name>/findings.md` (and `findings.json`, and `metadata.json`).
   ```
   /worktree security        # then in a new chat in ../wt-security:        /security-audit
   /worktree performance     # then in a new chat in ../wt-performance:     /performance-audit
   /worktree accessibility   # then in a new chat in ../wt-accessibility:   /accessibility-audit
   ```
   Run as many as you want in parallel — they are independent chat windows.
4. **Fix the findings in the same chat that produced them.** Ask Claude to read `audits/<audit-name>/findings.md` and implement the fixes. Stay in that chat — it has the most context about what the audit surfaced.
5. **Run the review in a fresh chat against the same worktree.** Re-running the originating audit *is* the review pass.
   ```
   # In a fresh Claude Code chat opened in the worktree (e.g. ../wt-security):
   /security-audit              # the audit re-run sees the fix and reports anything still outstanding
   ```
   The review picks up anything the original audit flagged that the fix didn't address, plus anything new that emerged from the fix itself.
6. **If the review surfaces something the audit missed, evolve the audit.**
   ```
   /system-self-improve
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

# 4. Spin up a worktree for the first audit
/worktree security
# then open a new Claude Code chat in ../wt-security and run:
#   /security-audit
```

## Running audits in parallel with Git worktrees

Audits do not modify the codebase, so you can run several at once against the main checkout. Reviews and fixes are easier to keep separate when each lives in its own worktree.

The fastest path is the [`/worktree`](worktree/SKILL.md) helper, which creates a worktree for the named audit and prints the next-step instructions:

```
/worktree security
/worktree performance
/worktree accessibility
/worktree dependency
```

The argument accepts the full skill name (`security-audit`), the short form (`security`), or any unambiguous prefix. Run `/worktree` with no argument to pick from a list of available audits.

Or run the equivalent Git commands by hand:

```bash
git worktree add ../wt-security       -b wt-security
git worktree add ../wt-performance    -b wt-performance
git worktree add ../wt-accessibility  -b wt-accessibility
git worktree add ../wt-dependency     -b wt-dependency
```

Either way: open one Claude Code chat per worktree, run the matching audit slash command in each, and let them work in parallel. When you are ready to apply fixes, do it in the same chat that produced the audit (each chat has the most context about its own findings). When the fixes are in, spin up a fresh chat against the worktree to run the review pass.

## The findings-file contract

Audits, fixes, and reviews each run in different chat sessions, so they cannot share in-memory state. The protocol between them is a deterministic on-disk shape:

```
audits/
  <audit-name>/
    findings.md       human-readable report you can read at a glance
    findings.json     machine-readable list of issues for downstream skills
    metadata.json     skill version, run timestamp, graphify revision hash
```

- **Fixing** is a free-form chat conversation in the same chat that produced the audit. The user asks Claude to read `findings.json` (or `findings.md`) and implement the fixes. There is no dedicated fix skill — the audit's findings file plus the implementation plan are the brief.
- **Reviewing** is re-running the originating audit in a fresh chat against the worktree containing the fix. Whatever the audit still flags is what the fix didn't address. There is no dedicated review skill — the audit *is* the review.
- **`/system-self-improve`** reads a review's gap report (or a user-supplied gap, or audit-history patterns) and proposes an edit to the originating audit's `SKILL.md` so the same class of gap is more likely to be caught next time.

## Status taxonomy

Every audit grades each check with one of four statuses, plus an informational Layer 0 diagnostic snapshot that has no status.

- **present** — the invariant holds.
- **partial** — most signals resolve with a small number of exceptions, or the codebase shows mixed adherence to a soft check, or a tier-dependent check is running below its required tier.
- **missing** — a structural prerequisite is absent (no linter installed, no lockfile present, no design system detected for design-system-conditional checks).
- **violation** — the audit identified concrete code, configuration, or output that breaks the invariant.

`/system-self-improve` is the exception. It grades the playbook itself rather than a target codebase, so each of its four sequential stages records `outcome: advanced | blocked | skipped` with a `blockerReason` when a stage cannot advance.

## The full skill list

The playbook's skills fall into three categories: setup utilities you run once per project (or once per machine), audits that grade the codebase against an opinionated baseline, and the meta layer that evolves the audits themselves.

### Setup utilities

| Trigger | Status | Purpose |
| --- | --- | --- |
| [`/install-skills-locally`](install-skills-locally/SKILL.md)   | ready | Copy every playbook skill into the current project's `.claude/skills/`. |
| [`/install-skills-globally`](install-skills-globally/SKILL.md) | ready | Copy every playbook skill into `~/.claude/skills/` for machine-wide use. |
| [`/pre-audit-setup`](pre-audit-setup/SKILL.md)                 | ready | Verify graphify, build the project knowledge graph, merge the PreToolUse hook. |
| [`/worktree`](worktree/SKILL.md)                               | ready | One-shot helper that creates a Git worktree for running an audit in parallel. Lenient prefix matching; bare `/worktree` opens a picker. |

### Audits

| Trigger | Status | Purpose |
| --- | --- | --- |
| [`/quality-gates-audit`](quality-gates-audit/SKILL.md)         | ready | Pre-commit, pre-push, and CI/CD lifecycle gates against an opinionated baseline. |
| [`/security-audit`](security-audit/SKILL.md)                   | ready | Frontend security: auth/sessions, XSS prevention, transport headers, secrets, third-party integrations. Server-side reserved for a future `/backend-security-audit`. |
| [`/accessibility-audit`](accessibility-audit/SKILL.md)         | ready | WCAG 2.2 AA across tooling, component patterns, and application shell. Static-only — screen-reader and focus-order verification deferred to humans. |
| [`/dependency-audit`](dependency-audit/SKILL.md)               | ready | Dependency tree across security, health, compliance, hygiene. Three input tiers (lockfile / + node_modules / + network). License signals are flagged for human review, never auto-enforced. |
| [`/performance-audit`](performance-audit/SKILL.md)             | ready | Runtime performance: render, network/data, assets/CWV, main-thread. Implementation plan ordered by Core Web Vitals impact. |
| [`/architecture-audit`](architecture-audit/SKILL.md)           | ready | Module boundaries, coupling, state/data flow, convention adherence. The most graphify-aligned audit — refuses to run without the knowledge graph. |
| [`/testing-audit`](testing-audit/SKILL.md)                     | ready | Tests against the Testing Library priority ladder and the well-known RTL pitfalls. Carries the playbook's Testing Philosophy. |
| [`/react-audit`](react-audit/SKILL.md)                         | ready | Idiomatic React: hook correctness, component design, state management, React 18/19 idioms. Layer 4 auto-skipped on React below 18. |
| [`/linting-audit`](linting-audit/SKILL.md)                     | ready | Lint configuration shape, rule coverage, strictness, suppressions hygiene. Auto-detects ESLint or Biome; dual-install is itself a violation. |
| [`/typescript-audit`](typescript-audit/SKILL.md)               | ready | Compiler flags, source-level type quality, type-system idioms, runtime validation at IO boundaries. Tunable per-file thresholds for `any`/`as`/`!`. |
| [`/bundle-build-audit`](bundle-build-audit/SKILL.md)           | ready | Build pipeline and bundle output. Static-first; `--with-stats` reads existing analyser artefacts but never runs the build. |
| [`/error-handling-audit`](error-handling-audit/SKILL.md)       | ready | Throw/catch hygiene, async/network paths, React error boundaries, logging and observability. React layer auto-skipped when not detected. |
| [`/documentation-audit`](documentation-audit/SKILL.md)         | ready | Onboarding, architectural/decision docs, code-level docs, operational + drift. Operational checks auto-skip for library-only projects. |

### Meta

| Trigger | Status | Purpose |
| --- | --- | --- |
| [`/system-self-improve`](system-self-improve/SKILL.md)         | ready | The meta-improvement layer of the playbook. Reads a review gap report and proposes a minimal reversible edit to the affected SKILL.md so the same class of gap is more likely to be caught next time. The only skill that mutates files outside its own `audits/` directory. |

A skill marked **stub** has valid YAML frontmatter and a clear "not yet implemented" notice. It will not run an audit. The implementation pass for each stub is added one at a time.

## Why each skill exists

Two or three sentences per skill explaining *why* it's a separate concern. The full *what* lives in each [`SKILL.md`](.) — these summaries answer "should I run this?" not "how does it work?".

### `/install-skills-locally`
Pin the playbook per project so the skills live alongside the code in version control. Different projects can track different versions of the playbook this way; the global installer is the alternative when you'd rather have one canonical copy on the machine.

### `/install-skills-globally`
Install once for the whole machine when you audit lots of different codebases and don't want to re-install per project. Trades version pinning for ergonomics.

### `/pre-audit-setup`
Idempotent project preparation that every audit assumes has been run. Verifies graphify, builds the knowledge graph the architecture audit (and others) read, and merges the graphify-aware PreToolUse hook into the project's `.claude/settings.json`.

### `/worktree`
Removes the friction of spinning up a Git worktree per audit so you can run multiple audits in true parallel from separate Claude Code chats. Lenient prefix matching (`/worktree sec` resolves to `security-audit`); bare `/worktree` opens a picker.

### `/quality-gates-audit`
Three lifecycle stages — pre-commit, pre-push, CI/CD — graded against an opinionated baseline. Asks whether the gates *run*; `/linting-audit` and `/typescript-audit` ask whether what runs is *well-configured*.

### `/security-audit`
Frontend security as a focused concern: auth/sessions, XSS prevention, transport headers, secrets, third-party integrations. Server-side security (SQL injection, SSRF, server-side authorization) is reserved for a future `/backend-security-audit` rather than smuggled in as half-coverage.

### `/accessibility-audit`
WCAG 2.2 AA, three opinionated layers (tooling, component patterns, application shell). Static-only by design — screen-reader behaviour and focus-order verification need a human and assistive technology, so the audit doesn't pretend to cover them.

### `/dependency-audit`
The dependency tree as a single concern: security, health, compliance, hygiene. Three explicit input tiers (lockfile / + node_modules / + network) make the accuracy-vs-cost trade-off visible; license findings are flagged for human review rather than auto-enforced because legal policy isn't the audit's call.

### `/performance-audit`
Runtime cost is its own discipline. Build configuration belongs to `/bundle-build-audit`, architectural invariants belong to `/architecture-audit`, hook correctness belongs to `/react-audit` — this skill owns memoization, virtualization, fetch waterfalls, image priority, layout shift, and Core Web Vitals practices.

### `/architecture-audit`
The most graphify-aligned audit. Hard requirement on the knowledge graph because half the checks (god-node detection, community comparison, cycle detection) are unimplementable without it; degrading silently would produce a misleading half-audit.

### `/testing-audit`
Anchored on the Testing Library priority ladder and the well-known RTL pitfalls. Carries the playbook's Testing Philosophy explicitly — behaviour over implementation, snapshots are a smell, semantic tokens never utility classes — so the audit's stance is unmistakable.

### `/react-audit`
Idiomatic React isolated from cost, accessibility, architecture, and error handling (each of which has its own audit). The skill's verbatim boundary table is the canonical map of who owns what across the React-adjacent surface; memoization-for-correctness lives uniquely here.

### `/linting-audit`
"Is the linting itself well-configured?" — complementary to `/quality-gates-audit`'s "does linting run?". Auto-detects ESLint or Biome and treats dual-install as a Layer 1 violation rather than ignoring the contradiction.

### `/typescript-audit`
The type system as a single concern: compiler flags, source-level type quality, type-system idioms, runtime validation at IO boundaries. Per-file thresholds for `any`/`as`/`!` are tunable so the audit fits codebases of different ages.

### `/bundle-build-audit`
The build pipeline and its output. Static-first by design and `--with-stats` reads existing analyser artefacts but never runs the build itself — keeping the audit read-only is what makes it safe to run in any working tree at any time.

### `/error-handling-audit`
Throw/catch hygiene, async and network error paths, React error boundaries, observability. Static-only by design — runtime error data belongs to Sentry/Datadog, not to a code-shape audit.

### `/documentation-audit`
Documentation as four lenses (onboarding, architectural/decision, code-level, operational + drift). Operational checks auto-skip for library-only projects so the audit doesn't penalise a library for not having a runbook.

### `/system-self-improve`
The only skill that mutates files outside its own `audits/` directory, and the only one with an `--apply` mode. Hard prohibitions baked in (never deletes a check or skill, never changes the four-layer convention, never edits target-project files, never commits) and `--apply` always prompts for explicit confirmation — there is no `--yes` escape hatch, by design.

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

To improve an existing skill, the preferred path is the playbook's own self-improvement loop: run the audit, run a review against the resulting fix, then run `/system-self-improve` so the patch to the audit body is grounded in a real gap rather than speculation.

## Related

- [graphify](https://graphify.net/graphify-claude-code-integration.html) — the knowledge-graph skill that `/pre-audit-setup` assumes is installed at `~/.claude/skills/graphify`.
