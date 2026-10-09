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

`${CLAUDE_SKILL_DIR}` is this skill's folder. In a clone it is `<playbook-root>/install-architect-playbook-locally`:

```bash
PLAYBOOK_ROOT="${FROM:-$(cd "${CLAUDE_SKILL_DIR}/.." && pwd)}"
if [ ! -f "$PLAYBOOK_ROOT/audit-protocol/SKILL.md" ] || [ ! -d "$PLAYBOOK_ROOT/.git" ]; then
  echo "Not a playbook clone: $PLAYBOOK_ROOT. Re-run with --from=<path to your architect-playbook clone>."
  exit 1
fi
```

`FROM` is the value of `--from`. If the skill runs from an installed copy rather than a clone, ask the user for the clone's path.

### Step 2 — Resolve destination

```bash
DEST="$(pwd)/.claude/skills"
mkdir -p "$DEST"
```

### Step 3 — Enumerate skills to install

For each direct sub-folder of `$PLAYBOOK_ROOT` that contains a `SKILL.md`, build a list. Exclude:

- `install-architect-playbook-locally`
- `install-architect-playbook-globally`
- Any folder whose name begins with `.`

Apply `--include` and `--exclude` filters from the command line, then resolve dependency closure:

- When any `*-audit` folder is selected, add `audit-protocol`. Every audit publishes through `audit-protocol/scripts/audit_run.py`, which validates each run with the score calculator. Also copy that one file, `repository-quality-score/scripts/calculate_repository_quality_score.py`. When `repository-quality-score` itself is not selected, copy only that file, without the scorer's `SKILL.md`, so the scorer is not offered with missing catalogs. Selecting an audit never adds other audits.
- When `repository-quality-score` is selected, read `repository-quality-score/score-policy.json` and add every folder named in `audits[].name`.

Show these additions in `--dry-run` output. If `--exclude` names `audit-protocol` while an audit is selected, or names a policy audit while `repository-quality-score` is selected, fail before copying and explain the conflict. Excluding `repository-quality-score` is allowed: audits still get the calculator file. Never install an audit without its protocol and calculator file, or a scorer whose catalogs are missing.

### Step 4 — Plan and confirm

For each selected skill, compare the source folder with the destination folder by content:

```bash
hash() { if command -v sha256sum >/dev/null; then sha256sum "$@"; else shasum -a 256 "$@"; fi; }
digest() { (cd "$1" && find . -type f ! -name '.DS_Store' ! -path '*/__pycache__/*' -print0 | sort -z | xargs -0 hash) | hash; }
```

- No destination folder: `installed`.
- Same digest: `unchanged`; nothing is written.
- Different digest: `updated`; the folder is replaced as a whole.

Print the plan. With `--dry-run`, stop here. If any folder would be `updated`, list them and ask before continuing, unless `--force` was passed, because a replaced folder loses any local edits.

### Step 5 — Copy

```bash
for skill in "${CHANGED[@]}"; do
  rm -rf "$DEST/$skill"
  cp -R "$PLAYBOOK_ROOT/$skill" "$DEST/$skill"
done
```

Copy the calculator file for audits without the scorer, as Step 3 describes. Never remove a destination folder whose name is not a playbook skill.

### Step 6 — Print the summary

```
installed:  architecture-audit
updated:    audit-protocol
unchanged:  repository-quality-score

3 skills checked; 2 written into ./.claude/skills/
Open a new Claude Code chat in this directory to pick up the new slash commands.
```

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
