# Quality gates audit report

Quality gates are implemented for this Markdown skill repository through validator tests, a standard-library repository validator, local Git hook templates, a hook installer, and GitHub Actions.

## Snapshot

# Snapshot

- Repository: BenSheridanEdwards/ArchitectPlaybook
- Commit: `a709947335cc1129b97f13afbe5449ea48950f5a`
- Skill folders: 19
- Audit skills: 14
- Graphify output present: no
- Generated: 2026-05-21T22:53:56.102236Z


## Top recommendations
1. **Use Python gates, not fake Node gates**
   - Why it matters: ArchitectPlaybook is a Markdown skill repository, not a Node app.
   - Smallest fix: Run scripts/validate-playbook.py in hooks and continuous integration.
2. **Install local hooks after clone**
   - Why it matters: Continuous integration catches problems late; hooks catch them before commit or push.
   - Smallest fix: Run python3 scripts/install-git-hooks.py.
3. **Add pull request title enforcement later**
   - Why it matters: Commit hooks do not protect squash merge titles typed in GitHub.
   - Smallest fix: Add a small workflow step that validates the pull request title.

## Checks
### quality.validator — present

- Expectation: Repository has an executable validation command.
- Evidence: scripts/validate-playbook.py validates frontmatter, skill index, bootstrap contract, markdown links, worktree flag contract, and whitespace.
- Gap: None after this branch.
- Remediation: Run python3 scripts/validate-playbook.py locally and in continuous integration.

### quality.local-hooks — present

- Expectation: Local pre-commit, pre-push, and commit-message gates exist without requiring Node tooling.
- Evidence: scripts/git-hooks/pre-commit, pre-push, commit-msg plus scripts/install-git-hooks.py.
- Gap: Contributors must install hooks once per clone.
- Remediation: Run python3 scripts/install-git-hooks.py after cloning.

### quality.continuous-integration — present

- Expectation: Pull requests and pushes rerun validation on a clean checkout.
- Evidence: .github/workflows/validate-playbook.yml runs python3 scripts/validate-playbook.py.
- Gap: None after this branch.
- Remediation: Watch the Validate playbook workflow on the pull request.

### quality.conventional-commits — present

- Expectation: Commit subjects are machine-parseable Conventional Commits.
- Evidence: scripts/validate-commit-message.py and scripts/git-hooks/commit-msg enforce allowed types.
- Gap: GitHub Actions does not yet check pull request titles.
- Remediation: Optional follow-up: add pull request title validation.

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
