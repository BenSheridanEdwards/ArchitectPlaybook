# Testing audit report

Testing is implemented as validator behavior tests plus executable repository-contract validation, matching ArchitectPlaybook’s Markdown skill corpus instead of pretending it is an app framework project.

## Snapshot

# Snapshot

- Repository: BenSheridanEdwards/ArchitectPlaybook
- Commit: `a709947335cc1129b97f13afbe5449ea48950f5a`
- Skill folders: 19
- Audit skills: 14
- Graphify output present: no
- Generated: 2026-05-21T22:53:56.102236Z


## Top recommendations
1. **Treat the validator as the test suite**
   - Why it matters: The repo is a skill corpus; executable contract checks are the meaningful tests.
   - Smallest fix: Run python3 scripts/validate-playbook.py before every commit.
2. **Add fixture tests only when behavior becomes script-heavy**
   - Why it matters: The current scripts are small enough for end-to-end validation, but installer behavior may deserve fixtures later.
   - Smallest fix: Follow-up if installer logic grows beyond copy-and-validate.

## Checks
### testing.repository-contracts — present

- Expectation: Skill contracts and README index are executable tests.
- Evidence: scripts/validate-playbook.py scans all top-level SKILL.md files and README links.
- Gap: None after this branch.
- Remediation: Keep validator checks close to CLAUDE.md rules.

### testing.worktree-contract — present

- Expectation: Worktrees are a flag on each audit, not a standalone slash command.
- Evidence: Validator fails if worktree/SKILL.md exists or audit Usage omits /<audit> --worktree.
- Gap: None after this branch.
- Remediation: Leave standalone worktree helpers out unless the architecture docs change first.

### testing.bootstrap-contract — present

- Expectation: README bootstrap claim matches committed files.
- Evidence: .claude/skills/install-architect-playbook-globally/SKILL.md is committed and validator checks it.
- Gap: None after this branch.
- Remediation: Keep bootstrap installer synchronized with the root installer skill.

### testing.validator-unit-tests — present

- Expectation: Validator behavior has direct tests and CI runs them.
- Evidence: tests/test_validate_playbook.py covers positive and negative validator contracts; GitHub Actions and local hooks run unittest discovery before repository validation.
- Gap: None after this branch.
- Remediation: Run `python3 -m unittest discover -s tests -p 'test_*.py'`.

### quality.hook-installer-safety — present

- Expectation: Local hook installation does not silently destroy contributor hooks and works from linked worktrees.
- Evidence: `scripts/install-git-hooks.py` resolves hooks with `git rev-parse --git-path hooks`, refuses to overwrite different existing hooks unless `--force` is passed, and writes a `.architect-playbook-backup` copy before replacement.
- Gap: None after this branch.
- Remediation: Run `python3 scripts/install-git-hooks.py`; use `--force` only after reviewing existing hooks.
