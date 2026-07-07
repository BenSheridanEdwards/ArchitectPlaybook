from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "validate_pr_body.py"

spec = importlib.util.spec_from_file_location("validate_pr_body", SCRIPT_PATH)
assert spec and spec.loader
validate_pr_body = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = validate_pr_body
spec.loader.exec_module(validate_pr_body)


GOOD_TITLE = "feat(checks): add machine-readable inventories"

GOOD_BODY = """# Why does this feature exist?

To make audits machine-checkable.

# What changed?

Added checks.json for twelve audits.

# Behavioural Proof (with video and screenshots)

Not applicable — no rendered UI in this repository.

# Verification Summary

- Commands run: validator and unit tests, both green.
"""

TEMPLATE_BODY = (ROOT / ".github" / "pull_request_template.md").read_text(encoding="utf-8")


class ValidatePrBodyTests(unittest.TestCase):
    def test_good_title_and_body_passes(self) -> None:
        self.assertEqual(validate_pr_body.validate(GOOD_TITLE, GOOD_BODY), [])

    def test_empty_template_body_fails(self) -> None:
        errors = validate_pr_body.validate(GOOD_TITLE, TEMPLATE_BODY)
        self.assertTrue(any("placeholder content" in error for error in errors))
        self.assertTrue(any("Behavioural Proof" in error for error in errors))

    def test_agent_prefixed_title_fails(self) -> None:
        errors = validate_pr_body.validate("[claude] add stuff", GOOD_BODY)
        self.assertTrue(any("agent or tool prefix" in error for error in errors))

    def test_non_conventional_title_fails(self) -> None:
        errors = validate_pr_body.validate("Add stuff without a type", GOOD_BODY)
        self.assertTrue(any("Conventional Commit subject" in error for error in errors))

    def test_missing_section_fails(self) -> None:
        body_without_verification = GOOD_BODY.replace("# Verification Summary", "# Notes")
        errors = validate_pr_body.validate(GOOD_TITLE, body_without_verification)
        self.assertTrue(
            any("missing required section heading: Verification Summary" in error for error in errors)
        )

    def test_behavioural_proof_with_inline_image_passes(self) -> None:
        body_with_image = GOOD_BODY.replace(
            "Not applicable — no rendered UI in this repository.",
            "![screenshot](https://github.com/owner/repo/blob/branch/docs/proof/x.png?raw=1)",
        )
        self.assertEqual(validate_pr_body.validate(GOOD_TITLE, body_with_image), [])


if __name__ == "__main__":
    unittest.main()
