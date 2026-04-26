# Documentation audit — architect-playbook

> **Worked example.** This file is the output of running `/documentation-audit` against the architect-playbook itself (the [dogfood pass](../../README.md)). The original run, before the playbook had a `LICENSE`, `CONTRIBUTING.md`, `ARCHITECTURE.md`, or any ADR infrastructure, surfaced four `violation` findings (one per missing artefact). All four were closed before this output was captured. The remaining `missing` and `skipped` findings reflect the playbook's nature as a documentation-only library — no source code, no build pipeline, no deployable target — and represent honest "not applicable" cases rather than gaps to fix.
>
> The README links to this file as the canonical example of what a `findings.md` actually looks like.

## Diagnostic snapshot

See [snapshot.md](snapshot.md) for the full Layer 0 diagnostic block. Headline numbers:

- **Project shape:** library (documentation-only).
- **Total documentation files:** 26.
- **README:** present, 235 lines.
- **LICENSE:** MIT.
- **ADR count:** 1, most recent 2026-04-26.
- **Per-feature README coverage:** 100%.
- **Knowledge graph:** not built for the playbook itself (fallback heuristics applied).

## Summary

| Layer | present | partial | missing | violation | skipped |
| --- | --- | --- | --- | --- | --- |
| Project entry and onboarding | 5 | 0 | 2 | 0 | 0 |
| Architectural and decision documentation | 5 | 1 | 0 | 0 | 0 |
| Code-level documentation | 0 | 0 | 0 | 0 | 7 (no source detected) |
| Operational documentation and drift | 2 | 0 | 0 | 0 | 6 (library-only project) |

## Layer 1 — Project entry and onboarding

| Check | Status | Note |
| --- | --- | --- |
| README present | present | `README.md` at the repository root. |
| README is substantive | present | 235 lines, well above the default 30-line threshold. |
| README covers onboarding essentials | present | What/install/workflow/quick-start sections all identifiable. |
| README setup instructions match `package.json` scripts | missing | No `package.json` in the playbook — the check is N/A for documentation-only libraries. Recommend `--exclude=readme-script-drift-detection` on future runs, or evolving the check to skip silently on docs-only projects (a candidate for `/system-self-improve`). |
| LICENSE present | present | MIT, copyright Ben Walnut, added in the dogfood commit. |
| CONTRIBUTING.md present | present | `CONTRIBUTING.md` at the repository root with the canonical add-a-skill, improve-a-skill, conventions, and PR-checklist sections. Added in the dogfood commit. |
| Required tool versions documented | missing | No `.nvmrc`, `.tool-versions`, `package.json` engines, or `mise.toml` — N/A for documentation-only libraries. Same recommendation as above: `--exclude` or evolve. |
| `.env.example` (or equivalent) present | present | Trivially satisfied — the playbook reads no environment variables. |

## Layer 2 — Architectural and decision documentation

| Check | Status | Note |
| --- | --- | --- |
| Architecture overview present | present | `ARCHITECTURE.md` at the repository root. Added in the dogfood commit. |
| ADR directory present | present | `docs/decisions/` exists with an index `README.md` and ADR 0001. Added in the dogfood commit. |
| Recent ADR activity | present | Most recent ADR is dated 2026-04-26 — well within the 180-day default threshold. |
| ADRs follow recognisable template | present | ADR 0001 includes Status, Date, Context, Decision, Consequences, Alternatives — the canonical template documented in `docs/decisions/README.md`. |
| Per-feature documentation | present | 18/18 skill folders carry a `SKILL.md`. The audit treats each `SKILL.md` as a per-feature documentation block; coverage is 100%. |
| Diagrams for non-trivial architecture | partial | `ARCHITECTURE.md` has no diagrams (Mermaid block, image, linked diagram). The playbook has multiple top-level skill domains, so the soft check fires. Recommended remediation: add a Mermaid diagram to `ARCHITECTURE.md` showing the Setup → Audits → Meta flow and the findings-file contract. |

## Layer 3 — Code-level documentation

**Skipped: no-source-code-detected.** The playbook contains no `.ts`/`.tsx` source files. TSDoc/JSDoc coverage, comment quality, commented-out code detection, TODO ownership, and Storybook coverage all require source code to grade against. This layer is skipped silently per the documentation-audit's design (the same way Layer 4 of `/react-audit` skips on React below 18).

## Layer 4 — Operational documentation and drift

| Check | Status | Note |
| --- | --- | --- |
| Deployment documented | skipped | Project shape is library — no deployable target. |
| Rollback procedure documented | skipped | Project shape is library — no deployable target. |
| Monitoring and alerting documented | skipped | Project shape is library — no deployable target. |
| Feature-flag documentation | skipped | No feature-flag library detected. |
| README script-drift detection | skipped | No `package.json` — no scripts to drift against. |
| Documentation freshness | present | All documentation files modified within the 180-day default threshold (the playbook is actively under development). |
| Internal links resolve | present | Every Markdown link to a file or anchor in the repository resolves. |
| External links resolve | skipped | `--with-link-check` not passed; the single external link (graphify.net reference) was not HEAD-requested. |

## Phase 2 — implementation plan offer

After this audit was run, the user was asked: *"Generate an implementation plan for the documentation gaps? (yes/no)"*

The user answered **no** — the only outstanding finding (the `partial` on diagrams) is small enough to address inline rather than via a generated plan. The two `missing` findings on Layer 1 are honest "not applicable for a docs-only project" cases that don't warrant remediation.

If the user had answered yes, the implementation plan would have included:

1. A Mermaid diagram for `ARCHITECTURE.md` covering the Setup → Audits → Meta flow plus the findings-file contract.
2. A note recommending `/system-self-improve` consideration for the README-script-drift-detection and required-tool-versions checks: both fire on documentation-only libraries even though they're meaningless there. A refined check would skip silently when no `package.json` is present.

## Notes for `/system-self-improve`

This run surfaces two candidate weaknesses in `/documentation-audit` itself:

- **`readme-script-drift-detection`** fires `missing` for documentation-only libraries with no `package.json`. The check should skip silently in that case.
- **`required-tool-versions-documented`** fires `missing` for the same shape. Same recommendation.

Either could be the basis for a `/system-self-improve` run with `--target-skill=documentation-audit` and a gap report describing the false-positive pattern.
