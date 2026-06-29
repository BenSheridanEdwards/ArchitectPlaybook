#!/usr/bin/env python3
"""Install repository-local Git hooks for Architect Playbook."""

from __future__ import annotations

import argparse
import stat
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOOKS = ROOT / "scripts" / "git-hooks"


def git_hooks_path() -> Path:
    path = subprocess.check_output(["git", "rev-parse", "--git-path", "hooks"], cwd=ROOT, text=True).strip()
    hooks_path = Path(path)
    return hooks_path if hooks_path.is_absolute() else ROOT / hooks_path


def install(force: bool) -> int:
    git_hooks = git_hooks_path()
    git_hooks.mkdir(parents=True, exist_ok=True)
    installed = []
    for source in sorted(HOOKS.iterdir()):
        if not source.is_file():
            continue
        destination = git_hooks / source.name
        if destination.exists() and destination.read_text(encoding="utf-8", errors="ignore") != source.read_text(encoding="utf-8"):
            backup = destination.with_suffix(destination.suffix + ".architect-playbook-backup")
            if not force:
                print(f"refusing to overwrite existing hook: {destination}")
                print(f"rerun with --force to back it up to {backup} and install the playbook hook")
                return 1
            backup.write_text(destination.read_text(encoding="utf-8", errors="ignore"), encoding="utf-8")
        destination.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        destination.chmod(destination.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        installed.append(source.name)
    print("installed Git hooks: " + ", ".join(installed))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Install Architect Playbook Git hooks into this checkout.")
    parser.add_argument("--force", action="store_true", help="back up and replace existing hooks with different contents")
    args = parser.parse_args()
    return install(force=args.force)


if __name__ == "__main__":
    raise SystemExit(main())
