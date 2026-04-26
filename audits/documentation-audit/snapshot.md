# Diagnostic snapshot — documentation-audit

**Generated:** 2026-04-26 by running `/documentation-audit` against the architect-playbook itself (the dogfood pass).

| Field | Value |
| --- | --- |
| Project shape | library (documentation-only — no source code, no build pipeline, no deployable target) |
| Documentation file count | 26 (`README.md`, `CLAUDE.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `LICENSE`, 18 × `SKILL.md`, ADR index, ADR 0001, audit findings) |
| Documentation-to-code line ratio | n/a (no `.ts`/`.tsx` source) |
| README presence and size | present, 235 lines |
| LICENSE present | yes — MIT |
| ADR count | 1 |
| Most-recent ADR date | 2026-04-26 (`0001-four-layer-baseline-with-layer-zero-snapshot.md`) |
| TODO/FIXME count | 0 (no source code; no TODOs in documentation) |
| Storybook detected | no |
| Documentation-site framework | none |
| Per-feature README coverage | 100% (18 / 18 skill folders carry a `SKILL.md`) |
| External-link total count | 1 (`graphify.net` reference link) |
| External-link unreachable count | not measured (`--with-link-check` not passed) |
| Operational layer status | skipped silently — library-only project shape, no deployable target detected |
| Knowledge-graph used | no (`graphify-out/graph.json` not present in the playbook clone — fallback heuristics applied) |
