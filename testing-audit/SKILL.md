---
name: testing-audit
description: Audit whether a JavaScript or TypeScript project's tests would fail if the product broke — runner, lint, and coverage facts from a collector, a pre-filter for hollow tests, risk-ranked judgement, and optional mutation testing.
disable-model-invocation: true
argument-hint: "[--worktree] [--since=<ref>] [--months=<n>] [--with-mutation] [--learn|--teach]"
---

# /testing-audit

Find out whether a project's tests would catch the bugs that matter, and which tests to fix, add, or retire first.

The audit asks one question of every test it reads: **would this fail if the behaviour it names broke?** It applies to any JavaScript or TypeScript project: a Node service, a library, a command-line tool, or a web application. The component layer applies only when the project has a user interface.

It has four sources:
- a deterministic collector, which detects runners, focused and skipped tests, coverage thresholds, and the Testing Library and jest-dom lint rules (read statically from the ESLint configuration);
- a mechanical pre-filter in the collector, which flags tests whose shape suggests they cannot fail;
- the judgement of the model, spent on the riskiest code first: Git churn hotspots and high fan-in modules;
- optionally, a Stryker mutation run on the five riskiest files (`--with-mutation`).

It is static and read-only by default. Only `--with-mutation` runs the project's tests, inside Stryker's sandbox.

**Testing philosophy.** The audit grades with three principles. Test behaviour, not implementation: assert what a user or caller observes. Snapshots are a smell: a snapshot nobody reviewed is an expected value copied from the code. Assert on meaning, not styling: a role, a label, or a semantic token, never a raw utility class.

[Detection notes](references/detection.md) explain how to evaluate each check, the hollow-test shapes, and the look-alikes to keep.

## Usage

```
/testing-audit                     # audit the current repository
/testing-audit --worktree          # run in .worktrees/testing-audit on its own branch
/testing-audit --since=<ref>       # judge only tests and modules changed since <ref> (a provisional, filtered run)
/testing-audit --months=12         # widen the Git history window for the risk ranking (default 6)
/testing-audit --with-mutation     # also run an installed Stryker on the five riskiest files
/testing-audit --learn             # teaching mode; --teach is an alias
```

`--since` and `--months` make the run provisional, because they change what the audit measures. Without `--with-mutation`, the mutation check is recorded as not evaluated, which lowers coverage but never the score.

## Ownership

| Concern | Owner |
| --- | --- |
| Whether the tests would catch bugs, how they mock, and whether critical paths are covered above the unit level | `/testing-audit` |
| Whether tests, coverage, and end-to-end suites run in hooks and continuous integration | `/quality-gates-audit` |
| Whether change hotspots are cohesive and well designed | `/architecture-audit` |
| Whether error paths are handled at all | `/error-handling-audit` |
| Automated accessibility scans and performance measurement in end-to-end runs | `/accessibility-audit`, `/performance-audit` |

The runner, coverage, and critical-path checks name their quality-gates counterparts in `relatedChecks`; this audit rates only what those checks do not: whether the suite exists, runs, and tests the right things.

## The baseline

Statuses follow the shared taxonomy: `present`, `partial`, `missing`, or `violation`. Checks that cannot apply are recorded as not applicable, and checks you could not evaluate as not evaluated, never as `partial`. Severity follows Architecture Decision Record 0004: behaviour-level checks are high, and lint-rule checks are medium or low. The method column says whether the collector decides the check (`tool`) or you do (`model`).

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

The collector records:
- runners, test files by kind (unit, integration, component, Vitest browser mode, Playwright component, end-to-end), the number of tests, and Storybook stories with `play` functions;
- user-interface frameworks, Testing Library packages, database clients, Mock Service Worker setups, and Testcontainers or other integration tooling;
- package scripts and continuous-integration lines that run tests, and where coverage thresholds live;
- module mocks of the repository's own modules, flake signals, and Testing Library query and interaction counts;
- the risk ranking (churn plus fan-in), untested risky files, the mutation targets, and the pre-filter's candidates.

Add the critical paths you identify, and which test level covers each.

### Layer 1 — Test infrastructure

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Tests run under a configured runner | high | tool | Test files exist, a runner is declared, and a package script runs it. | No tests, tests with no runner, or no script that runs them (partial). |
| No focused or skipped tests committed | medium | tool | No `.only`, `fit`, or `fdescribe`; no `.skip`, `xit`, `.todo`, or `.fixme` left behind. | A focused test (violation); skipped or to-do tests (partial). |
| Coverage thresholds configured | medium | tool | The unit or integration runner sets thresholds, and something collects coverage so they can fail. | No thresholds (missing), or thresholds nothing enforces (partial). |

### Layer 2 — Test effectiveness

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Tests would fail if the behaviour broke | high | model | A plausible bug in the behaviour each test names turns it red. | Hollow tests on risky code: no assertion, an expected value computed by the code, a Given that never reaches the case, or assertions that pass when imports return undefined. |
| Mocking only at boundaries | high | model | Only the network, clock, randomness, and third parties are faked; call assertions check content sent across a boundary. | Mocks of the repository's own modules, or tests that assert only on mocks they set up. |
| Tests are deterministic | medium | model | Time, randomness, and shared state are controlled; tests wait for conditions, not delays. | Fixed sleeps, the real clock or randomness in assertions, order-dependent tests. |
| Test titles state the behaviour checked | low | model | Each title names an observable outcome. Soft check — mixed adherence is reported as partial. | Titles that name functions or steps, or promise what the test does not check. |
| Mutations in the riskiest files are caught | medium | tool | With `--with-mutation`, at least 80 percent of mutants in the five riskiest files are detected. | Score below 80 percent (partial) or below 60 percent (violation). |

### Layer 3 — Test levels and critical paths

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Critical paths covered by integration or end-to-end tests | high | model | Each critical path runs through its real wiring in an integration or end-to-end test. | A critical path, or a top-ranked risky module on one, with no test above the unit level. |
| Data access tested against a real database | medium | model | Queries and migrations run against the production engine, for example with Testcontainers. Not applicable without a database client. | Data access tested only through a mocked client, or not at all. |

### Layer 4 — Component tests

The collector records these checks as not applicable when the project has no user-interface framework, Testing Library, or jest-dom, as each requires.

| Check | Severity | Method | Expectation | Violation signal |
| --- | --- | --- | --- | --- |
| Testing Library lint rules enforced | medium | tool | eslint-plugin-testing-library's framework configuration plus `prefer-user-event` and `prefer-explicit-assert`, covering the twelve rules in the detection notes. | Plugin not enabled (missing), or some rules off (partial). |
| jest-dom lint rules enforced | low | tool | eslint-plugin-jest-dom's recommended rules are on. | Plugin not enabled (missing), or some rules off (partial). |
| Components tested through what users perceive | medium | model | Queries by role, label, or text; user-event or a real browser; assertions on what users see. | Test IDs or selectors as the norm, assertions on state, props, or raw utility classes, or snapshots in place of assertions. |

## Judging a test

This is the core of the audit. For each test you read:

1. **Read the title, then the Given, then where the expected value comes from.**
2. **Could the expected value be wrong in the same way as the code?** It could if it is computed by the code under test, by a copy of its logic, or read back from the result. A literal from a specification or a worked example is independent.
3. **Which bug turns it red?** Name the smallest plausible bug in the behaviour the title names, such as a dropped condition, a filter removed, or an off-by-one at a boundary. If no such bug fails the test, it is hollow. Never judge by "replace the code with the right answer"; every concrete test passes that.
4. **Does the Given reach the case?** The commonest hollow test asserts real things about an input that never exercises the named case: data already sorted, nothing for the filter to remove, a value equal to the default, or input rejected earlier for another reason.

Classify each candidate as **real**, **hollow**, **mirror** (it asserts that an internal collaborator was called), **duplicate**, or **snapshot-only**, with one sentence and a `path:line` citation that quotes the line. Keep the look-alikes in the detection notes: idempotence, round trips, comparisons, and a mock that is only the Given are real tests.

## What this skill does

1. Stages a run through the shared audit protocol. The collector decides the tool checks, marks what does not apply, and records the risk ranking and pre-filter.
2. Reads tests in risk order within a discovery budget, rather than walking every file.
3. Judges each candidate with the method above. Every finding cites the lines it rests on.
4. Publishes `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` through the protocol, and summarises the act-on findings.
5. Offers an implementation plan with a retirement card for each low-value test.

## Implementation steps

1. **Begin.** Follow [the run protocol](../audit-protocol/references/run-protocol.md). With `--worktree`, create the worktree as it describes.

   ```bash
   python3 "${CLAUDE_SKILL_DIR}/../audit-protocol/scripts/audit_run.py" begin testing-audit
   ```

   - Pass `--since <ref>` for a diff-scoped run, and `--threshold months=<n>` for `--months`.
   - Pass `--enrichment with-mutation` for `--with-mutation`. The collector then runs Stryker itself, writing only under `.architect-audits/testing-audit/mutation/`, and records the mutation check. If Stryker is missing, fails, or times out, the check is not evaluated with the reason.
2. **Read the evidence the collector staged.** Read the `snapshot` in `.architect-audits/testing-audit/.staging/run.json`: `riskRanking`, `untestedRiskyFiles`, `preFilter`, `moduleMocks`, `flakeSignals`, and `firstClassTooling`. Identify the critical paths from routes, handlers, and the risk ranking, and record them with `snapshot --set criticalPaths=<json>`.
3. **Read within the discovery budget.** Read the tests of the five highest-ranked modules, then the first 25 pre-filter candidates in their ranked order. Widen by another 25 only while more than a third of what you read is hollow or a mirror. Record what you sampled with `snapshot --set sampled=<json>`.
4. **Evaluate every model check.**
   - Apply "Judging a test" and the detection notes.
   - Record each check with `record`, citing `path:line` with quoted fragments. Give every non-present result a `--tier`. Show absence with `files:` or a `search:` count, and prefix free-text observations with `note:`.
   - A pre-filter flag is a place to look, never evidence on its own; cite the test you read.
   - Use `hypothesis` for suspicions you cannot verify, and `not-evaluated` with a reason when the repository gives you no basis.
5. **Judge.** Keep at most about five findings `act-on`, ranked by severity and then by risk rank. Dismiss with a reason any flagged test you judged real.
6. **Finish.** Run `audit_run.py finish testing-audit`, fix anything it rejects, and present the chat summary the protocol defines.
7. **Offer the plan.** Ask: "Generate an implementation plan for the act-on findings? (yes/no)"

## Phase 2: the implementation plan

On yes, write `.architect-audits/testing-audit/implementation-plan.md` with the task briefs the protocol defines. Order them: critical paths with no test above the unit level, then hollow tests on the riskiest modules, then mocking, then infrastructure and lint rules.

End the plan with a **Retirement cards** section: one card for every test you judged hollow, a mirror, a duplicate, or snapshot-only.

```markdown
#### <test title> (`path/to/file.test.ts:42`)

- Verdict: hollow | mirror | duplicate | snapshot-only
- Why it cannot fail: <the bug in the named behaviour that leaves it green>
- What it costs: <false confidence, runtime, or churn on refactors>
- Action: delete | rewrite | merge into <test>
- Replacement: <a Given that could go the other way, and a literal expected value>
- Proof: <the planted bug the replacement must turn red>
```

A rewrite is done only when the replacement fails with the planted bug and passes without it. If a rewrite fails on unchanged code, the hollow test may have hidden a product bug: report it rather than weakening the assertion.

## Repository Quality Score findings contract

The protocol publishes findings schema `2.0.0`. It writes one `runIdentifier`, `runStartedAt` and `runFinishedAt`, and the `checkCatalogVersion`. It records `applicability`, `evaluationState`, and `evidenceQuality` for every catalog check, and repeats the run identity in `metadata.json`. See `.agents/AUDIT_FINDINGS_CONTRACT.md` in the playbook repository.

## What this skill explicitly does NOT do

- Modify, add, or delete any test, configuration, or source file. The only writes are under `.architect-audits/testing-audit/`, through the protocol and, with `--with-mutation`, Stryker.
- Install packages, or run ESLint. Lint rules are read from the configuration as text.
- Run the test suite, end-to-end suites, or a browser, except Stryker's sandboxed runs with `--with-mutation`.
- Require a test file per component or module. One integration test can cover a page of components.
- Treat coverage as proof of effectiveness, or grade a test by its shape alone. The pre-filter only chooses what to read.
- Audit languages other than JavaScript and TypeScript.
