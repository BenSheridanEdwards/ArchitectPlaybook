# React audit: detection notes

How to evaluate each check, what the collector reads and where it stops, and
the sources this baseline was verified against. The audit asks one question of
every effect, state variable, and boundary it reads: will this misbehave for a
user? Everything here serves that question.

## Where to read

1. Read the collector's snapshot first:
   - `packages`: React per package. `gatingVersion` is the version that
     decides the React 19 checks: the lockfile's resolved version, or the
     lowest version a range admits, or for a library the lowest version its
     `peerDependencies` range admits.
   - `compiler`: React Compiler adoption per package, with `mode` and
     `everywhere`, and `compilerOptOuts`.
   - `riskRanking`: component and hook files ranked by commits in the history
     window plus the number of files that import them.
   - `effects.candidates`, `state`, `exhaustiveDepsSuppressions`, and
     `directives`, all in risk order.
2. Read the five highest-ranked components and hooks with their callers. A
   racing effect on the busiest screen is worth more than ten on a settings
   page nobody opens.
3. Read the first 25 effect candidates. Widen by 25 only while more than a
   third of what you read is a real finding, up to the 60 the snapshot lists.
4. Read every non-serialisable prop candidate and every client graph chain:
   they are few, and they are high severity.

The knowledge graph, when `graphify-out/graph.json` exists, only helps choose
which components to read first.

## Layer 1: toolchain and lint (tool)

- **Hooks lint rules enforced for component files.** The collector reads
  ESLint configuration as text and never runs ESLint.
  - It checks up to 20 component and hook files, spread across workspace
    packages and taken in risk order. A file uses the nearest
    `eslint.config.*`; without one, the cascade of `.eslintrc*` files and
    `eslintConfig` in `package.json`.
  - In a flat configuration, only objects reachable from the default export
    count, and only those whose `files` and `ignores` match the file.
    `globalIgnores` and ignore-only objects drop a file entirely. Shared
    configurations, `extends`, and an object's own `rules` key apply in
    order, so a later setting wins, and a spread such as `{ ...scoped }`
    copies `files`, `ignores`, and `rules` in JavaScript spread order.
  - Presets it knows, by the installed version:
    - eslint-plugin-react-hooks 7 `recommended` and `recommended-latest`,
      written `reactHooks.configs.flat.recommended`, `"react-hooks/recommended"`
      in a flat `extends`, or `plugin:react-hooks/recommended` in a legacy
      file: the two classic rules and the fourteen compiler rules below.
    - Version 6: `recommended-latest` has the compiler rules; `recommended`
      has only the classic two. Version 5 and earlier: the classic two.
    - eslint-config-next 16 spreads the 7.x `recommended`; 15 and earlier
      extend the classic two. `react-app` and `airbnb/hooks` enable the
      classic two.
    - The retired `eslint-plugin-react-compiler` rule counts for the
      compiler rules.
  - The rules required for present: `rules-of-hooks`, `exhaustive-deps`,
    `config`, `error-boundaries`, `gating`, `globals`, `immutability`,
    `incompatible-library`, `preserve-manual-memoization`, `purity`, `refs`,
    `set-state-in-effect`, `set-state-in-render`, `static-components`,
    `unsupported-syntax`, and `use-memo`. `recommended-latest` also adds
    `void-use-memo`, which is not required.
  - Missing: no hook rule applies to the component files. Partial: a rule is
    off for some file, or the classic rules are on without the compiler
    rules.
  - A shared configuration it cannot read, such as an imported
    `@acme/eslint-config`, a helper call, or a legacy `extends` of an unknown
    package, could set these rules. When the required rules are not all on
    and such configuration exists, the check is not evaluated with the
    unreadable entries in the reason. When they are all on, the result is
    degraded. A plugin version it cannot resolve is read as the latest, and
    the result is degraded.
  - Biome without ESLint: `correctness.useHookAtTopLevel` and
    `correctness.useExhaustiveDependencies`, both recommended, cover the
    classic rules only, so the best result is partial. Oxlint without ESLint
    is not evaluated: check by hand that `react/rules-of-hooks` (a pedantic
    rule, off by default) and `react/exhaustive-deps` are on.
  - `/linting-audit`'s `react-hooks-recommended-configured` checks the same
    plugin. This check owns the concern; that one should retire or point here
    when its catalog migrates.
- **Built with a maintained toolchain.** Violation when any package declares
  `react-scripts` (CRACO and react-app-rewired wrappers are cited with it).
  Remix 2 is recorded in the snapshot, not graded: it continues as React
  Router 7 and later.
- **Exhaustive-deps suppressions are justified.** The collector reads real
  comments only, never strings. It counts `eslint-disable`,
  `eslint-disable-next-line`, and `eslint-disable-line` comments that name
  `react-hooks/exhaustive-deps`, and Biome `useExhaustiveDependencies`
  ignores. A reason is ESLint's ` -- reason` form, Biome's text after the
  colon (not the `<explanation>` placeholder), or a separate comment on the
  same line or the line above. A disable that names no rule is a blanket
  disable, which `/linting-audit` owns. Whether a suppression hides a bug is
  judged under "Effect dependencies are honest".

## Layer 2: effects and custom hooks

### The effect candidates

The collector lists each `useEffect`, `useLayoutEffect`, and
`useInsertionEffect` in files that import React or contain JSX, with its
dependency array and these flags. A flag is a place to look, not a verdict.

| Flag | What the collector saw |
| --- | --- |
| `only-sets-state` | The effect's own statements call only state setters, with no async work, subscription, or browser API. |
| `notifies-parent` | The effect's own statements call an `on…` prop callback. |
| `runs-once-and-sets-state` | An empty dependency array and a setter call. |
| `async-without-cleanup` | `fetch`, `axios`, `await`, `.then`, or a `fetch…`/`load…`/`get…` helper, and no returned cleanup. |
| `async-with-guard-but-no-cleanup` | The same, with an abort signal or flag in the body but no returned cleanup. |
| `subscribes-without-cleanup` | `addEventListener`, `setInterval`, `setTimeout`, `subscribe`, `observe`, an observer or socket, and no returned cleanup. |

A concise arrow body, such as `useEffect(() => store.subscribe(fn), [])`,
returns its value and is treated as having a cleanup.

### The checks

- **Effects do not derive or mirror state** (React's "You Might Not Need an
  Effect"). Violation: an effect that computes a value from props or state
  and stores it, an effect that resets state when a prop changes instead of
  a `key`, a chain of effects that each set state for the next, event logic
  (sending a request, showing a notification) moved into an effect that
  watches state, or a parent notified from an effect. Partial: one or two
  such effects on low-risk code. Keep: synchronising with an external
  system, such as a map widget, a subscription, or document title; reading
  `localStorage` once on mount in a client-only app.
- **Effects clean up and ignore stale results.**
  - A fetch in an effect needs an `AbortController` aborted in the cleanup,
    or an `ignore` flag set in the cleanup and checked before setting state.
    Name the failing sequence: two quick changes, the first response arrives
    last.
  - A listener, interval, timeout that sets state, observer, or socket needs
    its teardown in the returned function.
  - An external store read with an effect plus state should use
    `useSyncExternalStore`.
  - Where the data should load at all (a query layer, a loader, a Server
    Component) is `/architecture-audit`'s `server-state-has-one-home`; this
    check rates the race and the leak, not the location.
  - Violation: a race or leak on a ranked component. Partial: on rarely
    mounted code.
- **Effect dependencies are honest.** Read each suppressed or empty array
  against the body. The fix for logic that must read the latest value without
  re-running the effect, such as logging the current theme when a connection
  opens, is `useEffectEvent` (React 19.2). It is not a way to silence the
  rule: functions it returns must not be listed as dependencies, and only
  event-like logic belongs in it. Before 19.2, the fix is restructuring or a
  ref. Violation: a suppressed array whose effect reads a changing value and
  acts on it. Partial: a suppression that is defensible but unexplained.
- **Effect dependencies are stable without the compiler.** Not applicable
  when the collector found the React Compiler compiling every component (mode
  `infer` or `all`, with no `sources` or `gating` filter and no
  `'use no memo'` files). Otherwise look for objects, arrays, and functions
  created during render and listed as dependencies. The fix is to create them
  inside the effect, hoist them, or wrap them in `useMemo` or `useCallback`;
  react.dev keeps these hooks as the escape hatch for exactly this case. Do
  not recommend memoization for speed.
- **Custom hooks encapsulate one concern.** Partial when hooks mix unrelated
  state, return different shapes for the same kind of value, or wrap
  lifecycles (`useMount`, `useUpdateEffect`) instead of naming the
  synchronisation. Naming hooks with `use` is enforced by rules-of-hooks, so
  it is not graded here.

## Layer 3: state

- **Each value has one source of truth.** Use `state.stateInitialisedFromProps`
  and the `only-sets-state` effects. Violation: a prop copied into state and
  re-synchronised by an effect, two state variables that hold the same
  value, or local state mirroring a store or the URL and drifting from it. A
  prop named `initialX` or `defaultX` that seeds state deliberately is fine.
  `/architecture-audit`'s `single-owner-per-state` owns state written by
  several modules; this check rates duplication inside component trees.
- **One status value instead of boolean flags.** Use
  `state.booleanFlagGroups`: three or more boolean `useState` calls in one
  file. Violation when the booleans describe one process (`isLoading`,
  `isError`, `isSuccess`) and the code can set an impossible combination.
  Booleans for independent toggles are fine. A query library's status or
  `useActionState` counts as one status value.
- **State lives at the right level.** Soft check. Partial when a context or
  store holds state only one subtree reads, or state is lifted far above its
  readers. `<Activity mode="hidden">` (React 19.2) keeps a hidden tab's state
  without lifting it.
- **Shareable view state lives in the URL.** Soft check. Partial when list
  filters, search queries, pagination, or selected tabs live only in
  `useState` on a page a user would bookmark or share.

## Layer 4: components and server boundaries

- **Components take ref as a prop (tool).** Applies to packages whose gating
  version is 19 or later. Partial when any forwardRef wrapper remains there;
  a library whose peer range still admits React 18 is gated at 18, so its
  wrappers are not counted. Not evaluated when no React version resolves.
- **Components use named exports outside framework files (tool).** The
  collector reads `.tsx` and `.jsx` files, and JavaScript files with JSX. It
  exempts:
  - Next.js App Router files (`page`, `layout`, `template`, `default`,
    `loading`, `error`, `global-error`, `not-found`, `forbidden`,
    `unauthorized`, and metadata image and route files) and every Pages
    Router file;
  - React Router framework and Remix 2 route modules: files under the route
    folder, files named in `routes.ts`, `root`, and the entry files;
  - Expo Router routes, Gatsby pages and templates;
  - targets of `React.lazy(() => import(...))` and
    `next/dynamic(() => import(...))`.

  Stories, tests, configuration, and tooling folders at a package root are
  not component files.
- **Forms submit through actions.** Not applicable when every package gates
  below React 19. Partial when forms call `fetch` from `onSubmit` with their
  own `isSubmitting` and error state, or hand-roll optimistic updates, where
  a form action with `useActionState`, `useFormStatus`, or `useOptimistic`
  would hold that state. A form library that already manages submission
  state, such as React Hook Form with a Server Function, is fine. Leave it not
  evaluated when the React version is unknown.
- **Client modules import no server-only code (tool).** Applies with
  server components: a Next.js App Router folder, or Waku, `@vitejs/plugin-rsc`,
  or a `react-server-dom-*` bundler.
  - Client entries are files whose first statement is `'use client'`
    (comments may precede it). The client graph follows static, side-effect,
    and dynamic imports, but not type-only imports, through relative paths,
    tsconfig path aliases, and workspace packages. It stops at `'use server'`
    modules, whose exports a client may import as Server Functions.
  - Server markers: `server-only`, `next/headers`, Node built-ins, database
    clients and other server SDKs, and reads of `process.env` variables
    without a public prefix (`NEXT_PUBLIC_`, `VITE_`, `EXPO_PUBLIC_`,
    `PUBLIC_`, `REACT_APP_`) other than `NODE_ENV` and names a Next.js
    configuration's `env` option inlines into the client bundle.
  - Violation cites the client import and the marker line. An import the
    collector cannot resolve inside the client graph degrades the result.
  - Whether a secret or private field actually reaches the browser is
    `/security-audit`'s `server-only-data-stays-on-server`; this check rates
    the module graph.
- **Only serialisable props cross into client components.** Read
  `directives.nonSerialisablePropCandidates`: props passed from a server file
  to a component imported from a `'use client'` module whose value is an
  inline function, a function declared in the file that is not a Server
  Function, or a `new` instance of a class other than `Date`, `Map`, `Set`, a
  typed array, `FormData`, or `Promise`. Serialisable values are primitives,
  plain objects and arrays, `Date`, `Map`, `Set`, typed arrays, Promises,
  JSX, and Server Functions. Also read what the page passes: a whole record
  from the database passed as a prop ships every field. Violation: a plain
  function or class instance crosses. Partial: an unshaped record crosses.
- **'use client' marks small interactive leaves.** Read
  `directives.useClientExamples`, `clientRootFiles`, and `misplaced`. A
  directive below an import or other code is ignored by React, so the file is
  a Server Component. Violation: a root layout or page marked
  `'use client'`, or a misplaced directive in a file that uses hooks. Partial:
  large, mostly static containers marked client where a small interactive
  child would do. `error` and `global-error` files must be client
  components.

## Severity and judgement

Act on at most five findings; `finish` refuses more, and `judge` demotes the
rest. The collector marks its lint, suppression, ref, and export findings
`consider`, so they do not crowd out races, duplicated state, and boundary
leaks. Rank by severity, then by the risk rank of the code involved. Mark
findings on dormant code `consider`. When most candidates turn out to be
sound, say so, and dismiss them with reasons rather than inflating the list.

## React Native and other renderers

React Native and Expo apps are in scope: effects, state, hooks lint, and the
React 19 checks apply (React Native 0.78 and later ship React 19). The
boundary checks are not applicable, and Expo Router route files are exempt
from named exports. Expo SDK 54 and later enable the React Compiler in the
default template through `experiments.reactCompiler` in `app.json`, which the
collector reads.

## How the collector reads code

- Comments, strings, template literals, regular expressions, and JSX text are
  masked before any code pattern is matched. A `/` after an operator, a
  keyword such as `return`, or the `)` that closes an `if`, `while`, or `for`
  condition starts a regular expression; after a value it is division. When
  the lexer loses its place in a file, it falls back to masking comments
  only, so code is never silently hidden.
- Files over 2 MB are not read. They are listed under `skippedLargeFiles`,
  and the tool checks that scan source are degraded.
- `scripts/`, `tools/`, and `tooling/` folders count as tooling only at the
  repository root or a workspace package's root. Workspace globs from
  `package.json` and `pnpm-workspace.yaml` that are empty, negated, absolute,
  or climb out of the repository with `..` are ignored.
- React Compiler adoption is read from `next.config.*` (`reactCompiler`, top
  level in Next.js 16, under `experimental` in 15), Babel configuration
  (`babel-plugin-react-compiler`), Vite configuration (the Babel plugin, or
  `reactCompilerPreset` with `@vitejs/plugin-react` 6), Expo's `app.json`,
  and other bundler configuration files. A value it cannot read, such as
  `reactCompiler: process.env.COMPILER === "1"`, makes adoption unknown,
  never off; the stable-dependencies check then stays with the model. Mode
  `annotation` or `syntax`, a `sources` or `gating` option, or any
  `'use no memo'` file means the compiler does not cover every component.
- Versions come from `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, and
  `bun.lock` as text, then from the manifest range; pnpm `catalog:` ranges
  are read from `pnpm-workspace.yaml`. `node_modules` is never read.

## Sources

Checked on 2026-10-13.

- React 19 release, `ref` as a prop, actions, `useActionState`,
  `useFormStatus`, `useOptimistic`, and `use`:
  https://react.dev/blog/2024/12/05/react-19
- forwardRef "will be deprecated in a future release":
  https://react.dev/reference/react/forwardRef
- React 19.2, `<Activity>` and `useEffectEvent`:
  https://react.dev/blog/2025/10/01/react-19-2,
  https://react.dev/reference/react/Activity, and
  https://react.dev/reference/react/useEffectEvent
- React 19.3 (current, September 2026):
  https://react.dev/blog/2026/09/09/react-19-3
- You Might Not Need an Effect:
  https://react.dev/learn/you-might-not-need-an-effect
- Fetch cleanup with an ignore flag or abort:
  https://react.dev/learn/synchronizing-with-effects
- eslint-plugin-react-hooks rules and presets (7.1.1 is current; 7.0.0 put
  the compiler rules in `recommended`):
  https://react.dev/reference/eslint-plugin-react-hooks and
  https://github.com/facebook/react/blob/main/packages/eslint-plugin-react-hooks/CHANGELOG.md
- React Compiler 1.0, installation, `compilationMode`, `target`, and
  directives: https://react.dev/blog/2025/10/07/react-compiler-1,
  https://react.dev/learn/react-compiler/installation,
  https://react.dev/reference/react-compiler/compilationMode, and
  https://react.dev/reference/react-compiler/directives
- Memoization under the compiler, kept as an escape hatch for effect
  dependencies: https://react.dev/learn/react-compiler/introduction
- Next.js `reactCompiler` (stable in 16, experimental in 15):
  https://nextjs.org/docs/app/api-reference/config/next-config-js/reactCompiler
- Next.js file conventions and `proxy` (formerly middleware):
  https://nextjs.org/docs/app/api-reference/file-conventions and
  https://nextjs.org/docs/app/api-reference/file-conventions/proxy
- Vite plugin-react 6 and `reactCompilerPreset`:
  https://github.com/vitejs/vite-plugin-react/blob/main/packages/plugin-react/README.md
- Expo React Compiler and SDK 54 default:
  https://docs.expo.dev/guides/react-compiler/ and
  https://expo.dev/changelog/sdk-54
- `'use client'` placement and serialisable props:
  https://react.dev/reference/rsc/use-client; Server Functions:
  https://react.dev/reference/rsc/use-server
- `server-only` in Next.js:
  https://nextjs.org/docs/app/getting-started/server-and-client-components
- Remix merged into React Router 7, and React Router 8:
  https://remix.run/blog/merging-remix-and-react-router and
  https://remix.run/blog/react-router-v8; route modules:
  https://reactrouter.com/start/framework/route-module
- Create React App deprecation (14 February 2025):
  https://react.dev/blog/2025/02/14/sunsetting-create-react-app
- Biome hook rules: https://biomejs.dev/linter/rules/use-exhaustive-dependencies/
  and https://biomejs.dev/linter/rules/use-hook-at-top-level/; Oxlint:
  https://oxc.rs/docs/guide/usage/linter/rules/react/rules-of-hooks.html
- Expo Router default exports:
  https://docs.expo.dev/router/basics/core-concepts/; React 19 in React
  Native 0.78: https://reactnative.dev/blog/2025/02/19/react-native-0.78
