# Architect Playbook

A self-contained, self-improving collection of Claude Code slash-command skills for auditing any codebase. Walk onto a project, install the skills, run multiple audits in parallel from separate chat sessions, fix what they find, review the fixes in a Git worktree, and let `/system-self-improve` patch the audits themselves whenever a review surfaces a gap.

## Core principles

- **Opinionated baselines.** Each audit grades against a specific opinionated baseline, not whatever happens to be in the codebase.
- **Static-first with optional enrichment.** Every audit is read-only by default. Runtime or network data is opt-in via explicit `--with-*` flags.
- **Two-phase flow.** Every audit reports findings, then asks whether to generate an implementation plan. Mutation is never automatic.
- **Boundary discipline.** Cross-cutting concerns are explicitly called out.
- **Self-improving.** When a review surfaces a gap, `/system-self-improve` patches the originating audit.

## The workflow

1. **Get the playbook.**
   ```bash
   git clone <this-repository> ~/architect-playbook
   ```

2. **Install the skills.**
   ```
   /install-architect-playbook-globally     # once, machine-wide
   /install-architect-playbook-locally      # per project, pinned in version control
   ```

3. **Prepare the project.**
   ```
   /pre-audit-setup
   ```

4. **Run audits.**
   ```
   /security-audit              # concise Top 5 recommendations + full report saved to disk
   /worktree security           # run in an isolated worktree (recommended for parallel audits)
   ```
   Open multiple chats and use `/worktree` in each for true parallel execution.

5. **Fix the findings** in the same chat that produced them.

6. **Re-run the audit** to review the fix (best done in a fresh chat or worktree).

7. **Evolve the playbook** if a gap is found:
   ```
   /system-self-improve
   ```

## Flags

Every audit supports the same minimal set:

```
/<skill-name>                    # default: concise Top 5 + full report saved + ask about plan
/<skill-name> --learn            # mid-level engineer teaching mode
/<skill-name> --teach            # alias for --learn
```

Some audits expose additional enrichment or threshold flags (`--with-*`, `--threshold-*`). See the individual `SKILL.md` for details.

**Pro tip**: Use `/worktree <skill-name>` to run audits in isolated Git worktrees.

## The findings-file contract

Audits, fixes, and reviews each run in different chat sessions, so they cannot share in-memory state. The protocol between them is a deterministic on-disk shape:

```
.architect-audits/
  <audit-name>/
    findings.md       human-readable report you can read at a glance
    findings.json     machine-readable list of issues for downstream skills
    snapshot.md       the Layer 0 diagnostic snapshot, on its own
    metadata.json     skill version, run timestamp, graphify revision hash
```

- **Chat output** is human-first and concise: a short header, the Top 5 Highest-Leverage Recommendations (title, why it matters, consequences, smallest fix, lettered sub-actions), and a one-line pointer to the full report on disk. The full layered findings are never printed in the chat unless the user explicitly asks.
- **Fixing** is a free-form chat conversation in the same chat that produced the audit.
- **Reviewing** is re-running the originating audit in a fresh chat against the worktree containing the fix.
- **`/system-self-improve`** reads a review's gap report and proposes an edit to the originating audit's `SKILL.md`.

A worked example of this contract — `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` produced by running `/documentation-audit` against the playbook itself — is committed at [`.architect-audits/documentation-audit/`](.architect-audits/documentation-audit/).

## How audits grade issues

The full detailed report saved to `.architect-audits/<audit-name>/findings.md` grades every check with one of four statuses:

- **present** — the expected invariant is fully satisfied.
- **partial** — mostly correct, but with noticeable gaps or inconsistencies.
- **missing** — a required foundation (tooling, pattern, or configuration) is absent.
- **violation** — concrete code actively breaks the invariant.

The concise Top 5 recommendations you see by default in chat focus on **missing** and **violation** items (the highest-impact issues), plus the most important **partial** findings. The full report on disk shows the complete status for every check.

`/system-self-improve` uses a different outcome model (`advanced | blocked | skipped`).

## The full skill list

### Setup utilities

| Trigger | Purpose |
| --- | --- |
| [`/install-architect-playbook-locally`](install-architect-playbook-locally/SKILL.md) | Copy every playbook skill into the current project's `.claude/skills/`. |
| [`/install-architect-playbook-globally`](install-architect-playbook-globally/SKILL.md) | Copy every playbook skill into `~/.claude/skills/`. |
| [`/pre-audit-setup`](pre-audit-setup/SKILL.md) | Verify graphify, build the knowledge graph, merge the PreToolUse hook. |
| [`/worktree`](worktree/SKILL.md) | Create a Git worktree and run the named audit against it. |
| [`/preflight`](preflight/SKILL.md) | Detect optional enrichment tooling for `--with-*` flags. |

### Audits

| Trigger | Purpose |
| --- | --- |
| [`/quality-gates-audit`](quality-gates-audit/SKILL.md) | Pre-commit, pre-push, and CI/CD lifecycle gates. |
| [`/security-audit`](security-audit/SKILL.md) | Frontend security: auth, XSS, headers, secrets. |
| [`/accessibility-audit`](accessibility-audit/SKILL.md) | WCAG 2.2 AA across tooling, components, and shell. |
| [`/dependency-audit`](dependency-audit/SKILL.md) | Dependency tree across security, health, and hygiene. |
| [`/performance-audit`](performance-audit/SKILL.md) | Runtime cost and Core Web Vitals. |
| [`/architecture-audit`](architecture-audit/SKILL.md) | Module boundaries, coupling, and conventions. |
| [`/testing-audit`](testing-audit/SKILL.md) | Testing Library philosophy and best practices. |
| [`/react-audit`](react-audit/SKILL.md) | Idiomatic React patterns and React 19 idioms. |
| [`/linting-audit`](linting-audit/SKILL.md) | Linting configuration quality. |
| [`/typescript-audit`](typescript-audit/SKILL.md) | Type system quality and IO validation. |
| [`/bundle-build-audit`](bundle-build-audit/SKILL.md) | Build pipeline and bundle output. |
| [`/error-handling-audit`](error-handling-audit/SKILL.md) | Error hygiene and observability. |
| [`/documentation-audit`](documentation-audit/SKILL.md) | Documentation quality across multiple lenses. |
| [`/agentic-audit`](agentic-audit/SKILL.md) | Agentic instruction files (`CLAUDE.md`, `AGENTS.md`, Cursor rules) and `.claude/settings.json` hygiene. |

### Meta

| Trigger | Purpose |
| --- | --- |
| [`/system-self-improve`](system-self-improve/SKILL.md) | Read gap reports and evolve the playbook itself. |

## Conventions

- **Conventional Commits** for every commit.
- **No abbreviations** in skill names, triggers, descriptions, headings, identifiers, or prose.
- **Read-only by default.** Mutating runs require an explicit `--apply` flag and print a dry-run summary first.
- **Frontmatter shape** is fixed: `name`, `description`, `trigger`. The `trigger` value equals `/<folder-name>`.
- **No hard-coded absolute paths** in skill bodies. Derive from the current working directory or `$HOME`.

The full set of project-wide rules lives in [CLAUDE.md](CLAUDE.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide. The short version:

- **Add a new skill:** create a folder named after the slash command (full words, no abbreviations), write a `SKILL.md` following the canonical body structure, add the table row and the per-skill summary in the same commit. Audits also get the worktree Pro Tip block.
- **Improve an existing skill:** prefer the self-improvement loop. Run the audit, fix the findings, review the fix in a worktree, then run `/system-self-improve` so the patch to the audit body is grounded in a real gap rather than speculation.

The architectural intent behind the playbook's conventions lives in [ARCHITECTURE.md](ARCHITECTURE.md). Foundational decisions are recorded as ADRs in [docs/decisions/](docs/decisions/).

## License

[MIT](LICENSE).

## Related

- [graphify](https://graphify.net/graphify-claude-code-integration.html) — the knowledge-graph skill that `/pre-audit-setup` assumes is installed at `~/.claude/skills/graphify`.
