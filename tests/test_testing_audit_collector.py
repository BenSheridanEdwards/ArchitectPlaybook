from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
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
        self.assertIn('package.json — `"test": "vitest`', check["evidence"])

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

    def test_disabled_and_non_enabling_coverage_flags_do_not_claim_collection(self) -> None:
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 100 } } } };\n")
        for source in ("script", "workflow"):
            for flag in (
                "--coverage=false", "--coverage false", "--coverage='false'", '--coverage "false"', "--coverage.enabled=false",
                "--coverage.enabled false", "--coverage.provider=v8",
                "--coverage.reporter=json", "--coverage.thresholds.lines=100",
                "--coverage --coverage=false", "--coverage.enabled=true --coverage.enabled=false",
            ):
                with self.subTest(source=source, flag=flag):
                    command = f"vitest run {flag}"
                    self.package({**VITEST_PACKAGE, "scripts": {"test": command if source == "script" else "vitest run"}})
                    self.write(".github/workflows/ci.yml", f"jobs:\n  test:\n    steps:\n      - run: {command if source == 'workflow' else 'npm test'}\n")
                    self.commit()
                    result = self.collect()
                    check = self.check(result, "coverage-thresholds-configured")
                    self.assertEqual(check["status"], "partial", check)
                    self.assertEqual(result["snapshot"]["coverage"]["collectedBy"], [])

    def test_explicit_coverage_enable_flags_still_count_in_scripts_and_workflows(self) -> None:
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 100 } } } };\n")
        for source in ("script", "workflow"):
            for flag in (
                "--coverage", "--coverage=true", "--coverage true", "--coverage='true'", '--coverage "true"',
                "--coverage.enabled", "--coverage.enabled=true", "--coverage.enabled true",
                "--coverage=false && vitest run --coverage",
                "--coverage && vitest run --coverage=false",
            ):
                with self.subTest(source=source, flag=flag):
                    command = f"vitest run {flag}"
                    self.package({**VITEST_PACKAGE, "scripts": {"test": command if source == "script" else "vitest run"}})
                    self.write(".github/workflows/ci.yml", f"jobs:\n  test:\n    steps:\n      - run: {command if source == 'workflow' else 'npm test'}\n")
                    self.commit()
                    self.assertEqual(self.check(self.collect(), "coverage-thresholds-configured")["status"], "present")

    def test_duplicate_coverage_options_do_not_claim_collection(self) -> None:
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 100 } } } };\n")
        for source in ("script", "workflow"):
            for flags in (
                "--coverage=false --coverage=true",
                "--coverage.enabled=false --coverage.enabled=true",
                "--coverage --testNamePattern 'adds|subtracts' --coverage=false",
                "--coverage --testNamePattern='adds|subtracts' --coverage=false",
                "--coverage --testNamePattern '|' --coverage=false",
                "--coverage --testNamePattern 'adds;subtracts' --coverage=false",
                "--coverage --testNamePattern 'adds&subtracts' --coverage=false",
            ):
                with self.subTest(source=source, flags=flags):
                    command = f"vitest run {flags}"
                    self.package({**VITEST_PACKAGE, "scripts": {"test": command if source == "script" else "vitest run"}})
                    self.write(".github/workflows/ci.yml", f"jobs:\n  test:\n    steps:\n      - run: {command if source == 'workflow' else 'npm test'}\n")
                    self.commit()
                    result = self.collect()
                    self.assertEqual(self.check(result, "coverage-thresholds-configured")["status"], "partial")
                    self.assertEqual(result["snapshot"]["coverage"]["collectedBy"], [])

    def test_workflow_step_names_do_not_turn_wrappers_into_collection(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 100 } } } };\n")
        for wrapper in ("c8", "nyc"):
            with self.subTest(wrapper=wrapper):
                self.write(".github/workflows/ci.yml", f"jobs:\n  test:\n    steps:\n      - name: Replace {wrapper} coverage with Vitest\n        run: npm test\n")
                self.commit()
                result = self.collect()
                self.assertEqual(self.check(result, "coverage-thresholds-configured")["status"], "partial")
                self.assertEqual(result["snapshot"]["coverage"]["collectedBy"], [])

    def test_quoted_workflow_run_values_keep_their_collection_semantics(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 100 } } } };\n")
        for quote in ("'", '\"'):
            for flag, status in (("--coverage", "present"), ("--coverage=false", "partial"), ("--coverage.provider=v8", "partial")):
                with self.subTest(quote=quote, flag=flag):
                    self.write(".github/workflows/ci.yml", f"jobs:\n  test:\n    steps:\n      - run: {quote}vitest run {flag}{quote}\n")
                    self.commit()
                    self.assertEqual(self.check(self.collect(), "coverage-thresholds-configured")["status"], status)

    def test_node_coverage_and_c8_nyc_wrappers_still_count(self) -> None:
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 100 } } } };\n")
        for command in ("node --test --experimental-test-coverage", "c8 vitest run", "nyc vitest run"):
            with self.subTest(command=command):
                self.package({**VITEST_PACKAGE, "scripts": {"test": command}})
                self.commit()
                self.assertEqual(self.check(self.collect(), "coverage-thresholds-configured")["status"], "present")

    def test_disabled_coverage_verdict_is_partial_through_the_cli(self) -> None:
        self.package({**VITEST_PACKAGE, "scripts": {"test": "vitest run --coverage=false"}})
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 100 } } } };\n")
        self.commit()
        process = subprocess.run(
            [sys.executable, str(COLLECTOR), "--repository", str(self.root)],
            check=True, capture_output=True, text=True,
        )
        result = json.loads(process.stdout)
        self.assertEqual(self.check(result, "coverage-thresholds-configured")["status"], "partial")
        self.assertEqual(result["snapshot"]["coverage"]["collectedBy"], [])

    def test_commented_thresholds_do_not_count(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write("vitest.config.ts", "export default { test: { coverage: { provider: 'v8' /* thresholds: { lines: 80 } */ } } };\n")
        self.commit()
        self.assertEqual(self.check(self.collect(), "coverage-thresholds-configured")["status"], "missing")

    def test_empty_thresholds_are_not_a_floor(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write("vitest.config.ts", "export default { test: { coverage: { enabled: true, thresholds: {} /* lines: 80 */ } } };\n")
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
        self.write("src/test/helpers.ts", "import { admin } from '../admin';\nexport const run = () => admin();\n")
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
        self.assertEqual(snapshot["untestedRiskyFiles"], ["src/checkout.ts"], "a test helper's imports count as tested")
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
            raise subprocess.TimeoutExpired(command, options["timeout"])

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


class ReviewRegressionTests(CollectorTestCase):
    """Regression tests for the review of the rewrite; each fails on the collector before its fix."""

    # 1. Stryker must mutate a sandbox copy and write only under the audit's folder.
    def generated_configuration(self, project: str) -> str:
        self.write("stryker.config.json", project)
        return collector.mutation_configuration(self.root, ["src/refunds.ts"])

    def test_the_mutation_configuration_overrides_options_that_write_into_the_repository(self) -> None:
        text = self.generated_configuration('{"testRunner": "vitest", "inPlace": true, "fileLogLevel": "trace", "reporters": ["html", "dashboard"]}')
        overrides = json.loads(re.search(r"const overrides = (\{.*\});", text).group(1))
        self.assertIs(overrides["inPlace"], False)
        self.assertEqual(overrides["fileLogLevel"], "off")
        self.assertEqual(overrides["reporters"], ["json"])
        for key in ("tempDirName", "incrementalFile"):
            self.assertTrue(overrides[key].startswith(".architect-audits/testing-audit/mutation/"), key)
        self.assertTrue(overrides["jsonReporter"]["fileName"].startswith(".architect-audits/testing-audit/mutation/"))
        self.assertIn("{ ...base, ...overrides,", text, "overrides must come after the project's settings")

    @unittest.skipUnless(shutil.which("node"), "node is needed to evaluate the generated configuration")
    def test_node_reads_the_generated_configuration_with_the_sandbox_forced(self) -> None:
        self.write("stryker.config.mjs", "export default { testRunner: 'vitest', inPlace: true, fileLogLevel: 'trace', concurrency: 2 };\n")
        config = self.root / "generated.mjs"
        config.write_text(collector.mutation_configuration(self.root, ["src/refunds.ts"]), encoding="utf-8")
        loaded = subprocess.run(
            ["node", "--input-type=module", "-e", f"import c from {json.dumps(config.as_uri())}; console.log(JSON.stringify(c));"],
            capture_output=True, text=True, check=True,
        )
        effective = json.loads(loaded.stdout)
        self.assertIs(effective["inPlace"], False)
        self.assertEqual(effective["fileLogLevel"], "off")
        self.assertEqual(effective["testRunner"], "vitest")
        self.assertEqual(effective["concurrency"], 2)

    # 2. Runner recognition.
    def test_bun_create_react_app_angular_tap_and_uvu_are_recognised(self) -> None:
        cases = [
            ({"scripts": {"test": "bun test"}}, "src/a.test.ts", "import { test, expect } from 'bun:test';\ntest('adds', () => { expect(1 + 1).toBe(2); });\n", "bun:test"),
            ({"scripts": {"test": "react-scripts test"}, "dependencies": {"react-scripts": "5.0.1"}}, "src/App.test.js", "it('adds', () => { expect(1 + 1).toBe(2); });\n", "react-scripts"),
            ({"scripts": {"test": "ng test"}, "devDependencies": {"karma": "6.4.0", "jasmine-core": "5.0.0"}}, "src/app/app.spec.ts", "describe('app', () => { it('adds', () => { expect(1 + 1).toBe(2); }); });\n", "angular-cli"),
            ({"scripts": {"test": "tap"}, "devDependencies": {"tap": "21.0.0"}}, "test/math.ts", "import t from 'tap';\nt.test('adds', (t) => { t.equal(1 + 1, 2); t.end(); });\n", "tap"),
            ({"scripts": {"test": "uvu tests"}, "devDependencies": {"uvu": "0.5.6"}}, "tests/math.js", "import { test } from 'uvu';\nimport * as assert from 'uvu/assert';\ntest('adds', () => { assert.is(1 + 1, 2); });\ntest.run();\n", "uvu"),
        ]
        for manifest, path, text, runner in cases:
            with self.subTest(runner=runner):
                for existing in list(self.root.iterdir()):
                    if existing.name != ".git":
                        shutil.rmtree(existing) if existing.is_dir() else existing.unlink()
                self.package({"name": "app", **manifest})
                self.write(path, text)
                self.write("src/index.ts", "export const one = 1;\n")
                self.commit(runner)
                result = self.collect()
                self.assertIn(runner, result["snapshot"]["runners"])
                self.assertEqual(self.check(result, "tests-run-under-configured-runner")["status"], "present")

    def test_node_test_files_are_found_outside_test_folders(self) -> None:
        self.package({"name": "lib", "scripts": {"test": "node --test"}})
        self.write("src/math.js", "export const add = (a, b) => a + b;\n")
        self.write("src/math.check.js", "import { test } from 'node:test';\nimport assert from 'node:assert';\nimport { add } from './math.js';\ntest('adds', () => { assert.equal(add(1, 2), 3); });\n")
        self.write("lib/parse_test.js", "import { test } from 'node:test';\ntest('parses', () => {});\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["snapshot"]["testFiles"]["total"], 2)
        self.assertEqual(self.check(result, "tests-run-under-configured-runner")["status"], "present")

    def test_an_unrecognised_test_script_is_not_evaluated_rather_than_missing(self) -> None:
        self.package({"name": "app", "scripts": {"test": "./scripts/run-suite.sh --all"}})
        self.write("src/index.ts", "export const one = 1;\n")
        self.commit()
        check = self.check(self.collect(), "tests-run-under-configured-runner")
        self.assertEqual(check["evaluationState"], "not-evaluated")
        self.assertIn("./scripts/run-suite.sh --all", check["reason"])
        self.package({"name": "app", "scripts": {"test": "echo \"Error: no test specified\" && exit 1"}})
        self.commit()
        self.assertEqual(self.check(self.collect(), "tests-run-under-configured-runner")["status"], "missing")

    # 3. Repositories in other languages.
    def test_a_tooling_only_package_json_makes_the_audit_not_applicable(self) -> None:
        self.package({"name": "service", "private": True, "devDependencies": {"husky": "9.0.0", "@commitlint/cli": "19.0.0"}})
        self.write("commitlint.config.js", "module.exports = { extends: ['@commitlint/config-conventional'] };\n")
        self.write(".husky/commit-msg.js", "console.log('hook');\n")
        self.write("app/service.py", "def add(a, b):\n    return a + b\n")
        self.write("tests/test_service.py", "def test_add():\n    assert 1 + 1 == 2\n")
        self.commit()
        result = self.collect()
        self.assertTrue(all(item["applicability"] == "not-applicable" for item in result["checks"].values()))
        self.assertIn("audit-not-applicable", self.check(result, "tests-run-under-configured-runner")["reason"])

    # 4. Chai chains.
    def test_chai_assertions_are_read_as_matchers(self) -> None:
        text = (
            "import { expect } from 'chai';\n"
            "import { add, find, notify } from '../src/math.js';\n"
            "it('adds', () => { expect(add(2, 3)).to.equal(5); });\n"
            "it('is positive', () => { expect(add(2, 3) > 0).to.be.true; });\n"
            "it('deep equals', () => { expect(add(1, 1)).to.deep.equal(2); });\n"
            "it('should style', () => { add(2, 3).should.equal(5); });\n"
            "it('finds nothing', () => { expect(find('x')).to.be.undefined; });\n"
            "it('does not exist', () => { expect(find('x')).to.not.exist; });\n"
            "it('notifies', () => { notify(); expect(spy).to.have.been.calledWith('x'); });\n"
        )
        flags = flags_of("test/math.test.js", text)
        for title in ("adds", "is positive", "deep equals", "should style"):
            self.assertEqual(flags[title], [], title)
        self.assertEqual(flags["finds nothing"], ["passes-if-undefined"])
        self.assertEqual(flags["does not exist"], ["passes-if-undefined"])
        self.assertEqual(flags["notifies"], ["mock-only"])

    # 5, A, B. The ESLint reader.
    def lint_project(self) -> None:
        self.package({
            **VITEST_PACKAGE, "dependencies": {"react": "19.0.0"},
            "devDependencies": {"vitest": "4.0.0", "@testing-library/react": "16.0.0", "@testing-library/jest-dom": "6.0.0"},
        })
        self.write("src/ui/Button.test.tsx", "import { render, screen } from '@testing-library/react';\nit('shows', () => { render(<b />); expect(screen.getByRole('button')).toBeTruthy(); });\n")

    def lint_results(self) -> tuple[dict[str, Any], dict[str, Any]]:
        self.commit()
        result = self.collect()
        return self.check(result, "testing-library-lint-rules-enforced"), self.check(result, "jest-dom-lint-rules-enforced")

    def test_unquoted_yaml_configuration_is_read(self) -> None:
        self.lint_project()
        self.write(
            ".eslintrc.yml",
            "extends:\n  - eslint:recommended\n  - plugin:testing-library/react\n  - plugin:jest-dom/recommended\n"
            "rules:\n  testing-library/prefer-user-event: error\n  testing-library/prefer-explicit-assert: warn  # explicit\n",
        )
        testing_library, jest_dom = self.lint_results()
        self.assertEqual(testing_library["status"], "present")
        self.assertEqual(jest_dom["status"], "present")

    def test_an_unreadable_shared_configuration_is_not_evaluated(self) -> None:
        self.lint_project()
        package = json.loads((self.root / "package.json").read_text())
        package["eslintConfig"] = {"extends": ["react-app", "react-app/jest"]}
        self.package(package)
        testing_library, jest_dom = self.lint_results()
        self.assertEqual(testing_library["evaluationState"], "not-evaluated")
        self.assertIn("react-app/jest", testing_library["reason"])
        self.assertEqual(jest_dom["evaluationState"], "not-evaluated")

    def test_presets_scoped_away_from_test_files_or_left_unused_do_not_count(self) -> None:
        self.lint_project()
        self.write(
            "eslint.config.js",
            "import testingLibrary from 'eslint-plugin-testing-library';\n"
            "import jestDom from 'eslint-plugin-jest-dom';\n"
            "const unused = testingLibrary.configs['flat/react'];\n"
            "export default [{ files: ['scripts/**/*.js'], ...jestDom.configs['flat/recommended'] }];\n",
        )
        testing_library, jest_dom = self.lint_results()
        self.assertEqual(testing_library["status"], "missing")
        self.assertEqual(jest_dom["status"], "missing")

    def test_settings_apply_in_configuration_order(self) -> None:
        self.lint_project()
        self.write(
            "eslint.config.js",
            "import jestDom from 'eslint-plugin-jest-dom';\n"
            "export default [{ rules: { 'jest-dom/prefer-checked': 'off' } }, jestDom.configs['flat/recommended']];\n",
        )
        _, jest_dom = self.lint_results()
        self.assertEqual(jest_dom["status"], "present", "a later preset turns the rule back on")
        self.write(
            "eslint.config.js",
            "import jestDom from 'eslint-plugin-jest-dom';\n"
            "export default [jestDom.configs['flat/recommended'], { rules: { 'jest-dom/prefer-checked': 'off' } }];\n",
        )
        _, jest_dom = self.lint_results()
        self.assertEqual(jest_dom["status"], "partial")
        self.assertIn("jest-dom/prefer-checked", jest_dom["gap"])

    def test_an_own_rules_key_replaces_a_spread_presets_rules(self) -> None:
        self.lint_project()
        self.write(
            "eslint.config.js",
            "import jestDom from 'eslint-plugin-jest-dom';\n"
            "export default [{ ...jestDom.configs['flat/recommended'], rules: { 'jest-dom/prefer-checked': 'error' } }];\n",
        )
        _, jest_dom = self.lint_results()
        self.assertEqual(jest_dom["status"], "partial")
        self.assertIn("jest-dom/prefer-empty", jest_dom["gap"])

    def test_an_imported_shared_configuration_is_not_evaluated(self) -> None:
        self.lint_project()
        self.write("eslint.config.js", "import company from '@acme/eslint-config';\nexport default [...company];\n")
        testing_library, _ = self.lint_results()
        self.assertEqual(testing_library["evaluationState"], "not-evaluated")
        self.assertIn("company", testing_library["reason"])

    # 6. Evidence must survive the protocol when scripts contain escaped quotes.
    def test_scripts_with_escaped_quotes_survive_audit_run_begin(self) -> None:
        self.write("package.json", '{\n  "name": "monorepo",\n  "private": true,\n  "scripts": {\n'
                   '    "test": "pnpm -r --filter \\"./packages/**\\" test",\n'
                   '    "test:node": "node --import \\"tsx\\" --test"\n  }\n}\n')
        self.write("pnpm-workspace.yaml", "packages:\n  - packages/*\n")
        self.write("packages/core/package.json", json.dumps({"name": "@acme/core", "scripts": {"test": "node --import \"tsx\" --test"}}))
        self.write("packages/core/src/index.ts", "export const one = 1;\n")
        self.write("packages/core/src/index.test.ts", "import { test } from 'node:test';\nimport assert from 'node:assert';\nimport { one } from './index';\ntest('one', () => { assert.equal(one, 1); });\n")
        self.commit()
        protocol_spec = importlib.util.spec_from_file_location("audit_run_for_escaped_quotes", ROOT / "audit-protocol" / "scripts" / "audit_run.py")
        assert protocol_spec and protocol_spec.loader
        protocol = importlib.util.module_from_spec(protocol_spec)
        sys.modules[protocol_spec.name] = protocol
        protocol_spec.loader.exec_module(protocol)
        for entry in self.check(self.collect(), "tests-run-under-configured-runner")["evidence"]:
            protocol.verify_evidence(self.root, entry)
        completed = subprocess.run(
            [sys.executable, str(ROOT / "audit-protocol" / "scripts" / "audit_run.py"), "--repository", str(self.root), "begin", "testing-audit"],
            capture_output=True, text=True, check=True,
        )
        self.assertNotIn("rejected", completed.stdout)
        staged = json.loads((self.root / ".architect-audits" / "testing-audit" / ".staging" / "run.json").read_text())
        runner = next(check for check in staged["checks"] if check["checkId"] == CHECK + "tests-run-under-configured-runner")
        self.assertEqual(runner["status"], "present")
        self.assertEqual(runner["recordedBy"], "collector")

    # 7. The pre-filter is linear, and Stryker's budget fits the protocol's timeout.
    def test_a_huge_test_is_scanned_quickly_and_capped(self) -> None:
        body = "".join(f"  expect(square({index})).toBe({index * index});\n" for index in range(6000))
        text = "import { square } from './square';\nit('squares', () => {\n" + body + "});\n"
        began = time.monotonic()
        blocks, _ = collector.prefilter("src/square.test.ts", text, collector.mask(text))
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(blocks[0].flags, [])

    def test_the_mutation_budget_leaves_room_inside_the_protocol_timeout(self) -> None:
        self.assertEqual(collector.mutation_budget(5), collector.MUTATION_TIMEOUT_CAP_SECONDS)
        self.assertLessEqual(collector.mutation_budget(200) + 200 + collector.MUTATION_MARGIN_SECONDS, collector.PROTOCOL_COLLECTOR_TIMEOUT_SECONDS)
        self.write("node_modules/.bin/stryker", "#!/bin/sh\n")
        check = collector.mutation_check(self.root, True, ["src/a.ts"], budget=collector.mutation_budget(480))
        self.assertEqual(check["evaluationState"], "not-evaluated")
        self.assertIn("too little for Stryker", check["reason"])

    # 8. Monorepo resolution.
    def test_workspace_packages_and_inherited_aliases_resolve(self) -> None:
        self.package({"name": "monorepo", "private": True, "workspaces": ["packages/*"], "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "4.0.0"}})
        self.write("tsconfig.base.json", '{"compilerOptions": {"baseUrl": ".", "paths": {"@shared/*": ["packages/shared/src/*"]}}}\n')
        self.write("packages/app/tsconfig.json", '{"extends": "../../tsconfig.base.json"}\n')
        self.write("packages/core/package.json", json.dumps({"name": "@acme/core", "main": "dist/index.js"}))
        self.write("packages/core/src/index.ts", "export const core = 1;\n")
        self.write("packages/shared/package.json", json.dumps({"name": "@acme/shared"}))
        self.write("packages/shared/src/money.ts", "export const money = 1;\n")
        self.write("packages/app/package.json", json.dumps({"name": "@acme/app"}))
        self.write("packages/app/src/main.ts", "import { core } from '@acme/core';\nimport { money } from '@shared/money';\nexport const app = core + money;\n")
        self.write("packages/app/src/main.test.ts", "import { core } from '@acme/core';\nimport { money } from '@shared/money';\nit('adds', () => { expect(core + money).toBe(2); });\n")
        self.commit()
        ranking = {item["path"]: item for item in self.collect()["snapshot"]["riskRanking"]}
        self.assertEqual(ranking["packages/core/src/index.ts"]["fanIn"], 1)
        self.assertEqual(ranking["packages/core/src/index.ts"]["importedByTests"], ["packages/app/src/main.test.ts"])
        self.assertEqual(ranking["packages/shared/src/money.ts"]["importedByTests"], ["packages/app/src/main.test.ts"])

    # Notes: workspace globs, the discovery budget, large files, and mutation reporting.
    def test_bad_workspace_globs_are_ignored_without_escaping_the_repository(self) -> None:
        outside = self.root.parent / "outside"
        (outside / "pkg").mkdir(parents=True)
        (outside / "pkg" / "package.json").write_text('{"name": "outside", "devDependencies": {"jest": "30.0.0"}}', encoding="utf-8")
        self.package({"name": "monorepo", "workspaces": ["", "/tmp/*", "../outside/*", "C:/x/*", "packages/*"]})
        self.write("packages/a/package.json", json.dumps({"name": "a", "scripts": {"test": "vitest run"}, "devDependencies": {"vitest": "4.0.0"}}))
        self.write("packages/a/src/a.test.ts", "it('adds', () => { expect(1 + 1).toBe(2); });\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["snapshot"]["runners"], ["vitest"])

    def test_the_candidate_list_holds_the_whole_discovery_budget(self) -> None:
        self.assertGreaterEqual(collector.MAX_CANDIDATES, 100)
        detection = (ROOT / "testing-audit" / "references" / "detection.md").read_text(encoding="utf-8")
        self.assertIn(f"up to the {collector.MAX_CANDIDATES}", detection)

    def test_large_files_degrade_the_focus_check_and_are_listed(self) -> None:
        self.package(VITEST_PACKAGE)
        self.write("src/a.test.ts", "it('adds', () => { expect(1 + 1).toBe(2); });\n")
        self.write("src/huge.test.ts", "it.only('hidden', () => {});\n" + "// filler\n" * 250_000)
        self.commit()
        result = self.collect()
        check = self.check(result, "no-focused-or-skipped-tests")
        self.assertIn("src/huge.test.ts", check["degradedReason"])
        self.assertEqual(result["snapshot"]["skippedLargeFiles"]["examples"], ["src/huge.test.ts"])

    def test_mutation_failures_prefer_the_error_line_and_cite_survivors_first(self) -> None:
        self.assertEqual(collector.failure_line("starting\nERROR Cannot find package 'typescript'\nNode.js v26.7.0\n"), "ERROR Cannot find package 'typescript'")
        report = {"files": {"src/a.ts": {"mutants": [
            {"mutatorName": "A", "status": "NoCoverage", "location": {"start": {"line": 1}}},
            {"mutatorName": "B", "status": "Survived", "location": {"start": {"line": 2}}},
        ]}}}
        _, _, survivors = collector.mutation_score(report, self.root)
        self.assertEqual([line for _, line, _ in survivors], [2, 1])


class SecondReviewRegressionTests(CollectorTestCase):
    """Regression tests for the second review of the fixes; each fails on the collector before its fix."""

    lint_project = ReviewRegressionTests.lint_project
    lint_results = ReviewRegressionTests.lint_results

    def protocol(self) -> Any:
        protocol_spec = importlib.util.spec_from_file_location("audit_run_for_second_review", ROOT / "audit-protocol" / "scripts" / "audit_run.py")
        assert protocol_spec and protocol_spec.loader
        protocol = importlib.util.module_from_spec(protocol_spec)
        sys.modules[protocol_spec.name] = protocol
        protocol_spec.loader.exec_module(protocol)
        return protocol

    # Codex 1. The skill must not promise that mutation testing writes only under the audit's folder.
    def test_the_skill_says_mutation_runs_project_code_and_asks_first(self) -> None:
        skill = (ROOT / "testing-audit" / "SKILL.md").read_text(encoding="utf-8")
        detection = (ROOT / "testing-audit" / "references" / "detection.md").read_text(encoding="utf-8")
        for text in (skill, detection):
            text = " ".join(text.split())
            self.assertIsNone(re.search(r"writing only under|The only writes are under", text), "the docs still promise confined writes")
            self.assertIn("can write wherever that code writes", text)
        self.assertRegex(skill, r"Before passing `--enrichment with-mutation`[^\n]*ask")

    # Codex 2. A spread copies `files`, `ignores`, and `rules`, in JavaScript spread order.
    def test_a_spread_keeps_the_scope_of_the_spread_object(self) -> None:
        self.lint_project()
        preamble = "import jd from 'eslint-plugin-jest-dom';\n"
        cases = [
            ("const scoped = { files: ['other/**/*.test.tsx'], ...jd.configs['flat/recommended'] };\nexport default [{ ...scoped }];\n", "missing"),
            ("const scoped = { files: ['src/**/*.test.tsx'], ...jd.configs['flat/recommended'] };\nexport default [{ ...scoped }];\n", "present"),
            ("const scoped = { files: ['src/**/*.test.tsx'], ...jd.configs['flat/recommended'] };\nexport default [{ ...scoped, files: ['other/**'] }];\n", "missing"),
            ("const scoped = { files: ['other/**'], ...jd.configs['flat/recommended'] };\nexport default [{ files: ['src/**'], ...scoped }];\n", "missing"),
            ("const scoped = { ignores: ['src/ui/**'], ...jd.configs['flat/recommended'] };\nexport default [{ ...scoped }];\n", "missing"),
        ]
        for config, status in cases:
            with self.subTest(config=config):
                self.write("eslint.config.js", preamble + config)
                _, jest_dom = self.lint_results()
                self.assertEqual(jest_dom["status"], status)

    # Codex 3. A one-line legacy JavaScript configuration is read, and an unreadable `extends` is not called missing.
    def test_an_inline_legacy_javascript_configuration_is_read(self) -> None:
        self.lint_project()
        self.write(".eslintrc.cjs", "module.exports = { extends: ['plugin:jest-dom/recommended'] };\n")
        _, jest_dom = self.lint_results()
        self.assertEqual(jest_dom["status"], "present")
        self.write(".eslintrc.cjs", "module.exports = { root: true, extends: 'plugin:jest-dom/recommended' };\n")
        _, jest_dom = self.lint_results()
        self.assertEqual(jest_dom["status"], "present")
        self.write(".eslintrc.cjs", "const shared = require('./shared');\nmodule.exports = { extends: shared };\n")
        _, jest_dom = self.lint_results()
        self.assertEqual(jest_dom["evaluationState"], "not-evaluated")
        self.write(".eslintrc.cjs", "module.exports = require('./base');\n")
        _, jest_dom = self.lint_results()
        self.assertEqual(jest_dom["evaluationState"], "not-evaluated")

    # Codex 4. An unrecognised wrapper may run a declared runner's tests.
    def test_an_unrecognised_wrapper_with_a_runner_and_tests_is_not_evaluated(self) -> None:
        self.package({"name": "app", "scripts": {"test": "node scripts/run-tests.js"}, "devDependencies": {"vitest": "4.0.0"}})
        self.write("src/a.test.ts", "import { it, expect } from 'vitest';\nit('adds', () => { expect(1 + 1).toBe(2); });\n")
        self.write("src/index.ts", "export const one = 1;\n")
        self.commit()
        check = self.check(self.collect(), "tests-run-under-configured-runner")
        self.assertEqual(check.get("evaluationState"), "not-evaluated")
        self.assertIn("node scripts/run-tests.js", check["reason"])

    # Claude 5. The near-duplicate scan is bounded.
    def test_many_near_identical_snapshot_tests_collect_in_seconds(self) -> None:
        self.package(VITEST_PACKAGE)
        tests = []
        for index in range(200):
            props = " ".join(f'prop{number}="value-{number}-{index % 7}"' for number in range(80))
            tests.append(f"it('renders card {index}', () => {{\n  const {{ asFragment }} = render(<Card {props} />);\n  expect(asFragment()).toMatchSnapshot();\n}});\n")
        text = "import { render } from '@testing-library/react';\nimport { Card } from './Card';\n" + "".join(tests)
        self.assertGreater(len(text) / 200, 1_500)
        self.write("src/Card.test.tsx", text)
        self.write("src/Card.tsx", "export const Card = () => null;\n")
        self.commit()
        began = time.monotonic()
        result = self.collect()
        self.assertLess(time.monotonic() - began, 5)
        self.assertEqual(result["snapshot"]["preFilter"]["byFlag"]["near-duplicate"], 200)

    # Claude 6. A coverage floor needs a metric above zero.
    def test_zero_coverage_floors_do_not_count(self) -> None:
        cases = [
            ({}, "vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 0, branches: 0 }, enabled: true } } };\n", "missing"),
            ({}, "vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 0, branches: 50 }, enabled: true } } };\n", "present"),
            ({}, "vitest.config.ts", "export default { test: { coverage: { lines: 0, enabled: true } } };\n", "missing"),
            ({"devDependencies": {"jest": "30.0.0"}, "scripts": {"test": "jest"}}, "jest.config.js", "module.exports = { collectCoverage: true, coverageThreshold: {} };\n", "missing"),
            ({"devDependencies": {"jest": "30.0.0"}, "scripts": {"test": "jest"}}, "jest.config.js", "module.exports = { collectCoverage: true, coverageThreshold: { global: { lines: 0 } } };\n", "missing"),
            ({"devDependencies": {"jest": "30.0.0"}, "scripts": {"test": "jest"}}, "jest.config.js", "module.exports = { collectCoverage: true, coverageThreshold: { global: { lines: 80 } } };\n", "present"),
            ({"devDependencies": {"jest": "30.0.0"}, "scripts": {"test": "jest"}, "jest": {"collectCoverage": True, "coverageThreshold": {"global": {"lines": 0}}}}, None, "", "missing"),
            ({"devDependencies": {"jest": "30.0.0"}, "scripts": {"test": "jest"}, "jest": {"collectCoverage": True, "coverageThreshold": {"global": {"lines": -10}}}}, None, "", "present"),
            ({"scripts": {"test": "vitest run --coverage --coverage.thresholds.lines=0"}}, None, "", "missing"),
            ({"scripts": {"test": "vitest run --coverage --coverage.thresholds.lines=80"}}, None, "", "present"),
            ({"scripts": {"test": "node --test --experimental-test-coverage --test-coverage-lines=0"}}, None, "", "missing"),
        ]
        for manifest, path, text, status in cases:
            with self.subTest(manifest=manifest, path=path, text=text):
                for existing in list(self.root.iterdir()):
                    if existing.name != ".git":
                        shutil.rmtree(existing) if existing.is_dir() else existing.unlink()
                self.package({**VITEST_PACKAGE, **manifest})
                if path:
                    self.write(path, text)
                self.write("src/a.test.ts", "import { it, expect } from 'vitest';\nit('adds', () => { expect(1 + 1).toBe(2); });\n")
                self.commit()
                self.assertEqual(self.check(self.collect(), "coverage-thresholds-configured")["status"], status)

    # Claude 7. Bun, Karma, and tap keep their floors in settings the collector does not read.
    def test_coverage_is_not_evaluated_for_runners_whose_floors_are_not_read(self) -> None:
        cases = [
            ({"scripts": {"test": "bun test"}}, "bunfig.toml", "[test]\ncoverageThreshold = 0.9\n", "src/a.test.ts", "import { test, expect } from 'bun:test';\ntest('adds', () => { expect(1 + 1).toBe(2); });\n"),
            ({"scripts": {"test": "ng test"}, "devDependencies": {"karma": "6.4.0", "jasmine-core": "5.0.0"}}, "karma.conf.js", "module.exports = (config) => config.set({ coverageReporter: { check: { global: { lines: 80 } } } });\n", "src/app/app.spec.ts", "describe('app', () => { it('adds', () => { expect(1 + 1).toBe(2); }); });\n"),
            ({"scripts": {"test": "tap"}, "devDependencies": {"tap": "21.0.0"}}, ".taprc", "allow-incomplete-coverage: false\n", "test/math.ts", "import t from 'tap';\nt.test('adds', (t) => { t.equal(1 + 1, 2); t.end(); });\n"),
        ]
        for manifest, config, settings, path, text in cases:
            with self.subTest(config=config):
                for existing in list(self.root.iterdir()):
                    if existing.name != ".git":
                        shutil.rmtree(existing) if existing.is_dir() else existing.unlink()
                self.package({"name": "app", **manifest})
                self.write(config, settings)
                self.write(path, text)
                self.write("src/index.ts", "export const one = 1;\n")
                self.commit()
                check = self.check(self.collect(), "coverage-thresholds-configured")
                self.assertEqual(check.get("evaluationState"), "not-evaluated")
                self.assertIn("does not read", check["reason"])

    # Claude 8. Product code in nested `tools/` folders and `*-test.ts` files without a test API stays product code.
    def test_nested_tools_folders_and_loose_test_names_stay_product_code(self) -> None:
        self.package({"name": "agent", "workspaces": ["packages/*"], **{key: value for key, value in VITEST_PACKAGE.items() if key != "name"}})
        self.write("src/tools/search.ts", "export const search = () => 1;\n")
        self.write("src/experiments/ab-test.ts", "export const variant = () => 'a';\n")
        self.write("src/index.ts", "import { search } from './tools/search';\nimport { variant } from './experiments/ab-test';\nexport const run = () => search() + variant();\n")
        self.write("src/index.test.ts", "import { it, expect } from 'vitest';\nimport { run } from './index';\nit('runs', () => { expect(run()).toBe('1a'); });\n")
        self.write("scripts/release.ts", "export const release = 1;\n")
        self.write("packages/cli/package.json", json.dumps({"name": "cli"}))
        self.write("packages/cli/scripts/build.ts", "export const build = 1;\n")
        self.write("packages/cli/src/tools/run.ts", "export const tool = 1;\n")
        self.write("lib/parse_test.ts", "import { test } from 'vitest';\ntest('parses', () => {});\n")
        self.commit()
        result = self.collect()
        ranked = {item["path"] for item in result["snapshot"]["riskRanking"]}
        self.assertTrue({"src/tools/search.ts", "src/experiments/ab-test.ts", "packages/cli/src/tools/run.ts"} <= ranked, ranked)
        self.assertFalse({"scripts/release.ts", "packages/cli/scripts/build.ts"} & ranked, ranked)
        self.assertEqual(result["snapshot"]["testFiles"]["total"], 2)

    # Notes. Uncalled matchers, verbatim coverage quotes, and distinctive runner quotes.
    def test_an_uncalled_jest_matcher_is_flagged_but_chai_properties_are_not(self) -> None:
        text = (
            "import { run } from '../src/run.js';\n"
            "it('runs', () => { expect(run('x')).toBeTruthy; });\n"
            "it('is true', () => { expect(run('x')).to.be.true; });\n"
        )
        flags = flags_of("test/run.test.js", text)
        self.assertEqual(flags["runs"], ["passes-if-undefined"])
        self.assertEqual(flags["is true"], [])

    def test_coverage_and_runner_quotes_appear_verbatim_and_name_the_runner(self) -> None:
        self.write("package.json", '{\n  "name": "app",\n  "packageManager": "pnpm@9.0.0",\n  "scripts": {\n'
                   '    "test": "vitest run --coverage.enabled=\\"true\\"",\n'
                   '    "test:node": "c8 --reporter=\\"text\\" --check-coverage --lines 80 node --import \\"tsx\\" --test",\n'
                   '    "test:all": "pnpm -r test"\n  },\n  "devDependencies": {}\n}\n')
        self.write("vitest.config.ts", "export default { test: { coverage: { thresholds: { lines: 80 } } } };\n")
        self.write("src/a.test.ts", "import { it, expect } from 'vitest';\nit('adds', () => { expect(1 + 1).toBe(2); });\n")
        self.commit()
        protocol = self.protocol()
        result = self.collect()
        for name in ("coverage-thresholds-configured", "tests-run-under-configured-runner"):
            for entry in self.check(result, name)["evidence"]:
                protocol.verify_evidence(self.root, entry)
        runner = self.check(result, "tests-run-under-configured-runner")["evidence"]
        quotes = [quote for entry in runner for quote in re.findall(r"`([^`]+)`", entry)]
        self.assertFalse({"pnpm", "node", "c8"} & set(quotes), quotes)
        self.assertTrue(any("vitest" in quote for quote in quotes), quotes)
        self.assertEqual(self.check(result, "coverage-thresholds-configured")["status"], "present")


class FixtureTests(unittest.TestCase):
    def test_component_test_fixtures_clean_up_between_tests(self) -> None:
        """Without Vitest globals, Testing Library never unmounts, and later tests find earlier renders."""
        for case in ("hollow-suite", "sound-suite"):
            script = (ROOT / "evals" / "testing-audit" / case / "fixture.sh").read_text(encoding="utf-8")
            setup = re.search(r"w src/test/setup\.ts <<'EOF'\n(.*?)\nEOF\n", script, re.S)
            assert setup is not None
            self.assertTrue("globals: true" in script or re.search(r"afterEach\(\(\) => \{\s*cleanup\(\);", setup.group(1)), case)


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
