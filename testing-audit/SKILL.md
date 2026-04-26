---
name: testing-audit
description: Audit a TypeScript and React project's tests against an opinionated baseline rooted in the Testing Library query priority hierarchy and well-known React Testing Library pitfalls, spanning test runner and tooling, query priority and selector hygiene, interaction and async patterns, and test design and coverage. Static-first with optional --with-run enrichment. Optionally generates an implementation plan for the gaps.
trigger: /testing-audit
---

# /testing-audit

Audit a TypeScript and React project's tests against an opinionated baseline organised in four layers — **test runner and tooling**, **query priority and selector hygiene**, **interaction and async patterns**, **test design and coverage** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

The default mental model is React component tests written with `@testing-library/react`, plus end-to-end tests in Playwright or Cypress. Vitest and Jest are both supported as the test runner. Mocha and other runners are out of scope for v1.

## How this differs from neighbouring audits

| Concern | Owner |
| --- | --- |
| Whether tests *run* at every lifecycle stage (pre-commit, pre-push, continuous integration) | `/quality-gates-audit` |
| Whether `eslint-plugin-testing-library` and `eslint-plugin-jest-dom` are *configured* | `/linting-audit` |
| Whether tests *catch errors well* (error boundaries, async error paths) | `/error-handling-audit` |
| **Whether the tests themselves are well-formed**: query priority, async patterns, design, coverage | `/testing-audit` |
| End-to-end accessibility scans (axe in Playwright/Cypress) | `/accessibility-audit` |
| End-to-end performance measurement (Lighthouse CI) | `/performance-audit` |

When a single fix passes multiple audits (for example, configuring `eslint-plugin-testing-library` satisfies both `/linting-audit` and `/testing-audit`), every relevant audit surfaces the same gap so the user sees it once and resolves it once.

## Testing philosophy

This audit is opinionated. Three principles set the tone for everything below:

1. **Test behaviour, not implementation.** Assertions describe what the user perceives — what they see, click, type, hear, and read. Tests do not assert internal state shape, prop names, or specific function call sequences except where those *are* the public behaviour. The implementation plan's top-priority section addresses behaviour-vs-implementation drift before any other layer-4 work.
2. **Snapshots are a smell.** They almost always couple tests to implementation, churn on every harmless refactor, and rarely catch real regressions. Small, intentional, named snapshots are tolerable; large or whole-component snapshots are reported as `violation`. The skill's stated position is that snapshots are the exception, never the default.
3. **Assert against semantic tokens, not utility classes.** `expect(button).toHaveClass('bg-primary')` is resilient to design-system updates. `expect(button).toHaveClass('bg-gray-100')` breaks the moment the theme changes — and worse, it's testing what colour the button is rather than what the button is. Better still: assert on role, label, or text and skip the class assertion entirely.

These principles map directly to checks in Layer 4. They are not aspirations; they are how the audit grades.

## The query priority ladder

Layer 2's checks grade tests against this canonical Testing Library priority. It is reproduced here verbatim so the audit's stance is unmistakable.

**Priority 1 — Accessible to everyone (preferred):**

1. `getByRole` — the most reliable; queries elements exposed in the accessibility tree. Use with the `name` option: `getByRole('button', { name: /submit/i })`.
2. `getByLabelText` — the right tool for form fields. Mirrors how users navigate forms.
3. `getByPlaceholderText` — when a label isn't available. A placeholder is not a substitute for a label.
4. `getByText` — for non-interactive elements; how users find content outside forms.
5. `getByDisplayValue` — useful for navigating pages with pre-filled form values.

**Priority 2 — Semantic queries (variable user experience):**

1. `getByAltText` — for elements supporting `alt` (img, area, input, custom elements).
2. `getByTitle` — least reliable in this tier; the `title` attribute is not consistently read by screen readers and is not visible by default for sighted users.

**Priority 3 — Test IDs (last resort):**

1. `getByTestId` — the user cannot see or hear these. Use only when semantic matching is not feasible.

A healthy test suite sits heavily in Priority 1. The audit measures the distribution and flags codebases that lean on Priority 3 or fall back to `container.querySelector`.

## Static-first design with optional run enrichment

This skill is read-only. Two modes:

- **Static (default).** Read configuration files, source files, and test files. Pattern-detect query usage, async patterns, mocking shapes, structural test design, and styling-assertion hygiene.
- **Static plus opt-in `--with-run`.** Invoke the detected test runner in coverage mode (`vitest run --coverage --reporter=json`, `jest --coverage --json`) and parse the output. The coverage data feeds the diagnostic snapshot and enriches a small number of run-required checks (real coverage threshold verification, real test-count breakdown).

The skill **never modifies any test, configuration, or source file**, and **never runs Playwright or Cypress end-to-end suites in `--with-run`**. End-to-end runs serve real browsers and have side effects; that is a separate concern and the user's call.

## Usage

```
/testing-audit                                                   # full two-phase static run
/testing-audit --with-run                                        # static plus enrichment from Vitest/Jest coverage run
/testing-audit --report-only                                     # phase 1 only: write findings, do not ask about a plan
/testing-audit --plan                                            # skip phase 1 if findings already exist; jump to plan generation
/testing-audit --layer=test-runner-and-tooling                   # restrict the audit to a single layer (repeatable)
/testing-audit --layer=query-priority-and-selector-hygiene
/testing-audit --layer=interaction-and-async-patterns
/testing-audit --layer=test-design-and-coverage
/testing-audit --include=<check-name>                            # include only the named check (repeatable)
/testing-audit --exclude=<check-name>                            # skip the named check (repeatable)
/testing-audit --threshold-priority-one-ratio=80                 # override default 70 (percent)
/testing-audit --threshold-testid-ratio=5                        # override default 10 (percent, ceiling)
/testing-audit --threshold-by-role-ratio=60                      # override default 50 (percent of Priority 1)
/testing-audit --threshold-user-event-ratio=90                   # override default 80 (percent)
/testing-audit --threshold-snapshot-lines=50                     # override default 100 (lines, ceiling for partial)
```

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the codebase shows mixed adherence to a soft check.
- **missing** — a structural prerequisite is absent (no test runner installed, for example).
- **violation** — the audit identified concrete code that breaks the invariant.

Layer 0 is informational only and has no status.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Detected test runner: Vitest (with version), Jest (with version), or none.
- Detected component testing library: `@testing-library/react`, `@testing-library/preact`, none.
- Detected end-to-end framework: Playwright, Cypress, none.
- Test file count and total test count (test count requires `--with-run`).
- **Query usage distribution** across the priority ladder: counts and percentages per tier, with `getByRole` broken out specifically.
- `userEvent` vs `fireEvent` usage ratio.
- `getByTestId` and `container.querySelector` usage counts and the top files for each.
- Coverage data when `--with-run`: line, statement, branch, and function coverage.
- Flaky-pattern signal counts: fixed `setTimeout`/`setInterval` waits in tests, ordering-dependent test patterns, shared mutable test state.
- Snapshot test count and average snapshot size in lines.
- Components without any test file mapping to them (graph-aware when Graphify is present; falls back to per-folder heuristics otherwise).

### Layer 1 — Test runner and tooling

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Single test runner installed | Exactly one of Vitest or Jest in `devDependencies`. | Both present, with no clear migration. |
| `@testing-library/react` installed | When React is detected, the React Testing Library is in `devDependencies`. | Missing in a React project that ships tests. |
| `@testing-library/user-event` installed | Available for interaction simulation. The interaction-and-async layer prefers `userEvent` over `fireEvent`. | Missing. |
| `@testing-library/jest-dom` installed | Provides DOM-aware matchers (`toBeInTheDocument`, `toBeDisabled`, `toHaveClass`) so assertions can describe user-visible state rather than DOM-property internals. | Missing. |
| `eslint-plugin-testing-library` configured | The plugin is installed and enabled. (Overlap with `/linting-audit`; both surface so a single fix passes both.) | Plugin missing or not enabled. |
| `eslint-plugin-jest-dom` configured | The plugin is installed and enabled. (Overlap with `/linting-audit`.) | Plugin missing or not enabled. |
| jest-dom matchers loaded in setup file | The runner's setup file imports `@testing-library/jest-dom`. | No setup file, or setup file does not import jest-dom. |
| Coverage tool configured with thresholds | The test runner has a coverage configuration with explicit thresholds (line, statement, branch, function). | Coverage configured but thresholds absent, or no coverage configuration at all. |
| End-to-end framework present | Playwright or Cypress in `devDependencies`. Soft check — reported as `partial` for projects that legitimately may not need end-to-end tests. | Neither present in a user-facing application. |

### Layer 2 — Query priority and selector hygiene

This layer encodes the priority ladder and the most common selector-related pitfalls.

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Query distribution favours Priority 1 | At least the threshold percentage (default 70%; tunable via `--threshold-priority-one-ratio`) of all `*By*` queries are Priority 1 queries. | Distribution below the threshold. |
| `getByTestId` usage is rare | At most the threshold percentage (default 10%; tunable via `--threshold-testid-ratio`) of all queries use `getByTestId`. | Distribution above the threshold. |
| No `container.querySelector` for finding elements | Tests use Testing Library queries, not `container.querySelector` or `document.querySelector`. | `querySelector` calls in tests for element lookup. |
| Queries via `screen`, not destructured | Queries come from `screen.getBy*`, not destructured from the `render` return value. `screen` works everywhere and keeps debugging tools (`screen.debug()`) consistent. | `const { getByRole } = render(...)` patterns. |
| `render` return named `view`, not `wrapper` | When the render return is captured, it is named `view` (or destructured for what's needed). The render return is not wrapping anything; the `wrapper` name is a holdover from older testing libraries. Soft check — reported as `partial`. | `const wrapper = render(...)`. |
| `*ByRole` is the dominant Priority 1 query | Of the Priority 1 queries, at least the threshold percentage (default 50%; tunable via `--threshold-by-role-ratio`) are `getByRole` (with the `name` option). Soft check — reported as `partial`. | Priority 1 use without meaningful `getByRole` adoption. |
| No redundant ARIA roles in test assertions | Tests do not assert on `role` attributes that are already implicit (`<button role="button">`). The principle is the same one `/accessibility-audit` applies to source: do not pile on accessibility attributes that the semantic element already provides. | Test assertions matching `[role="button"]` on a `<button>`. |

### Layer 3 — Interaction and async patterns

This layer encodes the well-known pitfalls in interaction style, async handling, and `waitFor` discipline.

| Check | Expectation | Violation signal |
| --- | --- | --- |
| `userEvent` preferred over `fireEvent` | At least the threshold percentage (default 80%; tunable via `--threshold-user-event-ratio`) of interaction calls use `userEvent` rather than `fireEvent`. `userEvent` simulates the full sequence of events a real user produces (focus, keydown, input, change), where `fireEvent` fires only one. | Ratio below the threshold. |
| `find*` used for elements not yet present | When waiting for an element to appear, tests use `findBy*`, not `waitFor(() => getBy*())`. `findBy*` already retries until a timeout and produces clearer error messages. | `waitFor` callbacks containing only a `getBy*` lookup. |
| `query*` only for absence assertions | `queryBy*` is used only with `not.toBeInTheDocument()` or analogous absence checks. `queryBy*` returns `null` instead of throwing, which is the only behaviour that makes "is this element absent?" assertable; using it for presence assertions silently skips the check. | `queryBy*` used in a positive-presence assertion. |
| `waitFor` callback contains a single assertion | Each `waitFor` call wraps exactly one `expect`. With several assertions in one callback, the first failure causes a re-run of all of them, slowing the suite and obscuring which one actually failed. | `waitFor` callbacks with multiple `expect` calls. |
| `waitFor` callback is not empty | `waitFor(() => {})` followed by an assertion outside is wrong; the assertion belongs inside the callback so `waitFor` knows what it is waiting for. | Empty `waitFor` callbacks. |
| No side effects in `waitFor` | The `waitFor` callback contains only assertions — no `fireEvent`, `userEvent`, or other state mutation. `waitFor` re-runs its callback until it succeeds, so any side effect inside fires repeatedly. | `fireEvent` or `userEvent` calls inside `waitFor`. |
| No unnecessary `act` wrapping | `render` and `fireEvent` calls are not wrapped in `act(...)`; both are already wrapped internally. Hand-rolled `act` only adds noise (and sometimes silences warnings that should have been visible). | `act(() => { render(...) })` or `act(() => { fireEvent.click(...) })` patterns. |
| No manual `cleanup()` calls | Tests do not import or call `cleanup` from `@testing-library/react`. Modern test runners auto-cleanup after each test. | `cleanup` imported or called. |
| Assertions are explicit | Tests do not rely on `getBy*` throwing as the assertion; they wrap with `expect(...).toBeInTheDocument()` (or analogous). The intent of the test should be readable at a glance. | `getBy*` calls appearing on a line by themselves with no `expect`. |

### Layer 4 — Test design and coverage

This layer encodes the testing philosophy stated above.

| Check | Expectation | Violation signal |
| --- | --- | --- |
| Tests describe user behaviour | Test names describe the user-visible behaviour (`it('disables submit while saving', ...)`), not the implementation (`it('calls saveMutation when isLoading is true', ...)`). Heuristic detection but a first-class principle of this audit; the implementation plan addresses drift here before any other layer-4 work. Soft check — reported as `partial` when adherence is mixed. | Test names that describe internal state changes rather than user-observable outcomes. |
| No assertions against hard-coded utility classes | Class assertions use semantic theme tokens (`bg-primary`, `text-brand`, `border-destructive`) — never raw utility classes (`bg-gray-100`, `text-slate-500`). Better still, assert on role, label, or text and skip the class assertion entirely. **Reported as `violation` when raw utility classes appear in `toHaveClass` assertions.** | `expect(...).toHaveClass('bg-gray-100')` (or any utility-class form: numeric Tailwind colour scales, raw spacing utilities like `p-4`, raw layout utilities like `flex`). The audit recognises a token via heuristic: a token has no numeric suffix and matches `[bg|text|border|ring|fill|stroke]-[a-z]+` but not the Tailwind palette names. |
| Snapshots are bounded and intentional | Snapshot tests cover small, intentional, named outputs. **Reported as `violation` when a snapshot exceeds the threshold lines (default 100; tunable via `--threshold-snapshot-lines`) OR when the snapshot is obviously implementation-coupled (whole-component output, internal-component-tree shape).** Small, focused, named snapshots are reported as `partial` — the skill's stated position is that snapshots are a smell, but bounded ones can be defensible. | Auto-snapshots of full component output, snapshots larger than the threshold, or many snapshot tests in a single file (suggesting snapshot-as-default rather than considered choice). |
| Coverage thresholds enforced in continuous integration | The continuous-integration test step is configured to fail when coverage drops below the configured threshold. Soft check — reported as `partial` if thresholds exist but are not enforced. | Coverage configured but continuous integration does not fail on regression. |
| Critical-path end-to-end coverage | The project has at least one Playwright or Cypress spec covering the primary user journey (sign-in, primary action, sign-out, or the equivalent). Soft check. | No end-to-end specs at all in a user-facing application. |
| No flaky-pattern signals | Tests do not use fixed `setTimeout`/`setInterval` waits, do not depend on test ordering, and do not share mutable state across tests. | Detected fixed waits or ordering-dependent patterns. |
| Mocking at module boundaries | Mocks are placed at module or network boundaries (`vi.mock('axios', ...)`, MSW handlers), not inside internal implementations of the system under test. Soft check — reported as `partial`. | Mocks of internal helpers used by the component under test, indicating implementation-coupled tests. |
| Components have at least minimal coverage | Every component identified by Graphify (when present) as user-facing has at least one test file referencing it. Without the graph, the check falls back to a per-folder heuristic. Soft check — reported as `partial`. | Components without any associated test. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the audit cross-references graph nodes against test-file imports to identify components with no test, and ranks coverage gaps by graph centrality. Without the graph, the check falls back to per-folder heuristics with reduced precision.
2. **Confirms a TypeScript and React project.** Detects `package.json`, `tsconfig.json`, and `react` in dependencies. If any are absent, the skill stops and tells the user it currently supports TypeScript and React projects only.
3. **Detects the test runner, component testing library, and end-to-end framework** for the diagnostic snapshot and for layer-1 and layer-4 checks.
4. **When `--with-run` is set**, invokes the detected unit-test runner in coverage mode (`vitest run --coverage --reporter=json` or `jest --coverage --json`), captures the JSON output, and folds it into the snapshot. Never runs Playwright or Cypress.
5. **Walks every test file** to compute the diagnostic distributions (query priority breakdown, `userEvent`/`fireEvent` ratio, `getByTestId` count, `container.querySelector` count, snapshot count and sizes, flaky-pattern signal counts).
6. **Writes Layer 0 — the diagnostic snapshot** to `audits/testing-audit/snapshot.md` and prepends the same content to `findings.md`.
7. **Walks each check in the active layer list**, applying any `--include`, `--exclude`, and threshold overrides. Records a status, evidence, and (where relevant) sample file references per check.
8. **Writes phase 1 outputs** to `audits/testing-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), test runner and version, end-to-end framework, applied thresholds, applied filters, run-mode flag.
9. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the testing gaps? (yes/no)"

   On `yes`, writes `audits/testing-audit/implementation-plan.md` describing the rewrites, refactors, and configuration changes needed — ordered by **testing philosophy impact** (behaviour-vs-implementation drift first, snapshot smells next, utility-class assertions next), then by layer. The plan does not modify any project files.

   On `no`, exits cleanly. The user can re-run with `--plan` later to skip phase 1.

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "testing-audit: no package.json detected. This skill currently supports TypeScript and React projects only."; exit 1; }
test -f tsconfig.json || { echo "testing-audit: no tsconfig.json detected. This skill currently supports TypeScript projects only."; exit 1; }
```

Confirm React: `react` in dependencies (directly or via Next.js, Remix, etc.). When absent, stop with a friendly message.

### Step 2 — Detect the test runner and surrounding tools

Read `package.json` `devDependencies`:

- `vitest` → Vitest. Resolve version from the lockfile.
- `jest` → Jest. Resolve version from the lockfile.
- Both → record both, run the audit against whichever has a configuration file. The "single test runner" check reports `violation`.
- Neither → stop with a friendly message recommending Vitest or Jest before running this audit.

Detect `@testing-library/react`, `@testing-library/user-event`, `@testing-library/jest-dom`, `eslint-plugin-testing-library`, `eslint-plugin-jest-dom`, `playwright`, `@playwright/test`, `cypress`.

### Step 3 — Optionally run the test runner

When `--with-run` is set, invoke the detected runner:

- Vitest: `npx vitest run --coverage --reporter=json` (capture stdout, parse JSON).
- Jest: `npx jest --coverage --json` (capture stdout, parse JSON).

If the runner fails to start (binary missing, configuration invalid), record the failure and continue. Run-dependent enrichment of the diagnostic snapshot and a small number of coverage-related checks degrades to `partial`.

### Step 4 — Walk the test files

Enumerate test files. Detection patterns:

- File names matching `*.test.ts`, `*.test.tsx`, `*.spec.ts`, `*.spec.tsx`.
- Files inside `__tests__/` directories.
- Files declared by the runner's configuration `include` patterns.

For each file, collect:

- Query call counts per priority tier and per query name.
- `userEvent` and `fireEvent` call counts.
- `container.querySelector` and `document.querySelector` call counts.
- `screen` adoption (do queries come from `screen` or from a destructured render return).
- `wrapper`-named render returns.
- Snapshot counts and sizes.
- `waitFor` patterns (callback contents, assertion counts, side effects).
- `act` wrappings.
- `cleanup` imports and calls.
- Test names (for the behaviour-vs-implementation heuristic).
- Class assertions (`toHaveClass`) — categorise as semantic-token or raw-utility-class via the heuristic above.
- Mock targets (module-boundary vs internal).
- Flaky-pattern signals (fixed waits, ordering dependencies, shared mutable state).

### Step 5 — Build the diagnostic snapshot

Aggregate the per-file data into the items listed in Layer 0. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 6 — Resolve each check

For each check in the active layer list, walk its detection logic. Threshold-bearing checks compare the aggregate to the configured threshold. Heuristic checks (behaviour-vs-implementation, mocking-at-boundaries) report `partial` rather than `violation` even when adherence is poor, because the heuristics are imperfect.

For each check, record evidence and up to ten representative samples plus a total count.

### Step 7 — Write phase 1 outputs

Create `audits/testing-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 8 — Print the chat summary and offer phase 2

Print the snapshot's headline numbers (test runner, end-to-end framework, test count, query-tier distribution, `userEvent` ratio, snapshot count, components-without-tests count) plus a short table mirroring the four layers with counts of present, partial, missing, and violation per layer. Highlight the top three violations across the philosophy-driven layer-4 checks (behaviour-vs-implementation drift, utility-class assertions, oversized snapshots), then the top three from layers 2 and 3. Then ask the single yes-or-no question. Do not proceed to phase 2 without an explicit affirmative.

### Step 9 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`, ordered by testing-philosophy impact:

1. **Header** — repository name, baseline version, test runner, end-to-end framework, timestamp, total counts per layer.
2. **Behaviour-over-implementation fixes** — rewrites of test names and assertions that test internal state instead of user behaviour.
3. **Snapshot cleanup** — oversized or implementation-coupled snapshots to remove or replace with explicit assertions; bounded snapshots to keep and rationalise.
4. **Utility-class-assertion replacements** — every `toHaveClass('bg-gray-100')` (and similar) replaced with semantic-token assertions or, where possible, role/label/text assertions.
5. **Query-priority migrations** — `getByTestId` and `container.querySelector` sites rewritten in priority order: try `getByRole` first, then `getByLabelText`, then text-based queries.
6. **Async-pattern fixes** — `waitFor` discipline, `find*` adoption, `query*` confined to absence checks, `act` and `cleanup` cleanups.
7. **Tooling fixes** — missing testing-library packages, jest-dom matcher loading, eslint plugin configuration.
8. **Coverage and end-to-end fixes** — coverage threshold introduction, end-to-end framework adoption when missing, components-without-tests gap closure prioritised by graph centrality when Graphify is present.
9. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not edit tests, install packages, or modify configuration.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:14Z",
  "testRunner": { "name": "vitest", "version": "1.6.0" },
  "endToEndFramework": "playwright",
  "withRun": true,
  "thresholds": {
    "priorityOneRatio": 70,
    "testidRatio": 10,
    "byRoleRatio": 50,
    "userEventRatio": 80,
    "snapshotLines": 100
  },
  "snapshot": {
    "testFileCount": 187,
    "testCount": 624,
    "queryDistribution": {
      "priorityOne": { "ratio": 0.78, "byRole": 0.62, "byLabelText": 0.09, "byPlaceholderText": 0.02, "byText": 0.04, "byDisplayValue": 0.01 },
      "priorityTwo": { "ratio": 0.05, "byAltText": 0.04, "byTitle": 0.01 },
      "priorityThree": { "ratio": 0.17, "byTestId": 0.17 }
    },
    "userEventRatio": 0.71,
    "containerQuerySelectorCount": 14,
    "snapshots": { "count": 22, "averageLines": 142, "oversized": 9 },
    "componentsWithoutTests": 31,
    "coverage": { "lines": 0.74, "statements": 0.73, "branches": 0.61, "functions": 0.78 },
    "flakyPatternSignals": { "fixedWaits": 6, "orderingDependent": 0, "sharedMutableState": 2 }
  },
  "summary": {
    "testRunnerAndTooling":             { "present": 6, "partial": 1, "missing": 1, "violation": 1 },
    "queryPriorityAndSelectorHygiene":  { "present": 4, "partial": 2, "missing": 0, "violation": 1 },
    "interactionAndAsyncPatterns":      { "present": 5, "partial": 1, "missing": 0, "violation": 3 },
    "testDesignAndCoverage":            { "present": 2, "partial": 3, "missing": 0, "violation": 3 }
  },
  "checks": [
    {
      "layer": "test-design-and-coverage",
      "check": "no-utility-class-assertions",
      "status": "violation",
      "evidence": [],
      "samples": [
        { "path": "src/components/Button.test.tsx", "line": 28, "match": "expect(button).toHaveClass('bg-gray-100')" },
        { "path": "src/components/Card.test.tsx", "line": 41, "match": "expect(card).toHaveClass('p-4 text-slate-500')" }
      ],
      "totalCount": 17,
      "expectation": "Class assertions use semantic theme tokens (bg-primary, text-brand) — never raw utility classes. Better still, assert on role, label, or text.",
      "gap": "17 toHaveClass assertions reference raw Tailwind utility classes that will break on theme changes.",
      "remediation": "Replace utility-class assertions with semantic-token assertions (bg-primary, text-destructive) or, preferably, with role/label/text assertions that describe the user-visible behaviour."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), the test runner and version, the end-to-end framework, applied thresholds, applied filters, and the `withRun` flag plus any captured exit-status from the runner.

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags, threshold overrides, and the `--with-run` flag are recorded in `metadata.json` so a partial run can be reproduced.
- Run-derived data (coverage, real test count) is timestamp-tagged in metadata; staleness is the user's responsibility to manage by re-running.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| `no tsconfig.json detected` | JavaScript-only project. | Stop. Inform the user that the skill currently supports TypeScript projects only. |
| React not detected | The project does not depend on `react` directly or via a meta-framework. | Stop. Inform the user that this skill currently supports React projects only. |
| Neither Vitest nor Jest installed | The project has no recognised test runner. | Stop with a friendly message recommending `/quality-gates-audit` (which surfaces the broader gap) and the installation of Vitest or Jest before re-running. |
| Both Vitest and Jest installed | Mid-migration or accidental dual install. | Continue. Run the audit against whichever has a configuration file. Surface dual-installation as a `violation` on the layer 1 "single test runner" check. |
| `--with-run` set but runner fails to start | Binary missing, configuration invalid, monorepo path-resolution issue. | Record the failure and the captured stderr in metadata. Run-dependent checks degrade to `partial`. Continue with the static analysis. |
| Test files exist but match no recognised pattern | Custom test file naming. | Run with the patterns declared in the runner configuration. When that fails, fall back to scanning files that import `@testing-library/react`. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The components-without-tests check falls back to per-folder heuristics; the implementation plan loses centrality-based prioritisation. |
| Snapshot files exceed the threshold but were intentionally bounded by the author | False positive on the heuristic. | The implementation plan recommends the user document a per-snapshot rationale (a comment near the snapshot reference, or moving the snapshot to a separately-named file) before deciding to keep or replace. |
| Class assertion uses a custom design token that the heuristic does not recognise | Project-specific token naming. | The check reports `partial` rather than `violation` when the heuristic is unsure. The user can extend the recognised-token pattern in a future iteration via `/system-self-improve`. |

## What this skill explicitly does NOT do

- Modify any test, configuration file, or source file.
- Run Playwright, Cypress, or any other end-to-end test runner. End-to-end runs serve real browsers and have side effects.
- Install any package or dependency.
- Open pull requests or commit anything to git.
- Audit JavaScript-only projects.
- Audit non-React frontends. The query-priority and interaction layers use React Testing Library conventions; Vue Testing Library and others are out of scope for v1.
- Audit Mocha, AVA, Tap, or Node's built-in test runner. Vitest and Jest are the only supported runners in v1.
- Audit utility-first CSS itself. Tailwind, UnoCSS, and similar frameworks are perfectly fine in components — the audit's only stance on utility classes is in *test assertions*, where they make tests brittle. Component code is `/architecture-audit`'s and `/react-audit`'s domain.
- Replace human review of nuanced test design decisions. The audit catches structural patterns; trade-offs like "should this be a unit test or an integration test" still require judgement.

## References

- Testing Library, [About Queries — Priority](https://testing-library.com/docs/queries/about/#priority) — the canonical source for the priority ladder.
