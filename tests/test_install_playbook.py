from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import shutil
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
            (folder / "SKILL.md").write_text(f"---\nname: {name}\n---\n# {name}\n", encoding="utf-8")
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

    def test_a_foreign_skill_with_the_same_folder_name_is_never_replaced(self) -> None:
        foreign = self.destination / "one-audit"
        foreign.mkdir(parents=True)
        (foreign / "SKILL.md").write_text("---\nname: custom-security\n---\n", encoding="utf-8")
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        with self.assertRaises(installer.InstallError):
            installer.plan(self.clone, self.destination, skills, calculator_only)
        self.assertTrue((foreign / "SKILL.md").exists())

    def test_symbolic_links_below_the_destination_are_refused(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("keep\n", encoding="utf-8")
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        cases = {
            "scripts folder": (Path("repository-quality-score/scripts"), outside),
            "calculator file": (installer.CALCULATOR, outside / "keep.txt"),
            "dangling link": (installer.CALCULATOR, outside / "missing.py"),
            "skill folder": (Path("one-audit"), outside),
        }
        for name, (link, target) in cases.items():
            with self.subTest(name=name):
                if self.destination.exists():
                    shutil.rmtree(self.destination)
                (self.destination / link).parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, self.destination / link)
                with self.assertRaises(installer.InstallError):
                    installer.plan(self.clone, self.destination, skills, calculator_only)
                self.assertEqual((outside / "keep.txt").read_text(encoding="utf-8"), "keep\n")

    def test_a_calculator_only_install_can_become_the_full_scorer(self) -> None:
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        installer.apply(self.clone, self.destination, installer.plan(self.clone, self.destination, skills, calculator_only))
        (self.destination / "repository-quality-score" / ".DS_Store").write_bytes(b"finder")
        skills, calculator_only = installer.select(self.clone, ["repository-quality-score"], [])
        steps = installer.plan(self.clone, self.destination, skills, calculator_only)
        self.assertEqual({step["skill"]: step["status"] for step in steps}["repository-quality-score"], "updated")
        installer.apply(self.clone, self.destination, steps)
        self.assertTrue((self.destination / "repository-quality-score" / "SKILL.md").is_file())

    def test_a_linked_claude_or_skills_folder_is_refused(self) -> None:
        other = self.base / "other" / ".claude" / "skills"
        other.mkdir(parents=True)
        for linked in ("skills", ".claude"):
            with self.subTest(linked=linked):
                project = self.base / f"linked-{linked}"
                if linked == "skills":
                    (project / ".claude").mkdir(parents=True)
                    os.symlink(other, project / ".claude" / "skills")
                else:
                    project.mkdir()
                    os.symlink(other.parent, project / ".claude")
                with self.assertRaises(installer.InstallError):
                    installer.require_destination(project / ".claude" / "skills")

    def test_an_unowned_scorer_folder_is_never_overwritten(self) -> None:
        foreign = self.destination / installer.CALCULATOR
        foreign.parent.mkdir(parents=True)
        foreign.write_text("someone else's calculator\n", encoding="utf-8")
        (self.destination / "repository-quality-score" / "notes.md").write_text("theirs\n", encoding="utf-8")
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        with self.assertRaises(installer.InstallError):
            installer.plan(self.clone, self.destination, skills, calculator_only)
        self.assertEqual(foreign.read_text(encoding="utf-8"), "someone else's calculator\n")

    def test_linked_sources_are_refused(self) -> None:
        outside = self.base / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("private\n", encoding="utf-8")
        calculator = self.clone / installer.CALCULATOR
        calculator.unlink()
        os.symlink(outside / "secret.txt", calculator)
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        with self.assertRaises(installer.InstallError):
            installer.plan(self.clone, self.destination, skills, calculator_only)
        self.assertFalse(self.destination.exists())
        linked_skill = self.base / "elsewhere-audit"
        linked_skill.mkdir()
        (linked_skill / "SKILL.md").write_text("---\nname: three-audit\n---\n", encoding="utf-8")
        os.symlink(linked_skill, self.clone / "three-audit")
        with self.assertRaises(installer.InstallError):
            installer.plan(self.clone, self.destination, ["three-audit"], False)

    def test_a_hard_link_to_the_old_calculator_is_never_written_through(self) -> None:
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        installer.apply(self.clone, self.destination, installer.plan(self.clone, self.destination, skills, calculator_only))
        outside = self.base / "shared.py"
        outside.write_text("shared\n", encoding="utf-8")
        target = self.destination / installer.CALCULATOR
        target.unlink()
        os.link(outside, target)
        installer.apply(self.clone, self.destination, installer.plan(self.clone, self.destination, skills, calculator_only))
        self.assertEqual(outside.read_text(encoding="utf-8"), "shared\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "calculator\n")

    def test_a_failed_copy_keeps_the_previous_install(self) -> None:
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        installer.apply(self.clone, self.destination, installer.plan(self.clone, self.destination, skills, calculator_only))
        (self.destination / "one-audit" / "local.md").write_text("previous\n", encoding="utf-8")
        steps = installer.plan(self.clone, self.destination, skills, calculator_only)
        original = installer.shutil.copytree

        def failing_copy(*arguments: object, **options: object) -> None:
            raise OSError("disk full")

        installer.shutil.copytree = failing_copy
        try:
            with self.assertRaises(OSError):
                installer.apply(self.clone, self.destination, steps)
        finally:
            installer.shutil.copytree = original
        self.assertEqual((self.destination / "one-audit" / "local.md").read_text(encoding="utf-8"), "previous\n")

    def test_planted_staging_paths_are_neither_followed_nor_deleted(self) -> None:
        skills, calculator_only = installer.select(self.clone, ["one-audit"], [])
        installer.apply(self.clone, self.destination, installer.plan(self.clone, self.destination, skills, calculator_only))
        outside = self.base / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        os.symlink(outside, self.destination / ".one-audit.installing")
        keep = self.destination / ".one-audit.previous"
        keep.mkdir()
        (keep / "valuable.md").write_text("keep\n", encoding="utf-8")
        (self.destination / installer.CALCULATOR).write_text("old\n", encoding="utf-8")
        (self.destination / "one-audit" / "extra.md").write_text("old\n", encoding="utf-8")
        installer.apply(self.clone, self.destination, installer.plan(self.clone, self.destination, skills, calculator_only))
        self.assertEqual(outside.read_text(encoding="utf-8"), "outside\n")
        self.assertEqual((keep / "valuable.md").read_text(encoding="utf-8"), "keep\n")
        self.assertEqual((self.destination / installer.CALCULATOR).read_text(encoding="utf-8"), "calculator\n")
        self.assertFalse((self.destination / "one-audit" / "extra.md").exists())

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
