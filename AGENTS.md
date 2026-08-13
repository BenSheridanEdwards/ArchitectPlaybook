# Agent contract

This repository is Architect Playbook: a library of Claude Code slash-command
skills that audit any codebase against opinionated baselines. It is mostly
Markdown — one `SKILL.md` per skill folder — plus small standard-library Python
programs for repository validation and deterministic Repository Quality Score
calculation, with unit tests. There is no application to run and no build step;
the deliverable is the skills themselves and the contracts that keep them
installable, comparable, and safe to run in parallel sessions.

This file is the tool-agnostic contract for every agent (and human) working
here. `CLAUDE.md` adds the Claude-specific layer on top of it.

## How to run the gates

Run these from the repository root. They use only Python 3's standard library.

```bash
python3 scripts/validate-playbook.py              # frontmatter, sections, checks.json, README index
python3 -m unittest discover -s tests -p 'test_*.py'   # validator unit tests
python3 scripts/install-git-hooks.py              # once per clone: local commit/push gates
```

The validator enforces the contracts that must hold at every commit: frontmatter
shape and key order, `trigger` equal to `/<folder-name>`, the required audit
sections, `checks.json` schema and layer alignment, Markdown link integrity, and
README skill-index sync. Run it constantly — it is the fastest signal that a
change is sound. The unit tests cover the validator itself; keep them green.
`install-git-hooks.py` wires `pre-commit`, `pre-push`, and `commit-msg` hooks
that run the validator, the tests, and the Conventional Commit check locally.

## Operating rules

### Think before coding
State assumptions before writing. Surface tradeoffs. Ask before guessing on
skill contracts, `checks.json` shape, or irreversible changes. Push back when a
simpler approach exists.

### Simplicity first
Write the minimum that solves the task. No speculative sections, no abstractions
for single-use content. If a skill body or a checks file looks over-engineered,
simplify it.

### Surgical changes
Touch only what the task requires. Do not reformat, rename, or restructure
adjacent skills unless the change requires it. Match existing style.

### Separate research from implementation
When a task needs broad reading across many skills, do the research first and
produce a compact report, then implement from that report rather than a sprawling
context window.

### Truth rules
- Do not claim a gate exists unless the hook, script, or workflow exists and runs
  on a clean checkout.
- Do not describe an aspiration as current fact; label targets as targets.
- Never report "installed" from filesystem presence alone — prove the command,
  hook, or workflow runs.
- Never bypass gates. `--no-verify` is forbidden, and weakening the validator or
  tests to turn red green is a named violation.

### One agent, one directory
Parallel audits and edits must use separate Git worktrees or directories. No two
agents mutate the same checkout at once. Worktrees are a flag on each audit
(`--worktree`), not a standalone command.

### Fail loudly
If a command, test, hook, or assumption fails, report the exact failure. Do not
relabel partial success as done. Passing tests only count when they cover the
changed behaviour.

### Unique skill descriptions
Each skill describes exactly one job. If two skills could be selected for the
same reason, rename or split them before relying on them.

## What not to touch without explicit approval

- Secrets, credentials, tokens, keys, and local environment files.
- Public publishing, releases, and package publication.
- Destructive Git operations: branch deletion, force-push, history rewrite.
- Generated or managed blocks (for example the GitNexus block under Tooling
  below) unless the task explicitly requires regenerating them.

## Definition of done

A change is done only when the changed behaviour is verified by deterministic
evidence: the validator passes, the unit tests pass, and the pull request meets
`.agents/DEFINITION_OF_DONE.md`. The full pull-request contract, including the
required template and behavioural-proof rules, lives in `.agents/PR_QUALITY.md`.

## Where things live

| Topic | Read this |
| --- | --- |
| Every skill, grouped and linked | [README.md](README.md) |
| Why the playbook is shaped this way | [ARCHITECTURE.md](ARCHITECTURE.md) |
| Foundational decisions (four-layer baseline, Layer 0) | [docs/decisions/](docs/decisions/) |
| Claude-specific rules for this repo | [CLAUDE.md](CLAUDE.md) |
| Skill-authoring conventions the validator enforces | [.agents/CONVENTIONS.md](.agents/CONVENTIONS.md) |
| The gate matrix: what runs, when | [.agents/QUALITY_GATES.md](.agents/QUALITY_GATES.md) |
| Pull-request contract and proof law | [.agents/PR_QUALITY.md](.agents/PR_QUALITY.md) |
| Definition of done | [.agents/DEFINITION_OF_DONE.md](.agents/DEFINITION_OF_DONE.md) |
| How to contribute a skill | [CONTRIBUTING.md](CONTRIBUTING.md) |

## Tooling

The block below is generated and managed by `gitnexus analyze` between its
start and end HTML comment markers, and is rewritten on every re-index. Do not
hand-edit inside it.

<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **ArchitectPlaybook** (1167 symbols, 1596 relationships, 31 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> Index stale? Run `node .gitnexus/run.cjs analyze` from the project root — it auto-selects an available runner. No `.gitnexus/run.cjs` yet? `npx gitnexus analyze` (npm 11 crash → `npm i -g gitnexus`; #1939).

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows. For regression review, compare against the default branch: `detect_changes({scope: "compare", base_ref: "main"})`.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `query({search_query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `context({name: "symbolName"})`.
- For security review, `explain({target: "fileOrSymbol"})` lists taint findings (source→sink flows; needs `analyze --pdg`).

## Never Do

- NEVER edit a function, class, or method without first running `impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `rename` which understands the call graph.
- NEVER commit changes without running `detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/ArchitectPlaybook/context` | Codebase overview, check index freshness |
| `gitnexus://repo/ArchitectPlaybook/clusters` | All functional areas |
| `gitnexus://repo/ArchitectPlaybook/processes` | All execution flows |
| `gitnexus://repo/ArchitectPlaybook/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->
