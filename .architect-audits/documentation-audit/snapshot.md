# Documentation audit snapshot

**Review type:** Dogfood static review. No Claude Code slash command generated this artifact.

**Reviewed revision:** `634380d`

## Project documentation surface

- Primary entry point: `README.md`.
- Architecture overview: `ARCHITECTURE.md`.
- Contributor guide: `CONTRIBUTING.md`.
- License: `LICENSE`.
- Skill documentation: one `SKILL.md` file per skill directory.
- Committed documentation-audit example directory: `.architect-audits/documentation-audit/` added by this dogfood review.

## Static checks performed

- Read Markdown files and local Markdown links.
- Checked claims about committed examples against the file tree.
- Checked decision-record references against the file tree.
- Checked wording for claims that would be false on `main` when no decision-record directory is present.
