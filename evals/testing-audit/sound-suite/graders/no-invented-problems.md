---
type: llm
focus: { source: file, path: .architect-audits/testing-audit/findings.md }
---
The repository is deliberately well tested: refund rules are tested on both sides of each boundary (the
thirtieth and thirty-first day, the exact remaining balance and one penny over, zero, never paid) with
literal expected values; invoice totals use worked examples; the repository and the refund route run
against PostgreSQL in Testcontainers; the email provider is faked at the HTTP layer with Mock Service
Worker and the tests assert the content sent; component tests query by role and use user-event, and the
admin screen loads its invoices through Mock Service Worker. The `onSelect` call assertion checks the
component's public callback, not an internal collaborator. Pass if the report raises no violation, lists
at most two act-on findings, and every act-on or consider finding points to a real, specific weakness in
the cited code rather than a preference or a misreading. Fail if it reports a problem the cited code does
not have.
