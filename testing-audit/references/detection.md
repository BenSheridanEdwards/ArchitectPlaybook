# Testing audit: detection notes

How to evaluate each check, what counts as evidence, and when to stop. The
audit's one question is whether a test would fail if the behaviour it names
broke. Everything here serves that question.

## Where to read

1. Read the collector's snapshot first:
   - `riskRanking`: source files ranked by commits in the history window plus
     the number of source files that import them, with the tests that import
     each one;
   - `untestedRiskyFiles`: ranked files no test imports directly;
   - `preFilter`: tests flagged by shape, already in risk order;
   - `moduleMocks`, `flakeSignals`, `queryUsage`, and `interactions`;
   - `firstClassTooling`, `testFiles.byKind`, and `continuousIntegration`.
2. Read the tests of the five highest-ranked modules, with the module beside
   them. A hollow test on a hotspot is worth more than ten on dormant code.
3. Read the first 25 pre-filter candidates. Widen by 25 at a time only while
   more than a third of what you read is hollow or a mirror. Stop there: the
   findings need examples, not a census.
4. An untested risky file is not itself a finding under Layer 2. Take it to
   the critical-path check if it sits on a critical path.

Search by hand where the collector cannot see: helpers in shared test-utility
modules, custom matchers, and page objects.

## Layer 1: test infrastructure (tool)

- **Tests run under a configured runner.** Runners come from development
  dependencies, runner configuration files, `node:test` imports, and scripts
  running `node --test`. Test files are `*.test.*`, `*.spec.*`, `*.cy.*`,
  `*.e2e.*`, files in `__tests__/`, and files in `test/`, `tests/`, `e2e/`, or
  `integration/` folders that call `describe`, `it`, or `test`. "Present" needs
  a package script that runs a runner, or a `test` script that delegates to
  workspaces through Turborepo, Nx, Lerna, or a recursive package-manager run.
  Whether continuous integration runs it is `/quality-gates-audit`'s check;
  the lines that do are in the snapshot.
- **No focused or skipped tests committed.** The collector masks comments,
  strings, and regular expressions first, so `"it.only("` inside a string or a
  commented-out `.only` does not count.
  - Focused: `.only` on `describe`, `it`, `test`, `context`, `suite`, or a
    Playwright `test.describe`, and `fit`, `fdescribe`, `ftest`.
  - Skipped: `.skip`, `.todo`, `.fixme`, `xit`, `xdescribe`, `xtest`. A
    conditional `test.skip(browserName === 'webkit')` inside a test is not
    counted; neither are Vitest's `skipIf` and `runIf`.
- **Coverage thresholds configured.**
  - Vitest: `coverage.thresholds` in a Vitest or Vite configuration, or
    `--coverage.thresholds.<metric>` in a script.
  - Jest: `coverageThreshold` in its configuration or `package.json`.
  - c8 and nyc: `check-coverage` with a metric. node:test:
    `--test-coverage-lines` and its siblings.
  - Thresholds fail a run only when coverage is collected: `coverage.enabled`,
    `collectCoverage`, `--coverage` in a script or workflow, or c8 or nyc
    wrapping the run. Thresholds with none of these are partial.

## Layer 2: test effectiveness

### The pre-filter

The collector flags tests by shape. A flag is a place to look, not a
verdict: it misses hollow tests whose problem is what they assert, and it
flags real tests that only look hollow.

| Flag | What the collector saw |
| --- | --- |
| `no-assertion` | No `expect`, `assert`, `.should`, Testing Library `getBy` or `findBy` query, or call to a local or imported helper that asserts. |
| `mock-only` | Every assertion is a call assertion on a mock (`toHaveBeenCalledWith` and its family, `.mock.calls`), or compares a result with a value the file fed into a mock (`mockResolvedValue(invoice)` then `toEqual(invoice)`). |
| `snapshot-only` | Every assertion is a snapshot matcher. |
| `passes-if-undefined` | Every assertion would still pass if every function the test imports from the repository returned `undefined`. |
| `near-duplicate` | It makes the same assertions as another test in the same file, on a setup at least 90 percent the same. |

`passes-if-undefined` covers these shapes:
- a negated assertion, such as `not.toBe(0)`, `not.toThrow()`, or
  `not.toBeInTheDocument()`, with no positive assertion beside it;
- `toBeUndefined()`, `toBeFalsy()`, or equality with `undefined`;
- a subject that is a literal, such as `expect(true).toBe(true)`;
- an expected value computed by the same imported code, directly or through a
  variable: `expect(total(items)).toEqual(total(items))`, or
  `const expected = total(items)`;
- assertions that run only inside an `if`, a loop, a `forEach` or `map`
  callback, or a `catch`, so they never run when the result is empty;
- `.resolves`, `.rejects`, `expect.poll`, and Playwright's web-first matchers
  that are neither awaited nor returned, so the test ends first;
- `expect(value)` with no matcher.

### Hollow shapes the pre-filter cannot see

Read for these in every test you sample:

1. **The test builds what it tests.** No product code runs: a function defined
   in the test, a page the test serves itself with `route.fulfill`, or state it
   writes with `page.evaluate` and then asserts.
2. **The expected value is the code's own output.** A copied formula or
   template, a call to a helper the code also uses, or a snapshot accepted
   without being read.
3. **The mock goes in and comes out.** The fake returns a value, and the test
   asserts that same value comes back, while the logic in between had nothing
   to do for that input.
4. **The assertion is too weak to fail.** `toBeDefined()` on a value the types
   guarantee, `length >= 0`, a regular expression that matches any reason, or a
   presence check where the title promises an absence.
5. **The Given never reaches the case.** The input is rejected earlier for
   another reason, already has the property, or equals the default. This is the
   commonest shape in real suites and the one scanners miss most. Picture the
   simplest wrong version of the behaviour and ask whether this input would
   still give this result.
6. **A mirror.** `expect(save).toHaveBeenCalledWith(input)` on an internal
   collaborator passes when the collaborator is broken and fails when the code
   is refactored. Keep a call assertion only for an effect at a system boundary,
   and then assert the content sent.

### Look-alikes that are real tests

Keep these, and dismiss their flags with a reason:
- the mock is only the Given, and the product must act on it (a fake backend
  says a session expired; the test asserts the page says so);
- a round trip through two different functions, such as serialise and parse;
- an idempotence property, `f(f(x))` equal to `f(x)`;
- a comparative property: a larger input gives more pages than a smaller one;
- an identity contract: already-normal input comes back unchanged;
- one concrete literal example. A missing case is a coverage gap, not a hollow
  test.

### The checks

- **Tests would fail if the behaviour broke.**
  - Present: the sampled tests on the riskiest modules each name a bug that
    turns them red.
  - Partial: some hollow tests, but the risky behaviour also has real tests.
  - Violation: hollow tests are the only tests of a top-ranked module, or most
    sampled tests on risky code are hollow.

  Cite each hollow test at the assertion line, and say which bug leaves it
  green.
- **Mocking only at boundaries.**
  - Boundaries: HTTP (Mock Service Worker at the network edge is the
    first-class choice), the clock (fake timers), randomness, the file system
    and process in unit tests, and third-party SDKs.
  - Not boundaries: the repository's own services, repositories, hooks, and
    utilities. `moduleMocks.internalExamples` lists `vi.mock` and `jest.mock`
    calls on them.
  - Violation: a test of a module that mocks that module's own collaborators
    and asserts only on the mocks. A mocked repository in a route test is
    partial when a separate integration test covers the real path.
- **Tests are deterministic.** Use `flakeSignals`: fixed waits, the real
  clock, and randomness. Confirm each by reading: `setTimeout` inside a fake
  timer setup, or a random seed that is fixed, is fine. Also look for
  module-level mutable state that tests change without resetting.
- **Test titles state the behaviour checked.** Soft check. Partial when a
  meaningful share of sampled titles name functions or steps, or promise more
  than the test checks.
- **Mutations in the riskiest files are caught (tool).**
  - With `--with-mutation`, the collector writes a one-off Stryker
    configuration that keeps the project's own Stryker settings, if any, and
    mutates only `mutationTargets`. Sandbox and JSON report go under
    `.architect-audits/testing-audit/mutation/`.
  - The score is killed plus timed-out mutants over those plus survived and
    uncovered ones.
  - At least 80 percent is present, 60 to 80 partial, and under 60 a violation;
    surviving mutants are cited by line.
  - Without the flag, or when Stryker is not installed, fails, times out
    (eight minutes), or makes no mutants, the check is not evaluated with the
    reason. Never record it partial because the tool did not run.
  - Mutation testing finds behaviour no test pins down. It does not prove
    which test is hollow, so use survivors to choose where to read, not as
    verdicts on tests.

## Layer 3: test levels and critical paths

- **Critical paths covered by integration or end-to-end tests.**
  - Identify critical paths: sign-in and sessions, payments and refunds,
    primary writes, permission checks, and the routes or handlers behind the
    top-ranked risky modules.
  - Integration levels that count:
    - route-level tests through the real router (`supertest`, Fastify
      `inject`, Hono `app.request`);
    - tests against a real database (Testcontainers, a database service in
      continuous integration);
    - component or page tests with Mock Service Worker at the network edge;
    - Vitest browser mode, Playwright component tests, Storybook interaction
      tests run by the test-runner or the Vitest addon;
    - Playwright or Cypress end-to-end tests.
  - Missing: no test above the unit level at all. Violation: a critical path
    has no test at any level, or only unit tests with its collaborators
    mocked. Partial: some critical paths are covered.
- **Data access tested against a real database.** Not applicable without a
  database client (the collector decides). Present: repository or query code
  runs against the same engine in tests. In-memory substitutes such as
  `pg-mem` or SQLite for a PostgreSQL application are partial: they miss
  engine-specific behaviour. Mocked clients only, or no tests, is a violation.

## Layer 4: component tests

- **Testing Library lint rules enforced (tool).** The collector reads every
  ESLint configuration as text, flat or legacy, and `eslintConfig` in
  `package.json`. A rule is on when a shared configuration enables it, or it is
  set to `warn` or `error`, and no later setting turns it off. The twelve
  rules:
  - `prefer-screen-queries`, `render-result-naming-convention`,
    `no-container`, `no-node-access`;
  - `prefer-find-by`, `prefer-presence-queries`,
    `no-wait-for-multiple-assertions`, `no-wait-for-side-effects`;
  - `no-unnecessary-act`, `no-manual-cleanup`;
  - `prefer-user-event` and `prefer-explicit-assert`, which no shared
    configuration enables, so they must be set explicitly.

  The `react` configuration covers the first ten; `dom`, `vue`, `svelte`,
  `angular`, and `marko` cover fewer.
- **jest-dom lint rules enforced (tool).** The recommended configuration, or
  the eleven `prefer-*` rules it contains, set individually.
- **Components tested through what users perceive.**
  - Use `queryUsage` and `interactions`. Role, label, and text queries should
    dominate; `getByTestId` and `container.querySelector` should be rare and
    justified.
  - `user-event` should be preferred over `fireEvent`. A real browser (Vitest
    browser mode, Playwright component tests, Storybook interaction tests) is
    stronger still.
  - Violation: assertions on component state, props, or hook internals;
    `toHaveClass` with raw utility classes such as `bg-gray-100` rather than
    semantic tokens; or snapshots used in place of assertions on what users
    see.

## Severity and judgement

Act on at most about five findings. Rank by severity, then by the risk rank of
the code involved. A hollow test on the top hotspot outranks a missing lint
rule. Mark findings on dormant code `consider`. When most flagged tests turn
out to be real, say so, and dismiss the flags with reasons rather than
inflating the list.
