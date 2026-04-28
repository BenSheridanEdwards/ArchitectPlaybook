---
name: react-audit
description: Audit idiomatic React (hooks correctness, component design, state management, React 18/19 idioms). Static-only by design with optional implementation plan.
trigger: /react-audit
---

# /react-audit

Audit a TypeScript and React project against an opinionated baseline of **idiomatic React** organised in four layers — **hooks correctness**, **component design**, **state management**, **React 18/19 idioms** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

This skill is scoped to React-correctness independent of cost, accessibility, or architecture. Many React concerns live in adjacent audits; the boundary table below is the canonical map.

## How this differs from neighbouring audits

The architect-playbook deliberately keeps audit boundaries tight so a single concern lives in a single skill. Every audit in the playbook touches React in some way; this is where the lines fall:

| Concern | Owner |
| --- | --- |
| Hook correctness (Rules of Hooks, deps arrays, custom hook design) | `/react-audit` |
| Component-shape patterns (function components, forwardRef, displayName, naming) | `/react-audit` |
| State strategy at the React-API level (when to lift, when to derive, form/server/URL/component split) | `/react-audit` |
| React 18+ primitives usage (`useTransition`, `useId`, Suspense, `'use client'`, server actions, `useOptimistic`) | `/react-audit` |
| Memoization for **runtime cost** (perf-driven `useMemo`/`React.memo`/virtualization) | `/performance-audit` |
| Memoization for **correctness** (referential stability that downstream code depends on) | `/react-audit` |
| God components, file size, fan-out as architectural invariants | `/architecture-audit` |
| Single state-management library (Redux vs Zustand vs Jotai chosen once) | `/architecture-audit` |
| jsx-a11y rules and accessibility patterns inside components | `/accessibility-audit` |
| React error boundaries and Suspense + boundary pairing | `/error-handling-audit` |
| ESLint plugin coverage (which React rules are even enabled) | `/linting-audit` |
| Bundle-level concerns: `React.lazy` configuration, dynamic-import wiring | `/bundle-build-audit` |

When a single fix passes multiple audits (for example, enabling `eslint-plugin-react-hooks` satisfies both `/linting-audit` and `/react-audit`; choosing a single global state library satisfies `/architecture-audit` and `/react-audit`), every relevant audit surfaces the same gap so the user sees it once and resolves it once.

## Posture: static-only, no opt-in modes

Hook correctness is best caught by a typed AST analyser, which is exactly what `eslint-plugin-react-hooks` already is — and `/linting-audit` already verifies that plugin is configured. Re-running the plugin from this skill would be redundant. The non-lintable parts of this audit (state-strategy patterns, idiom adoption, hook-design quality) are static AST and pattern detection.

If the user wants a fresh real-run capture of hook violations, `/linting-audit --with-run` is the right tool. This skill stays static.

## Usage

```
/react-audit                                      # default: concise Top 5 + full report saved + ask about plan
/react-audit --learn                              # mid-level engineer teaching mode (detailed explanations + file/line examples)
/react-audit --teach                              # alias for --learn
```

**💡 Pro tip**: Use `/worktree react` to run this in an isolated worktree.

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

This audit deliberately has no numeric threshold flags. Most checks are zero-tolerance or qualitative; class-component presence is a `partial` regardless of count. The canonical path to evolving the baseline itself is `/system-self-improve`.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the codebase shows mixed adherence to a soft check.
- **missing** — a structural prerequisite is absent (no React installed, for example — the skill stops before this state is ever reached, but the status is reserved for future checks).
- **violation** — the audit identified concrete code that breaks the invariant.

Layer 0 is informational only and has no status. Layer 4 reports `skipped: "react-version-below-18"` (and no per-check entries) when React is older than 18.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- React version, resolved from `package.json` and the lockfile (the lockfile is authoritative when it disagrees with `package.json`).
- Framework variant (Next.js App Router or Pages Router, Remix, Vite-React, Create React App, plain React).
- Component counts: function components, class components, total. Class components are explicitly counted because their presence shapes layer 2.
- Custom hook count, plus the largest custom hook by line count (helps identify hooks that should be split).
- Detected form library: react-hook-form, formik, native, or none.
- Detected animation library: framer-motion, react-spring, native CSS animation/transitions, or none.
- Detected design system / component library: Radix UI, shadcn/ui, MUI, Chakra UI, Mantine, Ariakit, custom in-house, or none.
- Counts of new-React primitives in source: `useTransition`, `useDeferredValue`, `useId`, `useOptimistic`, `use()`, `useFormStatus`.
- `'use client'` and `'use server'` directive counts (App Router projects only; recorded as `null` otherwise).
- `forwardRef` usage count and `React.memo` usage count.

### Layer 1 — Hooks correctness

| Check | Expectation | Violation signal |
| --- | --- | --- |
| `eslint-plugin-react-hooks` recommended set enabled | The hooks plugin's recommended rules are enabled in the active ESLint configuration (or the equivalent Biome rules for projects on Biome). Overlap with `/linting-audit`; both surface. | Plugin missing, or recommended set off. |
| No silenced exhaustive-deps without justification | `// eslint-disable-next-line react-hooks/exhaustive-deps` lines have a justification comment on the same or adjacent line. Soft check — reported as `partial`. | Suppressions of `exhaustive-deps` with no surrounding explanation. |
| No empty deps arrays where deps exist | `useEffect(() => { ... }, [])` is not used when the body legitimately closes over rendered values that change. | Effects with empty deps that read state, props, or context that varies across renders. |
| No data fetching in effects when a query library exists | When TanStack Query, SWR, RTK Query, Apollo Client, or framework-native loaders are available, components fetch via the data layer rather than `useEffect` plus `fetch`. (Overlap with `/performance-audit`'s "server state in data layer" check.) | `useEffect` calling `fetch`/`axios` in a project with a query layer installed. |
| No state synchronisation | State is derived from props or other state, not stored in `useState` and re-synchronised by `useEffect`. | Patterns where a `useEffect` does `setX(propX)` to mirror props into state. |
| Status enums over multiple booleans | When a component tracks multi-stage state, a single status enum is used (`'idle' | 'loading' | 'success' | 'error'`) rather than separate `isLoading`, `isError`, `isSuccess` booleans that can drift. Soft check — reported as `partial`. | Three or more boolean state variables that represent a single conceptual lifecycle. |
| `useRef` for mutable non-render state | Mutable values that don't trigger re-renders use `useRef`, not `useState`. | `useState` used to hold a value that is never read in render but mutated frequently. |
| Cleanup functions present for side effects | `useEffect` returns a cleanup for subscriptions, timers, observers, async-cancellation, and event listeners. | Side-effect setup with no matching teardown. |
| Memoization for correctness, not just performance | When a callback or value appears in another hook's dependency array and that downstream hook depends on referential stability for correctness (typically a `useEffect` deps list), the callback or value is wrapped in `useCallback`/`useMemo`. Distinct from `/performance-audit`'s perf-driven memoization: this catches infinite-re-run bugs, not slow renders. | A `useEffect` whose deps include an inline callback created on each render. |
| Custom hook design | Custom hooks have a single responsibility, are named `useFoo`, and return a consistent shape across the codebase (single value for one return, tuple for two, object for three or more). Soft check — reported as `partial` when adherence is mixed. | Custom hooks returning mixed shapes inconsistently, or named without the `use` prefix. |

### Layer 2 — Component design

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Function components only in new code | New components are function components; class components persist only as legacy. The audit reports class components as `partial` rather than `violation` because legacy migrations are slow. The strongest signal is a class component whose file has been modified more recently than the most recent function-component file — recorded explicitly. | Class components present (count recorded); class component newer than the most recent function component (flagged). |
| Components named in PascalCase | Component files and exported component identifiers use PascalCase. | Non-PascalCase component definitions. |
| Hooks named with `use` prefix | Custom hooks export identifiers starting with `use` followed by an uppercase letter. | A function that calls hooks but does not start with `use`. |
| `forwardRef` for ref-receiving components | Components that need to receive `ref` (typically design-system primitives, focusable controls) use `forwardRef`. React 19's `ref` as a prop is also accepted. | A component renders a focusable element that consumers would expect to ref, but does not forward refs and is not React-19 ref-as-prop. |
| `displayName` on `memo`/`forwardRef` wrappers | Components wrapped in `React.memo` or `forwardRef` set `displayName` so the React DevTools tree is readable. | Wrapped components with no `displayName`. |
| Children prop typed | When a component accepts children, the prop is typed `React.ReactNode` (not `any`, not `JSX.Element`, which is too narrow). | `children: any` or `children: JSX.Element` in component prop types. |
| One component or hook per file | Files export one primary component or hook plus its supporting types and helpers, not a grab-bag of unrelated components. Soft check — reported as `partial`. | Files exporting more than three independent components or hooks. |
| Named exports for components | Components are exported as named exports, not default exports. Improves grep-ability and tooling. Soft check — reported as `partial`. | Default-exported components in projects that otherwise use named exports. |

### Layer 3 — State management

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Form state via a form library | Forms with more than five fields, or with non-trivial validation, use a dedicated form library (react-hook-form, formik). Native form state is acceptable for trivial forms. Soft check — reported as `partial`. | Complex forms hand-rolled with a `useState` per field. |
| Server state in the data layer | Server-fetched data lives in TanStack Query, SWR, RTK Query, Apollo Client, or framework-native loaders — not in component-local `useState` populated by `useEffect`+`fetch`. (Overlap with `/performance-audit` and `/architecture-audit`; all three surface.) | Fetched-data state held in component state with no query layer involved. |
| Single global state library | Exactly one of Redux Toolkit, Zustand, Jotai, MobX, or React Context as the global state strategy. (Overlap with `/architecture-audit`; both surface so a single fix passes both.) | Multiple global state libraries in the same application. |
| No state synchronisation between sources of truth | Data is stored in one place and derived elsewhere; the same value isn't held in component state and a global store and a URL parameter at once. | Detected duplicate-state patterns. |
| State at the right level | Local state lives in the component that uses it; lifted state lives at the lowest common ancestor; global state is for genuinely global concerns (auth, theme, feature flags) — not "anything used in two places". Soft check — reported as `partial`. | Global state holding values used by a single subtree, or local state for values used across the application. |
| URL state for shareable or recoverable state | Search queries, filters, sort orders, current page, and selected tabs live in URL parameters when the user would expect them to survive a reload. Soft check — reported as `partial`. | Detected patterns where a refresh would lose UI state that the user expects to keep. |

### Layer 4 — React 18/19 idioms (skipped silently when React below 18)

| Check | Expectation | Violation signal |
| --- | --- | --- |
| `useId` for accessible IDs | Generated IDs used to wire up labels, ARIA attributes, and form controls use `useId` rather than ad-hoc counters or random strings. (Overlap with `/accessibility-audit`.) | Hand-rolled ID generation when React 18+ is available. |
| `useTransition` or `startTransition` for non-urgent updates | Updates that can be deferred (filtering large lists, search-as-you-type, navigation in a Suspense-aware app) use `useTransition` or `startTransition`. Soft check — reported as `partial`. (Overlap with `/performance-audit`.) | Detected hot patterns where transitions would help and are absent. |
| Suspense boundaries for async data | When the framework or library supports Suspense for data fetching (React 19 `use()`, TanStack Query Suspense mode, framework loaders), Suspense boundaries with meaningful fallbacks are used. | Async data fetched without surrounding Suspense in projects that have opted into Suspense-based data fetching. |
| `'use client'` used minimally and deliberately (Next.js App Router only) | Client-component boundaries sit at the leaves of the tree, not at the root. (Overlap with `/architecture-audit`.) Soft check — reported as `partial` when more than half of components are client components. | Excessive `'use client'` fragmentation; or `'use client'` files importing server-only modules. Skipped silently outside App Router. |
| Server Actions used appropriately (Next.js App Router only) | Mutating operations in App Router projects use Server Actions (`'use server'`) when appropriate, rather than client-side `fetch` to a route handler that simply mutates. Soft check — reported as `partial`. | Patterns where Server Actions would be the natural choice but route handlers are used instead. Skipped silently outside App Router. |
| `useOptimistic` for optimistic mutations (React 19 only) | Optimistic UI updates use `useOptimistic` rather than hand-rolled optimistic state. Skipped silently for React 18.x. | Hand-rolled optimistic updates in React 19 codebases. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the audit identifies widely-consumed custom hooks (a hook used by 50 components is more important to get right than one used by 2) and prioritises them in the implementation plan. The audit still runs in full when the graph is absent.
2. **Confirms a TypeScript and React project.** Detects `package.json`, `tsconfig.json`, and `react` in dependencies. If any are absent, the skill stops and tells the user it currently supports TypeScript and React projects only.
3. **Resolves the React version** from `package.json` and the lockfile. Determines whether layer 4 runs (React 18 or newer) or is reported as skipped.
4. **Detects framework, form library, animation library, and design system** for the diagnostic snapshot and for layer-specific dispatch (App Router gates two layer 4 checks).
5. **Counts class components and identifies the most-recently-modified component of each kind** so the implementation plan can flag class components newer than the most recent function component as the strongest migration signal.
6. **Writes Layer 0 — the diagnostic snapshot** to `.architect-audits/react-audit/snapshot.md` and prepends the same content to `findings.md`.
7. **Walks each check in the active layer list**, applying any `--include` and `--exclude` filters. Records a status, evidence, and (where relevant) sample file references per check.
8. **Writes phase 1 outputs** to `.architect-audits/react-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), React version, framework variant, applied filters, layer 4 skipped status when applicable.
9. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the React idiom gaps? (yes/no)"

   On `yes`, writes `.architect-audits/react-audit/implementation-plan.md` describing exactly which hook patterns to fix, which components to refactor, which state-management adjustments to make, and which React 18/19 primitives to adopt — ordered by layer and then by impact (graph centrality applied when available). The plan does not modify any project files.

   On `no`, exits cleanly. 

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "react-audit: no package.json detected. This skill currently supports TypeScript and React projects only."; exit 1; }
test -f tsconfig.json || { echo "react-audit: no tsconfig.json detected. This skill currently supports TypeScript projects only."; exit 1; }
```

Confirm React: `react` in dependencies (directly or via Next.js, Remix, etc.). When absent, stop with a friendly message.

### Step 2 — Resolve React version and framework

Read `package.json` and the lockfile. The lockfile is authoritative when the two disagree. Record the major.minor version. If below 18, layer 4 is skipped.

Resolve framework variant from dependencies and directory layout:

- Next.js with `app/` → App Router.
- Next.js with `pages/` only → Pages Router.
- `@remix-run/*` → Remix.
- `vite` plus `react` → Vite-React.
- `react-scripts` → Create React App.
- `react` only → plain React.

### Step 3 — Detect the React-ecosystem stack

Read `package.json` dependencies for:

- Form library: `react-hook-form`, `formik`. If neither, native.
- Animation library: `framer-motion`, `motion`, `react-spring`, `@react-spring/*`.
- Design system: `@radix-ui/*`, `@chakra-ui/*`, `@mui/*`, `@mantine/*`, `@ariakit/*`. Detect shadcn/ui by the presence of `components/ui` plus its conventional file shape.
- Data layer (already detected by neighbouring audits; record here too for the implementation plan): `@tanstack/react-query`, `swr`, `@reduxjs/toolkit` with `createApi`, `@apollo/client`, framework-native loaders.

### Step 4 — Build the diagnostic snapshot

Compute the items listed in Layer 0. For component counts and the largest-hook calculation, walk `.tsx` and `.ts` source files; identify components by their JSX-returning signature and React inheritance, identify hooks by name and hook-call presence. Use Graphify communities to sample broadly when present; otherwise sweep all source files.

Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 5 — Resolve each check

For each check in the active layer list, walk its detection logic:

- **All required signals indicate the invariant holds →** `present`.
- **Most signals hold; a small number of exceptions →** `partial`. Soft checks default to `partial` when adherence is mixed.
- **The structural prerequisite is absent →** `missing`. (Reserved; in this audit `missing` is uncommon because the skill stops earlier when the prerequisite — React itself — is absent.)
- **Concrete violators exist →** `violation`. Always include sample file references.

Record up to ten representative samples plus a total count per check.

### Step 6 — Write phase 1 outputs

Create `.architect-audits/react-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 7 — Print the concise chat summary and offer phase 2

Print a human-first, scannable summary in the chat. Do not print the full layered findings — those are written to disk in Step 6. The chat output has exactly this shape:

1. **Short header** — audit name, timestamp, and a one-line summary of the codebase state.
2. **Top 5 Highest-Leverage Recommendations** — ordered by architectural principles: test philosophy, maintainability, risk reduction, velocity, long-term health. For fewer than five findings, print what exists. For each recommendation (numbered 1–5):
   - **Title** (one clear line).
   - **Why it matters** (explain the principle in 1–2 sentences).
   - **Real consequences if ignored** (honest downside for the team or project).
   - **Smallest high-leverage fix** (exact next step, effort level, and which files to touch).
   - At the end, add a lettered sub-list of concrete actions if useful (e.g. 2a, 2b) so the user can reply with "2b" or "1 and 3" to trigger implementation.
3. **Bottom line**: `Full detailed audit report (layered findings, snapshot, metadata, implementation plan) → .architect-audits/react-audit/findings.md`

When `--learning` or `--teach` is set, expand each recommendation into mid-level engineer teaching mode:
- For every item, explain as if teaching a mid-level engineer, pointing to specific files and line numbers from the current codebase.
- Use educational language: "Here's why this pattern bites teams in the long run…", "This is the exact mistake I see in most codebases at your stage…", "The fix is small but pays off huge because…".
- Include a short "What you'll learn from fixing this" section for each recommendation.
- Keep the numbered/lettered structure so the user can still reply with "2b" or "1 and 3".
- End with the same bottom-line link to the full report.

After printing, ask the single yes-or-no question: *"Generate an implementation plan for the gaps identified above? (yes/no)"* Do not proceed to phase 2 without an explicit affirmative.

### Step 8 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, React version, framework variant, design system, timestamp, total counts per layer.
2. **Layer 1 — hooks correctness plan**: per finding, the file and line, the offending pattern, and the recommended replacement (status enum snippet, derived-state refactor, cleanup function, memoization-for-correctness wrapping). Widely-consumed hooks (Graphify-prioritised) are addressed first because the blast radius is larger.
3. **Layer 2 — component design plan**: per finding, the rename, the `forwardRef` introduction, the `displayName` addition, or the file split. Class-component migrations are listed with the most-recently-modified ones flagged for migration first; legacy class components in stable code are marked low-priority.
4. **Layer 3 — state management plan**: per finding, the state to lift/lower/derive, the form-library introduction snippet, the URL-state migration. Cross-references `/architecture-audit`'s implementation plan when both flag the same single-state-library gap.
5. **Layer 4 — React 18/19 idiom adoption plan** (when not skipped): adoption snippets for `useId`, `useTransition`, Suspense boundaries, `useOptimistic`. Server Action conversion proposals for App Router projects.
6. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not edit components, install libraries, or refactor state.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:11Z",
  "reactVersion": "18.3.1",
  "framework": "next-app-router",
  "formLibrary": "react-hook-form",
  "animationLibrary": "framer-motion",
  "designSystem": "shadcn",
  "snapshot": {
    "componentCounts": { "function": 218, "class": 6, "total": 224 },
    "customHookCount": 47,
    "largestCustomHook": { "name": "useInboxState", "path": "src/features/inbox/useInboxState.ts", "lines": 184 },
    "newPrimitivesUsage": { "useTransition": 4, "useDeferredValue": 1, "useId": 12, "useOptimistic": 0, "use": 0, "useFormStatus": 0 },
    "useClientCount": 71,
    "useServerCount": 8,
    "forwardRefCount": 23,
    "memoCount": 14,
    "mostRecentlyModifiedFunctionComponent": "src/features/billing/PlanCard.tsx",
    "mostRecentlyModifiedClassComponent": "src/legacy/AdminGrid.tsx"
  },
  "summary": {
    "hooksCorrectness":               { "present": 6, "partial": 2, "missing": 0, "violation": 2 },
    "componentDesign":                { "present": 5, "partial": 2, "missing": 0, "violation": 1 },
    "stateManagement":                { "present": 3, "partial": 2, "missing": 0, "violation": 1 },
    "reactEighteenAndNineteenIdioms": { "present": 4, "partial": 2, "missing": 0, "violation": 0 }
  },
  "checks": [
    {
      "layer": "hooks-correctness",
      "check": "no-state-synchronisation",
      "status": "violation",
      "evidence": [],
      "samples": [
        { "path": "src/features/profile/ProfileForm.tsx", "line": 38, "pattern": "useEffect(() => setLocal(props.value), [props.value])" }
      ],
      "totalCount": 4,
      "expectation": "State is derived from props, not mirrored into useState by useEffect.",
      "gap": "4 components mirror props into local state and re-synchronise via useEffect.",
      "remediation": "Replace local state with direct prop usage; if the component needs a controllable initial value, accept a defaultValue prop and useState with it as the lazy initialiser, with no synchronising effect."
    }
  ]
}
```

When React is below 18, the layer 4 summary becomes `"reactEighteenAndNineteenIdioms": { "skipped": "react-version-below-18" }` and the corresponding entries do not appear in the `checks` array.

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), React version, framework variant, the form/animation/design-system detection, applied filters, and the layer-4-skipped status when applicable.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags are recorded in `metadata.json` so a partial run can be reproduced.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| `no tsconfig.json detected` | JavaScript-only project. | Stop. Inform the user that the skill currently supports TypeScript projects only. |
| React not detected | The project does not depend on `react` directly or via a meta-framework. | Stop. Inform the user that this skill currently supports React projects only. |
| React below 18 | The lockfile resolves React to 17.x or earlier. | Continue. Layer 4 reports `skipped: "react-version-below-18"` in `findings.json`. The implementation plan adds a "consider upgrading to React 18+ to unlock useId, useTransition, Suspense, and (eventually) useOptimistic" recommendation in its preamble. |
| React 18 with no detected framework | A bare React project with a hand-rolled bundler. | Continue. Framework-conditional checks (App Router specifics) are silently skipped. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The implementation plan loses centrality-based prioritisation; widely-consumed hooks are still surfaced via per-file count, with reduced precision. |
| Multiple Reacts in the lockfile | Multiple major versions of React present (typically because a dependency pinned an older React). | Record both versions in the snapshot. The audit operates against the version the application's main entry resolves. The duplicate-React situation is also a `/dependency-audit` concern; both audits surface it. |
| Class components dominate | The codebase has more class components than function components. | The "function components only in new code" check still reports `partial` (class components don't make this `violation`); the diagnostic snapshot records the imbalance and the implementation plan recommends a phased migration starting with most-frequently-modified files. |

## What this skill explicitly does NOT do

- Execute any code or run any test.
- Audit runtime cost (memoization-for-perf, virtualization, render thrashing). Those are owned by `/performance-audit`.
- Audit god components, file size, fan-out, or single-state-library architectural concerns. Those are owned by `/architecture-audit` (state-library overlap is intentionally surfaced in both).
- Audit accessibility patterns inside components. Those are owned by `/accessibility-audit`.
- Audit React error boundaries or Suspense + boundary pairing. Those are owned by `/error-handling-audit`.
- Audit ESLint plugin coverage. That is owned by `/linting-audit` (the `react-hooks` plugin overlap is intentionally surfaced in both).
- Audit `React.lazy` configuration or dynamic-import wiring. Those are owned by `/bundle-build-audit`.
- Install any package or dependency.
- Create, modify, or delete any file outside `.architect-audits/react-audit/`.
- Modify components, hooks, or configuration.
- Open pull requests or commit anything to git.
- Audit non-React frontends. Vue, Svelte, Angular, and Solid are out of scope for v1.
- Replace human review of nuanced React design decisions. The audit catches structural patterns; trade-offs like "should this be a custom hook or a child component" still require judgement.
