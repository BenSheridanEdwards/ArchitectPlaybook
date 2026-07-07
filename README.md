# Architect Playbook

A self-contained, self-improving collection of Claude Code slash-command skills for auditing any codebase. Walk onto a project, install the skills, run multiple audits in parallel from separate chat sessions with `--worktree`, fix what they find, review the fixes in a Git worktree, and let `/system-self-improve` patch the audits themselves whenever a review surfaces a gap.

## Audit types

- **Start:** **[Pre-Audit Setup](pre-audit-setup/SKILL.md)** — map the repo before judging it.
- **Ship:** **[Quality Gates](quality-gates-audit/SKILL.md)** — pre-hooks and release QA · **[Bundle and Build Health](bundle-build-audit/SKILL.md)** — build output and artifacts.
- **Risk:** **[Security](security-audit/SKILL.md)** — app and browser security · **[Dependency Health](dependency-audit/SKILL.md)** — package and lockfile risk.
- **Experience:** **[Accessibility](accessibility-audit/SKILL.md)** — WCAG and usability · **[Performance](performance-audit/SKILL.md)** — speed and rendering cost · **[Error Handling](error-handling-audit/SKILL.md)** — failure states and recovery.
- **Code:** **[Architecture](architecture-audit/SKILL.md)** — boundaries and coupling · **[Testing](testing-audit/SKILL.md)** — behavior coverage · **[React](react-audit/SKILL.md)** — components and hooks · **[TypeScript](typescript-audit/SKILL.md)** — type safety · **[Linting](linting-audit/SKILL.md)** — rule coverage.
- **Review:** **[Ben Architect Review](ben-architect-review/SKILL.md)** — principles-based architectural pull request reviews using Ben's judgement.
- **Knowledge:** **[Documentation](documentation-audit/SKILL.md)** — docs and drift · **[Agentic Setup](agentic-audit/SKILL.md)** — agent instructions and safety rails.

## Table of contents

- [Core principles](#core-principles)
- [The workflow](#the-workflow)
- [Flags](#flags)
- [The findings-file contract](#the-findings-file-contract)
- [How audits grade issues](#how-audits-grade-issues)
- [The full skill list](#the-full-skill-list)
  - [Setup utilities](#setup-utilities)
  - [Audits](#audits)
  - [Review](#review)
  - [Meta](#meta)
- [Conventions](#conventions)
- [Contributing](#contributing)
- [License](#license)
- [Related](#related)

## Core principles

- **Opinionated baselines.** Each audit grades against a specific opinionated baseline, not whatever happens to be in the codebase.
- **Static-first with optional enrichment.** Every audit is read-only by default. Runtime or network data is opt-in via explicit `--with-*` flags.
- **Two-phase flow.** Every audit reports findings, then asks whether to generate an implementation plan. Mutation is never automatic.
- **Boundary discipline.** Cross-cutting concerns are explicitly called out.
- **Self-improving.** When a review surfaces a gap, `/system-self-improve` patches the originating audit.

## The workflow

1. **Get the playbook and install the skills.**
   ```bash
   git clone <this-repository> ~/architect-playbook
   cd ~/architect-playbook
   claude       # open this directory in Claude Code
   ```
   Then, in the Claude Code chat:
   ```
   /install-architect-playbook-globally
   ```
   That's it. Every audit slash command is now available in every Claude Code session on the machine. The clone ships with `.claude/skills/install-architect-playbook-globally` already committed so this one bootstrap command is available the moment you open the cloned repo — no manual copy step needed.

   *(Optional, for teams: once you `cd` into a target project, you can also run `/install-architect-playbook-locally` to pin the skills alongside that project in version control. Most users don't need this.)*

2. **Prepare the project.**
   ```
   /pre-audit-setup
   ```

3. **Run audits.**
   ```
   /security-audit              # concise Top 5 recommendations + full report saved to disk
   /security-audit --worktree   # run in an isolated worktree (recommended for parallel audits)
   ```
   Open multiple chats and add `--worktree` in each for true parallel execution.

4. **Fix the findings** in the same chat that produced them.

5. **Re-run the audit** to review the fix (best done in a fresh chat or worktree).

6. **Evolve the playbook** if a gap is found:
   ```
   /system-self-improve
   ```

### Recommended audit order

Audits are independent, but they are not equally urgent, and some read context
the others depend on. This is the playbook's own recommended order. Phases run in
sequence; audits **within** a phase are independent and can run in parallel, one
worktree each (`--worktree`).

| Phase | Audits | Why here |
| --- | --- | --- |
| 0 — Prepare | `/install-architect-playbook-globally`, `/pre-audit-setup` | Install the skills and build the knowledge graph that everything downstream reads. |
| 1 — Base plate | `/agentic-audit`, `/quality-gates-audit` | Establish whether the context layer is truthful and the gates are real before trusting anything else. |
| 2 — Behaviour floor | `/testing-audit` | The safety net. Nothing else is safe to change until you know what the tests actually cover. |
| 3 — Structure | `/architecture-audit` | Fix boundaries and coupling before polishing the code that sits inside them. |
| 4 — Maintainability | `/typescript-audit`, `/react-audit`, `/linting-audit` | Make the code agents read and imitate consistent. |
| 5 — Risk | `/security-audit`, `/dependency-audit` | Close the vulnerabilities and supply-chain gaps. |
| 6 — Non-functionals | `/performance-audit`, `/accessibility-audit`, `/error-handling-audit`, `/bundle-build-audit` | Optimise last, once behaviour and structure have settled. |
| 7 — Knowledge | `/documentation-audit` | Detect drift once reality has stopped moving. |
| 8 — Review and loop | `/ben-architect-review` → re-run any failed audits → `/system-self-improve` | Review the changes, close the remaining gaps, and evolve the audits that missed something. |

## Flags

Every audit supports the same minimal set:

```
/<skill-name>                    # default: concise Top 5 + full report saved + ask about plan
/<skill-name> --worktree         # create an isolated Git worktree, then run the audit there
/<skill-name> --learn            # engineer teaching mode
/<skill-name> --teach            # alias for --learn
```

Some audits expose additional enrichment or threshold flags (`--with-*`, `--threshold-*`). See the individual `SKILL.md` for details.

**Pro tip**: Add `--worktree` to any audit to run it in an isolated Git worktree.

## Check metadata

Each audit's `SKILL.md` remains the human canonical source for the baseline, narrative guidance, and implementation steps. Audits may also include `checks.json` beside `SKILL.md`; that file is a machine-readable inventory of the checks so validation tools and generated maps can read check identifiers, layers, titles, expectations, violation signals, status values, related audits, and optional enrichment flags without scraping Markdown tables.

Keep `checks.json` aligned with `SKILL.md` whenever a check is added, removed, renamed, or moved between layers. The JSON is supporting metadata, not a replacement for the human-readable audit body.

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
- **Fixing** is a free-form chat conversation in the same chat that produced the audit.
- **Reviewing** is re-running the originating audit in a fresh chat against the worktree containing the fix.
- **`/system-self-improve`** reads a review's gap report and proposes an edit to the originating audit's `SKILL.md`.

Current dogfood reports for this repository are committed under `.architect-audits/` for pre-audit setup, quality gates, testing, and architecture. Each report includes `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` so downstream sessions can consume the same contract they expect from target projects.

## How audits grade issues

The full detailed report saved to `.architect-audits/<audit-name>/findings.md` grades every check with one of four statuses:

- **present** — the expected invariant is fully satisfied.
- **partial** — mostly correct, but with noticeable gaps or inconsistencies.
- **missing** — a required foundation (tooling, pattern, or configuration) is absent.
- **violation** — concrete code actively breaks the invariant.

The concise Top 5 recommendations you see by default in chat focus on **missing** and **violation** items (the highest-impact issues), plus the most important **partial** findings. The full report on disk shows the complete status for every check.

`/system-self-improve` uses a different outcome model (`advanced | blocked | skipped`).

## The full skill list

### Setup utilities

| Trigger | Purpose |
| --- | --- |
| [`/install-architect-playbook-locally`](install-architect-playbook-locally/SKILL.md) | Copy every playbook skill into the current project's `.claude/skills/`. |
| [`/install-architect-playbook-globally`](install-architect-playbook-globally/SKILL.md) | Copy every playbook skill into `~/.claude/skills/`. |
| [`/pre-audit-setup`](pre-audit-setup/SKILL.md) | Verify graphify, build the knowledge graph, merge the PreToolUse hook. |
| [`/preflight`](preflight/SKILL.md) | Detect optional enrichment tooling for `--with-*` flags. |

### Audits

Every audit accepts the universal `--worktree`, `--learn`, and `--teach` flags. Use `--worktree` for parallel runs in isolated Git worktrees; use `--learn` or `--teach` for engineer teaching mode. Per-audit flags below add optional enrichment on top of the static audit.

#### Release readiness

- **[Quality Gates](quality-gates-audit/SKILL.md)** (`/quality-gates-audit`) — pre-commit, pre-push, Continuous Integration, and release gates.
- **[Bundle and Build Health](bundle-build-audit/SKILL.md)** (`/bundle-build-audit`) — build pipeline, bundle shape, generated output, and release artifact hygiene. Optional: `--with-stats`, `--stats-path`.

#### Risk and dependency health

- **[Security](security-audit/SKILL.md)** (`/security-audit`) — authentication, authorization, Cross-Site Scripting, headers, secrets, and browser security. Optional: `--with-scan`.
- **[Dependency Health](dependency-audit/SKILL.md)** (`/dependency-audit`) — package security, dependency maintenance, lockfile hygiene, and update risk. Optional: `--with-network`.

#### Product experience

- **[Accessibility](accessibility-audit/SKILL.md)** (`/accessibility-audit`) — Web Content Accessibility Guidelines 2.2 AA coverage across tooling, components, and the application shell. Optional: `--severity=error`.
- **[Performance](performance-audit/SKILL.md)** (`/performance-audit`) — runtime cost, Core Web Vitals, rendering pressure, and user-perceived speed. Optional: `--with-lighthouse-results`, `--lighthouse-results-path`.
- **[Error Handling](error-handling-audit/SKILL.md)** (`/error-handling-audit`) — error boundaries, failure paths, observability, recovery, and user-facing failure states.

#### Code quality

- **[Architecture](architecture-audit/SKILL.md)** (`/architecture-audit`) — module boundaries, coupling, layering, ownership, and architectural conventions. Optional: `--pattern`.
- **[Testing](testing-audit/SKILL.md)** (`/testing-audit`) — test strategy, Testing Library behavior focus, coverage quality, and brittle tests. Optional: `--with-run`.
- **[React](react-audit/SKILL.md)** (`/react-audit`) — idiomatic React, component boundaries, state ownership, hooks, and React 19 patterns.
- **[TypeScript](typescript-audit/SKILL.md)** (`/typescript-audit`) — type system strength, strictness, unsafe escape hatches, and input/output validation. Optional: `--with-run`.
- **[Linting](linting-audit/SKILL.md)** (`/linting-audit`) — linting configuration quality and whether formatting and lint rules protect the codebase. Optional: `--with-run`.

#### Project knowledge and agent safety

- **[Documentation](documentation-audit/SKILL.md)** (`/documentation-audit`) — documentation quality, onboarding clarity, architecture notes, and drift from implementation. Optional: `--with-link-check`.
- **[Agentic Setup](agentic-audit/SKILL.md)** (`/agentic-audit`) — agent instruction files, Claude settings, repository automation guidance, and agent safety rails.

#### Flags by audit

All audits support the universal `--worktree`, `--learn`, and `--teach` flags. Use the table below to see which audits add their own enrichment flags.

| Audit | Universal flags | Additional flags |
| --- | --- | --- |
| [Quality Gates](quality-gates-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | — |
| [Bundle and Build Health](bundle-build-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--with-stats`, `--stats-path` |
| [Security](security-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--with-scan` |
| [Dependency Health](dependency-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--with-network` |
| [Accessibility](accessibility-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--severity=error` |
| [Performance](performance-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--with-lighthouse-results`, `--lighthouse-results-path` |
| [Error Handling](error-handling-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | — |
| [Architecture](architecture-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--pattern` |
| [Testing](testing-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--with-run` |
| [React](react-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | — |
| [TypeScript](typescript-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--with-run` |
| [Linting](linting-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--with-run` |
| [Documentation](documentation-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | `--with-link-check` |
| [Agentic Setup](agentic-audit/SKILL.md) | `--worktree`, `--learn`, `--teach` | — |

### Review

| Trigger | Purpose |
| --- | --- |
| [`/ben-architect-review`](ben-architect-review/SKILL.md) | Perform a principles-based architectural pull request review using Ben's judgement, then ask whether to post it as pending or submit it on GitHub. |

### Meta

| Trigger | Purpose |
| --- | --- |
| [`/system-self-improve`](system-self-improve/SKILL.md) | Read gap reports and evolve the playbook itself. |

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
- **Run the repository gates:** `python3 scripts/validate-playbook.py` before every pull request. For local commit and push hooks, run `python3 scripts/install-git-hooks.py` once per clone.

The architectural intent behind the playbook's conventions lives in [ARCHITECTURE.md](ARCHITECTURE.md). Foundational decisions are recorded as ADRs in [docs/decisions/](docs/decisions/).

## License

[MIT](LICENSE).

## Related

- [graphify](https://graphify.net/graphify-claude-code-integration.html) — the knowledge-graph skill that `/pre-audit-setup` assumes is installed at `~/.claude/skills/graphify`.
