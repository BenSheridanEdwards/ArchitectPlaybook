---
name: pull-request-quality-contract
description: Use before completing work or opening a pull request in this repository.
trigger: /pull-request-quality-contract
---

# /pull-request-quality-contract

Use this repository-local skill before completing work or opening or updating a pull request. It keeps the review evidence concrete: why the change exists, what changed, what proof exists, which commands ran, and which checks were deliberately skipped.

## Usage

```
/pull-request-quality-contract
```

## What this skill does

1. Reads `.agents/DEFINITION_OF_DONE.md` and `.github/pull_request_template.md`.
2. Maps the changed behaviour to the smallest relevant proof: tests, command output, screenshots, video, or a technical `Not applicable` reason.
3. Checks that skipped proof names the reason, risk, and follow-up owner.
4. Writes or updates the pull request body using the repository template.

## Implementation steps

1. Read `.agents/DEFINITION_OF_DONE.md` before implementation starts. Completion criterion: the pull request scope, required proof, and verification commands are known before coding.
2. Map behaviour changes to tests. Completion criterion: every user-visible behaviour changed by the pull request has an automated behaviour or end-to-end test, or an explicit technical reason it cannot be automated.
3. Produce behavioural proof for user interface changes. Completion criterion: the pull request contains current video and screenshots from the changed branch, or states `Not applicable` with the reason.
4. Run verification on the final branch. Completion criterion: format, lint, type, unit, integration, and end-to-end checks relevant to the changed files have been run after the last code change.
5. Write the pull request body using `.github/pull_request_template.md` exactly. Completion criterion: the body contains, in order, `Why does this feature exist?`, `What changed?`, `Behavioural Proof (with video and screenshots)`, and `Verification Summary`.
6. Do not mark work complete with placeholders. Completion criterion: every proof link, screenshot path, video path, test command, failure, skipped check, and residual risk is explicit.

## Evidence Rules

- Test claims require command output.
- User interface behaviour claims require screenshots or video from the branch under review.
- End-to-end claims require named scenarios and their pass/fail result.
- Skipped proof requires a technical reason, not convenience.
- Existing unrelated failures must be separated from failures introduced by the pull request.

## What this skill explicitly does NOT do

- It does not invent proof that was not produced.
- It does not require screenshots or video for non-visual documentation, governance, or script-only changes.
- It does not replace the repository validator or continuous integration checks.
- It does not mark a pull request ready while relevant checks are still failing, queued, or running.
