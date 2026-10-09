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

Audits read the repository and write only under `.architect-audits/<audit-name>/`.
The playbook still runs one agent per checkout, so give each parallel audit its
own worktree with `--worktree`. Use it too when the findings will be fixed on
their own branch.

1. Keep worktrees out of the main checkout's status first. Creating one must
   never make that tree dirty while another audit is running there:

   ```bash
   exclude="$(git rev-parse --git-common-dir)/info/exclude"
   grep -qxF '.worktrees/' "$exclude" 2>/dev/null || echo '.worktrees/' >> "$exclude"
   ```

2. Reuse `.worktrees/<audit-name>` if it is already a worktree of this
   repository. Otherwise create it. Reuse the `audit/<audit-name>` branch when it
   exists, and never reset it, because it may hold fixes committed since the
   last run:

   ```bash
   if git show-ref --verify --quiet refs/heads/audit/<audit-name>; then
     git worktree add .worktrees/<audit-name> audit/<audit-name>
   else
     git worktree add -b audit/<audit-name> .worktrees/<audit-name> HEAD
   fi
   ```

   An existing branch is audited as it stands. If the user wants the current
   `HEAD` audited instead, ask before moving the branch.

3. A new worktree has no knowledge graph, because `graphify-out/` is ignored.
   Copy it when the worktree is at the commit the graph was built from:

   ```bash
   if [ -f graphify-out/graph.json ] && git check-ignore -q graphify-out \
     && [ "$(git rev-parse HEAD)" = "$(git -C .worktrees/<audit-name> rev-parse HEAD)" ]; then
     cp -R graphify-out .worktrees/<audit-name>/
   fi
   ```

   Otherwise run `/pre-audit-setup` in the worktree, or run without the graph.
   The graph only guides where to read first; a run without it is still
   official (Architecture Decision Record 0005).

4. Pass `--repository .worktrees/<audit-name>` before every protocol command,
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
   - `--enrichment with-run`: records the enrichment flag `--with-run`. Name the
     flag without its dashes, or write `--enrichment=--with-run`.
   - `--threshold key=value`: records a threshold override. The collector
     receives it, so an option such as `months=12` changes what it collects.
   - `--filter=<argument>`: records any other filter the user passed.
   - `--since <ref>`: records the files changed since that reference, and
     marks the run filtered. Evaluate the checks those files can affect. Then
     record the rest in one step: `not-evaluated <audit-name> --remaining
     --reason "outside the --since scope"`.

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

   To change only the judgement of a `partial`, `missing`, or `violation`
   result, whether you or the collector recorded it, use `judge <audit-name>
   <check-id> --judgement <judgement> [--reason "<why>"]`. It keeps the status
   and evidence, and applies the same judgement rules as `record`. See
   [Judgement](#judgement).

   Use `audit-not-applicable <audit-name> --reason "<why>"` only after detection
   proves the audited technology is absent. A later per-check record replaces
   that whole-audit decision. `status <audit-name>` lists the checks still
   pending.

   Run protocol commands for one audit one at a time. The script locks the run
   while a command changes it, so parallel commands wait instead of losing
   updates. Still, a correction only means something after the record it
   corrects.
4. **Finish.** `python3 "$PROTOCOL" finish <audit-name>` refuses to publish
   while any check is pending, while more than five checks are `act-on`, or if
   the commit or working tree changed during the run. It then:
   - re-verifies every evidence entry;
   - validates the run with the Repository Quality Score calculator's own
     contract code;
   - renders `findings.md` and `snapshot.md` from the JSON;
   - publishes all four files, writing `findings.json` last as the completion
     marker. If a write fails, the previous files are restored. If the process
     is killed part-way, `findings.json` and `metadata.json` disagree on the
     run identity, and consumers reject the set rather than mixing two runs.

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

A check that falls back to a less precise method because the knowledge graph is missing is recorded with `--degraded "knowledge graph unavailable: <fallback used>"`.

A check you could not evaluate is never `partial`. Record it as `not-evaluated`
with the reason, for example `requires --with-network` or `test runner failed
to start: <error>`. When a tool degrades but you still evaluated the check from
other evidence, add `--degraded "<reason>"` to the record command.

## Evidence

Every evaluated check needs at least one entry the script can verify against
the repository: a citation, a file, a search, or a file count. Commands and
notes support a result but never stand alone. The script checks every entry
when you record it, and again when you finish. Use these forms:

| Form | Example | What is verified |
| --- | --- | --- |
| Citation | ``src/api/client.ts:42 — `fetch(url)` with no timeout`` | The file exists; the lines exist; every backtick-quoted fragment appears in those lines. |
| Citation range | ``src/api/client.ts:40-48 — retries are inline`` | The same, for the range. |
| File | ``tsconfig.json — `"strict": true` `` | The file exists; quoted fragments appear in it. Folders are not files. |
| Search | ``search: `dangerouslySetInnerHTML` in src → 0 matches`` | The script re-runs the search, and the count of matching lines must agree. Prefix the pattern with `re:` for a regular expression. |
| File count | ``files: `.github/workflows/*.yml` → 0 files`` | The script counts the files in the scope. Use it to show that something is absent or present. |
| Command | ``command: `npx tsc --noEmit` → 7 errors in 3 files`` | Nothing. It is recorded as reported tool output, and `findings.md` labels it "Reported". |
| Note | ``note: the team plans to drop the legacy client`` | Nothing. A free-text observation, labelled "Note". |

How entries are read:

- **Every entry that does not start with `note:`, `command:`, `search:`, or
  `files:` is a citation or a file.** Its path must exist. An observation that
  starts with a word such as `Next.js` must start with `note:`.
- **Separators.** Put ` — `, ` – `, ` -- `, ` - `, `: `, or a space between the
  path or line number and the note. A column after the line number, as in
  `src/a.ts:12:5`, is ignored.
- **Paths with spaces** go in backticks: `` `docs/My Notes.md`:3 — note ``.
- **Lines** end at a newline only, as in `grep -n`, and a trailing carriage
  return is ignored.
- **What is searched.** Searches and file counts cover tracked and untracked
  files that Git does not ignore. They skip `.architect-audits/`,
  `.worktrees/`, `graphify-out/`, `node_modules/`, symbolic links, binary files,
  and files over 8 MB. That is what ripgrep and `git grep` see.
- **Scopes.** `.` is the whole repository. A scope that names an existing file or
  folder is taken literally, so Next.js folders such as `app/[slug]` work.
  Otherwise the scope is a glob with Git's rules: `*` and `?` stay inside one
  folder, `**/` spans folders, a trailing `/**` matches everything inside, and
  `[...]` is a character class. So `src/*.ts` excludes `src/lib/a.ts`, and
  `src/**/*.ts` includes `src/a.ts`.
- **Regular expressions** use Python syntax. Write `\s` and `\d`, not POSIX
  classes such as `[[:space:]]`.
- **Secrets.** Never paste secrets. The script rejects entries that look like
  keys or tokens. Replace the secret with `<REDACTED>`, inside quotes too:
  ``src/payments.ts:4 — `stripeKey = "<REDACTED>"` `` verifies against the real
  line, because `<REDACTED>` matches any text. Keep some text around it.

Quote only what carries the signal.

Choose an evidence tier with `--tier`. It is required for `partial`, `missing`,
and `violation`, and defaults to `direct` for `present`:

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

## Collectors

An audit may ship `scripts/collect.py`, a deterministic, read-only collector
that decides the checks code can decide. `begin` runs it once:

```bash
python3 -B collect.py --repository <root> [--enrichment=<flag> ...] [--threshold=<key>=<value> ...]
```

It receives every enrichment flag and threshold the run records, and prints one
JSON object: `checks`, keyed by check identifier, with the same fields as
`record` (`status`, `evidence`, `evidenceTier`, `gap`, `remediation`,
`judgement`, `judgementReason`, `degradedReason`), or `applicability:
not-applicable` or `evaluationState: not-evaluated` with a `reason`; and
`snapshot`, an object of Layer 0 facts. Each result goes through the same
verification as a model's record. A rejected result stays pending, with a
warning, for the model to evaluate. Collector results are recorded as
`recordedBy: collector`, and because they are deterministic, their `command:`
entries may stand alone.

## Judgement

Every `partial`, `missing`, or `violation` result needs a judgement:

| Judgement | Use when |
| --- | --- |
| `act-on` | Fixing it is worth the team's time now. These become the chat's recommendations. At most five; `finish` refuses more. More than five usually means you are not filtering. |
| `consider` | Real, but lower value than the act-on items, or dependent on a product decision. |
| `noted` | Real, but accepted or out of scope for now. Needs `--reason`. |
| `dismissed` | Not a real problem here, such as a heuristic false positive. Needs `--reason`. Dismissed findings are listed in the report, so readers can audit your filtering. |

Status and judgement are separate. A dismissed or noted finding still reports
its true status, and the score stays honest.

A result recorded without `--judgement` is `act-on`, or `noted` when a
recorded decision covers it. A collector result takes the collector's own
judgement, or `act-on` if it gives none, so review collector checks with the
rest. When a recorded decision covers a collector result, the collector's
judgement is ignored and the finding is `noted` and linked to the decision.
To change a judgement without re-recording the evidence, run:

```bash
python3 "$PROTOCOL" judge <audit-name> <check-id> --judgement consider --reason "<why>"
```

`judge` works on any `partial`, `missing`, or `violation` result, from the
model or the collector. It refuses a check that is pending, `present`, not
applicable, or not evaluated. `noted` and `dismissed` need `--reason`, as they
do on `record`. When more than five checks are `act-on`, `finish` lists them in
report order and publishes nothing. Keep the five highest-value ones and demote
the rest with `judge`.

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

Decisions live in `.architect-audits/decisions.json`. Each `--scope` follows
the same rules as a search scope, so `src/legacy` covers everything in that
folder. When a later run records a non-present result for that check, and every
cited file falls inside the decision's scope, the script marks the finding
`noted` and links the decision.
To act on it anyway, pass `--judgement act-on` to `record` or `judge`, with
`--reason` explaining why the decision no longer holds. Decisions past their `--review-after` date are
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
