# Conventions

The conventions the playbook validator enforces, plus the ADR-backed shape every
audit follows. These are checked by `python3 scripts/validate-playbook.py`; a
change that breaks one of them fails the gate.

## Commits

- Every commit uses Conventional Commits: `type(scope): summary` or
  `type: summary`. Subjects are imperative present tense.
- Allowed types: `feat`, `fix`, `chore`, `docs`, `refactor`, `test`, `perf`,
  `build`, `ci`. Enforced by `scripts/validate-commit-message.py` via the
  `commit-msg` hook.
- Stage files explicitly. Do not use `git add -A` or `git add .` in committed
  examples or recommendations.

## Naming and language

- No abbreviations in prose, identifiers, headings, or triggers. Spell every
  word out (Documentation, not docs; Performance, not perf; Repository, not
  repo). Canonical ecosystem filenames such as `.gitignore`, `tsconfig.json`,
  and `package.json` keep their standard form.
- A skill folder name matches its slash-command trigger. The frontmatter
  `trigger` value equals `/<folder-name>` and `name` equals `<folder-name>`.

## Skill frontmatter and body

- Frontmatter keys start in this order: `name`, `description`, `trigger`. The
  `description` is a single line.
- A non-stub skill body includes `## Usage`, `## What this skill does`,
  `## Implementation steps`, and `## What this skill explicitly does NOT do`.
- An audit's `## Usage` documents `--worktree` as a flag on the audit command
  and never documents the internal `--target` flag. The body references the four
  findings files: `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`.
- A stub is the only exception: frontmatter, a placeholder heading, and a
  `**Status:** stub` notice.

## Four-layer baseline (ADR 0001)

Every audit uses the same baseline shape, recorded in
[../docs/decisions/0001-four-layer-baseline-with-layer-zero-snapshot.md](../docs/decisions/0001-four-layer-baseline-with-layer-zero-snapshot.md):

- Layer 0 — Diagnostic snapshot: informational only, always written, never
  graded.
- Layers 1 through 4 — domain concerns, each grading checks with the shared
  status taxonomy: `present`, `partial`, `missing`, `violation`.

The lifecycle-organised quality-gates audit is the one deliberate variant: it
groups by pre-commit, pre-push, and continuous-integration stages instead of the
four numbered layers.

## checks.json

An audit may ship a `checks.json` beside its `SKILL.md`. It is a machine-readable
inventory of the same checks the body describes — never a replacement for the
human-readable body. The validator requires:

- `skillName` equal to the folder name and `humanCanonicalSource` of `SKILL.md`.
- A `statusTaxonomy` defining `present`, `partial`, `missing`, `violation`.
- Each check with a non-empty `checkId` (prefixed `<folder-name>.`), `layer`,
  `title`, `expectation`, and `violationSignal`.
- Every `layer` matching a layer or stage heading in the body, and every
  `allowedStatuses` value drawn from the status taxonomy.

Keep `checks.json` aligned with `SKILL.md` whenever a check is added, removed,
renamed, or moved between layers.

## Links and whitespace

Internal Markdown links must resolve to existing files and anchors, and no
tracked Markdown, JSON, YAML, or Python file carries trailing whitespace. Both
are enforced by the validator.
