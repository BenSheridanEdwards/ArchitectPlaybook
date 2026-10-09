---
name: install-architect-playbook-locally
description: Copy every architect-playbook skill into the current project's .claude/skills/ directory so the slash commands are available inside this project only.
disable-model-invocation: true
argument-hint: "[--dry-run] [--force] [--from=<path>] [--include=<skill>] [--exclude=<skill>]"
---

# /install-architect-playbook-locally

Copy every skill folder from this architect-playbook repository into `<current-project>/.claude/skills/`. After running this, every architect-playbook slash command works inside the current project, and only inside the current project. Other projects on the machine are untouched.

The plugin is the recommended install (`/plugin install architect-playbook@architect-playbook`; see the README). Use this skill to pin the skills inside a project's version control.

## Usage

```
/install-architect-playbook-locally                 # install or update every skill into .claude/skills/
/install-architect-playbook-locally --dry-run       # print the plan without copying anything
/install-architect-playbook-locally --force         # replace changed skills without asking first
/install-architect-playbook-locally --from=<path>   # install from this playbook clone
/install-architect-playbook-locally --include=name  # install only the named skill and what it needs (repeatable)
/install-architect-playbook-locally --exclude=name  # skip a named skill (repeatable)
```

## What this skill does

1. **Resolves the playbook root:** the clone this skill runs from, or `--from=<path>`.
2. **Resolves the destination:** `<current-working-directory>/.claude/skills/`, created if missing.
3. **Selects skills:** every direct sub-folder of the playbook root that contains a `SKILL.md`, except the two installers, filtered by `--include` and `--exclude` and closed over dependencies.
4. **Replaces each changed skill as a whole.** A skill folder whose contents differ from the playbook's is removed and copied again, so files renamed or deleted in the playbook do not linger. An identical folder is left alone. Folders the playbook does not own are never touched.
5. **Reports one line per skill:** `installed`, `updated`, or `unchanged`.

## Implementation steps

### Step 1 — Resolve the playbook root

```bash
if [ "${FROM+set}" = set ]; then
  set -- "$FROM"                          # an explicit --from, even empty, must be valid; never fall back
else
  set -- "${CLAUDE_SKILL_DIR}/.." "${CLAUDE_SKILL_DIR}/../../.."   # Claude Code fills in this exact form
fi
PLAYBOOK_ROOT=""
for candidate in "$@"; do
  if [ -f "$candidate/scripts/install_playbook.py" ] && git -C "$candidate" rev-parse --show-toplevel >/dev/null 2>&1; then
    PLAYBOOK_ROOT="$(cd "$candidate" && pwd)"; break
  fi
done
[ -n "$PLAYBOOK_ROOT" ] || { echo "Not a playbook clone: '${FROM-the folder this skill runs from}'. Re-run with --from=<path to your clone>."; exit 1; }
```

Set `FROM` only when `--from` was passed, to its value; leave it unset otherwise. A Git worktree of the playbook counts as a clone. The script re-checks that the root is a Git checkout of the playbook.

If the skill runs from an installed copy rather than a clone, ask the user for the clone's path.

### Step 2 — Plan

```bash
python3 "$PLAYBOOK_ROOT/scripts/install_playbook.py" --destination "$(pwd)/.claude/skills" [--include <skill>]... [--exclude <skill>]...
```

The script selects the skills, closes the selection over dependencies (Step 3 explains the rules), compares each skill with the destination by a digest of file names, contents, and symbolic links, and prints one line per skill: `installed`, `updated`, or `unchanged`, plus `preserved` for each existing folder that is not a playbook skill. It writes nothing. It fails with a clear message on a dependency conflict or an invalid destination.

### Step 3 — Dependency rules the script applies

- Any audit adds `audit-protocol` and the calculator file `repository-quality-score/scripts/calculate_repository_quality_score.py`. When `repository-quality-score` itself is not selected, only that file is copied, without the scorer's `SKILL.md`, so the scorer is not offered with missing catalogs. Selecting an audit never adds other audits.
- `repository-quality-score` adds every audit its `score-policy.json` lists.
- Excluding `audit-protocol` while an audit is selected, or a policy audit while the scorer is selected, is a conflict. Excluding `repository-quality-score` is allowed.

### Step 4 — Confirm and apply

With `--dry-run`, show the plan and stop. Otherwise, if any skill is `updated`, list those skills and ask before continuing, unless `--force` was passed, because a replaced folder loses any local edits. Then apply:

```bash
python3 "$PLAYBOOK_ROOT/scripts/install_playbook.py" --destination "$(pwd)/.claude/skills" [same filters] --apply
```

Each changed skill folder is replaced as a whole, so files renamed or deleted in the playbook do not linger. Folders that are not playbook skills are never touched, and skills the playbook stopped shipping are not removed.

### Step 5 — Report

Show the script's summary, then: "Open a new Claude Code chat in this directory to pick up the new slash commands."

## Idempotency rules

- Re-running is safe. Only skills whose contents differ are written, and each is replaced as a whole.
- This skill never deletes a folder that is not a playbook skill, and never removes a skill the playbook no longer ships; clean those up by hand.
- This skill never touches `~/.claude/skills/`. For machine-wide install, use `/install-architect-playbook-globally`.
- Selecting any audit always installs `audit-protocol` and the score calculator file. Selecting `repository-quality-score` always installs its policy audit catalogs. Both are dependency-closed sets.

## Safety

- Refuses to write outside `<current-working-directory>/.claude/skills/`.
- Refuses to follow symlinks out of the playbook root when copying.
- Asks before replacing changed skills, unless `--force` is passed.
- `--dry-run` is honored — when set, the skill only prints the plan.

## Recommended commit message

If the user wants to track the installed skills in version control:

```
chore: install architect-playbook skills into .claude/skills/
```

## What this skill explicitly does NOT do

- Install graphify, or build its knowledge graph (run `/pre-audit-setup` for the graph).
- Modify any settings file.
- Run any audit.
- Affect any project other than the current working directory.
