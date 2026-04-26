---
name: bundle-build-audit
description: Audit build pipeline and bundle output (static-first with optional --with-stats enrichment). Checks configuration, composition, hygiene, and performance with optional implementation plan.
trigger: /bundle-build-audit
---

# /bundle-build-audit

Audit a TypeScript frontend's build pipeline and bundle output against an opinionated baseline organised in four layers — **build configuration**, **bundle composition and size**, **asset and dependency hygiene**, **build performance** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

The default mental model is TypeScript and React. The skill works across Vite, Next.js (App Router and Pages Router), Remix, Create React App, plain Webpack, plain Rollup, and Turbopack. Parcel and direct esbuild app builds are out of scope for v1.

## Static-first design with optional stats enrichment

This skill is read-only and never executes the build. Two modes:

- **Static (default).** Read configuration files (`vite.config.*`, `next.config.*`, `webpack.config.*`, `rollup.config.*`, `tsconfig.json`), `package.json`, lockfiles, `.gitignore`, and continuous-integration workflow files. Bundle-composition and size checks degrade to "configured to do X" — there are no concrete numbers.
- **Static plus opt-in `--with-stats`.** When the flag is passed and a recognisable bundle-stats artefact exists at the expected path (`dist/stats.json`, `.next/analyze/*.html`, `build/bundle-stats.json`, `dist/stats.html` from `rollup-plugin-visualizer`, or `bundle-stats.json` from `webpack-bundle-analyzer`), the skill reads it and uses real numbers for size, composition, and per-chunk checks.

The skill never runs the build itself. Running `npm run build` (or equivalent) is the responsibility of the user or a separate fix-and-validate skill — keeping this audit fully read-only is what makes it safe to run in any working tree at any time.

## Usage

```
/bundle-build-audit                                              # full two-phase static run
/bundle-build-audit --with-stats                                 # static plus enrichment from existing stats artefact
/bundle-build-audit --report-only                                # phase 1 only: write findings, do not ask about a plan
/bundle-build-audit --plan                                       # skip phase 1 if findings already exist; jump to plan generation
/bundle-build-audit --layer=build-configuration                  # restrict the audit to a single layer (repeatable)
/bundle-build-audit --layer=bundle-composition-and-size
/bundle-build-audit --layer=asset-and-dependency-hygiene
/bundle-build-audit --layer=build-performance
/bundle-build-audit --include=<check-name>                       # include only the named check (repeatable)
/bundle-build-audit --exclude=<check-name>                       # skip the named check (repeatable)
/bundle-build-audit --threshold-initial-bundle=300kb             # override default 250kb (gzipped)
/bundle-build-audit --threshold-total-bundle=1.5mb               # override default 1mb (gzipped)
/bundle-build-audit --threshold-build-time=90s                   # override default 60s (clean build)
/bundle-build-audit --threshold-incremental-build-time=15s       # override default 10s
/bundle-build-audit --stats-path=path/to/stats.json              # override the auto-detected stats artefact location
```

Threshold values accept human-friendly units: `kb`, `mb`, `gb` for sizes (interpreted as base-2 kilobytes, etc.); `s`, `ms`, `m` for time. The skill never accepts `--apply`.

The defaults baked into the skill are the recommended baseline for a typical modern React and TypeScript application. Threshold flags exist as an escape hatch for projects with deliberately different sensibilities; the canonical path to evolving the defaults themselves is `/system-self-improve`.

**💡 Pro tip**: Spin this up in its own Git worktree with `/worktree bundle-build` (or just `/worktree` to pick from a list) so you can run multiple audits in true parallel without any conflicts.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions.
- **missing** — a structural prerequisite is absent.
- **violation** — the audit identified concrete configuration or output that breaks the invariant.

Layer 0 is informational only and has no status.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Detected build tool and version (Vite 5.x, Next.js 14 App Router, Webpack 5, Rollup 4, Turbopack).
- Detected meta-framework (Next.js, Remix, Vite-React, Create React App, plain Vite, plain Webpack).
- Entry points and discovered route count when discoverable from the framework's conventions.
- Total bundle size and per-chunk breakdown — only when a stats artefact is available; otherwise "not measured".
- Top 10 largest dependencies in the bundle — only when stats are available.
- Browserslist resolved targets (run `npx browserslist` mentally — the skill reads the configuration and does not execute).
- Source-map mode: none, inline, separate file, or hidden with header.
- Build cache state: enabled, configured, and the location it points at.

### Layer 1 — Build configuration soundness

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Build tool present and pinned | A build tool is installed and its version is pinned in `package.json` (no `^` or `~` on the build tool itself). | Build tool missing, or pinned with a caret or tilde range. |
| Browserslist explicit | A `browserslist` field exists in `package.json`, or a `.browserslistrc` file is present. | No browserslist configuration anywhere. |
| Production mode enforced | The build script invokes the framework's production-build command and the continuous-integration workflow runs it with `NODE_ENV=production`. | `NODE_ENV` not set, or the build script invokes a development command. |
| Source maps configured deliberately | Production builds emit source maps in an explicitly chosen mode (`hidden`, separate file, or fully off). | No source-map configuration whatsoever; the build tool's default is silently in use. |
| Build output ignored from git | The build output directory (`dist/`, `.next/`, `build/`, `out/`) is in `.gitignore`. | Build output committed or not ignored. |
| Production dependencies separated | No development-only packages (build tools, type definitions, test runners, linters, formatters, Storybook) appear in `dependencies`. | Development packages in `dependencies`. |
| Deterministic build | The build script does not depend on environment-specific flags or interactive input. | Scripts containing `--watch`, interactive prompts, non-deterministic timestamps in output filenames. |

### Layer 2 — Bundle composition and size

Defaults in parentheses; every threshold overridable via flags. Checks marked **stats-required** report `partial` (with a "stats artefact not present" gap) when running without `--with-stats`.

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Initial bundle size budget *(stats-required)* | The initial JavaScript payload (entry chunk plus shared chunks loaded on first paint) is under the threshold (default 250 kilobytes gzipped). | Initial payload exceeds the threshold. |
| Total bundle size budget *(stats-required)* | The full client-side JavaScript payload across all chunks is under the threshold (default 1 megabyte gzipped). | Total exceeds the threshold. |
| Route-level code splitting | The build splits per route or per page. | Single-bundle output, or the framework's automatic route-splitting has been turned off. |
| No duplicate dependencies | Each dependency appears exactly once in the bundle; React, React-DOM, and large utilities are not bundled twice. | Multiple copies detected in stats (when available); or multiple incompatible major versions in the lockfile. |
| Large-library hygiene | Large libraries are imported with named imports or per-method paths so tree-shaking can remove what isn't used. | Patterns like `import _ from 'lodash'`, `import moment from 'moment'`, `import * as X from 'large-lib'` in source. |
| Dynamic imports for heavy features | Heavy non-critical features (rich-text editors, charting libraries, code mirrors, video players, PDF renderers) are loaded with `React.lazy` or framework-equivalent dynamic imports. | Heavy libraries imported synchronously at the top level of an entry-reachable module. |
| No development-only code in production | No unguarded `console.log`, no React DevTools shim, no debug-only code paths reachable in production builds. | Such patterns present in source without an `if (process.env.NODE_ENV !== 'production')` guard, or detected in built output when stats are available. |

### Layer 3 — Asset and dependency hygiene

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Image optimisation | Images use modern formats (WebP, AVIF) where the framework supports them. The framework's image primitive is used (`next/image` for Next.js, `<Image>` from `@unpic/react` or `vite-plugin-image-optimizer` for Vite). | Multi-megabyte PNGs or JPEGs in `public/`; Next.js project not using `next/image` for content imagery. |
| Font handling | Fonts are subset, self-hosted (or via a CDN with `font-display: swap`), and preloaded for above-the-fold text. | Loading entire web-font families with no `font-display`; sourcing fonts from a script-blocking external host. |
| CSS extraction | CSS is extracted into separate files in production builds, except for above-the-fold critical CSS. | CSS-in-JS configuration that ships styles inside JS chunks unconditionally without extraction. |
| Static assets cacheable | Build output produces hashed filenames (`main.[hash].js`) so long-cache headers can be set safely. | Filenames without content hashes. |
| No vendored dependencies | No copies of third-party packages live under `src/` or `vendor/`. | Files matching the signature of a known package present in `src/`. |
| `sideEffects` field declared | `package.json` has a `sideEffects` field set to `false` (or an array of files known to have side effects), so tree-shaking can remove unused exports. | Field absent. |
| Bundle analyser available | A bundle-analysis tool is installed and runnable. Recognised: `vite-bundle-visualizer`, `rollup-plugin-visualizer`, `@next/bundle-analyzer`, `webpack-bundle-analyzer`. | None present. |

### Layer 4 — Build performance

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Build cache enabled | The build tool's cache is enabled (Vite's dependency cache, Next.js `.next/cache`, Webpack `cache: { type: 'filesystem' }`, Turbopack defaults). | Cache disabled or configured to a discarded location. |
| TypeScript incremental mode | `tsconfig.json` has `incremental: true`, or the project uses composite project references. | Neither enabled. |
| Continuous-integration cache | The continuous-integration workflow caches the package-manager store and the build cache between runs. | No cache step in `.github/workflows/*.yml` (or equivalent), or the cache key is not derived from the lockfile hash. |
| Bundle-size budget enforced in continuous integration | A bundle-size check runs in continuous integration and fails the build when the budget is exceeded (`size-limit`, `bundlewatch`, `@next/bundle-analyzer` with thresholds, or equivalent). | No such step detected. |
| Build-time budget *(stats-required)* | Reported clean-build time is under the threshold (default 60 seconds), reported incremental rebuild time is under the threshold (default 10 seconds). Soft check — `partial` between threshold and 1.5 times threshold, `violation` above. | Measurements (when available from continuous-integration logs or local cache) exceed the thresholds. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: if `graphify-out/graph.json` exists, the skill uses community structure to suggest cleavage planes for code-splitting in the implementation plan. If absent, the audit still runs in full — the only loss is some intelligence in the remediation suggestions.
2. **Confirms a Node.js project with a recognised build tool.** Detects the build tool from `package.json` dependencies and configuration files. If none of the supported build tools is detected, the skill stops and tells the user.
3. **Detects the meta-framework** for the diagnostic snapshot.
4. **Locates a stats artefact when `--with-stats` is set.** Searches the conventional paths for the detected build tool, plus any path passed via `--stats-path`. Records the path or the absence in the snapshot.
5. **Writes Layer 0 — the diagnostic snapshot** to `audits/bundle-build-audit/snapshot.md` and prepends the same content to `findings.md`.
6. **Walks each check in the active layer list**, applying any `--include`, `--exclude`, and threshold overrides. Stats-required checks emit `partial` with a clear "no stats artefact" gap when running without enrichment.
7. **Writes phase 1 outputs** to `audits/bundle-build-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision hash (when present), build tool, framework, stats artefact path (or `null`), applied thresholds, applied filters.
8. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the bundle and build gaps? (yes/no)"

   On `yes`, writes `audits/bundle-build-audit/implementation-plan.md` describing exactly which configuration entries to add, which dependencies to swap, which dynamic imports to introduce, and which continuous-integration steps to add — ordered by layer and then by severity. The plan does not modify any project files.

   On `no`, exits cleanly. The user can re-run with `--plan` later to skip phase 1.

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "bundle-build-audit: no package.json detected. This skill currently supports Node.js frontend projects only."; exit 1; }
```

Detect the build tool:

- `vite` dependency → Vite.
- `next` dependency → Next.js (with App Router or Pages Router based on directory layout).
- `@remix-run/*` dependency → Remix.
- `react-scripts` dependency → Create React App.
- `webpack` dependency with a `webpack.config.*` → plain Webpack.
- `rollup` dependency with a `rollup.config.*` → plain Rollup.
- `@vercel/turbopack` or Next.js with Turbopack opted in → Turbopack.

If none match, stop and tell the user the skill currently supports the listed tools only.

### Step 2 — Locate the stats artefact (if `--with-stats`)

For each known build tool, check the conventional path:

- Vite with `rollup-plugin-visualizer`: `dist/stats.html` or `dist/stats.json`.
- Next.js with `@next/bundle-analyzer`: `.next/analyze/client.html`, `.next/analyze/server.html`.
- Webpack with `webpack-bundle-analyzer`: `bundle-stats.json` at the project root, or `dist/bundle-stats.json`.
- Rollup with `rollup-plugin-visualizer`: `dist/stats.html`, `dist/stats.json`.

Honour `--stats-path` over the conventional locations.

If `--with-stats` is set but no artefact is found, continue running with stats-required checks degrading to `partial` and add a clear "no stats artefact found at <searched paths>" line to the snapshot.

### Step 3 — Build the diagnostic snapshot

Read configuration files and (when available) the stats artefact. Compute the items listed in Layer 0. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 4 — Resolve each check

For each check in the active layer list, walk its detection logic. Use the same status taxonomy as the other audits (`present`, `partial`, `missing`, `violation`). Stats-required checks running without enrichment record `partial` with the gap "stats artefact not present; static signals indicate <observation>".

### Step 5 — Write phase 1 outputs

Create `audits/bundle-build-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 6 — Print the chat summary and offer phase 2

Print the snapshot's headline numbers (build tool, framework, stats source or "static-only", total bundle size or "not measured") plus a short table mirroring the four layers with counts of present, partial, missing, and violation per layer. Highlight the top three violations. Then ask the single yes-or-no question. Do not proceed to phase 2 without an explicit affirmative.

### Step 7 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, build tool, framework, stats source, timestamp, total counts per layer.
2. **Layer 1 — build configuration plan**: configuration snippets to add or change, with the exact file path.
3. **Layer 2 — bundle composition and size plan**: per violation, the offending pattern, the recommended replacement (named imports, dynamic imports, library swaps), and — when the Graphify graph is present — community-derived split planes for routes or feature folders.
4. **Layer 3 — asset and dependency hygiene plan**: per violation, the asset class, the recommended primitive (framework image component, font-loading approach), and the configuration change.
5. **Layer 4 — build performance plan**: continuous-integration cache step snippets, TypeScript configuration changes, bundle-size budget configuration.
6. **Closing checklist** — a flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not install packages and it does not modify configuration files.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:11Z",
  "buildTool": "vite",
  "framework": "vite-react",
  "statsSource": "dist/stats.json",
  "thresholds": {
    "initialBundleBytesGzipped": 256000,
    "totalBundleBytesGzipped": 1048576,
    "buildTimeSeconds": 60,
    "incrementalBuildTimeSeconds": 10
  },
  "snapshot": {
    "buildTool": { "name": "vite", "version": "5.4.0" },
    "framework": "vite-react",
    "entryPoints": ["src/main.tsx"],
    "totalBundleBytesGzipped": 712000,
    "perChunk": [
      { "name": "main", "bytesGzipped": 184000, "modules": 421 }
    ],
    "topDependencies": [
      { "name": "react-dom", "bytesGzipped": 41000 }
    ],
    "browserslist": ["chrome >= 109", "firefox >= 115", "safari >= 16"],
    "sourceMapMode": "hidden",
    "buildCache": { "enabled": true, "location": "node_modules/.vite" }
  },
  "summary": {
    "buildConfiguration":         { "present": 5, "partial": 1, "missing": 1, "violation": 0 },
    "bundleCompositionAndSize":   { "present": 3, "partial": 2, "missing": 0, "violation": 2 },
    "assetAndDependencyHygiene":  { "present": 4, "partial": 0, "missing": 2, "violation": 1 },
    "buildPerformance":           { "present": 3, "partial": 1, "missing": 1, "violation": 0 }
  },
  "checks": [
    {
      "layer": "bundle-composition-and-size",
      "check": "large-library-hygiene",
      "status": "violation",
      "thresholdApplied": null,
      "evidence": [],
      "samples": [
        { "path": "src/utils/format.ts", "match": "import _ from 'lodash'" }
      ],
      "expectation": "Large libraries are imported with named imports or per-method paths so tree-shaking can remove what isn't used.",
      "gap": "lodash imported as default in 4 files; full library shipped to the client.",
      "remediation": "Replace with `import { debounce } from 'lodash-es'` (or per-method paths) and add lodash-es to dependencies if not already present."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries the skill identity, timestamps, Graphify revision when present, and the configuration of the run.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags, threshold overrides, the `--with-stats` flag, and the resolved `--stats-path` are recorded in `metadata.json` so a partial run can be reproduced.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| No supported build tool detected | The project uses Parcel, esbuild directly, or a custom build script. | Stop. Inform the user that v1 supports Vite, Next.js, Remix, Create React App, Webpack, Rollup, and Turbopack only. |
| `--with-stats` set but no stats artefact found | The user has not run `npm run build` (or equivalent with the bundle analyser enabled) recently. | Continue running. Mark stats-required checks as `partial` with the gap "stats artefact not present at <searched paths>". Recommend running the build analyser before re-running with `--with-stats`. |
| Stats artefact present but unreadable | Corrupt or unrecognised format. | Treat the artefact as missing. Record the parse error in the snapshot. Continue with degraded checks. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in `metadata.json`. The implementation plan loses the community-derived split-plane suggestions; everything else is unaffected. |
| Multiple build tools detected | Both Webpack and Vite configured — possibly in a migration. | Pick the one referenced by the production build script in `package.json`. Mention the secondary tool in the snapshot. |

## What this skill explicitly does NOT do

- Run the build. The audit is fully read-only.
- Install any package or dependency.
- Create, modify, or delete any file outside `audits/bundle-build-audit/`.
- Modify configuration, source files, or continuous-integration workflows.
- Open pull requests or commit anything to git.
- Audit Parcel, raw esbuild, or custom build pipelines. Those are out of scope for v1.
- Measure runtime performance. That is the responsibility of `/performance-audit`. This skill stops at the bytes shipped and the time taken to ship them.
- Make build-tool migration recommendations (Webpack to Vite, Pages Router to App Router). Migration is a project-shaping decision, not an audit finding.
