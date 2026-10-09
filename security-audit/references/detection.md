# Security audit: detection notes

How to trace each check, what counts as evidence, and when a suspicion is not
yet a finding.

## The bar for a finding

A `violation` is a supported finding. It names:

1. **An entry point** an attacker controls: a Server Action argument, a route
   parameter, a query string, a form field, a request body, a header, a
   `postMessage`, stored content another user wrote, or text a model reads.
2. **A path across a boundary:** anonymous to authenticated, one user to
   another, browser to server, server to internal network, or user text to a
   model tool. Cite each hop as `path:line`.
3. **An impact:** what the attacker reads, changes, or runs.
4. **A challenge that failed.** You looked for the control that would stop it
   and it is absent or bypassable. Name where you looked.

Grade everything else honestly:

| Situation | Record |
| --- | --- |
| The full trace holds | `violation`, tier `direct` or `supported` |
| A hardening control is missing, with no concrete path that it would have stopped | `partial`, judged `consider` |
| A required structure is absent, such as no ignore rule at all | `missing` |
| A control exists and you cited it | `present` |
| You suspect a path but a hop is outside the code you can read | `hypothesis`, and grade the check on what you verified |
| The surface does not exist, such as no hand-written OAuth flow | `not-applicable` |

A disproved candidate is coverage evidence. Record the check `present` and cite
the control that stopped it, so readers can see what was examined. There is no
list of finding types to skip. A candidate is dropped only because its trace
fails.

## Severity, read for security

Architecture Decision Record 0004 rates a check by the consequence of the
problem it detects. For security:

- **critical:** directly exploitable. An attacker exploits it with nothing more
  than a request: an unauthenticated mutation, cross-user access, injection,
  server-side request forgery, script injection from user content, or an
  exposed secret.
- **high:** exploitable together with one more flaw or condition. Examples are
  a token in web storage (needs a script injection) and an open redirect
  (needs a phishing victim). So are middleware-only authorization (needs a
  matcher gap or a bypass) and `eval` (needs attacker text to reach it).
  Sign-in without attempt limits is high too: guessing succeeds only against
  a weak or reused password.
- **medium:** a missing defence in depth whose absence causes nothing by
  itself, such as a Content Security Policy or framing protection.
- **low:** hygiene, such as the baseline headers.

A check that finds a control missing is rated like the defect the control
prevents (Architecture Decision Record 0004). That is why "Local environment
files are ignored by Git" is critical, like "No secrets in source".

Severity belongs to the check. A partial result on a critical check is still
weighted as critical, so grade partial only when the gap is real.

## Tracing entry points

Start from the collector's `serverActions`, `routeHandlers`,
`pageEntryPoints`, `otherServerRoutes`, `webhookSites`, and `middleware` lists.
For each entry point:

1. **Who can call it?**
   - Server Actions are POST endpoints. Anyone can call one with any
     arguments, because action identifiers ship in the client bundle.
   - Values passed with `.bind(null, id)` (`boundActionArguments`) travel
     through the client and are attacker-controlled too, unlike variables an
     inline action closes over. `<form action={remove.bind(null, invoice.id)}>`
     looks safe and is not.
   - Route handlers and API routes answer any HTTP client. Loaders and Remix or
     React Router actions are the same.
   - Pages, layouts, and `generateMetadata` that load data from `params` or
     `searchParams` are reads anyone can request by URL. They are where
     cross-user reads most often sit.
   - Treat every argument, `params`, `searchParams`, header, cookie, and body
     field as attacker-controlled.
2. **What protects it?** Read the function body, then every data-access
   function it calls. A check in a layout does not protect a page's data or a
   Server Action, because layouts do not re-run on every request and actions do
   not pass through them. A check in middleware is a first pass only (see
   below).
3. **Where does its input go?** Follow each value to a query, a mutation, a
   server-side request, a redirect, a response, a log line, a prompt, or HTML.
4. **What comes back?** Check that responses and client component props
   carry only the fields the caller may see, not whole records with password
   hashes, internal flags, other users' data, or secrets.

Then work backwards from the client sinks the collector found:
`dangerouslySetInnerHTML`, `otherHtmlSinks`, `dynamicRedirects`,
`browserStorageAuthKeys`, and `messageListeners`. Find where each value comes
from. If a value comes from a database, find who can write it; a field another
user writes is attacker-controlled.

Read a file before citing it. Quote only the fragment that carries the signal.

## Layer 1: access control and sessions

- **Server entry points authenticate the caller.**
  - Look for a session lookup in the entry point or in a data-access function
    it always calls.
  - A token must be verified, not just decoded: `jwt.decode` and an unverified
    `JSON.parse(atob(...))` are not authentication.
  - Entry points meant to be public (sign-in, sign-up, public pages,
    capability links with unguessable tokens) are present when they expose
    nothing beyond their purpose. Webhooks are graded by their own check.
  - A page or layout that loads non-public data needs the same session check
    as an action, in the page or in the data-access function it calls.
- **Each request is authorized for the records it touches.** Every
  `findUnique`, `update`, `delete`, or SQL statement keyed by a request
  identifier needs an ownership, tenant, or role condition, or a check on the
  loaded record before use. Authentication alone is not authorization. Cite the
  query and the identifier's source. A capability link, whose unguessable
  random token is the authorization, counts as present when the endpoint reads
  or changes nothing beyond its purpose. A sequential or guessable identifier,
  or the record's own primary key reused in other routes, is not a capability.
  - Caches are authorization too. Look at `cachingSites`:
    - `unstable_cache` or `'use cache'` whose key omits the user or tenant
      while the function reads per-user data;
    - `fetch` with `force-cache` or `revalidate` that sends an `Authorization`
      header or cookie;
    - `export const dynamic = 'force-static'`, or `revalidate`, on a route that
      reads the session;
    - `Cache-Control: public` or `s-maxage` on an authenticated response.

    Each of these can serve one user's data to another: a violation.
- **Authorization does not rely on middleware alone.**
  - Read `config.matcher`. Routes outside it, including API routes the matcher
    excludes, get no protection at all.
  - Self-hosted Next.js before 12.3.5, 13.5.9, 14.2.25, and 15.2.3 lets any
    request skip middleware with the `x-middleware-subrequest` header
    (CVE-2025-29927). When the version is vulnerable, middleware-only
    authorization is directly exploitable; say so in the gap, and leave the
    version itself to `/dependency-audit`.
  - Next.js 16 renames `middleware.ts` to `proxy.ts`; the rule is the same.
- **State changes resist cross-site requests.**
  - Server Actions compare the `Origin` header with the host. Look for
    `serverActions.allowedOrigins` widened to other sites.
  - Route handlers have no such check. A cookie-authenticated POST, PUT, PATCH,
    or DELETE is protected by SameSite=Lax or Strict cookies, an origin check,
    or a token. SameSite does not separate sibling subdomains.
  - A GET handler that changes state is a violation whenever a cookie
    authenticates it.
- **Cross-origin access is limited to trusted origins.** Start from
  `corsSites`. `Access-Control-Allow-Origin` set from `request.headers.get('origin')`,
  `origin: true` in the `cors` package, or a check with `endsWith` or an
  unanchored regular expression, combined with `Access-Control-Allow-Credentials:
  true`, lets any site read the signed-in user's responses: a violation. A
  wildcard without credentials on public data is present.
- **Webhooks verify their signature before acting.** Start from
  `webhookSites` and any route with `webhook` in its path.
  - Present: the provider's own verifier (`stripe.webhooks.constructEvent`,
    `svix`, `@octokit/webhooks`), given the raw body from `request.text()`.
  - A violation: acting before verification, verifying `JSON.stringify` of a
    parsed body, comparing signatures with `===` instead of
    `crypto.timingSafeEqual`, or ignoring the signed timestamp so an old
    event can be replayed.
- **Hand-written sign-in flows.** Not applicable when Auth.js, Clerk, Auth0, or
  another maintained library builds the authorization request. Otherwise,
  find the request and the callback, and check that `state` (and `nonce` for
  OpenID Connect) is generated per request, stored, and compared; and that the
  code verifier is sent.
- **Session tokens stay out of web storage.** Start from
  `browserStorageAuthKeys`, then confirm what the value is. A key named `token`
  that holds a feature flag is not a finding.
- **Session cookies.** Read every `cookies().set`, `Set-Cookie`, and cookie
  library call that carries a session or token. Library defaults count when
  documented: Auth.js sets HttpOnly, SameSite=Lax, and Secure on HTTPS. A bare
  `cookies().set(name, value)` sets none of the three.
- **Strong cryptography.** Look at token generation (`Math.random`,
  `Date.now`, sequential identifiers), password hashing (`createHash('md5')`,
  `'sha1'`, single unsalted `'sha256'`), and verifiers (`jwt.verify` without
  `algorithms`, `alg: none`). MD5 used for cache keys or ETags is not a
  finding.
- **Sign-in and recovery endpoints are rate-limited.** Look at every
  hand-written sign-in, one-time-code, magic-link, password-reset, and sign-up
  entry point, and at `rateLimitLibraries`.
  - Present: limits per account and per client in a shared store such as
    Redis, or a hosted provider (Clerk, Auth0, Supabase) that enforces them.
  - A limit kept in process memory on serverless functions limits nothing.
  - A six-digit code with no attempt limit is guessable outright.
  - A per-account limit alone lets anyone lock a user out; that is a
    legitimate `consider` finding, not a violation.

## Layer 2: untrusted input and output

- **Server entry points validate their input.** Look for a schema parse (Zod,
  Valibot, and similar, or `next-safe-action`) on every argument before use.
  TypeScript types are not validation; nothing checks them at run time.
  Spreading `Object.fromEntries(formData)` or a whole JSON body into a
  `create` or `update` is mass assignment: a violation when it can set a field
  such as `role`, `ownerId`, or `price`.
- **Queries and commands are parameterised.** Prisma `$queryRaw` with a tagged
  template is parameterised; `$queryRawUnsafe` and `$executeRawUnsafe` with
  interpolation are not. Drizzle `sql` tagged templates are safe and
  `sql.raw` is not. Also check `child_process.exec` and `execSync` with
  interpolated strings, and `fs` paths built from request data without
  `path.resolve` and a prefix check.
- **Server requests do not follow user-supplied URLs.**
  - Start from `requestsWithDynamicUrl`: requests with a non-literal URL in
    files not marked `'use client'`. Some of them still run in the browser.
    Also check image proxies, PDF or screenshot renderers, webhook senders,
    and link previews.
  - A safe design parses the URL, requires `https:`, compares the hostname
    with an allowlist, and sets `redirect: 'error'` or re-checks each
    redirect.
  - A denylist of private addresses is partial at best: DNS rebinding, IPv6,
    and decimal or octal forms defeat it.
  - Next.js image `remotePatterns` with a wildcard hostname lets anyone use
    the optimizer as a proxy; grade it partial unless internal hosts are
    reachable.
- **dangerouslySetInnerHTML renders only sanitized HTML.** Start from the
  collector's sites. Constant strings and JSON-LD built with `JSON.stringify`
  of server data that escapes `<` are present. Content from a database,
  request, CMS, or model needs a sanitizer such as DOMPurify on the path. A
  sanitizer with a permissive configuration (allowing `script`, event
  handlers, or `style` with URLs) does not count.
- **Other HTML sinks.** The same test for `innerHTML`, `outerHTML`,
  `insertAdjacentHTML`, `document.write`, `createContextualFragment`,
  `rehype-raw`, `allowDangerousHtml`, and Markdown renderers with `html: true`.
- **Uploaded files cannot run or escape their storage.** Start from
  `uploadSites`. Not applicable when the application accepts no files.
  - A violation: an uploaded SVG or HTML file served inline from the
    application's own origin (stored script injection), or a client-supplied
    file name joined into a storage path (path traversal).
  - Also look for a trusted client `Content-Type`, a missing size limit, and a
    pre-signed URL not scoped to one key, a size, and a short expiry.
  - Present: content and size checked on the server, generated names, and files
    served from a separate origin or bucket, or with
    `Content-Disposition: attachment` and `nosniff`.
- **User-supplied URLs use safe schemes.** React 19 refuses to render
  `javascript:` URLs in `href` and `src`; earlier versions only warn. Nothing
  protects `window.open`, `location`, or URLs written outside React. A check
  that parses with `new URL` and compares `protocol` with an allowlist is
  present. A check on the raw string is present only if it is anchored, case
  insensitive, and ignores leading whitespace, because browsers do.
- **Redirects go only to allowed destinations.**
  - Start from `dynamicRedirects`, and also check sign-in `callbackUrl` and
    `returnTo` parameters.
  - Next.js `redirect()` follows absolute URLs.
  - A safe check resolves the target against a fixed origin with `new URL`, compares the
    origin, and refuses results that begin with `//` or `/\`.
  - `startsWith('/')` alone is a violation, because `//attacker.example` and
    `/\attacker.example` pass it.
- **No eval or new Function (tool).** The collector lists every call in
  shipped code and marks the result `consider`. It ignores strings, comments,
  regular expressions, prose in JSX text, and method declarations named
  `eval`, but still reads code inside `${...}` template interpolations and
  after a `>` comparison. Trace each site: if request, storage, or message
  data can reach it, re-record the check with `--judgement act-on` and the
  trace. Calls in tests and minified vendor files are excluded. A source file
  too large to read, or unreadable, makes a clean result degraded rather than
  silently clean.
- **Message listeners.** `event.origin === 'https://exact.example'` or a `Set`
  of exact origins is present. `includes`, `endsWith`, `indexOf`, or a
  regular expression without anchors is a violation, because
  `https://exact.example.attacker.net` passes.

## Layer 3: secrets, data, and browser hardening

- **No secrets in source (tool).**
  - The collector scans every tracked file, in any folder and of any size, for
    high-confidence formats, keystore and private-key files, and secret-named
    variables (including `*_KEY` and `*_PAT`) with real values in committed
    environment files.
  - Values are parsed as dotenv does: a quoted value ends at its closing
    quote, an unquoted one at ` #`, and a single-quoted value is literal. A
    value is exempt as a reference only when it is entirely `${NAME}` or
    `$NAME` outside single quotes. Only a connection URL whose host is local
    is exempt, and a connection password is a placeholder only when the whole
    password is a template expression such as `${DB_PASSWORD}`, `<password>`,
    or `****`. A double- or single-quoted value can span lines; it is cited
    at its first line.
  - It skips whole-value placeholders (`changeme`, `your-key-here`, `<token>`,
    `${VAR}`, one character repeated such as `xxxx` or `0000`), the documented AWS example keys, local
    database URLs, and private-key headers with no key body after them, as in
    documentation templates. A value that merely contains a word such as
    `Example` is still a secret.
  - Citations quote only a fixed prefix such as `sk_live_` or a variable name
    before `<REDACTED>`. The redaction guarantee below says what else is
    withheld. When every hit is under a test path, the result is judged
    `consider`.
  - A test-only private key or a revoked credential may be dismissed with a
    reason, but the key is still exposed: say whether it was ever live.
  - Google API keys are often public by design (Maps, Firebase) and are not in
    the high-confidence list. Judge them under the public-variable check.
  - With `--with-scan`, history findings are reported as counts, rule names,
    and commit identifiers, never file names. A secret only in history still
    needs rotating.
- **Local environment files are ignored by Git (tool).** Critical, because a
  missing control is rated like the defect it prevents.
  - The collector asks Git which rule ignores `.env`, `.env.local`, the
    development, test, production, and staging `.local` variants, a made-up
    `.env.any-mode.local` that only a general pattern covers, and every
    untracked environment file it finds on disk. It asks in the root, every
    folder with a `package.json` or a Next.js or Vite configuration, and
    every folder that holds an environment file.
  - A tracked `.gitignore` line whose `!` exception can match a local
    environment file, such as `!.env.staging.local` or `!**/.env.local`, is
    itself the finding (partial, citing the line), whether or not a matching
    file exists yet, unless a later rule re-ignores it: the collector asks Git,
    which applies the last matching pattern. An exception for a template such
    as `!.env.example` is not a finding.
  - Only a rule in a `.gitignore` the repository tracks counts. An untracked
    `.gitignore`, `.git/info/exclude`, or a global ignore file protects one
    person, so that coverage is missing or partial.
  - A committed `.env*.local` file is a violation whatever the ignore rules
    say, because an ignore rule does not untrack a file. One whose path cannot
    be printed is still counted.
  - Committed defaults such as `.env` or `.env.production` are allowed (Next.js
    documents them); "No secrets in source" checks their values.
- **Public environment variables hold no secrets.** Start from
  `publicEnvironmentNamesThatLookSecret`, then decide by what the value is:
  - publishable keys, analytics identifiers, and Supabase anonymous keys are
    public by design;
  - service-role keys, secret keys, signing secrets, and private tokens are
    not.

  Check `next.config` `env`, which inlines into the client bundle, too.
- **Server-only data never reaches client code.**
  - Follow each secret-reading module's importers. A secret that reaches a
    client component prop, a Server Action return value, or a JSON response
    is a violation.
  - The same holds for private fields: a whole user or account record passed
    to a client component carries its password hash, internal flags, or other
    users' email addresses into the page. Present: data shaped with Prisma
    `select`, a transfer object, or React's
    `experimental_taintObjectReference` and `experimental_taintUniqueValue`.
  - A module that reads secrets without `import 'server-only'` but has no
    client importer is partial: nothing stops the next import.
- **Logs record no secrets or tokens.** Look at `console.*`, logger, and error
  reporter calls in authentication, payment, and request middleware code.
  Logging a whole `headers`, `cookies()`, or request object counts.
  Redaction in the error reporter's configuration belongs to
  `/error-handling-audit`.
- **Personal data stays out of URLs and analytics.** Soft check. Look at link
  builders and `router.push` with email, phone, or token parameters, and
  analytics `track` or `capture` calls with personal fields.
- **Content Security Policy.** Find where headers are set: the collector's
  `securityHeaderSources` lists next.config, middleware, and hosting
  configuration. A nonce-based policy set in middleware is the Next.js
  pattern. `unsafe-inline` in `script-src` is ignored by browsers when a nonce
  or hash is also present, so judge the effective policy. Without a concrete
  injection path this is a hardening gap: partial or missing.
- **Framing.** `frame-ancestors 'none'` or `'self'`, or `X-Frame-Options`.
  Missing framing protection on pages with one-click actions is the concrete
  scenario that makes it a violation.
- **HTTPS is enforced.** Vercel, Netlify, and similar platforms redirect to
  HTTPS by default. Record the platform default you relied on in the snapshot,
  citing the deployment configuration file. `http://` URLs for
  `localhost` are fine.
- **Baseline headers.** Low severity; a missing header is partial unless it
  enables a concrete attack.
- **Third-party content.** Pinned CDN scripts need `integrity`. Vendor tag
  scripts that change by design cannot use it; record them, and check that the
  vendor is one the team relies on deliberately. Embedded third-party frames
  need `sandbox` unless they must run with full permissions, such as a payment
  form.

## Layer 4: large-language-model features

The collector lists AI SDKs (including Bedrock, Azure OpenAI, Vertex AI,
Mastra, and LangChain) and model API references, including any
`/chat/completions` path. When it finds none, `aiDetection` in the snapshot
says so. Search for a model call yourself (an HTTP call to a model endpoint,
a self-hosted model) before recording these checks not applicable. Otherwise,
start from every `tool(` definition and every model call.

- **Model tool calls act only with the user's permissions.** Treat each tool
  as an entry point whose arguments come from the attacker, because prompt
  injection decides them. This holds even when the user is honest: retrieved
  documents, emails, invoices, or web pages can carry instructions. A tool that
  takes a record identifier needs the same ownership check as the endpoint it
  mirrors. Refunds, deletions, payments, and messages to third parties need
  explicit user confirmation outside the model. A tool that only files a
  request for a person to review, scoped to the user's own records, already
  has that confirmation and needs no in-chat step. AI SDK 6 marks a tool
  that needs approval with `needsApproval`, which counts as that
  confirmation.
- **Model output is never rendered as raw HTML.** Follow the streamed or
  returned text to the component that renders it.
- **Prompts carry no secrets or other users' data.** Critical, because
  anything in a prompt is one injected question away from disclosure. Read
  system prompts and retrieval queries. A retrieval query not filtered by the
  current user or tenant leaks other users' data to anyone who asks.
- **Model usage is bounded per user.** Look for a session check, a per-user
  rate limiter, an output token cap, a step or tool-call limit, and an input
  size limit. The names depend on the SDK version:
  - AI SDK 4: `maxTokens` and `maxSteps`;
  - AI SDK 5 and later: `maxOutputTokens` and `stopWhen: stepCountIs(n)`;
  - provider SDKs: `max_tokens` or `max_completion_tokens`.

  An in-memory limiter on serverless functions limits nothing; grade it
  partial.

## Redaction guarantee

The collector never prints a secret it has detected:

- Every value the scan detects is redacted from all output, whatever check or
  snapshot fact would print it: a key, a connection password, a private-key
  body, or a secret-named environment value. A value of four characters or
  more is redacted wherever it appears, and one of eight or more also by
  every eight-character piece of it. A value of three characters or fewer
  would match inside unrelated text, so it is redacted only where it stands
  as a whole path component or a whole token.
- Text that matches a credential format, or is shaped like a generated
  credential (such as a long random or hexadecimal run), is withheld too.
- File names from Git history are never printed; history is reported by
  counts, rule names, and commit identifiers only.
- Redaction rewrites only text drawn from the repository: evidence, gaps,
  reasons, and snapshot values. Check identifiers, dictionary keys, and fixed
  fields such as status and evidence tier are never rewritten, so a secret
  that happens to equal `direct` cannot corrupt the result.

The limit: a secret that was never detected as a value cannot be recognised.
A file name that is itself a password, but is not credential-shaped and
matches no detected value, is shown as it is.

## Known limits

The collector is a deterministic first pass over common shapes: known
credential formats, secret-named variables, ignore rules, and the usual
spellings of `eval` and `new Function`. It does not parse JavaScript or every
dotenv dialect, so some secrets and some dynamic code will not match.
Recording `present` for a tool check means the collector found nothing, not
that nothing exists. While reading entry points and sinks, you still review
secrets and dynamic code, and re-record a tool check with your own citation
when you find a shape the collector missed.

## Citing secrets safely

Never put a secret value in evidence, a gap, a note, a hypothesis, the
snapshot, or the chat. The protocol rejects text that looks like a key, so a
leaked value also fails the run.

- Replace the value with `<REDACTED>` inside the quote, and keep only a fixed,
  non-secret prefix or a name beside it so the quote can be verified:
  ``lib/payments.ts:4 — `sk_live_<REDACTED>` ``. `<REDACTED>` matches any text
  at that position.
- Never quote other text from the same line. On a compact or minified line,
  the text beside one key can be part of another credential.
- For a secret-named variable in an environment file, quote the name:
  ``.env.production:3 — `SESSION_SECRET=<REDACTED>` ``.
- For a secret spread over several lines, such as a private key, cite the
  header line: ``certs/dev.pem:1 — `-----BEGIN <REDACTED>` ``.
- If a file's path itself looks like a credential, describe the finding in a
  `note:` without the path.
- For history, cite the scan as a command with counts only.
- Do not search for a secret's value with `search:`; search for its prefix or
  variable name.

## When to use `hypothesis`

Record a hypothesis, not a status, when one hop of the trace is outside what
you can read. Examples:

- authorization that may happen in a separate service or database policy, such
  as row-level security, that is not in the repository;
- a header that may be set by a proxy or platform configuration you cannot see;
- a framework or library behaviour you could not confirm for the installed
  version;
- input that reaches a sink only if an environment variable takes a certain
  value.

Grade the check on the evidence you have, and name the missing hop in the
hypothesis so a human can close it.

## Severity and judgement

Act on at most five findings; `finish` refuses more. Rank critical before high, then anonymous
entry points before authenticated ones, then by the number of affected
entry points. Exposed secrets always come first, because rotation cannot wait
for the rest of the plan. Mark hardening gaps `consider`. If every candidate
failed its challenge, say plainly that there are no material findings and
list what was traced.
