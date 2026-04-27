---
name: documentation-audit
description: Audit project documentation against an opinionated baseline spanning onboarding, architectural/decision docs, code-level docs, and operational documentation with drift detection. Static-first with optional --with-link-check. Optionally generates an implementation plan for the gaps.
trigger: /documentation-audit
---

# /documentation-audit

Audit a TypeScript project's documentation against an opinionated baseline organised in four layers — **project entry and onboarding**, **architectural and decision documentation**, **code-level documentation**, **operational documentation and drift** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

The default mental model is a TypeScript and React application, but most checks apply equally to any TypeScript project (libraries, services, monorepos). The operational layer adapts to the project shape — library-only projects silently skip the deployment, rollback, and monitoring checks while still running drift detection.

## How this differs from neighbouring audits

| Concern | Owner |
| --- | --- |
| Whether feature folders have a public-API doc comment or `README.md` | `/architecture-audit` (Layer 4 "public API documented") and `/documentation-audit` (Layer 2 "per-feature documentation") — both surface so a single fix passes both |
| Whether tests describe user-visible behaviour | `/testing-audit` (its own concern; not documentation) |
| Whether commit messages follow Conventional Commits | `/quality-gates-audit` |
| Whether the README explains the project and gets a newcomer running | `/documentation-audit` |
| Whether ADRs exist, are templated, and are kept current | `/documentation-audit` |
| Whether public APIs carry TSDoc or JSDoc | `/documentation-audit` |
| Whether runbooks, deployment docs, and rollback procedures exist | `/documentation-audit` |
| Drift between docs and code (README scripts vs `package.json`, doc mtime vs code mtime, dead links) | `/documentation-audit` |

## Static-first design with optional link-check enrichment

This skill is read-only. Two modes:

- **Static (default).** Read every Markdown file in the repository (`.md`, `.mdx`, excluding `node_modules` and ignored paths), `package.json`, source files (for TSDoc/JSDoc and TODO detection), and Git metadata for last-modified timestamps. **Internal and relative links are always checked against the file system regardless of mode** — no network needed for that.
- **Static plus opt-in `--with-link-check`.** Additionally HEAD-request external links found in documentation files to verify they resolve (HTTP 2xx or 3xx). The skill never POSTs, never authenticates, never follows redirects beyond the HEAD response. Timeouts and transient failures are tolerated; persistent failures across the run are reported.

The skill never modifies any documentation, source, or configuration file.

## Usage

```
/documentation-audit                                                  # full two-phase static run
/documentation-audit --with-link-check                                # static plus HEAD-checks of external URLs
/documentation-audit --report-only                                    # phase 1 only: write findings, do not ask about a plan
/documentation-audit --plan                                           # skip phase 1 if findings already exist; jump to plan generation
/documentation-audit --layer=project-entry-and-onboarding             # restrict the audit to a single layer (repeatable)
/documentation-audit --layer=architectural-and-decision-documentation
/documentation-audit --layer=code-level-documentation
/documentation-audit --layer=operational-documentation-and-drift
/documentation-audit --include=<check-name>                           # include only the named check (repeatable)
/documentation-audit --exclude=<check-name>                           # skip the named check (repeatable)
/documentation-audit --threshold-readme-min-lines=50                  # override default 30
/documentation-audit --threshold-adr-staleness-days=365               # override default 180
/documentation-audit --threshold-feature-doc-coverage=70              # override default 50 (percent for partial)
/documentation-audit --threshold-public-api-doc-coverage=80           # override default 60 (percent for present)
/documentation-audit --threshold-todo-staleness-days=180              # override default 90
/documentation-audit --threshold-doc-staleness-days=365               # override default 180
/documentation-audit --target=<path>                                  # operate on this directory instead of cwd (default: cwd)
```

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

When `--target=<path>` is set, the skill operates on that path instead of the current working directory. File reads (including every Markdown file walk), file globbing, regex searches, `git blame` invocations for TODO age, and external-link HEAD requests under `--with-link-check` all run scoped to the target. Findings land at `<target>/audits/documentation-audit/`. The default is the current working directory. This is the building block that lets `/worktree <name>` create a worktree and audit it from a chat opened elsewhere — all in one chat.

**💡 Pro tip**: Use `/worktree documentation` to run this against a Git worktree (creates the worktree and runs the audit in this same chat). Useful for branch isolation and for running multiple audits in true parallel across separate chats.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the codebase shows mixed adherence to a soft check.
- **missing** — a structural prerequisite is absent (no README at all, for example).
- **violation** — the audit identified concrete documentation or drift that breaks the invariant.

Layer 0 is informational only and has no status. Layer 4's operational checks (deployment, rollback, monitoring, feature flags) are silently skipped when the project is library-only or no deployable target is detected.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Total documentation file count (`.md` and `.mdx` files, excluding `node_modules` and gitignored paths).
- Documentation-to-code line ratio (Markdown lines vs `.ts` plus `.tsx` lines).
- README presence and size in lines.
- LICENSE presence and detected SPDX identifier.
- ADR count and the most-recent ADR date.
- TODO/FIXME count, with breakdown by age bucket: less than 30 days, 30–90 days, more than 90 days.
- Detected Storybook (`@storybook/*` in `devDependencies` plus `.storybook/` directory).
- Detected documentation-site framework: Docusaurus, Nextra, VitePress, MkDocs, Sphinx, custom, or none.
- Per-feature README coverage rate: number of feature folders with a `README.md` (or barrel-file documentation comment) divided by total feature folder count.
- External-link total count and, when `--with-link-check` is set, the count of unreachable external links.
- Project shape (informs the operational layer): deployable application, library, monorepo, or hybrid.

### Layer 1 — Project entry and onboarding

| Check | Expectation | Violation signal |
| --- | --- | --- |
| README present | A `README.md` (or `README.mdx`) exists at the repository root. | No README at all. |
| README is substantive | The README is at least the threshold lines (default 30; tunable via `--threshold-readme-min-lines`) and contains identifiable content beyond the project name and a logo. | README below the threshold. |
| README covers onboarding essentials | The README contains sections (or clearly-headed prose) for: what the project is, prerequisites, install, run development server, run tests, build for production. The audit looks for header text and command blocks matching common patterns. Soft check — reported as `partial` when some essentials are present and others are missing. | None of the essentials present. |
| README setup instructions match `package.json` scripts | Commands shown in the README install/run/test sections (`npm run <x>`, `pnpm <x>`, `yarn <x>`, `bun <x>`) reference scripts that actually exist in `package.json`. | A README command references a script that no longer exists. |
| LICENSE present | A `LICENSE` (or `LICENSE.md`/`LICENSE.txt`) file exists at the root with a recognisable license. | No license file. |
| CONTRIBUTING.md present | When the project appears to accept external contributions (signals: presence of `.github/PULL_REQUEST_TEMPLATE.md`, public-repository markers, an explicit notice in the README), a `CONTRIBUTING.md` exists. Soft check — reported as `partial` when absent in a project that appears to accept contributions. | Public-facing project with no contributing guide. |
| Required tool versions documented | One or more of `.nvmrc`, `.tool-versions`, `package.json` `engines.node`, `package.json` `volta`, or `mise.toml` is present. | None present. |
| `.env.example` (or equivalent) present | When the project reads environment variables, a `.env.example` (or `.env.sample`, `.env.template`) documents the variables required to run the project. The example file does not contain real secrets — only placeholder values. | Project reads environment variables but no example file documents them. |

### Layer 2 — Architectural and decision documentation

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Architecture overview present | A top-level architecture document exists at one of the conventional paths: `ARCHITECTURE.md`, `docs/architecture.md`, `docs/architecture/index.md`. | No architecture overview at any conventional path. |
| Architecture Decision Records present | An ADR directory exists (`docs/adr/`, `docs/decisions/`, `adr/`) with at least one ADR. Soft check — reported as `partial` when the directory exists but is empty. | No ADR infrastructure at all. |
| Recent ADR activity | When ADRs are present, at least one was added or modified within the threshold (default 180 days; tunable via `--threshold-adr-staleness-days`). Long stretches with no ADRs in an active codebase suggest important decisions are being made undocumented. Soft check — reported as `partial`. | All ADRs older than the threshold in a project with recent code activity. |
| ADRs follow a recognisable template | ADRs include identifiable sections for Status, Context, Decision, and Consequences (or the project's chosen template — the audit looks for header consistency across files). Soft check. | ADRs without a consistent structure. |
| Per-feature documentation | Each feature folder (under `src/features/`, `src/modules/`, or the equivalent for the detected pattern) has a `README.md` or a documenting comment block at the top of its barrel file. Threshold for `partial` vs `violation` is the per-feature coverage rate (default `partial` ≥ 50%, `violation` < 50%; tunable via `--threshold-feature-doc-coverage`). Overlap with `/architecture-audit`'s "public API documented" check; both surface so a single fix passes both. | Coverage below the violation threshold. |
| Diagrams for non-trivial architecture | Architecture documentation includes at least one diagram (Mermaid block, PlantUML, image asset, or linked external diagram tool). Soft check — reported as `partial` for codebases with multiple top-level domains and no diagram. | Architecture docs contain no diagrams in a structurally-complex project. |

### Layer 3 — Code-level documentation

The audit treats TSDoc and JSDoc as equivalent. TSDoc is a TypeScript-specific superset; the project picks one convention or mixes them, and the audit grades coverage and quality without preferring one over the other.

| Check | Expectation | Violation signal |
| --- | --- | --- |
| TSDoc/JSDoc on exported public APIs | Functions, hooks, components, and types exported from package entry points or public barrels carry a TSDoc/JSDoc block describing what they do. Coverage thresholds (tunable via `--threshold-public-api-doc-coverage`): `present` ≥ 60%, `partial` 30–60%, `violation` < 30%. | Public exports broadly undocumented. |
| Comments explain WHY, not WHAT | Comments add information that the code itself does not convey: a hidden constraint, a workaround for a specific bug, behaviour that would surprise a reader. Comments that restate what the next line obviously does are flagged. Heuristic — reported as `partial` rather than `violation` because the heuristic is imperfect. | High counts of restating-the-code comments. |
| No commented-out code | Code is deleted, not commented out. Git history is the audit log. | Blocks of commented-out code (multiple consecutive comment lines containing valid syntax). |
| TODOs include owner and reference | Every `TODO`/`FIXME` comment includes either an owner identifier (`TODO(@username)`, `TODO(team-frontend)`) or a tracking-system reference (`TODO(JIRA-123)`, `TODO(#456)`). Soft check — reported as `partial` when adherence is mixed. | Bare `TODO`/`FIXME` comments with no owner or reference. |
| TODOs not stale | No TODO is older than the threshold (default 90 days; tunable via `--threshold-todo-staleness-days`). Age is measured by the most recent Git modification of the line. Soft check — reported as `partial`. | TODOs older than the threshold. |
| No empty doc comments | No `/** */` blocks with no content. They suggest documentation was started and abandoned. | Empty TSDoc/JSDoc blocks. |
| Storybook stories for design-system components | When Storybook is detected and a design-system directory is identified (heuristic: `components/ui/`, `packages/ui/`, `src/design-system/`), every component has a story file. Soft check — reported as `partial` based on coverage rate. Skipped silently when no design system is detected. | Design-system components without stories. |

### Layer 4 — Operational documentation and drift

The operational checks (deployment, rollback, monitoring, feature flags) require the project to ship to a deployable target. The audit detects project shape from configuration: a `next.config.*`, `vercel.json`, `netlify.toml`, `Dockerfile`, server entry point, or framework-conventional deployable layout signals a deployable application; a `package.json` with `main`/`module`/`exports` and no application entry signals a library. When the project is library-only, the operational checks are silently skipped. **The drift checks always run, regardless of project shape.**

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Deployment documented | A document describes how the project is deployed: `docs/deploy.md`, `docs/deployment.md`, a deployment section in the README, or framework-conventional deployment notes. Skipped silently when no deployable target is detected. | Deployable project with no deployment documentation. |
| Rollback procedure documented | A document describes how to roll back a bad deployment. Skipped silently when no deployable target is detected. Soft check — reported as `partial` when deployment is documented but rollback is not. | Deployment documented; rollback not documented. |
| Monitoring and alerting documented | Documentation references the monitoring or observability stack (Sentry, Datadog, Grafana, CloudWatch dashboards, on-call rotations). Skipped silently for non-deployed projects. Soft check — reported as `partial`. | No references in documentation. |
| Feature-flag documentation | When a feature-flag library is detected (LaunchDarkly, GrowthBook, Statsig, ConfigCat, Unleash, custom flag service), feature flags are documented (which exist, who owns them, the conditions for ramp-up and removal). Skipped silently when no feature-flag library is detected. Soft check — reported as `partial`. | Feature-flag library used; no flag documentation. |
| README script-drift detection | Commands shown in the README correspond to scripts that exist in `package.json`. (Mirrors Layer 1's check from a drift-detection angle; deliberate, because both audiences benefit.) Always runs. | README references missing or renamed scripts. |
| Documentation freshness | The ratio of documentation files modified within the staleness threshold (default 180 days; tunable via `--threshold-doc-staleness-days`) to the total documentation file count is healthy. Soft check — reported as `partial` when more than half of docs are older than the threshold. Always runs. | Pervasive documentation staleness. |
| Internal links resolve | Markdown links to other files in the repository (`./docs/foo.md`, `../README.md`, `[text](#anchor)`) resolve to existing files and anchors. Always runs, regardless of `--with-link-check`. | Broken internal links or anchors. |
| External links resolve | Markdown links to external URLs return HTTP 2xx or 3xx on a HEAD request. Only checked when `--with-link-check` is set. Reported as `partial` when a small number fail (timeout-tolerant); `violation` when many fail (more than 10% of external links). | Many broken external links. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the audit cross-references feature folders against graph centrality so the per-feature documentation check (and the implementation plan) prioritises the most-consumed features. Without the graph, the audit falls back to per-folder coverage rates with no centrality weighting.
2. **Confirms a Node.js project.** Detects `package.json`. If absent, the skill stops and tells the user it currently supports Node.js projects.
3. **Detects project shape** (deployable application, library, monorepo, hybrid) for the operational-layer dispatch.
4. **Detects observability and feature-flag stacks** (for layer 4 conditional checks): Sentry, Datadog, LaunchDarkly, GrowthBook, etc., from `dependencies`.
5. **When `--with-link-check` is set**, collects every external URL from documentation files and HEAD-requests them with a short timeout. Records reachability status per URL. Internal links and anchors are always checked against the file system.
6. **Walks every documentation file** to collect counts, headings, code blocks, and link references. Cross-references README command blocks against `package.json` scripts.
7. **Walks every source file** to collect TODO/FIXME comments (with Git blame for age), TSDoc/JSDoc coverage on public exports, and commented-out code blocks.
8. **Writes Layer 0 — the diagnostic snapshot** to `audits/documentation-audit/snapshot.md` and prepends the same content to `findings.md`.
9. **Walks each check in the active layer list**, applying any `--include`, `--exclude`, and threshold overrides. Records a status, evidence, and (where relevant) sample file references per check.
10. **Writes phase 1 outputs** to `audits/documentation-audit/`:
    - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
    - `findings.json` — machine-readable.
    - `snapshot.md` — diagnostic snapshot on its own.
    - `metadata.json` — skill version, run timestamp, Graphify revision (when present), project shape, observability stack, feature-flag stack, applied thresholds, applied filters, link-check flag and counts.
11. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

    > "Generate an implementation plan for the documentation gaps? (yes/no)"

    On `yes`, writes `audits/documentation-audit/implementation-plan.md` describing exactly which documents to create, which sections to add to existing docs, which TODOs to address (or close), which links to fix, and which TSDoc/JSDoc blocks to write — ordered by audience: onboarding documentation first (newcomer experience matters most), then architectural and operational, then code-level, then drift cleanup. The plan does not modify any project files.

    On `no`, exits cleanly. The user can re-run with `--plan` later to skip phase 1.

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "documentation-audit: no package.json detected. This skill currently supports Node.js projects only."; exit 1; }
```

### Step 2 — Detect project shape

Inspect the repository for shape signals:

- Deployable application: `next.config.*`, `vercel.json`, `netlify.toml`, `Dockerfile`, `app/` or `pages/` directory, `src/main.tsx` plus a build script, server entry points.
- Library: `package.json` with `main`/`module`/`exports` fields and no application entry; absence of deployment configuration.
- Monorepo: `packages/` directory plus a workspace declaration in `package.json` or `pnpm-workspace.yaml`.
- Hybrid: signals from multiple categories.

Record the detected shape in `metadata.json`. The shape gates layer 4's operational checks.

### Step 3 — Detect observability and feature-flag stacks

Scan `dependencies` for `@sentry/*`, `@datadog/*`, `@grafana/*` (where applicable), `launchdarkly`, `@growthbook/*`, `statsig-js`, `configcat-js`, `unleash-client`. Record matches in `metadata.json`. The detection seeds the feature-flag-documentation check and the monitoring-documentation check.

### Step 4 — Walk documentation files

Enumerate `.md` and `.mdx` files outside `node_modules` and `.gitignore`. For each:

- Total lines.
- Last-modified timestamp from Git (the most recent commit touching the file).
- Heading structure.
- Fenced code blocks (capture commands inside).
- Link references: classify as internal (relative path or anchor) or external (absolute URL).

Special handling for `README.md`: extract command blocks for cross-referencing against `package.json` scripts. Special handling for ADRs: detect the ADR directory and parse each file for the template-section headers.

### Step 5 — Walk source files

Enumerate `.ts` and `.tsx` files. For each:

- Identify exported public-API symbols (functions, hooks, components, types) and whether they carry a TSDoc/JSDoc block.
- Find `TODO`/`FIXME` comments. For each, run `git blame` to get the most recent modification date.
- Find blocks of commented-out code (multiple consecutive comment lines that parse as TypeScript when uncommented).
- Find empty `/** */` blocks.

Use Graphify communities to sample broadly when present; otherwise sweep all source files.

### Step 6 — Optionally HEAD-check external links

When `--with-link-check` is set, collect the unique set of external URLs from step 4 and issue HEAD requests:

- Timeout per URL: 5 seconds.
- Concurrency: at most 10 in flight.
- Acceptable: HTTP 2xx, 3xx (without following redirects).
- Tolerate transient failures: retry once, then mark as unreachable.

Record per-URL status in metadata.

### Step 7 — Build the diagnostic snapshot

Aggregate the collected data into the items listed in Layer 0. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 8 — Resolve each check

For each check in the active layer list, walk its detection logic. Layer 4's operational checks are skipped silently when the detected project shape is library-only with no deployable signal. Threshold-bearing checks compare aggregated values to the configured thresholds.

For each check, record evidence and up to ten representative samples plus a total count.

### Step 9 — Write phase 1 outputs

Create `audits/documentation-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 10 — Print the chat summary and offer phase 2

Print the snapshot's headline numbers (project shape, total docs, README size, ADR count and most-recent date, TODO age breakdown, per-feature README coverage, external-link reachability when applicable) plus a short table mirroring the four layers with counts of present, partial, missing, and violation per layer. When layer 4's operational checks are skipped, the row reflects the skip with a note. Highlight the top three violations across layers 1 (onboarding) and 4 (drift) — both are the highest-leverage fixes for documentation. Then ask the single yes-or-no question. Do not proceed to phase 2 without an explicit affirmative.

### Step 11 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`, ordered by audience:

1. **Header** — repository name, baseline version, project shape, observability stack, feature-flag stack, timestamp, total counts per layer.
2. **Onboarding fixes** (highest priority) — README expansion or rewrite, missing essentials, LICENSE addition, `.env.example` scaffolding, tool-version pin file. Per finding, the file to add or section to write, with a starter template.
3. **Architectural and decision-documentation fixes** — architecture overview scaffold, ADR directory and starter template, per-feature READMEs prioritised by Graphify centrality when present.
4. **Operational and drift fixes** — deployment runbook scaffold, rollback procedure scaffold, monitoring documentation scaffold, feature-flag documentation index. Drift remediation: README script renames, broken-link fixes (internal first, external when `--with-link-check` provided data).
5. **Code-level fixes** — TSDoc blocks for the highest-impact public exports (graph-prioritised), TODO closeouts (oldest first), empty-doc-block removal, commented-out-code deletion.
6. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not write documentation, edit source, or fix links.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:21Z",
  "projectShape": "deployable-application",
  "observabilityStack": ["sentry"],
  "featureFlagStack": ["growthbook"],
  "withLinkCheck": true,
  "thresholds": {
    "readmeMinLines": 30,
    "adrStalenessDays": 180,
    "featureDocCoverage": 50,
    "publicApiDocCoverage": 60,
    "todoStalenessDays": 90,
    "docStalenessDays": 180
  },
  "snapshot": {
    "documentationFileCount": 47,
    "docToCodeLineRatio": 0.18,
    "readme": { "present": true, "lines": 42 },
    "license": { "present": true, "spdx": "MIT" },
    "adrs": { "count": 9, "mostRecentDate": "2026-03-12" },
    "todos": { "total": 31, "lessThan30Days": 8, "thirtyToNinetyDays": 12, "moreThan90Days": 11 },
    "storybookDetected": true,
    "docSiteFramework": "nextra",
    "perFeatureReadmeCoverage": 0.62,
    "externalLinkCount": 84,
    "externalLinkUnreachableCount": 5
  },
  "summary": {
    "projectEntryAndOnboarding":           { "present": 5, "partial": 1, "missing": 1, "violation": 1 },
    "architecturalAndDecisionDocumentation":{ "present": 3, "partial": 2, "missing": 0, "violation": 1 },
    "codeLevelDocumentation":              { "present": 3, "partial": 3, "missing": 0, "violation": 1 },
    "operationalDocumentationAndDrift":    { "present": 4, "partial": 2, "missing": 1, "violation": 1 }
  },
  "checks": [
    {
      "layer": "operational-documentation-and-drift",
      "check": "readme-script-drift-detection",
      "status": "violation",
      "evidence": ["README.md"],
      "samples": [
        { "command": "pnpm dev:legacy", "expectedScript": "dev:legacy", "actualScripts": ["dev", "dev:standalone"] }
      ],
      "totalCount": 2,
      "expectation": "Commands shown in the README reference scripts that exist in package.json.",
      "gap": "2 README commands point at scripts that no longer exist (dev:legacy, test:integration:legacy).",
      "remediation": "Either restore the scripts in package.json (if they are still wanted) or update the README to reference the current script names. Prefer updating the README — script renames usually reflect intentional cleanup."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), the project shape, the observability and feature-flag stacks, applied thresholds, applied filters, the `withLinkCheck` flag, and per-link reachability status when the flag is set.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags, threshold overrides, and the `--with-link-check` flag are recorded in `metadata.json` so a partial run can be reproduced.
- Link-reachability data is timestamp-tagged in metadata; staleness is the user's responsibility to manage by re-running.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| Project shape ambiguous | Mixed signals (a library `exports` field plus a `next.config.*`). | Record the shape as `hybrid` and run all layer 4 checks (operational and drift). The user can override the inferred shape by including or excluding specific layer 4 checks via `--include` and `--exclude`. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The implementation plan loses centrality-based prioritisation; per-feature documentation gaps are still surfaced, ordered by per-folder coverage rate. |
| `--with-link-check` set but the network is restricted | Sandbox or air-gapped environment. | All external links report as unreachable. The check degrades to `partial` with a metadata note explaining the network restriction; the user should run again from a network-permissive environment to get accurate results. |
| ADR directory uses a non-conventional path | Project keeps decision records in `documentation/decisions/` or similar. | The audit recognises `docs/adr/`, `docs/decisions/`, `adr/` by default. Other paths are picked up only when the user includes a top-level `decisions/` link from the README; otherwise the ADR check reports as missing. The implementation plan recommends moving ADRs to a conventional path. |
| TSDoc and JSDoc mixed across the codebase | Mid-migration or inconsistent convention. | Continue. Treat both as documentation. The audit does not flag the inconsistency itself; pick one and migrate at the project's pace. |
| `git blame` unavailable for TODO age | Repository is shallow-cloned, or the file has no Git history. | The TODO-staleness check degrades for affected lines; metadata records which lines have unknown age. Bare-TODO and owner/reference checks still run. |
| `package.json` has no scripts at all | Pre-bootstrap project. | The "README setup instructions match `package.json` scripts" check reports as `missing` rather than `violation`. The implementation plan recommends adding scripts before re-running. |

## What this skill explicitly does NOT do

- Modify any documentation, source, or configuration file.
- Generate documentation. The implementation plan describes what to write; it does not write it.
- Run any code, including `npm run` commands referenced in the README. Drift detection compares text against `package.json`; it does not execute.
- Authenticate to private documentation sites or wiki tools (Confluence, Notion, internal handbooks). The audit covers documentation that lives in the repository.
- Translate or audit translations of documentation.
- Audit the *quality* of writing beyond structural and drift checks. Whether a paragraph is clear is a human judgement call.
- Replace human review of operational documentation. The audit verifies presence; whether the runbook would actually work in a real incident is a tabletop-exercise concern.
- Audit `@typescript-eslint` documentation rules (`require-jsdoc`, `valid-jsdoc`). That is owned by `/linting-audit`.
- Audit non-Markdown documentation formats (ReStructuredText, AsciiDoc, plain text). Markdown and MDX only in v1.
