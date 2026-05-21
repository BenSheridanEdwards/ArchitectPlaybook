# Pre-audit setup report

Pre-audit setup is partially complete: ArchitectPlaybook skills are installed and audit reports exist, but graphify is missing so graph-based analysis could not be built.

## Snapshot

# Snapshot

- Repository: BenSheridanEdwards/ArchitectPlaybook
- Commit: `a709947335cc1129b97f13afbe5449ea48950f5a`
- Skill folders: 19
- Audit skills: 14
- Graphify output present: no
- Generated: 2026-05-21T22:53:56.102236Z


## Top recommendations
1. **Install graphify before graph-based audits**
   - Why it matters: The pre-audit setup contract depends on graphify for the shared codebase map.
   - Smallest fix: Install graphify, then rerun /pre-audit-setup from the repository root.
2. **Keep static audits explicit when graphify is unavailable**
   - Why it matters: Calling a static review a full graph audit would overstate confidence.
   - Smallest fix: Mark reports with mode=static-inspector until graphify outputs exist.

## Checks
### pre-audit.graphify-installed — missing

- Expectation: graphify exists at $HOME/.claude/skills/graphify/SKILL.md before /pre-audit-setup runs.
- Evidence: $HOME/.claude/skills/graphify/SKILL.md is absent.
- Gap: Knowledge graph generation cannot run.
- Remediation: Install graphify from its published integration instructions, then rerun /pre-audit-setup.

### pre-audit.project-hook — present

- Expectation: Project-local .claude/settings.json contains the graphify-aware PreToolUse hook.
- Evidence: .claude/settings.json exists and contains matcher Glob|Grep.
- Gap: None.
- Remediation: No action.

### pre-audit.audit-directory — present

- Expectation: .architect-audits exists for downstream audit outputs.
- Evidence: .architect-audits/pre-audit-setup was created for this report.
- Gap: None.
- Remediation: No action.
