# 0001 — Four-layer baseline with Layer 0 diagnostic snapshot

**Status:** Accepted
**Date:** 2026-04-26

## Context

Every audit in the playbook needs to do two related but distinct things:

1. **Describe the current state** of the audited concern in enough detail that a human can orient quickly. What linter is in use? What framework? How many `any` annotations exist? What's the current bundle size? Without this context, individual check results are hard to interpret.
2. **Grade against an opinionated baseline** — pass/fail per check, with enough structure that the user can see at a glance which checks passed and which need attention.

A flat list of checks fails (1) — the human has no orienting context. A descriptive snapshot alone fails (2) — there's no opinionated stance, no "you should fix these things". A nested tree of arbitrary depth makes (2) hard to scan and varies wildly between audits.

We also needed a structure that would scale — an audit might cover one tight concern (e.g., suppressions hygiene) or a sprawling one (e.g., security across auth, XSS, headers, secrets). The same skeleton has to fit both without forcing artificial subdivisions on the small ones or losing detail on the large ones.

## Decision

Every audit body uses the same skeleton: **Layer 0 (a diagnostic snapshot) plus four numbered layers (graded check tables).**

- **Layer 0** is informational only. It captures the current state of the audited concern: detected libraries, framework variants, threshold values used, counts, and any other context the reader needs to interpret the layered results. Layer 0 has no pass/fail status — it neither passes nor fails. It is always written, even when filters restrict the rest of the run.
- **Layers 1 through 4** are graded check tables. Each layer is organised by audit-internal cohesion (attack class for security, runtime cost dimension for performance, lifecycle stage for quality gates, etc.). Each check resolves to one of `present | partial | missing | violation`. A layer can be silently skipped when its structural prerequisite is absent (e.g., Layer 3 of `/error-handling-audit` skips when React isn't detected; Layer 4 of `/react-audit` skips on React below 18) — when that happens, the layer's summary records `skipped: "<reason>"` and produces no per-check entries.

The same shape applies to every audit. The shape is enforced by `/system-self-improve` — a proposed edit that breaks Layer 0's "informational only" property, removes the four-layer convention, or changes the numbering is rejected as a prohibited mutation.

## Consequences

### Easier

- **Cross-audit fluency.** A reader who has internalised one audit can read any other audit at half the speed because they know exactly where to look: framework detection in Layer 0, hygiene checks in the layer that holds them, etc.
- **Predictable outputs.** Every `findings.md` has the snapshot at the top followed by check results grouped by layer. Tooling that reads `findings.json` knows to expect `summary` keyed by camel-cased layer names plus a flat `checks` array. The findings-file contract becomes machine-readable without a per-audit schema.
- **Sane scope for new audits.** When proposing a new audit, the four-layer constraint forces a useful question: what are the four real concerns this audit covers? Audits that can't answer that question are usually trying to be two audits.
- **Safe self-improvement.** `/system-self-improve` can mechanically verify that a proposed edit preserves the convention. New checks go at the end of their layer (additive, not insertive); refinements stay within their layer; threshold changes don't affect structure at all.

### Harder

- **Audits with a natural three-layer or five-layer fit are forced into four.** This has bitten us mildly — `/quality-gates-audit` is naturally three layers (pre-commit, pre-push, CI/CD); `/security-audit` could plausibly be five (auth, XSS, transport, secrets, third-party). We accept the procrustean fit because the fluency win is larger than the cost.
- **The convention is foundational and changing it would ripple through every audit.** That's the trade-off with foundational conventions; ADRs exist to make those decisions visible so they're not accidentally undone.
- **Layer 0 adds work to every audit.** The diagnostic snapshot is real engineering — detecting frameworks, libraries, thresholds applied, and so on. The alternative (no snapshot, just check results) would be cheaper to author but harder to consume.

## Alternatives considered

- **Flat list of checks per audit, no layers.** Rejected because it loses scannability and makes overlap with other audits invisible. The layer structure is what surfaces "this is the auth-and-sessions section" vs "this is the secrets section" at a glance.
- **Variable layer count per audit.** Rejected because it breaks the cross-audit fluency property. A reader landing in a new audit would have to figure out the structure before they could read the content.
- **Nested layers (sub-sections under each layer).** Rejected as overkill for the audit shape. When a layer has too many checks to fit in one table comfortably, the right move is usually to split the audit (or to recognise that two of those checks belong in a neighbour audit), not to add a level of nesting.
- **Snapshot as part of Layer 1 rather than its own Layer 0.** Rejected because the snapshot has fundamentally different semantics — it's informational only, it always runs, and it never blocks. Putting it inside a graded layer would force a fake `present` status on it, which would be misleading.
- **Drop the snapshot entirely.** Considered. Audits would still produce useful pass/fail results without it. Rejected because the human-readability of `findings.md` drops noticeably without the orienting context — the snapshot is what lets a reader understand what they're looking at before they start scanning the layer tables.
