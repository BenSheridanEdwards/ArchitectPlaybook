# Architecture audit: detection notes

How to evaluate each check, what counts as evidence, and when to stop. Use these
words exactly in findings:

- **Module:** anything with an interface and an implementation, at any size.
- **Interface:** everything a caller must know: signatures, invariants,
  ordering, error modes, configuration.
- **Depth:** how much behaviour the interface hides.
- **Seam:** where an interface lives.
- **Adapter:** what fills a seam.
- **Locality:** whether a change stays in one place.

Do not write vague benefits such as "cleaner" or "more maintainable". Name the
locality or depth gained.

## Where to read

1. Read the collector's snapshot first:
   - `importCycles`
   - `hubs`
   - `mostImported`
   - `hotspots`
   - `changeCoupling`
   - `orphanCandidates`
   - `boundaryTooling`
2. Read the top five hotspots in full. Most structural risk sits where the code
   changes. A hotspot that is also a hub is the first thing to look at.
3. For each feature or package, read its entry point and one or two of its
   largest files.
4. Read the decision records and glossary when the collector found them. Do not
   re-litigate a recorded decision unless the friction is real. Then say
   "contradicts decision N, worth reopening because…".

## Layer 1: dependency structure

- **No import cycles (tool).** The collector resolves relative imports and the
  path aliases of every `tsconfig*.json` and `jsconfig.json`, and cites each
  edge. Only static imports count: type-only imports (including
  `import { type X }`), dynamic `import()`, and commented-out imports cannot
  form a load-time cycle. With `--with-run`, confirm with `madge --circular` or
  `depcruise`.
  - Unresolved relative and alias imports are counted in the snapshot. If there
    are many, re-record the check with `--degraded` saying the result rests on
    partial resolution.
  - Files the collector could not read are listed under `skippedSourceFiles`,
    and a clean result is then recorded as degraded.
- **Boundaries enforced by tooling (tool).** "Present" needs a rule that runs.
  It is not enough that a package is installed. The strongest evidence is a
  failing run on a deliberate violation; note it in the remediation as the
  acceptance test.
- **Dependencies point the declared way.**
  - Take the direction from the repository's own documentation or decision
    records first.
  - Otherwise infer it from the layout: domain and shared code below features,
    and features below routes and pages. Record your inference in the snapshot.
  - A violation is a concrete import, cited at its line.
- **Module internals are unreachable.**
  - Look for imports of another feature's or package's non-entry files.
  - "Present" needs both no such imports and a mechanism that would stop one:
    `exports` maps, a boundary rule, or a lint restriction.
  - Barrel files are one mechanism among several. Their absence is not a
    finding.
- **Workspaces import each other through entry points (tool).** Decided by the
  collector. Relative imports across workspaces from test files are partial.

## Layer 2: module design (red flags)

Apply the deletion test before calling anything shallow: would deleting this
module concentrate its complexity in one place, or just spread it across its
callers? A module whose deletion concentrates complexity is shallow. Record the
result of the test in the evidence or gap.

- **Modules are deep.** Look for:
  - callers that must call several functions in order to complete one
    operation;
  - options that expose internal stages;
  - a hook or service whose interface is as large as its body.

  Cite the interface and one awkward caller.
- **No pass-through layers.** A function, hook, or class that forwards the same
  arguments to another with the same shape and adds nothing. Keep a forwarding
  layer only when it adds policy, adaptation, or a distinct abstraction.
- **Each piece of state has one owner.** Search for every writer of the same
  store slice, cache key, local-storage key, or table. Two writers with
  different rules are a violation. Cite both.
- **One supported way to do each task.** Typical pairs:
  - two HTTP clients;
  - two date libraries;
  - two form approaches;
  - two feature-flag readers;
  - two logging paths.

  A migration in progress is partial if it is documented, and a violation if
  it is not.
- **No hand-synced lists.** Look for routes, permissions, feature names, or
  enum values written out in several places. Derivation, or a test that fails
  on disagreement, makes it present.
- **Representations stay behind interfaces.** Look for API response types,
  database row types, or framework request objects used in components or domain
  logic far from the boundary that receives them.
- **Code grouped by what it knows.** Folders or modules named for stages
  (`loaders/`, `validators/`, `transformers/`, `savers/`) that each re-encode
  the same entity. Low severity. Report it only when the duplication is real.

## Layer 3: change risk

- **Hotspots are cohesive and tested.** For each of the top hotspots, check:
  - does it mix unrelated responsibilities?
  - does a test exercise its interface?

  A hotspot that fails both is the strongest act-on candidate in this audit.
  Size alone is not a finding.
- **Files that change together live together.** Use the collector's
  `changeCoupling` pairs. Explain each strong cross-boundary pair: shared
  contract, misplaced code, or a missing abstraction. Coupling between a test
  and its subject is expected and not a finding.
- **Hub modules are stable.**
  - A module with both high fan-in and high fan-out amplifies change in both
    directions.
  - A widely imported utility that imports nothing is healthy.
  - A barrel that re-exports a package's public interface is judged by whether
    that interface is coherent.
- **No orphaned modules.**
  - Verify each candidate before reporting it: search for dynamic imports,
    framework conventions (file-based routing, configuration files), and
    string references.
  - With `--with-run` and `knip` installed, prefer its result and cite the
    command.

## Layer 4: data flow and decisions

- **Server state has one home.**
  - Idiomatic placements are present: Server Components, route loaders, Server
    Actions, and a query layer.
  - Client components that fetch in `useEffect` while a query layer or a
    server-side primitive exists are a violation.
  - Two client-side data-fetching libraries in active use are a violation.
- **One client-state strategy.** Count global stores in use, not dependencies
  listed. A form library, server-cache library, or URL state is not a second
  global store.
- **Side effects sit at the edges.** Direct `localStorage`, `sessionStorage`,
  `document.cookie`, or `window` access in components. Access inside a
  dedicated hook or service is present.
- **Decisions are recorded and still hold.** Missing when there are no decision
  records at all and the repository has made visible hard-to-reverse choices.
  It is a violation when code contradicts a recorded decision; cite both.
- **Domain language is consistent.** Compare names for the same concept across
  modules and the glossary. Soft check: report partial for mixed adherence.

## Severity and judgement

Act on at most about five findings. Rank by severity, then by how often the
affected code changes: a finding in a hotspot outranks the same finding in
dormant code. Mark speculative design observations `consider`. If every
candidate is speculative, say plainly that there are no material findings.
