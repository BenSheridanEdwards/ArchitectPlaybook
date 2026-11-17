"""Exercise the gate through its real PreToolUse standard-input interface."""

import json
import pathlib
import subprocess
import sys
import unittest


HOOK = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "block-no-verify.py"


class BlockNoVerifyTests(unittest.TestCase):
    def check_command(self, command, expected):
        result = subprocess.run(
            [sys.executable, str(HOOK)],
            input=json.dumps({"tool_input": {"command": command}}),
            text=True, capture_output=True, check=False,
        )
        self.assertEqual(result.returncode, expected, result.stderr)
        if expected == 2:
            self.assertIn("Blocked:", result.stderr)

    def test_commit_short_flags_cannot_skip_the_gates(self):
        for command in (
            "git commit -n -m change",
            "git commit -an -m change",
            "git commit -nm change",
            "git commit -anmchange",
            "git -C 'a working directory' commit -n",
            "git -c user.name=Example commit -n",
            "git --git-dir .git commit -n",
            "/usr/bin/git commit -n",
            "git status; git commit -n",
            "git status\ngit commit -n",
            "git commit path -n",
            "git commit tracked.txt -n -m change",
            "git commit -m ';' -n",
            "git commit --message='a;b' -n",
            "git commit -m \\; -n",
            "git " + chr(92) + "\ncommit -n -m change",
            "git commit -n -m change # don't skip",
            "# don't bypass\ngit commit -n",
            "git commit --message=tag#value -n",
            "git commit -m '# keep this' -n",
        ):
            with self.subTest(command=command):
                self.check_command(command, 2)

    def test_messages_paths_and_push_dry_runs_are_allowed(self):
        for command in (
            "git push -n origin main",
            "git commit -m '-n is an example'",
            "git commit -mn",
            "git commit -F notes.txt",
            "git commit -Fnotes.txt",
            "git commit --message '-n'",
            "git commit --message=-n",
            "git commit -- path-n",
            "git -C commit status -n",
            "printf '%s' 'git commit -n'",
            "git commit -m 'git' -- 'commit' '-n'",
            "git commit -m change # document -n",
            "git commit -m change # note; git commit -n",
            "git commit -m 'quoted # text and git commit -n'",
        ):
            with self.subTest(command=command):
                self.check_command(command, 0)

    def test_long_bypass_flags_remain_blocked(self):
        for command in ("git commit --no-verify -m change", "git push --no-verify"):
            with self.subTest(command=command):
                self.check_command(command, 2)


if __name__ == "__main__":
    unittest.main()
