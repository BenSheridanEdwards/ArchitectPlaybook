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
   - `rationale`: one sentence on why the check matters, used in reports and
     chat;
   - `lastVerified`: the date the guidance was last checked against current
     tools and frameworks;
   - optionally, `relatedChecks`: check identifiers in other audits that cover
     an overlapping concern.

   A concern has one owning check. Other audits reference it through
   `relatedChecks` instead of scoring it again.

   The check's row in `SKILL.md` shows its severity and method as cells, and the
   validator enforces the match. Schema `1.1.0` stays supported while audits
   migrate.
2. **Score policy schema `1.1.0`, policy version `2.0.0`.** The policy adds
   `severityWeights`:

   | Severity | Weight |
   | --- | ---: |
   | `critical` | 8 |
   | `high` | 4 |
   | `medium` | 2 |
   | `low` | 1 |

   A check in a `1.2.0` catalog weighs its severity weight. A check in a `1.1.0`
   catalog keeps the standard and soft weights. Status points, normalization per
   audit, equal audit weights, coverage, and bands are unchanged.
3. **Severity rubric.**
   - **Critical:**
     - an exploitable security weakness;
     - loss or corruption of user data;
     - a gate failure that lets broken code ship unnoticed.
   - **High:**
     - a likely user-visible failure;
     - a significant security or reliability risk;
     - a structural problem that compounds with every change.
   - **Medium:** a maintainability or reliability cost that grows over time.
   - **Low:** hygiene, consistency, and conventions.

   Rate the consequence of the check failing in a typical production TypeScript
   application, not how often the check fails.

## Consequences

- Scores move only for audits whose catalogs migrate to schema `1.2.0`. With
  every catalog at `1.1.0`, policy `2.0.0` produces the same numbers as policy
  `1.0.0`.
- Results remain comparable only for the same policy version and the same
  catalog versions. That was already the rule.
- A run that mixes migrated and unmigrated audits is still normalized per audit,
  so each audit's scale stays 0 to 100.
- Changing a check's severity is a catalog change: increase `catalogVersion`.
  Changing the severity weights is a policy change: increase `policyVersion` and
  update this record.
- `method: tool` checks become candidates for deterministic collectors
  (`scripts/collect.py`, Architecture Decision Record 0003). Model attention
  goes to `method: model` checks.
- `lastVerified` makes stale guidance visible. A check older than six months is
  due for a review against current tools.
