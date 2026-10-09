---
type: llm
focus: { source: file, path: .architect-audits/architecture-audit/findings.md }
---
The report's "Act on" section lists at most five findings, and each one cites concrete files and lines from
this repository. Together they name at least three of these real problems: the cart and checkout import
cycle; cart state written by both the Zustand store and the Redux slice (and directly by CartView) with
different keys and shapes; two HTTP clients (axios in src/lib/http.ts and fetch in src/lib/apiClient.ts);
OrderHistory fetching in useEffect although a React Query hook exists; the frequently changed, untested
src/features/checkout/checkout.ts. No act-on finding is a style preference or a problem that does not exist
in the code.
