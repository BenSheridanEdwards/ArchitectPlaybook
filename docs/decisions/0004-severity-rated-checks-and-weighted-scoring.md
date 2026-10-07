# 0004 — Severity-rated checks and weighted scoring

## Status

Accepted.

## Context

Architecture Decision Record 0002 gave every standard check weight 1 and every
soft check weight 0.5. That made the Repository Quality Score measure how many
checks pass, not how much risk remains:

- `/dependency-audit` weighed "`engines` field declared" exactly as much as "no
  high or critical vulnerabilities".
- `/testing-audit` weighed "queries via `screen`", a lint rule, at 1.0. It
  weighed "tests describe user behaviour", its own first-class principle, at
  0.5.
- Softness describes how a check is graded (mixed adherence reported as
  `partial`). It is not a measure of importance, but it was the only weight the
  policy could vary.

The catalogs also had no way to say which checks a script can decide and which
need judgement. Audits therefore asked the model to re-implement linters. There
was also no record of when a check's guidance was last verified against a
fast-moving ecosystem.

## Decision

1. **Catalog schema `1.2.0`.** Every check in a `1.2.0` catalog carries:
   - `severity`: `critical`, `high`, `medium`, or `low`, rated with the rubric
     below;
   - `method`: `tool` when a deterministic script or tool output can decide the
     status, `model` when it needs judgement;
   - `rationale`: one sentence on why the check matters, for reports and chat;
   - `lastVerified`: the date the guidance was last checked against current
     tools and frameworks, never later than the current date in UTC;
   - optionally, `relatedChecks`: identifiers of checks in other audits that
     own a concern this check overlaps (see "One owner per concern" below).

   The check's row in `SKILL.md` shows its severity and method as cells. When
   the table has Severity and Method columns, the values sit in those columns.
   The validator enforces both. Schema `1.1.0` stays supported while audits
   migrate.

   `relatedChecks` is not the older `relatedAudits` field. Many `1.1.0` checks
   carry `relatedAudits`, a list of audit names whose domain overlaps the
   check. It is an informal hint: nothing validates or reads it, and it does
   not say which check owns the shared concern. `relatedChecks` names the
   owning checks themselves. In every catalog schema, the validator requires a
   list of distinct identifiers of existing checks in other audits. When a
   catalog migrates, turn each `relatedAudits` entry into a `relatedChecks`
   entry where an owning check exists.
2. **Score policy schema `1.1.0`, policy version `2.0.0`.** The policy adds
   `severityWeights`:

   | Severity | Weight |
   | --- | ---: |
   | `critical` | 8 |
   | `high` | 4 |
   | `medium` | 2 |
   | `low` | 1 |

   The weight doubles at each level, so one check outweighs any single check
   rated below it.

   Under a policy with `severityWeights`, a check in a `1.2.0` catalog weighs
   its severity weight. `softCheck` no longer changes that weight; it only
   marks that mixed adherence is graded `partial`. A check in a `1.1.0` catalog
   keeps the standard and soft weights. A policy without `severityWeights`
   (schema `1.0.0`) uses the standard and soft weights for every catalog,
   including `1.2.0` catalogs.

   Coverage follows the weights. A check weighed by severity counts toward
   applicable-check coverage with its severity weight, so skipping a critical
   check cannot leave a run looking well covered. Every other check counts
   once, as before. `score.json` reports the weighted figure and the plain
   check count side by side. An official score still requires every
   applicable check to be evaluated, which is 100 percent on both measures.

   Status points, normalization per audit, equal audit weights, and bands are
   unchanged.
3. **Severity rubric.** Severity rates the consequence of the problem a check
   detects, in a typical production TypeScript application. It does not rate
   how often the check fails, or how strong the gate that would catch the
   problem is. A check that finds a gate or control missing is rated by the
   problem that control exists to stop: a missing staged-file formatter lets
   inconsistent formatting in, so it is low, not critical.

   - **Critical:** the problem exposes users or the business directly: an
     exploitable security weakness, loss or corruption of user data, or a
     known-vulnerable dependency in production.
   - **High:** the problem produces a failure that users or operators see in
     production, such as a crash, a blank screen, lost work, or an outage, or
     it is a security weakness that is exploitable only together with another
     flaw.
   - **Medium:** the problem makes defects more likely, or change slower and
     riskier, but produces no failure by itself.
   - **Low:** the problem costs consistency, readability, or hygiene only.

   High and medium split on one question: does the problem itself produce a
   failure in production, or only make one more likely? If two levels still
   fit, choose the lower one.

   When their catalogs migrate, these anchor checks take the severities shown,
   and other checks are rated against them:

   | Check | Severity | Why |
   | --- | --- | --- |
   | `security-audit.no-secrets-in-source` | `critical` | An exposed secret is directly exploitable. |
   | `dependency-audit.no-high-or-critical-vulnerabilities` | `critical` | Known-vulnerable code ships to users. |
   | `error-handling-audit.root-error-boundary-present` | `high` | One rendering error blanks the whole application. |
   | `typescript-audit.storage-reads-validated` | `high` | Stale stored data crashes the screen that reads it. |
   | `architecture-audit.no-circular-dependencies` | `medium` | Coupling makes change riskier; it does not fail by itself. |
   | `typescript-audit.strict-true` | `medium` | The compiler misses more defects; nothing fails by itself. |
   | `dependency-audit.engines-field-declared` | `low` | Hygiene: the supported runtime version is undeclared. |
   | `quality-gates-audit.staged-file-formatter` | `low` | Formatting drifts; nothing else is at risk. |

   Three rules settle the remaining cases:
   - **Missing control or present defect.** A check that finds a control
     missing and a check that finds the defect present are rated by the same
     consequence. "Continuous integration runs a dependency vulnerability
     scan" and "no high or critical vulnerabilities" both keep a
     known-vulnerable dependency out of production, so both are critical.
   - **One owner per concern.** A concern, meaning one thing a check
     verifies, is rated by one check, in the audit whose domain owns it.
     `quality-gates-audit.dependency-vulnerability-scan` and
     `dependency-audit.continuous-integration-vulnerability-scanning-enabled`
     verify the same control, so only one of them may rate it. The audit that
     does not own the control retires its copy when its catalog migrates. A
     narrower check that it keeps names the owner in `relatedChecks` and is
     rated only for what the owner does not cover. References point from the
     overlapping check to the owner. The calculator scores every check in a
     catalog, so `relatedChecks` records ownership; retiring the copy is what
     prevents double rating.
   - **Soft checks.** Rate a soft check by the consequence of its full
     failure, like any other check. Softness changes how a check is graded,
     not its severity.

## Consequences

- Scores move only for audits whose catalogs migrate to schema `1.2.0`. With
  every catalog at `1.1.0`, policy `2.0.0` produces the same numbers as policy
  `1.0.0`, coverage included.
- Severity weights act only inside one audit. Each audit is still normalized
  and the audits are averaged equally, so a low check in a small audit can
  cost more of the overall score than a critical check in a large one.
  Category scores, and deductions that show each check's severity, show where
  the risk sits.
- Results remain comparable only for the same policy version and the same
  catalog versions. That was already the rule.
- A run that mixes migrated and unmigrated audits is still normalized per audit,
  so each audit's scale stays 0 to 100.
- `score.json` moves to schema `1.1.0`. It adds `scorePolicy.severityWeights`,
  weighted coverage beside the plain check count, and each deduction's
  severity. Every schema `1.0.0` field keeps its value for the inputs that
  schema could score.
- Changing a check's severity is a catalog change: increase `catalogVersion`.
  Changing the severity weights is a policy change: increase `policyVersion` and
  update this record.
- `method: tool` checks become candidates for deterministic collectors
  (`scripts/collect.py`, Architecture Decision Record 0003). Model attention
  goes to `method: model` checks.
- `lastVerified` records when a check's guidance was last checked. The
  validator rejects impossible dates, and dates after the current UTC date,
  which would hide a check from review. Nothing reads the date yet. The target
  is a `/system-self-improve` staleness review that lists checks verified more
  than six months ago; until it exists, stale guidance is visible only by
  reading the catalogs.
