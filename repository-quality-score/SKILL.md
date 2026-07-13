---
name: repository-quality-score
description: Calculate an explainable Repository Quality Score from Architect Playbook audit outputs. Use after audits finish, whenever a user asks for a repository score, audit roll-up, quality summary, category comparison, or assessment coverage.
trigger: /repository-quality-score
---

# /repository-quality-score

Convert completed Architect Playbook audit evidence into deterministic category
scores, one overall score, coverage, and an explanation of every deduction or
exclusion. The bundled Python calculator owns all numeric decisions; do not
estimate or adjust scores in prose.

This is a read-only aggregation skill. It reads audit artifacts and writes only
to `.architect-audits/repository-quality-score/`. It does not audit source code
again and does not modify audit findings.

## How this differs from neighboring skills

| Concern | Owning skill |
| --- | --- |
| Determine whether a repository satisfies a domain baseline | The relevant `*-audit` skill |
| Prepare dependency-graph context | `/pre-audit-setup` |
| Install optional audit tools | `/preflight` |
| Aggregate completed audit results into scores | `/repository-quality-score` |
| Review a pull request with Ben's principles | `/ben-architect-review` |
| Evolve an audit after a real review gap | `/system-self-improve` |

## Usage

```text
/repository-quality-score
/repository-quality-score --current-worktree-only
```

The default inspects the current worktree and every registered Git worktree for
the same repository. This is necessary because audits run with `--worktree`
write their reports into separate worktrees.

Use `--current-worktree-only` when reports outside the current worktree must be
ignored.

## What this skill does

1. Resolves the target repository from the current working directory.
2. Resolves this installed skill folder and its sibling audit catalogs.
3. Loads the versioned `score-policy.json`.
4. Discovers audit findings in the current and registered worktrees.
5. Validates repository commit, run identity, catalog version, check identity,
   applicability, evaluation state, status, filters, and overrides.
6. Selects one atomic current-commit result per audit. It never merges checks
   from separate runs.
7. Calculates per-audit scores and an equal-category overall score using the
   bundled deterministic calculator.
8. Separately calculates catalog, audit, and applicable-check coverage.
9. Classifies the result as `official`, `provisional`, or `unavailable`.
10. Writes `score.md`, `score.json`, `snapshot.md`, and `metadata.json`.
11. Prints a concise chat summary and points to the full report.

## Scoring summary

The policy file is authoritative:

| Check type or status | Contribution |
| --- | ---: |
| Standard check | Weight 1.0 |
| Soft check | Weight 0.5 |
| `present` | 100 percent of its weight |
| `partial` | 50 percent of its weight |
| `missing` | 0 percent |
| `violation` | 0 percent |
| Non-applicable | Excluded |
| Applicable but not evaluated | Excluded and reduces coverage |

Each audit is normalized to 100 before audit categories are averaged. Missing
audits reduce coverage; they are not treated as zero scores.

Read `references/score-output-contract.md` when a field-level explanation of
inputs, score status, or outputs is needed.

## Implementation steps

### Step 1 — Resolve the target

Use the current working directory unless an enclosing Git repository root is
present, in which case use that root. Do not score the Architect Playbook clone
when the user intended a different target; ask only when the target is genuinely
ambiguous.

### Step 2 — Resolve the calculator

Resolve this file's directory, then locate:

```text
scripts/calculate_repository_quality_score.py
score-policy.json
```

Prefer `python3`; use `python` when `python3` is unavailable. Do not copy the
formula into the chat or implement a second calculator.

### Step 3 — Run deterministic calculation

Invoke the script with the resolved target. Pass
`--current-worktree-only` only when the user supplied that flag. The script's
target and skill-root arguments are internal execution plumbing, not alternate
scoring policies.

If the script returns an unexpected internal error, quote the error and stop.
Do not invent a fallback score.

### Step 4 — Verify outputs

Confirm all four expected files exist and `score.json` and `metadata.json` share
the same run identifier. Do not report success from directory presence alone.

### Step 5 — Report concisely

For an official or provisional result, print:

```text
Repository Quality Score: <score>/100 — <band>
Status: <Official or Provisional>
Audit coverage: <completed>/<policy audits>
Highest-impact category: <name and score>
Full report: .architect-audits/repository-quality-score/score.md
```

For an unavailable result, omit the number and print the blocking reasons plus
the report path when a diagnostic report was written.

## Output files

```text
.architect-audits/repository-quality-score/
  score.md       human-readable score, coverage, categories, and deductions
  score.json     machine-readable deterministic result
  snapshot.md    input candidates, worktrees, versions, and exclusions
  metadata.json  execution identity and input fingerprints
```

Aggregate reports reference source audit findings but do not copy raw evidence,
secret-like values, credential-bearing repository URLs, or absolute local
paths.

## Idempotency rules

- Re-running against identical logical inputs produces identical scores,
  categories, coverage, and ordering.
- Run identifiers and timestamps may change.
- The four report files are replaced only after complete new content is ready.
- `score.json` is replaced last as the completion marker.
- A failed validation does not delete audit findings or unrelated files.
- A concurrent score run fails loudly on the score lock instead of racing.

## Failure modes and remediation

| Failure | Behavior and remediation |
| --- | --- |
| No audit findings | Return unavailable and tell the user to run audits |
| Only some audits completed | Produce a provisional score and list missing audits |
| Results target another commit | Exclude them and rerun those audits on the current commit |
| Findings are legacy-shaped | Use exact safe mappings when possible, always provisional, and name audits to rerun |
| Unknown or duplicate check identifier | Exclude that audit candidate and rerun the audit |
| Catalog missing from installation | Reinstall the complete playbook; do not search unrelated directories |
| Filter or threshold override used | Produce a provisional score and record the customization |
| Skipped or failed check evaluation | Reduce coverage and produce a provisional score |
| Findings change while scoring | Retry once, then stop with an unavailable result |
| Git is unavailable | Inspect only the current directory and never call the result official |
| Output path escapes through a symlink | Refuse to write and report the unsafe path |

## What this skill explicitly does NOT do

- Run or rerun audits.
- Inspect source code to fill missing results.
- Change check outcomes, weights, bands, or policy based on model judgment.
- Treat a missing audit as a failed audit.
- Combine results from different commits or individual checks from different
  runs.
- Mutate application source, audit findings, Git state, branches, worktrees,
  hooks, or configuration.
- Fail continuous integration based on a numeric threshold.
- Publish, upload, or transmit reports.
- Claim security, accessibility, or regulatory certification.
- Replace the detailed audit reports or human architectural judgment.
