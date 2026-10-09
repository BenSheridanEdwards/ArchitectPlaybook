from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "testing-audit" / "scripts" / "collect.py"

for _variable in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR"):
    os.environ.pop(_variable, None)

spec = importlib.util.spec_from_file_location("testing_collector", COLLECTOR)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)

CHECK = "testing-audit."
VITEST_PACKAGE = {"name": "service", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "4.0.0"}}


def flags_of(path: str, text: str) -> dict[str, list[str]]:
    """Pre-filter flags by test title for one test file's text."""
    blocks, _ = collector.prefilter(path, text, collector.mask(text))
    return {block.title: [flag for flag, _ in block.flags] for block in blocks}


class MaskTests(unittest.TestCase):
    def test_comments_strings_templates_and_regular_expressions_are_blanked(self) -> None:
        text = (
            "const a = 'it.only(';\n"
            "// it.only('commented')\n"
            "/* fdescribe( */ const b = \"x.skip(\";\n"
            "const c = `it.only(${value.skip}) and more`;\n"
            "const d = /it\\.only\\(/g; const e = 4 / 2;\n"
        )
        masked = collector.mask(text)
        self.assertEqual(len(masked), len(text))
        self.assertEqual(masked.count("\n"), text.count("\n"))
        self.assertNotIn("only", masked)
        self.assertNotIn("fdescribe", masked)
        self.assertIn("value.skip", masked, "code inside ${} stays readable")
        self.assertIn("4 / 2", masked, "division is not a regular expression")
        self.assertIn("'", masked, "string delimiters are kept")

    def test_comment_only_mask_keeps_strings(self) -> None:
        text = "import x from './a'; // from './b'\n"
        masked = collector.mask(text, strings=False)
        self.assertIn("'./a'", masked)
        self.assertNotIn("./b", masked)


class CollectorTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "repository"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")

    def git(self, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(self.root), *arguments], check=True, capture_output=True)

    def write(self, relative: str, text: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def package(self, value: dict[str, Any]) -> None:
        self.write("package.json", json.dumps(value, indent=2) + "\n")

    def commit(self, message: str = "change") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def collect(self, **options: Any) -> dict[str, Any]:
        return collector.collect(self.root, **options)

    def check(self, result: dict[str, Any], name: str) -> dict[str, Any]:
        return result["checks"][CHECK + name]


class RunnerTests(CollectorTestCase):
    def test_no_tests_is_missing_and_effectiveness_checks_do_not_apply(self) -> None:
        self.package({"name": "service", "dependencies": {"fastify": "5.0.0"}})
        self.write("src/server.ts", "export const port = 3000;\n")
        self.commit()
        result = self.collect()
        self.assertEqual(self.check(result, "tests-run-under-configured-runner")["status"], "missing")
        for name in ("tests-fail-when-behaviour-breaks", "mocks-only-at-boundaries", "tests-are-deterministic", "test-titles-state-behaviour", "no-focused-or-skipped-tests"):
            self.assertEqual(self.check(result, name)["applicability"], "not-applicable", name)

    def test_tests_without_a_runner_are_missing(self) -> None:
        self.package({"name": "service"})
        self.write("src/a.test.ts", "describe('a', () => { it('adds', () => { expect(1 + 1).toBe(2); }); });\n")
        self.commit()
        check = self.check(self.collect(), "tests-run-under-configured-runner")
        self.assertEqual(check["status"], "missing")
        self.assertIn("no test runner is declared", check["gap"])

    def test_a_runner_with_no_script_is_partial_and_a_script_makes_it_present(self) -> None:
        self.package({"name": "service", "devDependencies": {"vitest": "4.0.0"}})
        self.write("src/a.test.ts", "import { it, expect } from 'vitest';\nit('adds', () => { expect(1 + 1).toBe(2); });\n")
        self.commit()
        self.assertEqual(self.check(self.collect(), "tests-run-under-configured-runner")["status"], "partial")
        self.package(VITEST_PACKAGE)
        self.commit()
        check = self.check(self.collect(), "tests-run-under-configured-runner")
        self.assertEqual(check["status"], "present")
        self.assertIn('package.json — `"vitest"`', check["evidence"])
        self.assertIn("package.json — `vitest`", check["evidence"])

    def test_node_test_backends_and_delegating_scripts_count(self) -> None:
        self.package({"name": "monorepo", "private": True, "workspaces": ["packages/*"], "scripts": {"test": "turbo run test"}})
        self.write("packages/api/package.json", json.dumps({"name": "api", "scripts": {"check": "tsc"}}))
        self.write("packages/api/test/orders.test.ts", "import { test } from 'node:test';\nimport assert from 'node:assert/strict';\ntest('totals', () => { assert.equal(1 + 1, 2); });\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["snapshot"]["runners"], ["node:test"])
        self.assertEqual(self.check(result, "tests-run-under-configured-runner")["status"], "present")

    def test_test_kinds_and_first_class_tooling_are_recognised(self) -> None:
        self.package({
            "name": "web",
            "scripts": {"test": "vitest run", "e2e": "playwright test", "test-storybook": "test-storybook"},
            "dependencies": {"react": "19.0.0"},
            "devDependencies": {
                "vitest": "4.0.0", "@playwright/test": "1.55.0", "@vitest/browser-playwright": "4.0.0",
                "@testing-library/react": "16.0.0", "msw": "2.0.0", "@storybook/test-runner": "0.23.0",
                "@playwright/experimental-ct-react": "1.55.0", "testcontainers": "11.0.0",
            },
        })
        self.write("e2e/checkout.spec.ts", "import { test, expect } from '@playwright/test';\ntest('pays', async ({ page }) => { await page.goto('/'); await expect(page.getByRole('heading')).toBeVisible(); });\n")
        self.write("src/Button.browser.test.tsx", "import { render } from 'vitest-browser-react';\nimport { test, expect } from 'vitest';\ntest('clicks', async () => { const screen = render(<b />); await expect.element(screen.getByRole('button')).toBeVisible(); });\n")
        self.write("src/Card.test.tsx", "import { render, screen } from '@testing-library/react';\nimport { server } from './mocks/server';\nit('shows', () => { render(<i />); expect(screen.getByText('x')).toBeTruthy(); });\n")
        self.write("src/mocks/server.ts", "import { setupServer } from 'msw/node';\nexport const server = setupServer();\n")
        self.write("src/Panel.ct.spec.tsx", "import { test, expect } from '@playwright/experimental-ct-react';\ntest('opens', async ({ mount }) => { const panel = await mount(<p />); await expect(panel).toBeVisible(); });\n")
        self.write("src/routes/orders.integration.test.ts", "import request from 'supertest';\nit('lists', async () => { const response = await request(app).get('/orders'); expect(response.status).toBe(200); });\n")
        self.write("src/Button.stories.tsx", "export const Clicked = { play: async ({ canvas }) => { await canvas.click(); } };\n")
        self.commit()
        snapshot = self.collect()["snapshot"]
        self.assertEqual(
            snapshot["testFiles"]["byKind"],
            {"component": 1, "end-to-end": 1, "integration": 1, "playwright-component": 1, "vitest-browser": 1},
        )
        self.assertEqual(snapshot["testFiles"]["storiesWithPlayFunctions"], 1)
        tooling = snapshot["firstClassTooling"]
        self.assertTrue(tooling["vitestBrowserMode"])
        self.assertTrue(tooling["playwrightComponentTests"])
        self.assertTrue(tooling["testcontainers"])
        self.assertEqual(tooling["storybookInteractionTests"], 1)
        self.assertIn("src/mocks/server.ts", tooling["mockServiceWorker"])
        self.assertEqual(snapshot["runners"], ["playwright", "storybook-test-runner", "vitest"])

    def test_continuous_integration_lines_that_run_tests_are_recorded(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write("src/a.test.ts", "it('adds', () => { expect(1 + 1).toBe(2); });\n")
        self.write(".github/workflows/ci.yml", "jobs:\n  test:\n    steps:\n      - run: npm ci\n      - run: npm test\n")
        self.commit()
        snapshot = self.collect()["snapshot"]
        self.assertEqual(snapshot["continuousIntegration"]["testRuns"], [".github/workflows/ci.yml:5 — `npm test`"])


class FocusTests(CollectorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.package(VITEST_PACKAGE)

    def test_focused_tests_are_violations_but_comments_and_strings_are_not(self) -> None:
        self.write(
            "src/a.test.ts",
            "describe('a', () => {\n"
            "  // it.only('was focused', () => {});\n"
            "  const label = 'it.only(';\n"
            "  it.only('adds', () => { expect(1 + 1).toBe(2); });\n"
            "});\n"
            "fdescribe('b', () => {});\n",
        )
        self.write("e2e/flow.spec.ts", "import { test } from '@playwright/test';\ntest.describe.only('flow', () => {});\n")
        self.commit()
        check = self.check(self.collect(), "no-focused-or-skipped-tests")
        self.assertEqual(check["status"], "violation")
        self.assertEqual(
            sorted(check["evidence"]),
            ["e2e/flow.spec.ts:2 — `test.describe.only`", "src/a.test.ts:4 — `it.only`", "src/a.test.ts:6 — `fdescribe`"],
        )

    def test_declared_skips_are_partial_and_conditional_skips_are_not_counted(self) -> None:
        self.write(
            "src/a.test.ts",
            "it.skip('later', () => {});\n"
            "xit('old', () => {});\n"
            "it.todo('refunds');\n"
            "it.skipIf(process.env.CI)('local only', () => { expect(1).toBe(1); });\n",
        )
        self.write("e2e/a.spec.ts", "import { test } from '@playwright/test';\ntest('x', async ({ browserName }) => {\n  test.skip(browserName === 'webkit', 'no support');\n});\n")
        self.commit()
        check = self.check(self.collect(), "no-focused-or-skipped-tests")
        self.assertEqual(check["status"], "partial")
        self.assertEqual(check["evidence"], ["src/a.test.ts:1 — `it.skip`", "src/a.test.ts:2 — `xit`", "src/a.test.ts:3 — `it.todo`"])

    def test_a_clean_suite_is_present(self) -> None:
        self.write("src/a.test.ts", "it('adds', () => { expect(1 + 1).toBe(2); });\n")
        self.commit()
        check = self.check(self.collect(), "no-focused-or-skipped-tests")
        self.assertEqual(check["status"], "present")
        self.assertTrue(check["evidence"][0].startswith("command: `collect.py focus scan` → 0 focused and 0 skipped tests in 1 test files"))


class CoverageTests(CollectorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("src/a.test.ts", "it('adds', () => { expect(1 + 1).toBe(2); });\n")

    def test_vitest_thresholds_need_coverage_collection(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write(
            "vitest.config.ts",
            "import { defineConfig } from 'vitest/config';\n"
            "export default defineConfig({ test: { coverage: { provider: 'v8', thresholds: { lines: 80 } } } });\n",
        )
        self.commit()
        check = self.check(self.collect(), "coverage-thresholds-configured")
        self.assertEqual(check["status"], "partial")
        self.assertIn("vitest.config.ts — `thresholds`", check["evidence"])
        self.package({**VITEST_PACKAGE, "scripts": {"test": "vitest run --coverage"}})
        self.commit()
        check = self.check(self.collect(), "coverage-thresholds-configured")
        self.assertEqual(check["status"], "present")
        self.assertIn("package.json — `--coverage`", check["evidence"])

    def test_commented_thresholds_do_not_count(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write("vitest.config.ts", "export default { test: { coverage: { provider: 'v8' /* thresholds: { lines: 80 } */ } } };\n")
        self.commit()
        self.assertEqual(self.check(self.collect(), "coverage-thresholds-configured")["status"], "missing")

    def test_jest_thresholds_in_the_manifest_and_a_workflow_flag(self) -> None:
        self.package({"name": "service", "scripts": {"test": "jest"}, "devDependencies": {"jest": "30.0.0"}, "jest": {"coverageThreshold": {"global": {"lines": 80}}}})
        self.write(".github/workflows/ci.yml", "jobs:\n  test:\n    steps:\n      - run: npx jest --coverage\n")
        self.commit()
        check = self.check(self.collect(), "coverage-thresholds-configured")
        self.assertEqual(check["status"], "present")
        self.assertIn(".github/workflows/ci.yml:4 — `--coverage`", check["evidence"])

    def test_only_end_to_end_runners_make_coverage_not_applicable(self) -> None:
        self.package({"name": "site", "scripts": {"test": "playwright test"}, "devDependencies": {"@playwright/test": "1.55.0"}})
        self.commit()
        self.assertEqual(self.check(self.collect(), "coverage-thresholds-configured")["applicability"], "not-applicable")


class LintTests(CollectorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.package({
            **VITEST_PACKAGE,
            "dependencies": {"react": "19.0.0"},
            "devDependencies": {
                "vitest": "4.0.0", "@testing-library/react": "16.0.0", "@testing-library/jest-dom": "6.0.0",
                "eslint-plugin-testing-library": "7.0.0", "eslint-plugin-jest-dom": "5.0.0",
            },
        })
        self.write("src/a.test.tsx", "it('adds', () => { expect(1 + 1).toBe(2); });\n")

    def test_missing_plugins_are_missing(self) -> None:
        self.write("eslint.config.js", "import js from '@eslint/js';\nexport default [js.configs.recommended];\n")
        self.commit()
        result = self.collect()
        check = self.check(result, "testing-library-lint-rules-enforced")
        self.assertEqual(check["status"], "missing")
        self.assertIn("eslint.config.js", check["evidence"])
        self.assertEqual(self.check(result, "jest-dom-lint-rules-enforced")["status"], "missing")

    def test_flat_presets_plus_the_two_opt_in_rules_are_present(self) -> None:
        self.write(
            "eslint.config.js",
            "import testingLibrary from 'eslint-plugin-testing-library';\n"
            "import jestDom from 'eslint-plugin-jest-dom';\n"
            "export default [\n"
            "  { files: ['**/*.test.tsx'], ...testingLibrary.configs['flat/react'] },\n"
            "  jestDom.configs['flat/recommended'],\n"
            "  { rules: { 'testing-library/prefer-user-event': 'error', 'testing-library/prefer-explicit-assert': ['warn'] } },\n"
            "];\n",
        )
        self.commit()
        result = self.collect()
        check = self.check(result, "testing-library-lint-rules-enforced")
        self.assertEqual(check["status"], "present")
        self.assertIn("eslint.config.js — `testingLibrary.configs['flat/react']`", check["evidence"])
        self.assertEqual(self.check(result, "jest-dom-lint-rules-enforced")["status"], "present")

    def test_a_preset_without_the_opt_in_rules_or_with_rules_off_is_partial(self) -> None:
        self.write(
            "eslint.config.mjs",
            "import testingLibrary from 'eslint-plugin-testing-library';\n"
            "export default [testingLibrary.configs['flat/react'], { rules: { 'testing-library/no-node-access': 'off' } }];\n",
        )
        self.commit()
        check = self.check(self.collect(), "testing-library-lint-rules-enforced")
        self.assertEqual(check["status"], "partial")
        self.assertIn("testing-library/no-node-access", check["gap"])
        self.assertIn("testing-library/prefer-user-event", check["gap"])
        self.assertIn("testing-library/prefer-explicit-assert", check["gap"])

    def test_commented_out_configuration_does_not_count(self) -> None:
        self.write("eslint.config.js", "// import testingLibrary from 'eslint-plugin-testing-library';\n// testingLibrary.configs['flat/react']\nexport default [];\n")
        self.commit()
        self.assertEqual(self.check(self.collect(), "testing-library-lint-rules-enforced")["status"], "missing")

    def test_legacy_and_manifest_configuration_are_read(self) -> None:
        self.write(".eslintrc.json", json.dumps({"extends": ["plugin:testing-library/dom"], "rules": {"testing-library/prefer-user-event": 2}}))
        self.commit()
        check = self.check(self.collect(), "testing-library-lint-rules-enforced")
        self.assertEqual(check["status"], "partial")
        self.assertIn("testing-library/no-container", check["gap"])
        self.assertNotIn("testing-library/prefer-user-event,", check["gap"])
        (self.root / ".eslintrc.json").unlink()
        package = json.loads((self.root / "package.json").read_text())
        package["eslintConfig"] = {"extends": ["plugin:jest-dom/recommended"]}
        self.package(package)
        self.commit()
        self.assertEqual(self.check(self.collect(), "jest-dom-lint-rules-enforced")["status"], "present")

    def test_lint_checks_do_not_apply_without_their_packages(self) -> None:
        self.package({**VITEST_PACKAGE, "dependencies": {"fastify": "5.0.0"}})
        self.commit()
        result = self.collect()
        self.assertEqual(self.check(result, "testing-library-lint-rules-enforced")["applicability"], "not-applicable")
        self.assertEqual(self.check(result, "jest-dom-lint-rules-enforced")["applicability"], "not-applicable")
        self.assertEqual(self.check(result, "components-tested-as-users-perceive-them")["applicability"], "not-applicable")
        self.assertEqual(self.check(result, "data-access-tested-against-real-database")["applicability"], "not-applicable")


class PrefilterTests(unittest.TestCase):
    def test_tests_without_assertions_are_flagged_unless_a_helper_or_query_asserts(self) -> None:
        text = (
            "import { total } from './total';\n"
            "function expectTotal(items, value) { expect(total(items)).toBe(value); }\n"
            "it('runs', () => { total([]); });\n"
            "it('uses a helper', () => { expectTotal([1], 1); });\n"
            "it('finds the heading', () => { render(<Page />); screen.getByRole('heading'); });\n"
        )
        flags = flags_of("src/total.test.ts", text)
        self.assertEqual(flags["runs"], ["no-assertion"])
        self.assertEqual(flags["uses a helper"], [])
        self.assertEqual(flags["finds the heading"], [])

    def test_mock_only_assertions_and_mock_echoes_are_flagged(self) -> None:
        text = (
            "import { getInvoice } from './service';\n"
            "import { repository } from './repository';\n"
            "vi.mock('./repository');\n"
            "it('loads', async () => {\n"
            "  await getInvoice('inv_1');\n"
            "  expect(repository.find).toHaveBeenCalledWith('inv_1');\n"
            "});\n"
            "it('returns the invoice', async () => {\n"
            "  vi.mocked(repository.find).mockResolvedValue(invoice);\n"
            "  expect(await getInvoice('inv_1')).toEqual(invoice);\n"
            "});\n"
            "it('applies the discount', async () => {\n"
            "  vi.mocked(repository.find).mockResolvedValue(invoice);\n"
            "  expect(await getInvoice('inv_1')).toEqual({ id: 'inv_1', total: 90 });\n"
            "});\n"
        )
        flags = flags_of("src/service.test.ts", text)
        self.assertEqual(flags["loads"], ["mock-only"])
        self.assertEqual(flags["returns the invoice"], ["mock-only"])
        self.assertEqual(flags["applies the discount"], [])

    def test_snapshot_only_tests_are_flagged(self) -> None:
        text = "it('renders', () => { const view = render(<A />); expect(view.container).toMatchSnapshot(); });\n"
        self.assertEqual(flags_of("src/a.test.tsx", text)["renders"], ["snapshot-only"])

    def test_shapes_that_pass_when_imports_return_undefined_are_flagged(self) -> None:
        text = (
            "import { total } from './total';\n"
            "import { parse } from 'date-fns';\n"
            "it('negated', () => { expect(total([1])).not.toBe(0); });\n"
            "it('self', () => { expect(total([1, 2])).toEqual(total([1, 2])); });\n"
            "it('derived', () => {\n  const expected = total([1, 2]);\n  expect(total([1, 2])).toEqual(expected);\n});\n"
            "it('literal', () => { expect(true).toBe(true); });\n"
            "it('falsy', () => { expect(total([])).toBeFalsy(); });\n"
            "it('guarded', () => { const result = total([1]); if (result) { expect(result).toBe(1); } });\n"
            "it('looped', () => { totals().forEach((value) => expect(value).toBeGreaterThan(0)); });\n"
            "it('unawaited', () => { expect(load()).resolves.toEqual({ id: 1 }); });\n"
            "it('no matcher', () => { expect(total([1])); });\n"
            "it('real', () => { expect(total([1, 2])).toBe(3); });\n"
            "it('package value', () => { const expected = parse('2026'); expect(total([1])).toEqual(expected); });\n"
            "it('awaited', async () => { await expect(load()).resolves.toEqual({ id: 1 }); });\n"
            "it('counted', () => { expect.assertions(1); const result = total([1]); if (result) { expect(result).toBe(1); } });\n"
        )
        flags = flags_of("src/total.test.ts", text)
        for title in ("negated", "self", "derived", "literal", "falsy", "guarded", "looped", "unawaited", "no matcher"):
            self.assertEqual(flags[title], ["passes-if-undefined"], title)
        for title in ("real", "package value", "awaited", "counted"):
            self.assertEqual(flags[title], [], title)

    def test_node_assert_shapes(self) -> None:
        text = (
            "import assert from 'node:assert/strict';\n"
            "import { total } from '../src/total.js';\n"
            "test('differs', () => { assert.notEqual(total([1]), 0); });\n"
            "test('equals', () => { assert.equal(total([1, 2]), 3); });\n"
            "test('ok', () => { assert.ok(total([1])); });\n"
        )
        flags = flags_of("test/total.test.ts", text)
        self.assertEqual(flags["differs"], ["passes-if-undefined"])
        self.assertEqual(flags["equals"], [])
        self.assertEqual(flags["ok"], [])

    def test_unawaited_playwright_assertions_are_flagged(self) -> None:
        text = (
            "import { test, expect } from '@playwright/test';\n"
            "test('shows the total', async ({ page }) => { await page.goto('/'); expect(page.getByText('Total')).toBeVisible(); });\n"
            "test('awaits the total', async ({ page }) => { await page.goto('/'); await expect(page.getByText('Total')).toBeVisible(); });\n"
        )
        blocks, _ = collector.prefilter("e2e/a.spec.ts", text, collector.mask(text), web_first=True)
        flags = {block.title: [flag for flag, _ in block.flags] for block in blocks}
        self.assertEqual(flags["shows the total"], ["passes-if-undefined"])
        self.assertEqual(flags["awaits the total"], [])

    def test_near_duplicates_are_flagged_and_tables_are_parsed(self) -> None:
        body = "const invoice = createInvoice({ lines: [{ amount: 100, quantity: 2 }], currency: '{0}' });\n  expect(invoice.total).toBe(200);\n"
        text = (
            "import { createInvoice } from './invoice';\n"
            "it('totals two lines', () => {\n  " + body.replace("{0}", "GBP") + "});\n"
            "it('totals two lines again', () => {\n  " + body.replace("{0}", "EUR") + "});\n"
            "it('totals a refund', () => {\n  " + body.replace("{0}", "GBP").replace("toBe(200)", "toBe(-200)") + "});\n"
            "it.each([[1, 1], [2, 4]])('squares %i', (value, expected) => { expect(square(value)).toBe(expected); });\n"
            "it.each`\n  value | expected\n  ${2} | ${4}\n`('doubles $value', ({ value, expected }) => { expect(double(value)).toBe(expected); });\n"
            "it.skip('skipped', () => {});\n"
            "it.todo('later');\n"
        )
        blocks, counts = collector.prefilter("src/invoice.test.ts", text, collector.mask(text))
        flags = {block.title: [flag for flag, _ in block.flags] for block in blocks}
        self.assertEqual(flags["totals two lines"], ["near-duplicate"])
        self.assertEqual(flags["totals two lines again"], ["near-duplicate"])
        self.assertEqual(flags["totals a refund"], [], "different assertions are not duplicates")
        self.assertEqual(flags["squares %i"], [])
        self.assertEqual(flags["doubles $value"], [])
        self.assertNotIn("skipped", flags)
        self.assertNotIn("later", flags)
        self.assertEqual(counts, {"near-duplicate": 2})


class RiskAndSnapshotTests(CollectorTestCase):
    def test_risk_ranking_orders_candidates_and_picks_mutation_targets(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write("src/refunds.ts", "export const refund = (amount: number) => amount;\n")
        self.write("src/format.ts", "export const format = (value: number) => String(value);\n")
        self.write("src/checkout.ts", "import { refund } from './refunds';\nimport { format } from './format';\nexport const checkout = () => format(refund(1));\n")
        self.write("src/admin.ts", "import { refund } from './refunds';\nexport const admin = () => refund(2);\n")
        self.write("src/format.test.ts", "import { format } from './format';\nit('formats', () => { format(1); });\n")
        self.write("src/refunds.test.ts", "import { refund } from './refunds';\nit('refunds', () => { expect(refund(1)).not.toBe(0); });\n")
        self.commit("start")
        for amount in range(3):
            self.write("src/refunds.ts", f"export const refund = (amount: number) => amount + {amount};\n")
            self.commit(f"refund {amount}")
        snapshot = self.collect()["snapshot"]
        ranking = snapshot["riskRanking"]
        self.assertEqual(ranking[0]["path"], "src/refunds.ts")
        self.assertEqual(ranking[0]["commits"], 4)
        self.assertEqual(ranking[0]["fanIn"], 2)
        self.assertEqual(ranking[0]["importedByTests"], ["src/refunds.test.ts"])
        self.assertEqual(snapshot["untestedRiskyFiles"], ["src/admin.ts", "src/checkout.ts"])
        self.assertEqual(snapshot["mutationTargets"][0], "src/refunds.ts")
        self.assertEqual([item["path"] for item in snapshot["preFilter"]["candidates"]], ["src/refunds.test.ts", "src/format.test.ts"])

    def test_months_threshold_is_parsed(self) -> None:
        self.assertEqual(collector.history_months(["months=12"], 6), 12)
        with self.assertRaises(SystemExit):
            collector.history_months(["months=0"], 6)

    def test_module_mocks_flake_signals_and_query_usage(self) -> None:
        self.package({**VITEST_PACKAGE, "dependencies": {"react": "19.0.0"}, "devDependencies": {"vitest": "4.0.0", "@testing-library/react": "16.0.0"}})
        self.write("tsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}\n')
        self.write("src/lib/repository.ts", "export const repository = {};\n")
        self.write(
            "src/Orders.test.tsx",
            "import { render, screen, fireEvent } from '@testing-library/react';\n"
            "vi.mock('@/lib/repository');\n"
            "vi.mock('axios');\n"
            "it('lists', async () => {\n"
            "  render(<Orders />);\n"
            "  await new Promise((resolve) => setTimeout(resolve, 500));\n"
            "  fireEvent.click(screen.getByTestId('more'));\n"
            "  expect(screen.getByRole('list')).toBeTruthy();\n"
            "  expect(Date.now()).toBeGreaterThan(0);\n"
            "});\n",
        )
        self.commit()
        snapshot = self.collect()["snapshot"]
        self.assertEqual(snapshot["moduleMocks"]["internal"], 1)
        self.assertEqual(snapshot["moduleMocks"]["external"], 1)
        self.assertEqual(snapshot["moduleMocks"]["internalExamples"], ["src/Orders.test.tsx:2 — `vi.mock('@/lib/repository'`"])
        self.assertEqual(snapshot["flakeSignals"]["fixedWaits"], 1)
        self.assertEqual(snapshot["flakeSignals"]["realClock"], 1)
        self.assertEqual(snapshot["queryUsage"]["TestId"], 1)
        self.assertEqual(snapshot["queryUsage"]["Role"], 1)
        self.assertEqual(snapshot["interactions"]["fireEvent"], 1)

    def test_a_repository_without_javascript_is_not_applicable(self) -> None:
        self.write("main.py", "print('hello')\n")
        self.commit()
        result = self.collect()
        self.assertEqual(len(result["checks"]), 13)
        self.assertTrue(all(item["applicability"] == "not-applicable" for item in result["checks"].values()))


class MutationTests(CollectorTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.package(VITEST_PACKAGE)
        self.write("src/refunds.ts", "export const refund = (amount: number) => amount;\n")
        self.write("src/refunds.test.ts", "import { refund } from './refunds';\nit('refunds', () => { expect(refund(1)).toBe(1); });\n")
        self.commit()

    def install_stryker(self) -> None:
        self.write("node_modules/.bin/stryker", "#!/bin/sh\n")

    def fake_run(self, statuses: list[str], recorded: list[Any]) -> Any:
        root = self.root

        def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
            config = Path(command[2]).read_text(encoding="utf-8")
            recorded.append((command, options, config))
            report = {
                "files": {
                    "src/refunds.ts": {
                        "mutants": [
                            {"mutatorName": "ArithmeticOperator", "status": status, "location": {"start": {"line": 1, "column": 1}}}
                            for status in statuses
                        ]
                    }
                }
            }
            (root / collector.MUTATION_DIRECTORY / "mutation.json").write_text(json.dumps(report), encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "", "")

        return run

    def test_without_the_flag_or_the_tool_the_check_is_not_evaluated(self) -> None:
        check = self.check(self.collect(), "mutations-in-riskiest-files-caught")
        self.assertEqual(check, {"evaluationState": "not-evaluated", "reason": "Mutation testing runs only with --with-mutation."})
        check = self.check(self.collect(with_mutation=True), "mutations-in-riskiest-files-caught")
        self.assertEqual(check["evaluationState"], "not-evaluated")
        self.assertIn("Stryker is not installed", check["reason"])

    def test_scores_grade_present_partial_and_violation(self) -> None:
        self.install_stryker()
        for statuses, expected in (
            (["Killed"] * 4 + ["Timeout"], "present"),
            (["Killed"] * 7 + ["Survived"] * 3, "partial"),
            (["Killed", "Survived", "NoCoverage", "CompileError"], "violation"),
        ):
            recorded: list[Any] = []
            check = self.check(self.collect(with_mutation=True, run=self.fake_run(statuses, recorded)), "mutations-in-riskiest-files-caught")
            self.assertEqual(check["status"], expected, statuses)
            command, options, config = recorded[0]
            self.assertEqual(command[1], "run")
            self.assertEqual(options["cwd"], str(self.root))
            self.assertIn('"mutate": ["src/refunds.ts"]', config)
            self.assertIn("const base = {};", config)
            self.assertFalse((self.root / collector.MUTATION_DIRECTORY / "stryker.audit.config.mjs").exists())
        self.assertIn("mutation score 33.3%", check["evidence"][0])
        self.assertIn("src/refunds.ts:1 — ArithmeticOperator mutant survived", check["evidence"])

    def test_the_projects_own_configuration_is_kept(self) -> None:
        self.install_stryker()
        self.write("stryker.config.json", '{"testRunner": "vitest", // runner\n "concurrency": 2}\n')
        recorded: list[Any] = []
        self.collect(with_mutation=True, run=self.fake_run(["Killed"], recorded))
        self.assertIn('const base = {"testRunner": "vitest", "concurrency": 2};', recorded[0][2])

    def test_a_timeout_or_a_missing_report_is_not_evaluated(self) -> None:
        self.install_stryker()

        def timeout(command: list[str], **options: Any) -> Any:
            raise subprocess.TimeoutExpired(command, collector.MUTATION_TIMEOUT_SECONDS)

        check = self.check(self.collect(with_mutation=True, run=timeout), "mutations-in-riskiest-files-caught")
        self.assertEqual(check["evaluationState"], "not-evaluated")
        self.assertIn("did not finish", check["reason"])

        def failing(command: list[str], **options: Any) -> Any:
            return subprocess.CompletedProcess(command, 1, "", "Error: no test runner\n")

        check = self.check(self.collect(with_mutation=True, run=failing), "mutations-in-riskiest-files-caught")
        self.assertEqual(check["evaluationState"], "not-evaluated")
        self.assertIn("exited 1 without a report: Error: no test runner", check["reason"])

    def test_the_command_line_reads_the_enrichment_flag(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            collector.main(["--repository", str(self.root), "--enrichment=--with-mutation", "--threshold=months=3"])
        result = json.loads(output.getvalue())
        self.assertIn("Stryker is not installed", result["checks"][CHECK + "mutations-in-riskiest-files-caught"]["reason"])
        self.assertEqual(result["snapshot"]["historyWindowMonths"], 3)


class ProtocolTests(CollectorTestCase):
    def test_evidence_survives_protocol_verification(self) -> None:
        protocol_spec = importlib.util.spec_from_file_location(
            "audit_run_for_testing_collector_tests", ROOT / "audit-protocol" / "scripts" / "audit_run.py"
        )
        assert protocol_spec and protocol_spec.loader
        protocol = importlib.util.module_from_spec(protocol_spec)
        sys.modules[protocol_spec.name] = protocol
        protocol_spec.loader.exec_module(protocol)
        self.package({
            "name": "web", "scripts": {"test": "vitest run --coverage"}, "dependencies": {"react": "19.0.0"},
            "devDependencies": {"vitest": "4.0.0", "@testing-library/react": "16.0.0", "@testing-library/jest-dom": "6.0.0"},
        })
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 80 } } } };\n")
        self.write("eslint.config.js", "import testingLibrary from 'eslint-plugin-testing-library';\nexport default [testingLibrary.configs['flat/react']];\n")
        self.write("src/my folder/a.test.tsx", "it.only('adds', () => { expect(1 + 1).toBe(2); });\nit.skip('later', () => {});\n")
        self.write(".github/workflows/ci.yml", "jobs:\n  test:\n    steps:\n      - run: npm test -- --coverage\n")
        self.commit()
        result = self.collect()
        for name, item in result["checks"].items():
            for entry in item.get("evidence", []):
                protocol.verify_evidence(self.root, entry)
        self.assertEqual(self.check(result, "no-focused-or-skipped-tests")["evidence"][0], "`src/my folder/a.test.tsx`:1 — `it.only`")


if __name__ == "__main__":
    unittest.main()
