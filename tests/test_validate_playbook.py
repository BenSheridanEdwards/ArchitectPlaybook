from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-playbook.py"

# Git exports repository variables such as GIT_INDEX_FILE to hooks and
# aliases. These tests create their own temporary repositories, so an
# inherited variable must never point them at the repository under test.
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

spec = importlib.util.spec_from_file_location("validate_playbook", VALIDATOR_PATH)
assert spec and spec.loader
validate_playbook = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validate_playbook
spec.loader.exec_module(validate_playbook)


PUBLISHING_SECTION = """
## Publishing results

```bash
python3 "${{CLAUDE_SKILL_DIR}}/../audit-protocol/scripts/audit_run.py" begin {audit}
```

Follow [the run protocol](../audit-protocol/references/run-protocol.md).
"""

VALID_SKILL = """---
name: example-audit
description: Example audit skill.
disable-model-invocation: true
argument-hint: "[--worktree] [--learn]"
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
        self.assertEqual(keys[:2], ["name", "description"])
        self.assertEqual(frontmatter["argument-hint"], "[--worktree] [--learn]")
        self.assertEqual(frontmatter["name"], "example-audit")
        self.assertIn("## Usage", body)

    def test_frontmatter_rejects_trigger_and_model_invocation(self) -> None:
        cases = {
            "trigger": (
                VALID_SKILL.replace("disable-model-invocation: true\n", "disable-model-invocation: true\ntrigger: /example-audit\n"),
                "must not use trigger",
            ),
            "model invocation": (
                VALID_SKILL.replace("disable-model-invocation: true\n", ""),
                "must set disable-model-invocation: true",
            ),
            "invalid boolean": (
                VALID_SKILL.replace("disable-model-invocation: true", "disable-model-invocation: yes"),
                "disable-model-invocation must be true or false",
            ),
            "unquoted argument hint": (
                VALID_SKILL.replace('argument-hint: "[--worktree] [--learn]"', "argument-hint: [--worktree] [--learn]"),
                "argument-hint must be quoted",
            ),
            "quoted boolean": (
                VALID_SKILL.replace("disable-model-invocation: true", 'disable-model-invocation: "true"'),
                "must be unquoted true or false",
            ),
            "argument hint": (
                VALID_SKILL.replace('argument-hint: "[--worktree] [--learn]"\n', ""),
                "must give an argument-hint",
            ),
        }
        for name, (text, message) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                skill_dir = Path(tmp) / "example-audit"
                skill_dir.mkdir()
                (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
                findings: list[Any] = []
                validate_playbook.validate_skills(Path(tmp), findings)
                self.assertTrue(any(message in finding.message for finding in findings), [f.message for f in findings])

    def test_skills_that_users_cannot_invoke_need_no_argument_hint(self) -> None:
        text = VALID_SKILL.replace('argument-hint: "[--worktree] [--learn]"\n', "user-invocable: false\n")
        with tempfile.TemporaryDirectory() as tmp:
            skill_dir = Path(tmp) / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_skills(Path(tmp), findings)
            self.assertFalse(any("argument-hint" in finding.message for finding in findings))

    def write_plugin_repository(self, root: Path, skills: list[str]) -> None:
        for name in ("example-audit", "install-architect-playbook-globally"):
            (root / name).mkdir()
            (root / name / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
        (root / ".claude-plugin").mkdir()
        (root / ".claude-plugin" / "plugin.json").write_text(
            json.dumps({"name": "architect-playbook", "skills": skills}), encoding="utf-8"
        )
        (root / ".claude-plugin" / "marketplace.json").write_text(
            json.dumps({"name": "architect-playbook", "owner": {"name": "Owner"}, "plugins": [{"name": "architect-playbook", "source": "./"}]}),
            encoding="utf-8",
        )

    def test_plugin_manifest_lists_every_skill_but_the_installers(self) -> None:
        cases = {
            "complete": (["./example-audit"], []),
            "missing": ([], ["plugin manifest skills is missing ./example-audit"]),
            "unknown": (["./example-audit", "./ghost"], ["plugin manifest skills lists ./ghost, which is not a playbook skill"]),
            "no prefix": (["example-audit"], ["plugin manifest skill path must be ./<skill-folder>: 'example-audit'"]),
            "duplicate": (["./example-audit", "./example-audit"], ["plugin manifest skills lists ./example-audit more than once"]),
            "installer": (
                ["./example-audit", "./install-architect-playbook-globally"],
                ["plugin manifest skills lists ./install-architect-playbook-globally, which is an installer, which a plugin install replaces"],
            ),
        }
        for name, (skills, expected) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.write_plugin_repository(root, skills)
                findings: list[Any] = []
                validate_playbook.validate_plugin_manifest(root, findings)
                self.assertEqual([finding.message for finding in findings], expected)

    def test_plugin_root_must_not_start_servers_or_hooks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_plugin_repository(root, ["./example-audit"])
            (root / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
            (root / "monitors").mkdir()
            (root / "monitors" / "monitors.json").write_text("[]", encoding="utf-8")
            manifest = root / ".claude-plugin" / "plugin.json"
            data = json.loads(manifest.read_text(encoding="utf-8"))
            data["experimental"] = {"monitors": [{"name": "poll", "command": "echo started", "description": "poll"}]}
            manifest.write_text(json.dumps(data), encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_plugin_manifest(root, findings)
            messages = [finding.message for finding in findings]
            self.assertEqual(sum("would load for every plugin user" in message for message in messages), 2)
            self.assertIn("plugin manifest must not declare experimental; the playbook ships only skills and pins no version", messages)

    def test_marketplace_lists_the_plugin_without_pinning_a_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_plugin_repository(root, ["./example-audit"])
            path = root / ".claude-plugin" / "marketplace.json"
            data = json.loads(path.read_text(encoding="utf-8"))
            data["plugins"][0]["version"] = "1.0.0"
            path.write_text(json.dumps(data), encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_plugin_manifest(root, findings)
            self.assertEqual([finding.message for finding in findings], ["marketplace manifest must not pin a version; installs follow commits"])
            path.unlink()
            findings = []
            validate_playbook.validate_plugin_manifest(root, findings)
            self.assertEqual([finding.message for finding in findings], ["marketplace manifest is missing"])

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
            (worktree / "SKILL.md").write_text("---\nname: worktree\ndescription: Bad.\ndisable-model-invocation: true\nargument-hint: \"[--learn]\"\n---\n", encoding="utf-8")
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

    def severity_catalog(self, **overrides: Any) -> str:
        check: dict[str, Any] = {
            "checkId": "example-audit.single-test-runner",
            "layer": "test-runner",
            "title": "Single test runner",
            "expectation": "Exactly one test runner is configured.",
            "violationSignal": "Multiple test runners are configured.",
            "severity": "high",
            "method": "tool",
            "rationale": "Two runners split the suite and double maintenance.",
            "lastVerified": "2026-10-07",
        }
        check.update(overrides)
        catalog = json.loads(VALID_CHECKS_JSON)
        catalog["schemaVersion"] = "1.2.0"
        catalog["checks"] = [{key: value for key, value in check.items() if value is not None}]
        return json.dumps(catalog, indent=2) + "\n"

    def write_severity_audit(self, root: Path, skill_row: str, catalog: str) -> None:
        skill_dir = root / "example-audit"
        skill_dir.mkdir()
        body = (
            VALID_SKILL_WITH_LAYER
            + "| Check | Severity | Method | Expectation | Violation signal |\n"
            + "| --- | --- | --- | --- | --- |\n"
            + skill_row
            + "\n"
        )
        (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
        (skill_dir / "checks.json").write_text(catalog, encoding="utf-8")

    def test_severity_catalog_accepts_rated_checks_shown_in_the_skill_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_severity_audit(
                root,
                "| Single test runner | high | tool | Exactly one runner. | Two runners. |",
                self.severity_catalog(),
            )
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertEqual(findings, [])

    def test_severity_catalog_requires_rating_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_severity_audit(
                root,
                "| Single test runner | high | tool | Exactly one runner. | Two runners. |",
                self.severity_catalog(severity="urgent", method=None, rationale=None, lastVerified="October 2026"),
            )
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            messages = [finding.message for finding in findings]
            self.assertTrue(any("severity must be one of" in message for message in messages))
            self.assertTrue(any("missing non-empty method" in message for message in messages))
            self.assertTrue(any("missing non-empty rationale" in message for message in messages))
            self.assertTrue(any("lastVerified must be a date" in message for message in messages))

    def test_severity_catalog_rejects_impossible_dates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_severity_audit(
                root,
                "| Single test runner | high | tool | Exactly one runner. | Two runners. |",
                self.severity_catalog(lastVerified="2026-99-99"),
            )
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertTrue(any("lastVerified must be a date" in finding.message for finding in findings))

    def test_severity_catalog_requires_the_skill_row_to_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_severity_audit(
                root,
                "| Single test runner | low | model | Exactly one runner. | Two runners. |",
                self.severity_catalog(),
            )
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertTrue(any("must show severity 'high' and method 'tool'" in finding.message for finding in findings))

    def test_related_checks_must_name_existing_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_severity_audit(
                root,
                "| Single test runner | high | tool | Exactly one runner. | Two runners. |",
                self.severity_catalog(relatedChecks=["other-audit.missing-check"]),
            )
            findings: list[Any] = []
            validate_playbook.validate_related_checks(root, findings)
            self.assertTrue(any("unknown check: other-audit.missing-check" in finding.message for finding in findings))

    def test_related_checks_must_be_a_list_in_every_catalog_schema(self) -> None:
        for schema in ("1.1.0", "1.2.0"):
            for value in (None, 5, True, "other-audit.real-check", {"checkId": "other-audit.real-check"}):
                with self.subTest(schema=schema, value=value), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    catalog = json.loads(self.severity_catalog())
                    catalog["schemaVersion"] = schema
                    # Set the value directly: the helper drops None, and an explicit null must fail.
                    catalog["checks"][0]["relatedChecks"] = value
                    self.write_severity_audit(
                        root,
                        "| Single test runner | high | tool | Exactly one runner. | Two runners. |",
                        json.dumps(catalog),
                    )
                    findings: list[Any] = []
                    validate_playbook.validate_check_metadata(root, findings)
                    validate_playbook.validate_related_checks(root, findings)
                    self.assertEqual(
                        [finding.message for finding in findings],
                        ["example-audit.single-test-runner relatedChecks must be a list of checkId strings"],
                    )

    def test_related_checks_must_be_distinct_checks_in_other_audits(self) -> None:
        cases = {
            "duplicate": (["other-audit.real-check", "other-audit.real-check"], "lists other-audit.real-check more than once"),
            "same audit": (["example-audit.single-test-runner"], "must name checks in other audits: example-audit.single-test-runner"),
            "valid": (["other-audit.real-check"], None),
        }
        for name, (related, expected) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.write_severity_audit(
                    root,
                    "| Single test runner | high | tool | Exactly one runner. | Two runners. |",
                    self.severity_catalog(relatedChecks=related),
                )
                other = root / "other-audit"
                other.mkdir()
                (other / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
                (other / "checks.json").write_text(
                    json.dumps({"checks": [{"checkId": "other-audit.real-check"}]}), encoding="utf-8"
                )
                findings: list[Any] = []
                validate_playbook.validate_related_checks(root, findings)
                messages = [finding.message for finding in findings]
                if expected is None:
                    self.assertEqual(messages, [])
                else:
                    self.assertEqual(len(messages), 1, messages)
                    self.assertIn(expected, messages[0])

    def test_exact_title_match_wins_over_a_longer_row_it_prefixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = json.loads(self.severity_catalog())
            catalog["checks"].append(
                {
                    "checkId": "example-audit.single-test-runner-in-continuous-integration",
                    "layer": "test-runner",
                    "title": "Single test runner in continuous integration",
                    "expectation": "Continuous integration runs one test runner.",
                    "violationSignal": "Continuous integration runs two test runners.",
                    "severity": "low",
                    "method": "model",
                    "rationale": "A second runner in continuous integration doubles its maintenance.",
                    "lastVerified": "2026-10-07",
                }
            )
            self.write_severity_audit(
                root,
                "| Single test runner | high | tool | Exactly one runner. | Two runners. |\n"
                "| Single test runner in continuous integration | low | model | One runner there. | Two runners there. |",
                json.dumps(catalog),
            )
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertEqual(findings, [])

    def test_severity_catalog_distinguishes_missing_and_ambiguous_rows(self) -> None:
        cases = {
            "no row": (
                "| Different check | high | tool | Exactly one runner. | Two runners. |",
                "has no SKILL.md table row whose first cell matches its title",
            ),
            "two rows": (
                "| Single test runner | high | tool | Exactly one runner. | Two runners. |\n\n"
                "| Check | Severity | Method | Expectation | Violation signal |\n"
                "| --- | --- | --- | --- | --- |\n"
                "| Single test runner | high | tool | Exactly one runner. | Two runners. |",
                "title matches 2 SKILL.md table rows",
            ),
        }
        for name, (rows, expected) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.write_severity_audit(root, rows, self.severity_catalog())
                findings: list[Any] = []
                validate_playbook.validate_check_metadata(root, findings)
                self.assertEqual(len(findings), 1, findings)
                self.assertIn(expected, findings[0].message)

    def test_severity_row_values_must_sit_in_their_columns(self) -> None:
        cases = {
            "swapped columns": (
                "| Check | Severity | Method | Expectation | Violation signal |\n",
                "| Single test runner | tool | high | Exactly one runner. | Two runners. |",
                False,
            ),
            "value inside a longer cell": (
                "| Check | Expectation | Rating | Approach |\n",
                "| Single test runner | Exactly one runner. | high risk | tool |",
                False,
            ),
            "own cells without Severity or Method headers": (
                "| Check | Expectation | Rating | Approach |\n",
                "| Single test runner | Exactly one runner. | high | tool |",
                True,
            ),
        }
        for name, (header, row, accepted) in cases.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                skill_dir = Path(tmp) / "example-audit"
                skill_dir.mkdir()
                separator = "|" + " --- |" * (header.count("|") - 1) + "\n"
                (skill_dir / "SKILL.md").write_text(
                    VALID_SKILL_WITH_LAYER + header + separator + row + "\n", encoding="utf-8"
                )
                (skill_dir / "checks.json").write_text(self.severity_catalog(), encoding="utf-8")
                findings: list[Any] = []
                validate_playbook.validate_check_metadata(Path(tmp), findings)
                if accepted:
                    self.assertEqual(findings, [])
                else:
                    self.assertTrue(
                        any("must show severity 'high' and method 'tool'" in finding.message for finding in findings),
                        findings,
                    )

    def test_severity_catalog_rejects_future_last_verified_dates(self) -> None:
        today = datetime.now(timezone.utc).date()
        for value, rejected in ((today + timedelta(days=366)).isoformat(), True), (today.isoformat(), False):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                self.write_severity_audit(
                    root,
                    "| Single test runner | high | tool | Exactly one runner. | Two runners. |",
                    self.severity_catalog(lastVerified=value),
                )
                findings: list[Any] = []
                validate_playbook.validate_check_metadata(root, findings)
                messages = [finding.message for finding in findings]
                if rejected:
                    self.assertEqual(
                        messages,
                        ["example-audit.single-test-runner lastVerified cannot be later than today's UTC date"],
                    )
                else:
                    self.assertEqual(messages, [])

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

    def test_check_metadata_requires_soft_check_wording_in_canonical_row(self) -> None:
        soft_catalog = VALID_CHECKS_JSON.replace(
            '      "violationSignal": "Multiple test runners are configured."',
            '      "violationSignal": "Multiple test runners are configured.",\n      "softCheck": true',
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                VALID_SKILL_WITH_LAYER
                + "\n| Check | Expectation | Violation signal |\n"
                + "| --- | --- | --- |\n"
                + "| Single test runner | Exactly one runner. | Multiple runners. |\n",
                encoding="utf-8",
            )
            (skill_dir / "checks.json").write_text(soft_catalog, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_check_metadata(root, findings)
            self.assertTrue(any("must be documented as a soft check" in finding.message for finding in findings))

            skill_text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            (skill_dir / "SKILL.md").write_text(
                skill_text.replace(
                    "| Single test runner | Exactly one runner. |",
                    "| Single test runner | Exactly one runner. Soft check. |",
                ),
                encoding="utf-8",
            )
            findings = []
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

    def test_audits_must_publish_through_the_shared_protocol(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(VALID_SKILL, encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_audit_protocol(root, findings)
            messages = [finding.message for finding in findings]
            self.assertTrue(any("audit protocol bundle missing: scripts/audit_run.py" in message for message in messages))
            self.assertTrue(any("must begin its run through the shared protocol script" in message for message in messages))
            self.assertTrue(any("must link the run protocol" in message for message in messages))

    def test_audit_protocol_rule_passes_with_bundle_and_reference(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protocol = root / "audit-protocol"
            (protocol / "scripts").mkdir(parents=True)
            (protocol / "references").mkdir()
            for relative in ("SKILL.md", "scripts/audit_run.py", "references/run-protocol.md"):
                (protocol / relative).write_text("placeholder\n", encoding="utf-8")
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(VALID_SKILL + PUBLISHING_SECTION.format(audit="example-audit"), encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_audit_protocol(root, findings)
            self.assertEqual(findings, [])

    def test_audit_protocol_rule_requires_the_audits_own_begin_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "example-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                VALID_SKILL
                + PUBLISHING_SECTION.format(audit="example-audit-two")
                + "\nDo not use audit-protocol/scripts/audit_run.py.\n",
                encoding="utf-8",
            )
            findings: list[Any] = []
            validate_playbook.validate_audit_protocol(root, findings)
            messages = [finding.message for finding in findings]
            self.assertTrue(any("begin example-audit" in message for message in messages), messages)

    def test_audit_protocol_rule_skips_stubs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            protocol = root / "audit-protocol"
            (protocol / "scripts").mkdir(parents=True)
            (protocol / "references").mkdir()
            for relative in ("SKILL.md", "scripts/audit_run.py", "references/run-protocol.md"):
                (protocol / relative).write_text("placeholder\n", encoding="utf-8")
            skill_dir = root / "planned-audit"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\nname: planned-audit\ndescription: Planned.\ndisable-model-invocation: true\nargument-hint: \"[--worktree]\"\n---\n\n# /planned-audit\n\n**Status:** stub\n",
                encoding="utf-8",
            )
            findings: list[Any] = []
            validate_playbook.validate_audit_protocol(root, findings)
            self.assertEqual(findings, [])

    def test_validation_skips_git_ignored_worktree_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / ".gitignore").write_text(".claude/worktrees/\n.worktrees/\n", encoding="utf-8")
            (root / "README.md").write_text("Clean.\n", encoding="utf-8")
            for ignored in (".claude/worktrees/agent", ".worktrees/audit"):
                directory = root / ignored
                directory.mkdir(parents=True)
                (directory / "README.md").write_text("[Missing](missing.md) \n", encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_markdown_links(root, findings)
            validate_playbook.validate_trailing_whitespace(root, findings)
            self.assertEqual(findings, [])

    def test_validation_skips_nested_git_checkouts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "README.md").write_text("Clean.\n", encoding="utf-8")
            nested = root / "nested-checkout"
            subprocess.run(["git", "init", "-q", str(nested)], check=True)
            (nested / "README.md").write_text("[Missing](missing.md)\n", encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_markdown_links(root, findings)
            self.assertEqual(findings, [])

    def test_validation_covers_tracked_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "tracked.md").write_text("[Missing](missing.md) \n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "tracked.md"], check=True)
            findings: list[Any] = []
            validate_playbook.validate_markdown_links(root, findings)
            validate_playbook.validate_trailing_whitespace(root, findings)
            messages = [finding.message for finding in findings]
            self.assertTrue(any("internal link target does not exist" in message for message in messages))
            self.assertTrue(any("trailing whitespace" in message for message in messages))

    def test_validation_of_a_copy_inside_another_repository_falls_back_to_walking(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outer = Path(tmp)
            subprocess.run(["git", "init", "-q", str(outer)], check=True)
            (outer / ".gitignore").write_text("playbook/\n", encoding="utf-8")
            root = outer / "playbook"
            root.mkdir()
            (root / "README.md").write_text("[Missing](missing.md)\n", encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_markdown_links(root, findings)
            self.assertTrue(any("internal link target does not exist" in finding.message for finding in findings))

    def test_validation_includes_untracked_files_that_are_not_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "draft.md").write_text("[Missing](missing.md)\n", encoding="utf-8")
            (root / "notes.json").write_text("{} \n", encoding="utf-8")
            findings: list[Any] = []
            validate_playbook.validate_markdown_links(root, findings)
            validate_playbook.validate_trailing_whitespace(root, findings)
            messages = [finding.message for finding in findings]
            self.assertTrue(any("internal link target does not exist" in message for message in messages))
            self.assertTrue(any("trailing whitespace" in message for message in messages))

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

    def write_score_bundle(self, root: Path, policy: dict[str, Any]) -> None:
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
        (score / "score-policy.json").write_text(json.dumps(policy), encoding="utf-8")

    def severity_policy(self) -> dict[str, Any]:
        return {
            "schemaVersion": "1.1.0",
            "policyVersion": "2.0.0",
            "scorePrecision": 2,
            "statusPoints": {"present": "1.0", "partial": "0.5", "missing": "0.0", "violation": "0.0"},
            "severityWeights": {"critical": "8", "high": "4", "medium": "2", "low": "1"},
            "checkWeights": {"standard": "1.0", "soft": "0.5"},
            "audits": [{"name": "example-audit", "weight": "1.0"}],
            "bands": [{"name": "Strong", "minimum": "90.00"}, {"name": "High risk", "minimum": "0.00"}],
        }

    def test_severity_policy_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_score_bundle(root, self.severity_policy())
            findings: list[Any] = []
            validate_playbook.validate_score_policy(root, findings)
            self.assertEqual(findings, [])

    def test_severity_policy_must_rate_every_severity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = self.severity_policy()
            del policy["severityWeights"]["medium"]
            self.write_score_bundle(root, policy)
            findings: list[Any] = []
            validate_playbook.validate_score_policy(root, findings)
            self.assertTrue(any("severityWeights must define exactly" in finding.message for finding in findings))

    def test_severity_weights_need_the_severity_policy_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            policy = self.severity_policy()
            policy["schemaVersion"] = "1.0.0"
            self.write_score_bundle(root, policy)
            findings: list[Any] = []
            validate_playbook.validate_score_policy(root, findings)
            self.assertTrue(any("severityWeights requires schemaVersion 1.1.0" in finding.message for finding in findings))

    def test_original_policy_schema_rejects_any_severity_weights_value(self) -> None:
        for value in (None, [], "8"):
            with self.subTest(value=value), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                policy = self.severity_policy()
                policy["schemaVersion"] = "1.0.0"
                policy["severityWeights"] = value
                self.write_score_bundle(root, policy)
                findings: list[Any] = []
                validate_playbook.validate_score_policy(root, findings)
                self.assertEqual(
                    [finding.message for finding in findings],
                    ["severityWeights requires schemaVersion 1.1.0"],
                )

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
                "disable-model-invocation: true\n"
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
                "disable-model-invocation: true\n"
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
