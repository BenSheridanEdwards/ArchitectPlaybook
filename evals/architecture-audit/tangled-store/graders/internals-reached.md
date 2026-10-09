---
type: regex
target: { source: file, path: .architect-audits/architecture-audit/findings.md }
pattern: "## All checks\\n\\n\\| Layer \\| Check \\| Severity \\| Status \\| Judgement \\|\\n(?:\\| -{3} ){5}\\|\\n(?:\\|[^\\n]*\\|\\n)*?\\| [a-z-]+ \\| Module internals are unreachable \\| \\w+ \\| (violation|partial|missing) \\|"
---
