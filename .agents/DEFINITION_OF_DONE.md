# Definition of Done

A completed pull request must contain the items below. If an item does not apply, the pull request must state `Not applicable` and give the technical reason.

## Required Pull Request Body

The pull request body must use `.github/pull_request_template.md` and preserve these sections in this order:

1. `Why does this feature exist?`
2. `What changed?`
3. `Behavioural Proof (with video and screenshots)`
4. `Verification Summary`

## Scope and Implementation

- The reason for the change is stated in user, product, or technical terms.
- The changed files and behaviour are summarized precisely.
- The implementation avoids unrelated refactors, formatting churn, and hidden scope expansion.
- New configuration, migrations, permissions, dependencies, or public API changes are called out.

## Behavioural Proof

- User interface behaviour changes include screenshots from the branch under review.
- Flows with motion, timing, cursor behaviour, audio, or multi-step interaction include video when practical.
- Screenshots and videos must show the changed behaviour, not a generic happy path.
- Behaviour and end-to-end tests map to the user-visible behaviour changed by the pull request.
- Missing visual proof or end-to-end coverage is allowed only with a technical reason and a stated replacement verification method.

## Verification

- Format, lint, typecheck, unit, integration, and end-to-end checks relevant to the changed files are run after the final code change.
- Test command names and pass/fail results are listed in the pull request.
- Existing unrelated failures are separated from failures introduced by the pull request.
- Any skipped check includes the reason, risk, and owner for follow-up.

## Review Readiness

- Documentation is updated when behavior, setup, or operation changes.
- Security, privacy, data retention, accessibility, and performance effects are considered when relevant.
- The pull request does not contain secrets, local-only paths, generated junk, or unrelated artifacts.
- The branch is ready for review only when the pull request body contains concrete evidence, not placeholders.
