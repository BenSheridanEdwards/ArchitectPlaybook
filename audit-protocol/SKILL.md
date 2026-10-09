---
name: audit-protocol
description: Shared run protocol, scripts, and reporting rules that every Architect Playbook audit follows. Audits load it by path; it is not run on its own.
user-invocable: false
disable-model-invocation: true
---

# Audit protocol

Every Architect Playbook audit records its results through `scripts/audit_run.py` and follows [the run protocol](references/run-protocol.md).

- **The script owns the exact parts:**
  - run identity;
  - the check catalog;
  - evidence verification;
  - validation against the findings contract;
  - report rendering;
  - atomic publication.
- **The audit owns the judgement.**

Audits never write `findings.json`, `metadata.json`, `findings.md`, or `snapshot.md` by hand.

## Usage

Audits call the script by path from their own skill directory. In Claude Code, `${CLAUDE_SKILL_DIR}` is the audit's skill directory. Other agents should use the directory that contains the audit's `SKILL.md`.

```bash
python3 "${CLAUDE_SKILL_DIR}/../audit-protocol/scripts/audit_run.py" begin <audit-name>
```

| Command | Purpose |
| --- | --- |
| `begin <audit> [--since <ref>] [--enrichment <name>] [--threshold key=value] [--restart]` | Stage a run with every catalog check pending. If the audit has `scripts/collect.py`, run it. Name enrichment flags without dashes, as in `--enrichment with-run`. |
| `record <audit> <check> --status <status> --tier <tier> --evidence <entry> ...` | Record one evaluated check. Evidence is verified when you record it, and again before publication. `--tier` is required unless the status is `present`. |
| `judge <audit> <check> --judgement <judgement> [--reason <text>]` | Change only the judgement of a recorded `partial`, `missing`, or `violation` result, from the model or the collector. `noted` and `dismissed` need `--reason`. |
| `not-applicable <audit> <check> --reason <text>` | Record a check that does not apply to this repository. |
| `not-evaluated <audit> <check> --reason <text>` | Record an applicable check you could not evaluate. Use `--remaining` instead of a check to record every still-pending check, for example the checks outside a `--since` scope. |
| `audit-not-applicable <audit> --reason <text>` | Record that the whole audit does not apply. |
| `snapshot <audit> --set key=value [--narrative <text>]` | Record Layer 0 facts. |
| `hypothesis <audit> --check <check> --note <text>` | Record an unverified suspicion for the report appendix. |
| `status <audit>` | List the checks that still need a result. |
| `finish <audit>` | Verify, render, and publish the run, then print the chat summary. Refuses while more than five checks are `act-on`. |
| `decide <check> --decision <kind> --reason <text> --owner <name> [--scope <path or glob>]` | Record a decision so later audits stop asking the user to act on a finding. |
| `hotspots [--months 6] [--top 25]` | List the most frequently changed files, to prioritise reading. |

To audit a repository other than the current one, pass `--repository <path>` before the command. The `--worktree` flow uses this.

## What this skill does

1. **Gives every audit one run lifecycle:** begin, record each check, finish.
2. **Makes omission impossible.** Every catalog check starts pending, and a run with a pending check cannot be published.
3. **Verifies evidence mechanically:**
   - cited files and lines must exist;
   - quoted text must appear at the cited lines;
   - search counts and file counts are re-run over the files Git does not ignore;
   - every result rests on at least one entry the script checked, so commands and notes never stand alone;
   - secret-looking text is rejected.
4. **Validates the whole run with the score calculator's own contract code,** reading the published bytes back the way the calculator does, so anything published is exactly what `/repository-quality-score` accepts.
5. **Serialises commands.** Commands that change a run hold a lock on it, so parallel commands wait instead of losing updates.
6. **Renders `findings.md` and `snapshot.md` from the JSON,** so the human and machine reports cannot drift.
7. **Publishes all four files with `findings.json` last** as the completion marker. A failed write restores the previous files, and an interrupted run leaves a set whose identities disagree, which consumers reject.
8. **Remembers decisions.** A user's accepted risks in `.architect-audits/decisions.json` stay visible in the report but stop appearing as things to act on.

## Implementation steps

These are the steps an audit follows. [The run protocol](references/run-protocol.md) explains each one, including the evidence forms, judgement buckets, chat format, and plan format.

1. Resolve the target repository. With `--worktree`, create or reuse the worktree as the run protocol describes, and pass `--repository <worktree>` to every command.
2. Run `begin`. Read the pending list it prints.
3. Record Layer 0 facts with `snapshot`.
4. Evaluate every pending check, and record each one with `record`, `not-applicable`, or `not-evaluated`.
5. Run `finish`. If it reports problems, fix the recorded evidence, or demote extra act-on findings with `judge`, and run `finish` again. Never write the findings files by hand.
6. Present the chat summary in the format the run protocol defines, then offer the implementation plan.

## What this skill explicitly does NOT do

- Decide any status. The audit judges; the script records, verifies, and publishes.
- Run on its own or appear as a slash command.
- Write outside `.architect-audits/`.
- Calculate a score. That is `/repository-quality-score`.
- Hide a finding because of a recorded decision. The status stays honest, and only the judgement changes.
