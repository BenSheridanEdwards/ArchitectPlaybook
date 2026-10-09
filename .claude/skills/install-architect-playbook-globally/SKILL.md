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

Use `--from=<path>` if given. Otherwise derive it from `${CLAUDE_SKILL_DIR}`, this skill's folder. In a clone that is `<playbook-root>/install-architect-playbook-globally`, or, for the bootstrap copy, `<playbook-root>/.claude/skills/install-architect-playbook-globally`:

```bash
for candidate in "${FROM:-}" "${CLAUDE_SKILL_DIR}/.." "${CLAUDE_SKILL_DIR}/../../.."; do
  [ -n "$candidate" ] && [ -f "$candidate/audit-protocol/SKILL.md" ] && [ -d "$candidate/.git" ] && PLAYBOOK_ROOT="$(cd "$candidate" && pwd)" && break
done
[ -n "${PLAYBOOK_ROOT:-}" ] || { echo "Not a playbook clone; re-run with --from=<path>."; exit 1; }
```

### Step 2 — Resolve destination

```bash
DEST="$HOME/.claude/skills"
mkdir -p "$DEST"
```

Refuse to proceed if `$HOME` is unset or empty.

### Step 3 — Enumerate skills

Same as the local installer. Exclude:

- `install-architect-playbook-locally`
- `install-architect-playbook-globally`
- Any folder whose name begins with `.`

After applying `--include` and `--exclude`, resolve dependency closure:

- If any `*-audit` folder is selected, add `audit-protocol`. Every audit publishes through `audit-protocol/scripts/audit_run.py`, which validates each run with the score calculator. Also copy that one file, `repository-quality-score/scripts/calculate_repository_quality_score.py`. If `repository-quality-score` itself is not selected, copy only that file, without the scorer's `SKILL.md`, so the scorer is not offered with missing catalogs. Selecting an audit never adds other audits.
- If `repository-quality-score` is selected, read its `score-policy.json` and add every folder named in `audits[].name`.

Show the expanded set during `--dry-run`. If the user excludes `audit-protocol` while an audit is selected, or a policy audit while `repository-quality-score` is selected, fail before copying with a clear conflict message. Excluding `repository-quality-score` is allowed: audits still get the calculator file. Never install an audit without its protocol and calculator file, or an incomplete scorer.

### Step 4 — Plan and confirm

Compare each selected skill with its destination by content digest, as the local installer's Step 4 does: `installed`, `updated`, or `unchanged`. List every skill that would be `updated`, and every existing destination folder the playbook does not own:

```
These skills already exist in ~/.claude/skills/ and differ from the playbook; they will be replaced:
  - security-audit
  - audit-protocol
Not managed by architect-playbook, left untouched:
  - graphify

Continue? [y/N]
```

With `--dry-run`, stop after printing. Without `--force`, abort unless the user answers `y`. Ownership is by folder name: a folder whose name is not a playbook skill is never touched.

### Step 5 — Copy each skill

Use the local installer's loop, writing to `$HOME/.claude/skills/`: remove each changed playbook-owned folder, then copy it again.

### Step 6 — Print the summary

```
installed:  pre-audit-setup
updated:    security-audit
unchanged:  audit-protocol
preserved:  graphify   (not managed by architect-playbook)

3 skills checked; 2 written into ~/.claude/skills/
Open a new Claude Code chat (any project) to pick up the new slash commands.
```

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
