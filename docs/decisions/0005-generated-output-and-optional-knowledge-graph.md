# 0005 — Generated output and an optional knowledge graph

## Status

Accepted.

## Context

`/pre-audit-setup` builds the graphify knowledge graph into `graphify-out/` and merged a hook into `.claude/settings.json`. Both changed the working tree:

- The score calculator treated any change outside `.architect-audits/` as a dirty tree, so every run after setup reported `source-worktree-dirty-at-audit-time`.
- That held until the user committed a generated graph and a settings change, which is not a reasonable ask of a team.

The calculator also marked any run without `graphify-out/graph.json` provisional (`graph-unavailable`). That rule existed because `/architecture-audit` required the graph. After its rewrite (#27), no audit requires the graph: every audit reads it, when present, only to choose where to read first. The rule therefore blocked official scores for a dependency nothing had. It also blocked every `--worktree` run, because a new worktree has no graph.

Graphify's own installer (`graphify claude install`) adds the same Glob and Grep hint hook and a `CLAUDE.md` note, so the playbook's hook duplicated it.

## Decision

1. **Generated output is not source.** Source cleanliness and the protocol's tree fingerprint ignore changes inside `.architect-audits/` and `graphify-out/`. The calculator's `GENERATED_OUTPUT_DIRECTORIES` lists them, and the protocol reuses that list.
2. **The knowledge graph is optional.** A run without the graph is no longer provisional. `execution.graphAvailable` stays in findings, and remains a tie-breaker when the calculator chooses between otherwise equal runs.
3. **`/pre-audit-setup` changes no settings or tracked files.** It verifies graphify, builds the graph, and creates `.architect-audits/`. Installing graphify's hook and `CLAUDE.md` note is left to the user, through graphify's installer.

## Consequences

- A repository can reach an official score straight after `/pre-audit-setup`, with or without the graph, and from a `--worktree` checkout.
- Scores computed before this change with `graph-unavailable` as their only provisional reason would now be official. Results are recomputed from findings on each run, so no stored score changes until it is recalculated.
- A repository that commits `graphify-out/` still has it ignored for cleanliness. Committing it changes nothing for scoring.
