#!/usr/bin/env python3
"""Plan and create deterministic architect-playbook audit worktrees."""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

EXCLUDED_SKILLS = {
    "install-architect-playbook-globally",
    "install-architect-playbook-locally",
    "pre-audit-setup",
    "preflight",
    "worktree",
}


@dataclass(frozen=True)
class Skill:
    """A slash-command skill that can be targeted by a worktree run."""

    name: str
    path: Path


class HelperError(Exception):
    """An expected user-facing helper failure."""


def run_git(arguments: Sequence[str], repository: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def find_git_root(start: Path) -> Path:
    result = run_git(["rev-parse", "--show-toplevel"], start)
    return Path(result.stdout.strip()).resolve()


def frontmatter_name(skill_file: Path) -> str | None:
    try:
        text = skill_file.read_text(encoding="utf-8")
    except OSError:
        return None
    if "**Status:** stub" in text:
        return None
    match = re.search(r"(?m)^name:\s*([^\s]+)\s*$", text)
    if not match:
        return None
    return match.group(1)


def candidate_skill_roots(project_root: Path, explicit_skills_root: Path | None) -> list[Path]:
    if explicit_skills_root is not None:
        return [explicit_skills_root.resolve()]

    roots: list[Path] = []
    local_skills = project_root / ".claude" / "skills"
    if local_skills.is_dir():
        roots.append(local_skills)
    roots.append(project_root)
    return roots


def enumerate_skills(project_root: Path, explicit_skills_root: Path | None) -> list[Skill]:
    discovered: dict[str, Skill] = {}
    for root in candidate_skill_roots(project_root, explicit_skills_root):
        if not root.is_dir():
            continue
        for skill_file in sorted(root.glob("*/SKILL.md")):
            name = frontmatter_name(skill_file)
            if not name or name in EXCLUDED_SKILLS:
                continue
            if not (name.endswith("-audit") or name == "system-self-improve"):
                continue
            discovered.setdefault(name, Skill(name=name, path=skill_file.parent))
    return [discovered[name] for name in sorted(discovered)]


def resolve_skill(argument: str, skills: Sequence[Skill]) -> Skill:
    names = [skill.name for skill in skills]
    if not argument:
        raise HelperError("No skill name supplied. Available skills: " + ", ".join(names))

    exact = [skill for skill in skills if skill.name == argument]
    if exact:
        return exact[0]

    audit_name = f"{argument}-audit"
    audit_match = [skill for skill in skills if skill.name == audit_name]
    if audit_match:
        return audit_match[0]

    prefix_matches = [skill for skill in skills if skill.name.startswith(argument)]
    if len(prefix_matches) == 1:
        return prefix_matches[0]
    if len(prefix_matches) > 1:
        raise HelperError(
            "Ambiguous skill prefix. Candidates: " + ", ".join(skill.name for skill in prefix_matches)
        )

    raise HelperError("Unknown skill. Available skills: " + ", ".join(names))


def slug_for_skill(skill_name: str) -> str:
    if skill_name.endswith("-audit"):
        return skill_name[: -len("-audit")]
    return skill_name


def git_dirty_state(project_root: Path) -> tuple[bool, list[str]]:
    result = run_git(["status", "--porcelain"], project_root)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return bool(lines), lines


def branch_exists(project_root: Path, branch: str) -> bool:
    result = run_git(["rev-parse", "--verify", "--quiet", f"refs/heads/{branch}"], project_root, check=False)
    return result.returncode == 0


def shell_command(parts: Iterable[os.PathLike[str] | str]) -> str:
    return " ".join(shlex.quote(os.fspath(part)) for part in parts)


def display_path(path: Path, base: Path) -> str:
    try:
        return os.path.relpath(path, base)
    except ValueError:
        return str(path)


def build_metadata(
    project_root: Path,
    skill: Skill,
    worktree_parent: Path,
    allow_dirty: bool,
) -> dict[str, object]:
    slug = slug_for_skill(skill.name)
    branch = f"wt-{slug}"
    worktree_path = (worktree_parent / f"wt-{slug}").resolve()
    dirty, dirty_entries = git_dirty_state(project_root)
    existing_branch = branch_exists(project_root, branch)
    path_exists = worktree_path.exists()

    if path_exists:
        create_command: list[str] | None = None
        worktree_check = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "--is-inside-work-tree"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if worktree_check.returncode != 0:
            raise HelperError(f"Worktree path already exists but is not a Git worktree: {worktree_path}")
        branch_check = subprocess.run(
            ["git", "-C", str(worktree_path), "rev-parse", "--abbrev-ref", "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        existing_worktree_branch = branch_check.stdout.strip() if branch_check.returncode == 0 else ""
        if existing_worktree_branch != branch:
            raise HelperError(
                f"Worktree path already exists on branch {existing_worktree_branch!r}; expected {branch!r}."
            )
        action = "use-existing-worktree"
    elif existing_branch:
        create_command = ["git", "worktree", "add", str(worktree_path), branch]
        action = "add-existing-branch"
    else:
        create_command = ["git", "worktree", "add", str(worktree_path), "-b", branch]
        action = "create-branch-and-worktree"

    return {
        "skillName": skill.name,
        "skillPath": display_path(skill.path, project_root),
        "slug": slug,
        "branch": branch,
        "worktreePath": str(worktree_path),
        "worktreePathRelative": display_path(worktree_path, project_root),
        "worktreeParent": str(worktree_parent.resolve()),
        "projectRoot": str(project_root),
        "dirty": dirty,
        "dirtyEntries": dirty_entries,
        "dirtyAllowed": allow_dirty,
        "branchExists": existing_branch,
        "worktreePathExists": path_exists,
        "action": action,
        "createCommand": create_command,
        "createCommandText": shell_command(create_command) if create_command else None,
        "auditInvocation": f"/{skill.name} --target={display_path(worktree_path, project_root)}",
        "cleanupCommands": [
            shell_command(["git", "worktree", "remove", str(worktree_path)]),
            shell_command(["git", "branch", "-d", branch]),
        ],
    }


def print_human(metadata: dict[str, object], dry_run: bool) -> None:
    print(f"Skill: {metadata['skillName']}")
    print(f"Branch: {metadata['branch']}")
    print(f"Worktree path: {metadata['worktreePathRelative']}")
    print(f"Dirty state: {'dirty' if metadata['dirty'] else 'clean'}")
    print(f"Action: {metadata['action']}")
    command_text = metadata.get("createCommandText")
    if command_text:
        prefix = "Dry-run command" if dry_run else "Command"
        print(f"{prefix}: {command_text}")
    else:
        print("Command: no worktree creation needed")
    print(f"Audit invocation: {metadata['auditInvocation']}")
    print("Cleanup commands:")
    for command in metadata["cleanupCommands"]:  # type: ignore[index]
        print(f"  {command}")


def create_worktree(project_root: Path, metadata: dict[str, object]) -> None:
    command = metadata.get("createCommand")
    if not command:
        return
    subprocess.run(command, cwd=project_root, check=True)


def parse_arguments(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Resolve an architect-playbook skill and create its deterministic Git worktree.",
    )
    parser.add_argument("skill", nargs="?", help="Audit skill name, short name, or unambiguous prefix.")
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path.cwd(),
        help="Repository to inspect. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--skills-root",
        type=Path,
        help="Directory containing skill folders. Defaults to local installed skills, then the repository root.",
    )
    parser.add_argument(
        "--worktree-parent",
        type=Path,
        help="Directory where wt-<slug> worktrees are created. Defaults to the repository parent.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print metadata and commands without creating a worktree.")
    parser.add_argument("--allow-dirty", action="store_true", help="Allow worktree creation when the current repository has uncommitted changes.")
    parser.add_argument("--json", action="store_true", help="Print metadata as JSON.")
    parser.add_argument("--list", action="store_true", help="List available worktree target skills and exit.")
    return parser.parse_args(argv)


def main(argv: Sequence[str]) -> int:
    arguments = parse_arguments(argv)
    try:
        project_root = find_git_root(arguments.project_root.resolve())
        worktree_parent = (arguments.worktree_parent.resolve() if arguments.worktree_parent else project_root.parent)
        skills = enumerate_skills(project_root, arguments.skills_root)
        if arguments.list:
            for skill in skills:
                print(skill.name)
            return 0
        skill = resolve_skill(arguments.skill or "", skills)
        metadata = build_metadata(project_root, skill, worktree_parent, arguments.allow_dirty)
        if metadata["dirty"] and not arguments.allow_dirty and not arguments.dry_run:
            if arguments.json:
                print(json.dumps(metadata, indent=2, sort_keys=True))
            raise HelperError("Repository has uncommitted changes. Commit, stash, or pass --allow-dirty.")
        if arguments.json:
            print(json.dumps(metadata, indent=2, sort_keys=True))
        else:
            print_human(metadata, arguments.dry_run)
        if not arguments.dry_run:
            create_worktree(project_root, metadata)
        return 0
    except subprocess.CalledProcessError as error:
        message = error.stderr.strip() or error.stdout.strip() or str(error)
        print(f"architect-worktree: {message}", file=sys.stderr)
        return error.returncode or 1
    except HelperError as error:
        print(f"architect-worktree: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
