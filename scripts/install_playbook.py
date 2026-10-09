#!/usr/bin/env python3
"""Plan and apply a copy install of the Architect Playbook skills.

The two installer skills call this script from a playbook clone. It selects
skills, closes the selection over dependencies, compares each skill with the
destination by a digest of file names, contents, and symbolic links, and
replaces only changed playbook-owned folders as a whole. Without --apply it
prints the plan and changes nothing.

Standard library only. Python 3.9 or later.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER_PREFIX = "install-architect-playbook-"
PROTOCOL = "audit-protocol"
SCORER = "repository-quality-score"
CALCULATOR = Path(SCORER) / "scripts" / "calculate_repository_quality_score.py"
SKIPPED_NAMES = {".DS_Store", "__pycache__"}


class InstallError(Exception):
    """A user-facing error that says what to fix."""


def playbook_skills(root: Path) -> list[str]:
    return sorted(
        path.name
        for path in root.iterdir()
        if path.is_dir()
        and not path.name.startswith((".", INSTALLER_PREFIX))
        and (path / "SKILL.md").is_file()
    )


def require_clone(root: Path) -> None:
    """The source must be a Git checkout of the playbook, including a worktree."""
    completed = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False
    )
    top = completed.stdout.strip()
    if completed.returncode != 0 or Path(top).resolve() != root.resolve() or not (root / PROTOCOL / "SKILL.md").is_file():
        raise InstallError(f"{root} is not a playbook clone; run the installer from a clone, or pass --from")


def policy_audits(root: Path) -> list[str]:
    policy = json.loads((root / SCORER / "score-policy.json").read_text(encoding="utf-8"))
    return [entry["name"] for entry in policy.get("audits", []) if isinstance(entry, dict) and isinstance(entry.get("name"), str)]


def select(root: Path, include: list[str], exclude: list[str]) -> tuple[list[str], bool]:
    """Return the skills to install and whether audits need the calculator file alone."""
    available = playbook_skills(root)
    unknown = sorted(set(include + exclude) - set(available))
    if unknown:
        raise InstallError(f"not a playbook skill: {', '.join(unknown)}")
    selected = set(include or available) - set(exclude)
    if SCORER in selected:
        missing = set(policy_audits(root)) - selected
        if missing & set(exclude):
            raise InstallError(f"{SCORER} needs every policy audit; do not exclude {', '.join(sorted(missing & set(exclude)))}")
        selected |= missing
    audits = {name for name in selected if name.endswith("-audit")}
    if audits:
        if PROTOCOL in exclude:
            raise InstallError(f"audits publish through {PROTOCOL}; do not exclude it")
        selected.add(PROTOCOL)
    return sorted(selected), bool(audits) and SCORER not in selected


def digest(path: Path) -> str:
    """Hash every file's name and contents and every link's name and target, without following links."""
    if not path.is_dir():
        raise InstallError(f"cannot read {path}")
    entries: list[str] = []
    for current, directories, files in os.walk(path, followlinks=False):
        directories[:] = sorted(name for name in directories if name not in SKIPPED_NAMES)
        base = Path(current)
        for name in sorted(files + [item for item in directories if (base / item).is_symlink()]):
            if name in SKIPPED_NAMES:
                continue
            item = base / name
            relative = item.relative_to(path).as_posix()
            if item.is_symlink():
                entries.append(f"link {relative} {os.readlink(item)}")
            else:
                entries.append(f"file {relative} {hashlib.sha256(item.read_bytes()).hexdigest()}")
    return hashlib.sha256("\n".join(entries).encode("utf-8")).hexdigest()


def skill_name(folder: Path) -> str | None:
    """The `name` in a skill folder's SKILL.md frontmatter, if it has one."""
    try:
        text = (folder / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---\n"):
        return None
    for line in text[4 : text.find("\n---", 4)].splitlines():
        key, _, value = line.partition(":")
        if key.strip() == "name":
            return value.strip().strip("'\"")
    return None


def require_no_links(destination: Path, relative: Path) -> None:
    """No component below the destination may be a symbolic link, so nothing is written outside it."""
    current = destination
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise InstallError(f"{current} is a symbolic link; move it aside first")


def require_owned(target: Path, name: str) -> None:
    """An existing folder is replaced only if it is this playbook's skill of the same name."""
    found = skill_name(target)
    if found != name:
        raise InstallError(
            f"{target} exists but is not the playbook's {name} skill (its SKILL.md name is {found!r}); move it aside first"
        )


def plan(root: Path, destination: Path, skills: list[str], calculator_only: bool) -> list[dict[str, str]]:
    steps = []
    for name in skills:
        target = destination / name
        require_no_links(destination, Path(name))
        if target.exists() and not target.is_dir():
            raise InstallError(f"{target} is not a folder; move it aside first")
        if target.exists():
            require_owned(target, name)
        if not target.exists():
            status = "installed"
        else:
            status = "unchanged" if digest(target) == digest(root / name) else "updated"
        steps.append({"skill": name, "status": status})
    if calculator_only:
        target = destination / CALCULATOR
        source = root / CALCULATOR
        require_no_links(destination, CALCULATOR)
        scorer = destination / SCORER
        if (scorer / "SKILL.md").exists():
            require_owned(scorer, SCORER)
        if not target.exists():
            status = "installed"
        else:
            status = "unchanged" if target.read_bytes() == source.read_bytes() else "updated"
        steps.append({"skill": f"{SCORER} (calculator file only)", "status": status})
    return steps


def apply(root: Path, destination: Path, steps: list[dict[str, str]]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for step in steps:
        if step["status"] == "unchanged":
            continue
        if step["skill"].endswith("(calculator file only)"):
            target = destination / CALCULATOR
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(root / CALCULATOR, target)
            continue
        target = destination / step["skill"]
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(root / step["skill"], target, symlinks=True, ignore=shutil.ignore_patterns(*SKIPPED_NAMES))


def require_destination(destination: Path) -> Path:
    """Only a `.claude/skills` folder may be written."""
    resolved = destination.expanduser().resolve()
    if resolved.name != "skills" or resolved.parent.name != ".claude":
        raise InstallError(f"destination must be a .claude/skills folder: {destination}")
    return resolved


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--destination", required=True, help="the .claude/skills folder to install into")
    parser.add_argument("--include", action="append", default=[], help="install only this skill and what it needs")
    parser.add_argument("--exclude", action="append", default=[], help="skip this skill")
    parser.add_argument("--apply", action="store_true", help="write the plan; without it, only print it")
    parser.add_argument("--json", action="store_true", help="print the plan as JSON")
    arguments = parser.parse_args(argv)
    try:
        require_clone(ROOT)
        destination = require_destination(Path(arguments.destination))
        skills, calculator_only = select(ROOT, arguments.include, arguments.exclude)
        steps = plan(ROOT, destination, skills, calculator_only)
        if arguments.apply:
            apply(ROOT, destination, steps)
    except InstallError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    if arguments.json:
        print(json.dumps({"destination": str(destination), "applied": arguments.apply, "steps": steps}, indent=2))
    else:
        for step in steps:
            print(f"{step['status'] + ':':11} {step['skill']}")
        written = sum(step["status"] != "unchanged" for step in steps)
        verb = "written" if arguments.apply else "would be written"
        print(f"\n{len(steps)} skills checked; {written} {verb} into {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
