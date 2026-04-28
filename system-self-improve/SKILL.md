---
name: system-self-improve
description: The meta-improvement layer of the architect-playbook. Reads a review's gap report (or a user-supplied gap, or audit-history patterns), locates the affected SKILL.md and adjacent files, and proposes a minimal reversible edit to the playbook so the same class of gap is more likely to be caught next time. Dry-run by default; --apply enables mutation but always prompts for confirmation.
trigger: /system-self-improve
---

# /system-self-improve

The meta-improvement layer of the architect-playbook. When a review surfaces a gap that one of the audits missed — or when running an audit reveals a weakness in the audit itself — `/system-self-improve` reads the gap, locates the affected SKILL.md (and any adjacent files: README, CLAUDE.md, `pre-audit-setup` hook, MEMORY.md), proposes a minimal reversible edit, asks for confirmation, and on approval mutates the playbook so the same gap is more likely to be caught next time.

This is the only skill in the playbook that writes to files outside its own `.architect-audits/` directory. That power is gated behind a deliberate two-stage flow.

## How this differs from neighbouring skills

| Concern | Owner |
| --- | --- |
| Auditing a target codebase against an opinionated baseline | Every audit skill (`/security-audit`, `/performance-audit`, etc.) |
| Generating an implementation plan that the **user** then executes against the target codebase | Every audit's phase 2 |
| Running a review pass against a worktree containing fixes | Re-running the originating audit in a fresh chat against the worktree |
| **Reading a review's gap report and editing the originating audit's `SKILL.md` so the same gap is caught next time** | `/system-self-improve` |
| **Maintaining cross-skill consistency** (boundary tables, README index, README per-skill summary, CLAUDE.md rules, MEMORY.md preferences) when an audit evolves | `/system-self-improve` |
| **Updating its own `SKILL.md`** when a meta-weakness is identified | `/system-self-improve` (recursive case, with stronger confirmation) |

## How `/system-self-improve` is triggered

Three input modes, in priority order. The skill scans for them in this order and uses the first that yields usable input.

1. **Review gap report** (most common). A reviewer — fresh chat against a worktree, human, or another agent — writes `.architect-audits/<originating-audit>/review-gap-report.md` describing what the originating audit failed to catch. The default scan path is `.architect-audits/*/review-gap-report.md` in the current working directory; an alternative path is supplied via `--gap-report=<path>`.
2. **User-supplied gap description.** The user invokes the skill with `--gap="<freeform description>"` plus `--target-skill=<skill-name>`. Useful when the user has noticed a gap directly without writing a formal review.
3. **Pattern across audit runs.** When run with `--from-audit-history`, the skill scans `.architect-audits/*/findings.json` across the project for patterns suggesting an audit is systematically wrong: every project hits the same threshold, an audit consistently surfaces `partial` with no `violation` (suggesting the threshold is too lenient or the check is too weak), an audit's `noGraphify` fallback is the dominant code path. This is the most speculative mode and reports candidates as suggestions only.

## Where `/system-self-improve` operates

The skill must run from inside a clone of the architect-playbook repository, or pointed at one via `--playbook-path=<path>`. It cannot edit a globally-installed copy of a skill in `~/.claude/skills/` because changes there don't survive re-install — improvements must land in the source repo and be re-installed via `/install-skills-locally` or `/install-skills-globally`.

When the gap report lives in a target project (`<some-project>/.architect-audits/<audit>/review-gap-report.md`) and the playbook clone is elsewhere, the user passes both `--gap-report=<path>` and `--playbook-path=<path>`.

## Posture: dry-run by default, `--apply` always prompts

This is the only skill in the playbook with a meaningful `--apply` mode. Two modes:

- **Dry-run (default).** Diagnose, locate, propose. Write `improvement-plan.md` plus a unified diff for every file the plan would touch. Do not modify anything.
- **`--apply`.** After the same diagnose-locate-propose cycle, prompt the user with the summary and the diff, ask the single confirmation question, and on `yes` perform the edits and append a log entry. On `no`, exit cleanly with the plan still on disk.

**There is no flag to skip the confirmation prompt under `--apply`.** That deliberate design forecloses accidental autonomous self-modification — even with `--apply`, a human approves each application. The skill is opinionated about its own safety.

## Usage

```
/system-self-improve                                          # dry-run; scan default review-gap-report paths
/system-self-improve --apply                                  # dry-run output + confirmation prompt + (on yes) mutation
/system-self-improve --gap="<description>" --target-skill=<name>   # user-supplied gap mode
/system-self-improve --from-audit-history                     # scan .architect-audits/*/findings.json for systematic patterns
/system-self-improve --gap-report=<path>                      # explicit gap-report path
/system-self-improve --playbook-path=<path>                   # operate on a playbook clone elsewhere
/system-self-improve --plan                                   # regenerate improvement-plan.md for an existing run
```

The skill never accepts any flag that bypasses the confirmation prompt under `--apply`. It also never operates on a target project's source files; it only reads gap reports and edits the playbook clone.

**💡 Pro tip**: Use `/worktree system-self-improve` to run this against a Git worktree of the playbook (creates the worktree and runs the meta-improve pass in this same chat). Useful for staging proposed edits on a separate branch before merging into `main`.

## The four stages plus Layer 0

The other audits use four layers organised by concern. This skill's layers are **stages of the meta-improvement workflow**, walked sequentially. Each stage either advances or surfaces a blocker. The status taxonomy differs from the audits accordingly: every stage records an `outcome` of `advanced`, `blocked`, or `skipped`, plus a `blockerReason` when relevant. There is no `present | partial | missing | violation` here — those concepts apply when grading a target codebase, not when diagnosing the playbook itself.

Layer 0 is informational only.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Playbook version: last commit hash, last commit subject, last-commit timestamp.
- Total skill count, with breakdown: ready, stub.
- Most-recently-modified skills (top 5), oldest skills by modification date (top 5; potentially stale).
- Total check count across all audits (sum of layer-1-through-N tables in every SKILL.md).
- Cross-skill overlap map: which skills surface gaps that other skills also surface (extracted from boundary tables and README per-skill summaries).
- Detected input source(s): list of `review-gap-report.md` files found, gap descriptions supplied via flags, or audit-history candidates.
- Graphify presence against the playbook itself: yes/no, plus the playbook's own god-node `SKILL.md` files when the graph is present.
- Existing `system-self-improve-log.md` entry count: history of past improvements.

### Stage 1 — Weakness diagnosis

Read the input gap, classify it, and confirm it warrants action.

| Stage check | Expectation | Blocker signal |
| --- | --- | --- |
| Input source resolved | A `review-gap-report.md` is present, OR `--gap` was supplied with `--target-skill`, OR `--from-audit-history` produced at least one candidate. | No usable input. The skill stops and explains how to provide one. |
| Gap classifiable | The gap maps to one of: **missing check** (no check covers this), **weak check** (a check exists but the detection misses cases), **wrong threshold** (a default is systematically off), **stale detection logic** (an ecosystem changed and the audit didn't), **cross-skill drift** (two skills disagree or duplicate), **boundary error** (the gap belongs to a skill that doesn't currently own it). | The gap is too vague to classify. The skill asks the user to refine it. |
| Originating skill identified | The skill that should have caught the gap is identified — from the gap report's path, from `--target-skill`, or by matching the gap description to existing audit content. | Multiple skills could legitimately own the gap. The skill surfaces the candidates and asks the user to pick. |
| Gap not already covered | The gap isn't already a check in the originating skill (i.e., the audit didn't run, or ran with the wrong filters). | The gap is already covered. The skill reports this and recommends re-running the audit with corrected filters; no edit needed. |
| Boundary check | The gap doesn't more naturally belong to a different audit. | The gap belongs elsewhere. The skill recommends moving the proposed change to the better-fit skill. |

### Stage 2 — Locate and analyse impact

Find the affected files and assess ripple effects.

| Stage check | Expectation | Blocker signal |
| --- | --- | --- |
| `SKILL.md` location | The originating skill's `SKILL.md` is found in the playbook clone. | Skill missing. The skill stops and asks the user whether the playbook path is correct. |
| Affected sections identified | The specific layer table, detection-logic step, threshold list, or implementation-plan block to modify is located, with line numbers. | The proposed change touches more than three sections in one `SKILL.md`. The skill flags this as a refactor scope rather than a self-improve scope and asks the user to confirm. |
| Ripple-effect map | Adjacent files that may need changes are identified: README skill index row, README per-skill summary, CLAUDE.md rules, `pre-audit-setup/SKILL.md` hook, other audits' boundary tables that mention the originating skill, MEMORY.md if a project-wide preference is implied. | None — ripple effects are surfaced as items in the plan, not as blockers. |
| Graphify centrality assessed | When the playbook's own knowledge graph is present (built by running `/pre-audit-setup` from inside the playbook clone), the originating skill's centrality is recorded. God-node skills (high inbound boundary-table references) get a "high blast radius" tag, which raises the bar for the proposed edit. | None — the centrality assessment informs how cautious the proposed edit should be, not whether to proceed. |
| Recursive-self-edit detected | When the originating skill is `/system-self-improve` itself, the skill flags this and the confirmation prompt downstream becomes the stronger recursive variant. | None — recursion is supported, not blocked. |

### Stage 3 — Propose the edit

Generate the minimal, reversible edit and an accompanying plan.

| Stage check | Expectation | Blocker signal |
| --- | --- | --- |
| Edit is minimal | The proposed diff is the smallest change that closes the gap: add a check at the end of the relevant layer, refine the wording of an existing check, change a threshold default, update a detection-logic line. | The proposed diff exceeds 50 added or modified lines. The skill flags this and recommends manual authoring; some changes are too big for self-improve. |
| Edit is reversible | The change can be reverted by reverting a single git commit. The plan records the previous values for any threshold changes and the previous text for any wording changes. | None — reversibility is guaranteed structurally, not by a check. |
| Edit preserves SKILL.md structure | Frontmatter intact, layer numbering preserved, established section ordering preserved, boundary table syntax preserved. The two-phase flow convention is preserved. The status taxonomy convention is preserved. The Testing Philosophy across audits is preserved. The four-layer structural convention is preserved. | A proposed edit would break one of these. The skill rejects the proposal and asks the user to clarify the intent — these conventions are foundational. |
| No prohibited mutations | The edit does not delete a check, does not delete a skill, does not change the four-layer structural convention, does not change the established two-phase flow or Testing Philosophy across audits, does not edit any target-project files. | A proposed edit would do any of these. The skill rejects the proposal. |
| Cross-skill consistency edits included | Plan includes README index row update if a status changes, README per-skill summary update if behaviour changes, boundary table updates in adjacent skills, MEMORY.md update if a new durable preference is implied. | None — these are part of the plan output, not blockers. |
| Conventional Commits message drafted | The plan includes a draft commit message: `feat:` for a new check, `fix:` for a corrected detection, `chore:` for a threshold tweak, `docs:` for prose-only refinement. | None. |

### Stage 4 — Apply and verify

Confirm with the user and (on approval) mutate. **This stage only runs when `--apply` is set.** In dry-run mode the skill stops after stage 3 with the plan written to disk.

| Stage check | Expectation | Blocker signal |
| --- | --- | --- |
| Confirmation requested | The skill prints the diff summary and asks: *"Apply this improvement plan to the playbook? (yes/no)"*. When the recursive-self-edit flag from stage 2 is set, the prompt becomes: *"This change modifies the meta-improvement skill itself. The next run of `/system-self-improve` will use the new logic. Continue? (yes/no)"* with a follow-on recommendation to commit immediately so the change is bisectable. | The user answers `no`. The skill exits cleanly; the plan stays on disk for manual application or re-invocation. |
| Edits applied atomically | All file changes happen before the log is written. If any single edit fails (file-system error, malformed diff), all changes are rolled back. | A file-system error. The skill rolls back and reports. |
| Post-edit verification | Every modified `SKILL.md` still has valid frontmatter, every modified table is still well-formed Markdown, every cross-reference in boundary tables and README still resolves to an existing file or anchor, the four-layer structural convention is still intact in modified files. | A verification failure. The skill rolls back the changes and reports the inconsistency. |
| Log entry appended | A line is appended to `system-self-improve-log.md` at the playbook root recording: timestamp, gap source, originating skill, classification, summary of changes, and the suggested commit message. The log is append-only — never rewritten. | None. |
| Commit suggestion printed | The skill prints the suggested Conventional Commits message and the list of modified files for the user to commit. **The skill does not commit on its own** — that is a deliberate boundary. | None. |

## What this skill does

1. **Reads the playbook's own knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists in the playbook clone (built by running `/pre-audit-setup` from inside the playbook), the audit identifies god-node skills and weights ripple-effect analysis accordingly. The skill still runs in full when the graph is absent (it greps for cross-references instead).
2. **Confirms the playbook clone.** Detects the playbook root by looking for the conventional structure: a top-level `README.md` mentioning "architect-playbook", a `CLAUDE.md` with the project rules, a set of `<skill-name>/SKILL.md` directories. If the current working directory is not a playbook clone and `--playbook-path=` is not supplied, the skill stops.
3. **Resolves the input source** in the priority order described above.
4. **Walks Stage 1 — Weakness diagnosis** and reports the gap classification.
5. **Walks Stage 2 — Locate and analyse**, including the ripple-effect map and Graphify centrality (when present). Flags recursive self-edits.
6. **Walks Stage 3 — Propose the edit**, generating the minimal diff plus the cross-skill consistency edits. Verifies the edit doesn't violate the structural conventions.
7. **Writes Layer 0 — the diagnostic snapshot** to `.architect-audits/system-self-improve/snapshot.md` and prepends the same content to `improvement-plan.md`.
8. **Writes the proposal** to `.architect-audits/system-self-improve/`:
   - `improvement-plan.md` — the human-readable proposal with reasoning, the proposed diff, ripple effects, suggested commit message.
   - `improvement-plan.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), input source, originating skill, classification, recursive flag, applied filters, run mode (`dry-run` or `apply`).
   - `proposed-changes.diff` — the unified diff that would be applied.
9. **Stage 4 only runs when `--apply` is set.** Prints the summary and the diff in chat, asks the confirmation question (or the recursive variant), and on `yes` performs the edits, runs post-edit verification, appends to the log, and prints the suggested commit message and modified-file list.

## Implementation steps

### Step 1 — Confirm the playbook clone

Detect the playbook by looking for the conventional structure at the current working directory (or the path passed via `--playbook-path=`):

- A `README.md` at the root mentioning "Architect Playbook" or "architect-playbook" in its first heading.
- A `CLAUDE.md` at the root with the project rules.
- At least one `<skill-name>/SKILL.md` directory at the root.

If none of these resolves, stop with:

> `/system-self-improve` requires a playbook clone. The current directory does not look like a playbook clone, and `--playbook-path=<path>` was not supplied.

### Step 2 — Resolve the input source

Walk the priority order:

1. Scan for `review-gap-report.md` files. Default scan path is `.architect-audits/*/review-gap-report.md` in the current working directory (which is *not* the playbook directory in the typical case — review reports live in the target project). When `--gap-report=<path>` is supplied, use that exclusively.
2. Honour `--gap` plus `--target-skill` when supplied.
3. Honour `--from-audit-history` and walk `.architect-audits/*/findings.json` looking for systematic patterns.

Record the resolved input source in metadata.

### Step 3 — Run Stage 1 (Weakness diagnosis)

For each blocker check, walk the logic. When a blocker triggers, stop and write the diagnostic snapshot plus a partial `improvement-plan.md` explaining why the run could not advance, then exit.

### Step 4 — Run Stage 2 (Locate and analyse impact)

Find the originating skill's `SKILL.md` in the playbook. Locate the specific section to modify by structural search:

- For "missing check" — find the relevant layer table; the new check goes at the end (additive, not insertive).
- For "weak check" — find the existing check row; record its current text for the reversibility log.
- For "wrong threshold" — find the threshold in the Usage block, the layer table, and the metadata schema.
- For "stale detection logic" — find the relevant detection-logic step.
- For "cross-skill drift" — find the boundary table entries in both skills.
- For "boundary error" — find both the source and destination skills.

Build the ripple-effect map: every adjacent file that needs a coordinated change.

When Graphify is present, query the graph for the originating skill's centrality (inbound references from other skills' boundary tables, README per-skill summaries, MEMORY.md). High-centrality skills get a "high blast radius" tag in the plan.

### Step 5 — Run Stage 3 (Propose the edit)

Generate the minimal diff. Verify against the prohibited-mutations list:

- No deleted checks.
- No deleted skills.
- No changes to the four-layer structural convention.
- No changes to the established two-phase flow convention.
- No changes to the Testing Philosophy preserved across audits.
- No edits to any target-project file.

Generate accompanying changes for every entry in the ripple-effect map. Draft the Conventional Commits message.

Write the plan files to `.architect-audits/system-self-improve/`. The diff goes into `proposed-changes.diff` as a unified diff covering every affected file.

### Step 6 — Print the chat summary

In dry-run mode, print:

- The originating skill, the classification, and the proposed change in one or two sentences.
- The list of files the plan would touch.
- The suggested commit message.
- A reminder that no files have been modified, and an instruction to re-run with `--apply` to proceed.

In `--apply` mode, additionally print the unified diff and ask the confirmation question. On `no`, exit. On `yes`, advance to Stage 4.

### Step 7 — Run Stage 4 (Apply and verify), only when `--apply` and confirmed

Apply the edits atomically. Run post-edit verification. On any failure, roll back every change.

Append a log entry to `system-self-improve-log.md`:

```markdown
## 2026-04-26T13:47:00Z — security-audit (missing-check)

**Source:** .architect-audits/security-audit/review-gap-report.md
**Originating skill:** security-audit
**Classification:** missing-check
**Summary:** Added Layer 1 check for `prompt()` usage as an XSS vector. Updated boundary table reference to confirm `/security-audit` ownership.
**Files modified:** security-audit/SKILL.md, README.md
**Suggested commit:** `feat(security-audit): add prompt() XSS vector to Layer 1 — input handling`
```

Print the commit suggestion and the modified-file list. Do not commit.

## Findings file shape

`improvement-plan.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:08Z",
  "runMode": "dry-run",
  "playbookPath": "/Users/bense/Coding/Consulting/Global/architect-playbook",
  "playbookCommit": "08a38d8",
  "inputSource": {
    "type": "review-gap-report",
    "path": "/Users/bense/Coding/Consulting/some-project/.architect-audits/security-audit/review-gap-report.md"
  },
  "snapshot": {
    "skillCounts": { "ready": 14, "stub": 1 },
    "logEntryCount": 7,
    "graphifyPresent": true
  },
  "stages": [
    {
      "stage": "weakness-diagnosis",
      "outcome": "advanced",
      "classification": "missing-check",
      "originatingSkill": "security-audit",
      "alreadyCovered": false,
      "boundaryConfirmed": true
    },
    {
      "stage": "locate-and-analyse",
      "outcome": "advanced",
      "skillPath": "security-audit/SKILL.md",
      "affectedSections": ["Layer 1 — Authentication, authorization, and sessions table"],
      "rippleEffectMap": ["README.md (per-skill summary)"],
      "graphifyCentrality": { "skill": "security-audit", "inboundReferences": 4, "tag": "medium-blast-radius" },
      "recursiveSelfEdit": false
    },
    {
      "stage": "propose-the-edit",
      "outcome": "advanced",
      "diffSummary": "Add `prompt() XSS vector` row to Layer 1 table; mention in README per-skill summary.",
      "diffLineCount": 12,
      "prohibitedMutationsTriggered": [],
      "suggestedCommit": "feat(security-audit): add prompt() XSS vector to Layer 1 — input handling"
    },
    {
      "stage": "apply-and-verify",
      "outcome": "skipped",
      "blockerReason": "dry-run mode; re-run with --apply to proceed"
    }
  ]
}
```

`improvement-plan.md` mirrors the same content in human-readable form: snapshot at the top, then a section per stage with the rationale, the affected sections with line numbers, the ripple-effect items, and the proposed commit message. `proposed-changes.diff` carries the unified diff. `metadata.json` carries skill identity, timestamps, the run mode, the playbook path and commit, the input source, the recursive flag, and the Graphify presence.

## Idempotency and reversibility rules

- Re-running in dry-run mode overwrites `improvement-plan.md`, `improvement-plan.json`, `snapshot.md`, `metadata.json`, and `proposed-changes.diff` in place.
- `system-self-improve-log.md` is **append-only**. It is never rewritten or truncated by this skill.
- Every applied change is reversible by reverting a single git commit (the user's commit, not one this skill makes — the skill never commits).
- Threshold changes record the previous value in the plan and the log so the user can revert without re-deriving it.
- Wording changes record the previous text in the plan and the log.
- The skill never deletes a check, a skill, or any file outside its own `.architect-audits/` directory.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `not a playbook clone` | The current directory and `--playbook-path` (when set) don't look like a playbook clone. | Run from inside the architect-playbook clone, or pass `--playbook-path=<path>`. |
| `no usable input` | No `review-gap-report.md` found, no `--gap` supplied, `--from-audit-history` produced nothing. | Provide one of the three input modes. The skill prints how-to instructions for each. |
| Gap is too vague to classify | The gap report or `--gap` description doesn't allow the skill to determine what kind of weakness it is. | The skill asks the user to refine the gap with concrete examples and one of the supported classifications. |
| Multiple candidate originating skills | The gap could legitimately be owned by more than one audit. | The skill lists the candidates and asks the user to pick via `--target-skill`. |
| Gap is already covered | An existing check would have caught the gap had it been run with the right filters. | The skill recommends re-running the originating audit with the corrected filters and exits without proposing an edit. |
| Proposed diff exceeds 50 lines | The change is too large to be considered a self-improve. | The skill recommends manual authoring and exits without writing a `proposed-changes.diff`. |
| Prohibited mutation triggered | The proposed edit would delete a check or skill, or change a foundational convention. | The skill rejects the proposal and exits with an explanation of which boundary was hit. |
| Post-edit verification failure | Frontmatter, table syntax, cross-references, or structural conventions were broken by the applied edit. | All changes are rolled back atomically. The skill reports the inconsistency. The user fixes the input or refines the gap and re-runs. |
| Recursive self-edit | The proposed edit modifies `system-self-improve/SKILL.md` itself. | Continue, but use the stronger confirmation prompt. After applying, the skill recommends committing immediately so the change is bisectable. |
| Knowledge graph missing | `/pre-audit-setup` has not been run from inside the playbook clone. | Continue. Record `noGraphify: true` in metadata. The ripple-effect analysis falls back to grepping cross-references; the centrality tag in the plan is omitted. |
| User answers `no` at the confirmation prompt | The user reviewed the diff and decided not to apply. | Exit cleanly. The plan and the diff stay on disk for manual application or re-invocation. |

## What this skill explicitly does NOT do

- Read or edit any target-project file. The skill only reads gap reports and pattern data **about** target codebases, and only edits the playbook clone.
- Delete any check from any audit. Improvements are additive or refining.
- Delete any skill from the playbook.
- Change the four-layer structural convention used by every audit.
- Change the established two-phase flow convention used by every audit.
- Change the Testing Philosophy preserved across audits.
- Change the established status taxonomy across audits (`present | partial | missing | violation` plus informational Layer 0).
- Commit any change to git. The skill prints the suggested Conventional Commits message and the modified-file list; the user commits.
- Push, open a pull request, or otherwise interact with any remote.
- Skip the confirmation prompt under `--apply`. There is no flag to bypass it.
- Operate on globally-installed skill copies under `~/.claude/skills/`. Improvements must land in the source repo and be re-installed.
- Run any code, including the audits whose findings it might be analysing.
- Decide on its own that an audit's threshold is wrong without an input gap. Pattern-based suggestions from `--from-audit-history` are surfaced as candidates only, not as automatic edits.

## Recursive-self-edit handling

When the originating skill is `/system-self-improve` itself, the run proceeds normally through stages 1, 2, and 3, with the recursive-self-edit flag set in metadata. Stage 4's confirmation prompt is replaced by the stronger variant:

> "This change modifies the meta-improvement skill itself. The next run of `/system-self-improve` will use the new logic. Continue? (yes/no)"

After the edits are applied, the skill prints an additional reminder:

> "Recursive self-edit applied. Strongly recommended: commit this change immediately so it is bisectable, and run `/system-self-improve` once with no input to confirm the meta-skill still operates correctly."

This is the only stage where the skill modifies its own behaviour for the next invocation.
