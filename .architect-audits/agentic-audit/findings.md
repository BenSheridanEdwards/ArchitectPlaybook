# Dogfood static agentic audit findings

This artifact is a dogfood static review shaped like `/agentic-audit` output. No Claude Code slash command generated it, and no generated timestamp is claimed.

## Snapshot

- Reviewed revision: `634380d`.
- Primary instruction file: `CLAUDE.md`.
- Settings file: `.claude/settings.json`.
- Other agent instruction files detected: none.

## Top recommendations

1. **Keep agent guidance grounded in existing paths.**
   - Why it matters: agentic instructions are executed by coding agents that often trust project-local references.
   - Consequence: stale or placeholder-style path guidance can send an agent to missing files or cause it to create records in the wrong place.
   - Smallest fix: remove active links to absent decision-record paths from user-facing project documentation and record the gap in this review.

2. **Keep placeholder-style examples fenced as examples.**
   - Why it matters: examples such as `<skill-name>` are safe when clearly inside schema or usage examples, but risky when they look like real targets.
   - Consequence: agents can copy placeholders into commits or commands.
   - Smallest fix: preserve the placeholder examples in `CLAUDE.md` because they are explicitly fenced schema examples, and continue flagging placeholder-style links when they appear as active references.

3. **Preserve project-local settings hygiene.**
   - Why it matters: broad tool permissions or user-global settings writes create risk across unrelated projects.
   - Consequence: an audit skill could mutate the wrong environment.
   - Smallest fix: keep `.claude/settings.json` project-local, keep `.claude/settings.local.json` ignored, and keep `CLAUDE.md` prohibiting writes to `~/.claude/settings.json`.

## Layer 1 — Project context coverage

- Status: `present`.
- Evidence: `CLAUDE.md` states commit rules, naming rules, skill structure, audit behavior, cross-session handoff, self-improvement constraints, and path rules.

## Layer 2 — Operational guidance and conventions

- Status: `present`.
- Evidence: `CLAUDE.md` requires Conventional Commits, explicit staging, no hard-coded absolute paths, read-only audit behavior, and deterministic audit output files.

## Layer 3 — Claude Code settings hygiene

- Status: `present`.
- Evidence: `.claude/settings.json` allows read/search tools only and defines a project-local Graphify context hook. `.gitignore` excludes `.claude/settings.local.json`.

## Layer 4 — Multi-agent consistency and drift

- Status: `partial`.
- Evidence: no secondary agent instruction files are present, so there is no cross-file drift to compare. Documentation references to absent decision-record paths were the main stale-reference hygiene issue found during this review and were corrected in `README.md` and `ARCHITECTURE.md`.

## Changed by this review

- Added this dogfood static-review artifact set under `.architect-audits/agentic-audit/`.
- Recorded that placeholder-style strings in `CLAUDE.md` are examples, not active links.
- Recorded that the main agentic hygiene gap was stale path guidance inherited through project documentation rather than unsafe Claude Code settings.
