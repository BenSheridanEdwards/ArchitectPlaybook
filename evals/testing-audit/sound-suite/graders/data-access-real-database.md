---
type: regex
target: { source: file, path: .architect-audits/testing-audit/findings.md }
pattern: "## All checks\\n\\n\\| Layer \\| Check \\| Severity \\| Status \\| Judgement \\|\\n(?:\\| -{3} ){5}\\|\\n(?:\\|[^\\n]*\\|\\n)*?\\| [a-z-]+ \\| Data access tested against a real database \\| \\w+ \\| (present|partial) \\|"
---
