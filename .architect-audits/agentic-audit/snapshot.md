# Agentic audit snapshot

**Review type:** Dogfood static review. No Claude Code slash command generated this artifact.

**Reviewed revision:** `634380d`

## Agentic instruction surface

- Primary instruction file: `CLAUDE.md`.
- Claude Code settings: `.claude/settings.json`.
- Other instruction files detected: none in the reviewed tree.

## Static checks performed

- Read project rules in `CLAUDE.md`.
- Read project-local Claude Code settings.
- Checked settings hygiene for broad permissions, local settings exclusion, and absolute-path risk.
- Checked instruction hygiene for stale or placeholder-style references in active guidance.
