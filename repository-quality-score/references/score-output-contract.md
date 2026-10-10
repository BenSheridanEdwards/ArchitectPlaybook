# Repository Quality Score output contract

## Input relationship

`checks.json` defines what an audit grades. `findings.json` records the result of
grading those checks. `metadata.json` proves which run and repository state the
findings describe. Repository Quality Score validates all three before using a
check result.

Canonical findings use schema `2.0.0` and the full catalog `checkId`. An
applicable evaluated check contributes according to status. A non-applicable
check is excluded. An applicable check that was not evaluated is excluded and
reduces assessment coverage.

Canonical evidence entries must be nonblank strings. Every applicable evaluated
check needs at least one entry, for both complete and degraded evidence. A
candidate that violates this structure is excluded; it cannot contribute a score
or assessment coverage. Not-applicable and not-evaluated checks may have empty
evidence lists. The calculator validates presence, not citation truth: the audit
protocol verifies evidence against the repository when recording and finishing
a run.

## Score formulas

```text
check earned points = check weight × status point value

audit score = sum(check earned points)
              / sum(applicable evaluated check weights)
              × 100

overall score = sum(audit score × audit category weight)
                / sum(included audit category weights)

coverage = sum(coverage weights of applicable evaluated checks)
           / sum(coverage weights of applicable checks)
           × 100
```

A check's weight is its severity weight when the policy defines
`severityWeights` and its catalog uses schema `1.2.0`; otherwise it is the
standard or soft weight. A check's coverage weight is its severity weight in
the first case and 1 in every other case, so coverage without severity
weighting is the plain share of checks evaluated.

Scores use decimal arithmetic and half-up rounding to the precision in the
policy. The quality band is assigned after rounding.

## Coverage fields

| Field | Meaning |
| --- | --- |
| Catalog coverage | Loaded policy-listed catalogs divided by policy audit count |
| Audit coverage | Valid current-commit audit runs divided by policy audit count |
| Applicable-check coverage | Coverage weight of evaluated applicable checks divided by the coverage weight of all known applicable checks in selected runs (`coverage.evaluationPercent`, and `coverage` per category) |
| Check-count coverage | Evaluated applicable checks divided by all known applicable checks, each counted once (`coverage.checkEvaluationPercent`, and `checkCoverage` per category) |
| Scored audit count | Selected audits with at least one applicable evaluated check |
| Non-applicable audit count | Completed audits that proved the whole domain does not apply |

The two check coverage figures are equal unless a selected audit is weighted
by severity. `coverage.coverageWeightEvaluated` and
`coverage.coverageWeightApplicable` carry the weighted totals.

Coverage and quality are intentionally separate. Missing evidence never becomes
an automatic pass or failure.

## Result status

`official` requires a canonical, catalog-compatible, complete, unfiltered,
non-degraded audit run for every policy audit on the current clean source commit.
Complete means every applicable check was evaluated, which is 100 percent on
both check coverage figures.

`provisional` means a numeric score is available but one or more official
conditions are not met. `statusReasons` provides stable reason codes and human
messages.

`unavailable` means no valid category score can be calculated. In this case
`overallScore` and `qualityBand` are `null`.

## score.json

The file contains:

- output schema version `1.1.0` and the scoring-policy version;
- run identity and timestamps;
- target repository name and Git commit;
- result status and reasons;
- overall score and quality band;
- catalog, audit, and check coverage, including both check coverage figures;
- per-audit scores, check counts, and both check coverage figures;
- highest-impact deductions, each with its check's `severity` when the catalog
  rates one;
- missing audits and excluded candidates; and
- catalog versions and fingerprints used by the calculation; and
- the applied status points, check weights, severity weights
  (`scorePolicy.severityWeights`, empty under a policy without them), and
  per-audit weights from the fingerprinted score policy.

Schema `1.1.0` added `scorePolicy.severityWeights`,
`coverage.checkEvaluationPercent`, `coverage.coverageWeightEvaluated`,
`coverage.coverageWeightApplicable`, per-category `checkCoverage`, and deduction
`severity`. Every schema `1.0.0` field keeps its `1.0.0` value whenever no check
is weighted by severity: when every selected catalog uses schema `1.1.0`, or
the policy has no `severityWeights`.

The file does not duplicate raw evidence or absolute paths. Follow the relative
source report pointer to inspect evidence in the originating audit.

## score.md

The human report presents status warnings first, followed by coverage, category
scores, highest-impact deductions, excluded inputs, and the policy explanation.
An unavailable report never displays a fabricated numeric score.

## snapshot.md

The snapshot records worktrees inspected, candidates found and selected,
catalog versions, current commit, source cleanliness, and exclusion reasons. It
is diagnostic input inventory, not a second score report.

## metadata.json

Metadata contains the score run identifier, timestamps, skill and policy
versions, target commit, worktree-discovery mode, the applied score policy,
input file fingerprints, catalog fingerprints, and final status reason codes.
Its run identifier must match `score.json`.

Each JSON input is parsed and fingerprinted from the same stable byte snapshot.
Immediately before publication, the calculator rechecks every fingerprint plus
the target commit and source-tree cleanliness. A change triggers one complete
retry; a second change produces no new completion marker.

## Comparability

Compare two scores only when their scoring-policy versions match and every
included audit has a compatible catalog version. A changed policy or baseline
can change a score even when source code is unchanged.
