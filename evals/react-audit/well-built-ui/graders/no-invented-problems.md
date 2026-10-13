---
type: llm
focus: { source: file, path: .architect-audits/react-audit/findings.md }
---
The repository is deliberately well built: the React Compiler is on and eslint-plugin-react-hooks' recommended
configuration applies to src; the orders search is a GET form whose only state is the URL, and the page queries on the server; OrdersTable
derives the selected order during render from selectedId; OrderSummary is a Server Component that computes
totals during render; OrderStatusPoller clears its interval in the effect cleanup; OrderViewTracker reads the
plan through useEffectEvent, which is not a dependency by design; RefundForm uses useActionState and
useFormStatus with one status union returned by a Server Function; server code imports server-only and is
reached from client components only through 'use server' modules; the only function passed to a client
component is the Server Function exportOrders; components take ref as a prop and export by name. Pass if the
report raises no violation, lists at most two act-on findings, and every act-on or consider finding points to a
real, specific weakness in the cited code rather than a preference, a memoization tip, or a misreading. Fail if
it reports a problem the cited code does not have. The refund reservation is one conditional UPDATE and the
total comes only from the order lines; data-layer details such as SQL are outside this audit and are not
React findings.
