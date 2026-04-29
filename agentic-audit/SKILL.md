---
name: agentic-audit
description: Audit a project's agentic instruction files (CLAUDE.md, AGENTS.md, .cursor/rules, .cursorrules, .github/copilot-instructions.md, Windsurf, Aider) and Claude Code settings (.claude/settings.json, .claude/settings.local.json) against an opinionated baseline covering project context coverage, operational guidance, settings hygiene, and multi-agent drift. Static-only with optional implementation plan.
trigger: /agentic-audit
---

# /agentic-audit

Audit a project's **agentic instruction files** and **Claude Code settings** against an opinionated baseline organised in four layers — **project context coverage**, **operational guidance and conventions**, **Claude Code settings hygiene**, **multi-agent consistency and drift** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

The default mental model is a TypeScript and React application with a `CLAUDE.md` at the repository root and a `.claude/` directory, but the audit recognises the wider ecosystem of agentic instruction files (`AGENTS.md`, `.cursor/rules/*.mdc`, `.cursorrules`, `.github/copilot-instructions.md`, `.windsurfrules`, `.aider.conf.yml` and `CONVENTIONS.md`) and grades whatever is present.

## How this differs from neighbouring audits

| Concern | Owner |
| --- | --- |
| Whether the README explains the project for humans | `/documentation-audit` (Layer 1 onboarding) |
| Whether the architecture overview document exists for humans | `/documentation-audit` (Layer 2 architectural docs) |
| Whether `CLAUDE.md` (or equivalent) gives an agent the context it needs | `/agentic-audit` (Layer 1 project context coverage) |
| Whether agentic instruction files describe build/test/lint commands | `/agentic-audit` (Layer 2 operational guidance) |
| Whether `.claude/settings.json` has safe permissions and no secrets | `/agentic-audit` (Layer 3 settings hygiene) |
| Whether pre-commit and CI hooks are configured | `/quality-gates-audit` |
| Whether `CLAUDE.md` and `AGENTS.md` and `.cursorrules` agree with each other and with `package.json` | `/agentic-audit` (Layer 4 multi-agent consistency and drift) |
| Whether public APIs carry TSDoc or JSDoc | `/documentation-audit` (Layer 3 code-level documentation) |
| Whether secrets appear in committed source files generally | `/security-audit` |

`/documentation-audit` covers documentation written for **humans**. `/agentic-audit` covers documentation and configuration written for **AI coding agents**. The two overlap in places (a project-purpose paragraph helps both audiences), and both surface the gap so a single fix passes both.

## Static-only design

This skill is read-only. It only reads files in the repository: agentic instruction files, `.claude/settings.json`, `.claude/settings.local.json`, `.gitignore`, `package.json`, and a sample of source files for cross-referencing. It never writes to the codebase, never runs project commands, never makes network requests, and never authenticates anywhere.

## Usage

```
/agentic-audit                                            # default: concise Top 5 + full report saved + ask about plan
/agentic-audit --learn                                    # mid-level engineer teaching mode (detailed explanations + file/line examples)
/agentic-audit --teach                                    # alias for --learn
/agentic-audit --threshold-instruction-min-lines=40       # override default 25
/agentic-audit --threshold-instruction-max-lines=600      # override default 400
/agentic-audit --threshold-permission-broadness=10        # override default 5 (count of broad-pattern permissions tolerated)
/agentic-audit --threshold-context-coverage=70            # override default 60 (percent of essential context items present)
```

**💡 Pro tip**: Use `/worktree agentic` to run this in an isolated worktree.

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the project shows mixed adherence to a soft check.
- **missing** — a structural prerequisite is absent (no agentic instruction file at all, for example).
- **violation** — the audit identified a concrete problem that breaks the invariant (a secret in `settings.json`, a broad permission pattern, a stale command reference).

Layer 0 is informational only and has no status. Layer 3's settings checks are silently skipped when neither `.claude/settings.json` nor `.claude/settings.local.json` exists. Layer 4's drift checks always run.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Detected agentic instruction files, with one row per file: path, line count, last-modified date (from Git), and recognised tool family (`claude`, `cursor`, `cursor-rules-directory`, `cursorrules-legacy`, `copilot`, `windsurf`, `aider`, `agents-md`, `conventions-md`, `other`).
- Total agentic-instruction line count across all detected files.
- Agentic instruction file count.
- `.claude/` directory presence.
- `.claude/settings.json` presence, shape (top-level keys), and total permission entry count.
- `.claude/settings.local.json` presence and `.gitignore` status.
- Detected hook count (per event: `PreToolUse`, `PostToolUse`, `Stop`, `Notification`, etc.).
- Detected MCP-server count (when `mcpServers` block is present in any settings file).
- Detected status-line, environment-variable, and model-override entries.
- Project shape (informs phrasing in the implementation plan): deployable application, library, monorepo, or hybrid.

### Layer 1 — Project context coverage

The agentic instruction file's job is to give an AI agent the same context a new engineer would get from a one-page brief. The audit checks that the **primary** instruction file (the most-substantive of `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, or the equivalent) covers the essential items.

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Primary instruction file present | At least one of `CLAUDE.md`, `AGENTS.md`, `.cursorrules`, or `.cursor/rules/*.mdc` exists at the repository root (or equivalent canonical path). | No agentic instruction file at all. |
| Primary instruction file is substantive | The primary file is at least the threshold lines (default 25; tunable via `--threshold-instruction-min-lines`) and contains identifiable structure beyond a single bullet list. | Primary file below the threshold. |
| Primary instruction file is not bloated | The primary file is at most the threshold lines (default 400; tunable via `--threshold-instruction-max-lines`). Agent context windows are precious; instruction files compete with the user's actual problem for tokens. | Primary file above the threshold. |
| Project purpose documented | The instruction file states what the project is and who it is for in identifiable prose (not just a project name). | No purpose statement. |
| Tech stack documented | The instruction file names the primary framework, language, and runtime (e.g. "TypeScript, React 19, Next.js 15, Node 22"). Soft check — reported as `partial` when only some are mentioned. | No tech-stack signals at all. |
| Architectural mental model documented | The instruction file gives a brief architectural orientation: directory layout, the dominant module pattern (feature folders, layered, hexagonal), or links to `ARCHITECTURE.md`. Soft check. | No architectural orientation. |
| Domain glossary present where appropriate | When the project uses domain-specific terminology (detected by frequency of capitalised non-English-dictionary tokens in source filenames, or by the presence of a `glossary.md`), the instruction file defines or links to a glossary. Soft check. Skipped when no domain terminology is detected. | Heavy domain terminology with no glossary. |
| Links to canonical human-facing docs | The instruction file points the agent at the README, `ARCHITECTURE.md`, ADR directory, and `CONTRIBUTING.md` when those exist. Soft check — reported as `partial` when some are linked and others are not. | None of the canonical docs are linked. |

### Layer 2 — Operational guidance and conventions

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Build, test, and run commands documented | The instruction file lists the canonical install, dev-server, test, and build commands (or links to a section that does). The actual commands are checked against `package.json` scripts in Layer 4. Soft check — reported as `partial` when some commands are documented and others are not. | None of the four basic commands are documented. |
| Code-style and naming conventions documented | The instruction file states the project's conventions: naming patterns (camelCase vs snake_case, file naming), import-ordering rules, formatting rules, abbreviations policy. Soft check. | No conventions documented in a non-trivial codebase. |
| Commit and branching conventions documented | The instruction file states the commit-message convention (Conventional Commits, gitmoji, plain), the branch-naming convention, and any `git add` policy. Soft check. | No commit conventions in a project with multiple contributors (signal: more than one Git author in recent history). |
| Testing philosophy or approach documented | The instruction file states the testing convention: framework, query priority (Testing Library), behaviour-over-implementation, snapshot policy. Soft check. Skipped silently when no test files are present. | Tests exist but no testing convention is documented. |
| "What to do" and "What NOT to do" guidance present | The instruction file contains explicit guidance for the agent: things to avoid (no `git add -A`, no abbreviations, no hard-coded paths), things to prefer. Soft check — `present` requires both positive and negative guidance, `partial` for one-sided guidance. | No agent-specific guidance at all. |
| Tone or response-style guidance present | When the project surfaces user-facing AI features (signal: dependencies on `ai`, `@ai-sdk/*`, `langchain`, `@anthropic-ai/sdk`, `openai`), the instruction file documents the desired response style or persona for those features. Soft check. Skipped silently when no AI surface is detected. | AI surface present; no response-style guidance. |
| Imperative-present rule signal | When the project requires Conventional Commits, the instruction file or a referenced doc explicitly demands the imperative-present subject form ("Add X", not "Added X"). Soft check. Skipped silently when Conventional Commits is not in use. | Conventional Commits required; imperative-present rule not stated. |

### Layer 3 — Claude Code settings hygiene

These checks read `.claude/settings.json` and, when present, `.claude/settings.local.json`. When neither exists, the entire layer is skipped silently (the project simply has not adopted Claude Code-specific configuration). Settings checks **do not** rate the absence of the file as a violation; users who do not run Claude Code locally have no reason to commit a `settings.json`.

| Check | Expectation | Violation signal |
| --- | --- | --- |
| `settings.json` is valid JSON | When present, the file parses as JSON with no syntax errors. | Parse error. |
| `settings.local.json` is gitignored | `.gitignore` (at the repository root or `.claude/.gitignore`) excludes `settings.local.json` so personal overrides do not leak into the project. The committed `settings.json` is the shared baseline. | `settings.local.json` is committed or not gitignored. |
| No secrets in `settings.json` | The file contains no entries that look like API keys, tokens, passwords, or private URLs. The audit pattern-matches against common secret shapes (`sk-`, `ghp_`, `xoxb-`, hex strings of suspicious length, JWT-shaped strings, AWS access-key prefixes) and against keys named `apiKey`, `token`, `secret`, `password`, `privateKey`. | A pattern match is found. |
| No secrets in `settings.local.json` | Even though `settings.local.json` is local-only, secrets belong in environment variables and a secret manager — not in a JSON file on disk that other tools may index. Soft check — reported as `partial` when secrets are present in `settings.local.json` only. | Pattern match in `settings.local.json`. |
| Permissions are appropriately scoped | The `permissions.allow` list does not contain over-broad patterns. Broad patterns include `Bash(*)`, `Bash(* )`, `Edit(*)`, `Write(*)`, and patterns that match every path. The audit tolerates up to the threshold count (default 5; tunable via `--threshold-permission-broadness`); above that, reported as `violation`. The audit always names the broad patterns it found. | More than the threshold count of broad patterns. |
| `permissions.deny` is non-empty in security-sensitive projects | When the project ships to production (signal: detected deployable target), the `permissions.deny` list explicitly forbids destructive commands (`rm -rf /`, `git push --force`, dropping production tables, mass deletion patterns). Soft check — reported as `partial` when deny is empty. Skipped silently for library-only projects. | Deny list empty in a deployed project. |
| Hooks reference scripts that exist | When `hooks` are configured, every hook command either is a one-line shell expression or invokes a script that exists in the repository (`./scripts/...`, `node ./scripts/...`, `bun ./scripts/...`). | A hook references a missing script. |
| Environment variables do not contain secrets | The `env` block's values are not secrets. Variable **names** that look like secrets (`*_TOKEN`, `*_KEY`, `*_SECRET`) are tolerated when the **value** is empty or references another variable; concrete secret-shaped values are flagged. | Concrete secret in `env`. |
| Status-line and model overrides are intentional | When `statusLine` or `model` is set, the value matches a documented Claude Code option. The audit warns on stale model identifiers (e.g. retired model IDs). Soft check. | A stale or invalid identifier. |
| MCP-server entries are auditable | When `mcpServers` is present, every entry has a recognisable command, a transport, and either no secrets or environment-variable references for secrets. Soft check. Skipped silently when no `mcpServers` block exists. | A concrete secret in an MCP-server config. |

### Layer 4 — Multi-agent consistency and drift

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Single source of truth for shared rules | When more than one agentic instruction file exists (`CLAUDE.md` plus `AGENTS.md`, or `CLAUDE.md` plus `.cursorrules`), one is the source of truth and the others either reference it explicitly ("see `CLAUDE.md`") or are symlinks. The audit detects symlinks and explicit cross-references. Soft check — reported as `partial` when the files diverge. | Files contain conflicting guidance. |
| Tech-stack mentions match dependencies | Frameworks and tools named in the instruction files appear in `package.json` (`react`, `next`, `vitest`, `tsup`). Stale mentions (a framework that is no longer a dependency) are flagged. Soft check. | Multiple stale framework references. |
| Commands documented match `package.json` scripts | Commands shown in instruction files (`pnpm dev`, `npm run test`, `bun run build`) reference scripts that actually exist in `package.json`. | An instruction-file command references a missing script. |
| Cross-references resolve | Internal references in instruction files (`see ARCHITECTURE.md`, `[CONTRIBUTING.md](CONTRIBUTING.md)`, `docs/decisions/`) point at files or directories that exist. Always runs. | A referenced file is missing. |
| No retired-model identifiers | Instruction files do not mention model IDs that have been retired (`claude-3-opus-20240229`, `claude-2`, `gpt-3.5-turbo`). Soft check — reported as `partial` when found, since the mention is sometimes historical context. | Multiple retired-model mentions in active guidance. |
| Instruction-file freshness | When the project has had recent activity (signal: commits in the last 90 days), at least one agentic instruction file has been modified within the threshold (default 180 days). Soft check. | All instruction files older than the threshold in an active project. |
| No abbreviations in agentic prose (when project requires it) | When `CLAUDE.md` or another rule file states a "no abbreviations" policy, the instruction files themselves comply with that policy. Soft check. Skipped silently when no abbreviations policy is declared. | The policy is declared and the file violates it. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the audit cross-references the directory layout described in the instruction files against the actual top-level communities so stale layout descriptions surface in Layer 4.
2. **Confirms the project root.** Detects the presence of at least one of: `package.json`, `.git/`, `pyproject.toml`, `Cargo.toml`. Without any project root signal, the skill stops and tells the user it expects to be run inside a project root.
3. **Detects project shape** (deployable application, library, monorepo, hybrid) for Layer 3's deploy-aware deny-list check.
4. **Detects an AI surface** (presence of `ai`, `@ai-sdk/*`, `langchain`, `@anthropic-ai/sdk`, or `openai` in `dependencies`) for Layer 2's tone-and-response-style check.
5. **Walks every recognised agentic instruction file** and classifies each by tool family. The audit recognises:
   - `CLAUDE.md` at repo root or any directory.
   - `AGENTS.md` at repo root.
   - `.cursorrules` at repo root.
   - `.cursor/rules/*.mdc`.
   - `.github/copilot-instructions.md`.
   - `.windsurfrules`.
   - `.aider.conf.yml` and `CONVENTIONS.md` (Aider).
6. **Reads `.claude/settings.json` and `.claude/settings.local.json`** when present. Validates JSON, walks the `permissions`, `hooks`, `env`, `mcpServers`, `statusLine`, and `model` blocks.
7. **Reads `.gitignore`** to verify `settings.local.json` is excluded.
8. **Cross-references commands and tech-stack mentions** against `package.json` scripts and dependencies, and cross-references file links against the file system.
9. **Writes Layer 0 — the diagnostic snapshot** to `.architect-audits/agentic-audit/snapshot.md` and prepends the same content to `findings.md`.
10. **Walks each check in the active layer list**, applying any threshold overrides. Records a status, evidence, and (where relevant) sample file references per check.
11. **Writes phase 1 outputs** to `.architect-audits/agentic-audit/`:
    - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
    - `findings.json` — machine-readable.
    - `snapshot.md` — diagnostic snapshot on its own.
    - `metadata.json` — skill version, run timestamp, Graphify revision (when present), project shape, AI-surface flag, detected agentic instruction files, applied thresholds.
12. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

    > "Generate an implementation plan for the agentic-instruction and settings gaps? (yes/no)"

    On `yes`, writes `.architect-audits/agentic-audit/implementation-plan.md` describing exactly which sections to add to `CLAUDE.md` (or the project's primary instruction file), which permissions to tighten, which hooks to fix, which secrets to relocate, and which drifted references to update — ordered by impact: settings-hygiene fixes first (lowest blast radius and highest risk), then context-coverage gaps (immediate agent-quality lift), then operational-guidance gaps, then drift cleanup. The plan does not modify any project files.

    On `no`, exits cleanly.

## Implementation steps

### Step 1 — Confirm the prerequisites

Detect a project root by checking for at least one of `package.json`, `.git/`, `pyproject.toml`, or `Cargo.toml`. If none is present, print `agentic-audit: no project root detected (expected one of package.json, .git, pyproject.toml, Cargo.toml)` and stop.

### Step 2 — Detect project shape and AI surface

- Project shape: same heuristic as `/documentation-audit` Layer 4 (deployable, library, monorepo, hybrid).
- AI surface: scan `dependencies` and `devDependencies` for `ai`, `@ai-sdk/*`, `langchain`, `@anthropic-ai/sdk`, `openai`, `cohere-ai`, `replicate`. Record matches in `metadata.json`.

### Step 3 — Discover agentic instruction files

Walk the repository (excluding `node_modules`, `.git`, and gitignored paths) for:

- `CLAUDE.md` at any depth.
- `AGENTS.md` at the repository root.
- `.cursorrules` at the repository root.
- `.cursor/rules/*.mdc`.
- `.github/copilot-instructions.md`.
- `.windsurfrules`.
- `.aider.conf.yml`, `CONVENTIONS.md`.

For each file, record path, line count, last-modified date (from `git log -1 --format=%cI -- <file>`), and tool family. Identify the **primary** file as the most-substantive (longest non-empty file among the detected set, with `CLAUDE.md` at the repo root preferred when present and substantive).

### Step 4 — Read Claude Code settings

If `.claude/settings.json` exists, parse it. If parsing fails, record the parse error and continue with Layer 3 checks marked appropriately. Walk top-level keys: `permissions` (with `allow` and `deny` lists), `hooks`, `env`, `mcpServers`, `statusLine`, `model`. If `.claude/settings.local.json` exists, parse and walk the same keys; the local file shadows the committed file.

### Step 5 — Confirm gitignore status of `settings.local.json`

Read `.gitignore` at the repository root (and any `.claude/.gitignore`). Confirm that `settings.local.json`, `.claude/settings.local.json`, or an equivalent pattern is present. Record the result; the Layer 3 check uses it directly.

### Step 6 — Cross-reference for Layer 4

- Extract every fenced or backtick-quoted command from the agentic instruction files (`pnpm dev`, `npm run test`, `bun run build`, `make ...`). Match script-running commands against `package.json` scripts; record each as `present`, `missing-script`, or `not-applicable` (for non-script commands like `git status`).
- Extract every framework or tool name mentioned in instruction-file prose (heuristic: capitalised technical nouns plus a curated list — React, Next, Vitest, Vite, Bun, pnpm, Storybook, etc.) and confirm each appears in `dependencies` or `devDependencies`.
- Extract every file reference (`[X](path)` or `see X`) from instruction files and confirm the path exists.
- Detect mentioned model identifiers and compare against a list of retired identifiers maintained in this skill.

### Step 7 — Build the diagnostic snapshot

Aggregate the data from steps 3–6 into the items listed in Layer 0. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 8 — Resolve each check

For each check in the active layer list, walk its detection logic. Layer 3 checks are skipped silently when no `.claude/settings*.json` file exists. Layer 2's testing-philosophy check is skipped silently when no test files are present. Layer 2's tone-and-response-style check is skipped silently when no AI surface is detected.

For each check, record evidence and up to ten representative samples plus a total count.

### Step 9 — Write phase 1 outputs

Create `.architect-audits/agentic-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 10 — Print the concise chat summary and offer phase 2

Print a human-first, scannable summary in the chat. Do not print the full layered findings — those are written to disk in Step 9. The chat output has exactly this shape:

1. **Short header** — audit name, timestamp, and a one-line summary of the agentic-instruction state ("two instruction files, settings.json present, three drift findings").
2. **Top 5 Highest-Leverage Recommendations** — ordered by blast radius and leverage: settings-hygiene risks (secrets, broad permissions) first, then context-coverage gaps (the agent literally cannot do good work without these), then operational-guidance gaps, then drift cleanup. For fewer than five findings, print what exists. For each recommendation (numbered 1–5):
   - **Title** (one clear line).
   - **Why it matters** (explain the principle in 1–2 sentences).
   - **Real consequences if ignored** (honest downside for the team or project).
   - **Smallest high-leverage fix** (exact next step, effort level, and which files to touch).
   - At the end, add a lettered sub-list of concrete actions if useful (e.g. 2a, 2b) so the user can reply with "2b" or "1 and 3" to trigger implementation.
3. **Bottom line**: `Full detailed audit report (layered findings, snapshot, metadata, implementation plan) → .architect-audits/agentic-audit/findings.md`

When `--learn` or `--teach` is set, expand each recommendation into mid-level engineer teaching mode:
- For every item, explain as if teaching a mid-level engineer, pointing to specific files and line numbers in the current project.
- Use educational language: "Here's why a bloated CLAUDE.md actively hurts agent quality…", "This is the exact mistake I see in most projects when they first adopt Claude Code…", "The fix is small but pays off huge because the agent stops re-asking for context every session…".
- Include a short "What you'll learn from fixing this" section for each recommendation.
- Keep the numbered/lettered structure so the user can still reply with "2b" or "1 and 3".
- End with the same bottom-line link to the full report.

After printing, ask the single yes-or-no question: *"Generate an implementation plan for the agentic-instruction and settings gaps? (yes/no)"* Do not proceed to phase 2 without an explicit affirmative.

### Step 11 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`, ordered by impact:

1. **Header** — repository name, baseline version, project shape, AI-surface flag, detected agentic instruction files, timestamp, total counts per layer.
2. **Settings-hygiene fixes** (highest priority) — secrets to relocate (with the specific environment-variable names to use), broad permissions to tighten (with safer patterns proposed), missing `permissions.deny` entries for deployed projects, hook references to fix, `settings.local.json` gitignore entry to add. Per finding, the concrete diff to make.
3. **Context-coverage fixes** — sections to add to the primary instruction file: project purpose paragraph, tech-stack line, architectural mental-model paragraph, links to canonical docs, glossary stub when appropriate. Per finding, a starter snippet the user can paste.
4. **Operational-guidance fixes** — build/test/run commands block, code-style and naming conventions block, commit-conventions block, testing-philosophy block, agent-specific "do this / not that" block. Per finding, a starter snippet.
5. **Drift fixes** — instruction-file commands to update against `package.json` scripts, stale tech-stack mentions to remove, broken cross-references to repair, retired model identifiers to replace. Per finding, the exact substitution.
6. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not write instruction files, edit settings, or relocate secrets.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-29T13:47:00Z",
  "runFinishedAt": "2026-04-29T13:47:18Z",
  "projectShape": "deployable-application",
  "aiSurfaceDetected": false,
  "thresholds": {
    "instructionMinLines": 25,
    "instructionMaxLines": 400,
    "permissionBroadness": 5,
    "contextCoverage": 60
  },
  "snapshot": {
    "agenticInstructionFiles": [
      { "path": "CLAUDE.md", "lines": 142, "lastModified": "2026-04-12", "toolFamily": "claude" },
      { "path": "AGENTS.md", "lines": 38,  "lastModified": "2026-02-01", "toolFamily": "agents-md" }
    ],
    "agenticInstructionLineTotal": 180,
    "claudeDirPresent": true,
    "settingsJson": {
      "present": true,
      "topLevelKeys": ["permissions", "hooks", "env"],
      "permissionAllowCount": 31,
      "permissionDenyCount": 0
    },
    "settingsLocalJson": { "present": true, "gitignored": true },
    "hookCounts": { "PreToolUse": 1, "PostToolUse": 0, "Stop": 1, "Notification": 0 },
    "mcpServerCount": 2,
    "statusLineSet": true,
    "modelOverrideSet": false
  },
  "summary": {
    "projectContextCoverage":         { "present": 4, "partial": 2, "missing": 1, "violation": 0 },
    "operationalGuidanceAndConventions": { "present": 3, "partial": 3, "missing": 0, "violation": 1 },
    "claudeCodeSettingsHygiene":      { "present": 6, "partial": 1, "missing": 0, "violation": 2 },
    "multiAgentConsistencyAndDrift":  { "present": 4, "partial": 1, "missing": 0, "violation": 2 }
  },
  "checks": [
    {
      "layer": "claude-code-settings-hygiene",
      "check": "no-secrets-in-settings-json",
      "status": "violation",
      "evidence": [".claude/settings.json"],
      "samples": [
        { "key": "env.OPENAI_API_KEY", "matchPattern": "sk-..." }
      ],
      "totalCount": 1,
      "expectation": "settings.json contains no secret-shaped values.",
      "gap": "An OpenAI API key with the sk- prefix is committed in .claude/settings.json under env.OPENAI_API_KEY.",
      "remediation": "Move the secret to a real environment-variable manager. Replace the value in settings.json with an empty string or a reference, rotate the key, and confirm no historical commits expose it."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), the project shape, the AI-surface flag, the detected agentic instruction files, and applied thresholds.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Threshold overrides are recorded in `metadata.json` so a partial run can be reproduced.
- Secret-pattern matches are not echoed in chat output beyond a redacted match preview (`sk-…`); the raw value never appears in any audit artefact.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no project root detected` | The skill is run outside a project root. | Change directory into the project root and re-run. |
| `settings.json` parse error | Invalid JSON committed by hand. | The audit reports the parse error and the line, then continues with the rest of Layer 3 marked `missing` for checks that depend on parsed contents. The user fixes the JSON and re-runs. |
| Multiple instruction files diverge | `CLAUDE.md` and `.cursorrules` were edited independently and now contradict each other. | The Layer 4 single-source-of-truth check reports `partial` or `violation` and the implementation plan recommends consolidating onto one file with the others either symlinked or rewritten as short pointers. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The Layer 4 layout-drift check loses graph cross-referencing; instruction-file checks otherwise unaffected. |
| Project uses a non-Node ecosystem | Python or Rust project. | Layer 4's `package.json`-script-drift check is skipped silently. The other layers still run; tech-stack-mentions cross-referencing degrades to "no dependency manifest available" and reports `partial` rather than `violation` for stale mentions. |
| Repository is shallow-cloned | `git log` cannot resolve last-modified for some files. | Last-modified data is recorded as unknown for the affected files. Freshness check degrades to `partial` with a metadata note. |
| Project is monorepo with per-package `CLAUDE.md` | Each package has its own instruction file. | The audit treats the repo-root file as the primary and walks every per-package file as additional. Per-package files are graded against the same Layer 1 and Layer 2 baseline; results aggregate into the same findings file. |
| Secret-shape match is a false positive | A long hexadecimal string is a Git commit hash, not a token. | The audit reports the match with context and a redacted preview. The user reviews and dismisses the false positive; threshold tuning is not currently exposed for secret-pattern matching, so the dismissal is a human review step. |

## What this skill explicitly does NOT do

- Modify any agentic instruction file, settings file, or other project file.
- Generate agentic instruction content. The implementation plan describes what to write; it does not write it.
- Run any code, including commands referenced in instruction files. Drift detection compares text against `package.json`; it does not execute.
- Audit `~/.claude/settings.json` or any other home-directory settings. Project-local settings only.
- Audit `~/.claude/CLAUDE.md` or other user-private agentic instruction files. Repository-scoped files only.
- Rotate secrets, scrub Git history, or push fixes. Secret detection reports the match; the human handles rotation.
- Audit MCP-server source code or behaviour. Only the configuration shape in settings is checked.
- Audit the prompt-engineering quality of agentic instructions beyond the structural and coverage checks above. Whether a paragraph is well-phrased is a human judgement call.
- Replace human review of the agentic instruction file. The audit verifies presence and structure; whether the agent actually performs better is measured by re-running real tasks against the agent.
- Audit non-agentic AI-feature code (LLM call sites, prompt templates) inside the application source. That is owned by general code review, not by `/agentic-audit`.
- Audit Cursor-specific MDC frontmatter semantics (`alwaysApply`, `globs`, `description`) beyond confirming the file exists and is recognised. The semantic check belongs to a future Cursor-specific audit.
