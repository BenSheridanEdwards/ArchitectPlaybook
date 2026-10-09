from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "architecture-audit" / "scripts" / "collect.py"

for _variable in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR"):
    os.environ.pop(_variable, None)

spec = importlib.util.spec_from_file_location("architecture_collector", COLLECTOR)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)

CHECK = "architecture-audit."


class ArchitectureCollectorTests(unittest.TestCase):
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

    def commit(self, message: str = "change") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def collect(self) -> dict:
        return collector.collect(self.root, months=6)

    def test_finds_runtime_cycles_through_aliases_and_multiline_imports(self) -> None:
        self.write(
            "tsconfig.json",
            '{\n  // comment\n  "compilerOptions": { "baseUrl": ".", "paths": { "@/*": ["src/*"], }, },\n}\n',
        )
        self.write("src/cart.ts", "import {\n  getUser,\n} from '@/user';\nexport const cart = () => getUser();\n")
        self.write("src/user.ts", "import { cart } from './cart';\nexport const getUser = () => cart;\n")
        self.commit()
        result = self.collect()
        cycle = result["checks"][CHECK + "no-circular-dependencies"]
        self.assertEqual(cycle["status"], "violation")
        self.assertIn("src/cart.ts:3 — `@/user`", cycle["evidence"])
        self.assertIn("src/user.ts:1 — `./cart`", cycle["evidence"])

    def test_type_only_imports_do_not_form_runtime_cycles(self) -> None:
        self.write("src/a.ts", "import type { B } from './b';\nexport type A = { b: B };\n")
        self.write("src/b.ts", "import type { A } from './a';\nexport type B = { a: A };\n")
        self.commit()
        cycle = self.collect()["checks"][CHECK + "no-circular-dependencies"]
        self.assertEqual(cycle["status"], "present")

    def test_import_examples_in_strings_do_not_form_edges_or_cycles(self) -> None:
        examples = [
            "export const example = 'require(\"./b\")';\n",
            'export const example = "require(\'./b\')";\n',
            "export const example = `require('./b')`;\n",
            "export const example = `example:\nimport { b } from './b';\n`;\n",
            "export const example = `example:\nexport { b } from './b';\n`;\n",
            "export const example = `import('./b')`;\n",
            'export const example = "escaped \\\" require(\'./b\')";\n',
        ]
        self.write("src/b.ts", "import { example } from './a';\nexport const b = example;\n")
        for example in examples:
            with self.subTest(example=example):
                self.write("src/a.ts", example)
                result = self.collect()
                self.assertEqual(result["checks"][CHECK + "no-circular-dependencies"]["status"], "present")
                self.assertEqual(result["snapshot"]["importEdges"], 1)
                self.assertNotIn("importCycles", result["snapshot"])

    def test_template_expressions_keep_real_imports_and_ignore_nested_literal_examples(self) -> None:
        self.write("src/b.ts", "import { example } from './a';\nexport const b = example;\n")
        examples = [
            ("export const example = `${require /* comment */ ('./b')}`;\n", "violation", 2),
            ("export const example = `${import /* comment */ ('./b')}`;\n", "present", 2),
            ("export const example = `${{ value: require('./b') }.value}`;\n", "violation", 2),
            ("export const example = `${`nested ${require('./b')}`}`;\n", "violation", 2),
            ("export const example = `${`require('./b')`}`;\n", "present", 1),
            ('export const example = `${"require(\'./b\')"}`;\n', "present", 1),
            ("export const example = `${1 /* require('./b') */}`;\n", "present", 1),
            ("export const example = `${/}/.test('}') ? require('./b') : ''}`;\n", "violation", 2),
            ("export const example = `${/}/.test('}') ? import('./b') : ''}`;\n", "present", 2),
            ("export const example = `${2 / 1 ? require('./b') : ''}`;\n", "violation", 2),
            ("if (true) /[\"']/.test('x'); export const example = require('./b');\n", "violation", 2),
            ("{} /[\"']/.test('x'); export const example = require('./b');\n", "violation", 2),
            ("const of = 2; export const example = of / require('./b') / 2;\n", "violation", 2),
            ("export const example = object.return / require('./b') / 2;\n", "violation", 2),
            ("export const example = object.if(true) / require('./b') / 2;\n", "violation", 2),
            ("function run() {} /[\"']/.test('x'); export const example = require('./b');\n", "violation", 2),
            ("export async function run() {} /[\"']/.test('x'); export const example = require('./b');\n", "violation", 2),
            ("export const example = function() {} / require('./b') / 2;\n", "violation", 2),
            ("export const example = `${await /}/.test('}') ? import('./b') : ''}`;\n", "present", 2),
            ("function* run() { return `${yield /}/.test('}') ? require('./b') : ''}`; }\nexport const example = run;\n", "violation", 2),
        ]
        for example, status, edges in examples:
            with self.subTest(example=example):
                self.write("src/a.ts", example)
                result = self.collect()
                self.assertEqual(result["checks"][CHECK + "no-circular-dependencies"]["status"], status)
                self.assertEqual(result["snapshot"]["importEdges"], edges)

    def test_real_imports_after_literals_keep_comments_types_and_line_citations(self) -> None:
        self.write(
            "src/a.ts",
            "export const example = `require('./absent')`;\n"
            "/* leading comment */ import /* comment */ { b } /* comment */ from './b';\n",
        )
        self.write("src/b.ts", "export /* comment */ { example as b } /* comment */ from './a';\n")
        result = self.collect()
        cycle = result["checks"][CHECK + "no-circular-dependencies"]
        self.assertEqual(cycle["status"], "violation")
        self.assertIn("src/a.ts:2 — `./b`", cycle["evidence"])
        self.assertEqual(result["snapshot"]["unresolvedRelativeImports"], 0)

    def test_literal_workspace_imports_do_not_report_a_boundary_violation(self) -> None:
        self.write("package.json", '{"name": "root", "workspaces": ["packages/*"]}\n')
        self.write("packages/one/package.json", '{"name": "one"}\n')
        self.write("packages/two/package.json", '{"name": "two"}\n')
        self.write("packages/two/src/internal.ts", "export const internal = 1;\n")
        self.write("packages/one/src/a.ts", "export const example = `require('../../two/src/internal')`;\n")
        result = self.collect()
        self.assertEqual(result["checks"][CHECK + "cross-workspace-contracts-respected"]["status"], "present")
        self.assertEqual(result["snapshot"]["importEdges"], 0)

    def test_jsx_text_punctuation_does_not_hide_real_expression_imports(self) -> None:
        self.write("src/b.ts", "import { example } from './a';\nexport const b = example;\n")
        examples = [
            "export const example = <div>don't {require('./b')}</div>;\n",
            'export const example = <div>"quote {require(\'./b\')}"</div>;\n',
            "export const example = <><div>{require('./b')}</div></>;\n",
            "export const example = <div><span>don't</span>{require('./b')}</div>;\n",
            "export const example = <T,>(value: T) => require('./b');\n",
            "export const example = <T extends unknown>(value: T) => require('./b');\n",
            "const run = <T,>(value: T) => value;\nimport { b } from './b';\nexport const example = b;\n",
            'export const example = <div data-path="C:\\">{require(\'./b\')}</div>;\n',
            'export const example = <div label="first\nsecond">{require(\'./b\')}</div>;\n',
        ]
        for example in examples:
            with self.subTest(example=example):
                self.write("src/a.tsx", example)
                result = self.collect()
                self.assertEqual(result["checks"][CHECK + "no-circular-dependencies"]["status"], "violation")
                self.assertEqual(result["snapshot"]["importEdges"], 2)

    def test_jsx_literal_text_and_attributes_do_not_form_import_edges(self) -> None:
        self.write("src/b.ts", "import { example } from './a';\nexport const b = example;\n")
        self.write(
            "src/a.tsx",
            'export const example = <div label="require(\'./b\')">require(\'./b\')</div>;\n',
        )
        result = self.collect()
        self.assertEqual(result["checks"][CHECK + "no-circular-dependencies"]["status"], "present")
        self.assertEqual(result["snapshot"]["importEdges"], 1)

    def test_literal_imports_do_not_hide_orphan_candidates(self) -> None:
        self.write("package.json", '{"name": "fixture", "main": "src/a.ts"}\n')
        self.write("src/a.ts", "export const example = `require('./unused')`;\n")
        self.write("src/unused.ts", "export const unused = 1;\n")
        result = self.collect()
        self.assertIn("src/unused.ts", result["snapshot"]["orphanCandidates"]["examples"])
        self.assertEqual(result["snapshot"]["importEdges"], 0)

    def test_generic_jsx_components_keep_later_lazy_imports(self) -> None:
        self.write("package.json", '{"name": "fixture", "main": "src/app.tsx"}\n')
        self.write("src/select.tsx", "export const Select = () => null;\n")
        self.write("src/page.tsx", "export const Page = () => null;\n")
        self.write("src/unused.tsx", "export const unused = 1;\n")
        for component in ("Select<string>", "Select<Array<string>>", "Select<{ value: string }>", "Select<'a' | 'b'>", "UI.Select<string>", "Select <string>"):
            with self.subTest(component=component):
                self.write(
                    "src/app.tsx",
                    "import { Select } from './select';\n"
                    f'export const Pick = () => <{component} label="require(\'./unused\')" />;\n'
                    "export const Page = lazy(() => import('./page'));\n",
                )
                result = self.collect()
                self.assertEqual(result["snapshot"]["importEdges"], 2)
                self.assertNotIn("src/page.tsx", result["snapshot"]["orphanCandidates"]["examples"])
                self.assertIn("src/unused.tsx", result["snapshot"]["orphanCandidates"]["examples"])

    def test_const_type_parameters_keep_real_imports(self) -> None:
        self.write("src/b.ts", "import { example } from './a';\nexport const b = example;\n")
        for parameters in ("const T,", "const T extends unknown", "const T = string"):
            with self.subTest(parameters=parameters):
                self.write("src/a.tsx", f"export const example = <{parameters}>(value: T) => require('./b');\n")
                result = self.collect()
                self.assertEqual(result["snapshot"]["importEdges"], 2)
                self.assertEqual(result["checks"][CHECK + "no-circular-dependencies"]["status"], "violation")

    def test_jsx_in_javascript_files_keeps_expression_imports(self) -> None:
        for suffix in (".js", ".mjs", ".cjs"):
            with self.subTest(suffix=suffix):
                self.write("src/b" + suffix, "import { example } from './a';\nexport const b = example;\n")
                for example in (
                    "export const example = <p>Don't {require('./b')}</p>;\n",
                    "export const example = value</abc/.test(text) ? require('./b') : null;\n",
                ):
                    with self.subTest(example=example):
                        self.write("src/a" + suffix, "const note = \"require('./absent')\";\n" + example)
                        result = self.collect()
                        self.assertEqual(result["snapshot"]["importEdges"], 2)
                        self.assertEqual(result["checks"][CHECK + "no-circular-dependencies"]["status"], "violation")
                        self.assertEqual(result["snapshot"]["unresolvedRelativeImports"], 0)
                (self.root / ("src/a" + suffix)).unlink()
                (self.root / ("src/b" + suffix)).unlink()

    def test_statement_regular_expressions_keep_later_real_imports(self) -> None:
        self.write("src/b.ts", "import { example } from './a';\nexport const b = example;\n")
        examples = [
            "class Example {} /`/.test('x');\nexport const example = require('./b');\n",
            "export default class Example {} /`/.test('x');\nexport const example = require('./b');\n",
            "if (true) {} else /`/.test('x');\nexport const example = require('./b');\n",
            "for (const value of /`/.exec('x')) {}\nexport const example = require('./b');\n",
            "for (const of of /`/.exec('x')) {}\nexport const example = require('./b');\n",
            "const of = 2; export const example = of / require('./b') / 2;\n",
            "export const example = object.of / require('./b') / 2;\n",
            "export const example = object.else / require('./b') / 2;\n",
            "for (of / require('./b') / 2; false;) {}\nexport const example = 1;\n",
            "export const example = class {} / require('./b') / 2;\n",
        ]
        for example in examples:
            with self.subTest(example=example):
                self.write("src/a.ts", "const note = \"require('./absent')\";\n" + example)
                result = self.collect()
                self.assertEqual(result["snapshot"]["importEdges"], 2)
                self.assertEqual(result["checks"][CHECK + "no-circular-dependencies"]["status"], "violation")
                self.assertEqual(result["snapshot"]["unresolvedRelativeImports"], 0)

    def test_unclosed_lexical_contexts_fall_back_to_import_matching(self) -> None:
        examples = [
            "const example = `unfinished;\nrequire('./b');\n",
            "const example = `${unfinished;\nrequire('./b');\n",
            "const example = <div>unfinished;\nrequire('./b');\n",
            "const example = <div label=\"unfinished;\nrequire('./b');\n",
            "const example = <div><span></div></span></div>;\nrequire('./b');\n",
            "const example = <div><span></div></span></div>; require('./b');\n",
            "const example = /unterminated;\nrequire('./b');\n",
        ]
        for example in examples:
            with self.subTest(example=example):
                example = "/* require('./commented') */\n" + example
                text, positions = collector.mask_comments(example, allow_markup=True)
                self.assertEqual(len(positions), len(example))
                self.assertTrue(positions[example.rindex("require")])
                self.assertNotIn("commented", text)
                self.assertEqual(text.count("\n"), example.count("\n"))

    def test_inline_type_only_imports_do_not_form_runtime_cycles(self) -> None:
        self.write("src/a.ts", "import { type B } from './b';\nexport type A = { b: B };\n")
        self.write("src/b.ts", "import {\n  type A,\n  type Other,\n} from './a';\nexport type B = { a: A };\n")
        self.commit()
        self.assertEqual(self.collect()["checks"][CHECK + "no-circular-dependencies"]["status"], "present")
        self.write("src/a.ts", "import { type B, value } from './b';\nexport type A = { b: B };\nexport const a = value;\n")
        self.write("src/b.ts", "import { type A } from './a';\nimport { a } from './a';\nexport const value = a;\nexport type B = { a: A };\n")
        self.commit()
        self.assertEqual(self.collect()["checks"][CHECK + "no-circular-dependencies"]["status"], "violation")

    def test_a_truncated_graph_degrades_a_clean_cycle_result(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.write("src/big.ts", "export const big = '" + "x" * (collector.MAX_FILE_BYTES + 1) + "';\n")
        self.commit()
        result = self.collect()
        cycle = result["checks"][CHECK + "no-circular-dependencies"]
        self.assertEqual(cycle["status"], "present")
        self.assertIn("1 source files were too many or too large", cycle["degradedReason"])
        self.assertEqual(result["snapshot"]["skippedSourceFiles"]["examples"], ["src/big.ts"])

    def test_package_tsconfig_extends_resolve_through_node_modules(self) -> None:
        self.write("node_modules/@acme/tsconfig/base.json", '{"compilerOptions": {"baseUrl": ".", "paths": {}}}\n')
        self.write("node_modules/@acme/tsconfig/package.json", '{"name": "@acme/tsconfig"}\n')
        self.write("tsconfig.json", '{"extends": ["@acme/tsconfig/base.json", "@missing/config"], "compilerOptions": {"baseUrl": ".", "paths": {"@/*": ["src/*"]}}}\n')
        self.write("src/a.ts", "import { b } from '@/b';\nexport const a = b;\n")
        self.write("src/b.ts", "import { a } from '@/a';\nexport const b = a;\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["checks"][CHECK + "no-circular-dependencies"]["status"], "violation")
        self.assertEqual(result["snapshot"]["unloadedTsconfigExtends"], ["@missing/config"])

    def test_a_generic_lint_script_does_not_run_dependency_cruiser(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.write(".dependency-cruiser.js", "module.exports = { forbidden: [] };\n")
        self.write("package.json", '{"name": "fixture", "scripts": {"lint": "eslint ."}}\n')
        self.commit()
        self.assertEqual(self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]["status"], "partial")
        self.write("eslint.config.js", "export default [{ rules: { 'import/no-cycle': 'error' } }];\n")
        self.commit()
        self.assertEqual(self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]["status"], "present")

    def test_dependency_cruiser_without_tsconfig_cannot_see_alias_imports(self) -> None:
        self.write("tsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}\n')
        self.write("src/a.ts", "export const a = 1;\n")
        self.write(".dependency-cruiser.js", "module.exports = { forbidden: [] };\n")
        self.write("package.json", '{"name": "fixture", "scripts": {"lint": "depcruise src"}}\n')
        self.commit()
        key = CHECK + "boundaries-enforced-by-tooling"
        result = self.collect()["checks"][key]
        self.assertEqual(result["status"], "partial")
        self.assertIn("tsConfig", result["gap"])
        self.write(".dependency-cruiser.js", "module.exports = { options: { tsConfig: { fileName: 'tsconfig.json' } }, forbidden: [] };\n")
        self.commit()
        self.assertEqual(self.collect()["checks"][key]["status"], "present")

    def test_alias_resolution_is_read_from_options_extends_flags_and_packages(self) -> None:
        key = CHECK + "boundaries-enforced-by-tooling"
        self.write("tsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}\n')
        self.write("src/a.ts", "export const a = 1;\n")
        self.write("package.json", '{"name": "fixture", "scripts": {"lint": "depcruise src"}}\n')
        cases = {
            "commented out": ("module.exports = {\n  // options: { tsConfig: { fileName: 'tsconfig.json' } },\n  forbidden: [],\n};\n", None, "partial"),
            "extended base": ("module.exports = { extends: './.dependency-cruiser.base.js', forbidden: [] };\n",
                              "module.exports = { options: { tsConfig: { fileName: 'tsconfig.json' } } };\n", "present"),
        }
        for name, (config, base, expected) in cases.items():
            with self.subTest(name=name):
                self.write(".dependency-cruiser.js", config)
                if base:
                    self.write(".dependency-cruiser.base.js", base)
                self.commit(name)
                self.assertEqual(self.collect()["checks"][key]["status"], expected)
        self.write(".dependency-cruiser.js", "module.exports = { forbidden: [] };\n")
        self.write("package.json", '{"name": "fixture", "scripts": {"lint": "depcruise src --ts-config tsconfig.json"}}\n')
        self.commit("flag")
        self.assertEqual(self.collect()["checks"][key]["status"], "present")

    def test_a_package_without_aliases_does_not_need_ts_config(self) -> None:
        self.write("package.json", '{"name": "root", "workspaces": ["packages/*"], "scripts": {"lint": "depcruise packages"}}\n')
        self.write("packages/one/package.json", '{"name": "one"}\n')
        self.write("packages/two/package.json", '{"name": "two"}\n')
        self.write("packages/one/.dependency-cruiser.js", "module.exports = { forbidden: [] };\n")
        self.write("packages/one/tsconfig.json", '{"compilerOptions": {"strict": true}}\n')
        self.write("packages/one/src/index.ts", "export const one = 1;\n")
        self.commit()
        self.assertEqual(self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]["status"], "present")

    def test_a_ts_config_flag_covers_only_the_package_it_checks(self) -> None:
        self.write("package.json", '{"name": "root", "workspaces": ["packages/*"]}\n')
        for name, script in (("one", "depcruise src --ts-config tsconfig.json"), ("two", "depcruise src")):
            self.write(f"packages/{name}/package.json", f'{{"name": "{name}", "scripts": {{"lint": "{script}"}}}}\n')
            self.write(f"packages/{name}/tsconfig.json", '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}\n')
            self.write(f"packages/{name}/.dependency-cruiser.js", "module.exports = { forbidden: [] };\n")
            self.write(f"packages/{name}/src/index.ts", "export const value = 1;\n")
        self.commit()
        result = self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["evidence"], ["packages/two/.dependency-cruiser.js — no tsConfig option, so path aliases are not resolved"])

    def test_inherited_aliases_need_ts_config_too(self) -> None:
        self.write("config/base.json", '{"compilerOptions": {"paths": {"@/*": ["../src/*"]}}}\n')
        self.write("tsconfig.json", '{"extends": "./config/base.json"}\n')
        self.write("src/a.ts", "export const a = 1;\n")
        self.write(".dependency-cruiser.js", "module.exports = { forbidden: [] };\n")
        self.write("package.json", '{"name": "fixture", "scripts": {"lint": "depcruise src"}}\n')
        self.commit()
        self.assertEqual(self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]["status"], "partial")

    def test_change_coupling_finds_features_inside_workspaces(self) -> None:
        files = ("apps/shop/src/features/cart/view.ts", "apps/shop/src/features/checkout/total.ts")
        for path in files:
            self.write(path, "export const value = 0;\n")
        self.commit("initial")
        for number in range(4):
            for path in files:
                self.write(path, f"export const value = {number + 1};\n")
            self.commit(f"change {number}")
        pairs = [item["files"] for item in self.collect()["snapshot"]["changeCoupling"]]
        self.assertIn(list(files), pairs)

    def test_boundary_tooling_is_missing_partial_or_present(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.write("package.json", '{"name": "fixture"}\n')
        self.commit()
        self.assertEqual(self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]["status"], "missing")
        self.write("package.json", '{"name": "fixture", "devDependencies": {"dependency-cruiser": "16.0.0"}}\n')
        self.write(".dependency-cruiser.js", "module.exports = { forbidden: [] };\n")
        self.commit()
        configured = self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]
        self.assertEqual(configured["status"], "partial")
        self.write(
            "package.json",
            '{"name": "fixture", "scripts": {"lint": "depcruise src"}, "devDependencies": {"dependency-cruiser": "16.0.0"}}\n',
        )
        self.commit()
        self.assertEqual(self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]["status"], "present")

    def test_cross_workspace_imports_separate_production_from_tests(self) -> None:
        self.write("package.json", '{"name": "root", "workspaces": ["packages/*"]}\n')
        self.write("packages/one/package.json", '{"name": "one"}\n')
        self.write("packages/two/package.json", '{"name": "two"}\n')
        self.write("packages/two/src/internal.ts", "export const internal = 1;\n")
        self.write("packages/one/src/a.test.ts", "import { internal } from '../../two/src/internal';\n")
        self.commit()
        result = self.collect()["checks"][CHECK + "cross-workspace-contracts-respected"]
        self.assertEqual(result["status"], "partial")
        self.write("packages/one/src/a.ts", "import { internal } from '../../two/src/internal';\nexport const a = internal;\n")
        self.commit()
        result = self.collect()["checks"][CHECK + "cross-workspace-contracts-respected"]
        self.assertEqual(result["status"], "violation")
        self.assertIn("packages/one/src/a.ts:1 — `../../two/src/internal`", result["evidence"])

    def test_single_package_repositories_skip_the_workspace_check(self) -> None:
        self.write("src/a.ts", "export const a = 1;\n")
        self.commit()
        result = self.collect()["checks"][CHECK + "cross-workspace-contracts-respected"]
        self.assertEqual(result["applicability"], "not-applicable")

    def test_snapshot_reports_hotspots_coupling_and_orphans(self) -> None:
        self.write("eslint.config.js", "export default [];\n")
        self.write("src/main.ts", "import { used } from './used';\nconsole.log(used);\n")
        self.write("src/used.ts", "export const used = 1;\n")
        self.write("src/unused.ts", "export const unused = 1;\n")
        self.write("lib/partner.ts", "export const partner = 1;\n")
        self.commit("initial")
        for number in range(4):
            self.write("src/used.ts", f"export const used = {number};\n")
            self.write("lib/partner.ts", f"export const partner = {number};\n")
            self.commit(f"change {number}")
        snapshot = self.collect()["snapshot"]
        self.assertEqual(snapshot["hotspots"][0]["commits"], 5)
        self.assertEqual(snapshot["orphanCandidates"]["examples"], ["lib/partner.ts", "src/unused.ts"])
        self.assertEqual(snapshot["changeCoupling"][0]["files"], ["lib/partner.ts", "src/used.ts"])

    def test_months_threshold_widens_the_history_window(self) -> None:
        self.assertEqual(collector.history_months(["months=12"], 6), 12)
        self.assertEqual(collector.history_months(["other=1"], 6), 6)
        for invalid in (["months=soon"], ["months=0"]):
            with self.assertRaises(SystemExit):
                collector.history_months(invalid, 6)

    def cycle_status(self) -> str:
        return self.collect()["checks"][CHECK + "no-circular-dependencies"]["status"]

    def test_aliases_from_every_tsconfig_resolve(self) -> None:
        self.write("tsconfig.json", '{"files": [], "references": [{"path": "./tsconfig.app.json"}]}\n')
        self.write("tsconfig.app.json", '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}\n')
        self.write("src/a.ts", "import { b } from '@/b';\nexport const a = () => b;\n")
        self.write("src/b.ts", "import { a } from '@/a';\nexport const b = () => a;\n")
        self.write("apps/web/tsconfig.json", '{"compilerOptions": {"baseUrl": ".", "paths": {"~/*": ["lib/*"]}}}\n')
        self.write("apps/web/lib/x.ts", "import { y } from '~/y';\nexport const x = () => y;\n")
        self.write("apps/web/lib/y.ts", "import { x } from '~/x';\nexport const y = () => x;\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["snapshot"]["importCycles"]["count"], 2)
        self.assertEqual(result["snapshot"]["unresolvedRelativeImports"], 0)

    def test_comments_and_lazy_imports_do_not_form_cycles(self) -> None:
        self.write("src/a.ts", "// import { b } from './b';\n/* import { b } from './b'; */\nexport const a = 1;\n")
        self.write("src/b.ts", "import { a } from './a';\nexport const load = () => import('./a');\nexport const b = a;\n")
        self.write("src/c.ts", "export const c = () => import('./d');\n")
        self.write("src/d.ts", "import { c } from './c';\nexport const d = c;\n")
        self.commit()
        self.assertEqual(self.cycle_status(), "present")

    def test_a_type_alias_without_semicolons_does_not_hide_a_runtime_import(self) -> None:
        self.write("src/a.ts", "export type Shape = { b: string }\nimport { b } from './b'\nexport const a = b\n")
        self.write("src/b.ts", "import { a } from './a'\nexport const b = a\n")
        self.commit()
        self.assertEqual(self.cycle_status(), "violation")

    def test_asset_imports_are_not_unresolved(self) -> None:
        self.write("src/a.ts", "import './a.css';\nimport logo from './logo.svg';\nimport data from './data.json';\nexport const a = logo;\n")
        self.commit()
        self.assertEqual(self.collect()["snapshot"]["unresolvedRelativeImports"], 0)

    def test_boundary_rules_in_workspaces_and_next_lint_count(self) -> None:
        self.write("package.json", '{"name": "root", "workspaces": ["packages/*"], "scripts": {"lint": "next lint"}}\n')
        self.write("packages/one/package.json", '{"name": "one"}\n')
        self.write("packages/two/package.json", '{"name": "two"}\n')
        self.write("packages/one/eslint.config.js", "export default [{ rules: { 'import/no-cycle': 'error' } }];\n")
        self.write("packages/one/src/index.ts", "export const one = 1;\n")
        self.commit()
        result = self.collect()["checks"][CHECK + "boundaries-enforced-by-tooling"]
        self.assertEqual(result["status"], "present")
        self.assertIn("packages/one/eslint.config.js — `import/no-cycle` rule configured", result["evidence"])

    def test_alias_imports_into_another_workspaces_internals_are_violations(self) -> None:
        self.write("package.json", '{"name": "root", "workspaces": ["packages/*"]}\n')
        self.write("tsconfig.json", '{"compilerOptions": {"paths": {"@acme/ui": ["./packages/ui/src/index.ts"], "@acme/ui/*": ["./packages/ui/src/*"]}}}\n')
        self.write("packages/ui/package.json", '{"name": "@acme/ui", "exports": {".": "./src/index.ts"}}\n')
        self.write("packages/ui/src/index.ts", "export { Button } from './button';\n")
        self.write("packages/ui/src/button.ts", "export const Button = 1;\n")
        self.write("packages/web/package.json", '{"name": "web"}\n')
        self.write("packages/web/src/page.ts", "import { Button } from '@acme/ui';\nexport const page = Button;\n")
        self.commit()
        key = CHECK + "cross-workspace-contracts-respected"
        self.assertEqual(self.collect()["checks"][key]["status"], "present")
        self.write("packages/web/src/deep.ts", "import { Button } from '@acme/ui/button';\nexport const deep = Button;\n")
        self.commit()
        result = self.collect()["checks"][key]
        self.assertEqual(result["status"], "violation")
        self.assertIn("packages/web/src/deep.ts:1 — `@acme/ui/button`", result["evidence"])

    def test_nested_workspaces_own_their_files(self) -> None:
        self.write("package.json", '{"name": "root", "workspaces": ["packages/*", "packages/app/plugins/*"]}\n')
        self.write("packages/app/package.json", '{"name": "app"}\n')
        self.write("packages/app/src/internal.ts", "export const internal = 1;\n")
        self.write("packages/app/plugins/widget/package.json", '{"name": "widget"}\n')
        self.write("packages/app/plugins/widget/src/index.ts", "import { internal } from '../../../src/internal';\nexport const widget = internal;\n")
        self.commit()
        result = self.collect()["checks"][CHECK + "cross-workspace-contracts-respected"]
        self.assertEqual(result["status"], "violation")

    def test_published_entries_and_their_source_twins_define_the_public_surface(self) -> None:
        self.write("package.json", '{"name": "root", "workspaces": ["packages/*"]}\n')
        self.write("tsconfig.json", '{"compilerOptions": {"paths": {"@acme/ui": ["./packages/ui/src/index.ts"], "@acme/kit": ["./packages/kit/src/index.ts"]}}}\n')
        self.write("packages/ui/package.json", '{"name": "@acme/ui", "exports": {".": "./dist/index.js"}}\n')
        self.write("packages/ui/src/index.ts", "export const ui = 1;\n")
        self.write("packages/kit/package.json", '{"name": "@acme/kit", "exports": {"./button": "./dist/button.js"}}\n')
        self.write("packages/kit/src/index.ts", "export const kit = 1;\n")
        self.write("packages/kit/src/button.ts", "export const button = 1;\n")
        self.write("packages/web/package.json", '{"name": "web"}\n')
        self.write("packages/web/src/page.ts", "import { ui } from '@acme/ui';\nexport const page = ui;\n")
        self.commit()
        key = CHECK + "cross-workspace-contracts-respected"
        self.assertEqual(self.collect()["checks"][key]["status"], "present")
        self.write("packages/web/src/kit.ts", "import { kit } from '@acme/kit';\nexport const usesKit = kit;\n")
        self.commit()
        self.assertEqual(self.collect()["checks"][key]["status"], "violation")

    def test_change_coupling_separates_sibling_features(self) -> None:
        self.write("src/features/cart/view.ts", "export const view = 0;\n")
        self.write("src/features/checkout/total.ts", "export const total = 0;\n")
        self.write("src/features/checkout/tax.ts", "export const tax = 0;\n")
        self.commit("initial")
        for number in range(4):
            self.write("src/features/cart/view.ts", f"export const view = {number + 1};\n")
            self.write("src/features/checkout/total.ts", f"export const total = {number + 1};\n")
            self.write("src/features/checkout/tax.ts", f"export const tax = {number + 1};\n")
            self.commit(f"change {number}")
        pairs = [item["files"] for item in self.collect()["snapshot"]["changeCoupling"]]
        self.assertIn(["src/features/cart/view.ts", "src/features/checkout/total.ts"], pairs)
        self.assertNotIn(["src/features/checkout/tax.ts", "src/features/checkout/total.ts"], pairs)

    def test_history_keeps_non_ascii_names_and_skips_configuration(self) -> None:
        for number in range(3):
            self.write("src/café.ts", f"export const café = {number};\n")
            self.write("next.config.ts", f"export default {{ value: {number} }};\n")
            self.commit(f"change {number}")
        hotspots = [item["path"] for item in self.collect()["snapshot"]["hotspots"]]
        self.assertIn("src/café.ts", hotspots)
        self.assertNotIn("next.config.ts", hotspots)

    def test_cycle_citations_with_spaces_verify(self) -> None:
        self.write("src/my folder/a.ts", "import { b } from './b';\nexport const a = () => b;\n")
        self.write("src/my folder/b.ts", "import { a } from './a';\nexport const b = () => a;\n")
        self.commit()
        evidence = self.collect()["checks"][CHECK + "no-circular-dependencies"]["evidence"]
        self.assertIn("`src/my folder/a.ts`:1 — `./b`", evidence)
        protocol = self.load_protocol()
        for entry in evidence:
            protocol.verify_evidence(self.root, entry)

    def load_protocol(self) -> Any:
        protocol_spec = importlib.util.spec_from_file_location(
            "audit_run_for_collector_tests", ROOT / "audit-protocol" / "scripts" / "audit_run.py"
        )
        assert protocol_spec and protocol_spec.loader
        protocol = importlib.util.module_from_spec(protocol_spec)
        sys.modules[protocol_spec.name] = protocol
        protocol_spec.loader.exec_module(protocol)
        return protocol

    def test_evidence_survives_protocol_verification(self) -> None:
        protocol_spec = importlib.util.spec_from_file_location(
            "audit_run_for_collector_tests", ROOT / "audit-protocol" / "scripts" / "audit_run.py"
        )
        assert protocol_spec and protocol_spec.loader
        protocol = importlib.util.module_from_spec(protocol_spec)
        sys.modules[protocol_spec.name] = protocol
        protocol_spec.loader.exec_module(protocol)
        self.write("src/cart.ts", "import { user } from './user';\nexport const cart = user;\n")
        self.write("src/user.ts", "import { cart } from './cart';\nexport const user = cart;\n")
        self.write("package.json", '{"name": "fixture", "devDependencies": {"dependency-cruiser": "16.0.0"}}\n')
        self.commit()
        for result in self.collect()["checks"].values():
            for entry in result.get("evidence", []):
                protocol.verify_evidence(self.root, entry)


if __name__ == "__main__":
    unittest.main()
