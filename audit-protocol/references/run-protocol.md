# Audit run protocol

Every Architect Playbook audit follows this protocol. It turns an audit from
"write four files that match a long contract" into "record one decision per
check". The protocol script owns run identity, Git state, validation, Markdown
rendering, and publication.

The script lives beside the audit skills:

```bash
PROTOCOL="${CLAUDE_SKILL_DIR}/../audit-protocol/scripts/audit_run.py"
python3 "$PROTOCOL" begin <audit-name>
```

Run every command from the target repository, or pass `--repository <path>`
before the command name.

## Running in a worktree (`--worktree`)

Audits are read-only and write only to `.architect-audits/<audit-name>/`. They
can therefore run in parallel in one checkout. Use `--worktree` when the
findings will be fixed on their own branch.

1. Create the worktree, or reuse it if `.worktrees/<audit-name>` is already a
   worktree of this repository:

   ```bash
   git worktree add -B audit/<audit-name> .worktrees/<audit-name> HEAD
   ```

2. Keep the worktree out of the main checkout's status, so it never makes that
   tree dirty:

   ```bash
   exclude="$(git rev-parse --git-common-dir)/info/exclude"
   grep -qxF '.worktrees/' "$exclude" 2>/dev/null || echo '.worktrees/' >> "$exclude"
   ```

3. Pass `--repository .worktrees/<audit-name>` before every protocol command,
   and read files from the worktree.

## The run, step by step

1. **Begin.** `python3 "$PROTOCOL" begin <audit-name> [options]` stages a run
   under `.architect-audits/<audit-name>/.staging/`. Every catalog check starts
   as `pending`. The script records:
   - the exact commit;
   - the working-tree state, measured before any output exists;
   - the execution options you pass.

   If the audit ships `scripts/collect.py`, begin runs it and records the checks
   it can decide deterministically.
   - `--enrichment <flag>`: records an enrichment flag such as `--with-run`.
   - `--threshold key=value`: records a threshold override.
   - `--since <ref>`: limits the run to files changed since that reference, and
     records a filter.

   Filtered and customised runs are always provisional for the Repository
   Quality Score.
2. **Record snapshot facts.** `python3 "$PROTOCOL" snapshot <audit-name> --set
   key=value` records Layer 0 facts. Values may be JSON. `--narrative "<text>"`
   adds a short prose summary. Snapshot facts are never graded.
3. **Record every check.** Each catalog check needs exactly one of:
   - `record <audit-name> <check-id> --status <status> --evidence "<entry>" ...`
     for an evaluated check;
   - `not-applicable <audit-name> <check-id> --reason "<why>"` when the check
     cannot apply to this repository;
   - `not-evaluated <audit-name> <check-id> --reason "<why>"` when the check
     applies but you could not evaluate it. This is honest and lowers coverage.
     It never lowers the score.

   Use `audit-not-applicable <audit-name> --reason "<why>"` only after detection
   proves the audited technology is absent. `status <audit-name>` lists the
   checks still pending.
4. **Finish.** `python3 "$PROTOCOL" finish <audit-name>` refuses to publish
   while any check is pending, or if the commit or working tree changed during
   the run. It then:
   - re-verifies every citation;
   - validates the run with the Repository Quality Score calculator's own
     contract code;
   - renders `findings.md` and `snapshot.md` from the JSON;
   - publishes all four files.

   It prints an Act on summary for the chat.

Never write `findings.json`, `metadata.json`, `findings.md`, or `snapshot.md`
by hand. A run that fails validation publishes nothing, and the previous
complete run stays in place.

## Statuses

| Status | Meaning |
| --- | --- |
| `present` | The invariant holds. |
| `partial` | The invariant mostly holds, with exceptions, or a soft check shows mixed adherence. |
| `missing` | A structural prerequisite is absent. |
| `violation` | Concrete code, configuration, or output breaks the invariant. |

A check you could not evaluate is never `partial`. Record it as `not-evaluated`
with the reason, for example `requires --with-network` or `test runner failed
to start: <error>`. When a tool degrades but you still evaluated the check from
other evidence, add `--degraded "<reason>"` to the record command.

## Evidence

Every evaluated check needs at least one verifiable evidence entry. The script
checks citations, file references, and searches against the repository when
you record them, and again when you finish. Use these forms:

| Form | Example | What is verified |
| --- | --- | --- |
| Citation | ``src/api/client.ts:42 — `fetch(url)` with no timeout`` | The file exists; the lines exist; every backtick-quoted fragment appears in those lines. |
| Citation range | ``src/api/client.ts:40-48 — retries are inline`` | The same, for the range. |
| File | ``tsconfig.json — `"strict": true` `` | The file exists; quoted fragments appear in it. |
| Search | ``search: `dangerouslySetInnerHTML` in src → 0 matches`` | The script re-runs the search and the count of matching lines must agree. Prefix the pattern with `re:` for a regular expression. |
| Command | ``command: `npx tsc --noEmit` → 7 errors in 3 files`` | Recorded as reported tool output. |

Free-text observations may accompany these but never stand alone.

- Quote only what carries the signal.
- Never paste secrets; write `<REDACTED>`. The script rejects entries that look
  like keys or tokens.

Choose an evidence tier with `--tier`:

- `direct`: you read the cited code, or ran the command, and it shows the
  status.
- `supported`: several pieces of evidence together establish the status.
- `inferred`: the evidence points one way but does not settle it.

A `violation` needs `direct` or `supported` evidence. If you suspect a problem
but cannot verify it, record a hypothesis instead:

```bash
python3 "$PROTOCOL" hypothesis <audit-name> --check <check-id> --note "<what you suspect and why>"
```

Hypotheses appear in an appendix and never affect a status or the score.

## Judgement

Every `partial`, `missing`, or `violation` result needs a judgement:

| Judgement | Use when |
| --- | --- |
| `act-on` | Fixing it is worth the team's time now. These become the chat's recommendations. Keep it to about five. More than that usually means you are not filtering. |
| `consider` | Real, but lower value than the act-on items, or dependent on a product decision. |
| `noted` | Real, but accepted or out of scope for now. Needs `--reason`. |
| `dismissed` | Not a real problem here, such as a heuristic false positive. Needs `--reason`. Dismissed findings are listed in the report, so readers can audit your filtering. |

Status and judgement are separate. A dismissed or noted finding still reports
its true status, and the score stays honest.

Before marking anything act-on, apply these filters:

- **Trace it.** Is the failure reachable from a real caller or input?
- **Is it a preference?** "I would have done it differently" is not a finding.
- **Is it consistent with the codebase's deliberate conventions?** Read the
  repository's decision records and the context intake first.
- **Is it a nitpick inflated to fill the list?**

## Recorded decisions

When the user rejects a recommendation for a lasting reason, offer to record
it:

```bash
python3 "$PROTOCOL" decide <check-id> --decision accepted-risk --reason "<why>" --owner "<who>" [--scope "src/legacy/**"] [--review-after 2027-01-31]
```

Decisions live in `.architect-audits/decisions.json`. When a later run records
a non-present result for that check, and every cited path falls inside the
decision's scope, the script marks the finding `noted` and links the decision.
To act on it anyway, pass `--judgement act-on` with `--reason` explaining why
the decision no longer holds. Decisions past their `--review-after` date are
flagged when the run finishes.

## Prioritising large repositories

You cannot read every file in a large repository. Spend reading where change
and risk concentrate:

```bash
python3 "$PROTOCOL" hotspots --months 6 --top 25
```

Read the hotspots and the knowledge graph's most central modules first. Record
what you sampled in your evidence. A search with a count is evidence about the
whole scope; a citation is evidence about one place.

## Chat output

After `finish` succeeds, print:

1. One header line: the audit, repository, short commit, and status counts.
2. **Act on.** Up to five act-on findings, highest severity first, numbered.
   For each:
   - the title;
   - why it matters, using the check's rationale;
   - the real consequence if ignored;
   - the smallest fix, naming the files and effort;
   - lettered sub-actions such as 2a and 2b, so the user can reply "2b" or
     "1 and 3".
3. One line counting dismissed and noted findings, if any.
4. The path to `findings.md`.

Do not print the full report unless the user asks.

With `--learn` or `--teach`, expand each act-on item for an engineer who is
learning:
- point to the exact lines;
- explain the principle and how it fails in practice;
- add "What you'll learn from fixing this".

Keep the numbering.

## Phase 2: the implementation plan

Ask once: "Generate an implementation plan for the act-on findings? (yes/no)".
On yes, write `.architect-audits/<audit-name>/implementation-plan.md`. Never
modify project files.

Write each task as a brief an agent can execute in a fresh session:

```markdown
### T1 — <outcome in one line> (`<check-id>`, high)

- Fix type: AUTO-FIX | ASK
- Enforcement: architecture | types | lint | test | documentation
- Current behaviour: <what happens now, with one citation>
- Desired behaviour: <what should happen>
- Files: <paths>
- Acceptance criteria: <observable checks that fail at the current commit and pass after the fix>
- Verification: <the exact command, and re-running this audit>
- Out of scope: <what not to touch>
```

- **AUTO-FIX** means the change is mechanical and a senior engineer would apply
  it without discussion.
- **ASK** means reasonable engineers could disagree, or the change touches
  security, data, public interfaces, or user-visible behaviour.

Choose the highest enforcement level that works:

1. Remove the mistake with architecture.
2. Make it unrepresentable with types.
3. Catch it with a lint rule whose error names the fix.
4. Catch it with a test.
5. Document it, last.

Order tasks so that each one leaves the repository working. Put prefactoring
first. Split wide rollouts, such as a new strict compiler flag, into batches.

Preserve an existing `implementation-plan.md` unless the user agrees to
regenerate it.
