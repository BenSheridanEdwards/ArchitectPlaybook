---
type: llm
focus: { source: file, path: .architect-audits/testing-audit/findings.md }
---
The report's "Act on" section lists at most five findings, and each one cites concrete files and lines from
this repository. Together they name at least three of these real problems: the invoice-total tests that
compare invoiceTotals with itself, assert nothing, or only assert `not.toBe(0)`; the route tests in
src/server/app.test.ts that mock the repository's own decideRefund and assert only that mocks were called
or that the mocked invoice came back; src/billing/refunds.ts, the most-changed file, whose refund window,
balance, and not-paid rules no test exercises; the committed `it.only` in src/notifications/mailer.test.ts;
invoice data access never tested against PostgreSQL. No act-on finding is a style preference or a problem
that does not exist in the code. Pass only if the report does not treat the snapshot or `toHaveClass`
tests as proof the admin list works.
