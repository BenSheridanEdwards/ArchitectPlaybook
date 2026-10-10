---
type: regex
target: { source: file, path: .architect-audits/testing-audit/findings.md }
pattern: "## All checks\\n\\n\\| Layer \\| Check \\| Severity \\| Status \\| Judgement \\|\\n(?:\\| -{3} ){5}\\|\\n(?:\\|[^\\n]*\\|\\n)*?\\| [a-z-]+ \\| No focused or skipped tests committed \\| \\w+ \\| (violation) \\|"
---
