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
   Verifies graphify is installed, builds the project knowledge graph, and merges the graphify-aware PreToolUse hook into `.claude/settings.json`. If you plan to pass any `--with-*` enrichment flag (`--with-stats`, `--with-run`, `--with-lighthouse-results`, `--with-scan`), also run `/preflight` to detect — and optionally install — the development dependencies those flags need.
3. **Run audits, one per chat.** Each audit takes a `--target=<path>` flag, so you have two clean choices:
   - **Main checkout (no worktree):** open a Claude Code chat in your project and run `/<audit>` directly. Findings land in `.architect-audits/<audit>/`.
   - **Worktree:** open a Claude Code chat in your project and run `/worktree <name>`. The skill creates `../wt-<name>` on a `wt-<name>` branch *and* runs the matching audit against it, all in this same chat. Findings land in `../wt-<name>/.architect-audits/<audit>/`.
   ```
   /security-audit                # one audit, main checkout
   /worktree security             # one audit, on a fresh worktree branch — same chat
   ```
   **Smart-handoff:** if you ran `/security-audit` in a chat and *then* decided you want a worktree to apply fixes on a clean branch, just run `/worktree` (no argument). When the chat already has recent findings from an audit run against the main checkout, `/worktree` infers the audit name from history, creates the worktree, copies the findings into it, and offers to generate the implementation plan against the worktree — no audit re-run.

   For multiple audits in true parallel, open multiple chats and run `/worktree <name>` in each. Each chat is independent; each audit operates on its own worktree.
4. **Fix the findings in the same chat that produced them.** Ask Claude to read `.architect-audits/<audit>/findings.md` (or `../wt-<name>/.architect-audits/<audit>/findings.md` if you used a worktree) and implement the fixes. Stay in that chat — it has the most context.
5. **Re-run the audit to review the fix.** In the same chat, run the audit again. The re-run *is* the review pass — anything still flagged is anything the fix didn't address.
   ```
   /security-audit                # re-run; or /security-audit --target=../wt-security if you used a worktree
   ```
   For full review independence (a separate chat with no memory of the fix conversation), open a fresh chat in the worktree and re-run the audit there. The trade-off is described in the [Single-audit shortcut](#single-audit-shortcut) section below.
6. **If the review surfaces something the audit missed, evolve the audit.**
   ```
   /system-self-improve
   ```
   This patches the originating audit's own `SKILL.md` so the same class of issue is caught next time. The playbook gets sharper every time you use it.

### Single-audit shortcut

For one audit, one chat does it all. Two flavours:

**No worktree** — fixes land on the current branch:

```
/pre-audit-setup
/security-audit
# read findings, ask Claude to fix them in this chat
/security-audit              # re-run to verify
```

**With worktree** — fixes land on a fresh `wt-security` branch:

```
/pre-audit-setup
/worktree security           # creates ../wt-security and runs /security-audit --target=../wt-security
# read findings, ask Claude to fix them in this chat (Claude edits ../wt-security/ files)
/worktree security           # re-running /worktree on an existing worktree reuses it; the audit re-runs as a review
```

Both flavours give up a small amount of review independence (the same chat that fixed it reviews it). For full review independence, open a fresh chat in the worktree and run `/<audit>` there — that chat has no memory of the fix conversation, so the review is genuinely blind.

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

# 4. Run an audit. Pick one:
/security-audit          # against the main checkout
# or:
/worktree security       # creates ../wt-security and runs the audit against it, in this same chat
```

## Running audits in parallel with Git worktrees

Audits don't modify the codebase, so you can run several at once. Each audit takes a `--target=<path>` flag, and the [`/worktree`](worktree/SKILL.md) helper packages the worktree-creation + audit-run pattern into a single slash command.

```
# Open four Claude Code chats in the project, then in each one:
/worktree security
/worktree performance
/worktree accessibility
/worktree dependency
```

Each chat creates its own worktree (`../wt-security`, `../wt-performance`, ...) and runs the matching audit against it, all in that same chat. Four independent chats, four worktrees, four audits running in true parallel. When fixes are needed, apply them in the chat that produced the audit (each chat has the full findings in context).

`/worktree` accepts the full skill name (`security-audit`), the short form (`security`), or any unambiguous prefix (`sec`). Run `/worktree` with no argument to pick from a list.

If you'd rather skip the helper, the manual equivalent is:

```bash
git worktree add ../wt-security -b wt-security
# then in a Claude Code chat in your project:
/security-audit --target=../wt-security
```

The skill always wins out because it bundles the worktree creation with the audit invocation in one slash command, but the underlying mechanic is plain Git plus the audit's `--target` flag.

## The findings-file contract

Audits, fixes, and reviews each run in different chat sessions, so they cannot share in-memory state. The protocol between them is a deterministic on-disk shape:

```
.architect-audits/
  <audit-name>/
    findings.md       human-readable report you can read at a glance
    findings.json     machine-readable list of issues for downstream skills
    snapshot.md       the Layer 0 diagnostic snapshot, on its own
    metadata.json     skill version, run timestamp, graphify revision hash
```

- **Chat output** is human-first and concise: a short header, the Top 5 Highest-Leverage Recommendations (title, why it matters, consequences, smallest fix, lettered sub-actions), and a one-line pointer to the full report on disk. The full layered findings are never printed in the chat unless the user explicitly asks.
- **Fixing** is a free-form chat conversation in the same chat that produced the audit. The user asks Claude to read `findings.json` (or `findings.md`) and implement the fixes. There is no dedicated fix skill — the audit's findings file plus the implementation plan are the brief.
- **Reviewing** is re-running the originating audit in a fresh chat against the worktree containing the fix. Whatever the audit still flags is what the fix didn't address. There is no dedicated review skill — the audit *is* the review.
- **`/system-self-improve`** reads a review's gap report (or a user-supplied gap, or audit-history patterns) and proposes an edit to the originating audit's `SKILL.md` so the same class of gap is more likely to be caught next time.

A worked example of this contract — `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` produced by running `/documentation-audit` against the playbook itself — is committed at [`.architect-audits/documentation-audit/`](.architect-audits/documentation-audit/).

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

| Trigger | Flags | Purpose |
| --- | --- | --- |
| [`/install-skills-locally`](install-skills-locally/SKILL.md)   | `--dry-run` (print the plan without copying)<br>`--force` (overwrite destinations even if they appear newer)<br>`--include=<name>` / `--exclude=<name>` (narrow or skip skills, repeatable) | Copy every playbook skill into the current project's `.claude/skills/`. |
| [`/install-skills-globally`](install-skills-globally/SKILL.md) | `--dry-run` (print the plan without copying)<br>`--force` (overwrite destinations even if they appear newer)<br>`--include=<name>` / `--exclude=<name>` (narrow or skip skills, repeatable) | Copy every playbook skill into `~/.claude/skills/` for machine-wide use. |
| [`/pre-audit-setup`](pre-audit-setup/SKILL.md)                 | `--dry-run` (describe what would change without modifying anything)<br>`--force` (rebuild the knowledge graph and rewrite the hook even if both already exist) | Verify graphify, build the project knowledge graph, merge the PreToolUse hook. |
| [`/worktree`](worktree/SKILL.md)                               | `<skill-name>` — positional, lenient prefix matching (e.g. `sec` resolves to `security-audit`). Bare `/worktree` opens a picker. | Creates a Git worktree for the named audit and runs the audit against it, all in this same chat. |
| [`/preflight`](preflight/SKILL.md)                             | `--audit=<name>` (lenient prefix matching like `/worktree`)<br>`--target=<path>` (operate on a different project root)<br>`--install` (prompt and install missing development dependencies)<br>`--scaffold-configs` (prompt and create minimal project configuration) | Detect optional enrichment tooling for audits with `--with-*` flags. Read-only by default; mutation is gated behind explicit flags and prompts before every change. |

### Audits

Every audit accepts the same set of common flags, listed once here so the per-skill column below stays focused on what is unique:

- `--report-only` — phase 1 only: write findings, do not offer a plan.
- `--plan` — skip phase 1 if findings already exist; jump to plan generation.
- `--layer=<name>` — restrict to a single layer (repeatable). `/quality-gates-audit` uses `--stage=<name>` instead.
- `--include=<check>` / `--exclude=<check>` — narrow or skip individual checks (repeatable).
- `--target=<path>` — operate on a different directory instead of the current working directory. This is what lets `/worktree` audit a worktree from a chat opened elsewhere.

Many audits also expose `--threshold-*` flags as escape hatches for codebases with deliberately different defaults — see each `SKILL.md` for the full list. None of the audit skills accept `--apply`; mutation is reserved for `/system-self-improve`.

| Trigger | Audit-specific flags | Purpose |
| --- | --- | --- |
| [`/quality-gates-audit`](quality-gates-audit/SKILL.md)         | (common only — uses `--stage=<pre-commit\|pre-push\|continuous-integration>` in place of `--layer=`) | Pre-commit, pre-push, and CI/CD lifecycle gates against an opinionated baseline. |
| [`/security-audit`](security-audit/SKILL.md)                   | `--with-scan` (enrich findings with output from installed scanners) | Frontend security: auth/sessions, XSS prevention, transport headers, secrets, third-party integrations. Server-side reserved for a future `/backend-security-audit`. |
| [`/accessibility-audit`](accessibility-audit/SKILL.md)         | `--severity=error` (report only violations and missing-required checks; suppress warnings) | WCAG 2.2 AA across tooling, component patterns, and application shell. Static-only — screen-reader and focus-order verification deferred to humans. |
| [`/dependency-audit`](dependency-audit/SKILL.md)               | `--with-network` (tier 3: enrich with `npm audit` and `npm outdated` registry data)<br>`--security-critical-packages=<list>` (override the default list of packages whose CVEs are treated as critical) | Dependency tree across security, health, compliance, hygiene. Three input tiers (lockfile / + node_modules / + network). License signals are flagged for human review, never auto-enforced. |
| [`/performance-audit`](performance-audit/SKILL.md)             | `--with-lighthouse-results` (enrich from existing Lighthouse JSON in the repo)<br>`--lighthouse-results-path=<path>` (override the default `lighthouse-results.json` location) | Runtime performance: render, network/data, assets/CWV, main-thread. Implementation plan ordered by Core Web Vitals impact. |
| [`/architecture-audit`](architecture-audit/SKILL.md)           | `--pattern=<feature-folders\|layered\|atomic-design\|monorepo-workspaces\|infer>` (override the architectural pattern that would otherwise be inferred from the project layout) | Module boundaries, coupling, state/data flow, convention adherence. The most graphify-aligned audit — refuses to run without the knowledge graph. |
| [`/testing-audit`](testing-audit/SKILL.md)                     | `--with-run` (enrich with a real Vitest/Jest coverage run) | Tests against the Testing Library priority ladder and the well-known RTL pitfalls. Carries the playbook's Testing Philosophy. |
| [`/react-audit`](react-audit/SKILL.md)                         | (common only) | Idiomatic React: hook correctness, component design, state management, React 18/19 idioms. Layer 4 auto-skipped on React below 18. |
| [`/linting-audit`](linting-audit/SKILL.md)                     | `--with-run` (enrich with a real `eslint . --format json` or `biome lint --reporter json` run) | Lint configuration shape, rule coverage, strictness, suppressions hygiene. Auto-detects ESLint or Biome; dual-install is itself a violation. |
| [`/typescript-audit`](typescript-audit/SKILL.md)               | `--with-run` (enrich with a real `tsc --noEmit` run) | Compiler flags, source-level type quality, type-system idioms, runtime validation at IO boundaries. Tunable per-file thresholds for `any`/`as`/`!`. |
| [`/bundle-build-audit`](bundle-build-audit/SKILL.md)           | `--with-stats` (enrich from an existing bundle-analyser stats artefact; never runs the build itself)<br>`--stats-path=<path>` (override the auto-detected stats artefact location) | Build pipeline and bundle output. Static-first; `--with-stats` reads existing analyser artefacts but never runs the build. |
| [`/error-handling-audit`](error-handling-audit/SKILL.md)       | (common only) | Throw/catch hygiene, async/network paths, React error boundaries, logging and observability. React layer auto-skipped when not detected. |
| [`/documentation-audit`](documentation-audit/SKILL.md)         | `--with-link-check` (HEAD-check external URLs in Markdown to detect broken links) | Onboarding, architectural/decision docs, code-level docs, operational + drift. Operational checks auto-skip for library-only projects. |

### Meta

| Trigger | Flags | Purpose |
| --- | --- | --- |
| [`/system-self-improve`](system-self-improve/SKILL.md)         | `--apply` (perform the proposed edit; always prompts for explicit confirmation first — there is no `--yes` escape hatch)<br>`--gap=<description>` + `--target-skill=<name>` (user-supplied gap mode: describe the gap and which skill should absorb the lesson)<br>`--from-audit-history` (scan `.architect-audits/*/findings.json` for systematic patterns instead of a single review report)<br>`--gap-report=<path>` (explicit path to a review gap report, overriding the default scan)<br>`--playbook-path=<path>` (operate on a playbook clone at a different location, e.g. a worktree)<br>`--plan` (regenerate `improvement-plan.md` for an existing run without re-scanning) | The meta-improvement layer of the playbook. Reads a review gap report and proposes a minimal reversible edit to the affected SKILL.md so the same class of gap is more likely to be caught next time. The only skill that mutates files outside its own `.architect-audits/` directory. |

## Why each skill exists

Two or three sentences per skill explaining *why* it's a separate concern. The full *what* lives in each [`SKILL.md`](.) — these summaries answer "should I run this?" not "how does it work?".

### `/install-skills-locally`
Pin the playbook per project so the skills live alongside the code in version control. Different projects can track different versions of the playbook this way; the global installer is the alternative when you'd rather have one canonical copy on the machine.

### `/install-skills-globally`
Install once for the whole machine when you audit lots of different codebases and don't want to re-install per project. Trades version pinning for ergonomics.

### `/pre-audit-setup`
Idempotent project preparation that every audit assumes has been run. Verifies graphify, builds the knowledge graph the architecture audit (and others) read, and merges the graphify-aware PreToolUse hook into the project's `.claude/settings.json`.

### `/worktree`
The single-chat wrapper for the audit-against-worktree pattern. `/worktree security` creates `../wt-security` *and* runs `/security-audit --target=../wt-security` in the same chat — no second chat needed. Lenient prefix matching (`/worktree sec` resolves to `security-audit`); bare `/worktree` opens a picker. The chat stays in the original project; the audit reaches into the worktree via the audit's `--target` flag, which every audit accepts. For multiple parallel audits, open multiple chats and `/worktree <name>` in each. **Smart-handoff:** if an audit was just run against the main checkout in this chat (no `--target`, findings within the last 30 minutes), `/worktree` infers the audit name from chat history, copies the findings into the worktree, skips the audit re-run, and offers to generate the implementation plan against the worktree.

### `/preflight`
Optional pre-step for audits that accept enrichment flags. Seven audits (`/bundle-build-audit`, `/typescript-audit`, `/testing-audit`, `/dependency-audit`, `/performance-audit`, `/security-audit`, `/linting-audit`) gather more comprehensive findings when their enrichment tooling is installed; running an audit, discovering the tooling is missing, installing it, then re-running is wasteful. `/preflight` detects what's missing in one shot, prints a status table sorted with the actionable rows on top, and — only behind explicit `--install` and `--scaffold-configs` flags — installs development dependencies and scaffolds minimal project configuration. Read-only by default; never touches global CLIs or external services.

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
The only skill that mutates files outside its own `.architect-audits/` directory, and the only one with an `--apply` mode. Hard prohibitions baked in (never deletes a check or skill, never changes the four-layer convention, never edits target-project files, never commits) and `--apply` always prompts for explicit confirmation — there is no `--yes` escape hatch, by design.

## Conventions

- **Conventional Commits** for every commit.
- **No abbreviations** in skill names, triggers, descriptions, headings, identifiers, or prose.
- **Read-only by default.** Mutating runs require an explicit `--apply` flag and print a dry-run summary first.
- **Frontmatter shape** is fixed: `name`, `description`, `trigger`. The `trigger` value equals `/<folder-name>`.
- **No hard-coded absolute paths** in skill bodies. Derive from the current working directory or `$HOME`.

The full set of project-wide rules lives in [CLAUDE.md](CLAUDE.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide. The short version:

- **Add a new skill:** create a folder named after the slash command (full words, no abbreviations), write a `SKILL.md` following the canonical body structure, add the table row and the per-skill summary in the same commit. Audits also get the worktree Pro Tip block.
- **Improve an existing skill:** prefer the self-improvement loop. Run the audit, fix the findings, review the fix in a worktree, then run `/system-self-improve` so the patch to the audit body is grounded in a real gap rather than speculation.

The architectural intent behind the playbook's conventions lives in [ARCHITECTURE.md](ARCHITECTURE.md). Foundational decisions are recorded as ADRs in [docs/decisions/](docs/decisions/).

## License

[MIT](LICENSE).

## Related

- [graphify](https://graphify.net/graphify-claude-code-integration.html) — the knowledge-graph skill that `/pre-audit-setup` assumes is installed at `~/.claude/skills/graphify`.
