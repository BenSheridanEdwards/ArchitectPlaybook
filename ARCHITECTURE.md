# Architect Playbook — Architecture

This document describes the architectural intent behind the playbook so future contributors can extend it without breaking the design. The user-facing description lives in [README.md](README.md); the project-wide rules live in [CLAUDE.md](CLAUDE.md); the historical record of foundational decisions lives in [docs/decisions/](docs/decisions/).

## What the playbook is

A collection of Claude Code slash-command skills that grade a TypeScript codebase against opinionated baselines and propose implementation plans for the gaps. The skills are stateless prompts (Markdown files with YAML frontmatter); the only execution context is whatever Claude Code chat session invokes them. There is no compiled binary, no service, no daemon — just structured prose that Claude follows.

This shape is what makes the playbook self-contained: cloning the repo and copying the skills into `.claude/skills/` is the entire installation. There are no dependencies to resolve, no version conflicts to manage.

## The three skill categories

Skills fall into three categories with deliberately different shapes:

### Setup utilities

`install-architect-playbook-locally`, `install-architect-playbook-globally`, `pre-audit-setup`, `worktree`. One-shot helpers that prepare a project (or the whole machine) for running audits. They have side effects (file copies, configuration merges, worktree creation) but those side effects are bounded and idempotent. Setup utilities do *not* follow the four-layer audit shape — that structure is reserved for audits.

### Audits

The thirteen `*-audit` skills plus the layer-2-onwards parts of `pre-audit-setup`'s knowledge-graph dependency model. Audits are read-only by default; they grade a codebase against an opinionated baseline and write findings to `.architect-audits/<audit-name>/`. Audits never mutate the codebase. They follow a strict canonical shape (see "The audit shape" below).

### Meta

`system-self-improve`. The only skill that mutates files outside its own `.architect-audits/` directory, and the only one with an `--apply` mode. Reads gap reports, locates affected SKILL.md files, proposes minimal reversible edits to the playbook so the same gap is more likely to be caught next time. Has its own structural conventions (four sequential stages with `outcome: advanced | blocked | skipped`) because it grades the playbook itself rather than a target codebase.

## The audit shape

Every audit body uses the same skeleton. Drift from this skeleton breaks the playbook's "read one audit, understand them all" property and is rejected by `/system-self-improve` as a prohibited mutation.

### Four layers plus a Layer 0 diagnostic snapshot

Layer 0 is informational only — a snapshot of the current state of the audited concern (counts, detected libraries, framework variants, threshold values used) that helps the human reading the report. It has no pass/fail status.

Layers 1 through 4 grade specific concerns within the audit's domain. Each layer is a table of checks; each check has an Expectation and a Violation signal. Layers are organised by audit-internal cohesion — for `/security-audit` they're attack classes, for `/performance-audit` they're runtime cost dimensions, for `/architecture-audit` they're invariant categories. Three layers can be skipped silently when a structural prerequisite is absent (e.g., Layer 3 of `/error-handling-audit` skips when React is not detected; Layer 4 of `/react-audit` skips on React below 18).

The decision to standardise on four layers plus Layer 0 is recorded in [ADR 0001](docs/decisions/0001-four-layer-baseline-with-layer-zero-snapshot.md).

### The four-status taxonomy

Every check resolves to one of:

- **present** — the invariant holds.
- **partial** — most signals resolve with exceptions, or the codebase shows mixed adherence to a soft check, or a tier-dependent check runs below its required tier.
- **missing** — a structural prerequisite is absent.
- **violation** — concrete code, configuration, or output breaks the invariant.

`/system-self-improve` substitutes `outcome: advanced | blocked | skipped` because it grades a process, not a target codebase.

### Two-phase flow

Phase 1: write findings, print summary. Phase 2: ask the user whether to generate an implementation plan; on yes, write `implementation-plan.md`; on no, exit cleanly.

No audit ever mutates the codebase. The implementation plan is descriptive Markdown that the user (or Claude in a follow-up chat) executes manually. The two-phase shape is what makes audits safe to run in any working tree at any time.

### Chat output shape

Default mode prints a concise summary: a short header, the Top 5 Highest-Leverage Recommendations (title, why it matters, consequences, smallest fix, lettered sub-actions), and a one-line pointer to the full report on disk. The full layered findings are never printed in the chat unless the user explicitly asks.

When `--learn` or `--teach` is set, each recommendation expands into mid-level engineer teaching mode: specific file references, line numbers, educational language ("Here's why this pattern bites teams…"), and a "What you'll learn from fixing this" section. The numbered/lettered structure is preserved so the user can still reply with "2b" or "1 and 3".

### Flag philosophy

Every audit has exactly three user-facing flags:

- (no flag) → Default: concise Top 5 + full report saved to disk + ask about plan
- `--learn` → Mid-level engineer teaching mode
- `--teach` → Alias for `--learn`

`--target=<path>` exists but is internal — only `/worktree` uses it. The old universal flags (`--report-only`, `--plan`, `--layer`, `--include`, `--exclude`) have been removed as redundant with the new default behavior. Audit-specific flags (`--with-*`, `--threshold-*`, `--pattern`, `--severity`, `--stats-path`, `--lighthouse-results-path`, `--security-critical-packages`) remain only where they add value.

### Static-first with optional enrichment

Every audit is fully static by default. Some audits offer opt-in flags that enrich the analysis with additional data:

- `--with-network` (`/dependency-audit`): runs the package manager's read-only audit and outdated commands.
- `--with-stats` (`/bundle-build-audit`): reads existing bundle-stats artefacts.
- `--with-run` (`/linting-audit`, `/testing-audit`, `/typescript-audit`): runs the linter/test runner/`tsc --noEmit` in read-only mode.
- `--with-lighthouse-results` (`/performance-audit`): reads existing Lighthouse JSON.
- `--with-link-check` (`/documentation-audit`): HEAD-requests external URLs.
- `--with-scan` (`/security-audit`): runs installed security scanners (`eslint-plugin-security`, Semgrep, etc.).

These flags have side effects (network requests, subprocess invocation) but never mutate the codebase. The static default keeps audits fast and safe; opt-in enrichment makes the trade-off explicit.

### Boundary discipline

Cross-cutting concerns (single state library, eslint plugins, error boundaries, jsx-a11y, useId for accessible IDs) surface in *every* audit they touch. The framing in the SKILL bodies is "single fix passes both" — a user closes the gap once and sees it disappear from all audits that surfaced it.

Three audits carry verbatim boundary tables that map who-owns-what across overlapping concerns: `/react-audit`, `/performance-audit`, `/typescript-audit`. These tables are the canonical reference. When a new audit risks overlapping with an existing one, the new audit must either claim the concern explicitly (and have the existing audits reference it) or defer to the existing audit (and surface the gap as a cross-reference).

## The findings-file contract

Audits, fixes, and reviews each run in different chat sessions. They cannot share in-memory state. The protocol between them is a deterministic on-disk shape:

```
.architect-audits/
  <audit-name>/
    findings.md              human-readable report
    findings.json            machine-readable list of issues
    snapshot.md              the Layer 0 diagnostic snapshot, on its own
    metadata.json            skill version, run timestamp, graphify revision, applied filters
    implementation-plan.md   only if the user agreed to generate one
```

`findings.md` always includes the snapshot at the top followed by check results grouped by layer. `findings.json` is structured for downstream tooling — its top-level shape is `{skillVersion, runStartedAt, runFinishedAt, snapshot, summary, checks}` with optional skill-specific fields.

**Chat output is human-first and concise.** Every audit prints a short header, the Top 5 Highest-Leverage Recommendations (title, why it matters, consequences, smallest fix, lettered sub-actions), and a one-line pointer to the full report on disk. The full layered findings are never printed in the chat unless the user explicitly asks. This keeps the chat scannable while the on-disk files carry the complete diagnostic.

The contract is what makes the multi-chat workflow tractable. A chat opened in a worktree to fix issues reads `findings.json`. A chat opened in another worktree to review the fix re-runs the originating audit and produces a new `findings.md` plus an optional `review-gap-report.md` if it found something the original audit missed.

## The self-improvement loop

The playbook is designed to evolve based on real review feedback rather than maintainer speculation:

1. The user runs an audit. It writes `findings.md`.
2. The user fixes the findings in the same chat. The fix lands as commits on a worktree branch.
3. The user opens a fresh chat against the worktree and re-runs the originating audit as a review pass. The re-run sees what the fix did and didn't address.
4. If the review surfaces a class of issue the original audit *should* have caught but didn't, the user writes `.architect-audits/<audit-name>/review-gap-report.md` describing the gap.
5. From inside the playbook clone, the user runs `/system-self-improve`. It reads the gap report, locates the affected SKILL.md, proposes a minimal reversible edit, asks for confirmation, and on `--apply` mutates the playbook.

The loop's safety properties are non-negotiable: dry-run by default, `--apply` always prompts for explicit confirmation (no `--yes` escape hatch), hard prohibitions against deleting checks or skills or changing the four-layer convention, append-only `system-self-improve-log.md`, never commits on its own.

## What the playbook deliberately is not

- **Not a CI tool.** Audits are designed to be run interactively from a chat, not from a continuous-integration pipeline. (Many of their findings could be enforced in CI, but that's the user's choice to wire up.)
- **Not a code-mod tool.** Audits never mutate the codebase. `/system-self-improve` mutates the *playbook* with consent.
- **Not a replacement for human review.** The audits catch structural patterns; nuanced trade-offs (when to lift state, whether a comment is good enough, whether a TODO is acceptable) remain a human responsibility.
- **Not a security or compliance certification.** `/security-audit` and `/dependency-audit` produce findings; they do not assert conformance to PCI-DSS, HIPAA, SOC 2, or any other framework. The audits' findings can support a compliance effort but are not themselves a compliance assessment.
- **Not a multi-language audit suite.** The current scope is TypeScript and React. Vue, Svelte, Angular, Solid, Python, Go, Rust, and Ruby are out of scope for v1; some audits could extend to other ecosystems, but that's a deliberate future decision rather than an accidental gap.

## Extending the playbook

When proposing a new audit or a substantive change to an existing one:

- If the change is grounded in a real gap surfaced by a review, prefer `/system-self-improve` to direct hand edits.
- If the change is a brand-new audit, follow the canonical body structure described in [CONTRIBUTING.md](CONTRIBUTING.md).
- If the change touches the four-layer convention, the status taxonomy, the two-phase flow, the Testing Philosophy, or any other shared convention, write an ADR in [docs/decisions/](docs/decisions/) explaining the rationale before making the change. These conventions are foundational; changing them affects every audit at once.
