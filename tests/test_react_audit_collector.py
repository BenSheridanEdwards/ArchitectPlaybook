from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COLLECTOR = ROOT / "react-audit" / "scripts" / "collect.py"
PROTOCOL = ROOT / "audit-protocol" / "scripts" / "audit_run.py"

for _variable in ("GIT_DIR", "GIT_INDEX_FILE", "GIT_WORK_TREE", "GIT_PREFIX", "GIT_COMMON_DIR"):
    os.environ.pop(_variable, None)

spec = importlib.util.spec_from_file_location("react_collector", COLLECTOR)
assert spec and spec.loader
collector = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = collector
spec.loader.exec_module(collector)

protocol_spec = importlib.util.spec_from_file_location("audit_run_for_react_collector_tests", PROTOCOL)
assert protocol_spec and protocol_spec.loader
protocol = importlib.util.module_from_spec(protocol_spec)
sys.modules[protocol_spec.name] = protocol
protocol_spec.loader.exec_module(protocol)

CHECK = "react-audit."
NEXT_PACKAGE = {
    "name": "shop",
    "private": True,
    "dependencies": {"next": "16.4.0", "react": "19.3.0", "react-dom": "19.3.0"},
    "devDependencies": {"eslint": "^9", "eslint-plugin-react-hooks": "^7.1.1", "typescript": "^5"},
}
HOOKS_FLAT = (
    'import { defineConfig } from "eslint/config";\n'
    'import reactHooks from "eslint-plugin-react-hooks";\n'
    'export default defineConfig([\n'
    '  { files: ["src/**/*.{ts,tsx}"], extends: [reactHooks.configs.flat.recommended] },\n'
    ']);\n'
)
ALIASES = '{"compilerOptions": {"paths": {"@/*": ["./src/*"]}}}\n'


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

    def package(self, value: dict[str, Any], relative: str = "package.json") -> None:
        self.write(relative, json.dumps(value, indent=2) + "\n")

    def commit(self, message: str = "change") -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def collect(self, **options: Any) -> dict[str, Any]:
        result = collector.collect(self.root, **options)
        for item in result["checks"].values():
            for entry in item.get("evidence", []):
                protocol.verify_evidence(self.root, entry)
        return result

    def check(self, result: dict[str, Any], name: str) -> dict[str, Any]:
        return result["checks"][CHECK + name]

    def next_app(self, eslint: str | None = HOOKS_FLAT) -> None:
        self.package(NEXT_PACKAGE)
        self.write("tsconfig.json", ALIASES)
        if eslint is not None:
            self.write("eslint.config.mjs", eslint)
        self.write("src/app/layout.tsx", "export default function RootLayout({ children }: { children: React.ReactNode }) {\n  return <html lang=\"en\"><body>{children}</body></html>;\n}\n")
        self.write("src/app/page.tsx", "import { Basket } from \"@/components/Basket\";\nexport default function Page() {\n  return <main><Basket /></main>;\n}\n")
        self.write("src/components/Basket.tsx", "'use client';\nimport { useState } from \"react\";\nexport function Basket() {\n  const [count, setCount] = useState(0);\n  return <button onClick={() => setCount(count + 1)}>{count}</button>;\n}\n")


class MaskTests(unittest.TestCase):
    def test_comments_strings_templates_and_regular_expressions_are_blanked(self) -> None:
        text = (
            "const a = 'useEffect(';\n"
            "// useEffect(() => {})\n"
            "/* forwardRef( */ const b = \"forwardRef(\";\n"
            "const c = `forwardRef(${value.useEffect}) and more`;\n"
            "const d = /forwardRef\\(/g; const e = 4 / 2;\n"
        )
        masked = collector.mask(text, "a.ts")
        self.assertEqual(len(masked), len(text))
        self.assertEqual(masked.count("\n"), text.count("\n"))
        self.assertNotIn("forwardRef", masked)
        self.assertNotIn("useEffect(", masked.replace("value.useEffect", ""))
        self.assertIn("value.useEffect", masked, "code inside ${} stays readable")
        self.assertIn("4 / 2", masked, "division is not a regular expression")

    def test_a_regular_expression_after_a_control_condition_is_masked(self) -> None:
        for keyword in ("if", "while", "for"):
            text = f"{keyword} (ready) /forwardRef(/.test(name);\nconst x = total / 2 / 3;\n"
            masked = collector.mask(text, "a.ts")
            self.assertNotIn("forwardRef", masked, keyword)
            self.assertIn("total / 2 / 3", masked)

    def test_division_after_a_call_is_not_a_regular_expression(self) -> None:
        text = "const ratio = width() / height() / 2; forwardRef(Thing);\n"
        self.assertIn("forwardRef(Thing)", collector.mask(text, "a.ts"))

    def test_jsx_text_and_closing_tags_do_not_hide_code(self) -> None:
        text = (
            "export function A() {\n"
            "  return <p>Don't forwardRef( here</p>;\n"
            "}\n"
            "const B = forwardRef((props, ref) => <div ref={ref}>{props.x}</div>);\n"
        )
        masked = collector.mask(text, "a.tsx")
        self.assertEqual(masked.count("forwardRef"), 1)
        self.assertIn("props.x", masked, "JSX expressions are code")

    def test_type_arguments_in_typescript_are_not_jsx(self) -> None:
        text = "const [user, setUser] = useState<User | null>(null);\nconst ref = forwardRef<HTMLInputElement, Props>(render);\n"
        masked = collector.mask(text, "a.tsx")
        self.assertIn("useState<User | null>(null)", masked)
        self.assertIn("forwardRef<HTMLInputElement, Props>(render)", masked)

    def test_comment_masked_text_keeps_strings_for_imports(self) -> None:
        source = collector.Source("a.tsx", "import x from './a'; // from './b'\n")
        self.assertIn("'./a'", source.comment_masked)
        self.assertNotIn("./b", source.comment_masked)
        self.assertEqual(len(source.comments), 1)


class ApplicabilityTests(CollectorTestCase):
    def test_a_repository_without_react_is_not_applicable(self) -> None:
        self.package({"name": "service", "dependencies": {"fastify": "5.4.0"}})
        self.write("src/index.ts", "export const a = 1;\n")
        self.commit()
        result = self.collect()
        self.assertFalse(result["snapshot"]["reactDetected"])
        self.assertEqual(set(result["checks"]), set(collector.ALL_CHECKS))
        for item in result["checks"].values():
            self.assertEqual(item["applicability"], "not-applicable")
            self.assertIn("audit-not-applicable", item["reason"])

    def test_a_repository_without_a_manifest_is_not_applicable(self) -> None:
        self.write("README.md", "# notes\n")
        self.commit()
        result = self.collect()
        self.assertIn("no package.json", self.check(result, "maintained-toolchain")["reason"])

    def test_react_18_makes_the_react_19_checks_not_applicable_with_a_reason(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "^18.3.1", "react-dom": "^18.3.1"}, "devDependencies": {"vite": "7.1.2"}})
        self.write("src/Field.tsx", "import { forwardRef } from 'react';\nexport const Field = forwardRef<HTMLInputElement>((props, ref) => <input ref={ref} />);\n")
        self.commit()
        result = self.collect()
        for name in ("ref-passed-as-a-prop", "forms-use-actions"):
            item = self.check(result, name)
            self.assertEqual(item["applicability"], "not-applicable")
            self.assertIn("^18.3.1", item["reason"])
        for name in ("client-modules-import-no-server-code", "serialisable-props-cross-the-boundary", "use-client-minimal-and-deliberate"):
            self.assertEqual(self.check(result, name)["applicability"], "not-applicable")
        self.assertNotIn(CHECK + "no-state-synchronisation", result["checks"], "model checks stay pending")

    def test_the_lockfile_decides_the_version(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "^18.2.0 || ^19.0.0"}})
        self.write("package-lock.json", json.dumps({"lockfileVersion": 3, "packages": {"": {}, "node_modules/react": {"version": "19.2.0"}}}))
        self.write("src/Field.tsx", "import { forwardRef } from 'react';\nexport const Field = forwardRef((props, ref) => <input ref={ref} />);\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["snapshot"]["packages"][0]["resolved"], "19.2.0")
        self.assertEqual(self.check(result, "ref-passed-as-a-prop")["status"], "partial")

    def test_pnpm_yarn_bun_and_catalog_versions_resolve(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "catalog:"}})
        self.write("pnpm-workspace.yaml", "packages:\n  - packages/*\ncatalog:\n  react: ^19.1.0\n")
        self.commit()
        self.assertEqual(self.collect()["snapshot"]["packages"][0]["gatingVersion"], "19.1")
        self.write("pnpm-lock.yaml", "lockfileVersion: '9.0'\n\nimporters:\n\n  .:\n    dependencies:\n      react:\n        specifier: 'catalog:'\n        version: 19.2.1\n")
        self.assertEqual(self.collect()["snapshot"]["packages"][0]["resolved"], "19.2.1")
        lockfiles = collector.Lockfiles(self.root)
        lockfiles.pnpm = ""
        lockfiles.yarn = '"react@^19.0.0":\n  version "19.0.4"\n'
        package = collector.Package(self.root, self.root.resolve())
        self.assertEqual(lockfiles.resolve(package, "react", "^19.0.0"), "19.0.4")
        lockfiles.yarn = '"react@npm:^19.0.0":\n  version: 19.1.2\n'
        self.assertEqual(lockfiles.resolve(package, "react", "^19.0.0"), "19.1.2")
        lockfiles.yarn = ""
        lockfiles.bun = '{"packages": {"react": ["react@19.3.0", "", {}, "sha512-x"]}}'
        self.assertEqual(lockfiles.resolve(package, "react", "^19.0.0"), "19.3.0")

    def test_an_unknown_version_leaves_the_ref_check_not_evaluated(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "latest"}})
        self.write("src/A.tsx", "export function A() { return <p />; }\n")
        self.commit()
        result = self.collect()
        self.assertEqual(self.check(result, "ref-passed-as-a-prop")["evaluationState"], "not-evaluated")
        self.assertNotIn(CHECK + "forms-use-actions", result["checks"])

    def test_a_library_is_gated_by_its_lowest_peer_version(self) -> None:
        self.package({"name": "ui", "peerDependencies": {"react": "^18.0.0 || ^19.0.0"}, "devDependencies": {"react": "19.3.0"}})
        self.write("src/Button.tsx", "import { forwardRef } from 'react';\nexport const Button = forwardRef<HTMLButtonElement>((props, ref) => <button ref={ref} />);\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["snapshot"]["packages"][0]["gatingVersion"], "18.0")
        self.assertEqual(self.check(result, "ref-passed-as-a-prop")["applicability"], "not-applicable")

    def test_monorepo_packages_are_gated_separately(self) -> None:
        self.package({"name": "monorepo", "private": True, "workspaces": ["apps/*", "", "/etc", "../outside"]})
        self.package({"name": "web", "dependencies": {"react": "19.3.0"}}, "apps/web/package.json")
        self.package({"name": "legacy", "dependencies": {"react": "18.3.1"}}, "apps/legacy/package.json")
        self.write("apps/web/src/Input.tsx", "export function Input({ ref }: { ref?: React.Ref<HTMLInputElement> }) { return <input ref={ref} />; }\n")
        self.write("apps/legacy/src/Input.tsx", "import { forwardRef } from 'react';\nexport const Input = forwardRef((props, ref) => <input ref={ref} />);\n")
        self.commit()
        result = self.collect()
        versions = {item["package"]: item["gatingVersion"] for item in result["snapshot"]["packages"]}
        self.assertEqual(versions, {"apps/legacy/package.json": "18.3", "apps/web/package.json": "19.3"})
        ref = self.check(result, "ref-passed-as-a-prop")
        self.assertEqual(ref["status"], "present", "forwardRef in the React 18 package is not counted")

    def test_react_native_is_in_scope_without_server_components(self) -> None:
        self.package({"name": "app", "main": "expo-router/entry", "dependencies": {"expo": "~54.0.0", "expo-router": "~6.0.0", "react": "19.1.0", "react-native": "0.81.4"}})
        self.write("app.json", '{"expo": {"name": "app", "experiments": {"reactCompiler": true}}}\n')
        self.write("app/index.tsx", "import { Text } from 'react-native';\nexport default function Home() { return <Text>Hi</Text>; }\n")
        self.write("components/Row.tsx", "import { Text } from 'react-native';\nexport default function Row() { return <Text>Row</Text>; }\n")
        self.commit()
        result = self.collect()
        self.assertIn("expo-router", result["snapshot"]["packages"][0]["frameworks"])
        self.assertEqual(self.check(result, "client-modules-import-no-server-code")["applicability"], "not-applicable")
        named = self.check(result, "named-exports-for-components")
        self.assertEqual(named["status"], "partial")
        self.assertEqual(named["evidence"], ["components/Row.tsx:2 — `export default`"], "Expo Router routes are exempt")
        self.assertEqual(self.check(result, "memoization-for-correctness")["applicability"], "not-applicable")

    def test_tooling_folders_count_only_at_a_package_root(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "19.3.0"}})
        self.write("scripts/Build.tsx", "export default function Build() { return <p />; }\n")
        self.write("src/tools/Picker.tsx", "export default function Picker() { return <p />; }\n")
        self.commit()
        named = self.check(self.collect(), "named-exports-for-components")
        self.assertEqual(named["evidence"], ["src/tools/Picker.tsx:1 — `export default`"])


class HooksLintTests(CollectorTestCase):
    def lint(self) -> dict[str, Any]:
        self.commit()
        return self.check(self.collect(), "react-hooks-plugin-enabled")

    def test_the_flat_recommended_preset_scoped_to_component_files_is_present(self) -> None:
        self.next_app()
        item = self.lint()
        self.assertEqual(item["status"], "present")
        self.assertEqual(item["evidence"], ["eslint.config.mjs — `reactHooks.configs.flat.recommended`"])

    def test_no_plugin_is_missing_and_consider(self) -> None:
        self.next_app('import js from "@eslint/js";\nexport default [js.configs.recommended];\n')
        item = self.lint()
        self.assertEqual(item["status"], "missing")
        self.assertEqual(item["judgement"], "consider")

    def test_a_preset_scoped_away_from_component_files_is_missing(self) -> None:
        self.next_app(HOOKS_FLAT.replace("src/**/*.{ts,tsx}", "scripts/**/*.ts"))
        self.assertEqual(self.lint()["status"], "missing")

    def test_global_ignores_drop_the_component_files(self) -> None:
        self.next_app(
            'import { defineConfig, globalIgnores } from "eslint/config";\nimport reactHooks from "eslint-plugin-react-hooks";\n'
            'export default defineConfig([globalIgnores(["src/**"]), reactHooks.configs.flat.recommended]);\n'
        )
        self.assertEqual(self.lint()["status"], "missing")

    def test_a_later_setting_turns_a_rule_off(self) -> None:
        self.next_app(HOOKS_FLAT.replace("]);\n", "  { rules: { 'react-hooks/exhaustive-deps': 'off' } },\n]);\n"))
        item = self.lint()
        self.assertEqual(item["status"], "partial")
        self.assertIn("exhaustive-deps", item["gap"])

    def test_an_earlier_off_is_overridden_by_a_later_preset(self) -> None:
        self.next_app(HOOKS_FLAT.replace("export default defineConfig([\n", "export default defineConfig([\n  { rules: { 'react-hooks/exhaustive-deps': 'off' } },\n"))
        self.assertEqual(self.lint()["status"], "present")

    def test_a_spread_keeps_the_scope_of_the_spread_object(self) -> None:
        self.next_app(
            'import reactHooks from "eslint-plugin-react-hooks";\n'
            'const scoped = { files: ["scripts/**"], ...reactHooks.configs.flat.recommended };\n'
            'export default [{ ...scoped }];\n'
        )
        self.assertEqual(self.lint()["status"], "missing")

    def test_an_own_rules_key_replaces_a_spread_presets_rules(self) -> None:
        self.next_app(
            'import reactHooks from "eslint-plugin-react-hooks";\n'
            'export default [{ ...reactHooks.configs.flat.recommended, rules: { "react-hooks/rules-of-hooks": "error" } }];\n'
        )
        item = self.lint()
        self.assertEqual(item["status"], "partial")
        self.assertIn("exhaustive-deps", item["gap"])

    def test_the_classic_rules_without_the_compiler_rules_are_partial(self) -> None:
        self.next_app(
            'import reactHooks from "eslint-plugin-react-hooks";\n'
            'export default [{ plugins: { "react-hooks": reactHooks }, rules: { "react-hooks/rules-of-hooks": "error", "react-hooks/exhaustive-deps": "warn" } }];\n'
        )
        item = self.lint()
        self.assertEqual(item["status"], "partial")
        self.assertIn("React Compiler rules", item["gap"])

    def test_plugin_6_recommended_lacks_the_compiler_rules_but_recommended_latest_has_them(self) -> None:
        self.next_app('import reactHooks from "eslint-plugin-react-hooks";\nexport default [{ plugins: { "react-hooks": reactHooks }, rules: reactHooks.configs.recommended.rules }];\n')
        package = dict(NEXT_PACKAGE, devDependencies={"eslint-plugin-react-hooks": "6.1.1"})
        self.package(package)
        self.assertEqual(self.lint()["status"], "partial")
        self.write("eslint.config.mjs", 'import reactHooks from "eslint-plugin-react-hooks";\nexport default [reactHooks.configs["recommended-latest"]];\n')
        self.assertEqual(self.lint()["status"], "present")

    def test_a_string_extends_and_eslint_config_next_16_count(self) -> None:
        self.next_app(
            'import { defineConfig } from "eslint/config";\nimport reactHooks from "eslint-plugin-react-hooks";\n'
            'export default defineConfig([{ files: ["**/*.tsx"], plugins: { "react-hooks": reactHooks }, extends: ["react-hooks/recommended"] }]);\n'
        )
        self.assertEqual(self.lint()["status"], "present")
        self.write("eslint.config.mjs", 'import { defineConfig } from "eslint/config";\nimport nextVitals from "eslint-config-next/core-web-vitals";\nexport default defineConfig([...nextVitals]);\n')
        self.package(dict(NEXT_PACKAGE, devDependencies={"eslint-config-next": "16.4.0"}))
        self.assertEqual(self.lint()["status"], "present")
        self.package(dict(NEXT_PACKAGE, devDependencies={"eslint-config-next": "15.5.4"}))
        self.assertEqual(self.lint()["status"], "partial")

    def test_legacy_json_yaml_and_javascript_configurations_are_read(self) -> None:
        self.next_app(None)
        self.package(dict(NEXT_PACKAGE, devDependencies={"eslint-plugin-react-hooks": "5.2.0"}))
        self.write(".eslintrc.json", '{"extends": ["eslint:recommended"], "overrides": [{"files": ["*.tsx"], "extends": ["plugin:react-hooks/recommended"]}]}\n')
        item = self.lint()
        self.assertEqual(item["status"], "partial", "plugin 5 has only the classic rules")
        (self.root / ".eslintrc.json").unlink()
        self.write(".eslintrc.yml", "extends:\n  - eslint:recommended\n  - plugin:react-hooks/recommended\n")
        self.assertEqual(self.lint()["status"], "partial")
        (self.root / ".eslintrc.yml").unlink()
        self.write(".eslintrc.yaml", "extends: plugin:react-hooks/recommended\n")
        self.assertEqual(self.lint()["status"], "partial")
        (self.root / ".eslintrc.yaml").unlink()
        self.write(".eslintrc.cjs", "module.exports = { extends:\n  ['plugin:react-hooks/recommended'] };\n")
        self.assertEqual(self.lint()["status"], "partial")
        self.package(dict(NEXT_PACKAGE, devDependencies={"eslint-plugin-react-hooks": "7.1.1"}))
        self.assertEqual(self.lint()["status"], "present")

    def test_flat_compat_extends_next_is_read(self) -> None:
        self.next_app(
            'import { FlatCompat } from "@eslint/eslintrc";\nconst compat = new FlatCompat({ baseDirectory: import.meta.dirname });\n'
            'export default [...compat.extends("next/core-web-vitals", "next/typescript")];\n'
        )
        self.package(dict(NEXT_PACKAGE, devDependencies={"eslint-config-next": "15.5.4"}))
        self.assertEqual(self.lint()["status"], "partial")

    def test_unreadable_shared_configuration_is_not_evaluated_never_missing(self) -> None:
        self.next_app('import company from "@acme/eslint-config";\nexport default [...company];\n')
        item = self.lint()
        self.assertEqual(item["evaluationState"], "not-evaluated")
        self.assertIn("company", item["reason"])
        self.write("eslint.config.mjs", "")
        self.write(".eslintrc.js", "module.exports = { extends: sharedConfig };\n")
        (self.root / "eslint.config.mjs").unlink()
        self.assertEqual(self.lint()["evaluationState"], "not-evaluated")

    def test_commented_out_configuration_does_not_count(self) -> None:
        self.next_app('// import reactHooks from "eslint-plugin-react-hooks";\nexport default [\n  // reactHooks.configs.flat.recommended,\n];\n')
        self.assertEqual(self.lint()["status"], "missing")

    def test_biome_is_partial_and_oxlint_is_not_evaluated(self) -> None:
        self.next_app(None)
        self.write("biome.json", '{"linter": {"enabled": true, "rules": {"recommended": true}}}\n')
        item = self.lint()
        self.assertEqual(item["status"], "partial")
        self.write("biome.json", '{"linter": {"rules": {"correctness": {"useHookAtTopLevel": "off", "useExhaustiveDependencies": "off"}}}}\n')
        self.assertEqual(self.lint()["status"], "missing")
        (self.root / "biome.json").unlink()
        self.write(".oxlintrc.json", '{"plugins": ["react"]}\n')
        self.assertEqual(self.lint()["evaluationState"], "not-evaluated")


class CompilerTests(CollectorTestCase):
    def stable(self) -> dict[str, Any] | None:
        self.commit()
        result = self.collect()
        return result["checks"].get(CHECK + "memoization-for-correctness"), result["snapshot"]["compiler"][0]

    def test_next_react_compiler_true_makes_the_stable_check_not_applicable(self) -> None:
        self.next_app()
        self.write("next.config.ts", "import type { NextConfig } from 'next';\nconst nextConfig: NextConfig = {\n  reactCompiler: true,\n};\nexport default nextConfig;\n")
        item, facts = self.stable()
        self.assertEqual(item["applicability"], "not-applicable")
        self.assertTrue(facts["everywhere"])
        self.assertIn("next.config.ts:3", item["reason"])

    def test_annotation_mode_unreadable_values_and_opt_outs_keep_it_applicable(self) -> None:
        self.next_app()
        self.write("next.config.ts", "export default { reactCompiler: { compilationMode: 'annotation' } };\n")
        item, facts = self.stable()
        self.assertIsNone(item)
        self.assertEqual(facts["mode"], "annotation")
        self.write("next.config.ts", "export default { reactCompiler: process.env.COMPILER === '1' };\n")
        item, facts = self.stable()
        self.assertIsNone(item)
        self.assertIsNone(facts["enabled"], "an unreadable value is unknown, never off")
        self.write("next.config.ts", "export default { experimental: { reactCompiler: true } };\n")
        self.write("src/components/Legacy.tsx", "'use no memo';\nexport function Legacy() { return <p />; }\n")
        item, _ = self.stable()
        self.assertIsNone(item, "a 'use no memo' file is not compiled")

    def test_babel_and_vite_configuration_are_read(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "19.3.0"}, "devDependencies": {"vite": "8.0.0", "@vitejs/plugin-react": "6.0.0"}})
        self.write("src/App.tsx", "export function App() { return <p />; }\n")
        self.write("vite.config.ts", "import react, { reactCompilerPreset } from '@vitejs/plugin-react';\nimport babel from '@rolldown/plugin-babel';\nexport default { plugins: [react(), babel({ presets: [reactCompilerPreset()] })] };\n")
        item, _ = self.stable()
        self.assertEqual(item["applicability"], "not-applicable")
        (self.root / "vite.config.ts").unlink()
        self.write("babel.config.json", '{"plugins": [["babel-plugin-react-compiler", {"compilationMode": "infer"}]]}\n')
        item, _ = self.stable()
        self.assertEqual(item["applicability"], "not-applicable")
        self.write("babel.config.json", '{"plugins": [["babel-plugin-react-compiler", {"sources": ["src/new"]}]]}\n')
        item, _ = self.stable()
        self.assertIsNone(item)


class SuppressionTests(CollectorTestCase):
    def test_reasons_adjacent_comments_strings_and_biome_ignores(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "19.3.0"}})
        self.write("src/A.tsx", (
            "import { useEffect } from 'react';\n"
            "export function A({ id }: { id: string }) {\n"
            "  useEffect(() => {\n"
            "    load(id);\n"
            "    // eslint-disable-next-line react-hooks/exhaustive-deps -- load is stable and id is read once\n"
            "  }, []);\n"
            "  useEffect(() => {\n"
            "    // The analytics client must not re-subscribe on every page change.\n"
            "    // eslint-disable-next-line react-hooks/exhaustive-deps\n"
            "  }, []);\n"
            "  const text = '// eslint-disable-next-line react-hooks/exhaustive-deps';\n"
            "  return <p>{text}</p>;\n"
            "}\n"
        ))
        self.commit()
        item = self.check(self.collect(), "no-silenced-exhaustive-deps")
        self.assertEqual(item["status"], "present")
        self.write("src/B.tsx", (
            "import { useEffect } from 'react';\n"
            "export function B({ id }: { id: string }) {\n"
            "  useEffect(() => {\n"
            "    load(id);\n"
            "  }, []); // eslint-disable-line react-hooks/exhaustive-deps\n"
            "  // biome-ignore lint/correctness/useExhaustiveDependencies: <explanation>\n"
            "  useEffect(() => { load(id); }, []);\n"
            "  return null;\n"
            "}\n"
        ))
        self.commit()
        item = self.check(self.collect(), "no-silenced-exhaustive-deps")
        self.assertEqual(item["status"], "partial")
        self.assertEqual(item["judgement"], "consider")
        self.assertEqual(len(item["evidence"]), 2)
        self.assertIn("src/B.tsx:5 — `react-hooks/exhaustive-deps`", item["evidence"][0] + item["evidence"][1])


class ComponentTests(CollectorTestCase):
    def test_forward_ref_is_counted_in_code_only(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "19.3.0"}})
        self.write("src/Field.tsx", (
            "import { forwardRef } from 'react';\n"
            "// forwardRef(old)\n"
            "const label = 'forwardRef(';\n"
            "export const Field = forwardRef<HTMLInputElement, { name: string }>(function Field(props, ref) {\n"
            "  return <input ref={ref} name={props.name} />;\n"
            "});\n"
            "export const Other = React.forwardRef((props, ref) => <div ref={ref} />);\n"
        ))
        self.commit()
        item = self.check(self.collect(), "ref-passed-as-a-prop")
        self.assertEqual(item["status"], "partial")
        self.assertEqual(item["evidence"], ["src/Field.tsx:4 — `forwardRef`", "src/Field.tsx:7 — `forwardRef`"])

    def test_framework_required_default_exports_and_lazy_targets_are_exempt(self) -> None:
        self.next_app()
        self.write("src/app/(shop)/basket/loading.tsx", "export default function Loading() { return <p>Loading</p>; }\n")
        self.write("src/app/error.tsx", "'use client';\nexport default function Error() { return <p>Error</p>; }\n")
        self.write("src/app/opengraph-image.tsx", "export default function Image() { return <div />; }\n")
        self.write("src/components/Chart.tsx", "export default function Chart() { return <svg />; }\n")
        self.write("src/components/Lazy.tsx", "import { lazy } from 'react';\nexport const LazyChart = lazy(() => import('./Chart'));\n")
        self.write("src/components/Header.tsx", "export default function Header() { return <header />; }\n")
        self.commit()
        item = self.check(self.collect(), "named-exports-for-components")
        self.assertEqual(item["status"], "partial")
        self.assertEqual(item["evidence"], ["src/components/Header.tsx:1 — `export default`"])

    def test_react_router_route_modules_are_exempt(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "19.3.0", "react-router": "7.9.0"}, "devDependencies": {"@react-router/dev": "7.9.0"}})
        self.write("react-router.config.ts", "export default { appDirectory: 'app', ssr: true };\n")
        self.write("app/routes.ts", "import { index, route } from '@react-router/dev/routes';\nexport default [index('routes/home.tsx'), route('about', 'pages/about.tsx')];\n")
        self.write("app/root.tsx", "export default function App() { return <html />; }\n")
        self.write("app/routes/home.tsx", "export default function Home() { return <p />; }\n")
        self.write("app/pages/about.tsx", "export default function About() { return <p />; }\n")
        self.commit()
        result = self.collect()
        self.assertIn("react-router-framework", result["snapshot"]["packages"][0]["frameworks"])
        self.assertEqual(self.check(result, "named-exports-for-components")["status"], "present")

    def test_create_react_app_is_a_deprecated_toolchain(self) -> None:
        self.write("package.json", '{\n  "name": "legacy",\n  "dependencies": {\n    "react": "18.2.0",\n    "react-scripts": "5.0.1"\n  },\n  "scripts": {"start": "react-scripts start --host \\"0.0.0.0\\""}\n}\n')
        self.write("src/App.jsx", "export function App() { return <p />; }\n")
        self.commit()
        item = self.check(self.collect(), "maintained-toolchain")
        self.assertEqual(item["status"], "violation")
        self.assertEqual(item["evidence"], ['package.json — `"react-scripts"`'])
        self.assertNotIn("judgement", item)


class BoundaryTests(CollectorTestCase):
    def test_a_client_chain_to_server_code_is_a_violation(self) -> None:
        self.next_app()
        self.write("src/lib/payments.ts", "import Stripe from \"stripe\";\nexport const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!);\nexport function formatPrice(cents: number) { return `$${cents / 100}`; }\n")
        self.write("src/components/Price.tsx", "'use client';\nimport { formatPrice } from \"@/lib/payments\";\nexport function Price({ cents }: { cents: number }) { return <span>{formatPrice(cents)}</span>; }\n")
        self.commit()
        result = self.collect()
        item = self.check(result, "client-modules-import-no-server-code")
        self.assertEqual(item["status"], "violation")
        self.assertIn("src/components/Price.tsx:2 — `@/lib/payments` in a 'use client' module", item["evidence"])
        self.write("src/components/Checkout.tsx", "'use client';\nimport { Price } from './Price';\nimport { formatPrice } from '@/lib/payments';\nexport function Checkout() { return <Price cents={formatPrice(1).length} />; }\n")
        self.commit()
        evidence = self.check(self.collect(), "client-modules-import-no-server-code")["evidence"]
        self.assertEqual(sum(1 for entry in evidence if "`@/lib/payments`" in entry), 2, "every direct client importer is cited, up to two")
        self.assertIn('src/lib/payments.ts:1 — `"stripe"` imports stripe, and it is bundled for the browser', item["evidence"])
        self.assertNotIn("judgement", item, "a boundary leak is act-on by default")

    def test_type_imports_public_variables_and_server_functions_are_allowed(self) -> None:
        self.next_app()
        self.write("src/lib/db.ts", "import 'server-only';\nimport { Pool } from 'pg';\nexport type Row = { id: string };\nexport const pool = new Pool();\n")
        self.write("src/lib/actions.ts", "'use server';\nimport { pool } from './db';\nexport async function save(id: string) { await pool.query('x', [id]); }\n")
        self.write("src/components/Form.tsx", (
            "'use client';\n"
            "import type { Row } from '@/lib/db';\n"
            "import { save } from '@/lib/actions';\n"
            "const site = process.env.NEXT_PUBLIC_SITE_URL;\n"
            "export function Form({ row }: { row: Row }) { return <form action={() => save(row.id)}>{site}</form>; }\n"
        ))
        self.commit()
        self.assertEqual(self.check(self.collect(), "client-modules-import-no-server-code")["status"], "present")

    def test_next_env_entries_and_a_byte_order_mark_are_handled(self) -> None:
        self.next_app()
        self.write("next.config.ts", "export default {\n  env: { BUILD_ID: process.env.BUILD_ID },\n};\n")
        self.write("src/components/Build.tsx", "\ufeff'use client';\nexport function Build() { return <p>{process.env.BUILD_ID}</p>; }\n")
        self.commit()
        result = self.collect()
        self.assertEqual(self.check(result, "client-modules-import-no-server-code")["status"], "present")
        self.assertIn("src/components/Build.tsx", result["snapshot"]["directives"]["useClientExamples"])
        self.write("next.config.ts", "export default {};\n")
        self.commit()
        self.assertEqual(self.check(self.collect(), "client-modules-import-no-server-code")["status"], "violation")

    def test_a_misplaced_directive_is_not_a_client_entry(self) -> None:
        self.next_app()
        self.write("src/components/Late.tsx", "import { useState } from 'react';\nimport { pool } from '@/lib/db';\n'use client';\nexport function Late() { const [a] = useState(pool); return <p>{String(a)}</p>; }\n")
        self.write("src/lib/db.ts", "import { Pool } from 'pg';\nexport const pool = new Pool();\n")
        self.commit()
        result = self.collect()
        self.assertEqual(self.check(result, "client-modules-import-no-server-code")["status"], "present")
        self.assertEqual(result["snapshot"]["directives"]["misplaced"], ["src/components/Late.tsx:3 — `use client` is below other code, so React ignores it"])
        for entry in result["snapshot"]["directives"]["misplaced"]:
            protocol.verify_evidence(self.root, entry)

    def test_an_unresolved_alias_in_the_client_graph_degrades_the_result(self) -> None:
        self.next_app()
        self.write("src/components/Odd.tsx", "'use client';\nimport { thing } from '@/missing/thing';\nexport function Odd() { return <p>{thing}</p>; }\n")
        self.commit()
        item = self.check(self.collect(), "client-modules-import-no-server-code")
        self.assertEqual(item["status"], "present")
        self.assertIn("@/missing/thing", item["degradedReason"])

    def test_non_serialisable_prop_candidates(self) -> None:
        self.next_app()
        self.write("src/components/Picker.tsx", "'use client';\nexport function Picker(props: { onPick: (id: string) => void; when: Date }) { return <p />; }\n")
        self.write("src/lib/actions.ts", "'use server';\nexport async function remember(id: string) {}\n")
        self.write("src/app/pick/page.tsx", (
            "import { Picker } from '@/components/Picker';\n"
            "import { remember } from '@/lib/actions';\n"
            "class Money { constructor(public cents: number) {} }\n"
            "function choose(id: string) { console.log(id); }\n"
            "async function store(id: string) {\n  'use server';\n}\n"
            "export default function Page() {\n"
            "  const items = useState<Picker>(null);\n"
            "  return (\n"
            "    <main>\n"
            "      <Picker onPick={choose} when={new Date()} />\n"
            "      <Picker onPick={(id) => console.log(id)} price={new Money(5)} />\n"
            "      <Picker onPick={remember} />\n"
            "      <Picker onPick={store} />\n"
            "    </main>\n"
            "  );\n"
            "}\n"
        ))
        self.commit()
        candidates = self.collect()["snapshot"]["directives"]["nonSerialisablePropCandidates"]
        found = {(item["line"], item["prop"], item["value"]) for item in candidates}
        self.assertEqual(found, {
            (12, "onPick", "the function `choose`"),
            (13, "onPick", "an inline function"),
            (13, "price", "an instance of `Money`"),
        })

    def test_without_server_components_the_boundary_checks_do_not_apply(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "19.3.0", "react-dom": "19.3.0"}, "devDependencies": {"vite": "7.1.2"}})
        self.write("src/App.tsx", "'use client';\nexport function App() { return <p />; }\n")
        self.commit()
        item = self.check(self.collect(), "serialisable-props-cross-the-boundary")
        self.assertEqual(item["applicability"], "not-applicable")
        self.assertIn("only a server-components bundler reads", item["reason"])


class EffectTests(CollectorTestCase):
    def test_effect_candidates_are_flagged(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "19.3.0"}})
        self.write("src/Search.tsx", (
            "import { useEffect, useState } from 'react';\n"
            "export function Search({ query, onChange }: { query: string; onChange: (q: string) => void }) {\n"
            "  const [draft, setDraft] = useState(query);\n"
            "  const [results, setResults] = useState([]);\n"
            "  const [isLoading, setIsLoading] = useState(false);\n"
            "  const [isError, setIsError] = useState(false);\n"
            "  const [isDone, setIsDone] = useState(false);\n"
            "  useEffect(() => { setDraft(query.trim()); }, [query]);\n"
            "  useEffect(() => { onChange(draft); }, [draft]);\n"
            "  useEffect(() => {\n"
            "    fetch(`/api?q=${query}`).then((response) => response.json()).then(setResults);\n"
            "  }, [query]);\n"
            "  useEffect(() => {\n"
            "    let ignore = false;\n"
            "    fetch('/api').then((r) => r.json()).then((data) => { if (!ignore) setResults(data); });\n"
            "    return () => { ignore = true; };\n"
            "  }, []);\n"
            "  useEffect(() => {\n"
            "    const id = setInterval(() => setIsDone(true), 1000);\n"
            "  }, []);\n"
            "  useEffect(() => store.subscribe(setResults), []);\n"
            "  const onVisit = useEffectEvent((id: string) => { log(id, draft); });\n"
            "  useEffect(() => { onVisit(query); }, [query]);\n"
            "  return <p>{draft}</p>;\n"
            "}\n"
        ))
        self.commit()
        snapshot = self.collect()["snapshot"]
        flags = {item["line"]: item["flags"] for item in snapshot["effects"]["candidates"]}
        self.assertEqual(flags, {
            8: ["only-sets-state"],
            9: ["notifies-parent"],
            10: ["async-without-cleanup"],
            18: ["subscribes-without-cleanup"],
        })
        self.assertEqual(snapshot["effects"]["total"], 7, "an effect that calls a useEffectEvent function is not a candidate")
        self.assertEqual(snapshot["state"]["booleanFlagGroups"][0]["booleans"], ["isLoading", "isError", "isDone"])
        self.assertEqual(snapshot["state"]["stateInitialisedFromProps"][0]["initialisedFrom"], "query")


class BoundsTests(CollectorTestCase):
    def test_large_files_are_skipped_listed_and_degrade_source_checks(self) -> None:
        self.package({"name": "web", "dependencies": {"react": "19.3.0"}})
        self.write("src/App.tsx", "export function App() { return <p />; }\n")
        self.write("src/generated.ts", "export const data = [" + "1," * 1_100_000 + "];\n")
        self.commit()
        result = self.collect()
        self.assertEqual(result["snapshot"]["skippedLargeFiles"]["examples"], ["src/generated.ts"])
        self.assertIn("src/generated.ts", self.check(result, "no-silenced-exhaustive-deps")["degradedReason"])

    def test_a_big_component_file_is_lexed_quickly(self) -> None:
        body = "".join(
            f"  useEffect(() => {{ setValue{index}(props.v{index} / 2); }}, [props.v{index}]); // note {index}\n"
            for index in range(3000)
        )
        text = "import { useEffect } from 'react';\nexport function Big(props: any) {\n" + body + "  return <p>{'x'}</p>;\n}\n"
        began = time.monotonic()
        source = collector.Source("src/Big.tsx", text)
        effects = collector.effects_in(source, {f"setValue{index}" for index in range(3000)})
        self.assertLess(time.monotonic() - began, 20)
        self.assertEqual(len(effects), collector.MAX_EFFECTS_PER_FILE)


class ReviewRegressionTests(CollectorTestCase):
    """Each case comes from a repository the adversarial review built to break the collector."""

    def hooks(self) -> dict[str, Any]:
        self.commit()
        return self.check(self.collect(), "react-hooks-plugin-enabled")

    def hooks_project(self) -> None:
        self.package({"name": "a", "dependencies": {"react": "19.1.0", "react-dom": "19.1.0"}, "devDependencies": {"eslint-plugin-react-hooks": "7.0.0", "eslint-config-next": "16.0.0", "eslint": "9"}})
        self.write("src/A.tsx", 'import { useState } from "react";\nexport function A() { const [a] = useState(0); return <p>{a}</p>; }\n')

    def test_nested_pnpm_negated_and_dotted_workspaces_are_found(self) -> None:
        self.write("frontend/package.json", '{"name":"web","dependencies":{"react":"19.1.0"}}\n')
        self.write("frontend/src/A.tsx", "export function A() { return <p>a</p>; }\n")
        self.write("backend/app.py", "print(1)\n")
        self.commit()
        packages = self.collect()["snapshot"]["packages"]
        self.assertEqual([item["package"] for item in packages], ["frontend/package.json"], "no workspace list: nested packages count")
        for workspace in ('packages: ["apps/*"]\n', "packages:\n- apps/*\n", "packages:\n  - 'apps/*' # apps\n  - '!apps/legacy'\n"):
            with self.subTest(workspace=workspace):
                self.write("package.json", '{"name":"root","private":true}\n')
                self.write("pnpm-workspace.yaml", workspace)
                self.write("apps/web/package.json", '{"name":"web","dependencies":{"react":"19.1.0"}}\n')
                self.write("apps/legacy/package.json", '{"name":"legacy","dependencies":{"react":"18.2.0"}}\n')
                self.commit()
                found = [item["package"] for item in self.collect()["snapshot"]["packages"]]
                self.assertIn("apps/web/package.json", found)
                self.assertEqual("apps/legacy/package.json" in found, "!apps/legacy" not in workspace)
        (self.root / "pnpm-workspace.yaml").unlink()
        self.write("package.json", '{"name":"root","private":true,"workspaces":["./apps/*","!apps/legacy","**"]}\n')
        self.write("node_modules/evil/package.json", '{"name":"evil","dependencies":{"react":"17.0.0"}}\n')
        self.write(".gitignore", "node_modules\n")
        self.commit()
        found = [item["package"] for item in self.collect()["snapshot"]["packages"]]
        self.assertNotIn("apps/legacy/package.json", found)
        self.assertFalse(any("node_modules" in item for item in found))

    def test_compiler_scoped_by_overrides_options_variables_or_conditions_is_not_everywhere(self) -> None:
        self.package({"name": "a", "dependencies": {"react": "19.1.0", "next": "16.0.0"}, "devDependencies": {"babel-plugin-react-compiler": "1.0.0"}})
        self.write("src/A.tsx", "export function A() { return <p>a</p>; }\n")
        cases = {
            "babel.config.js": "module.exports = {\n  plugins: [],\n  overrides: [\n    { test: './src/modern/**', plugins: ['babel-plugin-react-compiler'] },\n  ],\n};\n",
            "babel.config.json": '{"plugins": [], "overrides": [{"test": "./src/modern", "plugins": ["babel-plugin-react-compiler"]}]}\n',
            "next.config.ts": "const nextConfig = {\n  ...(process.env.ENABLE_COMPILER === '1' ? { reactCompiler: true } : {}),\n};\nexport default nextConfig;\n",
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                for other in cases:
                    if (self.root / other).exists():
                        (self.root / other).unlink()
                self.write(name, text)
                self.commit()
                result = self.collect()
                self.assertFalse(result["snapshot"]["compiler"][0]["everywhere"])
                self.assertNotIn(CHECK + "memoization-for-correctness", result["checks"])
        (self.root / "next.config.ts").unlink()
        self.write("babel.config.js", 'const ReactCompilerConfig = { compilationMode: "annotation" };\nmodule.exports = { plugins: [["babel-plugin-react-compiler", ReactCompilerConfig]] };\n')
        self.commit()
        self.assertEqual(self.collect()["snapshot"]["compiler"][0]["mode"], "annotation")
        self.write("babel.config.js", 'const options = require("./compiler-options");\nmodule.exports = { plugins: [["babel-plugin-react-compiler", options]] };\n')
        self.commit()
        compiler = self.collect()["snapshot"]["compiler"][0]
        self.assertFalse(compiler["everywhere"])
        self.assertIn("options", compiler["unread"][0])

    def test_compat_config_extglob_spread_extends_root_stop_js_spread_and_yaml_lists(self) -> None:
        self.hooks_project()
        self.write("eslint.config.mjs", 'import { FlatCompat } from "@eslint/eslintrc";\nconst compat = new FlatCompat({ baseDirectory: import.meta.dirname });\nexport default [\n  ...compat.config({ extends: ["next/core-web-vitals"] }),\n];\n')
        self.assertEqual(self.hooks()["status"], "present")
        self.write("eslint.config.mjs", 'import reactHooks from "eslint-plugin-react-hooks";\nexport default [\n  { files: ["**/*.ts?(x)"], ...reactHooks.configs.flat.recommended },\n];\n')
        self.assertEqual(self.hooks()["status"], "present")
        self.write("eslint.config.mjs", 'import { defineConfig } from "eslint/config";\nimport reactHooks from "eslint-plugin-react-hooks";\nconst hooks = { plugins: { "react-hooks": reactHooks }, extends: ["react-hooks/recommended"] };\nexport default defineConfig([\n  { ...hooks, files: ["src/**/*.tsx"] },\n]);\n')
        self.assertEqual(self.hooks()["status"], "present")
        self.write("eslint.config.mjs", 'import reactHooks from "eslint-plugin-react-hooks";\nexport default [\n  reactHooks.configs.flat\n    .recommended,\n];\n')
        item = self.hooks()
        self.assertEqual(item["status"], "present")
        self.assertNotIn("\n", item["evidence"][0], "evidence is one line")
        (self.root / "eslint.config.mjs").unlink()
        self.write(".eslintrc.json", '{"extends":["plugin:react-hooks/recommended-legacy"]}\n')
        self.write("src/.eslintrc.json", '{"root":true,"extends":["eslint:recommended"]}\n')
        self.assertEqual(self.hooks()["status"], "missing", "root: true stops the cascade above src")
        (self.root / ".eslintrc.json").unlink()
        (self.root / "src/.eslintrc.json").unlink()
        self.write(".eslintrc.js", 'const base = require("@acme/eslint-config");\nmodule.exports = {\n  ...base,\n  rules: { ...base.rules, "react-hooks/exhaustive-deps": "warn" },\n};\n')
        self.assertEqual(self.hooks()["evaluationState"], "not-evaluated")
        (self.root / ".eslintrc.js").unlink()
        self.write(".eslintrc.yml", "plugins:\n  - react-hooks\nrules:\n  react-hooks/rules-of-hooks:\n    - error\n  react-hooks/exhaustive-deps:\n    - warn\n")
        item = self.hooks()
        self.assertEqual(item["status"], "partial")
        self.assertIn("React Compiler rules", item["gap"])

    def test_odd_json_shapes_do_not_crash(self) -> None:
        self.package({"name": "a", "dependencies": {"react": "19.1.0"}, "eslintConfig": {"overrides": [{"files": 7}], "root": "yes"}})
        self.write("tsconfig.json", '{"compilerOptions": []}\n')
        self.write("babel.config.json", '{"plugins": [{"name": "x"}, 3]}\n')
        self.write("app.json", '{"expo": []}\n')
        self.write("biome.json", '[]\n')
        self.write("src/A.tsx", "export function A() { return <p>a</p>; }\n")
        self.commit()
        self.assertEqual(self.check(self.collect(), "react-hooks-plugin-enabled")["status"], "missing")

    def test_client_safe_packages_browser_conditions_and_generic_arrows_are_not_server_code(self) -> None:
        self.package({"name": "a", "dependencies": {"react": "19.1.0", "react-dom": "19.1.0", "next": "16.0.0", "@supabase/ssr": "0.5.0"}})
        self.write("app/page.tsx", 'import { Login } from "./Login";\nimport { Help } from "./Help";\nexport default function Page() { return <><Login /><Help map={(x) => x} /></>; }\n')
        self.write("app/Login.tsx", '"use client";\nimport { supabase } from "../lib/client";\nexport function Login() { return <button onClick={() => supabase()}>Log in</button>; }\n')
        self.write("lib/client.ts", 'import { createBrowserClient } from "@supabase/ssr";\nexport const supabase = () => createBrowserClient(process.env.NEXT_PUBLIC_SUPABASE_URL!, process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY!);\n')
        self.write("app/Help.tsx", '"use client";\ntype Mapper = <T>(value: T) => T;\nconst hint = "Set process.env.DATABASE_URL on the server";\nexport function Help({ map }: { map: Mapper }) { return <p>{map(hint)}</p>; }\n')
        self.commit()
        self.assertEqual(self.check(self.collect(), "client-modules-import-no-server-code")["status"], "present")
        self.package({"name": "root", "private": True, "workspaces": ["apps/*", "packages/*"]})
        self.write("apps/web/package.json", '{"name":"web","dependencies":{"react":"19.1.0","next":"16.0.0","@acme/data":"*"}}\n')
        self.write("apps/web/app/C.tsx", '"use client";\nimport { load } from "@acme/data";\nexport function C() { return <button onClick={() => load()}>x</button>; }\n')
        self.write("apps/web/app/page.tsx", 'import { C } from "./C";\nexport default function Page() { return <C />; }\n')
        self.write("packages/data/package.json", '{"name":"@acme/data","exports":{".":{"react-server":"./src/server.ts","default":"./src/client.ts"}},"dependencies":{"pg":"8"}}\n')
        self.write("packages/data/src/client.ts", 'export const load = () => fetch("/api");\n')
        self.write("packages/data/src/server.ts", 'import { Pool } from "pg";\nexport const load = () => new Pool();\n')
        for path in ("app/page.tsx", "app/Login.tsx", "app/Help.tsx", "lib/client.ts"):
            (self.root / path).unlink()
        self.commit()
        self.assertEqual(self.check(self.collect(), "client-modules-import-no-server-code")["status"], "present")

    def test_private_variables_are_cited_on_their_own_line_including_destructuring(self) -> None:
        self.next_app()
        self.write("src/components/Pay.tsx", '"use client";\nconst key = process.env\n  .STRIPE_SECRET_KEY;\nexport function Pay() { return <p>{key}</p>; }\n')
        self.write("src/components/Keys.tsx", '"use client";\nconst { PAYMENT_TOKEN, NEXT_PUBLIC_SITE } = process.env;\nexport function Keys() { return <p>{PAYMENT_TOKEN}{NEXT_PUBLIC_SITE}</p>; }\n')
        self.commit()
        result = self.collect()
        evidence = self.check(result, "client-modules-import-no-server-code")["evidence"]
        self.assertIn("src/components/Pay.tsx:3 — `STRIPE_SECRET_KEY` reads the private environment variable STRIPE_SECRET_KEY, and it is bundled for the browser", evidence)
        markers = [item["markers"] for item in result["snapshot"]["directives"]["serverOnlyReachedFromClient"]]
        self.assertIn(["reads the private environment variable PAYMENT_TOKEN"], markers)

    def test_lone_carriage_returns_do_not_shift_line_numbers(self) -> None:
        self.package({"name": "a", "dependencies": {"react": "19.1.0"}})
        self.write("src/B.tsx", '"use client";\r\nimport { forwardRef } from "react";\r\r\rexport const B = forwardRef<HTMLDivElement>(function B(p, ref) { return <div ref={ref} />; });\r')
        self.commit()
        self.assertEqual(self.check(self.collect(), "ref-passed-as-a-prop")["evidence"], ["src/B.tsx:2 — `forwardRef`"])

    def test_waku_pages_are_exempt_from_named_exports(self) -> None:
        self.package({"name": "a", "dependencies": {"react": "19.1.0", "react-dom": "19.1.0", "waku": "0.27.0", "react-server-dom-webpack": "19.1.0"}})
        self.write("src/pages/_layout.tsx", 'import type { ReactNode } from "react";\nexport default async function RootLayout({ children }: { children: ReactNode }) { return <main>{children}</main>; }\n')
        self.write("src/pages/index.tsx", 'export default async function HomePage() { return <h1>Home</h1>; }\n')
        self.commit()
        self.assertEqual(self.check(self.collect(), "named-exports-for-components")["status"], "present")

    def test_optional_and_prop_member_callbacks_notify_the_parent(self) -> None:
        self.package({"name": "a", "dependencies": {"react": "19.1.0"}})
        self.write("src/Field.tsx", (
            'import { useEffect, useState } from "react";\n'
            'export function Field({ onChange }: { onChange?: (v: string) => void }) {\n'
            '  const [value, setValue] = useState("");\n'
            '  useEffect(() => {\n    onChange?.(value);\n  }, [value, onChange]);\n'
            '  return <input value={value} onChange={(e) => setValue(e.target.value)} />;\n}\n'
            'export function Field2(props: { onChange: (v: string) => void }) {\n'
            '  const [value, setValue] = useState("");\n'
            '  useEffect(() => {\n    props.onChange(value);\n  }, [value, props]);\n'
            '  return <input value={value} onChange={(e) => setValue(e.target.value)} />;\n}\n'
        ))
        self.commit()
        candidates = self.collect()["snapshot"]["effects"]["candidates"]
        self.assertEqual([item["flags"] for item in candidates], [["notifies-parent"], ["notifies-parent"]])

    def test_big_lockfiles_still_resolve_the_version(self) -> None:
        packages = {"": {"dependencies": {"react": ">=18"}}, "node_modules/react": {"version": "19.1.0"}}
        packages.update({f"node_modules/pkg-{index}": {"version": "1.0.0", "integrity": "sha512-" + "a" * 80} for index in range(30_000)})
        self.write("package-lock.json", json.dumps({"lockfileVersion": 3, "packages": packages}))
        self.assertGreater((self.root / "package-lock.json").stat().st_size, collector.MAX_FILE_BYTES)
        self.package({"name": "a", "dependencies": {"react": ">=18"}})
        self.write("src/A.tsx", "export function A() { return <p />; }\n")
        self.commit()
        self.assertEqual(self.collect()["snapshot"]["packages"][0]["resolved"], "19.1.0")

    def test_suppressions_count_only_at_the_start_of_a_comment(self) -> None:
        self.package({"name": "a", "dependencies": {"react": "19.1.0"}})
        self.write("src/A.tsx", "// We removed the eslint-disable-next-line react-hooks/exhaustive-deps from here.\nexport function A() { return <p />; }\n")
        self.commit()
        self.assertEqual(self.collect()["snapshot"]["exhaustiveDepsSuppressions"]["total"], 0)

    def test_upper_bound_ranges_gate_below_the_bound(self) -> None:
        self.assertEqual(collector.lowest_version("<19"), (18, 0))
        self.assertEqual(collector.lowest_version("<=19.0.0"), (18, 0))
        self.assertEqual(collector.lowest_version(">=18 <20"), (18, 0))
        self.assertEqual(collector.lowest_version("<18.3"), (18, 0))

    def test_a_non_null_assertion_before_division_is_not_a_regular_expression(self) -> None:
        masked = collector.mask("const half = total! / 2; forwardRef(Thing); const other = /x/.test(y);\n", "a.ts")
        self.assertIn("total! / 2", masked)
        self.assertIn("forwardRef(Thing)", masked)
        self.assertNotIn("/x/", masked, "a real regular expression is still masked")

    def test_many_misplaced_directive_lines_scan_linearly(self) -> None:
        text = "export function f() {}\n" + "'use client';\n" * 20_000
        began = time.monotonic()
        found = collector.misplaced_directives(collector.Source("src/a.ts", text), [])
        self.assertLess(time.monotonic() - began, 10)
        self.assertEqual(len(found), 20_000)


class EvaluationFixtureTests(unittest.TestCase):
    """The collector decides every tool check the evaluation graders expect, with the status they expect."""

    def test_tool_check_graders_match_the_collector(self) -> None:
        catalog = json.loads((ROOT / "react-audit" / "checks.json").read_text(encoding="utf-8"))
        by_title = {check["title"]: check for check in catalog["checks"]}
        for case in ("tangled-ui", "well-built-ui"):
            folder = ROOT / "evals" / "react-audit" / case
            with tempfile.TemporaryDirectory() as directory:
                subprocess.run(["bash", str(folder / "fixture.sh")], cwd=directory, check=True, capture_output=True)
                result = collector.collect(Path(directory))
                for item in result["checks"].values():
                    for entry in item.get("evidence", []):
                        protocol.verify_evidence(Path(directory), entry)
            graded = 0
            for grader in sorted((folder / "graders").glob("*.md")):
                text = grader.read_text(encoding="utf-8")
                pattern = re.search(r'^pattern: (".*")$', text, re.M)
                if not pattern:
                    continue
                expression = json.loads(pattern.group(1))
                title = re.search(r"\\\| ([^|\\]+?) \\\| \\w\+ \\\| \(([^)]*)\) \\\|$", expression)
                assert title is not None, grader
                check = by_title[title.group(1).replace("\\(", "(").replace("\\)", ")")]
                result_item = result["checks"].get(check["checkId"])
                if result_item is None:
                    self.assertEqual(check["method"], "model", f"{case}: {grader.stem} expects a tool check the collector left pending")
                    continue
                status = result_item.get("status") or ("not applicable" if result_item.get("applicability") == "not-applicable" else "not evaluated")
                self.assertIn(status, title.group(2).split("|"), f"{case}: {grader.stem}")
                graded += 1
            self.assertGreaterEqual(graded, 6, case)


class CommandLineAndProtocolTests(CollectorTestCase):
    def test_months_threshold_is_parsed_and_validated(self) -> None:
        self.assertEqual(collector.history_months(["months=12"], 6), 12)
        with self.assertRaises(SystemExit):
            collector.history_months(["months=zero"], 6)
        with self.assertRaises(SystemExit):
            collector.history_months(["months=0"], 6)

    def test_paths_with_spaces_and_escaped_quotes_survive_verification(self) -> None:
        self.write("package.json", '{\n  "name": "web",\n  "dependencies": {"react": "19.3.0", "react-scripts": "5.0.1"},\n  "scripts": {"build": "react-scripts build --name \\"web app\\""}\n}\n')
        self.write("src/my folder/Field.tsx", "import { forwardRef } from 'react';\nexport default forwardRef((props, ref) => <input ref={ref} />);\n")
        self.commit()
        result = self.collect()
        self.assertEqual(self.check(result, "ref-passed-as-a-prop")["evidence"], ["`src/my folder/Field.tsx`:2 — `forwardRef`"])

    def test_begin_accepts_every_collector_result(self) -> None:
        self.next_app()
        self.write("src/lib/payments.ts", "export function total(cents: number) { return cents + Number(process.env.FEE_CENTS); }\n")
        self.write("src/components/Total.tsx", "'use client';\nimport { total } from '@/lib/payments';\nimport { forwardRef } from 'react';\nexport default function Total() { return <p>{total(1)}</p>; }\nexport const Box = forwardRef<HTMLDivElement>((props, ref) => <div ref={ref} />);\n")
        self.write("src/components/Effects.tsx", "import { useEffect } from 'react';\nexport function Effects({ id }: { id: string }) {\n  useEffect(() => {\n    track(id);\n    // eslint-disable-next-line react-hooks/exhaustive-deps\n  }, []);\n  return null;\n}\n")
        self.commit()
        completed = subprocess.run(
            [sys.executable, str(PROTOCOL), "--repository", str(self.root), "begin", "react-audit"],
            capture_output=True, text=True, check=True,
        )
        self.assertNotIn("rejected", completed.stdout)
        self.assertNotIn("Warning", completed.stdout)
        collected = collector.collect(self.root)
        self.assertIn(f"Collector resolved {len(collected['checks'])} checks.", completed.stdout)
        staged = json.loads((self.root / ".architect-audits" / "react-audit" / ".staging" / "run.json").read_text())
        statuses = {check["checkId"]: (check["status"], check["recordedBy"]) for check in staged["checks"] if check["recordedBy"]}
        self.assertEqual(statuses[CHECK + "client-modules-import-no-server-code"], ("violation", "collector"))
        self.assertEqual(statuses[CHECK + "ref-passed-as-a-prop"], ("partial", "collector"))
        self.assertEqual(statuses[CHECK + "no-silenced-exhaustive-deps"], ("partial", "collector"))
        self.assertEqual(statuses[CHECK + "named-exports-for-components"], ("partial", "collector"))
        self.assertEqual(statuses[CHECK + "react-hooks-plugin-enabled"], ("present", "collector"))
        self.assertEqual(staged["snapshot"]["reactDetected"], True)


if __name__ == "__main__":
    unittest.main()
