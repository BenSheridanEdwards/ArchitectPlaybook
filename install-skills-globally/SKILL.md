---
name: install-skills-globally
description: Copy every architect-playbook skill into ~/.claude/skills/ so the slash commands are available in every Claude Code session on this machine.
trigger: /install-skills-globally
---

# /install-skills-globally

Copy every skill folder from this architect-playbook repository into `~/.claude/skills/`. After running this, every architect-playbook slash command works in every Claude Code session on the machine, regardless of which project is open.

This skill is the machine-wide counterpart to [`/install-skills-locally`](../install-skills-locally/SKILL.md). The mechanics are the same; only the destination differs.

## Usage

```
/install-skills-globally                 # install or update every skill into ~/.claude/skills/
/install-skills-globally --dry-run       # print the plan without copying anything
/install-skills-globally --force         # overwrite destinations even if they appear newer
/install-skills-globally --include=name  # install only one named skill (repeatable)
/install-skills-globally --exclude=name  # skip a named skill (repeatable)
```

## When to choose this over the local install

Use this skill when:

- You audit lots of different codebases and you do not want to re-install per project.
- You want a single canonical version of every skill on disk.

Prefer `/install-skills-locally` when:

- You want different projects to pin different versions of the playbook.
- You want the installed skills to live alongside the project in version control.

## What this skill does

1. **Resolves the playbook root** the same way `/install-skills-locally` does.
2. **Resolves the destination** as `$HOME/.claude/skills/`. Creates it if missing.
3. **Asks for confirmation** before overwriting any existing skill of the same name in `~/.claude/skills/`. Global installs affect every Claude Code session on the machine, so the bar for overwriting must be higher than for a project-local install.
4. **Enumerates source skills** (every sub-folder with a `SKILL.md` except `install-skills-locally` and `install-skills-globally`).
5. **Copies each skill folder** with `cp -R`. The destination becomes a complete, self-contained copy.
6. **Reports the result** with the same format as the local installer.

## Implementation steps

### Step 1 — Resolve the playbook root

Same logic as `/install-skills-locally`. The skill file's parent of parent is the playbook root.

### Step 2 — Resolve destination

```bash
DEST="$HOME/.claude/skills"
mkdir -p "$DEST"
```

Refuse to proceed if `$HOME` is unset or empty.

### Step 3 — Enumerate skills

Same as the local installer. Exclude:

- `install-skills-locally`
- `install-skills-globally`
- Any folder whose name begins with `.`

### Step 4 — Confirmation gate for overwrites

Before copying, list every destination that already exists:

```
The following skills already exist in ~/.claude/skills/ and will be overwritten:
  - security-audit
  - performance-audit
  - graphify   (NOT MANAGED BY THIS PLAYBOOK — will not be touched)

Continue? [y/N]
```

If the user does not answer `y`, abort. Do not overwrite skills the playbook does not own — for the architect-playbook this is everything except `graphify`. Detect ownership by checking whether the destination's `SKILL.md` `name` field matches a skill in the playbook source. Anything whose name is not in the source list is left alone.

### Step 5 — Copy each skill

Same loop as the local installer, but writing to `$HOME/.claude/skills/`.

### Step 6 — Print the summary

```
installed:        pre-audit-setup
updated:          security-audit
installed (stub): performance-audit
...
preserved:        graphify   (not managed by architect-playbook)

15 skills installed into ~/.claude/skills/
Open a new Claude Code chat (any project) to pick up the new slash commands.
```

## Idempotency and safety rules

- Never overwrite skills the playbook does not own. `graphify` is the canonical example.
- Re-running with no flags is safe.
- Refuses to write outside `$HOME/.claude/skills/`.
- Refuses to delete any directory in the destination — only `cp -R` over the top of it.
- `--dry-run` is honored.

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
