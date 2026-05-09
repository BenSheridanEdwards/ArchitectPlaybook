# 0001 — Four-layer baseline with Layer 0 snapshot

## Status

Accepted.

## Context

Architect Playbook audits must be predictable across separate chat sessions and worktrees. A contributor should be able to read one audit body, understand the report shape, and apply the same mental model to every other audit.

The playbook also needs a place for diagnostic facts that explain the run without turning those facts into pass or fail checks. Examples include detected libraries, framework variants, counts, thresholds, and unavailable optional tools.

## Decision

Every audit uses the same baseline shape:

- **Layer 0 — Diagnostic snapshot:** informational only, always written, and never assigned a status.
- **Layer 1:** first domain concern for the audit.
- **Layer 2:** second domain concern for the audit.
- **Layer 3:** third domain concern for the audit.
- **Layer 4:** fourth domain concern for the audit.

Layers 1 through 4 grade checks with the shared status taxonomy: `present`, `partial`, `missing`, or `violation`.

Layer 0 is written to `snapshot.md` and included at the top of `findings.md`. The four graded layers remain in `findings.md` and `findings.json` so follow-up chats can read the same evidence and continue the work.

## Consequences

- Audits stay comparable even when their domains differ.
- Report readers can separate environmental facts from graded findings.
- New audits must fit four graded layers instead of inventing a custom structure.
- If a domain has fewer meaningful concerns, it should group related checks coherently rather than adding empty layers.
- Changing this baseline requires a later architecture decision because it affects every audit and every downstream review flow.
