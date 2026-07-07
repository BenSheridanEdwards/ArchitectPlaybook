from __future__ import annotations

import importlib.util
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

VALID_CHECKS_JSON = """{
  "schemaVersion": "1.0.0",
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

    def test_markdown_links_ignore_code_fences(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            readme = root / "README.md"
            readme.write_text("```md\n[Missing](missing.md)\n```\n", encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_markdown_links(root, findings)
            self.assertEqual(findings, [])

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
