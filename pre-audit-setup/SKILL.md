---
name: pre-audit-setup
description: One-time, idempotent preparation before audits. Verifies graphify is installed and builds the project knowledge graph that audits read for orientation, without changing settings or tracked files.
disable-model-invocation: true
argument-hint: "[--force] [--dry-run]"
---

# /pre-audit-setup

Optional, idempotent preparation before running audits. It builds the graphify knowledge graph, which audits read to orient themselves: god nodes, communities, and the files worth reading first. No audit requires the graph. Without it, a run is still official; only a check that falls back to a less precise method is recorded as degraded.

The setup leaves the working tree clean for scoring. `graphify-out/` is generated tooling output, so the audit protocol and the score calculator leave it out of source cleanliness, as they do `.architect-audits/` (Architecture Decision Record 0005). The setup changes no settings file and no `CLAUDE.md`, and changes committed files only when you confirm a rebuild of a committed `graphify-out/`.

## Usage

```
/pre-audit-setup                 # build the knowledge graph if it is missing
/pre-audit-setup --force         # rebuild the knowledge graph even if it exists
/pre-audit-setup --dry-run       # describe what would change without modifying anything
```

## What this skill does

1. **Creates the `.architect-audits/` directory** that audits publish into, at the repository root.
2. **Verifies the graphify skill is installed**, because the graph is built with its `/graphify` slash command. The `graphify` command-line tool alone is not enough; `graphify install` adds the skill. This skill does not install anything. If graphify is missing, it points the user to [https://graphify.net/graphify-claude-code-integration.html](https://graphify.net/graphify-claude-code-integration.html).
3. **Builds the project knowledge graph** by invoking `/graphify .` from the repository root. Audits read `graphify-out/GRAPH_REPORT.md` and `graphify-out/graph.json`. If `graphify-out/` is committed or is a symbolic link, it asks before rebuilding.
4. **Prints a checklist** of what was done, what was already in place, and what to do next.

## Implementation steps

Follow these steps in order. Stop at the first hard failure and report it.

### Step 1 — Work from the repository root and create the output folder

```bash
cd "$(git rev-parse --show-toplevel)"
```

Create `.architect-audits/` with `mkdir -p .architect-audits`, except with `--dry-run`, which only reports that it would.

Run every later step from the root. A graph built in a subfolder would be a stray, uncommitted `graphify-out/` that counts as source.

### Step 2 — Verify the graphify skill

```bash
if test -f "$HOME/.claude/skills/graphify/SKILL.md" || test -f .claude/skills/graphify/SKILL.md; then
  echo "graphify: present"
elif command -v graphify >/dev/null 2>&1; then
  echo "graphify: command-line tool found, but not its Claude Code skill; run: graphify install"
  exit 1
else
  echo "graphify: missing — see https://graphify.net/graphify-claude-code-integration.html"
  exit 1
fi
```

If graphify is missing, stop and show the message. Do not install anything. Audits still run without the graph.

### Step 3 — Decide whether to build

```bash
test -f graphify-out/graph.json && echo "knowledge graph: present" || echo "knowledge graph: missing"
test -L graphify-out && echo "graphify-out: symbolic link"
git ls-files --error-unmatch graphify-out >/dev/null 2>&1 && echo "graphify-out: committed"
```

- If the graph is present and `--force` was not passed, print the checklist and stop.
- With `--dry-run`, print what Step 4 would do and stop.
- If `graphify-out/` is committed or is a symbolic link, say that rebuilding would change committed files or write through the link, and continue only if the user confirms.

### Step 4 — Build the knowledge graph

Invoke the graphify slash command from the repository root, and wait for it to finish:

```
/graphify .
```

Do not run graphify's own installer (`graphify claude install`) from this skill. That command edits `CLAUDE.md` and `.claude/settings.json`, which are the user's to decide.

### Step 5 — Print the checklist

One line per item, each prefixed with `[done]`, `[already]`, or `[hint]`:

```
[done]    graphify presence verified
[done]    graphify-out/graph.json built
[done]    .architect-audits/ directory created
[hint]    next: run an audit, for example /architecture-audit; add --worktree to run several at once
```

## Idempotency rules

- Never overwrite an existing knowledge graph unless `--force` was passed.
- Never write to `.claude/settings.json`, `.claude/settings.local.json`, `~/.claude/settings.json`, or `CLAUDE.md`.
- Never change committed files without the user's confirmation.
- Never stage, commit, or ignore anything in Git.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `graphify: missing` | graphify is not installed | Install it from the integration page above, or run audits without the graph. |
| `command-line tool found, but not its Claude Code skill` | Only the Python package is installed | Run `graphify install`, then run this skill again. |
| `/graphify .` fails | A graphify-side problem | Read graphify's own error output. This skill does not retry. |
| A `--worktree` audit has no graph | `graphify-out/` is not copied into new worktrees | The run protocol copies the graph when the worktree is at the same commit; otherwise run `/pre-audit-setup` inside the worktree. |

## When to run again

Re-run `/pre-audit-setup --force` after a major refactor, after pulling a branch that changes the file layout significantly, or after adding packages or workspaces.

## What this skill explicitly does NOT do

- Install graphify, or run its installer.
- Modify any settings file, `CLAUDE.md`, or tracked file.
- Run any audit.
- Commit anything to Git.
