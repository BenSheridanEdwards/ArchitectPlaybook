# TODO

Honest, current follow-ups. Each item is real work that is not yet done; none of
it is claimed as complete elsewhere.

## Dogfood the remaining audits against this repository

Only four dogfood reports are committed under `.architect-audits/`
(`pre-audit-setup`, `quality-gates-audit`, `testing-audit`, `architecture-audit`),
so 4 of the 14 audits have been run against the playbook itself. Run the other
ten against this repository and commit their reports, including honest red and
amber grades where the playbook falls short of its own baselines:

- [ ] `/agentic-audit`
- [ ] `/security-audit`
- [ ] `/dependency-audit`
- [ ] `/documentation-audit`
- [ ] `/linting-audit`
- [ ] `/typescript-audit`
- [ ] `/react-audit`
- [ ] `/performance-audit`
- [ ] `/accessibility-audit`
- [ ] `/error-handling-audit`
- [ ] `/bundle-build-audit`

Each report writes `findings.md`, `findings.json`, `snapshot.md`, and
`metadata.json` so downstream sessions consume the same contract they expect from
target projects. Do not sand off red or amber grades — the value is in seeing
where the playbook does not yet meet its own bar.

## Auto-install the git hooks

Today a contributor must run `python3 scripts/install-git-hooks.py` once per
clone, and until they do, the local gates do not fire. Consider adopting
`git config core.hooksPath scripts/git-hooks` (with the hook scripts made
executable in the repository) so a fresh clone gets the `pre-commit`, `pre-push`,
and `commit-msg` gates without a manual install step. Weigh this against the
current installer's ability to back up a contributor's pre-existing hooks before
switching.
