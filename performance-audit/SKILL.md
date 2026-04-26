---
name: performance-audit
description: Audit a TypeScript and React frontend's runtime performance patterns against an opinionated baseline spanning render performance, network and data, assets and media and Core Web Vitals, and main-thread work and measurement. Static-first with optional --with-lighthouse-results enrichment. Optionally generates an implementation plan for the gaps.
trigger: /performance-audit
---

# /performance-audit

Audit a TypeScript and React frontend's **runtime** performance patterns against an opinionated baseline organised in four layers — **render performance**, **network and data**, **assets, media, and Core Web Vitals**, **main-thread work and measurement** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

## How this differs from neighbouring audits

The architect-playbook deliberately keeps audit boundaries tight so a single concern lives in a single skill. The split:

| Concern | Owner |
| --- | --- |
| Bundle size, build time, code splitting at the build-tool level | `/bundle-build-audit` |
| God components, file size, fan-out as architectural invariants | `/architecture-audit` |
| Hook correctness, idiomatic React patterns, ecosystem library usage | `/react-audit` |
| **Runtime cost of rendering, including React-specific levers** (memoization, list virtualization, context churn) | `/performance-audit` |
| **Network and data-fetching patterns at runtime** (waterfalls, stale times, prefetch) | `/performance-audit` |
| **Asset usage at runtime** (LCP priority, lazy loading, layout shift) | `/performance-audit` |
| Asset *optimisation* configuration (formats, hashed filenames, image primitive setup) | `/bundle-build-audit` |
| Static analysis configuration (which performance lint rules are enabled) | `/linting-audit` |

When a single gap is relevant to two audits (for example, the Lighthouse CI configuration check is shared between this skill and `/bundle-build-audit`), both surface it. A single fix passes both.

## Static-first design with optional Lighthouse-results enrichment

This skill is read-only and never runs the application. Two modes:

- **Static (default).** Pattern detection across source files, framework configuration, and the configuration of any detected performance-monitoring tools.
- **Static plus opt-in `--with-lighthouse-results`.** When the flag is passed and a Lighthouse JSON results file exists at the default path (`lighthouse-results.json`) or at `--lighthouse-results-path=<path>`, the skill reads the captured Largest Contentful Paint, Interaction to Next Paint, Cumulative Layout Shift, Total Blocking Time, First Contentful Paint, and overall performance score. They feed the diagnostic snapshot and a small number of run-required checks (LCP-image cross-validation, performance-score budget verification).

The skill **never runs Lighthouse itself**. Spinning up a server and running an end-to-end performance test has significant side effects (build, serve, network I/O); that is the user's job, the responsibility of a separate fix-and-validate skill, or a continuous-integration step. Keeping this audit fully read-only is what makes it safe to run in any working tree at any time.

## Usage

```
/performance-audit                                                  # full two-phase static run
/performance-audit --with-lighthouse-results                        # static plus enrichment from existing Lighthouse JSON
/performance-audit --report-only                                    # phase 1 only: write findings, do not ask about a plan
/performance-audit --plan                                           # skip phase 1 if findings already exist; jump to plan generation
/performance-audit --layer=render-performance                       # restrict the audit to a single layer (repeatable)
/performance-audit --layer=network-and-data
/performance-audit --layer=assets-media-and-core-web-vitals
/performance-audit --layer=main-thread-and-measurement
/performance-audit --include=<check-name>                           # include only the named check (repeatable)
/performance-audit --exclude=<check-name>                           # skip the named check (repeatable)
/performance-audit --threshold-virtualization=100                   # override default 50 items
/performance-audit --lighthouse-results-path=path/to/lighthouse.json  # override default lighthouse-results.json
```

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

The defaults baked into the skill are the recommended baseline. The list-virtualization threshold is tunable via flag; all other checks are zero-tolerance or qualitative. The canonical path to evolving the baseline itself is `/system-self-improve`.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the codebase shows mixed adherence to a soft check.
- **missing** — a structural prerequisite is absent (no performance-monitoring provider installed, for example).
- **violation** — the audit identified concrete code that breaks the invariant.

Layer 0 is informational only and has no status.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Detected meta-framework: Next.js App Router or Pages Router, Remix, Vite-React, Create React App, plain React.
- Detected data-fetching layer: TanStack Query, SWR, RTK Query, Apollo Client, native fetch, custom — or none beyond ad-hoc.
- Detected performance / analytics provider: Vercel Analytics, Vercel Speed Insights, Datadog RUM, Sentry Performance, the `web-vitals` package wired to a custom endpoint — or none.
- Image primitive in use: `next/image`, `@unpic/react`, `astro:assets`, native `<img>`, mixed.
- Font loading strategy detected: Next.js `next/font`, framework-managed, manual `<link rel="preload">`, mixed.
- Virtualization library presence: `react-window`, `react-virtual`, `@tanstack/react-virtual`, `react-virtuoso`, none.
- Service worker / Progressive Web App detected: yes/no, registration source.
- Captured Web Vitals from Lighthouse results when available: LCP, INP, CLS, TBT, FCP, plus the overall performance score.

### Layer 1 — Render performance

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Expensive computations memoized | Sort, filter, derive-from-list, and other O(n) or higher derivations inside component bodies are wrapped in `useMemo` (or moved out of render). | A computation involving a known-expensive method (`.sort`, `.filter`, `.flatMap`, `.reduce`) at the top level of a render function with non-trivial inputs and no `useMemo` wrapping. |
| Stable references for memo'd children's props | Components wrapped in `React.memo` receive object/array/function props that are stable across renders (constructed via `useMemo`/`useCallback` or constants). | An inline object literal or arrow function passed to a `React.memo`-wrapped child as a prop. |
| Stable Context values | Context provider values are wrapped in `useMemo` so consumers don't re-render on every parent render. | `<Context.Provider value={{ a, b }}>` with a fresh object literal each render. |
| Long lists virtualized | Lists rendering more than the threshold (default 50; tunable via `--threshold-virtualization`) of repeating items use a virtualization library. | A `.map()` rendering more items than the threshold without a virtualization wrapper. |
| Stable list keys | List keys are stable, unique, and not the array index when items can be inserted, removed, or reordered. | `key={index}` patterns in lists where the data shape suggests reordering is possible. |
| `React.memo` for pure components in hot paths | Pure components rendered many times per parent render (typically inside lists or hot interaction states) are wrapped in `React.memo`. Soft check — `partial`. Graphify-aware: when the graph is present, "frequently rendered" is computed from inbound render edges. | Frequently-rendered pure components without `React.memo` wrapping. |
| No expensive work in render without memoization | Render bodies don't synchronously perform large transformations (parsing, deep cloning, recursive walks) without `useMemo`. | Detected hot patterns running unconditionally in render bodies. |
| `useState` lazy initialiser for heavy initial values | Initial values that are themselves expensive to compute use the lazy initialiser form (`useState(() => expensiveCompute())`). | `useState(expensiveCompute())` with a non-trivial right-hand side that runs on every render. |

### Layer 2 — Network and data

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Single data-fetching strategy | Exactly one of TanStack Query, SWR, RTK Query, Apollo Client, or framework-native (Next.js loader/server components, Remix loader). Not multiple in the same application. | Two or more strategies present in dependencies and used in source. |
| Stale-time configured | The detected data layer has a non-zero default `staleTime` (or equivalent). The SDK default of zero means every consumer refetches on mount. Soft check — reported as `partial` if some queries override but no global default exists. | Default `staleTime` of 0 in the global query client configuration. |
| Parallel fetches use parallel patterns | Multiple independent fetches in the same render path use `Promise.all`, parallel `useQuery` calls, or Suspense's parallel-by-default behaviour — not sequential `await` chains. | Sequentially awaited fetches that have no data dependency between them. |
| No N+1 fetch patterns | Lists don't fire one fetch per row from inside list items. | A `useQuery` (or equivalent) called inside a list item's render path. |
| Request deduplication available | The detected data layer deduplicates concurrent requests for the same key. All four major libraries provide this by default; flagged when using a custom client without it. | A custom data layer with no deduplication. |
| Prefetching for predictable navigation | Links to known navigation targets prefetch (Next.js `<Link>` default behaviour, TanStack Query's `prefetchQuery`, SWR's prefetch helpers). Soft check — reported as `partial`. | A custom `<a>`-based navigation in apps where the data layer supports prefetching. |
| Streaming and Suspense for slow data (Next.js App Router only) | Long-loading data sections in Next.js App Router are wrapped in Suspense boundaries with meaningful fallbacks. | Slow data fetches in route handlers or server components that block the entire route render. Skipped silently when not in Next.js App Router. |
| Server-rendered data not re-fetched on client | Data already available from a server component, route loader, or initial server render is not re-fetched on the client. | Patterns where server-rendered data is also fetched via a client-side hook. |

### Layer 3 — Assets, media, and Core Web Vitals

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Framework image primitive used | Images use the framework's image component (`next/image`, `@unpic/react`, equivalent). Native `<img>` is reserved for icon-sized SVG and decorative purposes. | Plain `<img>` tags pointing at content imagery in a framework that provides an image primitive. |
| LCP image marked priority | At least one image per landing route is marked as the priority/eager-loaded LCP candidate (`priority` prop in Next.js, `loading="eager"` plus `fetchpriority="high"` in plain HTML). When `--with-lighthouse-results` confirms the actual LCP element, the audit cross-checks; otherwise it heuristically requires a priority hint to exist somewhere on hero/landing routes. | No priority/eager-loaded image on landing routes, or the heuristically-identified hero image is not the priority one. |
| Explicit dimensions on images | Every image has explicit `width` and `height` attributes, or CSS `aspect-ratio`, so the browser can reserve space and avoid Cumulative Layout Shift. | An `<img>` (native or framework) with no dimensions and no aspect-ratio CSS. |
| Lazy loading below-the-fold images | Below-the-fold images use lazy loading (framework default, or `loading="lazy"`). | Forced eager loading on below-the-fold imagery. |
| Fonts use `font-display: swap` (or optional) | Web fonts are configured with `font-display: swap` (or `optional` for tightly-controlled experiences), with critical fonts preloaded. | Fonts loaded without `font-display`, or with `font-display: block` (which causes invisible text periods). |
| Critical CSS handled | The framework handles critical CSS extraction, OR the project explicitly inlines critical CSS for hero/landing routes. Soft check — reported as `partial`. | Neither path is set up. |
| No layout-shifting late content | Content that loads asynchronously (advertisements, embeds, iframes, dynamic recommendations) has reserved space (height or aspect-ratio) so it doesn't push other content when it arrives. | Async-loading content with no reserved dimensions. |
| Video autoplay is muted | `<video autoplay>` is paired with `muted`. (Browsers block unmuted autoplay anyway, but the explicit attribute prevents the playback failure path.) | Autoplay videos without `muted`. |

### Layer 4 — Main-thread work and measurement

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Long computations deferred or workered | Computations that visibly block the main thread (large data parsing, heavy synchronous work) use `useDeferredValue`, `startTransition`, `requestIdleCallback`, or a Web Worker. Soft check — reported as `partial`. | Detected hot patterns running synchronously in event handlers or effects. |
| Scroll/resize/mousemove handlers throttled | High-frequency event handlers (scroll, resize, mousemove, drag) are debounced or throttled. | `addEventListener('scroll', ...)` or React equivalents firing without throttling. |
| Animations use compositor-friendly properties | Animations that change visual state use `transform`, `opacity`, `filter` — not `width`, `height`, `top`, `left`, `margin`. | CSS animations or JS-driven animations that touch layout properties. |
| Web Vitals reporting integrated | The project reports Web Vitals to a backend: the `web-vitals` package wired to an analytics endpoint, framework-provided analytics, Vercel Speed Insights, Datadog RUM, or Sentry Performance. | None detected. |
| Real User Monitoring integrated | A Real User Monitoring provider is configured for production. (Overlaps with Web Vitals reporting; the audit accepts the same providers.) | None detected. |
| Lighthouse CI configured | Lighthouse CI is configured to run in continuous integration with thresholds for performance score and Core Web Vitals. (Overlap with `/bundle-build-audit`'s budget check; both surface the gap so a single fix passes both.) | No Lighthouse CI configuration detected. |
| Performance budgets defined and enforced | Beyond bundle-size budgets (owned by `/bundle-build-audit`), the project enforces budgets on Lighthouse-derived metrics (performance score, LCP, INP, CLS) and continuous integration fails the build when they regress. | No metric budgets defined, or defined but not enforced. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the audit identifies hot render paths (god components rendered on every navigation, central data hooks consumed by many components) and uses centrality to prioritise the implementation plan. Without the graph, the audit operates on per-file pattern detection with reduced precision.
2. **Confirms a TypeScript and React project.** Detects `package.json`, `tsconfig.json`, and `react` in dependencies. If any are absent, the skill stops and tells the user it currently supports TypeScript and React frontend projects only.
3. **Detects framework, data layer, performance provider, and image primitive** for the diagnostic snapshot and for resolving framework-conditional checks.
4. **When `--with-lighthouse-results` is set**, reads the JSON file at the resolved path and extracts LCP, INP, CLS, TBT, FCP, and the overall performance score. If the file is missing or unparseable, the run-dependent enrichment of the LCP-image and performance-budget checks degrades to `partial` with a clear gap; the static analysis still runs.
5. **Writes Layer 0 — the diagnostic snapshot** to `audits/performance-audit/snapshot.md` and prepends the same content to `findings.md`.
6. **Walks each check in the active layer list**, applying any `--include`, `--exclude`, and threshold overrides. Records a status, evidence, and (where relevant) sample file references per check.
7. **Writes phase 1 outputs** to `audits/performance-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), framework, data layer, performance provider, image primitive, font strategy, virtualization library, applied thresholds, applied filters, Lighthouse-results presence.
8. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the performance gaps? (yes/no)"

   On `yes`, writes `audits/performance-audit/implementation-plan.md` describing exactly which components to memoize, which lists to virtualize, which fetch sites to parallelise, which images to mark priority, and which monitoring primitives to wire up — ordered by Core Web Vitals impact (LCP-related and INP-related first, then CLS, then code-quality items). The plan does not modify any project files.

   On `no`, exits cleanly. The user can re-run with `--plan` later to skip phase 1.

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "performance-audit: no package.json detected. This skill currently supports TypeScript and React frontend projects only."; exit 1; }
test -f tsconfig.json || { echo "performance-audit: no tsconfig.json detected. This skill currently supports TypeScript projects only."; exit 1; }
```

Confirm React: `react` in dependencies (directly or via Next.js, Remix, etc.). When absent, stop with a friendly message.

### Step 2 — Detect framework and runtime stack

Read `package.json`. Resolve:

- Framework variant: Next.js App Router/Pages Router, Remix, Vite-React, Create React App, plain React.
- Data-fetching layer: scan for `@tanstack/react-query`, `swr`, `@reduxjs/toolkit` (with `createApi`), `@apollo/client`, native usage of framework loaders.
- Performance provider: scan for `@vercel/analytics`, `@vercel/speed-insights`, `@datadog/browser-rum`, `@sentry/nextjs` (or other Sentry packages with the Performance integration enabled), `web-vitals`.
- Image primitive: `next/image` is detected when `next` is in dependencies and `<Image>` from `next/image` is imported anywhere; `@unpic/react`, `astro:assets`, etc. by import.
- Font strategy: Next.js `next/font` imports, framework-managed font primitives, manual `<link rel="preload">` patterns.
- Virtualization library: scan for `react-window`, `react-virtual`, `@tanstack/react-virtual`, `react-virtuoso`.

### Step 3 — Optionally read Lighthouse results

When `--with-lighthouse-results` is set, resolve the file path (default `lighthouse-results.json`, override `--lighthouse-results-path=`). Parse the JSON and extract the audit results. If the file is missing or unparseable, record the failure and continue with run-dependent checks degrading to `partial`.

### Step 4 — Build the diagnostic snapshot

Compute the items listed in Layer 0. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 5 — Resolve each check

For each check in the active layer list, walk its detection logic. Layer 1 checks rely on AST-aware pattern matching (memoization, list keys, list virtualization); layer 2 reads the data-layer configuration plus call-site shapes; layer 3 reads JSX patterns and image-primitive usage; layer 4 reads source (worker/transition usage, throttling) and continuous-integration configuration (Lighthouse CI presence).

For each finding, record evidence and up to ten sample file references plus a total count.

### Step 6 — Write phase 1 outputs

Create `audits/performance-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 7 — Print the chat summary and offer phase 2

Print the snapshot's headline numbers (framework, data layer, performance provider, image primitive, captured Web Vitals when available) plus a short table mirroring the four layers with counts of present, partial, missing, and violation per layer. Highlight the top three violations by Core Web Vitals impact (LCP first, then INP, then CLS). Then ask the single yes-or-no question. Do not proceed to phase 2 without an explicit affirmative.

### Step 8 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`, **ordered by Core Web Vitals impact**:

1. **Header** — repository name, baseline version, framework, data layer, performance provider, timestamp, total counts per layer.
2. **LCP-impacting fixes** — image primitive adoption, LCP priority hints, critical CSS handling, font preload for above-the-fold text. Per finding, the file or configuration to edit and a snippet.
3. **INP-impacting fixes** — long-task deferral, throttled handlers, virtualization for long lists, memoization for hot render paths (graph-prioritised when Graphify is present).
4. **CLS-impacting fixes** — explicit image dimensions, reserved space for async content, font-display strategy.
5. **Code-quality fixes** — non-CWV-impacting items (single data-fetching strategy, stale-time defaults, prefetch wiring, performance-monitoring integration).
6. **Continuous-integration fixes** — Lighthouse CI configuration snippet, performance-budget enforcement.
7. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not edit source files and it does not install packages.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:16Z",
  "framework": "next-app-router",
  "dataLayer": "tanstack-query",
  "performanceProvider": "vercel-speed-insights",
  "imagePrimitive": "next-image",
  "fontStrategy": "next-font",
  "virtualizationLibrary": "react-virtual",
  "withLighthouseResults": true,
  "lighthouseResultsPath": "lighthouse-results.json",
  "thresholds": { "virtualization": 50 },
  "snapshot": {
    "framework": "next-app-router",
    "dataLayer": "tanstack-query",
    "performanceProvider": "vercel-speed-insights",
    "imagePrimitive": "next-image",
    "virtualizationLibrary": "react-virtual",
    "serviceWorker": false,
    "webVitals": {
      "performanceScore": 78,
      "lcpMs": 2900,
      "inpMs": 240,
      "cls": 0.12,
      "tbtMs": 320,
      "fcpMs": 1450
    }
  },
  "summary": {
    "renderPerformance":             { "present": 4, "partial": 2, "missing": 0, "violation": 2 },
    "networkAndData":                { "present": 5, "partial": 1, "missing": 0, "violation": 2 },
    "assetsMediaAndCoreWebVitals":   { "present": 3, "partial": 2, "missing": 1, "violation": 2 },
    "mainThreadAndMeasurement":      { "present": 3, "partial": 1, "missing": 1, "violation": 2 }
  },
  "checks": [
    {
      "layer": "render-performance",
      "check": "long-lists-virtualized",
      "status": "violation",
      "thresholdApplied": 50,
      "evidence": [],
      "samples": [
        { "path": "src/features/inbox/MessageList.tsx", "line": 42, "renderedItems": "messages.length (typical 200+)" }
      ],
      "totalCount": 3,
      "expectation": "Lists rendering more than 50 repeating items use a virtualization library.",
      "gap": "3 lists detected exceeding the threshold without virtualization.",
      "remediation": "Wrap MessageList's render with @tanstack/react-virtual (already in dependencies). Apply the same pattern to ContactsList and ActivityFeed."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), framework, data layer, performance provider, image primitive, font strategy, virtualization library, applied thresholds, applied filters, and the `withLighthouseResults` flag plus the resolved path.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags, threshold overrides, and the `--with-lighthouse-results` flag (plus the resolved Lighthouse path) are recorded in `metadata.json` so a partial run can be reproduced.
- Lighthouse-derived snapshot fields are timestamp-tagged in metadata; staleness is the user's responsibility to manage by re-running Lighthouse and re-running this audit.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| `no tsconfig.json detected` | JavaScript-only project. | Stop. Inform the user that the skill currently supports TypeScript projects only. |
| React not detected | The project does not depend on `react` directly or through a meta-framework. | Stop. Inform the user that this skill currently supports React frontends only. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The "frequently rendered" determination on the React.memo check falls back to per-file detection; the implementation plan loses centrality-based prioritisation. |
| `--with-lighthouse-results` set but file missing | Lighthouse has not been run, or the path is wrong. | Record the failure in metadata. The LCP-image cross-validation and performance-score budget checks degrade to `partial`. The static analysis still runs. |
| Lighthouse results file is from a different application | Stale artefact from another project. | The audit cannot detect this with confidence. Trust the file. The user is responsible for ensuring Lighthouse results match the project under audit. |
| Multiple data-fetching libraries detected | Mid-migration or accidental dual install. | Record both in the snapshot. The "single data-fetching strategy" check reports `violation`. |
| Multiple performance providers detected | Vendor migration or both server-side and client-side providers in use simultaneously. | Record all detected providers. Treat both for the layer 4 checks. Surface the dual-install as a `partial` if it appears unintentional. |
| List length not statically determinable | The audit cannot confirm whether a `.map()` exceeds the threshold (data is fetched at runtime). | Mark the list-virtualization check as `partial` with a `confidence: "unknown-list-length"` flag, recording the file reference. The user is recommended to virtualize anyway when list sizes can grow. |

## What this skill explicitly does NOT do

- Run the application, start a development server, or run Lighthouse, WebPageTest, or any other live performance tool.
- Audit bundle size, build time, or code-splitting configuration. Those are owned by `/bundle-build-audit`.
- Audit god components, file size, or fan-out as architectural invariants. Those are owned by `/architecture-audit`.
- Audit hook correctness or idiomatic React patterns. Those are owned by `/react-audit`.
- Install any package or dependency.
- Create, modify, or delete any file outside `audits/performance-audit/`.
- Modify components, configuration, or continuous-integration workflows.
- Open pull requests or commit anything to git.
- Audit non-React frontends. Vue, Svelte, Angular, and Solid are out of scope for v1; Vue and Svelte performance patterns share much of this baseline but require their own dispatch logic.
- Replace human profiling. The audit catches structural patterns; identifying the specific bottleneck in a slow page still requires Chrome DevTools, React DevTools Profiler, or a similar tool.
