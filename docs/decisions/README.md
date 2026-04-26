# Architecture Decision Records

This directory holds Architecture Decision Records (ADRs) for the architect-playbook. ADRs document the *why* behind foundational choices that shape every audit and every contribution. They exist so a future contributor (or future you) doesn't accidentally undo a decision whose rationale is no longer obvious from the code.

## When to write an ADR

Write an ADR when proposing a change that affects the playbook's foundational conventions, including:

- The four-layer plus Layer 0 audit shape.
- The four-status taxonomy (`present | partial | missing | violation`).
- The two-phase flow (report → ask → optional plan).
- The Testing Philosophy (behaviour over implementation, snapshots are a smell, semantic tokens over utility classes).
- The findings-file contract (`findings.md`, `findings.json`, `snapshot.md`, `metadata.json`).
- The static-first-with-opt-in-enrichment posture.
- The boundary-discipline pattern (cross-cutting concerns surface in every audit they touch).
- The scope of `/system-self-improve` (read-only by default, hard prohibitions, never commits).

Also write an ADR when introducing a deliberately scope-limiting decision such as "frontend-only in v1" or "TypeScript-only in v1". These limits are not gaps; they are choices that future contributors might otherwise mistakenly try to remove.

You do not need an ADR for routine changes — adding a check to an existing audit, tweaking a threshold default, fixing a detection-logic edge case, refining prose. Those are well within the canonical body structure and don't need historical justification beyond the commit message.

## Format

Each ADR is a Markdown file named `NNNN-short-slug.md`, where `NNNN` is the next sequential four-digit number. Use these section headings:

```markdown
# NNNN — Title

**Status:** Accepted | Superseded by NNNN | Deprecated
**Date:** YYYY-MM-DD

## Context

What problem motivated this decision? What constraints are in play?

## Decision

What was decided. Be specific.

## Consequences

What does this make easier? What does it make harder? What ripples follow?

## Alternatives considered

What other options were on the table? Why were they rejected?
```

## Index

| ID | Title | Status |
| --- | --- | --- |
| [0001](0001-four-layer-baseline-with-layer-zero-snapshot.md) | Four-layer baseline with Layer 0 diagnostic snapshot | Accepted |
