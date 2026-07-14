from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-playbook.py"

spec = importlib.util.spec_from_file_location("validate_playbook", VALIDATOR_PATH)
assert spec and spec.loader
validate_playbook = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validate_playbook
spec.loader.exec_module(validate_playbook)


VALID_SKILL = """---
name: example-audit
description: Example audit skill.
trigger: /example-audit
---

## Usage

`/example-audit`
`/example-audit --worktree`
`/example-audit --learn`
`/example-audit --teach`

## What this skill does

Reports findings.md, findings.json, snapshot.md, and metadata.json. It supports --worktree as an audit flag.

## Implementation steps

1. Inspect the repository.
2. Write the findings.

## What this skill explicitly does NOT do

It does not mutate the repository.
"""

VALID_SKILL_WITH_LAYER = VALID_SKILL + "\n### Layer 1 - Test runner\n\n"

VALID_RQS_CONTRACT = """
## Repository Quality Score findings contract

`findings.json` uses schema `2.0.0` with `runIdentifier`, `runStartedAt`,
`runFinishedAt`, `checkCatalogVersion`, `applicability`, `evaluationState`, and
`evidenceQuality`. `metadata.json` repeats the shared run identity.
"""

VALID_CHECKS_JSON = """{
  "schemaVersion": "1.1.0",
  "catalogVersion": "1.0.0",
  "skillName": "example-audit",
  "humanCanonicalSource": "SKILL.md",
  "statusTaxonomy": {
    "present": "Fully satisfied.",
    "partial": "Partly satisfied.",
    "missing": "Absent.",
    "violation": "Broken."
  },
  "checks": [
    {
      "checkId": "example-audit.single-test-runner",
      "layer": "test-runner",
      "title": "Single test runner",
      "expectation": "Exactly one test runner is configured.",
      "violationSignal": "Multiple test runners are configured."
    }
  ]
}
"""


class ValidatePlaybookTests(unittest.TestCase):
    def test_parse_frontmatter_reads_expected_keys_and_body(self) -> None:
        frontmatter, keys, body = validate_playbook.parse_frontmatter(VALID_SKILL)
        self.assertEqual(keys[:3], ["name", "description", "trigger"])
        self.assertEqual(frontmatter["name"], "example-audit")
        self.assertIn("## Usage", body)

    def test_valid_minimal_skill_repository_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("[Example](example-audit/SKILL.md)\n", encoding="utf-8")
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_skills(root, findings)
            validate_playbook.validate_no_standalone_worktree(root, findings)
            validate_playbook.validate_readme_index(root, findings)
            validate_playbook.validate_markdown_links(root, findings)
            self.assertEqual(findings, [])

    def test_missing_required_section_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("[Example](example-audit/SKILL.md)\n", encoding="utf-8")
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            broken = VALID_SKILL.replace("## Implementation steps", "## Steps")
            (skill_dir / "SKILL.md").write_text(broken, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_skills(root, findings)
            self.assertTrue(any("missing required section: Implementation steps" in finding.message for finding in findings))

    def test_standalone_worktree_skill_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            worktree = root / "worktree"
            worktree.mkdir()
            (worktree / "SKILL.md").write_text("---\nname: worktree\ndescription: Bad.\ntrigger: /worktree\n---\n", encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_no_standalone_worktree(root, findings)
            self.assertTrue(any("not a standalone slash command" in finding.message for finding in findings))

    def test_audit_usage_must_not_document_internal_target(self) -> None:
        broken = VALID_SKILL.replace("`/example-audit --learn`", "`/example-audit --target=../repo`")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(broken, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_skills(root, findings)
            self.assertTrue(any("must not document internal --target" in finding.message for finding in findings))

    def test_check_metadata_accepts_valid_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(VALID_SKILL_WITH_LAYER, encoding="utf-8")
            (skill_dir / "checks.json").write_text(VALID_CHECKS_JSON, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertEqual(findings, [])

    def test_implemented_audit_requires_check_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(VALID_SKILL_WITH_LAYER, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertTrue(any("must ship checks.json" in finding.message for finding in findings))

    def test_check_metadata_requires_catalog_version_and_boolean_soft_check(self) -> None:
        broken = VALID_CHECKS_JSON.replace('  "catalogVersion": "1.0.0",\n', "")
        broken = broken.replace(
            '      "violationSignal": "Multiple test runners are configured."',
            '      "violationSignal": "Multiple test runners are configured.",\n      "softCheck": "yes"',
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(VALID_SKILL_WITH_LAYER, encoding="utf-8")
            (skill_dir / "checks.json").write_text(broken, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertTrue(any("catalogVersion" in finding.message for finding in findings))
            self.assertTrue(any("softCheck must be a boolean" in finding.message for finding in findings))

    def test_check_metadata_rejects_duplicate_ids_and_unknown_layers(self) -> None:
        broken = VALID_CHECKS_JSON.replace('"test-runner"', '"missing-layer"')
        broken = broken.replace(
            "  ]\n}",
            """,
    {
      "checkId": "example-audit.single-test-runner",
      "layer": "test-runner",
      "title": "Duplicate",
      "expectation": "Unique identifiers are required.",
      "violationSignal": "The identifier repeats."
    }
  ]
}
""",
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(VALID_SKILL_WITH_LAYER, encoding="utf-8")
            (skill_dir / "checks.json").write_text(broken, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertTrue(any("duplicate checkId" in finding.message for finding in findings))
            self.assertTrue(any("unknown layer" in finding.message for finding in findings))

    def test_audit_layer_slugs_reads_stage_headings(self) -> None:
        body = (
            "### Stage 1 — Pre-commit (fast, runs on every commit attempt)\n"
            "### Stage 2 — Pre-push (slower, runs once before pushing)\n"
            "### Layer 1 — Test runner\n"
        )
        slugs = validate_playbook.audit_layer_slugs(body)
        self.assertIn("pre-commit", slugs)
        self.assertIn("pre-push", slugs)
        self.assertIn("test-runner", slugs)

    def test_check_metadata_accepts_stage_based_layer(self) -> None:
        stage_skill = VALID_SKILL + "\n### Stage 1 — Pre-commit (fast)\n\n"
        stage_checks = VALID_CHECKS_JSON.replace('"test-runner"', '"pre-commit"')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(stage_skill, encoding="utf-8")
            (skill_dir / "checks.json").write_text(stage_checks, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertEqual(findings, [])

    def test_score_feature_requires_canonical_audit_contract_markers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            score = root / "repository-quality-score"
            score.mkdir()
            (score / "SKILL.md").write_text("score\n", encoding="utf-8")
            audit = root / "example-audit"
            audit.mkdir()
            (audit / "SKILL.md").write_text(VALID_SKILL_WITH_LAYER, encoding="utf-8")

            findings: list[Any] = []
            validate_playbook.validate_audit_findings_contract(root, findings)

            self.assertTrue(any("must document" in finding.message for finding in findings))

    def test_canonical_audit_contract_rejects_legacy_result_keys_and_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            score = root / "repository-quality-score"
            score.mkdir()
            (score / "SKILL.md").write_text("score\n", encoding="utf-8")
            audit = root / "example-audit"
            audit.mkdir()
            (audit / "SKILL.md").write_text(
                VALID_SKILL_WITH_LAYER
                + VALID_RQS_CONTRACT
                + '\n```json\n{"check": "old", "status": "misconfigured"}\n```\n',
                encoding="utf-8",
            )

            findings: list[Any] = []
            validate_playbook.validate_audit_findings_contract(root, findings)

            messages = [finding.message for finding in findings]
            self.assertTrue(any("full checkId" in message for message in messages))
            self.assertTrue(any("classification" in message for message in messages))

    def test_canonical_findings_examples_must_match_check_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            score = root / "repository-quality-score"
            score.mkdir()
            (score / "SKILL.md").write_text("score\n", encoding="utf-8")
            audit = root / "example-audit"
            audit.mkdir()
            example = {
                "schemaVersion": "2.0.0",
                "skillName": "example-audit",
                "checkCatalogSchemaVersion": "1.1.0",
                "checkCatalogVersion": "9.9.9",
                "checks": [
                    {
                        "checkId": "example-audit.unknown-check",
                        "layer": "wrong-layer",
                    },
                    {
                        "checkId": "example-audit.single-test-runner",
                        "layer": "wrong-layer",
                    },
                    {"checkId": "example-audit.single-test-runner"},
                ]
            }
            (audit / "SKILL.md").write_text(
                VALID_SKILL_WITH_LAYER
                + VALID_RQS_CONTRACT
                + "\n```json\n"
                + json.dumps(example)
                + "\n```\n",
                encoding="utf-8",
            )
            (audit / "checks.json").write_text(
                VALID_CHECKS_JSON, encoding="utf-8"
            )

            findings: list[Any] = []
            validate_playbook.validate_audit_findings_contract(root, findings)

            messages = [finding.message for finding in findings]
            self.assertTrue(any("absent from checks.json" in message for message in messages))
            self.assertTrue(any("must be test-runner" in message for message in messages))
            self.assertTrue(any("layer is missing" in message for message in messages))
            self.assertTrue(
                any("checkCatalogVersion must be 1.0.0" in message for message in messages)
            )

    def test_markdown_links_ignore_code_fences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / "README.md"
            readme.write_text("```md\n[Missing](missing.md)\n```\n", encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_markdown_links(root, findings)
            self.assertEqual(findings, [])

    def test_readme_skill_links_use_posix_paths_on_every_platform(self) -> None:
        links = validate_playbook.readme_skill_links("[Example](example-audit/SKILL.md)\n")
        self.assertEqual(links, {"example-audit/SKILL.md"})

    def test_score_policy_audit_list_must_match_catalogs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audit = root / "example-audit"
            audit.mkdir()
            (audit / "SKILL.md").write_text(VALID_SKILL_WITH_LAYER, encoding="utf-8")
            (audit / "checks.json").write_text(VALID_CHECKS_JSON, encoding="utf-8")
            score = root / "repository-quality-score"
            (score / "scripts").mkdir(parents=True)
            (score / "references").mkdir()
            (score / "evals").mkdir()
            for relative_path in validate_playbook.SCORE_BUNDLE_FILES:
                path = score / relative_path
                if not path.exists():
                    path.write_text("placeholder\n", encoding="utf-8")
            policy = {
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
                "audits": [{"name": "unknown-audit", "weight": "1.0"}],
                "bands": [
                    {"name": "Strong", "minimum": "90.00"},
                    {"name": "High risk", "minimum": "0.00"},
                ],
            }
            (score / "score-policy.json").write_text(json.dumps(policy), encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_score_policy(root, findings)
            self.assertTrue(any("policy audit list is out of sync" in finding.message for finding in findings))

    def test_bootstrap_contract_rejects_materialized_tracked_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                ".claude/skills/install-architect-playbook-globally\n",
                encoding="utf-8",
            )
            installer = root / "install-architect-playbook-globally"
            installer.mkdir()
            (installer / "SKILL.md").write_text(
                "---\n"
                "name: install-architect-playbook-globally\n"
                "description: Installer.\n"
                "trigger: /install-architect-playbook-globally\n"
                "---\n",
                encoding="utf-8",
            )
            entry = root / ".claude" / "skills" / "install-architect-playbook-globally"
            entry.parent.mkdir(parents=True)
            entry.write_text(
                "../../install-architect-playbook-globally\n", encoding="utf-8"
            )
            findings: list[Any] = []

            validate_playbook.validate_bootstrap_contract(root, findings)

            self.assertTrue(
                any("must be a real directory" in finding.message for finding in findings)
            )

    def test_bootstrap_contract_rejects_copy_drift(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                ".claude/skills/install-architect-playbook-globally\n",
                encoding="utf-8",
            )
            installer = root / "install-architect-playbook-globally"
            installer.mkdir()
            source_text = (
                "---\n"
                "name: install-architect-playbook-globally\n"
                "description: Installer.\n"
                "trigger: /install-architect-playbook-globally\n"
                "---\n"
            )
            (installer / "SKILL.md").write_text(source_text, encoding="utf-8")
            bootstrap = (
                root
                / ".claude"
                / "skills"
                / "install-architect-playbook-globally"
            )
            bootstrap.mkdir(parents=True)
            (bootstrap / "SKILL.md").write_text(
                source_text + "drift\n", encoding="utf-8"
            )

            findings: list[Any] = []
            validate_playbook.validate_bootstrap_contract(root, findings)

            self.assertTrue(
                any("must exactly match" in finding.message for finding in findings)
            )

    def test_cli_accepts_current_repository(self) -> None:
        result = subprocess.run(
            [sys.executable, str(VALIDATOR_PATH), str(ROOT)],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
        self.assertIn("playbook validation passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
