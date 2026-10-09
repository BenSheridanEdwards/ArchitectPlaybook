from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
