# Pull-request quality

The contract every pull request in this repository meets before it is opened,
updated, or marked ready. It restates the shared rules and points at the
authoritative documents rather than duplicating them.

## Title

The title is a Conventional Commit subject: `type(scope): summary` or
`type: summary`. Agent, tool, author, or source prefixes such as `[codex]`,
`[claude]`, or `[agent]` are not allowed. The CI `pr-contract` job enforces this.

## Body

The body uses [`../.github/pull_request_template.md`](../.github/pull_request_template.md)
and keeps these four sections, in order:

1. `Why does this feature exist?`
2. `What changed?`
3. `Behavioural Proof (with video and screenshots)`
4. `Verification Summary`

Do not overwrite the template's prefilled headings. Fill each section with real
content — the `pr-contract` job fails a section that still holds only the empty
template placeholder.

## Behavioural proof

- User-visible behaviour changes include screenshots from the branch under
  review, embedded inline with `![alt](...png?raw=1)`.
- When no rendered or behavioural proof applies, the section states
  `Not applicable` with the technical reason. This repository is a Markdown and
  Python skill library with no rendered UI, so most changes here are proved by
  validator and unit-test output rather than screenshots.
- The `pr-contract` job requires the Behavioural Proof section to contain either
  an inline image (`![`) or the string `Not applicable`.

## Verification

List the commands run and their pass or fail results. For this repository that
means at least:

```bash
python3 scripts/validate-playbook.py
python3 -m unittest discover -s tests -p 'test_*.py'
```

Separate any pre-existing unrelated failures from failures the change
introduces, and give a reason, risk, and follow-up owner for any skipped check.

## Definition of done

A pull request is done only when it satisfies
[DEFINITION_OF_DONE.md](DEFINITION_OF_DONE.md): the changed behaviour is verified
by deterministic evidence, the body contains concrete evidence rather than
placeholders, and every required section is present and filled. The gate matrix
that produces that evidence lives in [QUALITY_GATES.md](QUALITY_GATES.md).
