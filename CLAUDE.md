# Project rules for the architect-playbook

Read [AGENTS.md](AGENTS.md) first — this file adds only the Claude-specific
layer on top of it. AGENTS.md is the tool-agnostic contract: what the repo is,
how to run the gates, the operating rules, and the truth rules. The commit,
naming, and skill-authoring conventions the validator enforces live in
[.agents/CONVENTIONS.md](.agents/CONVENTIONS.md). The gate matrix lives in
[.agents/QUALITY_GATES.md](.agents/QUALITY_GATES.md); the pull-request contract
lives in [.agents/PR_QUALITY.md](.agents/PR_QUALITY.md).

The rules below govern how a Claude Code session authors and runs the skills in
this repository. If a rule and a skill body disagree, the rule wins and the
skill body should be patched (run `/system-self-improve` if appropriate).

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
- **`checks.json` mirrors the layers.** When an audit ships a `checks.json` beside its `SKILL.md`, every check's `layer` must match a layer (or lifecycle stage) heading in the body, and the file must stay aligned whenever a check is added, removed, renamed, or moved. The validator enforces this.

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

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **ArchitectPlaybook** (675 symbols, 754 relationships, 8 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/ArchitectPlaybook/context` | Codebase overview, check index freshness |
| `gitnexus://repo/ArchitectPlaybook/clusters` | All functional areas |
| `gitnexus://repo/ArchitectPlaybook/processes` | All execution flows |
| `gitnexus://repo/ArchitectPlaybook/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
