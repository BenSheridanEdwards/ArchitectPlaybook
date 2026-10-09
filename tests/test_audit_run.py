from __future__ import annotations

import contextlib
import errno
import importlib.util
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any

for _variable in (
    "GIT_DIR",
    "GIT_INDEX_FILE",
    "GIT_WORK_TREE",
    "GIT_PREFIX",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_COMMON_DIR",
):
    os.environ.pop(_variable, None)

ROOT = Path(__file__).resolve().parents[1]
AUDIT = "fixture-audit"
FETCH_CHECK = f"{AUDIT}.data-fetching-in-one-place"
CYCLES_CHECK = f"{AUDIT}.no-import-cycles"
IMPORTS_CHECK = f"{AUDIT}.shallow-relative-imports"
LANGUAGE_CHECK = f"{AUDIT}.domain-language"
COLLECTOR_AUDIT = "collector-audit"

# The tests run the protocol against a fixture skills root: a copy of the real
# protocol and calculator beside two small fixture audits. They never depend on
# the check identifiers of a real audit, which change as audits are rewritten.
FIXTURE_CATALOG = {
    "schemaVersion": "1.1.0",
    "catalogVersion": "1.0.0",
    "skillName": AUDIT,
    "checks": [
        {
            "checkId": FETCH_CHECK,
            "layer": "Layer 2 — Data",
            "title": "Data fetching in one place",
            "severity": "high",
            "method": "model",
            "rationale": "Scattered fetching hides caching and error handling.",
        },
        {"checkId": CYCLES_CHECK, "layer": "Layer 1 — Structure", "title": "No import cycles", "severity": "high", "method": "tool"},
        {"checkId": IMPORTS_CHECK, "layer": "Layer 1 — Structure", "title": "Shallow relative imports", "severity": "medium", "method": "model"},
        {
            "checkId": LANGUAGE_CHECK,
            "layer": "Layer 4 — Language",
            "title": "Domain language",
            "softCheck": True,
            "allowedStatuses": ["present", "partial", "missing"],
            "severity": "low",
            "method": "model",
        },
    ]
    + [
        {"checkId": f"{AUDIT}.extra-{number}", "layer": "Layer 3 — Change", "title": f"Extra check {number}", "severity": "medium"}
        for number in range(1, 5)
    ],
}
COLLECTOR_CATALOG = {
    "schemaVersion": "1.1.0",
    "catalogVersion": "1.0.0",
    "skillName": COLLECTOR_AUDIT,
    "checks": [
        {"checkId": f"{COLLECTOR_AUDIT}.tool-check", "layer": "Layer 1 — Tools", "title": "Tool check"},
        {"checkId": f"{COLLECTOR_AUDIT}.bad-check", "layer": "Layer 1 — Tools", "title": "Bad check"},
        {"checkId": f"{COLLECTOR_AUDIT}.lint-check", "layer": "Layer 1 — Tools", "title": "Lint check"},
        {"checkId": f"{COLLECTOR_AUDIT}.model-check", "layer": "Layer 2 — Model", "title": "Model check"},
    ],
}
COLLECTOR_SCRIPT = """import argparse, json, sys
parser = argparse.ArgumentParser()
parser.add_argument("--repository", required=True)
parser.add_argument("--enrichment", action="append", default=[])
parser.add_argument("--threshold", action="append", default=[])
arguments = parser.parse_args()
print(json.dumps({
    "checks": {
        "collector-audit.tool-check": {"status": "present", "evidence": ["command: `collect.py scan` → 0 problems"]},
        "collector-audit.bad-check": {
            "status": "violation", "evidence": ["src/missing.ts:1 — `x`"], "gap": "g", "remediation": "r",
        },
        "collector-audit.lint-check": {
            "status": "partial", "evidence": ["command: `collect.py lint` → plugin missing"],
            "gap": "A lint plugin is missing.", "remediation": "Add the plugin.",
        },
    },
    "snapshot": {
        "enrichment": arguments.enrichment,
        "threshold": arguments.threshold,
        "dontWriteBytecode": sys.flags.dont_write_bytecode,
    },
}))
"""

SKILLS: Path
SCRIPT: Path
audit_run: Any
_skills_directory: tempfile.TemporaryDirectory[str]


def build_skills_root(destination: Path) -> None:
    shutil.copytree(ROOT / "audit-protocol", destination / "audit-protocol", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(
        ROOT / "repository-quality-score",
        destination / "repository-quality-score",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name, catalog in ((AUDIT, FIXTURE_CATALOG), (COLLECTOR_AUDIT, COLLECTOR_CATALOG)):
        (destination / name).mkdir()
        (destination / name / "checks.json").write_text(json.dumps(catalog, indent=2), encoding="utf-8")
    (destination / COLLECTOR_AUDIT / "scripts").mkdir()
    (destination / COLLECTOR_AUDIT / "scripts" / "collect.py").write_text(COLLECTOR_SCRIPT, encoding="utf-8")


def setUpModule() -> None:
    global SKILLS, SCRIPT, audit_run, _skills_directory
    _skills_directory = tempfile.TemporaryDirectory()
    SKILLS = Path(_skills_directory.name).resolve()
    build_skills_root(SKILLS)
    SCRIPT = SKILLS / "audit-protocol" / "scripts" / "audit_run.py"
    spec = importlib.util.spec_from_file_location("audit_run_under_test", SCRIPT)
    assert spec and spec.loader
    audit_run = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = audit_run
    spec.loader.exec_module(audit_run)


def tearDownModule() -> None:
    _skills_directory.cleanup()


def git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *arguments], check=True, capture_output=True, text=True
    ).stdout.strip()


def fake_secret(*parts: str) -> str:
    """Build secret-shaped test strings at run time so no scanner sees a literal key."""
    return "".join(parts)


class AuditRunTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve() / "target"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.email", "test@example.com")
        git(self.root, "config", "user.name", "Test")
        self.write("src/a.ts", "export const a = 1;\nexport function load() {\n  return fetch('/x');\n}\n")
        self.write("package.json", '{"name": "demo"}\n')
        self.commit("initial")

    def write(self, relative: str, text: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def commit(self, message: str = "change") -> None:
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "-m", message)

    def run_command(self, *arguments: str) -> tuple[int, str]:
        output = io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
            code = audit_run.main(["--repository", str(self.root), *arguments])
        return code, output.getvalue()

    def run_script(self, *arguments: str, script: Path | None = None) -> subprocess.CompletedProcess[str]:
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
        return subprocess.run(
            [sys.executable, str(script or SCRIPT), "--repository", str(self.root), *arguments],
            capture_output=True,
            text=True,
            timeout=300,
            env=environment,
        )

    def staged(self, audit: str = AUDIT) -> dict[str, Any]:
        return json.loads(
            (self.root / ".architect-audits" / audit / ".staging" / "run.json").read_text(encoding="utf-8")
        )

    def check(self, check_id: str, audit: str = AUDIT) -> dict[str, Any]:
        return next(item for item in self.staged(audit)["checks"] if item["checkId"] == check_id)

    def resolve_remaining(self) -> None:
        code, output = self.run_command("not-evaluated", AUDIT, "--remaining", "--reason", "test scope")
        self.assertEqual(code, 0, output)

    def record(self, check: str, *arguments: str) -> tuple[int, str]:
        return self.run_command("record", AUDIT, check, *arguments)

    def assert_rejected(self, result: tuple[int, str], fragment: str) -> None:
        code, output = result
        self.assertEqual(code, 1, output)
        self.assertIn(fragment, output)

    def assert_accepted(self, result: tuple[int, str]) -> None:
        code, output = result
        self.assertEqual(code, 0, output)


class LifecycleTests(AuditRunTestCase):
    def test_begin_stages_every_catalog_check_as_pending(self) -> None:
        self.assert_accepted(self.run_command("begin", AUDIT))
        run = self.staged()
        self.assertEqual(len(run["checks"]), len(FIXTURE_CATALOG["checks"]))
        self.assertTrue(all(check["recordedBy"] is None for check in run["checks"]))
        self.assertTrue(run["target"]["sourceWorkingTreeClean"])

    def test_begin_refuses_to_overwrite_a_staged_run_without_restart(self) -> None:
        self.run_command("begin", AUDIT)
        self.assert_rejected(self.run_command("begin", AUDIT), "--restart")
        self.assert_accepted(self.run_command("begin", AUDIT, "--restart"))

    def test_finish_refuses_pending_checks(self) -> None:
        self.run_command("begin", AUDIT)
        self.assert_rejected(self.run_command("finish", AUDIT), "no recorded result")
        self.assertFalse((self.root / ".architect-audits" / AUDIT / "findings.json").exists())

    def test_finish_refuses_when_the_repository_changes_during_the_run(self) -> None:
        self.run_command("begin", AUDIT)
        self.resolve_remaining()
        self.write("src/b.ts", "export const b = 2;\n")
        self.assert_rejected(self.run_command("finish", AUDIT), "changed during the run")

    def test_a_generated_knowledge_graph_keeps_the_tree_clean(self) -> None:
        self.write("graphify-out/graph.json", "{}\n")
        self.assert_accepted(self.run_command("begin", AUDIT))
        self.assertTrue(self.staged()["target"]["sourceWorkingTreeClean"])
        self.resolve_remaining()
        self.write("graphify-out/GRAPH_REPORT.md", "# Report\n")
        self.assert_accepted(self.run_command("finish", AUDIT))

    def test_finish_notices_same_size_edits_to_untracked_files(self) -> None:
        notes = self.write("notes.txt", "alpha\n")
        self.run_command("begin", AUDIT)
        self.resolve_remaining()
        before = notes.stat()
        notes.write_text("omega\n", encoding="utf-8")
        os.utime(notes, ns=(before.st_atime_ns, before.st_mtime_ns))
        self.assert_rejected(self.run_command("finish", AUDIT), "changed during the run")

    def test_finish_notices_further_edits_to_an_already_dirty_file(self) -> None:
        self.write("src/a.ts", "export const a = 2;\n")
        self.run_command("begin", AUDIT)
        self.resolve_remaining()
        self.write("src/a.ts", "export const a = 3;\n")
        self.assert_rejected(self.run_command("finish", AUDIT), "changed during the run")

    def test_finish_publishes_a_run_the_score_calculator_accepts(self) -> None:
        self.run_command("begin", AUDIT)
        self.assert_accepted(
            self.record(
                CYCLES_CHECK, "--status", "present",
                "--evidence", "command: `npx madge --circular src` → no cycles",
                "--evidence", "package.json — `\"name\": \"demo\"`",
            )
        )
        self.assert_accepted(
            self.record(
                FETCH_CHECK, "--status", "violation", "--tier", "direct",
                "--evidence", "src/a.ts:3 — `fetch('/x')`", "--evidence", "note: the only data call",
                "--gap", "Direct fetch.", "--remediation", "Use a query hook.",
            )
        )
        self.resolve_remaining()
        code, output = self.run_command("finish", AUDIT)
        self.assertEqual(code, 0, output)
        self.assertIn("Act on", output)
        output_directory = self.root / ".architect-audits" / AUDIT
        for name in ("findings.json", "findings.md", "snapshot.md", "metadata.json"):
            self.assertTrue((output_directory / name).is_file(), name)
            self.assertEqual(stat.S_IMODE((output_directory / name).stat().st_mode), audit_run.FILE_MODE, name)
        self.assertFalse((output_directory / ".staging").exists())
        fingerprints: dict[Path, Any] = {}
        findings = audit_run.CALCULATOR.strict_json_load(output_directory / "findings.json", fingerprints)
        metadata = audit_run.CALCULATOR.strict_json_load(output_directory / "metadata.json", fingerprints)
        catalog = audit_run.CALCULATOR.load_catalog(SKILLS, AUDIT, {})
        candidate = audit_run.CALCULATOR.parse_canonical_candidate(
            findings, metadata, catalog, output_directory / "findings.json", "test"
        )
        self.assertEqual(candidate.run_identifier, findings["runIdentifier"])
        self.assertIn("snapshot", findings)
        markdown = (output_directory / "findings.md").read_text(encoding="utf-8")
        self.assertIn("## Act on", markdown)
        self.assertIn("Data fetching in one place", markdown)
        self.assertIn("Why it matters: Scattered fetching", markdown)
        self.assertIn("   - Note: the only data call", markdown)
        self.assertIn("Reported: `npx madge --circular src` → no cycles", markdown)
        self.assertIn("## Snapshot", markdown)

    def test_failed_publication_restores_the_previous_run(self) -> None:
        self.run_command("begin", AUDIT)
        self.run_command("audit-not-applicable", AUDIT, "--reason", "First run.")
        self.assert_accepted(self.run_command("finish", AUDIT))
        output_directory = self.root / ".architect-audits" / AUDIT
        names = ("findings.json", "metadata.json", "findings.md")
        before = {name: (output_directory / name).read_text(encoding="utf-8") for name in names}
        self.run_command("begin", AUDIT)
        self.run_command("audit-not-applicable", AUDIT, "--reason", "Second run.")
        original = audit_run.write_text_atomic

        def failing_write(path: Path, text: str) -> None:
            if path.name == "findings.json" and path.parent == output_directory:
                raise OSError("disk full")
            original(path, text)

        audit_run.write_text_atomic = failing_write
        try:
            self.assert_rejected(self.run_command("finish", AUDIT), "previous run was restored")
        finally:
            audit_run.write_text_atomic = original
        after = {name: (output_directory / name).read_text(encoding="utf-8") for name in names}
        self.assertEqual(after, before)

    def test_audit_level_not_applicable_marks_every_check(self) -> None:
        self.run_command("begin", AUDIT)
        self.assert_accepted(self.run_command("audit-not-applicable", AUDIT, "--reason", "No TypeScript sources."))
        self.assert_accepted(self.run_command("finish", AUDIT))
        findings = json.loads((self.root / ".architect-audits" / AUDIT / "findings.json").read_text(encoding="utf-8"))
        self.assertEqual(findings["auditApplicability"]["status"], "not-applicable")
        self.assertTrue(all(check["applicability"] == "not-applicable" for check in findings["checks"]))

    def test_a_per_check_record_replaces_audit_level_not_applicable(self) -> None:
        self.run_command("begin", AUDIT)
        self.run_command("audit-not-applicable", AUDIT, "--reason", "Looked absent.")
        self.assert_accepted(self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/a.ts:1"))
        self.assertIsNone(self.staged()["auditApplicability"])
        self.assert_accepted(self.run_command("finish", AUDIT))

    def test_not_evaluated_remaining_records_every_pending_check(self) -> None:
        self.run_command("begin", AUDIT)
        self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/a.ts:1")
        self.assert_accepted(self.run_command("not-evaluated", AUDIT, "--remaining", "--reason", "outside the --since scope"))
        checks = self.staged()["checks"]
        self.assertFalse(any(check["recordedBy"] is None for check in checks))
        self.assertEqual(self.check(CYCLES_CHECK)["status"], "present")
        self.assert_rejected(self.run_command("not-evaluated", AUDIT, CYCLES_CHECK, "--remaining", "--reason", "x"), "--remaining")

    def test_degraded_reason_cannot_reopen_a_check(self) -> None:
        self.run_command("begin", AUDIT)
        self.assert_rejected(
            self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/a.ts:1", "--degraded", "pending"),
            "reserved",
        )

    def test_hotspots_ranks_frequently_changed_files(self) -> None:
        for number in range(3):
            self.write("src/a.ts", f"export const a = {number};\n")
            self.commit(f"change {number}")
        code, output = self.run_command("hotspots", "--json")
        self.assertEqual(code, 0, output)
        self.assertEqual(json.loads(output)[0]["path"], "src/a.ts")

    def test_audit_names_cannot_escape_the_output_directory(self) -> None:
        self.assert_rejected(self.run_command("status", "../outside"), "invalid audit name")


class EvidenceTests(AuditRunTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.run_command("begin", AUDIT)

    def test_record_verifies_cited_lines_and_quotes(self) -> None:
        common = ["--status", "violation", "--tier", "direct", "--gap", "Direct fetch.", "--remediation", "Use a query hook."]
        self.assert_accepted(self.record(FETCH_CHECK, "--evidence", "src/a.ts:3 — `fetch('/x')` in a module", *common))
        self.assert_rejected(self.record(FETCH_CHECK, "--evidence", "src/a.ts:3 — `axios.get('/x')`", *common), "does not appear")
        self.assert_rejected(self.record(FETCH_CHECK, "--evidence", "src/a.ts:40", *common), "outside")
        self.assert_rejected(self.record(FETCH_CHECK, "--evidence", "src/missing.ts:1", *common), "does not exist")

    def test_citations_accept_extensionless_files(self) -> None:
        self.write("Makefile", "build:\n\ttsc --noEmit\n")
        self.assert_accepted(self.record(CYCLES_CHECK, "--status", "present", "--evidence", "Makefile:2 — `tsc --noEmit`"))
        self.assert_rejected(self.record(CYCLES_CHECK, "--status", "present", "--evidence", "Makefile:2 — `eslint .`"), "does not appear")

    def test_every_separator_after_a_reference_has_its_quotes_checked(self) -> None:
        for separator in (" — ", " – ", " -- ", " - ", ": ", " "):
            self.assert_rejected(
                self.record(CYCLES_CHECK, "--status", "present", "--evidence", f"src/a.ts:3{separator}`axios.get(url)`"),
                "does not appear",
            )
            self.assert_accepted(
                self.record(CYCLES_CHECK, "--status", "present", "--evidence", f"src/a.ts:3{separator}`fetch('/x')`")
            )
        self.assert_accepted(self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/a.ts:3:10 — `fetch`"))

    def test_unrecognised_forms_are_rejected_rather_than_kept_as_observations(self) -> None:
        for entry in ("src/a.ts:3, `fetch`", "src/a.ts:3x — note", "`src/a.ts:3"):
            self.assert_rejected(self.record(CYCLES_CHECK, "--status", "present", "--evidence", entry), "unrecognised evidence")

    def test_paths_with_spaces_are_quoted(self) -> None:
        self.write("docs/My Notes.md", "# Notes\nNo cycles here.\n")
        self.assert_accepted(
            self.record(CYCLES_CHECK, "--status", "present", "--evidence", "`docs/My Notes.md`:2 — `No cycles`")
        )

    def test_folders_and_missing_paths_are_not_evidence(self) -> None:
        for entry in (".", "src", "src — the source folder"):
            self.assert_rejected(self.record(CYCLES_CHECK, "--status", "present", "--evidence", entry), "is a folder")
        self.assert_rejected(
            self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/features/legacy/importer.ts"),
            "cited file does not exist",
        )
        self.assert_rejected(
            self.record(CYCLES_CHECK, "--status", "present", "--evidence", "Next.js — app router detected"),
            "note:",
        )

    def test_commands_and_notes_never_stand_alone(self) -> None:
        for entry in ("command: `rg -n axios src` → 14 matches", "note: looks fine to me"):
            self.assert_rejected(self.record(CYCLES_CHECK, "--status", "present", "--evidence", entry), "verify")
        self.assert_accepted(
            self.record(
                CYCLES_CHECK, "--status", "present",
                "--evidence", "command: `npx madge --circular src` → no cycles",
                "--evidence", "src/a.ts:1",
            )
        )

    def test_non_present_results_need_an_explicit_tier(self) -> None:
        arguments = ["--status", "violation", "--evidence", "src/a.ts:3", "--gap", "g", "--remediation", "r"]
        self.assert_rejected(self.record(FETCH_CHECK, *arguments), "--tier")
        self.assert_rejected(self.record(FETCH_CHECK, *arguments, "--tier", "inferred"), "direct or supported")
        self.assert_accepted(self.record(FETCH_CHECK, *arguments, "--tier", "supported"))
        self.assert_rejected(self.record(FETCH_CHECK, "--status", "violation", "--tier", "direct", "--evidence", "src/a.ts:3"), "--gap")

    def test_dismissed_findings_need_a_reason(self) -> None:
        arguments = [
            "--status", "violation", "--tier", "direct", "--evidence", "src/a.ts:3",
            "--gap", "g", "--remediation", "r", "--judgement", "dismissed",
        ]
        self.assert_rejected(self.record(FETCH_CHECK, *arguments), "--reason")
        self.assert_accepted(self.record(FETCH_CHECK, *arguments, "--reason", "Only a prototype route."))

    def test_lines_end_at_newlines_only(self) -> None:
        self.write("src/form.ts", "one\ntwo\fstill two\nthree\r\n")
        self.assert_accepted(self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/form.ts:3 — `three`"))
        self.assert_rejected(self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/form.ts:4"), "outside")

    def test_secret_like_evidence_is_rejected(self) -> None:
        secrets = (
            fake_secret("AKIA", "ABCDEFGHIJKLMNOP"),
            fake_secret("sk_", "live_", "0123456789abcdefABCDEF01"),
            fake_secret("AIza", "Sy", "A" * 33),
            fake_secret("glpat", "-", "abcdefghij0123456789"),
            fake_secret("npm_", "a1" * 18),
            fake_secret('apiKey = "', "Zx9", "q" * 20, '"'),
        )
        for secret in secrets:
            self.assert_rejected(
                self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/a.ts:1", "--evidence", f"note: {secret}"),
                "secret",
            )

    def test_redacted_quotes_match_the_real_secret(self) -> None:
        self.write("src/keys.ts", "const stripeKey = \"" + fake_secret("sk_", "live_", "0123456789abcdefABCDEF01") + "\";\n")
        self.assert_accepted(
            self.record(CYCLES_CHECK, "--status", "present", "--evidence", 'src/keys.ts:1 — `stripeKey = "<REDACTED>"`')
        )
        self.assert_rejected(
            self.record(CYCLES_CHECK, "--status", "present", "--evidence", 'src/keys.ts:1 — `apiKey = "<REDACTED>"`'),
            "does not appear",
        )
        self.assert_rejected(
            self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/keys.ts:1 — `<REDACTED>`"),
            "some text around",
        )


class SearchTests(AuditRunTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.write("src/deep/b.ts", "export const b = () => fetch('/y');\n")
        self.write("index.ts", "export const root = () => fetch('/z');\n")
        self.write("app/[slug]/page.tsx", "export default function Page() {\n  return fetch('/p');\n}\n")
        self.write(".gitignore", "dist/\n.next/\n")
        self.write("dist/bundle.js", "fetch('/bundle'); dangerouslySetInnerHTML\n")
        self.commit("fixtures")
        self.run_command("begin", AUDIT)

    def search(self, pattern: str, scope: str, count: int) -> tuple[int, str]:
        return self.record(
            IMPORTS_CHECK, "--status", "present", "--evidence", f"search: `{pattern}` in {scope} → {count} matches"
        )

    def test_scopes_follow_git_glob_rules(self) -> None:
        for scope, count in (
            ("src/*.ts", 1),
            ("src/**/*.ts", 2),
            ("**/*.ts", 3),
            ("app/[slug]/page.tsx", 1),
            ("app/[slug]", 1),
            (".", 4),
            ("`src`", 2),
        ):
            self.assert_accepted(self.search("fetch(", scope, count))

    def test_search_counts_are_re_run(self) -> None:
        self.assert_rejected(self.search("../../../", "src", 3), "the repository has 0")
        self.assert_accepted(self.search("re:fetch\\('/[xy]'\\)", "src", 2))
        self.assert_rejected(self.search("re:[[:space:]]fetch", "src", 2), "POSIX")

    def test_ignored_files_are_not_searched(self) -> None:
        self.assert_accepted(self.search("dangerouslySetInnerHTML", ".", 0))
        self.assert_rejected(self.search("fetch(", "dist", 0), "no searchable files")
        self.assert_rejected(self.search("fetch(", "missing", 0), "does not exist")

    def test_large_ignored_folders_do_not_count_against_the_limit(self) -> None:
        for number in range(30):
            self.write(f".next/cache/{number}.js", "fetch('/cached');\n")
        original = audit_run.MAX_SEARCH_FILES
        audit_run.MAX_SEARCH_FILES = 10
        try:
            self.assert_accepted(self.search("fetch(", "src/**/*.ts", 2))
        finally:
            audit_run.MAX_SEARCH_FILES = original

    def test_file_counts_show_absence_and_presence(self) -> None:
        self.assert_accepted(
            self.record(CYCLES_CHECK, "--status", "missing", "--tier", "direct", "--gap", "g", "--remediation", "r",
                        "--evidence", "files: `.github/workflows/*.yml` → 0 files")
        )
        self.assert_rejected(
            self.record(CYCLES_CHECK, "--status", "present", "--evidence", "files: `src/**/*.ts` → 3 files"),
            "the repository has 2",
        )

    def test_glob_translation_matches_git(self) -> None:
        self.write("src/deep/deeper/c.ts", "export const c = 3;\n")
        self.write("lib/x.js", "export const x = 1;\n")
        self.commit("more fixtures")
        audit_run.clear_caches()
        files = audit_run.repository_files(self.root)
        for pattern in (
            "src/*.ts", "src/**/*.ts", "**/*.ts", "**", "src/**", "*.ts", "src/*/b.ts", "**/deep/**",
            "src/deep/**/*.ts", "?ndex.ts", "src/[!a]*.ts", "lib/*", "app/*/page.tsx", "app/[s]*/*.tsx",
        ):
            listed = git(self.root, "ls-files", "--", f":(glob){pattern}").splitlines()
            expected = sorted(path for path in listed if path in files)
            actual = sorted(path for path in files if audit_run.Scope(self.root, pattern).matches(path))
            self.assertEqual(actual, expected, pattern)


class JudgementTests(AuditRunTestCase):
    GRADED = (FETCH_CHECK, CYCLES_CHECK, IMPORTS_CHECK, f"{AUDIT}.extra-1", f"{AUDIT}.extra-2", f"{AUDIT}.extra-3")

    def judge(self, check: str, *arguments: str, audit: str = AUDIT) -> tuple[int, str]:
        return self.run_command("judge", audit, check, *arguments)

    def record_partial(self, check: str) -> tuple[int, str]:
        return self.record(
            check, "--status", "partial", "--tier", "direct", "--evidence", "src/a.ts:3 — `fetch('/x')`",
            "--gap", "A gap.", "--remediation", "A fix.",
        )

    def test_judge_changes_a_collector_finding_without_re_recording_it(self) -> None:
        self.assert_accepted(self.run_command("begin", COLLECTOR_AUDIT))
        lint = f"{COLLECTOR_AUDIT}.lint-check"
        before = self.check(lint, COLLECTOR_AUDIT)
        self.assertEqual((before["judgement"], before["recordedBy"]), ("act-on", "collector"))
        self.assert_accepted(self.judge(lint, "--judgement", "consider", "--reason", "Low value here.", audit=COLLECTOR_AUDIT))
        after = self.check(lint, COLLECTOR_AUDIT)
        self.assertEqual((after["judgement"], after["judgementReason"]), ("consider", "Low value here."))
        unchanged = {key: value for key, value in after.items() if key not in {"judgement", "judgementReason"}}
        self.assertEqual(unchanged, {key: value for key, value in before.items() if key not in {"judgement", "judgementReason"}})

    def test_judge_changes_a_model_finding(self) -> None:
        self.run_command("begin", AUDIT)
        self.assert_accepted(self.record_partial(FETCH_CHECK))
        self.assert_accepted(self.judge(FETCH_CHECK, "--judgement", "consider"))
        check = self.check(FETCH_CHECK)
        self.assertEqual((check["judgement"], check["judgementReason"], check["recordedBy"]), ("consider", None, "model"))

    def test_judge_refuses_checks_without_a_graded_result(self) -> None:
        self.run_command("begin", AUDIT)
        self.assert_rejected(self.judge(FETCH_CHECK, "--judgement", "consider"), "still pending")
        self.assert_accepted(self.record(CYCLES_CHECK, "--status", "present", "--evidence", "src/a.ts:1"))
        self.assert_rejected(self.judge(CYCLES_CHECK, "--judgement", "consider"), "is present")
        self.run_command("not-applicable", AUDIT, IMPORTS_CHECK, "--reason", "No imports.")
        self.assert_rejected(self.judge(IMPORTS_CHECK, "--judgement", "consider"), "not applicable")
        self.run_command("not-evaluated", AUDIT, LANGUAGE_CHECK, "--reason", "No time.")
        self.assert_rejected(self.judge(LANGUAGE_CHECK, "--judgement", "consider"), "not evaluated")

    def test_judge_needs_a_reason_for_noted_and_dismissed(self) -> None:
        self.run_command("begin", AUDIT)
        for judgement in ("noted", "dismissed"):
            self.assert_accepted(self.record_partial(FETCH_CHECK))
            self.assert_rejected(self.judge(FETCH_CHECK, "--judgement", judgement), "needs --reason")
            self.assertEqual(self.check(FETCH_CHECK)["judgement"], "act-on")
            self.assert_accepted(self.judge(FETCH_CHECK, "--judgement", judgement, "--reason", "Prototype route."))
            self.assertEqual(self.check(FETCH_CHECK)["judgement"], judgement)

    def test_judge_keeps_a_recorded_decision_noted_unless_given_a_reason(self) -> None:
        self.run_command("decide", FETCH_CHECK, "--decision", "accepted-risk", "--reason", "Legacy.", "--owner", "Owner")
        self.run_command("begin", AUDIT)
        self.assert_accepted(self.record_partial(FETCH_CHECK))
        self.assertEqual(self.check(FETCH_CHECK)["judgement"], "noted")
        self.assert_rejected(self.judge(FETCH_CHECK, "--judgement", "act-on"), "decision-001")
        self.assert_accepted(self.judge(FETCH_CHECK, "--judgement", "act-on", "--reason", "The route is live again."))

    def test_finish_refuses_more_than_five_act_on_findings(self) -> None:
        self.run_command("begin", AUDIT)
        for check in self.GRADED:
            self.assert_accepted(self.record_partial(check))
        self.resolve_remaining()
        code, output = self.run_command("finish", AUDIT)
        self.assertEqual(code, 1, output)
        self.assertIn("6 checks are act-on", output)
        # Report order: high severity first, then catalog order.
        positions = [output.index(f"{check} (") for check in self.GRADED]
        self.assertEqual(positions, sorted(positions))
        self.assertIn(f"judge {AUDIT} <check-id> --judgement consider", output)
        self.assertFalse((self.root / ".architect-audits" / AUDIT / "findings.json").exists())
        self.assert_accepted(self.judge(f"{AUDIT}.extra-3", "--judgement", "consider", "--reason", "Lowest value."))
        code, output = self.run_command("finish", AUDIT)
        self.assertEqual(code, 0, output)
        self.assertIn("5. Extra check 2", output)
        self.assertNotIn("Extra check 3 [", output)
        markdown = (self.root / ".architect-audits" / AUDIT / "findings.md").read_text(encoding="utf-8")
        self.assertIn("## Consider", markdown)


class DecisionTests(AuditRunTestCase):
    def decide(self, *scope: str) -> tuple[int, str]:
        arguments = ["decide", FETCH_CHECK, "--decision", "accepted-risk", "--reason", "Legacy route.", "--owner", "Owner"]
        for item in scope:
            arguments += ["--scope", item]
        return self.run_command(*arguments)

    def record_violation(self, citation: str, *extra: str) -> tuple[int, str]:
        return self.record(
            FETCH_CHECK, "--status", "violation", "--tier", "direct", "--evidence", citation,
            "--gap", "Direct fetch.", "--remediation", "Use a query hook.", *extra,
        )

    def test_recorded_decisions_keep_findings_out_of_act_on(self) -> None:
        self.assert_accepted(self.decide("src/**"))
        self.run_command("begin", AUDIT)
        self.assert_accepted(self.record_violation("src/a.ts:3"))
        check = self.check(FETCH_CHECK)
        self.assertEqual((check["judgement"], check["decisionId"], check["status"]), ("noted", "decision-001", "violation"))
        self.assert_rejected(self.record_violation("src/a.ts:3", "--judgement", "act-on"), "decision-001")
        self.assert_accepted(self.record_violation("src/a.ts:3", "--judgement", "act-on", "--reason", "The route is live again."))

    def test_decision_scopes_cover_folders_and_globs(self) -> None:
        self.write("src/legacy/old.ts", "fetch('/old');\n")
        self.commit("legacy")
        self.assert_accepted(self.decide("src/legacy"))
        self.run_command("begin", AUDIT)
        self.assert_accepted(self.record_violation("src/legacy/old.ts:1"))
        self.assertEqual(self.check(FETCH_CHECK)["judgement"], "noted")
        self.assert_accepted(self.record_violation("src/a.ts:3"))
        self.assertEqual(self.check(FETCH_CHECK)["judgement"], "act-on")

    def test_glob_decision_scopes_include_the_top_folder(self) -> None:
        self.assert_accepted(self.decide("src/**/*.ts"))
        self.run_command("begin", AUDIT)
        self.assert_accepted(self.record_violation("src/a.ts:3"))
        self.assertEqual(self.check(FETCH_CHECK)["judgement"], "noted")

    def test_decision_identifiers_stay_unique(self) -> None:
        self.decide()
        self.decide()
        path = self.root / ".architect-audits" / "decisions.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["decisions"] = [item for item in data["decisions"] if item["id"] != "decision-001"]
        path.write_text(json.dumps(data), encoding="utf-8")
        self.assert_accepted(self.decide())
        identifiers = [item["id"] for item in json.loads(path.read_text(encoding="utf-8"))["decisions"]]
        self.assertEqual(identifiers, ["decision-002", "decision-003"])

    def test_malformed_decision_files_fail_cleanly(self) -> None:
        path = self.root / ".architect-audits" / "decisions.json"
        path.parent.mkdir(parents=True)
        for content in ("[]", '{"decisions": [1]}', '{"decisions": [{"id": "decision-001"}]}'):
            path.write_text(content, encoding="utf-8")
            code, output = self.decide()
            self.assertEqual(code, 1, output)
            self.assertIn("error:", output)
            self.assertNotIn("Traceback", output)


class OptionTests(AuditRunTestCase):
    def test_since_records_a_filtered_run(self) -> None:
        first = git(self.root, "rev-parse", "HEAD")
        self.write("src/b.ts", "export const b = 2;\n")
        self.commit("second")
        self.assert_accepted(self.run_command("begin", AUDIT, "--since", first))
        run = self.staged()
        self.assertEqual(run["scope"]["changedFiles"], ["src/b.ts"])
        self.assertTrue(run["execution"]["filtersApplied"])
        self.assertEqual(run["execution"]["filterArguments"], [f"--since={first}"])

    def test_since_reports_an_empty_diff(self) -> None:
        code, output = self.run_command("begin", AUDIT, "--since", "HEAD")
        self.assertEqual(code, 0, output)
        self.assertIn("No files changed since HEAD", output)

    def test_since_without_a_merge_base_fails(self) -> None:
        branch = git(self.root, "rev-parse", "--abbrev-ref", "HEAD")
        git(self.root, "checkout", "-q", "--orphan", "unrelated")
        self.commit("unrelated history")
        git(self.root, "checkout", "-q", branch)
        self.assert_rejected(self.run_command("begin", AUDIT, "--since", "unrelated"), "no merge base")

    def test_enrichment_flags_may_omit_their_dashes(self) -> None:
        self.assert_accepted(self.run_command("begin", AUDIT, "--enrichment", "with-run", "--enrichment=--with-network"))
        self.assertEqual(self.staged()["execution"]["enrichmentArguments"], ["--with-run", "--with-network"])

    def test_snapshot_values_must_be_finite(self) -> None:
        self.run_command("begin", AUDIT)
        self.assert_rejected(self.run_command("snapshot", AUDIT, "--set", "ratio=NaN"), "not valid JSON")
        self.assert_rejected(self.run_command("snapshot", AUDIT, "--set", "largest=1e400"), "not a finite number")
        file = self.write("snapshot.json", '{"ratio": Infinity}')
        self.assert_rejected(self.run_command("snapshot", AUDIT, "--json", str(file)), "not valid JSON")
        self.assert_accepted(self.run_command("snapshot", AUDIT, "--set", "ratio=0.5", "--set", "framework=Next.js"))
        self.assertEqual(self.staged()["snapshot"], {"ratio": 0.5, "framework": "Next.js"})

    def test_repository_names_never_carry_local_paths_or_credentials(self) -> None:
        git(self.root, "remote", "add", "origin", "placeholder")
        for remote, expected in (
            ("git@github.com:owner/repo.git", "owner/repo"),
            ("https://user:token@github.com/owner/repo.git", "owner/repo"),
            ("https://git.example.com/team/app", "team/app"),
            ("ssh://git@git.example.com:22/repo.git", "repo"),
            ("https://dev.azure.com/org/project/_git/app", "project/app"),
            ("git@ssh.dev.azure.com:v3/org/project/app", "project/app"),
            ("../../upstream.git", "target"),
            ("/home/someone/clients/private-client/app.git", "target"),
            ("file:///srv/git/app.git", "target"),
        ):
            git(self.root, "remote", "set-url", "origin", remote)
            self.assertEqual(audit_run.repository_name(self.root), expected, remote)


class ConcurrencyAndInstallTests(AuditRunTestCase):
    def test_parallel_commands_do_not_lose_updates(self) -> None:
        self.run_command("begin", AUDIT)
        checks = [check["checkId"] for check in FIXTURE_CATALOG["checks"]]
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
        processes = [
            subprocess.Popen(
                [sys.executable, str(SCRIPT), "--repository", str(self.root), "not-evaluated", AUDIT, check, "--reason", f"reason {index}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            for index, check in enumerate(checks)
        ]
        processes.append(
            subprocess.Popen(
                [sys.executable, str(SCRIPT), "--repository", str(self.root), "snapshot", AUDIT, "--set", "framework=none"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
        )
        for process in processes:
            _, error = process.communicate(timeout=300)
            self.assertEqual(process.returncode, 0, error)
        run = self.staged()
        self.assertEqual(sorted(check["checkId"] for check in run["checks"] if check["recordedBy"]), sorted(checks))
        self.assertEqual(run["snapshot"].get("framework"), "none")

    def test_the_run_lock_admits_one_holder_at_a_time(self) -> None:
        directory = self.root / ".architect-audits" / AUDIT
        events: list[str] = []
        first_inside = threading.Event()

        def first() -> None:
            with audit_run.exclusive_lock(directory):
                events.append("first in")
                first_inside.set()
                time.sleep(1.0)
                events.append("first out")

        def second() -> None:
            first_inside.wait(timeout=60)
            with audit_run.exclusive_lock(directory):
                events.append("second in")

        threads = [threading.Thread(target=first), threading.Thread(target=second)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=120)
        self.assertEqual(events, ["first in", "first out", "second in"])

    def test_a_lock_file_serialises_commands_where_flock_is_unsupported(self) -> None:
        if audit_run.fcntl is None:
            self.skipTest("flock is a POSIX fallback path")
        original = audit_run.fcntl.flock

        def unsupported(*arguments: Any) -> None:
            raise OSError(errno.ENOLCK, "No locks available")

        audit_run.fcntl.flock = unsupported
        try:
            self.test_the_run_lock_admits_one_holder_at_a_time()
        finally:
            audit_run.fcntl.flock = original
        self.assertFalse((self.root / ".architect-audits" / AUDIT / ".lock").exists())

    def test_a_lock_file_left_by_an_exited_process_is_replaced(self) -> None:
        directory = self.root / ".architect-audits" / AUDIT
        directory.mkdir(parents=True)
        exited = subprocess.Popen([sys.executable, "-c", "pass"])
        exited.wait()
        (directory / ".lock").write_text(str(exited.pid), encoding="ascii")
        with audit_run.lock_file(directory):
            self.assertEqual((directory / ".lock").read_text(encoding="ascii"), str(os.getpid()))
        self.assertFalse((directory / ".lock").exists())

    def test_running_the_protocol_writes_no_bytecode_beside_the_calculator(self) -> None:
        with tempfile.TemporaryDirectory() as copy:
            skills = Path(copy)
            build_skills_root(skills)
            result = self.run_script("begin", COLLECTOR_AUDIT, script=skills / "audit-protocol" / "scripts" / "audit_run.py")
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            self.assertEqual(sorted(str(path.relative_to(skills)) for path in skills.rglob("__pycache__")), [])

    def test_collector_results_are_verified_and_receive_the_run_options(self) -> None:
        result = self.run_script("begin", COLLECTOR_AUDIT, "--enrichment", "with-run", "--threshold", "months=12")
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn("collector result for collector-audit.bad-check rejected", result.stdout)
        run = self.staged(COLLECTOR_AUDIT)
        tool = self.check(f"{COLLECTOR_AUDIT}.tool-check", COLLECTOR_AUDIT)
        self.assertEqual((tool["status"], tool["recordedBy"], tool["evidenceTier"]), ("present", "collector", "direct"))
        self.assertIsNone(self.check(f"{COLLECTOR_AUDIT}.bad-check", COLLECTOR_AUDIT)["recordedBy"])
        self.assertEqual(run["snapshot"]["enrichment"], ["--with-run"])
        self.assertEqual(run["snapshot"]["threshold"], ["months=12"])
        self.assertEqual(run["execution"]["thresholdOverrides"], {"months": "12"})
        self.assertEqual(run["snapshot"]["dontWriteBytecode"], 1)


if __name__ == "__main__":
    unittest.main()
