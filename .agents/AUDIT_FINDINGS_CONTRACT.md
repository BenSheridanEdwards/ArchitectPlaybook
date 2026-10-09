# Audit findings contract

This is the shared machine-readable protocol between audit sessions and
downstream consumers such as Repository Quality Score. `SKILL.md` remains the
human canonical source for each audit baseline; `checks.json` is its
machine-readable inventory; `findings.json` records one execution of that
inventory.

## Catalog identity

Every implemented audit ships `checks.json` with:

- `schemaVersion` `"1.1.0"`, or `"1.2.0"` with per-check severity, method,
  rationale, and `lastVerified` (Architecture Decision Record 0004);
- a semantic `catalogVersion`;
- `skillName` equal to the folder name;
- `humanCanonicalSource: "SKILL.md"`;
- the four-status taxonomy; and
- a non-empty `checks` array with unique full `checkId` values.

Increase `catalogVersion` whenever a check is added, removed, renamed, moved,
reweighted, or materially redefined. Give a materially different invariant a
new `checkId` rather than silently reusing the old identity.

## Canonical run identity

`findings.json` and `metadata.json` use schema `2.0.0` and repeat these fields
with identical values:

```json
{
  "schemaVersion": "2.0.0",
  "runIdentifier": "one identifier shared by the complete run",
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
  }
}
```

Determine source-tree cleanliness before writing the current run. Ignore status
entries entirely inside `.architect-audits/` and `graphify-out/`, because those
are generated output (Architecture Decision Record 0005); do not ignore any
other uncommitted path. `graphAvailable` is reported but never makes a run
provisional.

Timestamps are timezone-aware RFC 3339 values and finish must not precede start.
The repository value must not contain credentials or an absolute local path.

## Check-result shape

Every catalog check appears exactly once in an unfiltered canonical run:

```json
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
  "evidence": ["No dependency cycles were found."],
  "gap": null,
  "remediation": null
}
```

The valid state combinations are:

| Applicability | Evaluation | Status | Meaning |
| --- | --- | --- | --- |
| `applicable` | `evaluated` | Four-status value | The check contributes to quality scoring |
| `applicable` | `not-evaluated` | `null` | The check reduces assessment coverage |
| `not-applicable` | `not-evaluated` | `null` | The check is excluded without penalty |

Rules:

- A non-applicable result has a non-empty `applicabilityReason`.
- An applicable, non-evaluated result has a non-empty `evaluationReason`.
- An evaluated result has evidence quality `complete` or `degraded`.
- A degraded evaluated result has an evaluation reason.
- A filtered-out check is applicable and non-evaluated, never omitted.
- A structurally skipped check is non-applicable, never omitted.
- `misconfigured` is a classification. Its canonical status is `partial`.
- `evidence` is an array of redacted strings.
- `gap` and `remediation` are strings or `null`.
- Audit-specific fields may extend the object but cannot replace shared fields.

## Audit-level applicability

An audit may record itself as not applicable only after it executes enough
detection to prove the target technology is absent:

```json
{
  "auditApplicability": {
    "status": "not-applicable",
    "reason": "React is not a dependency of this repository."
  }
}
```

It still emits all catalog checks as non-applicable. A missing audit directory
never means not applicable.

## Publishing through the audit protocol

Audits publish findings only through `audit-protocol/scripts/audit_run.py`
(Architecture Decision Record 0003). The script stages the run, verifies
evidence, validates the run with the score calculator, renders the Markdown
reports, and publishes the four files with `findings.json` written last as the
completion marker. It adds these optional fields,
which consumers may rely on when `protocolVersion` is present:

| Field | Where | Meaning |
| --- | --- | --- |
| `protocolVersion` | `findings.json`, `metadata.json` | Version of the run protocol that published the run. |
| `summary` | `findings.json` | Status, judgement, not-applicable, and not-evaluated counts. |
| `snapshot` | `findings.json` | The Layer 0 facts as an object. Informational; never graded. |
| `scope` | `findings.json` | The `--since` reference and its changed files. Always written; `since` is `null` and the list empty when the run is not diff-scoped. |
| `hypotheses` | `findings.json` | Unverified suspicions. Never statuses, never scored. |
| `treeFingerprint` | `metadata.json` | Hash of the commit and working-tree status at the start of the run. |
| `severity`, `method` | each check | Copied from the catalog when it defines them. |
| `evidenceTier` | each evaluated check | `direct`, `supported`, or `inferred`. A `violation` is never `inferred`. |
| `judgement` | each non-present check | `act-on`, `consider`, `noted`, or `dismissed`. |
| `judgementReason` | each `noted` or `dismissed` check | Why the finding is not acted on. |
| `decisionId` | each check covered by a decision | The entry in `.architect-audits/decisions.json`. |
| `recordedBy` | each check | `model` or `collector`. Pending checks have none, and a run with a pending check is never published. |

Every evaluated check carries at least one evidence entry the script verified
against the repository: a `path:line` citation, a file, a `search:` entry, or a
`files:` count. `command:` and `note:` entries may accompany them but never
stand alone, except in results from a deterministic collector. Citations,
files, searches, and file counts are re-verified before publication.

## Write safety

- Assign the run identifier before rendering outputs.
- Render complete files before replacing prior outputs.
- Use the same run identity in findings and metadata.
- Never print or persist unredacted secrets.
- Preserve `implementation-plan.md` unless the user agrees to regenerate it.
- Do not mutate files outside the audit's own findings directory.
