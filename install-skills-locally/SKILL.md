---
name: install-skills-locally
description: Copy every architect-playbook skill into the current project's .claude/skills/ directory so the slash commands are available inside this project only.
trigger: /install-skills-locally
---

# /install-skills-locally

Copy every skill folder from this architect-playbook repository into `<current-project>/.claude/skills/`. After running this, every architect-playbook slash command works inside the current project, and only inside the current project. Other projects on the machine are untouched.

## Usage

```
/install-skills-locally                 # install or update every skill into .claude/skills/
/install-skills-locally --dry-run       # print the plan without copying anything
/install-skills-locally --force         # overwrite destinations even if they appear newer
/install-skills-locally --include=name  # install only one named skill (repeatable)
/install-skills-locally --exclude=name  # skip a named skill (repeatable)
```

## What this skill does

1. **Resolves the playbook root.** Locate the architect-playbook repository the user wants to install from. The playbook root is the directory that contains this skill's parent folder (`install-skills-locally/SKILL.md`). If the skill is being run from inside `~/.claude/skills/install-skills-locally/SKILL.md` (already globally installed), ask the user for the path to the playbook clone.
2. **Resolves the destination.** `<current-working-directory>/.claude/skills/`. Create it if it does not exist.
3. **Enumerates source skills.** Every direct sub-folder of the playbook root that contains a `SKILL.md`, except `install-skills-locally` and `install-skills-globally` themselves (those should not be installed into target projects — they belong to the playbook).
4. **Copies each skill folder** with `cp -R <source>/<skill> <destination>/<skill>`. The destination becomes a complete, self-contained copy. Subsequent edits to the playbook do not propagate; re-run this skill to refresh.
5. **Reports the result** with one line per skill: `installed`, `updated`, `skipped (stub)`, or `skipped (newer at destination)`.

## Implementation steps

### Step 1 — Resolve the playbook root

```bash
# This skill file lives at <playbook-root>/install-skills-locally/SKILL.md when running
# from the source repository. Detect that case first.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd 2>/dev/null || pwd)"
PLAYBOOK_ROOT="$(dirname "$SKILL_DIR")"

if [ ! -f "$PLAYBOOK_ROOT/install-skills-locally/SKILL.md" ]; then
  echo "Could not auto-detect the architect-playbook root."
  echo "Re-run from inside a clone of architect-playbook, or pass --from=<path>."
  exit 1
fi
```

When invoked as a slash command (not as a shell script), use the Read tool to find the playbook clone the user pointed at, or ask the user for the path.

### Step 2 — Resolve destination

```bash
DEST="$(pwd)/.claude/skills"
mkdir -p "$DEST"
```

### Step 3 — Enumerate skills to install

For each direct sub-folder of `$PLAYBOOK_ROOT` that contains a `SKILL.md`, build a list. Exclude:

- `install-skills-locally`
- `install-skills-globally`
- Any folder whose name begins with `.`

Apply `--include` and `--exclude` filters from the command line.

### Step 4 — Detect stubs

A SKILL.md is considered a stub if its body contains the literal string `**Status:** stub`. Stubs are still copied by default (so they are visible as slash commands and the user knows what is on the way), but the report flags them with `skipped (stub)` if `--no-stubs` is passed.

### Step 5 — Copy each skill

```bash
for skill in "${SKILLS[@]}"; do
  src="$PLAYBOOK_ROOT/$skill"
  dst="$DEST/$skill"

  if [ -d "$dst" ] && [ -z "$FORCE" ]; then
    # Compare modification time; skip if destination is newer.
    if [ "$(stat -f %m "$dst/SKILL.md" 2>/dev/null || stat -c %Y "$dst/SKILL.md")" \
         -gt "$(stat -f %m "$src/SKILL.md" 2>/dev/null || stat -c %Y "$src/SKILL.md")" ]; then
      echo "skipped (newer at destination): $skill"
      continue
    fi
  fi

  rm -rf "$dst"
  cp -R "$src" "$dst"
  echo "installed: $skill"
done
```

### Step 6 — Print the summary

```
installed:                  pre-audit-setup
installed:                  system-self-improve
installed (stub):           security-audit
installed (stub):           performance-audit
...

15 skills installed into ./.claude/skills/
Open a new Claude Code chat in this directory to pick up the new slash commands.
```

## Idempotency rules

- Re-running with no flags is safe. Files only change if the source is newer (or `--force` is set).
- This skill never deletes a destination skill that is not in the source — manual cleanup only.
- This skill never touches `~/.claude/skills/`. For machine-wide install, use `/install-skills-globally`.

## Safety

- Refuses to write outside `<current-working-directory>/.claude/skills/`.
- Refuses to follow symlinks out of the playbook root when copying.
- `--dry-run` is honored — when set, the skill only prints the plan.

## Recommended commit message

If the user wants to track the installed skills in version control:

```
chore: install architect-playbook skills into .claude/skills/
```

## What this skill explicitly does NOT do

- Install graphify (run `/pre-audit-setup` for that).
- Modify settings.json (run `/pre-audit-setup`).
- Run any audit.
- Affect any project other than the current working directory.
