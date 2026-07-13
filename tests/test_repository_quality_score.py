from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CALCULATOR = (
    ROOT
    / "repository-quality-score"
    / "scripts"
    / "calculate_repository_quality_score.py"
)


class RepositoryQualityScoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        self.repository = root / "target-repository"
        self.skills = root / "skills"
        self.repository.mkdir()
        self.skills.mkdir()
        self._write_policy()
        self._write_catalog(
            "audit-one",
            [
                ("audit-one.first", "layer-one", False),
                ("audit-one.second", "layer-one", False),
            ],
        )
        self._write_catalog(
            "audit-two",
            [("audit-two.soft", "layer-two", True)],
        )
        self._git("init")
        self._git("config", "user.email", "rqs@example.test")
        self._git("config", "user.name", "RQS Test")
        (self.repository / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self._git("add", "tracked.txt")
        self._git("commit", "-m", "test: initial fixture")
        self.commit = self._git("rev-parse", "HEAD").stdout.strip()

    def _git(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(self.repository), *arguments],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def _write_json(self, path: Path, value: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")

    def _write_policy(self) -> None:
        self._write_json(
            self.skills / "repository-quality-score" / "score-policy.json",
            {
                "schemaVersion": "1.0.0",
                "policyVersion": "1.0.0",
                "scorePrecision": 2,
                "statusPoints": {
                    "present": "1.0",
                    "partial": "0.5",
                    "missing": "0.0",
                    "violation": "0.0",
                },
                "checkWeights": {"standard": "1.0", "soft": "0.5"},
                "audits": [
                    {"name": "audit-one", "weight": "1.0"},
                    {"name": "audit-two", "weight": "1.0"},
                ],
                "bands": [
                    {"name": "Strong", "minimum": "90.00"},
                    {"name": "Sound", "minimum": "75.00"},
                    {"name": "Needs attention", "minimum": "60.00"},
                    {"name": "High risk", "minimum": "0.00"},
                ],
            },
        )

    def _write_catalog(
        self, audit: str, checks: list[tuple[str, str, bool]]
    ) -> None:
        self._write_json(
            self.skills / audit / "checks.json",
            {
                "schemaVersion": "1.1.0",
                "catalogVersion": "1.0.0",
                "skillName": audit,
                "checks": [
                    {
                        "checkId": check_id,
                        "layer": layer,
                        "title": check_id.rsplit(".", 1)[-1].replace("-", " ").title(),
                        "softCheck": soft,
                        "allowedStatuses": [
                            "present",
                            "partial",
                            "missing",
                            "violation",
                        ],
                    }
                    for check_id, layer, soft in checks
                ],
            },
        )

    def _canonical_findings(
        self,
        audit: str,
        statuses: list[str | None],
        *,
        commit: str | None = None,
        filters_applied: bool = False,
        graph_available: bool = True,
        not_applicable: set[int] | None = None,
        not_evaluated: set[int] | None = None,
    ) -> dict[str, Any]:
        catalog = json.loads(
            (self.skills / audit / "checks.json").read_text(encoding="utf-8")
        )
        not_applicable = not_applicable or set()
        not_evaluated = not_evaluated or set()
        checks: list[dict[str, Any]] = []
        for index, (catalog_check, status) in enumerate(
            zip(catalog["checks"], statuses, strict=True)
        ):
            if index in not_applicable:
                checks.append(
                    {
                        "checkId": catalog_check["checkId"],
                        "layer": catalog_check["layer"],
                        "applicability": "not-applicable",
                        "applicabilityReason": "technology-not-detected",
                        "evaluationState": "not-evaluated",
                        "evaluationReason": None,
                        "evidenceQuality": "none",
                        "classification": "technology-not-detected",
                        "status": None,
                        "evidence": [],
                        "gap": None,
                        "remediation": None,
                    }
                )
            elif index in not_evaluated:
                checks.append(
                    {
                        "checkId": catalog_check["checkId"],
                        "layer": catalog_check["layer"],
                        "applicability": "applicable",
                        "applicabilityReason": None,
                        "evaluationState": "not-evaluated",
                        "evaluationReason": "filtered by test fixture",
                        "evidenceQuality": "none",
                        "classification": "filtered",
                        "status": None,
                        "evidence": [],
                        "gap": None,
                        "remediation": None,
                    }
                )
            else:
                checks.append(
                    {
                        "checkId": catalog_check["checkId"],
                        "layer": catalog_check["layer"],
                        "applicability": "applicable",
                        "applicabilityReason": None,
                        "evaluationState": "evaluated",
                        "evaluationReason": None,
                        "evidenceQuality": "complete",
                        "classification": "conformance",
                        "status": status,
                        "evidence": [f"evidence for {catalog_check['checkId']}"],
                        "gap": None,
                        "remediation": None,
                    }
                )
        run_identifier = f"run-{audit}"
        return {
            "schemaVersion": "2.0.0",
            "runIdentifier": run_identifier,
            "skillName": audit,
            "skillVersion": "1.0.0",
            "checkCatalogSchemaVersion": "1.1.0",
            "checkCatalogVersion": "1.0.0",
            "runStartedAt": "2026-07-13T10:00:00Z",
            "runFinishedAt": "2026-07-13T10:01:00Z",
            "target": {
                "repository": "target-repository",
                "gitCommit": commit or self.commit,
                "sourceWorkingTreeClean": True,
            },
            "execution": {
                "filtersApplied": filters_applied,
                "filterArguments": ["--include=test"] if filters_applied else [],
                "thresholdOverrides": {},
                "policyOverrides": {},
                "enrichmentArguments": [],
                "graphAvailable": graph_available,
            },
            "checks": checks,
        }

    def _write_findings(
        self,
        audit: str,
        value: Any,
        *,
        repository: Path | None = None,
        run_directory: str | None = None,
    ) -> None:
        audit_root = (repository or self.repository) / ".architect-audits" / audit
        if run_directory is not None:
            audit_root /= run_directory
        self._write_json(audit_root / "findings.json", value)
        if isinstance(value, dict) and value.get("schemaVersion") == "2.0.0":
            shared_fields = (
                "schemaVersion",
                "runIdentifier",
                "skillName",
                "skillVersion",
                "checkCatalogSchemaVersion",
                "checkCatalogVersion",
                "runStartedAt",
                "runFinishedAt",
                "target",
                "execution",
            )
            self._write_json(
                audit_root / "metadata.json",
                {field: value[field] for field in shared_fields},
            )

    def _run_score(self, *, current_worktree_only: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable,
            str(CALCULATOR),
            "--target",
            str(self.repository),
            "--skills-root",
            str(self.skills),
        ]
        if current_worktree_only:
            command.append("--current-worktree-only")
        return subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )

    def _score_json(self) -> dict[str, Any]:
        return json.loads(
            (
                self.repository
                / ".architect-audits"
                / "repository-quality-score"
                / "score.json"
            ).read_text(encoding="utf-8")
        )

    def test_complete_canonical_evidence_produces_official_weighted_score(self) -> None:
        self._write_findings(
            "audit-one", self._canonical_findings("audit-one", ["present", "missing"])
        )
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["present"])
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "official")
        self.assertEqual(result["overallScore"], 75)
        self.assertEqual(result["qualityBand"], "Sound")
        self.assertEqual(result["coverage"]["evaluationPercent"], 100)
        self.assertEqual(result["categories"][0]["score"], 50)
        self.assertEqual(result["categories"][1]["score"], 100)
        self.assertEqual(result["highestImpactDeductions"][0]["overallImpact"], 25)
        self.assertEqual(
            result["highestImpactDeductions"][0]["checkId"], "audit-one.second"
        )
        self.assertNotIn(str(self.repository), json.dumps(result))
        metadata = json.loads(
            (
                self.repository
                / ".architect-audits"
                / "repository-quality-score"
                / "metadata.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(metadata["runIdentifier"], result["runIdentifier"])
        self.assertEqual(metadata["runFinishedAt"], result["runFinishedAt"])

    def test_filtered_or_degraded_canonical_evidence_is_provisional(self) -> None:
        self._write_findings(
            "audit-one",
            self._canonical_findings(
                "audit-one", ["present", None], filters_applied=True, not_evaluated={1}
            ),
        )
        self._write_findings(
            "audit-two",
            self._canonical_findings("audit-two", ["present"], graph_available=False),
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "provisional")
        reason_codes = {reason["code"] for reason in result["statusReasons"]}
        self.assertIn("filtered-run", reason_codes)
        self.assertIn("checks-not-evaluated", reason_codes)
        self.assertIn("graph-unavailable", reason_codes)
        self.assertLess(float(result["coverage"]["evaluationPercent"]), 100)
        self.assertEqual(result["categories"][0]["score"], 100)

    def test_legacy_exact_identifier_mapping_is_scored_but_provisional(self) -> None:
        self._write_findings(
            "audit-one",
            {
                "gitCommit": self.commit,
                "timestamp": "2026-07-13T09:00:00Z",
                "checks": [
                    {"check": "first", "status": "present", "evidence": ["one"]},
                    {
                        "check": "second",
                        "status": "misconfigured",
                        "evidence": ["two"],
                    },
                ],
            },
        )
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["present"])
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "provisional")
        self.assertEqual(result["overallScore"], 87.5)
        self.assertIn(
            "legacy-findings-schema",
            {reason["code"] for reason in result["statusReasons"]},
        )
        self.assertEqual(result["categories"][0]["counts"]["partial"], 1)

    def test_legacy_ambiguous_suffix_invalidates_the_candidate(self) -> None:
        self._write_catalog(
            "audit-one",
            [
                ("audit-one.first", "layer-one", False),
                ("audit-one.group.first", "layer-one", False),
            ],
        )
        self._write_findings(
            "audit-one",
            {
                "gitCommit": self.commit,
                "checks": [{"check": "first", "status": "present"}],
            },
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = self._score_json()
        self.assertIn(
            "not an exact unique match",
            result["excludedCandidates"][0]["reason"],
        )

    def test_canonical_state_and_completeness_violations_are_rejected(self) -> None:
        def missing_classification(value: dict[str, Any]) -> None:
            del value["checks"][0]["classification"]

        def extra_check(value: dict[str, Any]) -> None:
            value["checks"].append(dict(value["checks"][0]))

        def invalid_status(value: dict[str, Any]) -> None:
            value["checks"][0]["status"] = "misconfigured"

        def non_applicable_without_reason(value: dict[str, Any]) -> None:
            check = value["checks"][0]
            check.update(
                {
                    "applicability": "not-applicable",
                    "applicabilityReason": None,
                    "evaluationState": "not-evaluated",
                    "evaluationReason": None,
                    "evidenceQuality": "none",
                    "status": None,
                    "evidence": [],
                }
            )

        def degraded_without_reason(value: dict[str, Any]) -> None:
            value["checks"][0]["evidenceQuality"] = "degraded"

        mutations = {
            "missing-field": missing_classification,
            "extra-check": extra_check,
            "invalid-status": invalid_status,
            "not-applicable-without-reason": non_applicable_without_reason,
            "degraded-without-reason": degraded_without_reason,
        }
        for name, mutate in mutations.items():
            with self.subTest(name=name):
                findings = self._canonical_findings(
                    "audit-one", ["present", "present"]
                )
                mutate(findings)
                self._write_findings("audit-one", findings)

                completed = self._run_score()

                self.assertEqual(completed.returncode, 2, completed.stderr)
                result = self._score_json()
                self.assertEqual(result["status"], "unavailable")
                self.assertTrue(result["excludedCandidates"])

    def test_no_findings_writes_unavailable_report_and_returns_two(self) -> None:
        completed = self._run_score()

        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "unavailable")
        self.assertIsNone(result["overallScore"])
        self.assertEqual(result["missingAudits"], ["audit-one", "audit-two"])

    def test_missing_catalog_reduces_catalog_coverage_without_internal_error(self) -> None:
        (self.skills / "audit-two" / "checks.json").unlink()
        self._write_findings(
            "audit-one", self._canonical_findings("audit-one", ["present", "present"])
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "provisional")
        self.assertEqual(result["overallScore"], 100)
        self.assertEqual(result["coverage"]["catalogsLoaded"], 1)
        self.assertEqual(result["coverage"]["catalogsExpected"], 2)
        self.assertIn(
            "catalog-unavailable",
            {reason["code"] for reason in result["statusReasons"]},
        )

    def test_stale_commit_is_excluded_instead_of_merged(self) -> None:
        self._write_findings(
            "audit-one",
            self._canonical_findings("audit-one", ["present", "present"], commit="a" * 40),
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["excludedCandidates"][0]["reason"], "commit-mismatch")

    def test_duplicate_json_keys_are_rejected_without_crashing_other_inputs(self) -> None:
        path = self.repository / ".architect-audits" / "audit-one" / "findings.json"
        path.parent.mkdir(parents=True)
        path.write_text(
            '{"schemaVersion":"legacy","gitCommit":"%s","checks":[],"checks":[]}\n'
            % self.commit,
            encoding="utf-8",
        )
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["present"])
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "provisional")
        self.assertEqual(result["overallScore"], 100)
        self.assertIn("duplicate JSON key", result["excludedCandidates"][0]["reason"])

    def test_nonfinite_and_invalid_utf8_json_are_safely_excluded(self) -> None:
        path = self.repository / ".architect-audits" / "audit-one" / "findings.json"
        path.parent.mkdir(parents=True)
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["present"])
        )
        path.write_text(
            '{"gitCommit":"%s","checks":[],"value":NaN}\n' % self.commit,
            encoding="utf-8",
        )

        nonfinite = self._run_score()

        self.assertEqual(nonfinite.returncode, 0, nonfinite.stderr)
        self.assertIn(
            "non-finite JSON number",
            self._score_json()["excludedCandidates"][0]["reason"],
        )
        path.write_bytes(b"\xff\xfe\x00")

        invalid_utf8 = self._run_score()

        self.assertEqual(invalid_utf8.returncode, 0, invalid_utf8.stderr)
        self.assertIn(
            "valid UTF-8 JSON",
            self._score_json()["excludedCandidates"][0]["reason"],
        )

    def test_mismatched_metadata_is_rejected_instead_of_qualified(self) -> None:
        self._write_findings(
            "audit-one", self._canonical_findings("audit-one", ["present", "present"])
        )
        metadata_path = (
            self.repository / ".architect-audits" / "audit-one" / "metadata.json"
        )
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        metadata["runIdentifier"] = "different-run"
        self._write_json(metadata_path, metadata)

        completed = self._run_score()

        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "unavailable")
        self.assertIn(
            "disagree on runIdentifier",
            result["excludedCandidates"][0]["reason"],
        )

    def test_invalid_or_reverse_timestamp_is_rejected(self) -> None:
        findings = self._canonical_findings("audit-one", ["present", "present"])
        findings["runFinishedAt"] = "2026-07-13T09:59:00Z"
        self._write_findings("audit-one", findings)

        completed = self._run_score()

        self.assertEqual(completed.returncode, 2, completed.stderr)
        result = self._score_json()
        self.assertIn(
            "runFinishedAt cannot precede runStartedAt",
            result["excludedCandidates"][0]["reason"],
        )

    def test_not_applicable_checks_are_excluded_from_denominator(self) -> None:
        self._write_findings(
            "audit-one",
            self._canonical_findings(
                "audit-one", ["present", None], not_applicable={1}
            ),
        )
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["present"])
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "official")
        self.assertEqual(result["overallScore"], 100)
        self.assertEqual(result["categories"][0]["counts"]["notApplicable"], 1)

    def test_zero_score_is_official_and_not_rendered_as_unavailable(self) -> None:
        self._write_findings(
            "audit-one",
            self._canonical_findings("audit-one", ["missing", "violation"]),
        )
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["violation"])
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "official")
        self.assertEqual(result["overallScore"], 0)
        self.assertEqual(result["qualityBand"], "High risk")
        report = (
            self.repository
            / ".architect-audits"
            / "repository-quality-score"
            / "score.md"
        ).read_text(encoding="utf-8")
        self.assertIn("**0.00 / 100**", report)
        self.assertNotIn("RQS official: unavailable", completed.stdout)

    def test_exact_band_boundary_is_assigned_after_decimal_rounding(self) -> None:
        ten_checks = [
            (f"audit-one.check-{index}", "layer-one", False)
            for index in range(10)
        ]
        self._write_catalog("audit-one", ten_checks)
        self._write_findings(
            "audit-one",
            self._canonical_findings(
                "audit-one", [*["present"] * 9, "missing"]
            ),
        )
        self._write_findings(
            "audit-two",
            self._canonical_findings("audit-two", [None], not_applicable={0}),
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "official")
        self.assertEqual(result["overallScore"], 90)
        self.assertEqual(result["qualityBand"], "Strong")

    def test_soft_checks_use_half_weight_within_their_category(self) -> None:
        self._write_catalog(
            "audit-one",
            [
                ("audit-one.standard", "layer-one", False),
                ("audit-one.soft", "layer-one", True),
            ],
        )
        self._write_findings(
            "audit-one",
            self._canonical_findings("audit-one", ["present", "missing"]),
        )
        self._write_findings(
            "audit-two",
            self._canonical_findings("audit-two", [None], not_applicable={0}),
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        self.assertEqual(result["overallScore"], 66.67)
        self.assertEqual(result["categories"][0]["possibleWeight"], 1.5)

    def test_registered_worktree_findings_are_discovered_only_in_default_mode(self) -> None:
        worktree = Path(self.temporary.name) / "registered worktree"
        self._git("worktree", "add", "-b", "rqs-worktree-test", str(worktree))
        self._write_findings(
            "audit-one", self._canonical_findings("audit-one", ["present", "present"])
        )
        self._write_findings(
            "audit-two",
            self._canonical_findings("audit-two", ["present"]),
            repository=worktree,
        )

        default_mode = self._run_score(current_worktree_only=False)

        self.assertEqual(default_mode.returncode, 0, default_mode.stderr)
        result = self._score_json()
        self.assertEqual(result["status"], "official")
        self.assertEqual(result["coverage"]["auditsSelected"], 2)
        self.assertTrue(
            any(label.startswith("worktree:") for label in result["discovery"]["worktreesInspected"])
        )
        current_only = self._run_score(current_worktree_only=True)
        self.assertEqual(current_only.returncode, 0, current_only.stderr)
        self.assertEqual(self._score_json()["status"], "provisional")

    def test_nested_run_reports_its_exact_relative_source_pointer(self) -> None:
        self._write_findings(
            "audit-one",
            self._canonical_findings("audit-one", ["present", "missing"]),
            run_directory="runs/2026-07-13",
        )

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = self._score_json()
        expected = (
            "current/.architect-audits/audit-one/runs/2026-07-13/findings.json"
        )
        self.assertEqual(result["inputFindings"][0]["source"], expected)
        self.assertEqual(result["categories"][0]["selectedRun"]["source"], expected)
        self.assertEqual(
            result["highestImpactDeductions"][0]["sourceReport"], expected
        )

    def test_invalid_policy_preserves_previous_complete_score(self) -> None:
        self._write_findings(
            "audit-one", self._canonical_findings("audit-one", ["present", "present"])
        )
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["present"])
        )
        first = self._run_score()
        self.assertEqual(first.returncode, 0, first.stderr)
        score_path = (
            self.repository
            / ".architect-audits"
            / "repository-quality-score"
            / "score.json"
        )
        previous = score_path.read_bytes()
        (self.skills / "repository-quality-score" / "score-policy.json").write_text(
            "{ invalid policy\n", encoding="utf-8"
        )

        failed = self._run_score()

        self.assertEqual(failed.returncode, 1)
        self.assertEqual(score_path.read_bytes(), previous)

    def test_repeated_logical_input_has_stable_values_and_ordering(self) -> None:
        self._write_findings(
            "audit-one", self._canonical_findings("audit-one", ["partial", "missing"])
        )
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["present"])
        )
        first_run = self._run_score()
        self.assertEqual(first_run.returncode, 0, first_run.stderr)
        first = self._score_json()
        second_run = self._run_score()
        self.assertEqual(second_run.returncode, 0, second_run.stderr)
        second = self._score_json()
        for result in (first, second):
            result.pop("runIdentifier")
            result.pop("runStartedAt")
            result.pop("runFinishedAt")

        self.assertEqual(first, second)

    def test_output_symlink_escape_is_refused(self) -> None:
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        audit_root = self.repository / ".architect-audits"
        try:
            audit_root.symlink_to(outside, target_is_directory=True)
        except OSError as exc:
            self.skipTest(f"directory symlinks are unavailable: {exc}")

        completed = self._run_score()

        self.assertEqual(completed.returncode, 1)
        self.assertIn("resolves outside", completed.stderr)
        self.assertFalse((outside / "repository-quality-score" / "score.json").exists())

    def test_dirty_source_and_existing_lock_prevent_false_official_output(self) -> None:
        self._write_findings(
            "audit-one", self._canonical_findings("audit-one", ["present", "present"])
        )
        self._write_findings(
            "audit-two", self._canonical_findings("audit-two", ["present"])
        )
        (self.repository / "untracked-source.ts").write_text("dirty\n", encoding="utf-8")

        completed = self._run_score()

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self._score_json()["status"], "provisional")
        lock = (
            self.repository
            / ".architect-audits"
            / "repository-quality-score"
            / ".lock"
        )
        lock.write_text("held\n", encoding="utf-8")

        locked = self._run_score()

        self.assertEqual(locked.returncode, 1)
        self.assertIn("another score calculation", locked.stderr)


if __name__ == "__main__":
    unittest.main()
