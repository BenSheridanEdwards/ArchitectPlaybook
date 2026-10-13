---
type: regex
target: { source: file, path: .architect-audits/react-audit/findings.md }
pattern: "## All checks\\n\\n\\| Layer \\| Check \\| Severity \\| Status \\| Judgement \\|\\n(?:\\| -{3} ){5}\\|\\n(?:\\|[^\\n]*\\|\\n)*?\\| [a-z-]+ \\| Exhaustive-deps suppressions are justified \\| \\w+ \\| (present) \\|"
---
