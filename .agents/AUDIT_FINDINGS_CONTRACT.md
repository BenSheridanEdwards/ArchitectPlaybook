# Audit findings contract

This is the shared machine-readable protocol between audit sessions and
downstream consumers such as Repository Quality Score. `SKILL.md` remains the
human canonical source for each audit baseline; `checks.json` is its
machine-readable inventory; `findings.json` records one execution of that
inventory.

## Catalog identity

Every implemented audit ships `checks.json` with:

- `schemaVersion: "1.1.0"`;
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
entries entirely inside `.architect-audits/`, because those are generated audit
artifacts; do not ignore any other uncommitted path.

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

## Write safety

- Assign the run identifier before rendering outputs.
- Render complete files before replacing prior outputs.
- Use the same run identity in findings and metadata.
- Never print or persist unredacted secrets.
- Preserve `implementation-plan.md` unless the user agrees to regenerate it.
- Do not mutate files outside the audit's own findings directory.
