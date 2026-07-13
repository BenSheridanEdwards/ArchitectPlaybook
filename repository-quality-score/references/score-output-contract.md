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

## Score formulas

```text
check earned points = check weight × status point value

audit score = sum(check earned points)
              / sum(applicable evaluated check weights)
              × 100

overall score = sum(audit score × audit category weight)
                / sum(included audit category weights)
```

Scores use decimal arithmetic and half-up rounding to the precision in the
policy. The quality band is assigned after rounding.

## Coverage fields

| Field | Meaning |
| --- | --- |
| Catalog coverage | Loaded policy-listed catalogs divided by policy audit count |
| Audit coverage | Valid current-commit audit runs divided by policy audit count |
| Applicable-check coverage | Evaluated applicable checks divided by all known applicable checks in selected runs |
| Scored audit count | Selected audits with at least one applicable evaluated check |
| Non-applicable audit count | Completed audits that proved the whole domain does not apply |

Coverage and quality are intentionally separate. Missing evidence never becomes
an automatic pass or failure.

## Result status

`official` requires a canonical, catalog-compatible, complete, unfiltered,
non-degraded audit run for every policy audit on the current clean source commit.

`provisional` means a numeric score is available but one or more official
conditions are not met. `statusReasons` provides stable reason codes and human
messages.

`unavailable` means no valid category score can be calculated. In this case
`overallScore` and `qualityBand` are `null`.

## score.json

The file contains:

- output schema and scoring-policy versions;
- run identity and timestamps;
- target repository name and Git commit;
- result status and reasons;
- overall score and quality band;
- catalog, audit, and check coverage;
- per-audit scores and check counts;
- highest-impact deductions;
- missing audits and excluded candidates; and
- catalog versions and fingerprints used by the calculation.

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
versions, target commit, worktree-discovery mode, input file fingerprints,
catalog fingerprints, and final status reason codes. Its run identifier must
match `score.json`.

## Comparability

Compare two scores only when their scoring-policy versions match and every
included audit has a compatible catalog version. A changed policy or baseline
can change a score even when source code is unchanged.
