# Agent Operating Rules

Source: https://x.com/mikenevermiss/status/2068197417506222428?s=46

## Behavioural Rules

### Think Before Coding
State assumptions before writing code. Surface tradeoffs. Ask before guessing on architecture, data shape, security, or irreversible changes. Push back when a simpler approach exists.

### Simplicity First
Write the minimum code that solves the task. No speculative features. No abstractions for single-use code. If the solution looks over-engineered, simplify it.

### Surgical Changes
Touch only what the task requires. Do not reformat, rename, or refactor adjacent code unless it is required for the change. Match existing style.

### Goal-Driven Execution
Define success before implementation. Keep working until that definition is met and verified. Do not ask for step-by-step instructions when the path can be inferred.

### Deterministic Control Flow
Do not use model calls for deterministic decisions. Routing, retries, status checks, thresholds, and branching rules belong in code.

### Hard Token Budgets
For long tasks, set a clear investigation budget. If the budget is reached without a verified solution, write findings and next steps to `PROGRESS.md` and stop cleanly.

### One Agent, One Directory
Parallel agents must use separate git worktrees or directories. No two agents should mutate the same checkout at the same time.

### Checkpoint Multi-Step Work
For tasks longer than three steps, create or update `PROGRESS.md` with: completed work, findings, next action, blockers, and verification status.

### Fail Loudly
If a command, test, build, hook, or assumption fails, report the exact failure. Do not relabel partial success as done. Passing tests only count when they cover the changed behavior.

### Unique Skill Descriptions
Each skill must describe exactly one job. If two skills could be selected for the same reason, rename or split them before relying on them.

### Separate Research From Implementation
If a task needs broad reading or multiple source lookups, do research first and produce a compact report. Start implementation from that report, not from a sprawling context window.

### Scoped Hooks Only
Hooks must have explicit scope: file extension, path, command, or session event. Avoid unconditional hooks on every tool call. Batch logging to session end where possible.

## What Not To Touch Without Explicit Approval

- Secrets, credentials, tokens, keys, and local environment files.
- Production configuration, deployment settings, billing, permissions, and security controls.
- Generated artifacts or snapshots unless the task explicitly requires updating them.
- Public publishing, releases, package publication, or external messaging.
- Destructive git operations including branch deletion, force-push, and history rewrite.

## Default Success Criteria

A task is not done until the changed behavior is verified by deterministic evidence: tests, build output, typecheck/lint results, screenshots/video for UI behavior, or another concrete artifact that does not depend on the model's judgment.

---

# Project rules for the architect-playbook

These rules apply to every Claude Code session that opens this repository, and to every skill authored inside it. They are intentionally short. If a rule and a skill body disagree, the rule wins and the skill body should be patched (run `/system-self-improve` if appropriate).

## Commits

- **Use Conventional Commits for every commit.** Subject lines use the imperative present tense.
- Common types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`, `build`, `ci`.
- Commit subjects must be machine-parseable. No free-form subjects, no skipping the type prefix.
- Stage files explicitly. Do not use `git add -A` or `git add .` in committed examples or recommendations.

## Naming and language

- **No abbreviations.** Spell every word out.
  - Correct: Documentation, Performance, Accessibility, Dependency, Configuration, Authentication, Repository, Authorization, Internationalization.
  - Incorrect: Documentation → docs, Performance → perf, Accessibility → a11y, Dependency → dep, Configuration → config (in prose), Authentication → auth, Repository → repo.
- Skill folder names match their slash-command trigger. The trigger field in the YAML frontmatter equals `/<folder-name>`.
- Filenames may keep their canonical ecosystem form (`.gitignore`, `tsconfig.json`, `package.json`). The no-abbreviations rule applies to prose, identifiers, headings, and triggers.

## Skill structure

Every `SKILL.md` in this repository starts with this YAML frontmatter:

```yaml
---
name: <folder-name>
description: <one-line description used by the Claude Code skill matcher>
trigger: /<folder-name>
---
```

After the frontmatter, the body must include at minimum:

1. A short purpose paragraph.
2. A `## Usage` section showing the slash command and its flags.
3. A `## What this skill does` section.
4. A `## Implementation steps` section, in order.
5. A `## What this skill explicitly does NOT do` section. Boundaries matter for parallel sessions.

Stubs are an explicit exception. A stub contains only the frontmatter, a placeholder heading, and a `**Status:** stub` notice.

## Audit behavior

- **Read-only by default.** No skill writes to the codebase outside of `.architect-audits/<skill-name>/` unless the user passes `--apply`.
- A mutating run prints a dry-run summary first and waits for confirmation.
- **Chat output is human-first and concise.** Every audit prints a short header, the Top 5 Highest-Leverage Recommendations (title, why it matters, consequences, smallest fix, lettered sub-actions), and a one-line pointer to the full report on disk. The full layered findings, snapshot, metadata, and implementation plan are always written to `.architect-audits/<skill-name>/` but never printed in the chat unless the user explicitly asks.
- **`--learn` or `--teach` expands output into teaching mode.** When set, each recommendation is explained as if teaching an engineer, with specific file references, line numbers, educational language ("Here's why this pattern bites teams…"), and a "What you'll learn from fixing this" section. The numbered/lettered structure is preserved so the user can still reply with "2b" or "1 and 3".
- **`--worktree` is the user-facing worktree control.** Worktrees are a flag on each audit, not a separate slash command. When passed, the audit creates or reuses `../wt-<audit-slug>` on branch `wt-<audit-slug>`, then runs the same audit against that checkout.
- **`--target=<path>` is internal only.** It is not documented in the Usage table. Audit skills use it internally when the user passes `--worktree` to redirect the audit to an isolated Git worktree directory.
- Every audit writes four files to disk:
  - `.architect-audits/<skill-name>/findings.md` — human-readable report.
  - `.architect-audits/<skill-name>/findings.json` — machine-readable for downstream skills.
  - `.architect-audits/<skill-name>/snapshot.md` — diagnostic snapshot.
  - `.architect-audits/<skill-name>/metadata.json` — skill version, run timestamp, graphify revision hash.

## Cross-session handoff

Audits, fixes, and reviews each run in different Claude Code chat sessions. They cannot share in-memory state. The on-disk findings files are the protocol.

- A fix skill reads `.architect-audits/<skill-name>/findings.json` from the same project.
- A review skill reads the audit's findings plus the diff produced by the fix.
- `/system-self-improve` reads the review's gap report and rewrites the originating audit's `SKILL.md` so the same gap is caught next time.

## Self-improvement

- Never silently rewrite a skill body. The only path to evolving a skill is `/system-self-improve`, invoked by the user, with a review gap report as input.
- When a skill's `SKILL.md` is edited (by hand or by `/system-self-improve`), the README skill index must be updated in the same commit.

## Paths

- Never hard-code absolute paths in skill bodies.
- Derive from the current working directory or `$HOME`.
- Never write to `~/.claude/settings.json` from any skill in this playbook. Project-local settings only.
