---
name: pre-audit-setup
description: One-time, idempotent preparation that every architect-playbook audit depends on. Verifies graphify is installed, builds the project knowledge graph, and merges the graphify-aware PreToolUse hook into the project's .claude/settings.json.
trigger: /pre-audit-setup
---

# /pre-audit-setup

Idempotent preparation that every audit in the architect-playbook expects to have been run at least once in the target project. Run this immediately after installing the skills.

## Usage

```
/pre-audit-setup                 # do everything that has not been done yet
/pre-audit-setup --force         # rebuild the knowledge graph and rewrite the hook even if both already exist
/pre-audit-setup --dry-run       # describe what would change without modifying anything
```

## What this skill does

1. **Verifies graphify is installed at `~/.claude/skills/graphify`.** This skill does not install graphify. If it is missing, stop and tell the user where to install it (see [https://graphify.net/graphify-claude-code-integration.html](https://graphify.net/graphify-claude-code-integration.html)). Do not invent a clone URL.
2. **Builds the project knowledge graph** by invoking `/graphify .` from the current working directory. Subsequent audits read `graphify-out/graph.json` and `graphify-out/GRAPH_REPORT.md` as their map of the codebase.
3. **Merges the graphify-aware PreToolUse hook** into the project-local `.claude/settings.json` (creating the file if needed). The hook attaches a hint to every Glob and Grep call: when the knowledge graph exists, Claude is reminded to read the report before searching raw files.
4. **Creates the `.architect-audits/` directory** that downstream audits will write findings into. Adds a single `.gitkeep` so it survives `git clean`.
5. **Prints a checklist** describing what was done, what was already in place, and what the user should verify before launching audits.

## Implementation steps

Follow these steps in order. Stop at the first hard failure and report.

### Step 1 — Verify graphify

```bash
test -f "$HOME/.claude/skills/graphify/SKILL.md" \
  && echo "graphify: present" \
  || { echo "graphify: missing — install it first (see https://graphify.net/graphify-claude-code-integration.html)"; exit 1; }
```

If graphify is missing, stop and surface the message above. Do not attempt to download anything.

### Step 2 — Detect prior runs

```bash
test -f graphify-out/graph.json && echo "knowledge graph: present" || echo "knowledge graph: missing"
test -f .claude/settings.json   && echo "settings.json: present"  || echo "settings.json: missing"
```

If both the knowledge graph and the hook are already present, and `--force` was not passed, print the checklist and exit cleanly. The skill is idempotent.

### Step 3 — Build the knowledge graph

If `graphify-out/graph.json` is missing or `--force` was passed, run:

```
/graphify .
```

(Invoke the graphify slash command — do not call it as a shell command.) Wait for it to finish before moving on.

### Step 4 — Merge the PreToolUse hook into `.claude/settings.json`

The hook to merge is exactly:

```json
{
  "matcher": "Glob|Grep",
  "hooks": [
    {
      "type": "command",
      "command": "[ -f graphify-out/graph.json ] && echo '{\"hookSpecificOutput\":{\"hookEventName\":\"PreToolUse\",\"additionalContext\":\"graphify: Knowledge graph exists. Read graphify-out/GRAPH_REPORT.md for god nodes and community structure before searching raw files.\"}}' || true"
    }
  ]
}
```

Merge rules (in order):

1. If `.claude/settings.json` does not exist, create it with the structure:
   ```json
   { "hooks": { "PreToolUse": [ <the hook above> ] } }
   ```
2. If it exists but has no `hooks` key, add `hooks: { PreToolUse: [<the hook above>] }`.
3. If it has `hooks.PreToolUse`, append the hook **only if** no existing entry has both the same `matcher` and an identical `command` string. Never duplicate.
4. Preserve every other field in `.claude/settings.json` exactly as it was.
5. Use `jq` if it is on PATH; otherwise read the file with the Read tool, transform it in memory, and write it back with the Write tool.

After merging, print the final hook block so the user can confirm.

### Step 5 — Ensure the `.architect-audits/` directory exists

```bash
mkdir -p .architect-audits
[ -f .architect-audits/.gitkeep ] || touch .architect-audits/.gitkeep
```

### Step 6 — Print the checklist

Output a short report with one line per item, each prefixed with `[done]`, `[skipped]`, or `[already]`:

```
[done]    graphify presence verified
[done]    graphify-out/graph.json built (53 files indexed)
[done]    PreToolUse hook merged into .claude/settings.json
[done]    .architect-audits/ directory created
[hint]    next: open one chat per audit slash command and run them in parallel
```

## Idempotency rules

- Never duplicate the hook entry. The check is `matcher` plus exact `command` string match.
- Never overwrite the existing knowledge graph unless `--force` was passed.
- Never modify any setting other than `hooks.PreToolUse`.
- Never touch `~/.claude/settings.json`. This skill is project-local only.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `graphify: missing` | graphify is not installed at `~/.claude/skills/graphify` | Install graphify first; see the integration page above. |
| `/graphify .` fails | graphify-side problem | Read graphify's own error output. This skill does not retry. |
| Settings file is invalid JSON | Pre-existing corruption in `.claude/settings.json` | Stop and ask the user to fix or move the file. Never silently overwrite. |

## When to run again

Re-run `/pre-audit-setup` after:

- A major refactor (the knowledge graph has gone stale).
- Pulling a branch that significantly changes file layout.
- Adding new top-level packages or workspaces.

Use `--force` when re-running to rebuild the graph and re-merge the hook.

## What this skill explicitly does NOT do

- Install graphify itself.
- Modify global Claude Code settings.
- Run any audit. Audits are separate skills and are intended to run in their own chat sessions.
- Commit anything to git. Commit changes manually, with a Conventional Commits message such as `chore: add graphify pre-tool-use hook to settings.json`.
