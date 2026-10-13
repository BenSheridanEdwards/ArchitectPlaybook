---
type: llm
focus: { source: file, path: .architect-audits/react-audit/findings.md }
---
Read the "Present" section of the report, and any evidence the report gives for these checks. Each of these
present verdicts must cite, as `path:line` evidence with a quoted fragment, specific code that was read and
earns it:
- "Effects clean up and ignore stale results": the `clearInterval` cleanup in
  src/components/OrderStatusPoller.tsx, or another effect's cleanup it reviewed;
- "Effect dependencies are honest": the `useEffectEvent` call in src/components/OrderViewTracker.tsx, or the
  dependency arrays of the effects it reviewed;
- "Effects do not derive or mirror state" and "Each value has one source of truth": the reviewed effects or
  state declarations, such as the `selected` value derived during render in src/components/OrdersTable.tsx or
  the totals computed during render in src/components/OrderSummary.tsx;
- "Forms submit through actions" or "One status value instead of boolean flags": `useActionState` in
  src/components/RefundForm.tsx or the RefundState union in src/app/orders/[id]/actions.ts;
- "'use client' marks small interactive leaves": a reviewed 'use client' file.
Pass only if every listed check that is present meets this. Fail if any of them rests only on a file name, a
`search:` or `files:` count, or a note.
