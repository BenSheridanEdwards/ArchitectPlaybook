---
name: architecture-audit
description: Graphify-powered architectural audit of a TypeScript codebase. Produces a diagnostic snapshot + checks module boundaries, coupling, state/data flow, and conventions, with optional implementation plan.
trigger: /architecture-audit
---

# /architecture-audit

Audit a TypeScript codebase against an opinionated architectural baseline organised in four layers — **module boundaries**, **coupling and complexity**, **state and data flow**, **convention adherence** — preceded by a **diagnostic snapshot** that describes the codebase's actual shape. Uses the Graphify knowledge graph as the primary data source. Then offers to generate an implementation plan for the gaps. The audit is fully static and read-only.

The default mental model is TypeScript and React. Most checks apply to any TypeScript codebase; the state and data flow layer leans React-specific because that is where the most consequential architectural pain in modern frontend work actually lives.

## Hard requirement: the Graphify knowledge graph

This is the one audit that requires `graphify-out/graph.json`. Half the checks (god-node detection, community comparison, circular-dependency detection, fan-in and fan-out analysis) are unimplementable without it. If the graph is missing, the skill stops immediately with this message:

> `/architecture-audit` requires the Graphify knowledge graph. Please run `/pre-audit-setup` first, then re-run this audit.

There is no graceful degradation. A misleading half-audit is worse than no audit.

## Usage

```
/architecture-audit                                 # default: concise Top 5 + full report saved + ask about plan
/architecture-audit --learn                         # mid-level engineer teaching mode (detailed explanations + file/line examples)
/architecture-audit --teach                         # alias for --learn
/architecture-audit --pattern=<feature-folders|layered|atomic-design|monorepo-workspaces|infer>  # override inferred architectural pattern
/architecture-audit --threshold-god-module=40       # override the default fan-in threshold (default 30)
/architecture-audit --threshold-god-component=30    # override the default god-component parent threshold (default 25)
/architecture-audit --threshold-file-size=500       # override the default file-size threshold in lines (default 400)
/architecture-audit --threshold-fan-out=20          # override the default component fan-out threshold (default 15)
```

**💡 Pro tip**: Use `/worktree architecture` to run this in an isolated worktree.

This skill never accepts `--apply`. The implementation plan is descriptive Markdown.

The defaults baked into the skill are the recommended baseline. Threshold flags exist as an escape hatch for codebases with deliberately different sensibilities; the canonical path to evolving the defaults themselves is `/system-self-improve`.

## The opinionated baseline

A check resolves to one of four statuses, with the same semantics as `/accessibility-audit`:

- **present** — the invariant holds; the audit found no problems.
- **partial** — the invariant holds in most places but the audit found a small number of edge cases.
- **missing** — a structural prerequisite is absent (for example, no feature folders exist, so the "single entry point" check has nothing to evaluate).
- **violation** — the invariant is broken; the audit identified concrete code that fails it.

Layer 0 is informational only and has no status.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

Written before any check runs, so the human reading the report has architectural context.

- Top 10 god nodes by PageRank, with file path and incoming edge count.
- Communities detected by Graphify, with member count, dominant directory, and a representative file per community.
- Module count, edge count, average and median fan-in and fan-out.
- Directory tree depth statistics: deepest path, average depth, count of files at each depth.
- Detected meta-framework: Next.js App Router, Next.js Pages Router, Remix, Vite-React, plain React, plain TypeScript.
- Detected architectural pattern: feature-folders, layered, atomic-design, monorepo-workspaces, or no-clear-pattern. The inference rationale is recorded so the user can audit the audit.
- Monorepo presence: yes or no, with workspace count when yes.

### Layer 1 — Module boundaries

| Check | Expectation | Violation signal |
| --- | --- | --- |
| No circular dependencies | The dependency graph is acyclic. | One or more cycles detected by Graphify; each cycle reported as an ordered list of module paths. |
| Feature folders have a single entry point | Each top-level folder under `src/features/`, `src/modules/`, or the equivalent for the detected pattern exposes a barrel (`index.ts`) and external imports go through it. | An external file imports from a deep path inside a feature folder, bypassing its `index.ts`. Reported with the importer, the feature, and the bypassed path. |
| No deep relative imports | Relative imports go up at most two levels (`../../`). | Any import string containing `../../../` or deeper. Reported with the importer file. |
| No back-imports across layers | When the architectural pattern is layered (or detected as layered), upstream layers do not import from downstream layers (for example, UI does not import from data; data does not import from UI). When the pattern is feature-folders, sibling features do not import from each other except through their public entry points. | An import that crosses a boundary in the wrong direction. Reported with the importer, the import, and the direction of the violation. |
| Cross-workspace contracts respected (monorepos only) | Internal packages import only from each other's published entry points, not deep paths. | A deep import into another workspace's `src/` directory. Skipped silently when the project is not a monorepo. |

### Layer 2 — Coupling and complexity

Defaults are in parentheses; every threshold is overridable via the flags above.

| Check | Expectation | Violation signal |
| --- | --- | --- |
| No god module | No single module has fan-in greater than the threshold (default 30). | A module exceeding the threshold. Reported with the module path, its PageRank rank, and its inbound edge count. |
| No god component | No React component is rendered by more than the threshold number of parents (default 25). | A component exceeding the threshold, with the component path and the count of distinct rendering parents. |
| File size budget | No source file exceeds the threshold in lines (default 400). | A file exceeding the threshold, reported with its line count. |
| Component fan-out budget | No component imports more than the threshold (default 15) — high fan-out signals a component that is doing too much. | A component exceeding the threshold, reported with the count and the imported modules. |
| No orphaned files | Every source file is reachable from at least one entry point. | Files with zero inbound edges that are not themselves entry points (the application entry, route files, test files, configuration files). Reported as a list of likely-dead modules; the user verifies before deletion. |

### Layer 3 — State and data flow

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Single state-management strategy | Exactly one of: Redux Toolkit, Zustand, Jotai, MobX, React Context (alone). Not multiple in the same application. | Two or more state-management libraries present in `package.json` dependencies, with no clear migration path declared. |
| Data fetching layer separated | Server state lives in a designated data-fetching layer (TanStack Query, SWR, RTK Query, or a custom hooks layer) — not in component-level `useEffect` plus `fetch`. | Files outside the designated hooks or data layer that call `fetch`, `axios`, or framework-level data primitives directly. Reported with file references. |
| No global mutable singletons | No top-level `let` exports holding mutable state. | A module-level mutable export (`export let x = ...`). Reported per occurrence. |
| Side effects isolated | Components do not read or write `localStorage`, `sessionStorage`, `document.cookie`, or `window.*` directly — they go through a hook or a service module. | Direct usage in component files. Reported with file references. |
| Server vs. client distinction (Next.js App Router only) | The `'use client'` directive is used deliberately, not on every file. Server components do not import client-only modules and vice versa. | A `'use client'` file importing a known server-only module, or a server component importing a known client-only one. Excessive `'use client'` boundary fragmentation (more than half of the components marked client) reported as `partial`. Skipped silently when the App Router is not in use. |

### Layer 4 — Convention adherence

| Check | Expectation | Violation signal |
| --- | --- | --- |
| File-naming consistency | The audit detects the dominant naming pattern in each directory (PascalCase for components, `useFoo.ts` for hooks, kebab-case for utilities, etc.) and flags outliers. | Files whose name does not match the dominant pattern in their directory. |
| Directory-structure consistency | Every directory at the same depth follows the same organisational convention as siblings of the same kind. | A directory that does not match the inferred pattern (a feature folder among layered directories, or vice versa). |
| Index barrels consistent | If barrel files (`index.ts` re-exports) are used in any feature folder, they are used in every feature folder. | Mixed-mode usage. |
| Public API documented | Each feature folder's `index.ts` has a documenting comment block describing its public surface, or the folder has a `README.md`. This is a soft check — missing documentation reports as `partial`, never `violation`. | Feature folders without either a documenting comment in the barrel or a `README.md`. |

## What this skill does

1. **Stops if the knowledge graph is missing.** Refuses to run with the friendly message above.
2. **Reads the knowledge graph.** Loads `graphify-out/graph.json` and `graphify-out/GRAPH_REPORT.md`. The PreToolUse hook installed by `/pre-audit-setup` reminds you of this on every Glob and Grep — respect it.
3. **Confirms a TypeScript project.** Detects TypeScript via `tsconfig.json` and a `package.json` dependency on `typescript`. If absent, the skill stops and tells the user it currently supports TypeScript only.
4. **Detects the meta-framework and the architectural pattern.** Records both in the diagnostic snapshot. The pattern detection is heuristic; the rationale is recorded so the user can override with `--pattern=` if the inference is wrong.
5. **Writes Layer 0 — the diagnostic snapshot** to `.architect-audits/architecture-audit/snapshot.md` and embeds the same content at the top of `findings.md`. The snapshot is informational and always present.
6. **Walks each check in the active layer list**, applying any `--include`, `--exclude`, and threshold overrides. Records a status, evidence, and (where relevant) sample file references per check.
7. **Writes phase 1 outputs** to `.architect-audits/architecture-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own, for easy linking from elsewhere.
   - `metadata.json` — skill version, run timestamp, Graphify revision hash, framework, detected pattern, applied thresholds, applied filters.
8. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the architectural violations and partial-coverage gaps? (yes/no)"

   On `yes`, writes `.architect-audits/architecture-audit/implementation-plan.md` describing exactly which boundaries to introduce, which god modules to decompose, which conventions to align, and which orphans to remove — ordered by layer and then by severity. The plan does not modify any project files.

   On `no`, exits cleanly. 

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f graphify-out/graph.json || { echo "/architecture-audit requires the Graphify knowledge graph. Please run /pre-audit-setup first, then re-run this audit."; exit 1; }
test -f tsconfig.json || { echo "architecture-audit: no tsconfig.json detected. This skill currently supports TypeScript projects only."; exit 1; }
test -f package.json || { echo "architecture-audit: no package.json detected. This skill currently supports TypeScript projects only."; exit 1; }
```

### Step 2 — Detect framework and architectural pattern

Read `package.json` and the directory layout:

- `next` dependency plus `app/` directory → Next.js App Router.
- `next` dependency plus `pages/` directory → Next.js Pages Router.
- `@remix-run/*` dependency → Remix.
- `vite` plus `react` → Vite-React.
- `react-scripts` → Create React App.
- `react` only → plain React.
- Otherwise → plain TypeScript.

Pattern inference (skipped when `--pattern=` is set explicitly):

- A `src/features/` or `src/modules/` directory with sibling folders, each containing its own components, hooks, and tests → `feature-folders`.
- Top-level directories named for layers (`presentation/`, `application/`, `domain/`, `infrastructure/`, or `ui/`, `services/`, `data/`) → `layered`.
- Directories named `atoms/`, `molecules/`, `organisms/`, `templates/`, `pages/` → `atomic-design`.
- A `packages/` directory plus a workspace declaration in `package.json` (or `pnpm-workspace.yaml`) → `monorepo-workspaces`.
- None of the above → `no-clear-pattern`.

Record both the detection result and the rationale in `metadata.json`.

### Step 3 — Build the diagnostic snapshot

Read the knowledge graph and compute the items listed in Layer 0 above. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 4 — Resolve each check

For each check in the active layer list, walk its detection logic against the graph and the source tree:

- **All required signals indicate the invariant holds →** `present`.
- **Most signals hold; a small number of exceptions →** `partial`. Define "small" per check (typically: at most three offenders, or fewer than 5% of the relevant population).
- **The structural prerequisite is absent →** `missing`.
- **Concrete violators exist →** `violation`. Always include sample file references.

Record every matching path in the `evidence` array. For checks that can produce many offenders (god modules, deep relative imports, naming inconsistencies), record up to ten representative samples plus a total count rather than the exhaustive list.

### Step 5 — Write phase 1 outputs

Create `.architect-audits/architecture-audit/` if it does not exist. Write `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json`. If a previous run exists, overwrite all four. `implementation-plan.md` is preserved unless the user agrees to regenerate it.

### Step 6 — Print the concise chat summary and offer phase 2

Print a human-first, scannable summary in the chat. Do not print the full layered findings — those are written to disk in Step 5. The chat output has exactly this shape:

1. **Short header** — audit name, timestamp, and a one-line summary of the codebase state.
2. **Top 5 Highest-Leverage Recommendations** — ordered by architectural principles: test philosophy, maintainability, risk reduction, velocity, long-term health. For fewer than five findings, print what exists. For each recommendation (numbered 1–5):
   - **Title** (one clear line).
   - **Why it matters** (explain the principle in 1–2 sentences).
   - **Real consequences if ignored** (honest downside for the team or project).
   - **Smallest high-leverage fix** (exact next step, effort level, and which files to touch).
   - At the end, add a lettered sub-list of concrete actions if useful (e.g. 2a, 2b) so the user can reply with "2b" or "1 and 3" to trigger implementation.
3. **Bottom line**: `Full detailed audit report (layered findings, snapshot, metadata, implementation plan) → .architect-audits/architecture-audit/findings.md`

When `--learn` or `--teach` is set, expand each recommendation into mid-level engineer teaching mode:
- For every item, explain as if teaching a mid-level engineer, pointing to specific files and line numbers from the current codebase.
- Use educational language: "Here's why this pattern bites teams in the long run…", "This is the exact mistake I see in most codebases at your stage…", "The fix is small but pays off huge because…".
- Include a short "What you'll learn from fixing this" section for each recommendation.
- Keep the numbered/lettered structure so the user can still reply with "2b" or "1 and 3".
- End with the same bottom-line link to the full report.

After printing, ask the single yes-or-no question: *"Generate an implementation plan for the gaps identified above? (yes/no)"* Do not proceed to phase 2 without an explicit affirmative.

### Step 7 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, framework, detected pattern, timestamp, total counts per layer.
2. **Layer 1 — module boundaries plan**:
   - For each circular dependency, propose a specific edge to break and the refactor that breaks it (extract shared dependency, invert direction with an interface, move shared code).
   - For each missing barrel, the file to create and a starter export list.
   - For each deep relative import, the import to rewrite and the path-alias entry to add to `tsconfig.json` if appropriate.
3. **Layer 2 — coupling and complexity plan**:
   - For each god module, candidate cleavage planes derived from community structure.
   - For each oversized file, suggested split lines based on internal cohesion.
   - For each orphan, a short note recommending verification before deletion.
4. **Layer 3 — state and data flow plan**:
   - Migration strategy when multiple state-management libraries are present.
   - Files to extract direct `fetch` calls from, with the recommended hooks-layer destination.
   - Replacement strategy for any global mutable singletons.
5. **Layer 4 — convention adherence plan**:
   - Renames per outlier file, in batched form (suitable for a single rename pull request per directory).
   - Documentation snippets for missing public API documentation.
6. **Closing checklist** — a flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not move files, rewrite imports, or delete orphans.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:24Z",
  "framework": "next-app-router",
  "pattern": { "detected": "feature-folders", "rationale": "src/features/* with co-located components, hooks, and tests", "overrideUsed": false },
  "thresholds": { "godModule": 30, "godComponent": 25, "fileSize": 400, "fanOut": 15 },
  "snapshot": {
    "godNodes": [
      { "path": "src/lib/api-client.ts", "inboundEdges": 87, "pageRank": 0.041 }
    ],
    "communities": [
      { "id": 0, "memberCount": 42, "dominantDirectory": "src/features/inbox", "representative": "src/features/inbox/MessageList.tsx" }
    ],
    "moduleCount": 1284,
    "edgeCount": 5712,
    "fanInMean": 4.4,
    "fanInMedian": 2,
    "fanOutMean": 4.4,
    "fanOutMedian": 3,
    "directoryDepth": { "deepest": 7, "average": 3.2 }
  },
  "summary": {
    "moduleBoundaries":       { "present": 2, "partial": 1, "missing": 0, "violation": 2 },
    "couplingAndComplexity":  { "present": 1, "partial": 1, "missing": 0, "violation": 3 },
    "stateAndDataFlow":       { "present": 3, "partial": 0, "missing": 0, "violation": 2 },
    "conventionAdherence":    { "present": 2, "partial": 1, "missing": 0, "violation": 1 }
  },
  "checks": [
    {
      "layer": "coupling-and-complexity",
      "check": "no-god-module",
      "status": "violation",
      "thresholdApplied": 30,
      "evidence": [],
      "samples": [
        { "path": "src/lib/api-client.ts", "inboundEdges": 87, "pageRank": 0.041 }
      ],
      "expectation": "No module has fan-in greater than 30.",
      "gap": "1 module exceeds the threshold; api-client is a load-bearing god node.",
      "remediation": "Decompose api-client by feature: extract per-feature client modules and have features import their own slice."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top followed by one section per check, grouped by layer.

`snapshot.md` contains only the snapshot — useful for linking directly from a pull-request description or from another skill's output.

`metadata.json`:

```json
{
  "skillName": "architecture-audit",
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:24Z",
  "graphifyRevision": "<hash from graphify-out/metadata>",
  "framework": "next-app-router",
  "pattern": { "detected": "feature-folders", "rationale": "...", "overrideUsed": false },
  "thresholds": { "godModule": 30, "godComponent": 25, "fileSize": 400, "fanOut": 15 },
  "filtersApplied": { "layers": ["module-boundaries", "coupling-and-complexity", "state-and-data-flow", "convention-adherence"], "include": [], "exclude": [] }
}
```

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags, threshold overrides, and the pattern override are recorded in `metadata.json` so a partial run can be reproduced.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| Knowledge graph missing | `/pre-audit-setup` has not been run, or the graph has been deleted. | Stop. Print the friendly message and exit. Do not run any check. |
| `tsconfig.json` missing | The skill is being run on a JavaScript-only project, or outside the project root. | Stop. Inform the user that the skill currently supports TypeScript only. |
| Pattern inference inconclusive | The directory layout matches no clear convention. | Record `no-clear-pattern` in the snapshot. Skip the layered-specific and feature-folder-specific checks; report them as `missing` with an explanation. |
| Pattern override is wrong | The user passed `--pattern=layered` but no layered structure exists. | Trust the user's intent. Run the layered-style checks and report what they would expect to see; many will resolve as `missing` or `violation`. |
| Threshold override is extreme | A user passes `--threshold-god-module=1000`. | Honour the value. Record it in `metadata.json` so the report makes the choice visible. |
| Graph stale (large refactor since last `/graphify`) | The graph and the source no longer agree. | Detect stale-graph by comparing graph file modification time to the most recent `git log` commit time on source files. When stale, prefix the diagnostic snapshot with a warning and recommend `/pre-audit-setup --force`. Do not refuse to run. |

## What this skill explicitly does NOT do

- Run any code, type-checker, linter, or test.
- Move files, rewrite imports, delete orphans, or otherwise modify the project.
- Install any package or dependency.
- Create, modify, or delete any file outside `.architect-audits/architecture-audit/`.
- Open pull requests or commit anything to git.
- Audit JavaScript-only projects.
- Audit individual workspaces in a monorepo. The skill audits the repository root and reports on cross-workspace boundaries; per-workspace deep-dives are recommended as follow-up.
- Make architectural decisions on the user's behalf. The implementation plan proposes; the human disposes.
