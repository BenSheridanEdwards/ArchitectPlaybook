---
name: install-architect-playbook-globally
description: Copy every architect-playbook skill into ~/.claude/skills/ so the slash commands are available in every Claude Code session on this machine.
disable-model-invocation: true
argument-hint: "[--dry-run] [--force] [--from=<path>] [--include=<skill>] [--exclude=<skill>]"
---

# /install-architect-playbook-globally

Copy every skill folder from this architect-playbook repository into `~/.claude/skills/`. After running this, every architect-playbook slash command works in every Claude Code session on the machine, regardless of which project is open.

This skill is the machine-wide counterpart to `/install-architect-playbook-locally`. The mechanics are the same; only the destination differs.

The plugin is the recommended install (`/plugin install architect-playbook@architect-playbook`; see the README). It updates with the repository and leaves `~/.claude/skills/` alone. If you install the plugin later, remove these copies: a copied skill owns the bare command name.

## Usage

```
/install-architect-playbook-globally                 # install or update every skill into ~/.claude/skills/
/install-architect-playbook-globally --dry-run       # print the plan without copying anything
/install-architect-playbook-globally --force         # replace changed skills without asking first
/install-architect-playbook-globally --from=<path>   # install from this playbook clone
/install-architect-playbook-globally --include=name  # install only the named skill and what it needs (repeatable)
/install-architect-playbook-globally --exclude=name  # skip a named skill (repeatable)
```

## When to choose this over the local install

Use this skill when:

- You audit lots of different codebases and you do not want to re-install per project.
- You want a single canonical version of every skill on disk.

Prefer `/install-architect-playbook-locally` when:

- You want different projects to pin different versions of the playbook.
- You want the installed skills to live alongside the project in version control.

## What this skill does

1. **Resolves the playbook root** the same way `/install-architect-playbook-locally` does.
2. **Resolves the destination** as `$HOME/.claude/skills/`. Creates it if missing.
3. **Enumerates source skills** (every sub-folder with a `SKILL.md` except the two installers), filtered and closed over dependencies.
4. **Asks for confirmation** before replacing any existing skill in `~/.claude/skills/`, unless `--force` is passed. Global installs affect every Claude Code session on the machine.
5. **Replaces each changed skill folder as a whole**, exactly as the local installer does, and leaves identical folders and folders it does not own alone.
6. **Reports the result** with the same format as the local installer.

## Implementation steps

### Step 1 — Resolve the playbook root

Use `--from=<path>` if given. Otherwise derive the root from `${CLAUDE_SKILL_DIR}`, this skill's folder. In a clone that is `<playbook-root>/install-architect-playbook-globally`, or, for the bootstrap copy, `<playbook-root>/.claude/skills/install-architect-playbook-globally`:

```bash
if [ -n "${FROM:-}" ]; then
  set -- "$FROM"                          # an explicit --from must be valid; never fall back
else
  set -- "${CLAUDE_SKILL_DIR:-.}/.." "${CLAUDE_SKILL_DIR:-.}/../../.."
fi
PLAYBOOK_ROOT=""
for candidate in "$@"; do
  if [ -f "$candidate/scripts/install_playbook.py" ] && git -C "$candidate" rev-parse --show-toplevel >/dev/null 2>&1; then
    PLAYBOOK_ROOT="$(cd "$candidate" && pwd)"; break
  fi
done
[ -n "$PLAYBOOK_ROOT" ] || { echo "Not a playbook clone: ${FROM:-the folder this skill runs from}. Re-run with --from=<path to your clone>."; exit 1; }
```

`FROM` is the value of `--from`. A Git worktree of the playbook counts as a clone. The script re-checks that the root is a Git checkout of the playbook.

Refuse to proceed if `$HOME` is unset or empty.

### Step 2 — Plan

```bash
python3 "$PLAYBOOK_ROOT/scripts/install_playbook.py" --destination "$HOME/.claude/skills" [--include <skill>]... [--exclude <skill>]...
```

The script selects the skills, closes the selection over dependencies (Step 3 explains the rules), compares each skill with the destination by a digest of file names, contents, and symbolic links, and prints one line per skill: `installed`, `updated`, or `unchanged`. It writes nothing. It fails with a clear message on a dependency conflict or an invalid destination.

### Step 3 — Dependency rules the script applies

- Any audit adds `audit-protocol` and the calculator file `repository-quality-score/scripts/calculate_repository_quality_score.py`. When `repository-quality-score` itself is not selected, only that file is copied, without the scorer's `SKILL.md`, so the scorer is not offered with missing catalogs. Selecting an audit never adds other audits.
- `repository-quality-score` adds every audit its `score-policy.json` lists.
- Excluding `audit-protocol` while an audit is selected, or a policy audit while the scorer is selected, is a conflict. Excluding `repository-quality-score` is allowed.

### Step 4 — Confirm and apply

With `--dry-run`, show the plan and stop. Otherwise, if any skill is `updated`, list those skills and ask before continuing, unless `--force` was passed, because a replaced folder loses any local edits. Also name any existing folders in `~/.claude/skills/` that are not playbook skills, such as `graphify`, and say they stay untouched. Then apply:

```bash
python3 "$PLAYBOOK_ROOT/scripts/install_playbook.py" --destination "$HOME/.claude/skills" [same filters] --apply
```

Each changed skill folder is replaced as a whole, so files renamed or deleted in the playbook do not linger. Folders that are not playbook skills are never touched, and skills the playbook stopped shipping are not removed.

### Step 5 — Report

Show the script's summary, then: "Open a new Claude Code chat (any project) to pick up the new slash commands."

## Idempotency and safety rules

- Never overwrite skills the playbook does not own. `graphify` is the canonical example.
- Re-running with no flags is safe.
- Refuses to write outside `$HOME/.claude/skills/`.
- Replaces a changed playbook-owned skill folder as a whole, so stale files do not linger. Never deletes any other directory.
- `--dry-run` is honored.
- Selecting any audit always installs `audit-protocol` and the score calculator file. Selecting `repository-quality-score` always installs its policy audit catalogs. Both are dependency-closed sets.

## Recommended commit message

This skill writes to your home directory, not your project, so there is nothing to commit. If you maintain `~/.claude` as a tracked dotfiles repository, use:

```
chore: refresh architect-playbook skills in ~/.claude/skills/
```

## What this skill explicitly does NOT do

- Install graphify.
- Modify any project's `.claude/settings.json`.
- Run any audit.
- Touch global Claude Code settings (`~/.claude/settings.json`).
