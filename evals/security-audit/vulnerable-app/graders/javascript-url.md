---
type: regex
target: { source: file, path: .architect-audits/security-audit/findings.md }
pattern: "## All checks\\n\\n\\| Layer \\| Check \\| Severity \\| Status \\| Judgement \\|\\n(?:\\| -{3} ){5}\\|\\n(?:\\|[^\\n]*\\|\\n)*?\\| [a-z-]+ \\| User-supplied URLs use safe schemes \\| \\w+ \\| (violation|partial) \\|"
---
