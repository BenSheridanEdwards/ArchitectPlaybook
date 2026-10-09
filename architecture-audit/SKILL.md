---
name: architecture-audit
description: Audit a TypeScript codebase's structure — import cycles and boundaries, module depth, change hotspots, and data-flow ownership — with verified evidence, then run a design session on the candidate you choose.
trigger: /architecture-audit
---

# /architecture-audit

Find where the structure of a TypeScript codebase makes change expensive or risky, and what to change first.

The audit has three sources:
- a deterministic collector, which builds the import graph, Git hotspots, and change coupling;
- the shared design vocabulary;
- the judgement of the model, spent where change concentrates.

It is static and read-only. It ends with a design session on one candidate, not a generic plan.

The vocabulary comes from John Ousterhout's deep modules and from the red flags agent contributors trip over:
- **Module:** anything with an interface and an implementation.
- **Interface:** everything a caller must know.
- **Depth:** behaviour hidden per unit of interface.
- **Seam:** where an interface lives.
- **Locality:** what maintainers gain when a change stays in one place.

[Detection notes](references/detection.md) explain how to evaluate each check.

## Usage

```
/architecture-audit                     # audit the current repository
/architecture-audit --worktree          # run in .worktrees/architecture-audit on its own branch
/architecture-audit --since=<ref>       # judge only what changed since <ref> (a provisional, filtered run)
/architecture-audit --months=12         # widen the Git history window for hotspots (default 6)
/architecture-audit --with-run          # also run installed dependency-cruiser, madge, or knip to confirm collector results
/architecture-audit --pattern=layered   # declare the intended pattern instead of inferring it
/architecture-audit --learn             # teaching mode; --teach is an alias
```

`--pattern` accepts `feature-folders`, `layered`, `hexagonal`, or `monorepo-workspaces`. It replaces the inferred pattern as the declared dependency direction, and is recorded in the snapshot, so it does not make the run provisional. `--since` and `--months` do, because they change what the audit measures.

## Ownership

| Concern | Owner |
| --- | --- |
| Import cycles, boundaries, module depth, state ownership, change hotspots, server-state placement | `/architecture-audit` |
| React component and hook correctness | `/react-audit` |
| Runtime cost of rendering and data fetching | `/performance-audit` |
| Lint rules that enforce boundaries, as configuration | `/linting-audit` |
| Whether architecture documents and decision records exist and are current | `/documentation-audit` |

`/architecture-audit` owns where server data loads and how global client state is held. The React and performance audits still carry overlapping checks (`react-audit.server-state-in-data-layer`, `react-audit.single-global-state-library`, and `performance-audit.single-data-fetching-strategy`). Under Architecture Decision Record 0004, each retires its copy or points it here with `relatedChecks` when its catalog moves to schema `1.2.0`.

The three tool checks always cover the whole repository, also in a `--since` run, because a cycle or a boundary rule is a property of the whole graph.

## The baseline

Statuses follow the shared taxonomy: `present`, `partial`, `missing`, or `violation`. Checks that cannot apply are recorded as not applicable, and checks you could not evaluate as not evaluated. Severity follows Architecture Decision Record 0004. The method column says whether the collector decides the check (`tool`) or you do (`model`).

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

The collector records these facts:
- source files and resolved imports;
- import cycles;
- hubs (high fan-in and fan-out) and the most-imported modules;
- orphan candidates;
- Git hotspots and change coupling;
- boundary tooling, decision-record directories, and glossaries.

Add the framework, the architectural pattern you infer (with your reasoning), and whether the repository is a multi-package workspace.

### Layer 1 — Dependency structure

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| No import cycles | medium | tool | Runtime imports form no cycles; type-only imports are excluded. | A cycle among runtime imports, cited by the importing lines that form it. |
| Boundaries enforced by tooling | medium | tool | A boundary tool encodes the dependency rules and runs in a script or continuous integration. | No boundary tool, or one that nothing runs. |
| Dependencies point the declared way | medium | model | Domain and shared code do not import user-interface, framework, or infrastructure code; sibling features meet only at public entry points. | An import against the declared or inferred direction. |
| Module internals are unreachable | medium | model | Outside code imports only a module's public entry point, and something makes importing internals fail. | Imports reaching into another feature's or package's internal files. |
| Workspaces import each other through entry points | medium | tool | Workspaces import each other by package name. Not applicable outside multi-package workspaces. | A relative import into another workspace's files; test-only cases are partial. |

### Layer 2 — Module design

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Modules are deep | medium | model | Important modules hide substantial behaviour behind a small interface; deleting one would concentrate complexity. | An interface nearly as complex as its implementation, or callers coordinating several calls for one operation. |
| No pass-through layers | medium | model | Every layer adds policy, adaptation, or a distinct abstraction. | A wrapper that forwards the same arguments to another with the same shape. |
| Each piece of state has one owner | high | model | Each store, cache entry, table, or shared object has one writer. | More than one module writes the same state. |
| One supported way to do each task | medium | model | Each recurring task has one supported mechanism. | Two mechanisms for the same task in active use. |
| No hand-synced lists | medium | model | Each list of items has one source the others derive from, or a check that fails when they disagree. | The same items listed in several places with no derivation or check. |
| Representations stay behind interfaces | medium | model | External data is parsed into domain types at the boundary; wire, storage, and framework types stay inside their module. | Wire or storage types used deep inside other layers. |
| Code grouped by what it knows | low | model | Modules are organised around the knowledge they own, not execution order. | Stage-named modules that each re-implement one representation. |

### Layer 3 — Change risk

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Hotspots are cohesive and tested | medium | model | The most frequently changed files are cohesive and tested at their interface. | A top hotspot that is large, mixes responsibilities, or is untested. |
| Files that change together live together | medium | model | Files that repeatedly change together share a module, or the coupling is explained. | Strong change coupling across features or packages with no structural reason. |
| Hub modules are stable | medium | model | High fan-in modules import little and change rarely; re-exporting barrels are judged by what they expose. | High fan-in with high fan-out, or with frequent change. |
| No orphaned modules | low | model | Every source module is reachable from an entry point, route, test, or configuration. | Modules nothing imports that are not entry points, verified before reporting. |

### Layer 4 — Data flow and decisions

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Server state has one home | medium | model | Server data loads in one designated place: a query layer, or the framework's server-side data primitives. | Client components fetching in effects, or several data-fetching strategies. |
| One client-state strategy | medium | model | Global client state uses one strategy; context is for dependency injection and stable values. | Two or more global state libraries with no stated migration. |
| Side effects sit at the edges | medium | model | Components reach storage, cookies, and globals through a hook or service. | Direct storage, cookie, or window access in components. |
| Decisions are recorded and still hold | medium | model | Hard-to-reverse decisions are recorded, and the code still follows them. | No record of major decisions, or code that contradicts one. |
| Domain language is consistent | low | model | Each domain concept has one name in code and documentation. Soft check — mixed adherence is reported as partial. | One concept named differently across modules. |

## What this skill does

1. Stages a run through the shared audit protocol. The collector decides the three tool checks and records the Layer 0 facts.
2. Spends your reading on the hotspots, the hubs, and the modules the collector flags, rather than walking every file.
3. Evaluates the model checks with the deletion test and the red flags in the detection notes. Every finding cites the lines it rests on.
4. Publishes `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` through the protocol, and summarises the act-on findings.
5. Offers a design session on one candidate the user picks.

## Implementation steps

1. **Begin.** Follow [the run protocol](../audit-protocol/references/run-protocol.md). With `--worktree`, create the worktree as it describes.

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../audit-protocol/scripts/audit_run.py" begin architecture-audit
   ```

   - Pass `--since <ref>` for a diff-scoped run.
   - Pass `--enrichment with-run` and `--threshold months=<n>` to record those options. The collector receives both.
   - The collector runs automatically.
2. **Read the evidence the collector staged.** Read the `snapshot` in `.architect-audits/architecture-audit/.staging/run.json`. If `graphify-out/GRAPH_REPORT.md` exists, read its god nodes and communities. Record the framework, the pattern, and your reasoning with `snapshot --set`. With `--pattern`, record it as `declaredPattern` and judge direction against it; otherwise record `inferredPattern` and the evidence for it.
3. **Confirm the tool checks when asked.** With `--with-run`, run `npx --no-install depcruise`, `madge --circular`, or `knip` if they are installed. Re-record any check whose result differs, citing the command.
4. **Evaluate every model check.**
   - Start with the top hotspots, the hubs, and the change-coupling pairs. Then sample the feature and package entry points.
   - Apply the detection notes.
   - Record each check with `record`, citing `path:line` with quoted fragments. Give every non-present result a `--tier`. Show absence with `files:` or a `search:` count, and prefix free-text observations with `note:`.
   - Use `hypothesis` for suspicions you cannot verify.
   - Use `not-evaluated` with a reason when the repository gives you no basis, for example no recorded decisions to compare against.
5. **Judge.** Keep at most about five findings `act-on`, ranked by severity and then by how often the code changes. Dismiss with a reason anything that is consistent with a recorded decision or a deliberate convention.
6. **Finish.** Run `audit_run.py finish architecture-audit`, fix anything it rejects, and present the chat summary the protocol defines.
7. **Offer the design session.** Ask: "Want to explore one of these? Pick a number, or say no."

## Phase 2: the design session

Do not write a static plan for architecture. On the user's pick:

1. **Frame the problem.** State the constraints, what the deepened module must hide, its dependency category, and which tests will survive.
2. **Design it twice.** Sketch two or three structurally distinct interfaces:
   - the smallest interface;
   - the most common caller first;
   - ports and adapters where the dependency is truly external.

   Compare them on depth, locality, and where the seam sits, and recommend one.
3. **Grill the choice in rounds.** Ask every currently answerable question at once, numbered, each with your recommended answer. Look up facts yourself; ask the user only for decisions.
4. **Write the agreed outcome** to `.architect-audits/architecture-audit/implementation-plan.md` as agent briefs, in the format the protocol defines.
5. **Offer to record rejections.** If the user rejects a candidate for a lasting reason, offer `audit_run.py decide <check-id> --decision accepted-risk ...`, or an architecture decision record, so later audits do not suggest it again.

## Repository Quality Score findings contract

The protocol publishes findings schema `2.0.0`. It writes one `runIdentifier`, `runStartedAt` and `runFinishedAt`, and the `checkCatalogVersion`. It records `applicability`, `evaluationState`, and `evidenceQuality` for every catalog check, and repeats the run identity in `metadata.json`. See `.agents/AUDIT_FINDINGS_CONTRACT.md` in the playbook repository.

## What this skill explicitly does NOT do

- Modify, move, or delete any project file. The only writes are under `.architect-audits/architecture-audit/`, through the protocol.
- Grade file length or fan-in alone. A widely imported primitive is healthy, and a long cohesive module can be deep.
- Treat idiomatic framework data loading, such as Server Components, loaders, and Server Actions, as a violation.
- Require barrel files. It asks that internals be unreachable, however that is enforced.
- Audit non-TypeScript code, or make design decisions for the user. The design session proposes; the user decides.
