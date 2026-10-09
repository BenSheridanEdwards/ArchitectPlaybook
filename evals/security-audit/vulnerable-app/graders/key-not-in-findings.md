---
type: regex
target: { source: file, path: .architect-audits/security-audit/findings.md }
pattern: "(?<![\\s\\S])(?![\\s\\S]*FixtureOnlyNotARealKey)[\\s\\S]+"
---
