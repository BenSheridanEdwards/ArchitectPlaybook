from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
spec = importlib.util.spec_from_file_location("install_playbook", ROOT / "scripts" / "install_playbook.py")
assert spec and spec.loader
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


def git(root: Path, *arguments: str) -> None:
    subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)


class InstallPlaybookTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.base = Path(self.directory.name).resolve()
        self.clone = self.base / "playbook"
        for name in ("audit-protocol", "repository-quality-score", "one-audit", "two-audit", "pre-audit-setup", "install-architect-playbook-locally"):
            folder = self.clone / name
            folder.mkdir(parents=True)
            (folder / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")
        (self.clone / "repository-quality-score" / "scripts").mkdir()
        (self.clone / installer.CALCULATOR).write_text("calculator\n", encoding="utf-8")
        (self.clone / "repository-quality-score" / "score-policy.json").write_text(
            json.dumps({"audits": [{"name": "one-audit"}, {"name": "two-audit"}]}), encoding="utf-8"
        )
        git(self.clone, "init", "-q")
        git(self.clone, "add", "-A")
        git(self.clone, "-c", "user.email=t@e.st", "-c", "user.name=T", "commit", "-qm", "init")
        self.destination = self.base / "project" / ".claude" / "skills"

    def test_a_worktree_is_a_clone_and_an_installed_copy_is_not(self) -> None:
        installer.require_clone(self.clone)
        worktree = self.base / "worktree"
        git(self.clone, "worktree", "add", "-q", "--detach", str(worktree))
        installer.require_clone(worktree)
        copy = self.base / "copy"
        copy.mkdir()
        (copy / "audit-protocol").mkdir()
        (copy / "audit-protocol" / "SKILL.md").write_text("# copy\n", encoding="utf-8")
        with self.assertRaises(installer.InstallError):
            installer.require_clone(copy)

    def test_selection_closes_over_dependencies_and_rejects_conflicts(self) -> None:
        self.assertEqual(installer.select(self.clone, ["one-audit"], []), (["audit-protocol", "one-audit"], True))
        self.assertEqual(
            installer.select(self.clone, ["repository-quality-score"], []),
            (["audit-protocol", "one-audit", "repository-quality-score", "two-audit"], False),
        )
        everything, calculator_only = installer.select(self.clone, [], ["repository-quality-score"])
        self.assertNotIn("install-architect-playbook-locally", everything)
        self.assertTrue(calculator_only)
        for include, exclude in ((["one-audit"], ["audit-protocol"]), ([], ["two-audit"]), (["ghost"], [])):
            with self.subTest(include=include, exclude=exclude), self.assertRaises(installer.InstallError):
                installer.select(self.clone, include, exclude)

    def test_the_digest_sees_contents_names_and_links_and_fails_on_a_missing_folder(self) -> None:
        folder = self.base / "folder"
        folder.mkdir()
        (folder / "a.txt").write_text("one\n", encoding="utf-8")
        first = installer.digest(folder)
        (folder / "a.txt").write_text("two\n", encoding="utf-8")
        second = installer.digest(folder)
        (folder / "a.txt").rename(folder / "b.txt")
        third = installer.digest(folder)
        os.symlink("b.txt", folder / "link")
        fourth = installer.digest(folder)
        (folder / "link").unlink()
        os.symlink("elsewhere", folder / "link")
        fifth = installer.digest(folder)
        self.assertEqual(len({first, second, third, fourth, fifth}), 5)
        with self.assertRaises(installer.InstallError):
            installer.digest(self.base / "missing")

    def test_plan_then_apply_replaces_only_changed_owned_folders(self) -> None:
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        steps = installer.plan(self.clone, self.destination, skills, calculator_only)
        self.assertEqual({step["status"] for step in steps}, {"installed"})
        self.assertFalse(self.destination.exists())
        installer.apply(self.clone, self.destination, steps)
        self.assertTrue((self.destination / installer.CALCULATOR).is_file())
        self.assertFalse((self.destination / "repository-quality-score" / "SKILL.md").exists())
        (self.destination / "one-audit" / "stale.md").write_text("old\n", encoding="utf-8")
        (self.destination / "graphify").mkdir()
        steps = installer.plan(self.clone, self.destination, skills, calculator_only)
        self.assertEqual({step["skill"]: step["status"] for step in steps}["one-audit"], "updated")
        self.assertEqual({step["skill"]: step["status"] for step in steps}["audit-protocol"], "unchanged")
        installer.apply(self.clone, self.destination, steps)
        self.assertFalse((self.destination / "one-audit" / "stale.md").exists())
        self.assertTrue((self.destination / "graphify").is_dir())

    def test_only_a_claude_skills_folder_is_a_destination(self) -> None:
        installer.require_destination(self.destination)
        with self.assertRaises(installer.InstallError):
            installer.require_destination(self.base / "somewhere")

    def test_the_command_prints_a_plan_without_writing(self) -> None:
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = installer.main(["--destination", str(self.destination), "--include", "architecture-audit", "--json"])
        self.assertEqual(code, 0)
        plan = json.loads(output.getvalue())
        self.assertFalse(plan["applied"])
        self.assertIn({"skill": "audit-protocol", "status": "installed"}, plan["steps"])
        self.assertFalse(self.destination.exists())


if __name__ == "__main__":
    unittest.main()
