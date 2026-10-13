---
type: llm
focus: { source: file, path: .architect-audits/react-audit/findings.md }
---
The report's "Act on" section lists at most five findings, and each one cites concrete files and lines from
this repository. Together they name at least four of these real problems: src/components/OrderDetail.tsx and
src/components/RefundForm.tsx import formatAmount from src/lib/payments.ts, which creates a Stripe client from
STRIPE_SECRET_KEY, so server code is bundled into 'use client' components; src/app/orders/page.tsx passes the
plain function exportCsv to the client component OrdersTable as onExport; the search effect in
src/components/OrderSearch.tsx fetches with no abort or ignore flag, so an older response can overwrite a newer
one; OrdersTable keeps selectedOrder in state and syncs it from selectedId in an effect that omits orders;
OrderSummary computes total and refundable in an effect instead of during render; RefundForm tracks one
submission with isSubmitting, isError, isSuccess, and isOverLimit booleans. Lint, forwardRef, default-export, and
suppression findings may appear under Consider but must not displace these. No act-on finding is a style
preference, a performance tip such as adding useMemo, or a problem the cited code does not have.
