---
name: react-audit
description: Audit React correctness for the React 19 and compiler era — effects that should not exist or leak, duplicated and boolean state, server-client boundary leaks, and hooks lint, toolchain, and export facts from a deterministic collector.
disable-model-invocation: true
argument-hint: "[--worktree] [--since=<ref>] [--months=<n>] [--learn|--teach]"
---

# /react-audit

Find the React code that will misbehave in production, and fix it in risk order: effects that copy state or race, values with two sources of truth, and server code or functions leaking across the server-client boundary.

The audit is written for React 19, the React Compiler, and server components. It applies to any repository that depends on React, including React Native and Expo apps, where the server-component checks do not apply. When no `package.json` declares React, the collector marks every check not applicable; record the whole audit with `audit-not-applicable`.

It has two sources:
- a deterministic collector, which reads React versions per package, the framework, React Compiler adoption, the hooks lint rules that apply to component files, `react-scripts`, exhaustive-deps suppressions, forwardRef wrappers, default exports outside framework files, and the client module graph; it also lists effect and state candidates and ranks files by Git churn and import fan-in;
- the judgement of the model, spent on the riskiest components first, within a discovery budget.

Checks gate themselves on facts, not failures. A React 18 package gets "not applicable" for the React 19 checks, a project without server components gets it for the boundary checks, and the stable-dependencies check does not apply when the React Compiler compiles every component. The audit is static and read-only; it never runs the application, ESLint, or the compiler.

[Detection notes](references/detection.md) explain each check, the collector's limits, and the sources verified for this baseline.

## Usage

```
/react-audit                  # audit the current repository
/react-audit --worktree       # run in .worktrees/react-audit on its own branch
/react-audit --since=<ref>    # judge only files changed since <ref> (a provisional, filtered run)
/react-audit --months=12      # widen the Git history window for the risk ranking (default 6)
/react-audit --learn          # teaching mode; --teach is an alias
```

`--since` and `--months` make the run provisional, because they change what the audit measures. The tool checks always cover the whole repository, also in a `--since` run.

## Ownership

Each concern has one owning check (Architecture Decision Record 0004).

| Concern | Owner |
| --- | --- |
| Effects, state inside component trees, custom hooks, React 19 APIs, and the server-client module graph | `/react-audit` |
| Whether the React hooks lint rules apply to component files | `/react-audit` (`react-hooks-plugin-enabled`); `/linting-audit`'s `react-hooks-recommended-configured` duplicates it until that catalog migrates |
| Where server data loads, the client-state strategy, and module-level state ownership | `/architecture-audit` (`server-state-has-one-home`, `single-client-state-strategy`, `single-owner-per-state`) |
| Secrets and private fields reaching the browser | `/security-audit` (`server-only-data-stays-on-server`) |
| Memoization and rendering cost | `/performance-audit` |
| Error boundaries and request error paths | `/error-handling-audit` |

The state and boundary checks name their owners in `relatedChecks` and rate only what those checks do not cover.

## The baseline

Statuses follow the shared taxonomy: `present`, `partial`, `missing`, or `violation`. A check that cannot apply is not applicable, and one you could not evaluate is not evaluated with its reason, never `partial`. Severity follows Architecture Decision Record 0004: correctness bugs users see are high, idiom and style are medium or low. The method column says whether the collector decides the check (`tool`) or you do (`model`).

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

The collector records:
- React per package (declared, resolved from the lockfile, and the version that gates the React 19 checks, which is the lowest a peer range admits);
- frameworks and routers (Next.js App or Pages Router, React Router framework mode, Remix 2, Expo Router, React Native, Gatsby, Vite, Create React App) and whether server components are in use;
- React Compiler adoption per package, its compilation mode, and files that opt out with `'use no memo'`;
- the hooks lint presets and rules that apply to the sampled component files;
- `'use client'` and `'use server'` files, inline Server Functions, misplaced directives, and the client module graph;
- forwardRef and ref-as-prop counts, and default exports inside and outside framework files;
- effect candidates, boolean state groups, state initialised from props, exhaustive-deps suppressions, and non-serialisable prop candidates;
- the risk ranking, and source files too large to read.

### Layer 1 — Toolchain and lint

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Hooks lint rules enforced for component files | medium | tool | eslint-plugin-react-hooks 7's `recommended` (rules-of-hooks, exhaustive-deps, and the compiler rules) applies to every component and hook file. | No hook rule for component files (missing); classic rules without the compiler rules, Biome's hook rules, or rules off for some files (partial). |
| Built with a maintained toolchain | medium | tool | No package builds with Create React App (`react-scripts`), which was deprecated in February 2025. | A package depends on react-scripts. |
| Exhaustive-deps suppressions are justified | medium | tool | Every exhaustive-deps suppression gives its reason after ` -- ` or in an adjacent comment. | Suppressions with no reason (partial). |

### Layer 2 — Effects and custom hooks

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Effects do not derive or mirror state | high | model | Derived values are computed during render; resets use a `key`; event logic and parent notifications run in handlers. | An effect that only sets state from props or state, an effect chain, or an effect that calls a parent callback when state changes. |
| Effects clean up and ignore stale results | high | model | Effects that subscribe, listen, time, or fetch return a cleanup that unsubscribes, clears, aborts, or sets an ignore flag. | A fetch with no abort or ignore flag, or a listener, interval, or subscription with no cleanup. |
| Effect dependencies are honest | medium | model | Effects list what they read; latest-value logic uses `useEffectEvent` (React 19.2) instead of a suppression. | An empty or partial dependency array, often suppressed, over values that change. |
| Effect dependencies are stable without the compiler | medium | model | Without the compiler, objects and functions in effect dependencies are created inside the effect, hoisted, or memoized. Not applicable when the compiler compiles every component. | A dependency created fresh on every render. |
| Custom hooks encapsulate one concern | medium | model | One concern per hook, a consistent return shape, named for what it does. Soft check — mixed adherence is reported as partial. | Hooks mixing concerns, inconsistent shapes, or lifecycle wrappers such as `useMount`. |

### Layer 3 — State

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Each value has one source of truth | high | model | Each value lives in one place in the component tree and is derived elsewhere; props are not copied into state unless named as initial values. | State copied from a prop and kept in sync, or a value held in two state variables, a store, or the URL at once. |
| One status value instead of boolean flags | medium | model | A multi-step process is one status value, or the data layer's or `useActionState`'s state. | Three or more booleans describing one process. |
| State lives at the right level | medium | model | Local where used, shared at the lowest common parent, global only for application-wide concerns; hidden UI that keeps state uses `<Activity>` or stays mounted. Soft check — mixed adherence is reported as partial. | A store or context holding one subtree's state, or state lifted far above its readers. |
| Shareable view state lives in the URL | low | model | Filters, queries, pages, and tabs a user expects to survive a reload live in search parameters. Soft check — mixed adherence is reported as partial. | Such view state held only in `useState`. |

### Layer 4 — Components and server boundaries

The React 19 checks are not applicable below React 19; the boundary checks are not applicable without server components.

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Components take ref as a prop | low | tool | React 19 components accept `ref` as a prop; forwardRef stays only where a peer range supports React 18. | forwardRef wrappers in React 19 code (partial). |
| Components use named exports outside framework files | low | tool | Named exports, except framework-required files (Next.js App and Pages Router files, route modules, Expo Router routes) and lazy-loading targets. | Default-exported components elsewhere (partial). |
| Forms submit through actions | medium | model | Form mutations use actions with `useActionState`, `useFormStatus`, and `useOptimistic`, or a form library built on them. | `onSubmit` handlers with hand-rolled pending, error, or optimistic state. |
| Client modules import no server-only code | high | tool | Nothing reachable from a 'use client' file imports `server-only`, Node built-ins, server SDKs, or reads a private environment variable. | A client module chain that reaches server code (violation). |
| Only serialisable props cross into client components | high | model | Server Components pass plain, field-selected data, JSX, or Server Functions to client components. | A plain function, class instance, or whole database record passed to a client component. |
| 'use client' marks small interactive leaves | medium | model | The directive sits on the first line of files that need state, effects, or browser APIs, near the leaves. | 'use client' on root layouts, pages, or mostly static containers, or a directive React ignores. |

## Judging an effect

This is the core of the audit. For each effect you read:

1. **Name what it synchronises with.** An effect exists to keep a component in step with something outside React: a subscription, a timer, the DOM, a network request. If the answer is "other state" or "a prop", the effect should not exist; compute during render, reset with a `key`, or move the logic into the event handler.
2. **Trace the cleanup.** For a subscription, listener, timer, or observer, find the teardown. For async work, find the abort or the ignore flag that stops an older response from landing after a newer one. Name the sequence that fails, such as "type 'ab', the 'a' response arrives last".
3. **Read the dependencies against the body.** Every prop, state value, or context the effect reads belongs in the array. A suppressed or emptied array is honest only when the omitted value is read by event-like logic, which `useEffectEvent` now expresses.
4. **Check where the value lives.** If the effect copies a value into state, ask which copy is the truth and what happens when they differ.

Classify each candidate as **unneeded effect**, **missing cleanup**, **race**, **stale closure**, **duplicated state**, or **sound**, with one sentence and a `path:line` citation that quotes the line. Keep the look-alikes in the detection notes: synchronising with an external system, a fetch with an ignore flag, and a ref read inside an effect are fine.

## What this skill does

1. Stages a run through the shared audit protocol. The collector decides the tool checks, marks what does not apply, and records the candidates and risk ranking.
2. Reads components in risk order within a discovery budget, rather than walking every file.
3. Judges effects, state, and boundaries with the method above. Every finding cites the lines it rests on.
4. Publishes `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` through the protocol, and summarises the act-on findings.
5. Offers an implementation plan of agent-executable briefs.

## Implementation steps

1. **Begin.** Follow [the run protocol](../audit-protocol/references/run-protocol.md). With `--worktree`, create the worktree as it describes.

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../audit-protocol/scripts/audit_run.py" begin react-audit
   ```

   Pass `--since <ref>` for a diff-scoped run, and `--threshold months=<n>` for `--months`.
2. **Read the evidence the collector staged.** Read the `snapshot` in `.architect-audits/react-audit/.staging/run.json`: `packages`, `compiler`, `riskRanking`, `effects.candidates`, `state`, `exhaustiveDepsSuppressions`, and `directives`. Collector checks marked not applicable stay that way; do not re-record them.
3. **Read within the discovery budget.** Read the five highest-ranked components and hooks, then the first 25 effect candidates in ranked order, then every non-serialisable prop candidate and every module in `serverOnlyReachedFromClient`. Widen by 25 candidates only while more than a third of what you read is a real finding. Record what you sampled with `snapshot --set sampled=<json>`.
4. **Evaluate every model check.**
   - Apply "Judging an effect" and the detection notes.
   - Record each check with `record`, citing `path:line` with quoted fragments. Give every non-present result a `--tier`. Show absence with `files:` or a `search:` count, and prefix free-text observations with `note:`.
   - A candidate flag is a place to look, never evidence on its own; cite the code you read.
   - Use `hypothesis` for suspicions you cannot verify, and `not-evaluated` with a reason when the repository gives you no basis, such as `forms-use-actions` when the React version is unknown.
5. **Judge.** Keep at most five findings `act-on` (`finish` refuses more; demote the rest with `judge`), ranked by severity and then by risk rank. The collector marks lint, suppression, ref, and export findings `consider`. Dismiss with a reason any candidate you judged sound.
6. **Finish.** Run `audit_run.py finish react-audit`, fix anything it rejects, and present the chat summary the protocol defines.
7. **Offer the plan.** Ask: "Generate an implementation plan for the act-on findings? (yes/no)"

## Phase 2: the implementation plan

On yes, write `.architect-audits/react-audit/implementation-plan.md` with the task briefs the protocol defines. Order them: server-client boundary leaks, then effects that race or leak, then duplicated and derived state, then dependency honesty, then toolchain, lint, and idiom. Give each effect fix an acceptance criterion a test can check, such as "typing two queries quickly shows the second query's results".

## Repository Quality Score findings contract

The protocol publishes findings schema `2.0.0`. It writes one `runIdentifier`, `runStartedAt` and `runFinishedAt`, and the `checkCatalogVersion`. It records `applicability`, `evaluationState`, and `evidenceQuality` for every catalog check, and repeats the run identity in `metadata.json`. See `.agents/AUDIT_FINDINGS_CONTRACT.md` in the playbook repository.

## What this skill explicitly does NOT do

- Modify, add, or delete any source, configuration, or test file. The audit writes only under `.architect-audits/react-audit/`, through the protocol.
- Install packages, or run ESLint, the React Compiler, a bundler, or the application. Lint and compiler settings are read from configuration as text.
- Recommend `useMemo`, `useCallback`, or `React.memo` for speed, or penalise their absence; rendering cost belongs to `/performance-audit`, and the compiler memoizes for you.
- Grade naming, `displayName`, one component per file, or function versus class components; linters and TypeScript own those.
- Decide where server data should load or which state library to use; `/architecture-audit` owns both.
- Audit Vue, Svelte, Solid, Angular, or Preact without React.
