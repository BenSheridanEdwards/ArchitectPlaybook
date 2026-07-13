# Repository Quality Score implementation plan

## Document control

| Field | Value |
| --- | --- |
| Status | Ready for product review and implementation |
| Branch | `feature/repository-quality-score` |
| Scope | Architect Playbook audit-result normalization and deterministic repository scoring |
| Intended reader | An implementation agent or maintainer starting without prior conversation context |
| Current implementation state | Planning only; Repository Quality Score does not exist yet |
| Last verified | 2026-07-13 |

## Purpose

Build a new `/repository-quality-score` skill that reads results produced by the
existing Architect Playbook audits and converts them into:

- one score from 0 to 100 for each completed audit category;
- one overall repository score from 0 to 100;
- an audit-coverage and check-coverage statement;
- an `official`, `provisional`, or `unavailable` result status;
- a deterministic explanation of every deduction and exclusion; and
- human-readable and machine-readable reports under
  `.architect-audits/repository-quality-score/`.

The feature is an aggregation layer. It does not inspect application source code
again, replace an audit, fix findings, certify compliance, or fail continuous
integration based on a score.

The manager has used a shortened label for the feature. Repository conventions
prohibit abbreviations in names, triggers, headings, prose, and identifiers, so
all repository artifacts use the full name `repository-quality-score` or
"Repository Quality Score". Do not create a shortened folder, trigger, class,
function, field, or heading.

## Outcome in one flow

```text
Installed audit checks.json catalogs
                    +
Audit-generated findings.json and metadata.json files
                    |
                    v
Repository Quality Score input discovery and validation
                    |
                    v
Deterministic per-check point calculation
                    |
                    v
Per-audit scores + equal-category aggregation
                    |
                    v
score.md + score.json + snapshot.md + metadata.json
```

The scoring calculator must never infer a pass from an omitted result. It must
distinguish repository quality from assessment coverage so that incomplete audit
evidence cannot look like a complete, high-quality assessment.

## Verified current repository state

Architect Playbook is a Markdown skill library with a Python standard-library
validator. There is no application binary or build step. Installation copies
complete top-level skill folders, which means a new top-level
`repository-quality-score/` folder will be discovered by both installers without
installer code changes.

There are 14 audit skills with 390 checks:

| Audit | Checks | Soft checks |
| --- | ---: | ---: |
| Accessibility | 23 | 0 |
| Agentic | 32 | 20 |
| Architecture | 19 | 5 |
| Bundle and build | 26 | 1 |
| Dependency | 21 | 4 |
| Documentation | 29 | 14 |
| Error handling | 28 | 4 |
| Linting | 27 | 4 |
| Performance | 31 | 5 |
| Quality gates | 20 | 5 |
| React | 30 | 11 |
| Security | 36 | 5 |
| Testing | 33 | 8 |
| TypeScript | 35 | 11 |
| **Total** | **390** | **93** |

Every audit has a `checks.json`, but the validator currently treats that file as
optional. Each catalog defines stable `checkId` values, layer membership,
expectations, violation signals, optional `softCheck`, and optional
`allowedStatuses`.

Current findings are not normalized enough for safe scoring:

- Most documented examples identify results with `check`, not `checkId`.
- Quality gates uses a `gates` array and the status `misconfigured`, although
  the shared taxonomy and its `checks.json` use `partial`.
- Committed dogfood reports use an `id` field and custom identifiers that do not
  necessarily match current catalog identifiers.
- Some audit examples use string evidence; others use arrays.
- Skipped framework or project-shape layers omit their checks from the results.
- Filters, threshold overrides, enrichment modes, Git commit, and clean-tree
  state are not represented uniformly.
- Reports have no shared findings-schema version or run identifier.
- Parallel audits using `--worktree` write findings into separate registered
  Git worktrees, not necessarily into the worktree where scoring is invoked.

These inconsistencies make normalization a prerequisite. Do not implement a
calculator that merely counts whatever entries happen to be present.

## Existing baseline-gate condition

Before this plan was added, the documented gates were run on Windows with
`python`:

```text
python scripts/validate-playbook.py
python -m unittest discover -s tests -p 'test_*.py'
```

The validator reported 21 existing errors and the test suite reported two
existing failures. The causes are Windows path normalization in README skill
link comparison and the tracked bootstrap symlink not materializing as a
directory when Windows symlink checkout is disabled. The Linux continuous-
integration checkout is the authoritative clean-checkout comparison until those
cross-platform issues are handled.

Implementation must not hide or relabel these failures. Before changing
production behavior, capture a clean Linux or continuous-integration-equivalent
baseline. Keep any Windows baseline repair in a separate prerequisite commit or
separate pull request so Repository Quality Score behavior remains reviewable.

## Scope

### Included

- A read-only `/repository-quality-score` skill.
- A bundled Python 3 standard-library calculator and report renderer.
- A versioned scoring-policy file.
- A normalized audit findings contract shared by all 14 audits.
- Migration instructions and examples in all 14 audit skills.
- Explicit representation of applicable, non-applicable, evaluated,
  non-evaluated, and degraded checks without changing the four-status taxonomy.
- Discovery of audit results in the current worktree and registered worktrees.
- Deterministic source selection when more than one result exists for an audit.
- Strict validation, coverage calculation, score status, and failure reporting.
- Provisional best-effort handling of safely recognizable legacy findings.
- Validator rules for the new contracts and policy.
- Unit, integration, cross-platform, rendering, and skill-behavior tests.
- README, architecture, contribution, convention, and decision documentation.

### Excluded from the first release

- Automatically running all audits.
- Automatically fixing audit findings.
- A graphical dashboard, hosted service, database, or network application
  programming interface.
- Uploading repository data or results.
- Organization-wide rankings.
- Historical trend storage; audits currently overwrite their latest outputs.
- Continuous-integration failure thresholds.
- User-configurable scoring weights or quality bands.
- Severity-based score caps.
- Compliance or security certification.
- Fuzzy or model-judged score calculation.
- Scoring third-party audit skills not listed in the versioned policy.

## Product and architecture decisions for the first release

The implementation agent should treat the following as the version-one default
policy. If the manager rejects one, update the architecture decision and policy
before writing the calculator rather than embedding a different assumption in
code.

| Decision | Version-one rule | Reason |
| --- | --- | --- |
| Check results | `present`, `partial`, `missing`, `violation` remain the only graded statuses | Preserves Architecture Decision 0001 and existing audit meaning |
| Non-applicability | Represent separately from status | Avoids changing the foundational four-status taxonomy |
| Standard-check weight | `1.0` | Simple baseline |
| Soft-check weight | `0.5` | Existing `softCheck` metadata already distinguishes guidance |
| Present points | `1.0` of the check weight | Full credit |
| Partial points | `0.5` of the check weight | Half credit for mixed adherence |
| Missing points | `0.0` | Structural prerequisite absent |
| Violation points | `0.0` | Concrete invariant broken |
| Audit aggregation | Equal weight per included audit category | Prevents audits with more checks from dominating |
| Missing audit | Exclude from quality score and reduce coverage | Absence of evidence is not proof of failure |
| Non-applicable check | Exclude from numerator and denominator | Prevents irrelevant technology checks from penalizing a repository |
| Non-evaluated check | Exclude from score, reduce check coverage, make result provisional | Prevents filters and execution failures from becoming silent passes or failures |
| Degraded evaluated check | Include its recorded status, mark result provisional | Preserves available evidence while disclosing reduced confidence |
| Critical score cap | None | Current catalogs do not define a stable severity field |
| Quality bands | Strong 90–100; Sound 75–89.99; Needs attention 60–74.99; High risk 0–59.99 | Clear initial communication bands |
| Score precision | Two decimal places, decimal half-up rounding | Stable display and band boundaries |
| Staleness | Current Git commit and catalog compatibility, not arbitrary wall-clock age | Static source at the same commit does not become wrong merely due to time |
| Default target | Current Git commit only | Never combine audit results from different source states |
| Legacy findings | Exact, unique mapping only; always provisional | Offers migration help without pretending old files satisfy the new contract |

## Architecture

### Component 1: audit check catalogs

The 14 `checks.json` files remain the machine-readable definitions of what is
graded. `SKILL.md` remains the human canonical source.

Extend each catalog root with a content version distinct from its structural
schema version:

```json
{
  "schemaVersion": "1.1.0",
  "catalogVersion": "1.0.0",
  "skillName": "architecture-audit",
  "humanCanonicalSource": "SKILL.md",
  "statusTaxonomy": {},
  "checks": []
}
```

Rules:

- `schemaVersion` changes when the JSON structure changes.
- `catalogVersion` changes when checks are added, removed, renamed, moved,
  reweighted, or materially redefined.
- A materially different check receives a new `checkId`; do not silently reuse
  an identifier for a different invariant.
- `softCheck` must be a Boolean when present and defaults to `false`.
- `allowedStatuses` must be a non-empty subset of the four-status taxonomy when
  present.
- Repository Quality Score reads only audit names explicitly listed in its
  scoring policy.

### Component 2: normalized audit results

Every audit continues to write its four existing files. The migration is
additive at the directory level and does not rename those files:

```text
.architect-audits/<audit-name>/
  findings.md
  findings.json
  snapshot.md
  metadata.json
  implementation-plan.md     only after user agreement, unchanged behavior
```

`findings.json` becomes the canonical scoring input. `metadata.json` repeats
the run identity and repository context needed to detect partially written or
mismatched files.

Canonical `findings.json` shape:

```json
{
  "schemaVersion": "2.0.0",
  "runIdentifier": "8d98724c-5bd5-49a3-82a7-d061ef8aab74",
  "skillName": "architecture-audit",
  "skillVersion": "1.0.0",
  "checkCatalogSchemaVersion": "1.1.0",
  "checkCatalogVersion": "1.0.0",
  "runStartedAt": "2026-07-13T10:00:00Z",
  "runFinishedAt": "2026-07-13T10:05:00Z",
  "target": {
    "repository": "owner/project",
    "gitCommit": "0123456789abcdef0123456789abcdef01234567",
    "sourceWorkingTreeClean": true
  },
  "execution": {
    "filtersApplied": false,
    "filterArguments": [],
    "thresholdOverrides": {},
    "policyOverrides": {},
    "enrichmentArguments": [],
    "graphAvailable": true
  },
  "snapshot": {},
  "summary": {},
  "checks": [
    {
      "checkId": "architecture-audit.no-circular-dependencies",
      "layer": "module-boundaries",
      "applicability": "applicable",
      "applicabilityReason": null,
      "evaluationState": "evaluated",
      "evaluationReason": null,
      "evidenceQuality": "complete",
      "classification": "observed",
      "status": "present",
      "evidence": ["No cycles were found in the dependency graph."],
      "gap": null,
      "remediation": null
    }
  ]
}
```

Audit-specific top-level snapshot and summary fields may remain, but the scorer
must ignore them. Audit-specific per-check fields may remain only as additional
fields; they cannot replace the shared keys.

### Result-state invariants

| Applicability | Evaluation state | Status | Score treatment |
| --- | --- | --- | --- |
| `applicable` | `evaluated` | One of four statuses | Included |
| `applicable` | `not-evaluated` | `null` | Excluded, coverage reduced, provisional |
| `not-applicable` | `not-evaluated` | `null` | Excluded without reducing applicable-check coverage |

Additional rules:

- A non-applicable result requires a non-empty `applicabilityReason`.
- An applicable, non-evaluated result requires a non-empty `evaluationReason`.
- An evaluated result requires `evidenceQuality` of `complete` or `degraded`.
- A degraded result requires an `evaluationReason` and makes its audit
  provisional, but its status still contributes to the score.
- A complete evaluated result must not carry a degradation reason.
- `classification` defaults to `observed`. Quality-gate misconfiguration is
  represented as `status: "partial"` and
  `classification: "misconfigured"`; `misconfigured` is not a fifth status.
- Filtered-out checks must be emitted as applicable and non-evaluated rather
  than omitted.
- Structurally skipped checks must be emitted as non-applicable rather than
  omitted.
- Every catalog check must appear exactly once in a canonical unfiltered run.
- `evidence` is always an array. It may be empty, but each entry must be a
  string. Do not include secret values or credentials.
- `gap` and `remediation` are strings or `null`.

### Audit-level applicability

Add an optional top-level audit applicability object:

```json
{
  "auditApplicability": {
    "status": "applicable",
    "reason": null
  }
}
```

An entire audit can be `not-applicable` only when the audit itself confirms the
target technology is absent. The audit must still produce a valid run containing
all catalog checks marked non-applicable. A missing audit file never means that
the audit was not applicable.

### Metadata contract

`metadata.json` must contain, at minimum:

```json
{
  "schemaVersion": "2.0.0",
  "runIdentifier": "8d98724c-5bd5-49a3-82a7-d061ef8aab74",
  "skillName": "architecture-audit",
  "skillVersion": "1.0.0",
  "checkCatalogSchemaVersion": "1.1.0",
  "checkCatalogVersion": "1.0.0",
  "runStartedAt": "2026-07-13T10:00:00Z",
  "runFinishedAt": "2026-07-13T10:05:00Z",
  "target": {
    "repository": "owner/project",
    "gitCommit": "0123456789abcdef0123456789abcdef01234567",
    "sourceWorkingTreeClean": true
  },
  "execution": {}
}
```

The shared fields in `findings.json` and `metadata.json` must match exactly.
Repository Quality Score rejects that candidate if they disagree.

### Source working-tree cleanliness

Audits and the calculator write `.architect-audits/`, so those generated files
must not make the source tree appear dirty. Determine
`sourceWorkingTreeClean` before writing the current run and ignore status entries
whose paths are entirely inside `.architect-audits/`. Do not ignore any other
uncommitted file.

### Component 3: versioned scoring policy

Add `repository-quality-score/score-policy.json`. The policy is the only place
where point values, audit roster, category weights, bands, and display precision
are defined.

Proposed shape:

```json
{
  "schemaVersion": "1.0.0",
  "policyVersion": "1.0.0",
  "scorePrecision": 2,
  "statusPoints": {
    "present": "1.0",
    "partial": "0.5",
    "missing": "0.0",
    "violation": "0.0"
  },
  "checkWeights": {
    "standard": "1.0",
    "soft": "0.5"
  },
  "audits": [
    { "name": "accessibility-audit", "weight": "1.0" },
    { "name": "agentic-audit", "weight": "1.0" },
    { "name": "architecture-audit", "weight": "1.0" },
    { "name": "bundle-build-audit", "weight": "1.0" },
    { "name": "dependency-audit", "weight": "1.0" },
    { "name": "documentation-audit", "weight": "1.0" },
    { "name": "error-handling-audit", "weight": "1.0" },
    { "name": "linting-audit", "weight": "1.0" },
    { "name": "performance-audit", "weight": "1.0" },
    { "name": "quality-gates-audit", "weight": "1.0" },
    { "name": "react-audit", "weight": "1.0" },
    { "name": "security-audit", "weight": "1.0" },
    { "name": "testing-audit", "weight": "1.0" },
    { "name": "typescript-audit", "weight": "1.0" }
  ],
  "bands": [
    { "name": "Strong", "minimum": "90.00" },
    { "name": "Sound", "minimum": "75.00" },
    { "name": "Needs attention", "minimum": "60.00" },
    { "name": "High risk", "minimum": "0.00" }
  ]
}
```

Store decimals as strings in policy JSON and parse with `decimal.Decimal`.
Never calculate with binary floating-point numbers.

Policy changes require a policy-version increase. Adding or removing an audit,
changing a category weight, changing a check weight, changing status points,
changing rounding, or changing a band is a policy change and requires an
architecture decision update.

### Component 4: deterministic calculator

Add
`repository-quality-score/scripts/calculate_repository_quality_score.py`.
Use Python 3 standard library only.

Suggested internal modules can remain functions in one file until more than one
real consumer exists:

1. Parse command arguments.
2. Resolve target repository, current commit, and registered worktrees.
3. Resolve the installed skill root from the calculator's own path.
4. Load and validate the scoring policy.
5. Load and validate each named audit catalog.
6. Discover candidate audit runs.
7. Parse canonical or legacy findings without trusting input shape.
8. Select one complete run per audit deterministically.
9. Validate repository state, catalog compatibility, run identity, and check
   completeness.
10. Calculate audit scores, coverage, overall score, bands, and deductions.
11. Render reports in memory.
12. Verify input fingerprints did not change during calculation.
13. Write outputs safely and deterministically.

Do not introduce classes solely for organization. Use small data classes only
where they protect a real invariant at a boundary, such as a validated check
result or score result.

## Input discovery

### Skill catalog resolution

The calculator lives at:

```text
<skills-root>/repository-quality-score/scripts/calculate_repository_quality_score.py
```

Derive `<skills-root>` from the script location and load each policy-listed
`<skills-root>/<audit-name>/checks.json`. This works in the source repository,
global installation, and local installation because installers copy complete
skill folders.

If a policy-listed catalog is absent, record missing catalog coverage and make
the overall result provisional or unavailable. Do not scan unrelated global
folders for a same-named file and do not include an unlisted third-party audit.

The normal installers need no behavior change because they already enumerate
top-level folders containing `SKILL.md` and copy the complete folder. Add tests
or verification proving that the new skill's script, policy, and references are
included in a copied skill folder.

### Audit result roots

By default, inspect:

1. `<target>/.architect-audits/` in the current worktree.
2. `.architect-audits/` in each registered worktree returned by
   `git worktree list --porcelain` for the same Git common directory.

Do not recursively scan arbitrary sibling directories. Registered Git
worktrees are the trust boundary for cross-worktree discovery.

Provide `/repository-quality-score --current-worktree-only` to disable
registered-worktree discovery. Keep target-path plumbing internal to the skill;
users normally invoke the command from the repository they want to score.

### Candidate selection

Never merge individual checks from separate runs. One audit score comes from
one atomic audit run.

For each policy audit, order valid candidates by:

1. Result targets the current commit.
2. Canonical schema over legacy schema.
3. Matching catalog schema and catalog version.
4. Unfiltered run over filtered run.
5. No threshold or policy overrides over customized policy.
6. Complete evidence over degraded evidence.
7. Latest valid `runFinishedAt`.
8. Lexicographically stable worktree label as the final tie-breaker.

Only current-commit candidates contribute by default. List older and
mixed-commit candidates as excluded. If there is no current-commit candidate,
the audit is missing for the current score; do not choose an older commit merely
because it has more completed audits.

Store only a worktree label and the path relative to its root in generated JSON.
Do not persist absolute local paths or remote URLs containing credentials.

### Concurrent audit writes

Canonical audits must assign one `runIdentifier` to all output files. They
should render files to temporary names and replace final names only after the
full run is ready. Repository Quality Score must:

- require matching run identifiers between findings and metadata;
- record a SHA-256 fingerprint of every input file it reads;
- verify fingerprints again immediately before writing reports;
- retry discovery once if an input changed; and
- abort with a clear unavailable result after the second change.

This prevents scoring half-written results while parallel audits are finishing.

## Exact scoring algorithm

### Check calculation

For each applicable, evaluated check:

```text
check weight = 0.5 when softCheck is true, otherwise 1.0
earned points = check weight * status point value
possible points = check weight
```

Example:

| Check | Soft | Status | Earned | Possible |
| --- | --- | --- | ---: | ---: |
| Check A | No | Present | 1.00 | 1.00 |
| Check B | No | Partial | 0.50 | 1.00 |
| Check C | No | Violation | 0.00 | 1.00 |
| Check D | Yes | Present | 0.50 | 0.50 |

```text
audit score = sum(earned points) / sum(possible points) * 100
            = 2.00 / 3.50 * 100
            = 57.14
```

Validate each result status against the catalog's `allowedStatuses` when it is
present. An invalid status makes that audit candidate unusable.

### Audit calculation

- Exclude non-applicable and non-evaluated checks from both numerator and
  denominator.
- Include degraded evaluated checks and add a provisional reason.
- If the audit has no applicable evaluated checks because the whole audit is
  explicitly non-applicable, count it as assessed but do not create a category
  score.
- If the audit has applicable checks but none were evaluated, its category
  score is unavailable.
- Quantize the category score to two decimal places using
  `ROUND_HALF_UP` after calculating with full decimal precision.

### Overall calculation

```text
overall score =
  sum(quantized audit score * audit category weight)
  / sum(included audit category weights)
```

All 14 version-one category weights are `1.0`. An explicitly non-applicable
audit is not included in the overall denominator. A missing audit is also not
included, but it reduces audit coverage and prevents an official score.

Quantize the overall score to two decimal places using `ROUND_HALF_UP`, then
assign the quality band from the quantized score. This prevents a displayed
`90.00` score from being labeled `Sound` because of an undisplayed fraction.

### Coverage

Report separate measures:

```text
catalog coverage = loaded policy catalogs / policy audit count
audit coverage = valid current-commit audit runs / policy audit count
applicable check coverage = evaluated applicable checks /
                            all known applicable checks in selected runs
```

Also report:

- policy audit count;
- scored audit count;
- explicitly non-applicable audit count;
- missing audit count;
- evaluated check count;
- degraded evaluated check count;
- non-evaluated applicable check count; and
- non-applicable check count.

Never collapse these into a single ambiguous coverage number.

### Score status

An overall result is `official` only when all of the following are true:

- The scoring policy and all policy-listed catalogs are valid.
- All 14 audits have one valid canonical result for the current commit, even if
  an audit records itself as non-applicable.
- Every selected result uses the supported findings schema.
- Findings and metadata run identifiers and shared fields match.
- Check catalog schema and catalog versions match installed catalogs.
- Every catalog check appears exactly once.
- Every applicable check was evaluated.
- No evaluated check used degraded evidence.
- No filters, threshold overrides, or scoring-policy overrides were used.
- Every audit recorded a clean source working tree.
- The current source working tree is clean after ignoring only
  `.architect-audits/` artifacts.
- No legacy adapter was used.

The result is `provisional` when at least one audit score can be calculated but
one or more official conditions are not met. Return every reason as a stable
machine-readable code plus a human message.

The result is `unavailable` when no valid category score can be calculated, the
policy is invalid, the target cannot be safely resolved, or inputs are changing
too quickly to form a consistent snapshot.

Recommended reason codes include:

```text
catalog-missing
catalog-version-mismatch
current-commit-result-missing
degraded-evidence
filtered-audit
legacy-input
metadata-mismatch
mixed-commit-input-excluded
non-evaluated-check
policy-override
source-worktree-dirty
threshold-override
unknown-check
```

### Highest-impact deductions

For each scored check:

```text
check deduction = possible points - earned points
category deduction percentage = check deduction / category possible points * 100
overall impact = category deduction percentage *
                 category weight / included category weight sum
```

Sort deductions by overall impact descending, then audit name, then `checkId`.
Do not rank non-applicable or non-evaluated checks as deductions. Report them in
coverage and exclusions instead.

## Legacy compatibility

The repository already contains and may have generated findings without
`schemaVersion: "2.0.0"`. Do not silently call them canonical.

The calculator may build a provisional legacy candidate only when all mappings
are exact and unique:

1. Accept a full catalog `checkId` from legacy `checkId` or `id` when it matches
   exactly.
2. Otherwise accept a legacy `check`, `gate`, or `id` only when it exactly
   matches one unique suffix from the current audit catalog.
3. Never use fuzzy title matching, semantic similarity, array position, or
   layer position.
4. Treat every catalog check missing from the legacy file as non-evaluated, not
   present and not failed.
5. Convert quality-gate `misconfigured` to `partial` with classification
   `misconfigured`.
6. Reject duplicate, ambiguous, unknown, or cross-audit identifiers.
7. Mark every resulting score provisional with `legacy-input` and tell the user
   which audits must be rerun.

Committed dogfood reports whose custom identifiers do not map exactly should be
listed as unscoreable and left unchanged until they are deliberately rerun. Do
not rewrite historical evidence merely to make the new score green.

## Generated output contract

Write only inside:

```text
.architect-audits/repository-quality-score/
  score.md
  score.json
  snapshot.md
  metadata.json
```

### score.json

Required top-level shape:

```json
{
  "schemaVersion": "1.0.0",
  "policyVersion": "1.0.0",
  "runIdentifier": "f89a0254-82ea-4410-8aaf-d5e23323770b",
  "runStartedAt": "2026-07-13T10:10:00Z",
  "runFinishedAt": "2026-07-13T10:10:01Z",
  "target": {
    "repository": "owner/project",
    "gitCommit": "0123456789abcdef0123456789abcdef01234567"
  },
  "status": "provisional",
  "statusReasons": [],
  "overallScore": 67.22,
  "qualityBand": "Needs attention",
  "coverage": {},
  "categories": [],
  "highestImpactDeductions": [],
  "missingAudits": [],
  "excludedCandidates": [],
  "inputCatalogs": []
}
```

Use JSON numbers containing values already rounded to two decimal places for
scores and impacts; insignificant trailing zeroes need not be preserved in the
JSON text. Keep counts as integers. Emit keys and arrays in documented stable
order and finish the file with one newline. Human-readable Markdown always
displays scores with two decimal places.

Do not duplicate raw evidence, source snippets, secrets, or absolute paths in
the score output. A deduction should contain audit name, `checkId`, title,
status, points, impact, and a relative pointer to the source audit report.

When status is unavailable, write `overallScore: null` and
`qualityBand: null`, plus actionable status reasons.

### score.md

Render, in this order:

1. Repository, commit, score, band, and result status.
2. Status warnings and why the score is not official.
3. Coverage table.
4. Per-audit score table.
5. Highest-impact deductions.
6. Missing, excluded, non-evaluated, and degraded inputs.
7. Scoring-method summary and policy version.
8. Commands or actions needed to obtain an official score.

### snapshot.md

Record input inventory only:

- current commit and clean-tree result;
- registered worktrees inspected;
- candidate runs discovered per audit;
- selected run per audit;
- policy and catalog versions;
- input fingerprints;
- filters, overrides, degradation, and exclusions.

### metadata.json

Record execution identity and reproducibility data:

- skill and output-schema versions;
- policy version and policy fingerprint;
- run identifier and timestamps;
- target commit;
- current-worktree-only mode;
- selected input fingerprints;
- loaded catalog fingerprints;
- final status and reason codes.

### Chat output

Keep chat output concise:

```text
Repository Quality Score: 67.22/100 — Needs attention
Status: Provisional
Audit coverage: 8/14
Highest-impact category: Testing (52.50)
Full report: .architect-audits/repository-quality-score/score.md
```

If unavailable, do not print a numeric score. Print the blocking inputs and the
report path.

## Safe and deterministic file handling

- Parse JSON as UTF-8 only.
- Reject duplicate object keys, trailing data, `NaN`, and infinity.
- Apply a documented maximum size, recommended 10 mebibytes per JSON input.
- Resolve every input path and refuse symlinks that escape the selected target
  worktree or installed skill root.
- Refuse to write if the output directory or a parent under
  `.architect-audits/` resolves outside the target through a symlink.
- Render every output fully in memory before touching existing reports.
- Write temporary files in the destination directory, flush and close them,
  then replace final files.
- Replace `score.json` last as the completion marker.
- Put the same run identifier in `score.json` and `metadata.json`; consumers
  reject mismatched output sets.
- Remove only temporary files created by the current failed run.
- Preserve the last complete report when validation or calculation fails before
  replacement. If possible, also print the new failure in chat.
- Use an exclusive lock file for concurrent score runs. If a lock exists, fail
  loudly with its start time; do not guess that it is stale and delete it.
- Never delete audit findings or another tool's files.
- Never access the network.

## Command behavior and exit codes

The slash command is the user interface. The bundled script is an internal,
testable implementation seam.

Recommended script behavior:

| Exit code | Meaning |
| ---: | --- |
| 0 | Official or provisional reports generated successfully |
| 1 | Unexpected internal or policy-programming error |
| 2 | User-correctable unavailable result, such as no scoreable inputs |

Do not use score thresholds as exit codes in version one. Continuous-integration
gating is out of scope.

## Edge-case behavior matrix

| Edge case | Required behavior |
| --- | --- |
| No `.architect-audits/` directory | Write or print an unavailable diagnostic; tell the user to run audits |
| No valid audit result | No numeric score; status unavailable |
| One valid audit | Calculate a provisional score; show 1/14 audit coverage |
| Missing audit | Exclude from quality denominator; reduce audit coverage |
| Entire audit explicitly non-applicable | Count as assessed, exclude from overall denominator |
| All checks in selected audit non-applicable | No category score; do not divide by zero |
| Some applicable checks non-evaluated | Score evaluated checks only; reduce check coverage; provisional |
| Degraded evaluated checks | Include status contribution; provisional with reason |
| Filtered audit | Prefer an unfiltered candidate; otherwise provisional |
| Threshold override | Prefer default candidate; otherwise provisional |
| Architecture pattern declaration | Record it; do not treat a declared project pattern as a score-policy override |
| Security critical-package override | Treat as policy override and provisional |
| Unknown check identifier | Candidate invalid; never ignore silently |
| Duplicate check identifier | Candidate invalid |
| Missing catalog check result | Canonical candidate invalid or non-evaluated only if explicitly represented |
| Extra result not in catalog | Candidate invalid |
| Invalid status | Candidate invalid |
| Status disallowed for that check | Candidate invalid |
| `misconfigured` quality gate | Canonical form is partial plus misconfigured classification |
| React below version 18 | Layer-four checks are explicitly non-applicable |
| React absent | React-specific checks or audit applicability are explicitly non-applicable, not missing |
| Library-only project | Deployment-only documentation checks are non-applicable |
| No Claude settings | Settings-only agentic checks are non-applicable |
| Optional runtime artifact absent | Follow the audit's defined partial or degraded result; never invent present |
| Graph missing with valid fallback | Evaluated with degraded evidence; provisional |
| Required parser or tool fails | Applicable and non-evaluated with reason; provisional |
| Findings JSON malformed | Exclude candidate and report parse location |
| Findings and metadata run identifiers differ | Exclude as partial write |
| Start time after finish time | Exclude candidate |
| Timestamp lacks timezone | Exclude canonical candidate |
| Current worktree has source changes | Provisional; ignore only audit artifacts |
| Audits target different commits | Use only current-commit candidates and list exclusions |
| Result exists only for older commit | Missing for current score; do not silently score history |
| Multiple runs for same audit | Apply deterministic candidate ordering; never merge checks |
| Parallel audit changes during scoring | Retry once, then unavailable |
| Audit output in registered worktree | Discover and consider it for the current commit |
| Unregistered sibling directory | Do not scan it |
| Git unavailable | Current worktree only; score can be provisional but never official |
| Installed catalog missing | Reduce catalog coverage; provisional or unavailable |
| Third-party `*-audit` folder installed | Ignore unless named by policy |
| Catalog version mismatch | Exclude canonical candidate; legacy path may be provisional only |
| Policy version changes | New scores identify new policy; do not imply direct comparability |
| Score exactly 90, 75, or 60 | Strong, Sound, or Needs attention respectively |
| Raw score rounds across a band boundary | Quantize first, then assign band |
| Empty evidence array | Allowed when status and explanation remain valid |
| Evidence contains a secret-like value | Do not copy it into score outputs; audits should redact it |
| Output directory is an escaping symlink | Refuse to write |
| Concurrent score run | Fail loudly on exclusive lock |
| Previous complete score exists | Replace only after new outputs are fully rendered and validated |
| Windows path separators and line endings | Produce the same logical score and stable JSON as Linux |

## Required repository changes

### New files

| File | Purpose |
| --- | --- |
| `docs/decisions/0002-repository-quality-score-and-audit-result-contract.md` | Approve aggregation category, normalized result states, policy versioning, and scoring model |
| `.agents/AUDIT_FINDINGS_CONTRACT.md` | Maintainer-facing canonical audit result contract |
| `repository-quality-score/SKILL.md` | Slash-command workflow and boundaries |
| `repository-quality-score/score-policy.json` | Versioned point, weight, audit roster, rounding, and band policy |
| `repository-quality-score/scripts/calculate_repository_quality_score.py` | Deterministic validator, calculator, and renderer |
| `repository-quality-score/references/score-output-contract.md` | Detailed generated-output field contract if keeping it in `SKILL.md` would exceed 500 lines |
| `repository-quality-score/evals/evals.json` | Realistic skill behavior evaluation prompts and assertions |
| `tests/test_repository_quality_score.py` | Calculator, discovery, validation, rendering, and integration tests |

Prefer programmatically constructed temporary fixtures in tests. Add small
checked fixtures only for byte-level malformed JSON, duplicate-key JSON, legacy
shapes, or path cases that are clearer as files.

### Existing files to modify

| Files | Change |
| --- | --- |
| All 14 `checks.json` files | Add and validate structural and catalog versions |
| All 14 audit `SKILL.md` files | Emit canonical identifiers, run identity, result states, clean-tree context, explicit skipped checks, and normalized findings examples |
| `quality-gates-audit/SKILL.md` | Replace canonical `misconfigured` status with partial plus classification while preserving human wording |
| `accessibility-audit/SKILL.md` | Add the missing explicit findings shape needed for downstream scoring |
| `scripts/validate-playbook.py` | Validate catalog versions, score policy, score skill bundle, and canonical findings examples or contract markers |
| `tests/test_validate_playbook.py` | Cover every new validator rule and valid backward-compatible repository shape |
| `README.md` | Add workflow step, skill index row, feature explanation, score interpretation, and findings contract update |
| `ARCHITECTURE.md` | Add read-only aggregation behavior, normalized contract, output contract, and correct the current audit count |
| `CLAUDE.md` | Add cross-session score-consumer contract and safety boundaries |
| `.agents/CONVENTIONS.md` | Add catalog versioning and normalized result rules |
| `CONTRIBUTING.md` | Explain how audit changes affect catalog versions, scoring compatibility, policy versions, and tests |
| `docs/decisions/README.md` | Index the new architecture decision |
| `system-self-improve/SKILL.md` | Change only if its audit-history scan needs explicit handling for canonical non-evaluated or non-applicable results |

### Files not expected to change

- Installer skills: their folder enumeration and complete-folder copy already
  includes the new skill and bundled resources. Verify rather than edit.
- `preflight/SKILL.md`: Repository Quality Score has no installable enrichment
  dependency.
- `ben-architect-review/SKILL.md`: it does not consume audit history for score
  calculation.
- Git hooks and continuous-integration workflow: existing gates already execute
  the full validator and unit-test discovery. Change only if a new standalone
  command is intentionally added to the gate matrix.
- Committed historical dogfood reports: preserve them until deliberately rerun.

## Implementation sequence

Do not update all audits and build the calculator in one unreviewable change.
Use the phases below and keep the repository valid at every commit.

### Phase 0: baseline, decisions, and impact analysis

1. Confirm the branch is `feature/repository-quality-score`.
2. Record `git status --short --branch`; preserve unrelated untracked files.
3. Run the current validator and tests in a Linux or continuous-integration-
   equivalent environment and record the baseline.
4. Keep the known Windows path and symlink failures separate from feature
   regressions. Decide whether to repair them in a prerequisite commit.
5. Read `AGENTS.md`, `CLAUDE.md`, `.agents/CONVENTIONS.md`,
   `.agents/QUALITY_GATES.md`, `.agents/PR_QUALITY.md`, and
   `.agents/DEFINITION_OF_DONE.md` again before implementation.
6. Re-index GitNexus if stale. Before modifying any function or method, run
   upstream impact analysis for that symbol and report high or critical risk
   before editing. At minimum inspect `validate_check_metadata`,
   `validate_skills`, `audit_directories`, and `main` before changing the
   validator.
7. Confirm the version-one policy table in this document with the manager.
8. Write and accept Architecture Decision 0002 before changing shared status,
   findings, or aggregation contracts.

Exit criterion: approved policy, clean baseline evidence, and known blast
radius.

### Phase 1: formalize contracts without scoring

1. Add the architecture decision and decision index entry.
2. Add `.agents/AUDIT_FINDINGS_CONTRACT.md`.
3. Update conventions with catalog and findings rules.
4. Define supported catalog schema `1.1.0`, findings schema `2.0.0`, and output
   schema `1.0.0`.
5. Add the `catalogVersion` field to all 14 catalogs without changing any check
   content or identifier.
6. Extend the validator for catalog field types and supported versions.
7. Add focused validator unit tests before migrating audit bodies.

Exit criterion: all catalogs validate and no audit behavior has changed yet.

Suggested commit:

```text
docs(architecture): define repository quality scoring contracts
```

### Phase 2: normalize all audit outputs

Migrate one audit at a time, running the validator after each audit:

1. Architecture, because it is the clearest reference example.
2. Quality gates, because it has the special `gates` and `misconfigured` shape.
3. React, error handling, agentic, and documentation, because they have explicit
   skipped or non-applicable layers.
4. Bundle and build, dependency, testing, TypeScript, linting, performance, and
   security, because they contain enrichment, tier, threshold, or confidence
   behavior.
5. Accessibility, adding its missing detailed findings contract.

For each audit:

- Preserve its four layers, Layer 0, read-only posture, flags, recommendations,
  and two-phase plan prompt.
- Load its own `checks.json` and use `checkId` exactly.
- Emit all catalog checks exactly once in an unfiltered run.
- Represent skips and filters explicitly.
- Normalize shared metadata and result fields.
- Preserve useful audit-specific snapshot and summary data.
- Update the README in the same commit group as required when skill bodies
  change.
- Verify status counts in the summary are derived from canonical checks rather
  than independently invented.

Add validator-assisted contract checks where practical. Do not rely on manual
review of 390 identifiers.

Exit criterion: every audit can produce a canonical, complete, scoreable result
without changing what its domain checks mean.

Suggested commit:

```text
feat(findings): normalize audit results for repository scoring
```

### Phase 3: implement policy and pure calculation

1. Add `score-policy.json` and its validator.
2. Implement strict JSON loading and typed validation.
3. Implement check-point calculation using `Decimal`.
4. Implement category calculation, equal-category aggregation, rounding, and
   band assignment.
5. Implement coverage and official/provisional/unavailable classification.
6. Implement stable deduction impact and ordering.
7. Keep calculation functions free of filesystem writes so unit tests can call
   them directly.

Exit criterion: pure in-memory tests cover the full scoring policy.

### Phase 4: implement discovery, compatibility, and output

1. Resolve target and skill roots safely.
2. Discover current and registered-worktree candidates.
3. Implement canonical candidate validation.
4. Implement deterministic candidate selection.
5. Implement narrow legacy adapters and diagnostics.
6. Implement input fingerprint recheck and single retry.
7. Implement safe report rendering and replacement.
8. Implement lock handling and exit codes.

Exit criterion: temporary-repository integration tests cover local results,
parallel-worktree results, mixed commits, legacy inputs, and concurrent change.

Suggested commit for phases 3 and 4:

```text
feat(score): add deterministic repository quality calculator
```

### Phase 5: implement the slash-command skill

Create `repository-quality-score/SKILL.md` with frontmatter in the required
order and at least these sections:

- Purpose and neighboring-skill boundaries.
- `## Usage`.
- `## What this skill does`.
- `## Implementation steps`.
- Input discovery and validation behavior.
- Output file shape.
- Idempotency rules.
- Failure modes and remediation.
- `## What this skill explicitly does NOT do`.

Keep the skill below roughly 500 lines. Move detailed field reference into the
bundled `references/` file and deterministic behavior into the script. The skill
must invoke the script rather than recompute points in model prose.

The skill must not use the audit four-layer skeleton or two-phase implementation
plan prompt because it is a read-only aggregation skill, not another source-code
audit. Document this classification in Architecture Decision 0002.

Exit criterion: installing the folder provides the skill, script, policy, and
references as a self-contained unit.

### Phase 6: documentation and downstream compatibility

1. Add Repository Quality Score after the audit step in the README workflow.
2. Add its skill index and "why this exists" entry.
3. Explain score, coverage, bands, and status without presenting the score as a
   certification.
4. Update architecture and Claude-specific cross-session contracts.
5. Update contribution instructions for catalog and policy versioning.
6. Inspect `system-self-improve --from-audit-history` against canonical results.
   Teach it to ignore non-applicable and non-evaluated checks if needed.
7. Verify local and global installer dry-run behavior sees the new skill and
   copies bundled files. Do not claim installed until a real copy is proven.

Exit criterion: a new user can install audits, run them in parallel worktrees,
run Repository Quality Score, and understand the report from documentation
alone.

### Phase 7: behavior evaluations and regression proof

Create realistic evaluation cases in `repository-quality-score/evals/evals.json`:

1. Complete current-commit canonical audit set produces an official score.
2. Parallel worktree set with missing, degraded, filtered, and mixed-commit
   candidates produces a correct provisional score and explanation.
3. No usable findings produces unavailable status without a fabricated number.
4. Legacy `check`, `gate`, and custom `id` inputs demonstrate exact mapping and
   safe rejection.

Assertions should verify output files, numeric results, coverage, status reason
codes, deterministic ordering, no mutation outside the score directory, and no
raw secret or absolute-path copying.

Follow the skill evaluation workflow only when it can use isolated directories
or worktrees. The repository contract forbids multiple agents mutating one
checkout. Deterministic Python unit and integration tests remain the merge gate;
model evaluations supplement them.

Exit criterion: calculator tests pass, skill behavior matches the documented
workflow, and results are human-reviewed.

### Phase 8: final verification and pull-request readiness

1. Run focused tests while iterating.
2. Run the full validator and unit-test suite after the final change.
3. Run the calculator twice against identical fixtures and byte-compare stable
   output fields, excluding timestamps and run identifiers where documented.
4. Test on Windows and Linux path semantics.
5. Test a clean checkout with local and global installation copies.
6. Run GitNexus `detect_changes` against `main` before committing and confirm
   only expected symbols and flows changed.
7. Review `git diff --check`, `git diff --stat`, and the complete diff.
8. Stage only explicit files. Never use `git add .` or `git add -A`.
9. Use Conventional Commit subjects.
10. Fill the pull-request template with behavioral proof. For this non-visual
    feature, state why screenshots are not applicable and include deterministic
    command results.

Exit criterion: repository definition of done is satisfied with no unexplained
regression.

## Test plan

### Pure scoring tests

- All present produces 100.00.
- All missing or violation produces 0.00.
- Mixed present, partial, missing, and violation produces exact expected score.
- Soft-check present and partial use half weight correctly.
- Non-applicable checks do not change score.
- Non-evaluated checks do not change score but reduce coverage.
- Degraded checks contribute and make status provisional.
- Equal category weighting prevents a 36-check audit from dominating a
  19-check audit.
- Explicitly non-applicable audit is excluded from overall denominator.
- Zero denominator returns unavailable rather than raising or returning zero.
- Scores at and around 90, 75, and 60 receive correct bands.
- Decimal half-up rounding behaves correctly at midpoint boundaries.
- Highest-impact deductions are mathematically correct and stably ordered.

### Contract-validation tests

- Valid canonical findings and metadata pass.
- Unsupported schema and catalog versions fail.
- Missing and extra checks fail canonical validation.
- Duplicate and cross-audit identifiers fail.
- Invalid status and disallowed status fail.
- Non-applicable with a status fails.
- Non-applicable without reason fails.
- Applicable non-evaluated without reason fails.
- Evaluated without status fails.
- Degraded without reason fails.
- Findings and metadata shared-field mismatch fails.
- Invalid timestamps and finish-before-start fail.
- String evidence fails canonical validation; arrays pass.
- Duplicate JSON keys, `NaN`, infinity, invalid UTF-8, trailing JSON, and files
  above size limit fail safely.

### Discovery and selection tests

- Current worktree result is discovered.
- Registered worktree result is discovered.
- Unregistered sibling result is ignored.
- Current-commit result wins over newer old-commit result.
- Canonical result wins over legacy result.
- Unfiltered default-policy result wins over customized result.
- Complete result wins over degraded result.
- Latest finish time and stable path tie-breaker work.
- Checks from different runs are never merged.
- Worktree labels do not leak absolute paths.
- Git-unavailable mode is provisional and current-worktree-only.

### Legacy tests

- Full exact `checkId` maps.
- Unique exact suffix from `check`, `gate`, or `id` maps.
- Quality-gate misconfigured normalizes to partial classification.
- Ambiguous suffix is rejected.
- Unknown custom dogfood identifier is rejected.
- Missing legacy checks reduce coverage and force provisional status.
- Fuzzy title match is never attempted.

### File-safety tests

- Output is confined to the score directory.
- Escaping input and output symlinks are rejected.
- Previous complete output survives validation failure.
- Input mutation triggers one retry and then an unavailable result.
- Concurrent lock prevents a second run.
- Temporary files are cleaned without deleting unrelated files.
- `score.json` is replaced last and shares run identity with metadata.
- Repeated logical input yields stable ordering and values.
- Windows backslashes, drive-letter case, spaces, Unicode paths, and line
  endings do not alter the logical result.

### Rendering tests

- Official report includes score, band, complete coverage, policy version, and
  categories.
- Provisional report puts warnings before score detail.
- Unavailable report contains no numeric score.
- Missing audits and non-evaluated checks are visible.
- Raw evidence and absolute paths are not copied.
- JSON keys and arrays are stable.
- Markdown contains no broken internal links or trailing whitespace.

Avoid broad snapshot tests. Assert behaviorally meaningful fields, values,
ordering, and report sections.

### Validator regression tests

- Existing valid non-audit skills still pass.
- Existing four-layer and lifecycle-stage audits still pass after migration.
- A score skill is not incorrectly forced into the audit four-layer contract.
- Missing `checks.json` on an implemented scoreable audit fails.
- Audit stubs remain intentionally exempt if the repository continues to allow
  audit stubs.
- Missing or invalid catalog version fails.
- Invalid scoring policy fails.
- Policy audit list remains synchronized with implemented audit catalogs.
- README index synchronization includes the new skill.
- Installer bootstrap validation still behaves truthfully on a clean checkout.

## Regression-control strategy

### Preserve existing audit behavior

The migration changes result serialization, not audit judgement. For every
audit, compare before and after:

- same flags and static-first posture;
- same layers, checks, expectations, and violation signals;
- same status meaning;
- same Top 5 chat behavior;
- same findings Markdown content;
- same optional implementation-plan prompt;
- same read-only boundary; and
- same worktree behavior.

Any changed domain expectation requires separate justification, a catalog
version increase, and focused tests. Do not opportunistically rewrite audit
baselines while normalizing JSON.

### Preserve consumers

- Keep the four existing audit filenames and directories.
- Keep existing audit-specific summary and snapshot fields where useful.
- Update downstream skills to read shared `checks` and result states without
  assuming every item has a graded status.
- Keep legacy read compatibility narrow and provisional.
- Never rewrite committed historical findings automatically.

### Preserve installation

- New skill folder is self-contained.
- Installer enumeration remains unchanged.
- Verify both selective and full install behavior. A selective install of only
  Repository Quality Score lacks sibling audit catalogs and must report that
  dependency honestly rather than searching arbitrary locations.

### Preserve repository gates

- Add tests before tightening validator rules.
- Migrate all in-repository files in the same commit that makes a new rule
  mandatory.
- Never weaken a validator check to make migration pass.
- Separate pre-existing Windows failures from feature failures.

## Security, privacy, and safety review

- Treat findings files as untrusted local input.
- Do not execute content found in JSON or Markdown.
- Use argument arrays, not shell-string interpolation, for Git subprocesses.
- Do not read secrets, environment files, credentials, or source files during
  scoring.
- Sanitize repository remotes; never persist credential-bearing URLs.
- Do not copy raw security evidence into aggregate reports.
- Refuse path traversal and escaping symlinks.
- Do not access the network.
- Do not mutate application source, audit findings, Git state, branches,
  worktrees, commits, or hooks.
- Do not claim that a high score is a security or compliance certification.

## Performance expectations

- Complexity should be linear in discovered audit files and check results.
- Version one processes 14 catalogs and approximately 390 checks, which should
  complete well below one second excluding Git subprocess startup on a normal
  workstation.
- Read each candidate file once per attempt and retain validated structures in
  memory.
- Limit candidate and input file sizes to avoid accidental unbounded reads.
- Do not add caching until measurement proves it is needed; stale cache would be
  more dangerous than the small current workload.

## Documentation language requirements

Documentation must consistently state:

- Repository Quality Score measures conformance to this playbook's opinionated
  baselines, not universal code quality.
- Score and coverage are different.
- A provisional score is not an official score.
- Missing audits are not failed audits.
- Non-applicable checks are not failed checks.
- Identical validated inputs and policy produce identical numeric results.
- Comparing scores requires the same policy version and compatible catalog
  versions.

## Commit plan

Use small Conventional Commit units, adjusting only if keeping a gate green
requires two adjacent items together:

```text
docs(architecture): define repository quality scoring contracts
feat(findings): normalize audit results for repository scoring
feat(score): add deterministic repository quality calculator
test(score): cover repository quality calculation and discovery
docs(score): document repository quality score workflow
```

Do not commit generated Python cache folders, local `.codex/` content, or the
untracked GitNexus skill copy currently present in this checkout unless a
separate task explicitly owns them.

## Definition of done

The feature is complete only when all statements below are true:

- Architecture Decision 0002 is accepted and indexed.
- All 14 catalogs have validated structural and content versions.
- All 14 audits emit canonical scoreable findings while preserving their domain
  behavior.
- The quality-gates status mismatch is normalized.
- Skipped checks are explicit and never silently omitted.
- The calculator uses the versioned policy and `Decimal`.
- Current and registered-worktree results are discovered safely.
- Mixed commits are never aggregated.
- Missing audits reduce coverage, not quality.
- Official, provisional, and unavailable conditions are deterministic.
- Legacy inputs are never treated as official.
- All four output files are generated safely and contain reproducibility data.
- No raw secrets or absolute local paths appear in aggregate output.
- Installer copies are proven to include bundled resources.
- Focused and full tests pass on the clean reference environment.
- Cross-platform tests cover Windows and Linux semantics.
- GitNexus change detection reports only expected impact before commit.
- Repository validator and unit tests pass after the final change, with any
  pre-existing unrelated platform issue separately evidenced and owned.
- Pull-request title, body, behavioral proof, and verification summary satisfy
  `.agents/PR_QUALITY.md` and `.agents/DEFINITION_OF_DONE.md`.

## Future implementation-agent runbook

An agent starting from this document should execute in this exact order:

1. Read all repository contracts named in Phase 0.
2. Inspect current branch, status, and any user changes; do not clean them.
3. Re-run baseline gates in the reference environment.
4. Confirm the version-one policy decisions with the user if they have not
   already been approved.
5. Use GitNexus impact analysis before editing validator symbols.
6. Implement Architecture Decision 0002 and shared contracts first.
7. Add validator tests, then catalog version fields.
8. Normalize audit outputs in the ordered migration groups.
9. Implement and test pure scoring before filesystem discovery.
10. Implement and test discovery, legacy compatibility, and safe writes.
11. Add the slash-command skill and bundled references.
12. Update documentation and inspect downstream consumers.
13. Run behavior evaluations in isolated directories or worktrees.
14. Run full gates, cross-platform checks, GitNexus change detection, and diff
    review.
15. Report exact evidence and remaining risks; do not call partial success done.

If a policy decision changes during implementation, stop and update the
architecture decision, scoring policy, expected calculations, and tests before
continuing. The JSON files and score must never encode an undocumented business
rule.
