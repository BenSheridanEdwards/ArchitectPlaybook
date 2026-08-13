# 0002 — Repository Quality Score and audit-result contract

## Status

Accepted.

## Context

Architect Playbook audits write findings for downstream chat sessions, but the
existing machine-readable examples use several incompatible result shapes.
Most audits use a `checks` array with a short `check` field, quality gates uses
a `gates` array and a `misconfigured` status, committed dogfood reports use
custom `id` values, and structurally skipped checks are commonly omitted. A
scoring consumer cannot distinguish a passed check from an omitted check without
a normalized contract.

The playbook also recommends running audits in parallel Git worktrees. Their
reports may therefore live in separate registered worktrees even when they
describe the same source commit.

The four-status taxonomy and four-layer audit shape are foundational decisions.
A repository score must preserve them instead of introducing a fifth graded
status for non-applicability.

## Decision

Add `/repository-quality-score` as a read-only aggregation skill. It consumes
the versioned `checks.json`, `findings.json`, and `metadata.json` contracts from
the existing audits and writes its own reports under
`.architect-audits/repository-quality-score/`.

The aggregation skill is not a source-code audit. It does not use the four-layer
shape, does not rerun checks, and does not offer an implementation-plan phase.
Its deterministic calculation is implemented by a bundled Python 3
standard-library script.

Every check catalog carries two versions:

- `schemaVersion` versions the catalog structure.
- `catalogVersion` versions the baseline content and scoring meaning.

Every canonical audit run uses findings schema `2.0.0`. It identifies checks by
their full catalog `checkId`, records one result for every catalog check, and
separates these concepts:

- applicability: `applicable` or `not-applicable`;
- evaluation state: `evaluated` or `not-evaluated`;
- evidence quality for evaluated checks: `complete` or `degraded`; and
- the existing graded status: `present`, `partial`, `missing`, or `violation`.

Non-applicable and non-evaluated checks have no graded status. Quality-gate
misconfiguration is represented as `partial` with classification
`misconfigured`; it is not a fifth status.

Version-one scoring is deliberately simple and versioned in
`repository-quality-score/score-policy.json`:

- standard checks have weight 1;
- soft checks have weight 0.5;
- present earns 100 percent of the check weight;
- partial earns 50 percent;
- missing and violation earn zero;
- non-applicable and non-evaluated checks are excluded;
- each included audit category has equal overall weight; and
- missing audits reduce coverage rather than becoming zero scores.

The score is official only when every policy audit has a complete compatible
run for the current clean source commit with no filters, threshold overrides,
legacy inputs, non-evaluated checks, or degraded evidence. A calculable but
incomplete result is provisional. A result with no valid category score is
unavailable.

The calculator may discover results in the current worktree and registered Git
worktrees, but it never combines checks from different runs or source commits.
Legacy inputs are mapped only by exact, unique identifiers and can never produce
an official score.

## Consequences

- Audit outputs become reliable cross-session data rather than loosely similar
  examples.
- All audits must emit explicit results for skipped and filtered checks.
- Existing historical findings remain valid evidence, but may be provisional or
  unscoreable until the audit is rerun.
- Scores are reproducible only with the same scoring-policy and catalog
  versions.
- A single score never replaces category scores, coverage, or deduction detail.
- Optional enrichment failures and graph fallbacks remain visible through
  degraded evidence and prevent an official result.
- A future change to points, weights, bands, category membership, or aggregation
  requires a scoring-policy version increase and an architecture-decision
  update.
