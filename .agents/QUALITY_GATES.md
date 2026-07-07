# Quality gates

The gates that protect this repository, the exact command each runs, and when it
runs. Every gate is real: the script or workflow exists and runs on a clean
checkout. Nothing here is aspirational.

## The matrix

| Gate | Command | Runs |
| --- | --- | --- |
| Playbook validator | `python3 scripts/validate-playbook.py` | Locally on demand; `pre-commit` and `pre-push` hooks; CI `validate` job on push and pull request. |
| Validator unit tests | `python3 -m unittest discover -s tests -p 'test_*.py'` | Locally on demand; `pre-commit` and `pre-push` hooks; CI `validate` job. |
| Commit-message check | `python3 scripts/validate-commit-message.py <file>` | `commit-msg` hook on every commit. |
| Pull-request contract | `python3 scripts/validate_pr_body.py` (title and body via `PR_TITLE` / `PR_BODY`) | CI `pr-contract` job on pull-request `opened`, `edited`, `synchronize`, `reopened`. |
| No-verify block | `python3 scripts/block-no-verify.py` (PreToolUse hook) | Every Bash tool call in a Claude Code session; denies `git commit`/`git push --no-verify`. |

## Local hooks

Run `python3 scripts/install-git-hooks.py` once per clone. It installs
`pre-commit`, `pre-push`, and `commit-msg` into the checkout's hooks directory.
Re-run with `--force` to replace conflicting hooks (the existing hook is backed
up first). Until it is run, the hooks do not fire — filesystem presence of the
templates under `scripts/git-hooks/` is not the same as an installed hook.

- `pre-commit` and `pre-push` both run the unit tests and then the validator.
- `commit-msg` runs the Conventional Commit check against the subject line.

## Continuous integration

`.github/workflows/validate-playbook.yml` defines two jobs:

- `validate` — runs on push and pull request. Runs the unit tests, then the
  playbook validator.
- `pr-contract` — runs only on pull-request events. It passes the pull-request
  title and body to `scripts/validate_pr_body.py` through the environment, using
  `toJSON(...)` so untrusted text is never interpolated into the workflow
  script. The check fails when the title is not a Conventional Commit subject,
  when a required template section is missing or empty, or when the Behavioural
  Proof section neither embeds an inline image nor states `Not applicable`.

## The no-bypass rule

`--no-verify` is forbidden. Weakening the validator, the tests, or any hook to
turn a red gate green is a named violation. When a gate fails, fix the cause. The
PreToolUse `block-no-verify` hook enforces this inside a Claude Code session by
denying any `git commit` or `git push` that carries `--no-verify`.
