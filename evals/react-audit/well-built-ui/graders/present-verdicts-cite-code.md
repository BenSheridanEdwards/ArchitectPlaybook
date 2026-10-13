---
type: llm
focus: { source: file, path: .architect-audits/react-audit/findings.md }
---
Read the "Present" section of the report, and any evidence the report gives for these checks. Pass only if
each of these present verdicts cites, as `path:line` evidence or a quoted fragment, the specific code that
earns it, not just a file name or a whole-repository statement:
"Effects clean up and ignore stale results" cites the `clearInterval` cleanup in
src/components/OrderStatusPoller.tsx; "Effect dependencies are honest" cites the `useEffectEvent` call in
src/components/OrderViewTracker.tsx; "Effects do not derive or mirror state" or "Each value has one source of
truth" cites the `selected` value derived during render in src/components/OrdersTable.tsx (or the totals
computed during render in src/components/OrderSummary.tsx); "Forms submit through actions" or "One status
value instead of boolean flags" cites `useActionState` in src/components/RefundForm.tsx or the RefundState
union in src/app/orders/[id]/actions.ts. Fail if any of these checks is present with only a generic file
citation, a `files:` count, or a note.
