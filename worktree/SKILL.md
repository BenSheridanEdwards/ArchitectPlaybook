---
name: worktree
description: Create a Git worktree for the named architect-playbook audit and run the audit against it, all in this same chat. Lenient prefix matching on the skill name; bare /worktree opens a picker. The chat stays in the original project; the audit operates on the worktree via the audit's --target flag.
trigger: /worktree
---

# /worktree

Create a Git worktree and run the named audit against it, in the same chat. The chat stays in the original project — Claude Code chats are bound to the directory they were opened in — but the audit operates on the worktree because every audit accepts a `--target=<path>` flag that overrides its working directory.

This means: open a fresh chat in your project, run `/worktree security`, and the chat creates `../wt-security`, then runs `/security-audit --target=../wt-security`, then drops findings at `../wt-security/audits/security-audit/`. One chat, one command, audit done.

## Usage

```
/worktree <skill-name>          # one-shot: create worktree and run the named audit against it
/worktree <prefix>              # lenient prefix matching: /worktree sec → security-audit
/worktree                       # no argument: print the list of audits and ask which one
```

The argument accepts any of these forms for a skill named `<short>-audit`:

- The full name: `security-audit`.
- The short form: `security`.
- An unambiguous prefix: `sec` (resolves to `security-audit` if no other audit starts with `sec`).

When the argument is ambiguous (matches multiple skills), the skill prints the candidates and asks which one. When the argument is missing entirely, the skill lists every audit-style skill in the playbook and asks the user to pick.

## What this skill does

1. **Confirms the working directory is a Git repository.** Stops with a friendly message if not.
2. **Resolves the target skill name.**
   - With an argument, attempts an exact match first, then a `<arg>-audit` match, then a prefix match against every audit-style skill in the current `.claude/skills/` directory (or the playbook clone, when run from inside one).
   - With no argument, lists the available audit-style skills and asks the user to pick.
   - On ambiguity, lists the candidates and asks the user to pick.
3. **Computes the worktree slug.** For `<short>-audit`, the slug is `<short>` (so `security-audit` → `wt-security`). For non-audit skills like `system-self-improve`, the slug is the full name (so `system-self-improve` → `wt-system-self-improve`).
4. **Creates the worktree.** Runs `git worktree add ../wt-<slug> -b wt-<slug>`. When the branch already exists, runs `git worktree add ../wt-<slug> wt-<slug>` (without `-b`). When the worktree path already exists, skips creation and notes "worktree already exists at `../wt-<slug>`".
5. **Invokes the resolved audit against the worktree.** In the same chat, runs the resolved skill's body with `--target=../wt-<slug>` (relative to the chat's cwd). All file reads, file globbing, regex searches, and subprocess invocations performed by the audit run scoped to the worktree. Findings land at `../wt-<slug>/audits/<skill-name>/`.
6. **Reports the result inline** in the chat, the same way the audit normally would. The two-phase flow (report → ask about implementation plan) runs as usual; the implementation-plan question is answered in this same chat.

The skill does **not** open a new Claude Code chat. The chat that ran `/worktree` is the chat that runs the audit. Once the audit and any follow-up fixes are done, the user can `git worktree remove ../wt-<slug>` from a terminal when they're finished.

## Implementation steps

### Step 1 — Confirm we are inside a Git repository

```bash
git rev-parse --is-inside-work-tree > /dev/null 2>&1 || { echo "/worktree must be run inside a Git repository."; exit 1; }
```

### Step 2 — Enumerate available audit-style skills

Walk `.claude/skills/` (when running in a target project) or the playbook clone (when running there) for every directory whose `SKILL.md` does not contain the literal `**Status:** stub`. From each, read the `name:` from the frontmatter. Build the candidate list.

Skills that do not benefit from worktrees — `pre-audit-setup`, `install-skills-locally`, `install-skills-globally` — are excluded from the candidate list. Their effects need to land in the main checkout, not in a worktree, so a worktree wrapper around them would mislead.

### Step 3 — Resolve the requested skill

- **No argument**: print the candidate list and ask the user which skill (number or name).
- **Exact match**: a candidate's `name` exactly equals the argument. Use it.
- **`<arg>-audit` match**: the candidate `<arg>-audit` exists. Use it.
- **Unique prefix match**: exactly one candidate starts with the argument. Use it.
- **Ambiguous prefix**: multiple candidates start with the argument. Print them and ask.
- **No match**: print the available list and ask.

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
  echo "Worktree already exists at $worktree_path — using it as-is."
elif git rev-parse --verify "$branch" > /dev/null 2>&1; then
  git worktree add "$worktree_path" "$branch"
else
  git worktree add "$worktree_path" -b "$branch"
fi
```

If `git worktree add` fails (for example, because the branch is checked out elsewhere), surface the error and stop. Do not try to recover automatically — the user needs to clean up the conflicting worktree first.

### Step 6 — Invoke the resolved audit against the worktree

In the same chat, follow the resolved skill's body with `--target=<worktree_path>` set. The audit's checks read files, run subprocesses, and write findings all scoped to the worktree path. Findings land at `<worktree_path>/audits/<resolved-skill-name>/`.

When the audit's two-phase flow asks whether to generate an implementation plan, the user answers in this chat. When the user asks Claude to fix the findings, Claude reads `<worktree_path>/audits/<resolved-skill-name>/findings.json` and edits files under `<worktree_path>/` — all from this same chat, all using absolute or worktree-relative paths.

### Step 7 — Suggest cleanup when the user is done

Print, after the audit completes (whether or not the user proceeds to phase 2):

```
Worktree at ../wt-<slug> on branch wt-<slug>.
When you're done with this audit and any fixes:
  git worktree remove ../wt-<slug>
  git branch -d wt-<slug>      # only after merging or discarding
```

The skill does not run cleanup automatically — the user might still be applying fixes when the audit finishes.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `/worktree must be run inside a Git repository` | The current working directory is not under any `.git` tree. | `cd` into the project root and re-run. |
| No candidate skills found | `.claude/skills/` is empty or missing. | Run `/install-skills-locally` (or `/install-skills-globally`) first. |
| Argument matches a known skill name but not in the candidates | The skill is one of the install/setup skills (`pre-audit-setup`, `install-skills-locally`, `install-skills-globally`). | These skills don't benefit from worktrees. Run them in the main checkout. |
| `git worktree add` fails because the branch is checked out elsewhere | A previous worktree for the same branch wasn't cleaned up. | Run `git worktree list` to find it; `git worktree remove` to clean up; re-run `/worktree <skill>`. |
| The user runs `/worktree security` and `security-audit` does not exist in the project | The audit skill hasn't been installed yet. | Run `/install-skills-locally` to refresh, then re-run. |
| Audit reads or writes the wrong directory | The audit body did not respect `--target=<path>`. | Bug in the audit. Report via `/system-self-improve` with a `review-gap-report.md` describing the path-resolution failure. |

## What this skill explicitly does NOT do

- Open a new Claude Code chat. Chats are launched by the user; the skill cannot start one. The whole point of this skill is to remove the need for a fresh chat by running the audit in the existing chat with `--target` pointing at the worktree.
- Change the current chat's working directory. The chat stays in the original project; the audit reaches into the worktree via `--target`.
- Modify any source file or configuration (beyond creating the worktree directory and branch).
- Commit, push, or merge anything. The created branch starts as a copy of the current HEAD; the user does the actual work inside the worktree.
- Clean up old worktrees. The user runs `git worktree remove` and `git branch -d` when finished.
- Worktree the install or setup skills (`pre-audit-setup`, `install-skills-locally`, `install-skills-globally`). Their effects need to land in the main checkout.
