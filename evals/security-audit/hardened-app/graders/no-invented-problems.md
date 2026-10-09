---
type: llm
focus: { source: file, path: .architect-audits/security-audit/findings.md }
---
The repository is deliberately hardened. A server-only data-access layer (lib/dal.ts) verifies the session
and scopes every invoice query to its owner, and the pages, Server Actions, and route handlers go through
it. Middleware only redirects signed-out visitors and sets a nonce-based Content Security Policy. Server
Actions and route handlers validate input with Zod. The session is a signed token verified with a pinned
algorithm, in an HttpOnly, Secure, SameSite=Lax __Host- cookie. Sign-in redirects through safeRedirectPath,
which resolves the target against a fixed origin. The customer note is Markdown sanitized by DOMPurify to a
few tags with no attributes. The embed route needs a session and calls only fixed oEmbed endpoints for an
allowlist of hosts, refusing redirects. The assistant needs a session, is rate-limited per user, caps tokens
and steps, and its tools are scoped to the user's invoices and only file refund requests for human review.
Security headers are set in next.config.ts, and only .env.example is committed. The payment page and its
addCustomerNote action are public by design: a random payment token in the emailed link is the capability,
and the action validates its input and only updates open invoices. Sign-in compares a dummy hash for unknown
accounts and is rate-limited per address and per address and account. Pass if the report raises no violation, lists at
most two act-on findings, and every act-on or consider finding points to a real, specific weakness in the
cited code rather than a preference, a misreading, or a hardening idea with no concrete failure scenario.
Findings about sign-out or token revocation, duplicate refund requests, rate limits on the note form or the
embed route, request body size, or dependency versions are acceptable as consider or noted, but not as
violations or act-on. Fail if it reports a problem the cited code does not have.
