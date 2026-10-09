---
type: regex
target: { source: file, path: .architect-audits/security-audit/snapshot.md }
pattern: "(?<![\\s\\S])(?![\\s\\S]*FixtureOnlyNotARealKey)[\\s\\S]+"
---
