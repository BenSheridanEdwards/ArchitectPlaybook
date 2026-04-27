---
name: worktree
description: Create a Git worktree for the named architect-playbook audit and run the audit against it, all in this same chat. Smart-handoff: if an audit was just run in this chat without --target and findings are recent, /worktree creates the worktree, copies the existing findings, and offers to generate the implementation plan against the worktree — no audit re-run. Lenient prefix matching on the skill name; bare /worktree opens a picker (or auto-resolves to the just-run audit when smart-handoff fires). The chat stays in the original project; the audit operates on the worktree via the audit's --target flag.
trigger: /worktree
---

# /worktree

Create a Git worktree and run the named audit against it, in the same chat. The chat stays in the original project — Claude Code chats are bound to the directory they were opened in — but the audit operates on the worktree because every audit accepts a `--target=<path>` flag that overrides its working directory.

The skill has two modes:

- **Normal mode (default).** Open a fresh chat in your project, run `/worktree security`, and the chat creates `../wt-security`, then runs `/security-audit --target=../wt-security`, then drops findings at `../wt-security/audits/security-audit/`. One chat, one command, audit done.
- **Smart-handoff mode.** If an audit was just run in this chat against the main checkout (no `--target`) and the findings are still recent, `/worktree` skips the audit re-run, copies the existing findings into the worktree, and asks whether to generate the implementation plan against the worktree. Useful when you ran the audit first to look at the findings, then decided you wanted a clean branch to apply the fixes on.

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
   - With no argument, *first* checks the chat's conversation history for a recent audit invocation that ran without `--target`. If found, that audit is the resolved name and smart-handoff mode is enabled.
   - With no argument and no recent audit in chat, lists the available audit-style skills and asks the user to pick.
   - On ambiguity, lists the candidates and asks the user to pick.
3. **Decides between Normal mode and Smart-handoff mode.** See the "Smart-handoff mode" section below for the trigger conditions. If smart-handoff fires, jumps to step 6. Otherwise, continues to step 4.
4. **Computes the worktree slug.** For `<short>-audit`, the slug is `<short>` (so `security-audit` → `wt-security`). For non-audit skills like `system-self-improve`, the slug is the full name (so `system-self-improve` → `wt-system-self-improve`).
5. **Creates the worktree.** Runs `git worktree add ../wt-<slug> -b wt-<slug>`. When the branch already exists, runs `git worktree add ../wt-<slug> wt-<slug>` (without `-b`). When the worktree path already exists, skips creation and notes "worktree already exists at `../wt-<slug>`".
6. **Branches on mode:**
   - **Normal mode**: invokes the resolved audit against the worktree with `--target=../wt-<slug>`. All file reads, globbing, searches, and subprocess invocations run scoped to the worktree. Findings land at `../wt-<slug>/audits/<skill-name>/`. The audit's two-phase flow (report → ask about implementation plan) runs as usual.
   - **Smart-handoff mode**: copies `audits/<skill-name>/{findings.md, findings.json, snapshot.md, metadata.json}` from the chat's cwd into `../wt-<slug>/audits/<skill-name>/`. Updates the copied `metadata.json` with `handedOffFrom: "cwd"` and `handedOffAt: <timestamp>`. Skips the audit re-run. Asks the user the standard phase-2 question: *"Generate an implementation plan against the worktree? (yes/no)"*. On yes, writes `../wt-<slug>/audits/<skill-name>/implementation-plan.md` describing fixes against files at `../wt-<slug>/...`.
7. **Reports the result inline** in the chat. The chat is now positioned to apply fixes against the worktree if the user asks — Claude reads `../wt-<slug>/audits/<skill-name>/findings.json` and edits files under `../wt-<slug>/`.

The skill does **not** open a new Claude Code chat. The chat that ran `/worktree` is the chat that does the work. Once the audit (or handoff) and any follow-up fixes are done, the user can `git worktree remove ../wt-<slug>` from a terminal when they're finished.

## Smart-handoff mode

The smart-handoff is a quality-of-life branch for the common workflow of "I ran an audit, looked at the findings, now I want to apply the fixes on a clean branch instead of polluting the current one." Without smart-handoff the user would have to either re-run the audit fresh against a worktree (wasteful — the findings already exist) or copy files around by hand. With smart-handoff, `/worktree` does the right thing automatically.

### Trigger conditions

Smart-handoff fires when **all** of these hold:

1. `/worktree` is invoked (with or without an argument).
2. The chat's conversation history shows a recent `/<audit>` slash-command invocation in this chat that ran **without** `--target` (i.e., against the main checkout). When `/worktree` was called with an explicit argument, that argument must match the recent audit's name (or its short form via lenient prefix matching) for the handoff to fire — otherwise the user is asking for a different audit than the one they ran, and Normal mode runs.
3. `audits/<resolved-audit>/findings.md` exists in the chat's current working directory.
4. The findings file's modification time is within 30 minutes of "now" (a heuristic for "still fresh enough that re-running would not produce different results").

If any condition fails, Normal mode runs (worktree + fresh audit invocation against it).

### What the handoff does

When the handoff fires, after the worktree is created:

1. **Copy the findings.** `cp -R audits/<audit>/findings.md`, `findings.json`, `snapshot.md`, `metadata.json` from the chat's cwd into `../wt-<slug>/audits/<audit>/`. The implementation-plan.md is **not** copied; it gets regenerated against the worktree if the user asks.
2. **Annotate the metadata.** Update `../wt-<slug>/audits/<audit>/metadata.json` to record:
   - `handedOffFrom`: `"cwd"`
   - `handedOffAt`: ISO timestamp of when the handoff ran
   - `originalRunStartedAt`: preserved from the original metadata
   - `originalRunFinishedAt`: preserved from the original metadata
3. **Print a short summary.** "Smart-handoff: picked up findings for `<audit>` (originally run at `<timestamp>`). Worktree created at `../wt-<slug>`. The findings have been copied; no audit re-run."
4. **Run phase 2 against the worktree.** Ask the user the same yes/no question the audit asks:
   > "Generate an implementation plan against the worktree? (yes/no)"
   On yes, write `../wt-<slug>/audits/<audit>/implementation-plan.md` describing fixes against files at `../wt-<slug>/...`. The plan can reference graph centrality from `../wt-<slug>/graphify-out/graph.json` if it exists in the worktree (which it does only if `/pre-audit-setup` was run on the worktree separately — most of the time it won't be, and the plan will note `noGraphify: true`).
5. **Suggest the natural next step.** "If you'd like to apply the plan now, ask me to read `../wt-<slug>/audits/<audit>/implementation-plan.md` and start editing files under `../wt-<slug>/`. The chat will stay in this directory; the edits land in the worktree via absolute paths."

### When the user wants a fresh re-run instead

Pass an explicit argument that doesn't match the recently-run audit, or wait until the existing findings are older than 30 minutes. Both cause Normal mode to run instead of the handoff.

If the user wants to *force* a fresh re-run of the same audit they just ran (for example, because they made changes between the audit and the `/worktree` invocation), the cleanest path is to re-run the audit first (`/<audit>` again, against the main checkout) and then run `/worktree` — the new findings overwrite the old ones, the mtime resets, and the handoff picks them up. Or delete `audits/<audit>/findings.md` before invoking `/worktree`, which forces Normal mode (no findings to hand off).

## Implementation steps

### Step 1 — Confirm we are inside a Git repository

```bash
git rev-parse --is-inside-work-tree > /dev/null 2>&1 || { echo "/worktree must be run inside a Git repository."; exit 1; }
```

### Step 2 — Enumerate available audit-style skills

Walk `.claude/skills/` (when running in a target project) or the playbook clone (when running there) for every directory whose `SKILL.md` does not contain the literal `**Status:** stub`. From each, read the `name:` from the frontmatter. Build the candidate list.

Skills that do not benefit from worktrees — `pre-audit-setup`, `install-skills-locally`, `install-skills-globally` — are excluded from the candidate list. Their effects need to land in the main checkout, not in a worktree, so a worktree wrapper around them would mislead.

### Step 3 — Resolve the requested skill

If `/worktree` was called with no argument, **first** scan the chat's conversation history for a recent slash-command invocation matching `/<some-audit>` that ran without `--target`. If found, use that audit's name as the resolved name (this is the smart-handoff inference). If multiple recent audits ran in the same chat, take the most recent one.

If `/worktree` was called with an argument, resolve it via the matching ladder:

- **Exact match**: a candidate's `name` exactly equals the argument. Use it.
- **`<arg>-audit` match**: the candidate `<arg>-audit` exists. Use it.
- **Unique prefix match**: exactly one candidate starts with the argument. Use it.
- **Ambiguous prefix**: multiple candidates start with the argument. Print them and ask.
- **No match**: print the available list and ask.

If the no-argument inference yielded nothing AND no argument was supplied, print the candidate list and ask the user to pick.

### Step 4 — Decide between Normal mode and Smart-handoff mode

After the resolved skill is in hand, check the smart-handoff trigger conditions:

1. The conversation history shows a recent `/<resolved-skill-name>` invocation in this chat that ran without `--target`. (If the user supplied an argument, the resolved name must match the recent invocation; otherwise the inference at step 3 is what surfaced this audit name in the first place.)
2. `audits/<resolved-skill-name>/findings.md` exists in the chat's current working directory.
3. The findings file's modification time is within 30 minutes of "now".

If all three hold, set `mode=smart-handoff`. Otherwise set `mode=normal`.

### Step 5 — Compute the slug

```
slug = skill_name
if slug ends with "-audit":
  slug = slug without the "-audit" suffix
```

So `security-audit` → `security`, `bundle-build-audit` → `bundle-build`, `system-self-improve` → `system-self-improve`.

### Step 6 — Create the worktree

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

### Step 7 — Branch on mode

**If `mode=normal`:** in the same chat, follow the resolved skill's body with `--target=<worktree_path>` set. The audit's checks read files, run subprocesses, and write findings all scoped to the worktree path. Findings land at `<worktree_path>/audits/<resolved-skill-name>/`. The audit's two-phase flow runs as usual.

**If `mode=smart-handoff`:**

```bash
mkdir -p "$worktree_path/audits/$skill_name"
cp audits/"$skill_name"/findings.md      "$worktree_path/audits/$skill_name/findings.md"
cp audits/"$skill_name"/findings.json    "$worktree_path/audits/$skill_name/findings.json"
cp audits/"$skill_name"/snapshot.md      "$worktree_path/audits/$skill_name/snapshot.md"
cp audits/"$skill_name"/metadata.json    "$worktree_path/audits/$skill_name/metadata.json"
```

Then update `<worktree_path>/audits/<skill-name>/metadata.json` to add `handedOffFrom: "cwd"`, `handedOffAt: <iso-timestamp>`, and preserve the `originalRunStartedAt` and `originalRunFinishedAt` fields from the source metadata.

Print to the chat:

```
Smart-handoff: picked up findings for <skill-name> (originally run at <originalRunFinishedAt>).
Worktree created at <worktree_path>.
The findings have been copied; no audit re-run.
```

Then ask the user the standard phase-2 question:

> "Generate an implementation plan against the worktree? (yes/no)"

On `yes`, write `<worktree_path>/audits/<skill-name>/implementation-plan.md` describing fixes against files at `<worktree_path>/...`. On `no`, exit with the cleanup hint from Step 8.

When the user asks Claude to apply the plan, Claude reads `<worktree_path>/audits/<skill-name>/implementation-plan.md` and edits files under `<worktree_path>/` — all from this same chat, all using absolute or worktree-relative paths.

### Step 8 — Suggest cleanup when the user is done

Print, after the audit (or handoff phase 2) completes:

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
| Smart-handoff fired but the user wanted a fresh re-run | The user made changes between the original audit and the `/worktree` invocation, and the existing findings are stale. | Re-run the audit first (`/<audit>` again, no `--target`), which overwrites the findings and resets the mtime. Then run `/worktree` — the handoff picks up the fresh findings. Or delete `audits/<audit>/findings.md` before invoking `/worktree`, which forces Normal mode. |
| Smart-handoff did not fire and the user expected it to | The findings are older than 30 minutes, or `--target` was used in the recent run, or the conversation history doesn't include a recent `/<audit>` invocation that this `/worktree` call could match. | Confirm the recent invocation was in *this* chat (a different chat doesn't share history) and ran against the main checkout. If everything looks right but the handoff still doesn't fire, the trigger heuristic may need tuning — file a review gap report. |

## What this skill explicitly does NOT do

- Open a new Claude Code chat. Chats are launched by the user; the skill cannot start one. The whole point of this skill is to remove the need for a fresh chat by running the audit in the existing chat with `--target` pointing at the worktree.
- Change the current chat's working directory. The chat stays in the original project; the audit reaches into the worktree via `--target`.
- Modify any source file or configuration (beyond creating the worktree directory and branch).
- Commit, push, or merge anything. The created branch starts as a copy of the current HEAD; the user does the actual work inside the worktree.
- Clean up old worktrees. The user runs `git worktree remove` and `git branch -d` when finished.
- Worktree the install or setup skills (`pre-audit-setup`, `install-skills-locally`, `install-skills-globally`). Their effects need to land in the main checkout.
