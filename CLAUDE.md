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
- **`--target=<path>` is internal only.** It is not documented in the Usage table. Only `/worktree` uses it to redirect audits to a worktree directory.
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
