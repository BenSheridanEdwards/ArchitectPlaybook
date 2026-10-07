from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PATH = ROOT / "scripts" / "install-git-hooks.py"

spec = importlib.util.spec_from_file_location("install_git_hooks", INSTALLER_PATH)
assert spec and spec.loader
install_git_hooks = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = install_git_hooks
spec.loader.exec_module(install_git_hooks)


class InstallGitHooksTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        base = Path(self.directory.name)
        self.hooks = base / "hooks"
        self.hooks.mkdir()
        self.sources = base / "sources"
        self.sources.mkdir()
        (self.sources / "pre-commit").write_text("#!/bin/sh\necho playbook\n", encoding="utf-8")
        self.original = (install_git_hooks.HOOKS, install_git_hooks.git_hooks_path)
        install_git_hooks.HOOKS = self.sources
        install_git_hooks.git_hooks_path = lambda: self.hooks

    def tearDown(self) -> None:
        install_git_hooks.HOOKS, install_git_hooks.git_hooks_path = self.original

    def test_refuses_to_overwrite_a_different_hook_without_force(self) -> None:
        (self.hooks / "pre-commit").write_text("#!/bin/sh\necho mine\n", encoding="utf-8")
        self.assertEqual(install_git_hooks.install(force=False), 1)
        self.assertEqual((self.hooks / "pre-commit").read_text(encoding="utf-8"), "#!/bin/sh\necho mine\n")

    def test_repeated_forced_installs_keep_every_backup(self) -> None:
        for number in range(3):
            (self.hooks / "pre-commit").write_text(f"#!/bin/sh\necho mine {number}\n", encoding="utf-8")
            self.assertEqual(install_git_hooks.install(force=True), 0)
        backups = sorted(path for path in self.hooks.iterdir() if "backup" in path.name)
        self.assertEqual(len(backups), 3)
        contents = {path.read_text(encoding="utf-8") for path in backups}
        self.assertEqual(contents, {f"#!/bin/sh\necho mine {number}\n" for number in range(3)})
        self.assertEqual((self.hooks / "pre-commit").read_text(encoding="utf-8"), "#!/bin/sh\necho playbook\n")


if __name__ == "__main__":
    unittest.main()
