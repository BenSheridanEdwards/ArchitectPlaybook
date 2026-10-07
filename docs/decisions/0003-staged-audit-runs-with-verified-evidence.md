# 0003 — Staged audit runs with verified evidence

## Status

Accepted.

## Context

Architecture Decision Record 0002 defined a precise findings contract: schema `2.0.0`, run identity shared by `findings.json` and `metadata.json`, and every catalog check emitted exactly once with applicability, evaluation state, evidence quality, and status. Audits were expected to produce that contract by following prose instructions in each `SKILL.md`.

They did not. As of October 2026:

- **Nothing anywhere followed the contract.** No schema 2.0.0 `findings.json` existed under the owner's projects, and the score calculator had never scored real output.
- **Earlier runs drifted badly:**
  - catalogs collapsed to a quarter of their checks;
  - identifiers were invented;
  - a non-canonical `misconfigured` status appeared;
  - one audit wrote an entirely different document shape.
- **The skill text contradicted the contract.** Seven audits told the model to grade checks it could not evaluate as `partial`, which earns half credit.
- **Nothing checked that cited files, lines, and quotes exist.** A hallucinated finding looked exactly like a real one.

Longer and stricter prose did not change this. The playbook needed a mechanism.

## Decision

Audits publish only through `audit-protocol/scripts/audit_run.py`, a standard-library script shared by every audit. The protocol is described in `audit-protocol/references/run-protocol.md`.

1. **Staged runs.** `begin` records the run identity, the exact commit, working-tree cleanliness, a tree fingerprint, and the execution options. It stages every catalog check as pending.
2. **One decision per check.** Each check is recorded as evaluated, not applicable, or not evaluated with a reason. `finish` refuses to publish while any check is pending.
3. **Verified evidence.** Every evaluated check needs at least one verifiable evidence entry:
   - a citation;
   - a file;
   - a command;
   - a search.

   Cited files and lines must exist, and text quoted in backticks must appear at the cited lines. Search counts are re-run. Secret-looking evidence is rejected. A `violation` needs `direct` or `supported` evidence. Unverified suspicions become hypotheses in an appendix, never statuses.
4. **Judgement as data.** Every non-present finding carries a judgement: `act-on`, `consider`, `noted`, or `dismissed`. The chat's top recommendations come only from `act-on`. Dismissals carry reasons, and the report lists them.
5. **Decision memory.** `.architect-audits/decisions.json` records findings the user has accepted, scoped by check and path. Covered findings keep their honest status, so the score still reflects them, but their judgement becomes `noted`.
6. **One validator.** `finish` validates the run with the calculator's own `parse_canonical_candidate`, so a published run is exactly what `/repository-quality-score` accepts.
7. **Rendered reports.** `findings.md` and `snapshot.md` are rendered from the JSON. The four files are replaced one at a time, with `findings.json` last as the completion marker. A failed write restores the previous files. If the process is killed part-way, `findings.json` and `metadata.json` disagree on run identity, and the calculator rejects the set instead of mixing runs.
8. **Collectors.** An audit may ship `scripts/collect.py`, a deterministic, read-only collector. `begin` runs it and records its results through the same validation.

Findings keep schema `2.0.0`. The protocol adds optional fields that the contract already allows: `protocolVersion`, `evidenceTier`, `judgement`, `judgementReason`, `decisionId`, `recordedBy`, `summary`, `scope`, and `hypotheses`. Per-check `severity` and `method` are copied from the catalog when the catalog defines them.

## Consequences

- Every audit depends on two sibling skill folders: `audit-protocol` for the script, and `repository-quality-score` for the calculator module. Installers must copy both whenever they install an audit.
- Audit skill bodies shrink. Run identity, the findings contract, report shapes, and the chat format are defined once instead of fourteen times.
- A run that changes the repository mid-way, cites something that does not exist, or skips a check cannot be published. The model must fix the evidence or record the honest state.
- Not-evaluated checks reduce coverage instead of earning credit. Scores fall where they were previously inflated.
- `skillVersion` in findings is the audit's catalog version. The catalog is the versioned definition of what the audit measures.
- The protocol's own behaviour is covered by `tests/test_audit_run.py`. Changing it requires keeping those tests green.
