from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "audit-protocol" / "scripts" / "audit_run.py"

spec = importlib.util.spec_from_file_location("audit_run", SCRIPT)
assert spec and spec.loader
audit_run = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit_run
spec.loader.exec_module(audit_run)

AUDIT = "architecture-audit"
VIOLATION_CHECK = "architecture-audit.data-fetching-layer-separated"
PRESENT_CHECK = "architecture-audit.no-circular-dependencies"
SEARCH_CHECK = "architecture-audit.no-deep-relative-imports"


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


class AuditRunTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name) / "target"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.ts").write_text(
            "export const a = 1;\nexport function load() {\n  return fetch('/x');\n}\n",
            encoding="utf-8",
        )
        (self.root / "package.json").write_text('{"name": "demo"}\n', encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "initial")

    def tearDown(self) -> None:
        self.directory.cleanup()

    def run_command(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = audit_run.main(["--repository", str(self.root), *arguments])
        return code, output.getvalue()

    def staged(self) -> dict[str, Any]:
        return json.loads(
            (self.root / ".architect-audits" / AUDIT / ".staging" / "run.json").read_text(encoding="utf-8")
        )

    def resolve_remaining(self) -> None:
        for check in self.staged()["checks"]:
            if check["evaluationReason"] == "pending":
                code, output = self.run_command("not-evaluated", AUDIT, check["checkId"], "--reason", "test scope")
                self.assertEqual(code, 0, output)

    def test_begin_stages_every_catalog_check_as_pending(self) -> None:
        code, output = self.run_command("begin", AUDIT)
        self.assertEqual(code, 0, output)
        run = self.staged()
        catalog = json.loads((ROOT / AUDIT / "checks.json").read_text(encoding="utf-8"))
        self.assertEqual(len(run["checks"]), len(catalog["checks"]))
        self.assertTrue(all(check["evaluationReason"] == "pending" for check in run["checks"]))
        self.assertTrue(run["target"]["sourceWorkingTreeClean"])

    def test_begin_refuses_to_overwrite_a_staged_run_without_restart(self) -> None:
        self.run_command("begin", AUDIT)
        code, output = self.run_command("begin", AUDIT)
        self.assertEqual(code, 1)
        self.assertIn("--restart", output)
        code, _ = self.run_command("begin", AUDIT, "--restart")
        self.assertEqual(code, 0)

    def test_record_verifies_cited_lines_and_quotes(self) -> None:
        self.run_command("begin", AUDIT)
        common = ["--status", "violation", "--gap", "Direct fetch.", "--remediation", "Use a query hook."]
        code, output = self.run_command(
            "record", AUDIT, VIOLATION_CHECK, "--evidence", "src/a.ts:3 — `fetch('/x')` in a module", *common
        )
        self.assertEqual(code, 0, output)
        code, output = self.run_command(
            "record", AUDIT, VIOLATION_CHECK, "--evidence", "src/a.ts:3 — `axios.get('/x')`", *common
        )
        self.assertEqual(code, 1)
        self.assertIn("does not appear", output)
        code, output = self.run_command("record", AUDIT, VIOLATION_CHECK, "--evidence", "src/a.ts:40", *common)
        self.assertEqual(code, 1)
        self.assertIn("outside", output)
        code, output = self.run_command("record", AUDIT, VIOLATION_CHECK, "--evidence", "src/missing.ts:1", *common)
        self.assertEqual(code, 1)
        self.assertIn("does not exist", output)

    def test_citations_accept_extensionless_files(self) -> None:
        (self.root / "Makefile").write_text("build:\n\ttsc --noEmit\n", encoding="utf-8")
        git(self.root, "add", "Makefile")
        git(self.root, "commit", "-q", "-m", "add makefile")
        self.run_command("begin", AUDIT)
        code, output = self.run_command(
            "record", AUDIT, PRESENT_CHECK, "--status", "present", "--evidence", "Makefile:2 — `tsc --noEmit`"
        )
        self.assertEqual(code, 0, output)
        code, output = self.run_command(
            "record", AUDIT, PRESENT_CHECK, "--status", "present", "--evidence", "Makefile:2 — `eslint .`"
        )
        self.assertEqual(code, 1)
        self.assertIn("does not appear", output)

    def test_record_re_runs_search_evidence(self) -> None:
        self.run_command("begin", AUDIT)
        code, output = self.run_command(
            "record", AUDIT, SEARCH_CHECK, "--status", "present", "--evidence", "search: `../../../` in src → 3 matches"
        )
        self.assertEqual(code, 1)
        self.assertIn("the repository has 0", output)
        code, output = self.run_command(
            "record", AUDIT, SEARCH_CHECK, "--status", "present", "--evidence", "search: `fetch(` in src → 1 match"
        )
        self.assertEqual(code, 0, output)

    def test_record_rejects_unverifiable_and_weak_violations(self) -> None:
        self.run_command("begin", AUDIT)
        code, output = self.run_command("record", AUDIT, PRESENT_CHECK, "--status", "present", "--evidence", "Looks fine to me")
        self.assertEqual(code, 1)
        self.assertIn("verifiable entry", output)
        code, output = self.run_command(
            "record", AUDIT, VIOLATION_CHECK, "--status", "violation", "--tier", "inferred",
            "--evidence", "src/a.ts:3", "--gap", "g", "--remediation", "r",
        )
        self.assertEqual(code, 1)
        self.assertIn("direct or supported", output)
        code, output = self.run_command("record", AUDIT, VIOLATION_CHECK, "--status", "violation", "--evidence", "src/a.ts:3")
        self.assertEqual(code, 1)
        self.assertIn("--gap", output)

    def test_record_rejects_secret_like_evidence(self) -> None:
        self.run_command("begin", AUDIT)
        code, output = self.run_command(
            "record", AUDIT, PRESENT_CHECK, "--status", "present",
            "--evidence", "command: `cat .env` → AKIAABCDEFGHIJKLMNOP",
        )
        self.assertEqual(code, 1)
        self.assertIn("secret", output)

    def test_dismissed_findings_need_a_reason(self) -> None:
        self.run_command("begin", AUDIT)
        arguments = [
            "record", AUDIT, VIOLATION_CHECK, "--status", "violation", "--evidence", "src/a.ts:3",
            "--gap", "g", "--remediation", "r", "--judgement", "dismissed",
        ]
        code, output = self.run_command(*arguments)
        self.assertEqual(code, 1)
        self.assertIn("--reason", output)
        code, output = self.run_command(*arguments, "--reason", "Only a prototype route.")
        self.assertEqual(code, 0, output)

    def test_finish_refuses_pending_checks(self) -> None:
        self.run_command("begin", AUDIT)
        code, output = self.run_command("finish", AUDIT)
        self.assertEqual(code, 1)
        self.assertIn("no recorded result", output)
        self.assertFalse((self.root / ".architect-audits" / AUDIT / "findings.json").exists())

    def test_finish_refuses_when_the_repository_changes_during_the_run(self) -> None:
        self.run_command("begin", AUDIT)
        self.resolve_remaining()
        (self.root / "src" / "b.ts").write_text("export const b = 2;\n", encoding="utf-8")
        code, output = self.run_command("finish", AUDIT)
        self.assertEqual(code, 1)
        self.assertIn("changed during the run", output)

    def test_finish_publishes_a_run_the_score_calculator_accepts(self) -> None:
        self.run_command("begin", AUDIT)
        self.run_command(
            "record", AUDIT, PRESENT_CHECK, "--status", "present", "--tier", "supported",
            "--evidence", "command: `npx madge --circular src` → no cycles",
        )
        self.run_command(
            "record", AUDIT, VIOLATION_CHECK, "--status", "violation", "--evidence", "src/a.ts:3 — `fetch('/x')`",
            "--gap", "Direct fetch.", "--remediation", "Use a query hook.",
        )
        self.resolve_remaining()
        code, output = self.run_command("finish", AUDIT)
        self.assertEqual(code, 0, output)
        self.assertIn("Act on", output)
        output_directory = self.root / ".architect-audits" / AUDIT
        for name in ("findings.json", "findings.md", "snapshot.md", "metadata.json"):
            self.assertTrue((output_directory / name).is_file(), name)
        self.assertFalse((output_directory / ".staging").exists())
        findings = json.loads((output_directory / "findings.json").read_text(encoding="utf-8"))
        metadata = json.loads((output_directory / "metadata.json").read_text(encoding="utf-8"))
        catalog = audit_run.CALCULATOR.load_catalog(ROOT, AUDIT, {})
        candidate = audit_run.CALCULATOR.parse_canonical_candidate(
            findings, metadata, catalog, output_directory / "findings.json", "test"
        )
        self.assertEqual(candidate.run_identifier, findings["runIdentifier"])
        markdown = (output_directory / "findings.md").read_text(encoding="utf-8")
        self.assertIn("## Act on", markdown)
        self.assertIn("Data fetching layer separated", markdown)
        self.assertIn("## Snapshot", markdown)

    def test_audit_level_not_applicable_marks_every_check(self) -> None:
        self.run_command("begin", AUDIT)
        code, _ = self.run_command("audit-not-applicable", AUDIT, "--reason", "No TypeScript sources.")
        self.assertEqual(code, 0)
        code, output = self.run_command("finish", AUDIT)
        self.assertEqual(code, 0, output)
        findings = json.loads(
            (self.root / ".architect-audits" / AUDIT / "findings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(findings["auditApplicability"]["status"], "not-applicable")
        self.assertTrue(all(check["applicability"] == "not-applicable" for check in findings["checks"]))

    def test_recorded_decisions_keep_findings_out_of_act_on(self) -> None:
        code, output = self.run_command(
            "decide", VIOLATION_CHECK, "--decision", "accepted-risk", "--reason", "Legacy route, retiring in Q1.",
            "--owner", "Ben", "--scope", "src/**",
        )
        self.assertEqual(code, 0, output)
        self.run_command("begin", AUDIT)
        code, output = self.run_command(
            "record", AUDIT, VIOLATION_CHECK, "--status", "violation", "--evidence", "src/a.ts:3",
            "--gap", "Direct fetch.", "--remediation", "Use a query hook.",
        )
        self.assertEqual(code, 0, output)
        check = next(item for item in self.staged()["checks"] if item["checkId"] == VIOLATION_CHECK)
        self.assertEqual(check["judgement"], "noted")
        self.assertEqual(check["decisionId"], "decision-001")
        self.assertEqual(check["status"], "violation")
        code, output = self.run_command(
            "record", AUDIT, VIOLATION_CHECK, "--status", "violation", "--evidence", "src/a.ts:3",
            "--gap", "Direct fetch.", "--remediation", "Use a query hook.", "--judgement", "act-on",
        )
        self.assertEqual(code, 1)
        self.assertIn("decision-001", output)

    def test_since_records_a_filtered_run(self) -> None:
        first = subprocess.run(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
        ).stdout.strip()
        (self.root / "src" / "b.ts").write_text("export const b = 2;\n", encoding="utf-8")
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", "second")
        code, output = self.run_command("begin", AUDIT, "--since", first)
        self.assertEqual(code, 0, output)
        run = self.staged()
        self.assertEqual(run["scope"]["changedFiles"], ["src/b.ts"])
        self.assertTrue(run["execution"]["filtersApplied"])
        self.assertEqual(run["execution"]["filterArguments"], [f"--since={first}"])

    def test_hotspots_ranks_frequently_changed_files(self) -> None:
        for number in range(3):
            (self.root / "src" / "a.ts").write_text(f"export const a = {number};\n", encoding="utf-8")
            git(self.root, "commit", "-q", "-am", f"change {number}")
        code, output = self.run_command("hotspots", "--json")
        self.assertEqual(code, 0, output)
        ranked = json.loads(output)
        self.assertEqual(ranked[0]["path"], "src/a.ts")


if __name__ == "__main__":
    unittest.main()
