---
name: worktree
description: One-shot helper that creates a Git worktree for running an architect-playbook audit in parallel. Accepts a skill name (with lenient prefix matching) or, with no argument, prints the available audits to pick from. Then prints the next-step instructions.
trigger: /worktree
---

# /worktree

A small helper that removes the friction of spinning up a Git worktree for an architect-playbook audit. The playbook is designed around running audits in parallel from separate Claude Code chats; each parallel chat lives in its own worktree so the audits don't collide. This skill is the "one slash command instead of remembering `git worktree` syntax" version of that workflow.

The skill itself does almost nothing — it runs `git worktree add` for you, names the worktree consistently, and prints the next step in plain English. There is no audit, no findings file, no two-phase flow.

## Usage

```
/worktree <skill-name>          # one-shot: create worktree for the named skill
/worktree <prefix>              # lenient prefix matching: /worktree security → security-audit
/worktree                       # no argument: print the list of audits and ask which one
```

The argument accepts any of these forms for a skill named `<short>-audit`:

- The full name: `security-audit`
- The short form: `security`
- An unambiguous prefix: `sec` (resolves to `security-audit` if no other audit starts with `sec`)

When the argument is ambiguous (matches multiple skills), the skill prints the candidates and asks which one. When the argument is missing entirely, the skill lists every audit-style skill in the playbook and asks the user to pick.

## What this skill does

1. **Confirms the working directory is a Git repository.** Stops with a friendly message if not.
2. **Resolves the target skill name.**
   - With an argument, attempts an exact match first, then a "<arg>-audit" match, then a prefix match against every audit-style skill in the current `.claude/skills/` directory (or the playbook clone, when run from inside one).
   - With no argument, lists the available audit-style skills and asks the user to pick.
   - On ambiguity, lists the candidates and asks the user to pick.
3. **Computes the worktree slug.** For `<short>-audit`, the slug is `<short>` (so `security-audit` → `wt-security`). For non-audit skills like `system-self-improve`, the slug is the full name (so `system-self-improve` → `wt-system-self-improve`).
4. **Creates the worktree.** Runs `git worktree add ../wt-<slug> -b wt-<slug>`. When the branch already exists, runs `git worktree add ../wt-<slug> wt-<slug>` (without `-b`). When the worktree path already exists, skips creation and prints "worktree already exists at ../wt-<slug>".
5. **Prints the next-step instructions** in plain English, naming the directory and the slash command to run there. The skill does NOT change the current chat's working directory or open a new chat — Claude Code chats are scoped to the directory they were opened in, so the user opens a fresh chat manually.

## Implementation steps

### Step 1 — Confirm we are inside a Git repository

```bash
git rev-parse --is-inside-work-tree > /dev/null 2>&1 || { echo "/worktree must be run inside a Git repository."; exit 1; }
```

### Step 2 — Enumerate available skills

Walk `.claude/skills/` (when running in a target project) for every directory whose `SKILL.md` does not contain the literal `**Status:** stub`. From each, read the `name:` from the frontmatter. Build the candidate list.

### Step 3 — Resolve the requested skill

- **No argument**: print the candidate list and ask the user which skill (number or name).
- **Exact match**: a candidate's `name` exactly equals the argument. Use it.
- **`<arg>-audit` match**: the candidate `<arg>-audit` exists. Use it.
- **Unique prefix match**: exactly one candidate starts with the argument. Use it.
- **Ambiguous prefix**: multiple candidates start with the argument. Print them and ask.
- **No match**: print the available list and ask.

Skills that do not benefit from worktrees (`pre-audit-setup`, `install-skills-locally`, `install-skills-globally`) are excluded from the candidate list — their effects need to land in the main checkout, so a worktree would mislead.

### Step 4 — Compute the slug

```
slug = skill_name
if slug ends with "-audit":
  slug = slug without the "-audit" suffix
```

So `security-audit` → `security`, `bundle-build-audit` → `bundle-build`, `system-self-improve` → `system-self-improve`.

### Step 5 — Create the worktree

```bash
slug="<resolved>"
worktree_path="../wt-$slug"
branch="wt-$slug"

if [ -d "$worktree_path" ]; then
  echo "Worktree already exists at $worktree_path"
elif git rev-parse --verify "$branch" > /dev/null 2>&1; then
  git worktree add "$worktree_path" "$branch"
else
  git worktree add "$worktree_path" -b "$branch"
fi
```

### Step 6 — Print the next-step instructions

Print exactly:

```
✓ Worktree ready at ../wt-<slug> on branch wt-<slug>
→ Open a new Claude Code chat in ../wt-<slug>
→ In that chat, run: /<resolved-skill-name>
→ When you're done with the audit and any fixes:
    git worktree remove ../wt-<slug>
    git branch -d wt-<slug>      # only after merging or discarding
```

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `/worktree must be run inside a Git repository` | The current working directory is not under any `.git` tree. | `cd` into the project root and re-run. |
| No candidate skills found | `.claude/skills/` is empty or missing. | Run `/install-skills-locally` (or `/install-skills-globally`) first. |
| Argument matches a known skill name but not in the candidates | The skill is one of the install/setup skills (`pre-audit-setup`, `install-skills-locally`, `install-skills-globally`). | These skills don't benefit from worktrees. Run them in the main checkout. |
| `git worktree add` fails because the branch is checked out elsewhere | A previous worktree for the same branch wasn't cleaned up. | Run `git worktree list` to find it; `git worktree remove` to clean up; re-run `/worktree <skill>`. |
| The user runs `/worktree security` and `security-audit` does not exist in the project | The audit skill hasn't been installed yet. | Run `/install-skills-locally` to refresh, then re-run. |

## What this skill explicitly does NOT do

- Run any audit. The skill creates the worktree and prints next-step instructions; the user opens a chat in the worktree and runs the audit there.
- Open a new Claude Code chat. Chats are launched by the user; the skill cannot start one.
- Change the current chat's working directory. Doing so would silently change which project the current chat operates on.
- Modify any source file or configuration. The only filesystem change is the new worktree directory and branch.
- Commit, push, or merge anything. The created branch is empty (a copy of the current HEAD). The user does the actual work inside the worktree.
- Clean up old worktrees. The user runs `git worktree remove` and `git branch -d` when finished.
- Worktree the install or setup skills (`pre-audit-setup`, `install-skills-locally`, `install-skills-globally`). Their effects need to land in the main checkout.
