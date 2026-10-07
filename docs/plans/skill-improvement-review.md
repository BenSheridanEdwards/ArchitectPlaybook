# Architect Playbook: skill review and improvement plan

**Reviewed:** 6 October 2026, against `main` at `fd18e4d`.

**Scope:** all twenty skills in this repository, plus the repository-local `pull-request-quality-contract`.

**Compared with:**

- Matt Pocock's `mattpocock/skills` at `6fd9479`.
- Lauren Tan's `pstack` in `cursor/plugins` at `df58112`.
- Garry Tan's `garrytan/gstack` at `5885157`.

**Status:** this is the plan of record. It is executed through reviewed pull requests, starting with #23.

**Method:** every `SKILL.md`, contract, decision record, the validator, and the calculator were read in full. All `.architect-audits/` outputs under `~/Projects` were inspected. Claims about the three external collections were checked against their source files.

## Summary

### What is strong

- **The evidence contract.** Separating applicability, evaluation state, evidence quality, and status is more rigorous than anything in the three comparison collections.
- **The scoring machinery.** The deterministic score calculator, its input fingerprinting, and the official, provisional, and unavailable split are good engineering. gstack only reached equivalent honesty in its `/health` skill last month.
- **Posture.** The truth rules, the read-only default, and ask-before-plan and ask-before-post are right for audits. Two of the three comparison collections are looser here, and you should not follow them.
- **`/ben-architect-review` is the best skill in the set.** It encodes real judgement: a failure scenario before every comment, a decision ladder, and an explicit "what not to block on".

### What is not working

1. **The audits do not produce the contract.** No schema 2.0.0 `findings.json` exists anywhere under `~/Projects`. The real runs that do exist collapse the catalog (seven of thirty-three checks emitted), invent identifiers and statuses, or write a different document shape. The calculator has never scored real output.
2. **The model is doing a linter's job.** At least half of the 390 checks are mechanical facts that a compiler, linter, or script decides better. Those checks crowd out the judgement checks only a model can do. Each audit's `SKILL.md` is 2,600 to 5,200 words (roughly 5,000 to 9,500 tokens); pstack averages about 600 words per skill.
3. **Skill text contradicts the contract.**
   - Seven audits tell the model to grade unevaluated checks `partial`, which earns 50 percent.
   - `/pre-audit-setup` leaves the tree dirty, so no score can be official.
4. **Several baselines are stale or wrong.**
   - Stale: the React Compiler era, Biome 2 and Oxlint, pnpm 11 defaults, the OWASP Top 10:2025, and `@axe-core/react`.
   - Wrong: god components measured by fan-in, mandatory barrel files, Server Component `fetch` flagged as a violation, and stricter pre-commit setups penalised.
5. **The scope misses where modern TypeScript risk lives.** Server Actions and route handlers are outside `/security-audit`, and a Node service gets "not applicable" from `/testing-audit`.
6. **Nothing proves an improvement works.** `/system-self-improve` checks the structure of its edit, never whether the audit now catches the gap.

### The ten changes that matter most

1. **Generate a skeleton for the model to fill.** Each run starts from a generated skeleton, and the audit cannot finish until a `validate-findings` script passes. Enforce it with a skill-scoped `Stop` hook using `once: true`.
2. **Add fixture-based evaluations per audit.** Use the method you already run in `~/Projects/no-tautology-eval`. Every `SKILL.md` change and every `/system-self-improve` patch must show red, then green, on a fixture.
3. **Split checks into `tool` and `model`.** Tool checks are computed by a collector script. Model checks get the attention, with quoted `path:line` evidence.
4. **Do the Phase 0 correctness fixes.** These are the contract contradictions, the dirty-tree bug, stale flags, the accessibility structure, and the unrecognised `trigger:` key. Most are one-line changes.
5. **Verify findings and sort them by judgement.**
   - Give every finding an evidence tier or confidence.
   - Re-read every cited line.
   - Run an independent refutation pass for high-severity findings.
   - Sort into Act on, Consider, Noted, and Dismissed. The chat's Top 5 becomes Act on, and Dismissed becomes a visible trust mechanism.
6. **Remember decisions.** Add `.architect-audits/decisions.json`, honoured by every audit and reported separately by the score. When a user rejects a finding for a lasting reason, offer to record it.
7. **Add severity, and give each concern one owner.** Add `severity` to the catalogs, give every cross-cutting concern a single owning check, and adopt score policy 2.0.0.
8. **Rebuild `/architecture-audit`.**
   - Scope the work by Git churn.
   - Use the deletion test and the agent-era design red flags.
   - End with a design session instead of a static plan.

   Then refresh React, security, testing, and dependency, in that order of risk.
9. **Add a context intake, a pull-request-scoped `--since` mode, and better plans.** Plans should be agent briefs, ordered by the enforcement ladder: architecture, then types, then lint, then tests, then documentation.
10. **Package the playbook as a Claude Code plugin.**
    - Generate shared protocol text from one template.
    - Cut each `SKILL.md` to about 150 lines plus references.
    - Mark user-run skills `disable-model-invocation: true`.

Before you restructure the README or reorder checks, coordinate with MergeFlow. Its X-Ray feature scrapes both (see cross-cutting problem 12).

## What real runs show

- **No canonical runs exist.** Fifty-two `findings.json` files exist under `~/Projects`; none uses schema 2.0.0, and no `score.json` exists. The 1,862-line calculator has only ever scored test fixtures. The repository's 58 unit tests pass.
- **MergeFlow, May 2026:**
  - The testing audit emitted seven checks against a catalog of thirty-three, with invented identifiers such as `testing.coverage-thresholds`.
  - The quality-gates audit used `misconfigured` as a status. It also graded whole-repository type checking in pre-commit as misconfigured because the baseline expects staged-file type checking.
  - The architecture audit wrote a different document entirely: `resolvedInThisPr`, `remainingFindings`, `nextPr`.
- **This repository's own architecture dogfood** invented four checks. It graded "graph missing" as `partial`, though the skill says that case must be "not evaluated".
- **These runs predate schema 2.0.0.** But 2.0.0 made the specification longer and stricter without adding anything that forces the model to conform. Longer prose is not a mechanism.

## Cross-cutting problems, in order of impact

### 1. Nothing forces the output contract, and real runs ignore it

**Evidence:**

- There is no schema 2.0.0 `findings.json` anywhere under `~/Projects`: none of the 52 findings files found, and no `score.json`. The 1,862-line calculator has never scored a real canonical audit.
- On MergeFlow in May 2026, the testing audit emitted seven checks against a 33-check catalog, with invented identifiers. The quality-gates audit used `misconfigured` as a status. The architecture audit wrote a different document shape (`resolvedInThisPr`, `nextPr`).
- This repository's own architecture dogfood graded "graph missing" as `partial`, though the skill mandates "not evaluated" for that case.
- Those runs predate 2.0.0. But 2.0.0 made the specification longer and stricter without adding any mechanism that makes the model conform.

**Fix:** have the model fill in the output rather than author it.

1. A shared `begin-run` script writes the skeleton: run identity, commit, cleanliness measured by the same code the calculator uses, and every catalog check pre-filled as applicable and not evaluated.
2. The model changes only the checks it actually evaluated.
3. A shared `validate-findings` script, built on the calculator's existing `_canonical_check` validation, must pass before the audit may report completion.
4. A skill-scoped `Stop` hook (skills can now declare `hooks:` in frontmatter) can enforce step 3.

### 2. The skills contradict the contract they cite

These are mechanical fixes, but each one changes scores or behaviour:

- **Unevaluated checks get half credit.** Seven audits say a check that could not be evaluated should "degrade to `partial`": dependency tiers, bundle stats, `--with-run` failures, Lighthouse, link checks, shallow clones. `partial` earns 50 percent. The contract says applicable but not evaluated, with null status. Add a validator rule that rejects the phrase.
- **Removed flags still applied.** Eleven audits still apply `--include` and `--exclude`, which `ARCHITECTURE.md` says were removed.
- **Stale examples and structure:**
  - `/architecture-audit` shows a pre-2.0.0 `metadata.json` example.
  - `/accessibility-audit` has three layers and no Layer 0, against Architecture Decision Record 0001.
- **Installer and documentation drift:**
  - The installers contradict each other on deletion.
  - The installers reference stubs that no longer exist.
  - `CONTRIBUTING.md` requires a README "Why each skill exists" section that does not exist.
- **The required `trigger:` frontmatter key does nothing.** Claude Code ignores unknown keys silently, and uploading the skills to claude.ai for cloud sessions fails with "Unexpected key(s) in SKILL.md frontmatter". The `name` already equals the folder. Drop it, or move it under `metadata:`. Either way, change the frontmatter rule in `CLAUDE.md`, `.agents/CONVENTIONS.md`, `CONTRIBUTING.md`, and the validator's `validate_skills` in the same pull request, because all four currently require or document a top-level `trigger`. MergeFlow reads `trigger` but falls back to `/<folder>`.

### 3. `/pre-audit-setup` makes an official score impossible

It leaves `graphify-out/` untracked and `.claude/settings.json` modified. The calculator then reports `source-worktree-dirty-at-audit-time` for every run. Use `.claude/settings.local.json` and `.git/info/exclude` instead (details in the per-skill section).

### 4. The model is doing a linter's job

By my rough classification, at least half of the 390 checks are mechanical facts:

- configuration values;
- package presence;
- lint-rule equivalents;
- file existence;
- script drift;
- command output.

Examples:

- `/typescript-audit`: about 22 of 35 (compiler flags and typescript-eslint rules).
- `/testing-audit`: about 25 of 33 (tooling presence and Testing Library lint rules).
- `/quality-gates-audit`: all 20 are configuration detection.

Asking a model to grep for these is slower and less accurate than the tool, and it spends the attention the judgement checks need.

**Fix:** add `"method": "tool" | "model"` to each catalog check.

- **Tool checks** are computed by a per-audit collector script (`tsc --showConfig`, `eslint --print-config`, `eslint --format json` for named rules, `knip`, `dependency-cruiser`, `npm audit --json`, `git log`). The script writes their status.
- **Model checks** get the remaining budget, and require `path:line` evidence with the quoted line.
- **When the tool is absent,** the finding is "enable the tool", one check, rather than sixteen guessed checks.

### 5. The score does not measure what matters

- **No severity:** every standard check weighs the same, so `engines field declared` counts as much as `no high or critical vulnerabilities`.
- **Soft weights invert priorities.** In `/testing-audit`, "Queries via `screen`" (a lint rule) and "No assertions against hard-coded utility classes" weigh 1.0. "Tests describe user behaviour", which the audit calls its first-class principle, and "Mocking at module boundaries" weigh 0.5.
- **Concerns are counted repeatedly:** one decision about state or data fetching costs up to four checks across four audits. React hooks, testing, and accessibility lint plugins each count in two audits.
- **Fix:** policy 2.0.0 with `severity` (for example critical 8, high 4, medium 2, low 1), plus one `ownerCheckId` per cross-cutting concern. Other audits cross-reference it without scoring it.

### 6. Findings are unverified and cannot be dismissed

- There is no confidence or evidence tier and no verification pass.
- There is no way to record "we decided this deliberately", so every re-run re-flags the same trade-offs until people stop reading.
- MergeFlow already models `approvedPatterns` and `architectureNotes` for this playbook, but the skills themselves have no equivalent.

**Fix:**

- an evidence tier per finding;
- a re-read of every cited line before writing;
- an independent refutation pass for high-severity findings;
- `.architect-audits/decisions.json`, holding accepted risks with reason, owner, and expiry. Every audit honours it, and the score reports accepted risks separately rather than hiding them.

### 7. Baselines are stale, and some are wrong

- **Stale:**
  - No mention of React Compiler, `useEffectEvent`, `useActionState`, Oxlint, or `knip`.
  - Remix appears in nine skills, Create React App in seven, `bun.lockb` in three, and `.eslintrc` scaffolding in one.
  - `@axe-core/react` does not support React 18 or later.
  - Biome detection assumes 1.x.
  - pnpm 11 security defaults and the OWASP Top 10:2025 are not reflected.
- **Wrong:** god components measured by fan-in, required barrel files, Server Component `fetch` flagged, whole-repository pre-commit type checks graded "misconfigured", and lockfile duplicates graded as violations (details per skill).
- **Fix:** add `lastVerified` per check, and a `/system-self-improve --staleness` sweep each quarter. Allow check retirement.

### 8. Static-only where runtime evidence is now cheap

Accessibility and performance can now be measured by an agent-driven browser. Quality gates can be run, and branch protection can be queried. The repository's truth rules ("prove the command, hook, or workflow runs") already demand this; the audits just do not do it yet.

### 9. Scope does not match full-stack TypeScript

A Next.js application's highest-risk code (Server Actions and route handlers) is out of scope for `/security-audit`. A Node TypeScript service gets "not applicable" from `/testing-audit`. `/error-handling-audit` is client-centric.

### 10. Workflow gaps

- **No context intake.** Audits judge without knowing:
  - the product and its users;
  - risk tolerance;
  - deliberate decisions;
  - out-of-scope areas.

  Matt Pocock's grilling and pstack's "interview the repository, not the user" combine well here. Learn everything observable from the repository. Then ask the user only about what is not observable, in rounds, each question with a recommended answer. Write the result to `.architect-audits/context.md`.
- **No pull-request-scoped mode.** Whole-repository audits are a one-off. Teams adopt baselines through ratchets: "no new violations in changed files" (`--since=<ref>`).
- **Plans are not agent-executable.** Each task should carry the check identifiers it flips, the files, acceptance criteria, and a verification command. Optionally emit GitHub issues as vertical slices.
- **The self-improvement loop is not closed.** It depends on a human writing `review-gap-report.md`. `/ben-architect-review` should draft one whenever a blocker maps to no catalog check.

### 11. Packaging and Claude Code features the playbook does not use

- **Frontmatter:** `disable-model-invocation: true` on audits and installers (pstack sets it on 50 of 51 skills), plus `argument-hint` and read-only `allowed-tools`.
- **Dynamic context:** `${CLAUDE_SKILL_DIR}` to find `checks.json` and scripts, and `` !`git rev-parse HEAD` `` style injection.
- **Isolation:** `context: fork` to run an audit in its own subagent context.
- **Distribution:** plugin plus marketplace instead of copy installers.
- **Worktree location:** `--worktree` creates `../wt-<audit>` beside the repository, which conflicts with your `~/Projects/README.md` rule that worktrees live in `.worktrees/`.
- **Worktrees are not needed for parallelism.** Read-only audits do not conflict and can run in parallel without them; a worktree is only needed where a fix will land.

### 12. MergeFlow depends on undocumented internals of this repository

MergeFlow's X-Ray (`apps/server/src/architectPlaybookCatalogue.ts`):

- builds its audit list by parsing the README "Audit types" bullet lines (`- **Ship:** **[Quality Gates](...)**`);
- builds each inventory by scraping SKILL.md tables, keyed on the column headers;
- generates its own check identifiers (`<folder>.<slug>.<index>`);
- instructs the model to emit the legacy `checks[].check` shape (`apps/server/src/xRayRunner.ts:84`).

The skill text it injects at the same time demands schema 2.0.0 with `checkId`. It also scores with its own `STATUS_SCORE` table, a second calculator.

**Fix:**

- Publish `playbook.json` (audits, groups, triggers, versions, catalog paths).
- Have MergeFlow read `playbook.json` and `checks.json` instead of prose.
- Agree one findings contract.
- Treat README restructuring and check reordering as breaking changes until then.

### 13. This repository fails its own agentic audit

- The generated GitNexus block appears in both `CLAUDE.md` and `AGENTS.md`. `CLAUDE.md` says to read `AGENTS.md` first, so the block loads twice.
- That block instructs symbol-level impact analysis before every edit, in a repository that is mostly Markdown.
- The validator's `rglob` scans untracked files and `.worktrees/` checkouts, so a stray draft or a worktree on another branch can fail the local gates.

## Per-skill review

Each entry gives a verdict, then what to keep, fix, and add. File references are to this repository unless stated.

### `/install-architect-playbook-globally` and `/install-architect-playbook-locally`

**Verdict:** replace with plugin packaging; until then, fix the contradictions.

- **Fix:** the global installer promises it "Refuses to delete any directory in the destination — only `cp -R` over the top of it" (`install-architect-playbook-globally/SKILL.md:106`), then says "Same loop as the local installer" (`:86`), which runs `rm -rf "$dst"` (`install-architect-playbook-locally/SKILL.md:85`). Copying over the top without removal also leaves renamed reference files behind.
- **Fix:** both examples still print `installed (stub)` lines, but no stubs exist. `--no-stubs` is used in Step 4 but missing from Usage. Modification-time comparison is unreliable after `git checkout`; compare content hashes instead.
- **Fix:** the `${BASH_SOURCE[0]}` root detection cannot work inside a skill. Claude Code now substitutes `${CLAUDE_SKILL_DIR}` in skill bodies; use it.
- **Change:** a Claude Code plugin plus a marketplace manifest replaces both skills and the copy drift (`/plugin marketplace add BenSheridanEdwards/ArchitectPlaybook`, then `/plugin install`). Updates become versioned, and `/system-self-improve` edits reach users without a manual re-install. For maintainers, a symlink install keeps the clone and the installed copy identical.

### `/pre-audit-setup`

**Verdict:** right idea; its side effects block the playbook's own score.

- **Fix (high):** it creates untracked `graphify-out/` and modifies `.claude/settings.json`. The score calculator treats any change outside `.architect-audits/` as a dirty tree (`repository-quality-score/scripts/calculate_repository_quality_score.py:322`). After setup, no audit can feed an official score unless the user commits a generated graph and a settings change. Put the hook in `.claude/settings.local.json` and add `graphify-out/` to `.git/info/exclude`; neither touches tracked files.
- **Fix:** graphify now installs with `pip` plus `graphify claude install`. Confirm that `~/.claude/skills/graphify/SKILL.md` (Step 1) is still where it lands.
- **Add:** make this the one place that detects the stack. Write `.architect-audits/project-profile.json` once (package manager, framework and router, React and TypeScript versions, monorepo layout, deployment target, test runner, linter). Today each audit re-detects the stack with its own list; the Remix detection alone appears in nine files.
- **Add:** a short context intake, described in the cross-cutting section.

### `/preflight`

**Verdict:** useful, but a deterministic lookup table written as a 201-line prompt. It should be a script.

- **Fix:** `process.env.CI === 'true'` (`preflight/SKILL.md:99`) is JavaScript inside an agent instruction; check `$CI`.
- **Fix:** it scaffolds `.eslintrc.cjs` (`:146`). ESLint 9 made flat configuration the default, so print flat-configuration snippets only.
- **Fix:** Bun's text lockfile `bun.lock` is not detected; only `bun.lockb` is.
- **Fix:** `.lighthouserc.json` configures Lighthouse continuous integration (`@lhci/cli`), not the `lighthouse` package it installs. "Four Core Web Vitals categories" conflates Lighthouse categories with Core Web Vitals metrics.
- **Fix:** it refuses monorepo roots, while `/architecture-audit` audits the repository root. Pick one monorepo story.
- **Change:** ship `preflight/scripts/detect.py --json`. The skill runs it, prints the table, and offers installs. Fold the result into `project-profile.json`.

### `/architecture-audit`

**Verdict:** measures lint-level structure rather than architecture, and several checks would push teams toward worse designs.

- **Wrong:** "No god component" uses fan-in (`architecture-audit/SKILL.md:81`). A `Button` rendered by 100 parents is a healthy primitive. God components are high fan-out, large, and multi-responsibility.
- **Wrong:** "No god module" uses fan-in above 30. `utils`, `types`, and an `api-client` are expected to be widely imported; the example remediation tells teams to split `api-client`. The risk signal is high fan-in combined with high churn or high fan-out (a hub), or a stable module depending on an unstable one.
- **Wrong:** "Feature folders have a single entry point" requires barrel files (`:69`, `:102`). Barrels are widely discouraged for bundle size, tooling speed, and cycle risk, which conflicts with the bundle and performance audits' goals.
- **Wrong:** "Data fetching layer separated" (`:91`) flags `fetch` outside a hooks layer. That is idiomatic in Server Components, route loaders, and Server Actions.
- **Wrong:** a 400-line file budget graded as a violation rewards shallow modules. Ousterhout's deep-module argument says interface size matters more than file length.
- **Fix:** the `metadata.json` example (`:311-325`) is the pre-2.0.0 shape. Step 6 still applies the removed `--include` and `--exclude` flags.
- **Fix:** without graphify every check becomes not evaluated (`:15`). Cycles and boundary rules can come deterministically from `dependency-cruiser` or `madge`, so keep graphify for communities and centrality only.
- **Add:**
  - Deep versus shallow modules: interface surface compared with implementation size, and pass-through layers.
  - Change coupling and hotspots from Git history (churn multiplied by complexity), plus files that always change together across a boundary.
  - Dependency direction versus stability.
  - Whether boundary rules are enforced by tooling (dependency-cruiser, `eslint-plugin-boundaries`, Nx module boundaries).
  - Whether accepted decisions still hold in code.
  - Domain-language consistency against a glossary.
- **Replace Layer 2 with the agent-era red flags.** Use pstack's eight design red flags: shallow module, information leakage, temporal decomposition, pass-through method, split ownership, two ways to do one task, importable internals, hand-synced list. Require Matt Pocock's deletion test for every "shallow module" claim ("would deleting it concentrate complexity, or just move it?"). Badge each candidate `Strong`, `Worth exploring`, or `Speculative`.
- **Change:** phase 2 should not be a static plan for architecture. Offer a design session on one chosen candidate. Generate two or three structurally distinct shapes (Matt's design-it-twice, pstack's `arena`), then grill the trade-offs in rounds before writing anything. Offer to record a rejected candidate as a decision so the next audit does not re-suggest it.

### `/testing-audit`

**Verdict:** a React Testing Library style guide rather than an audit of whether the tests would catch bugs.

- **Fix (high):** a TypeScript backend gets "not applicable" because React is required (`testing-audit/SKILL.md:176`, `:203`). Grade any TypeScript project, and make the Testing Library layers conditional.
- **Fix:** about sixteen of the thirty-three checks are rules in `eslint-plugin-testing-library` and `eslint-plugin-jest-dom`:
  - `prefer-screen-queries`
  - `render-result-naming-convention`
  - `no-container` and `no-node-access`
  - `prefer-find-by`
  - `prefer-presence-queries`
  - `no-wait-for-multiple-assertions`
  - `no-wait-for-side-effects`
  - `no-unnecessary-act`
  - `no-manual-cleanup`
  - `prefer-user-event`
  - `prefer-explicit-assert`

  Grade "plugin enabled with these rules", and count violations by running the linter.
- **Fix:** "Components have at least minimal coverage" demands a test file per component, which contradicts the audit's own behaviour-first philosophy: one integration test can cover a page of components.
- **Add:** "would these tests fail if the product broke?" Your own `no-tautology` skill (iterated with evals in `~/Projects/no-tautology-eval`) is exactly this judgement. Make it the core Layer 4 method, ranked by risk: hotspot and high-centrality modules first.
- **Add:** optional `--with-mutation` running Stryker on the five riskiest files, where a mutation score is far stronger evidence than coverage.
- **Borrow:** gstack's `/test-audit` shape: a mechanical pre-filter before the model reads anything (no assertion, source grep, near-duplicate), a discovery budget, and a "retirement card" per low-value test. Also pstack's five shapes that "still pass when every imported function returns `undefined`".
- **Add:** recognise Vitest browser mode, Playwright component tests, Storybook interaction tests, and Mock Service Worker as first-class.

### `/typescript-audit`

**Verdict:** solid configuration layer; the source layer duplicates `typescript-eslint`; the boundary layer is the valuable part.

- **Keep:** Layer 4 (runtime validation at input and output boundaries) is high-leverage judgement work.
- **Fix:** Layer 1 should read the resolved configuration with `tsc --showConfig` instead of re-implementing `extends` resolution in prose. Layer 2 maps one-to-one to typescript-eslint rules:
  - `no-explicit-any`
  - `ban-ts-comment`
  - `no-non-null-assertion`
  - `consistent-type-assertions`
  - `no-unsafe-function-type`
  - `no-wrapper-object-types`
  - `no-empty-object-type`

  Regex counting of `as` and `!` is unreliable: it catches `import { a as b }` and misses non-null assertions.
- **Fix:** at a solution-style root (`"files": []` plus `references`), `tsc --noEmit` checks nothing. Use `tsc -b`, or run per project.
- **Add:**
  - `verbatimModuleSyntax`.
  - `erasableSyntaxOnly` for Node type stripping.
  - Type coverage percentage from the `type-coverage` package as a snapshot metric.
  - Server-boundary validation for Server Actions and route handlers in Layer 4.

### `/react-audit`

**Verdict:** written for React 18. React 19 and the compiler change or retire roughly a quarter of its checks.

- **Add (high):** detect React Compiler adoption. `eslint-plugin-react-hooks` `recommended` now ships the compiler rules: `set-state-in-effect`, `set-state-in-render`, `purity`, `refs`, `immutability`, `static-components`, `globals`, `preserve-manual-memoization`, `incompatible-library`, and `error-boundaries`. Several Layer 1 checks become "rule enabled and clean". Under the compiler, "memoization for correctness" and most manual memoization advice change meaning.
- **Fix:** `forwardRef` is unnecessary in React 19 (ref as a prop). "Named exports for components" must exempt framework-required default exports: Next.js pages and layouts, and `React.lazy` targets.
- **Fix:** Remix is now React Router 7; detect `@react-router/*`. Treat Create React App as a deprecated-toolchain finding, not a framework variant.
- **Add:**
  - `useEffectEvent` as the fix for justified `exhaustive-deps` suppressions.
  - `useActionState` and form actions.
  - `<Activity>`.
  - Server and client component boundary correctness.

### `/performance-audit`

**Verdict:** mostly static micro-optimisation heuristics; it needs measurement.

- **Fix:** "Expensive computations memoized" flags every `.filter` or `.sort` in render without `useMemo`. That is premature optimisation by React's own guidance, and the compiler handles it. With React Compiler detected, Layer 1 memoization checks should be not applicable.
- **Fix:** the "single data-fetching strategy" concern is duplicated across `/architecture-audit`, `/react-audit` (twice), and `/performance-audit`, so one gap costs four checks.
- **Add:** a measured mode. With an agent-driven browser (Playwright, or Chrome DevTools through its Model Context Protocol server), record a trace on the top three routes. Report Largest Contentful Paint breakdown, Interaction to Next Paint, and long tasks, and rank fixes by measured impact. Static heuristics then become hypotheses verified by the trace.
- **Borrow:**
  - pstack's performance mantras for ranking ("Don't do it" before "Do it cheaper", stopping when an earlier mantra meets the target).
  - pstack's benchmark checklist for any reported number.
  - gstack `/benchmark`'s median of three runs and its regression thresholds.

### `/accessibility-audit`

**Verdict:** good content, but it breaks the playbook's own structure, and its runtime story is missing.

- **Fix:** three layers and no Layer 0 (`accessibility-audit/SKILL.md:9`), against Architecture Decision Record 0001. It also lacks the boundary table, idempotency, and failure-mode sections the other audits have.
- **Fix:** "Axe in development" recommends `@axe-core/react` (`:43`), which does not support React 18 or later.
- **Add:** the Web Content Accessibility Guidelines 2.2 criteria the audit claims but does not check: 2.5.8 Target Size (Minimum), 2.4.11 Focus Not Obscured, 2.5.7 Dragging Movements, 3.3.7 Redundant Entry, and 3.3.8 Accessible Authentication.
- **Add:** `--with-browser`, which runs axe on the top routes and does a keyboard tab-through with focus-visibility screenshots. Static analysis cannot verify focus order.

### `/security-audit`

**Verdict:** a careful frontend checklist, but the highest risks in a modern TypeScript application are out of scope.

- **Fix (high):** "frontend-only" (`security-audit/SKILL.md:11-31`) excludes server-side authorization. In Next.js, Server Actions and route handlers are public endpoints. Broken Access Control is still number one in the OWASP Top 10:2025, and it now also covers Server-Side Request Forgery. Bring in server code that lives in the same repository:
  - authentication and authorization in every Server Action and route handler, including object-level checks;
  - server-side input validation;
  - middleware-only authorization (the CVE-2025-29927 class);
  - `server-only` imports for secrets;
  - user-controlled URLs reaching server `fetch`.
- **Add:** a large-language-model feature layer when an AI software development kit is detected: prompt injection into tool calls, model output rendered as HTML, and secrets in prompts.
- **Add:** gstack's `/cso` bar for a supported finding: "a concrete attacker-controlled entrypoint, a path across an intended security boundary, demonstrated impact, and a challenge of relevant protective controls". Missing hardening, such as an absent header, needs a concrete failure scenario before it becomes a violation; otherwise report it as a hardening recommendation. gstack tried blanket exclusion lists (version 2) and removed them (version 3), so do not add one. Keep disproved candidates as coverage evidence.
- **Fix:** retire `target="_blank"` without `rel="noopener"` (`:136`); browsers have implied `noopener` since 2021. In `--with-scan`, `--no-eslintrc` and `--rulesdir` no longer exist in ESLint 9. Add `gitleaks` over history: secrets removed from the tree are still in Git.

### `/dependency-audit`

**Verdict:** sound tiers; scoring of unevaluated tiers is wrong; the supply-chain section is pre-2025.

- **Fix (high):** "Tier-dependent checks running below their required tier record `partial`" (`dependency-audit/SKILL.md:185`, also `:177`). A default run therefore gives 50 percent credit to "No high or critical vulnerabilities" without looking. The contract says these are applicable but not evaluated.
- **Fix:** "No duplicate packages" fires on almost every real lockfile. Restrict it to singletons (`react`, `react-dom`, `@types/react`, styling runtimes, `graphql`) and large duplicates. Exact-pinning `react` and `next` in `package.json` adds little over a lockfile and delays security patches.
- **Add:**
  - Install-script policy: pnpm `allowBuilds` or `onlyBuiltDependencies`, or npm `ignore-scripts`.
  - Release cooldown: pnpm 11 defaults `minimumReleaseAge` to one day; Renovate has `minimumReleaseAge`.
  - Provenance with `npm audit signatures`.
  - `knip` for unused dependencies, files, and exports, instead of a graph or regex sweep.

### `/bundle-build-audit`

**Verdict:** reasonable; trim opinions that misfire on modern defaults.

- **Fix:** "Build tool pinned (no caret)" and "Browserslist explicit" misfire: lockfiles pin, and Vite and Next.js ship deliberate defaults (Vite targets Baseline widely available). "Production mode enforced with `NODE_ENV` in continuous integration" is set automatically by `next build` and `vite build`.
- **Fix:** stats-required checks "report `partial`" without stats (Layer 2 preamble); same contract bug as the dependency audit.
- **Fix:** examples name Vite 5 and Next.js 14. Next.js 16 builds with Turbopack by default.

### `/error-handling-audit`

**Verdict:** one of the better audits; extend it beyond the browser.

- **Fix:** "No `console.log` in production code paths" duplicates `/bundle-build-audit`'s development-code check.
- **Add:**
  - Next.js `error.tsx` and `global-error.tsx` as boundaries.
  - `instrumentation.ts` `onRequestError`.
  - Node `unhandledRejection` handling.
  - Server Action error contracts.
  - Typed result types (for example `neverthrow` or Effect) as valid alternatives to throwing.

  OWASP Top 10:2025 added A10, Mishandling of Exceptional Conditions, which is a useful anchor for this audit's priority.
- **Borrow:** gstack `/plan-eng-review`'s rule as the top check: a failure mode with "no test AND no error handling AND would be silent" is a critical gap.

### `/linting-audit`

**Verdict:** accurate for 2024; the ecosystem moved.

- **Fix:** "Exactly one linter installed" penalises ESLint plus Oxlint and Biome-formatting-plus-ESLint, both common and deliberate. Biome 2 changed its configuration (domains, type-aware rules, plugins), so "Biome 1.x" detection is stale. Prefer `eslint-plugin-import-x` with the TypeScript resolver; `import/no-cycle` is notoriously slow.
- **Change:** read `eslint --print-config` for a few representative files instead of inferring the rule set from `extends` arrays in prose.

### `/quality-gates-audit`

**Verdict:** contradicts the repository's own truth rules.

- **Fix (high):** "Never executes any gate — this is a static audit" (`quality-gates-audit/SKILL.md:74`), while `AGENTS.md:57` says "Never report 'installed' from filesystem presence alone — prove the command, hook, or workflow runs." Add `--with-run`:
  - confirm hooks are installed (`core.hooksPath` or executable `.git/hooks/*`);
  - run each hook script once;
  - confirm, through `gh api` rulesets or branch protection, that continuous integration checks are required for merge. An optional check is not a gate.
- **Wrong:** in a real MergeFlow run, whole-repository type checking in pre-commit was graded "misconfigured" because the baseline expects staged-only type checking. Type checking is whole-program; the stricter setup should not lose points.
- **Add:** GitHub Actions hardening:
  - actions pinned to commit hashes (the `tj-actions/changed-files` compromise);
  - least-privilege `permissions:`;
  - no untrusted checkout under `pull_request_target`;
  - `zizmor` in continuous integration.

  Also "agents cannot bypass local gates": your own `scripts/block-no-verify.py` pattern, generalised.

### `/documentation-audit`

**Verdict:** fine; most checks are deterministic and should be computed.

- **Change:** README script drift, link resolution, TODO age through `git blame`, Architecture Decision Record structure, and file existence are scriptable. Spend model attention on the judgement checks: does the README get a new engineer to a running app, and do the architecture documents describe the code as it is?
- **Fix:** README script drift appears twice (Layers 1 and 4) and is scored twice.
- **Borrow:** gstack `/document-release`'s per-entity coverage map across reference, how-to, tutorial, and explanation (Diátaxis), plus its diagram-drift check (architecture diagram entity names against the code).

### `/agentic-audit`

**Verdict:** audits 2024-era agent setup; misses most of what matters in 2026.

- **Add (high):**
  - Verification loop: does the instruction file tell an agent exactly how to prove its work (type check, test, lint, run the app)? This is the single highest-leverage item in agent setup.
  - Skills (`.claude/skills`, `.agents/skills`): valid frontmatter, distinct descriptions, under 500 lines, no unknown keys.
  - Subagents, plugins, and output styles.
  - Hooks as deterministic guardrails.
  - Instruction budget: auto-loaded instruction files plus imports plus Model Context Protocol tool definitions plus skill descriptions.
  - Rules that should be hooks or lint rules instead of prose.
  - Contradictions and duplication between instruction files.
  - Deny rules for reading `.env` files.
- **Wrong:** "Tone or response-style guidance present" (when an AI dependency exists) confuses the coding agent's instructions with the product's prompts. "Primary instruction file is substantive" (at least 25 lines) penalises concise files; brevity is a virtue here.
- **Borrow:**
  - pstack's `/correct` rule-to-enforcer table: every instruction-file rule names what enforces it, or is a judgement call.
  - Matt Pocock's no-op and sediment tests ("What would happen if you just deleted that paragraph?").
  - gstack's description byte budget as a check on installed skills.
- **Dogfood:** this repository would fail its own audit. The generated GitNexus block is duplicated in `CLAUDE.md` and `AGENTS.md`, and `CLAUDE.md` says to read `AGENTS.md` first, so it loads twice. It also tells agents to run symbol impact analysis before editing anything, in a repository that is mostly Markdown.

### `/repository-quality-score`

**Verdict:** excellent engineering on top of inputs that have never existed: there are zero schema 2.0.0 findings anywhere under `~/Projects`.

- **Keep:** deterministic calculator, fingerprinting, official versus provisional versus unavailable, and score separated from coverage.
- **Fix:** the score treats `engines field declared` and `no high or critical vulnerabilities` as equal. Add `severity` to `checks.json` and weight by it in policy 2.0.0. Give each cross-cutting concern one owning check, so other audits cross-reference it instead of scoring it again.
- **Fix:** after `/pre-audit-setup`, `graph-unavailable` and the dirty tree make every score provisional (see `/pre-audit-setup`).
- **Add:** a trend view: score and coverage per commit from previous `score.json` files.
- **Borrow from gstack `/health`:**
  - history written only for numeric runs;
  - trends compared only like-for-like ("Coverage changed — scores are not comparable");
  - recommendations ranked by weight multiplied by deficit;
  - "never substitute an older pass for a newer failure";
  - each audit shown as CURRENT or STALE by tree fingerprint.

### `/ben-architect-review`

**Verdict:** the best skill in the playbook. It encodes real judgement: a failure scenario before every comment, a decision ladder, and "what not to block on". Improve the mechanics, not the judgement.

- **Fix:** 614 lines exceeds Claude Code's guidance of under 500 lines for `SKILL.md`. Move the accessibility, security, and responsive checklists and the example library into `references/`, loaded when the diff touches those areas.
- **Add:** a Spec axis. Matt Pocock's `code-review` runs Standards and Spec in isolated sub-agents. gstack's `/review` grades every deliverable in the linked plan or issue DONE, PARTIAL, NOT DONE, CHANGED, or UNVERIFIABLE. Today the skill reduces the pull request to its promised outcome, but never checks deliverable by deliverable.
- **Add:** an adversarial pass before posting. For each blocker, a fresh subagent tries to refute the failure scenario from the code. Only surviving blockers drive `REQUEST_CHANGES`.
- **Add:** large pull requests: detect scope, as gstack's review army does (security, data migration, interface contract, performance, testing). Fan out matching specialists, merge by fingerprint, and state what was not reviewed. Sort the result into pstack's Act on, Consider, Noted, and Dismissed; only Act on items can block.
- **Add:** read `.architect-audits/` findings and accepted decisions for the touched files, and flag new violations the pull request introduces against the audit catalogs. That is a diff-scoped audit for free.
- **Add:** calibration learning. Log every drafted comment and what Ben actually posted (edited, deleted, softened) to a local file. Feed the difference to `/system-self-improve`; it is the best available signal of "Ben's judgement".
- **Add:** when a blocker maps to no audit check, write a `review-gap-report.md` draft. That closes the self-improvement loop, which today depends on a human writing that file.
- **Consider:** separate the portable method from the persona. A `reviewer-profile.md` (voice, thresholds, examples) lets others use the method; you keep `ben` as the default profile.

### `/system-self-improve`

**Verdict:** safe, but it cannot tell whether an improvement works.

- **Fix (high):** "post-edit verification" is structural only (`system-self-improve/SKILL.md:125`). An improvement should be a red-to-green test:
  1. turn the gap into a fixture (a tiny repository or diff with the planted issue) and an expected check status;
  2. run the current audit on it and show the miss;
  3. apply the edit;
  4. re-run and show the catch, plus the existing fixtures still passing.

  You already run exactly this method in `~/Projects/no-tautology-eval`.
- **Fix:** "never delete a check" (`:113`, `:198`) makes catalogs ratchet towards bloat. Allow retirement through a `deprecated` flag, emitted as not applicable, with a catalog version bump and a decision record.
- **Add:** `--staleness` mode, which lists checks whose `lastVerified` date is older than six months, or which name tools, packages, or versions that have since changed.
- **Borrow:**
  - Matt Pocock's rule to patch only on an observed failure, quoting the excerpt, and to route mechanical gaps to a script or catalog check and judgement gaps to prose.
  - pstack's `reflect` filters (durability, specificity, existing-skill-first) and blind evaluation on a different model family.
  - gstack's "each failed eval becomes one row in the detector's owner test".

### `/pull-request-quality-contract` (repository-local)

**Verdict:** fine for this repository. The Behavioural Proof section mostly resolves to "Not applicable" here, so the check could say so up front for Markdown-only changes.

## What to borrow

Only ideas that fix a problem identified above are listed. Each collection also has things not to copy; those are noted at the end of each section.

### From Matt Pocock (`mattpocock/skills`, HEAD `6fd9479`, 6 October 2026)

1. **Rework `/architecture-audit` along `improve-codebase-architecture`.**
   - **Scope by churn first:** walk `git log` for hot spots, or take the user's named focus.
   - **Explore against friction questions instead of fixed metrics:**
     - Does one concept require bouncing between many small modules?
     - Is the interface nearly as complex as the implementation?
     - Were pure functions extracted only for testability, while the bugs live in the call sites?
     - Do modules leak across seams?
     - Is the code hard to test through its interface?
   - **Apply the deletion test to suspects:** "would deleting it concentrate complexity, or just move it?"
   - **Badge every candidate** `Strong`, `Worth exploring`, or `Speculative`.
   - **Stop before proposing designs:** "Do NOT propose interfaces yet."
   - **Then interview the user** on the one they pick.
2. **A shared architecture vocabulary.** Module, interface ("everything a caller must know"), depth, seam, adapter, leverage, locality. "One adapter means a hypothetical seam. Two adapters means a real one." Put it in one reference file that the architecture audit and `/ben-architect-review` both load. Ban vague benefit words ("cleaner", "more maintainable") in findings.
3. **Rejection memory.** When a user rejects a candidate for a lasting reason, offer to record it "so future architecture reviews don't re-suggest it". Matt uses decision records and `.out-of-scope/<concept>.md`, matched by concept rather than keyword. This is the missing `decisions.json`.
4. **Grilling in rounds.** Ask every currently answerable question at once, numbered, each with a recommended answer. "Finding facts is your job, never the user's." Use it for the context intake and for phase 2.
5. **Agent briefs for plans.** Current behaviour, desired behaviour, key interfaces (types, not line numbers), acceptance criteria that fail at the current commit, out of scope. Plans become vertical-slice tickets with blocking edges, prefactoring first. Wide rollouts (new strict flags, new lint rules) use expand–contract batches.
6. **Two isolated review axes.** `code-review` runs Standards and Spec (does it do what the issue asked, without scope creep) in separate sub-agents, word-capped and never re-ranked together. It skips what tooling already enforces. `/ben-architect-review` lacks the Spec axis.
7. **Enforce, then prove the enforcement fails.** `setup-ts-deep-modules` wires `dependency-cruiser` and requires "you have observed a pass, then a fail on the deep import, then a pass again". "A config that doesn't fail on a violation is worthless." Apply this to every quality-gate recommendation.
8. **Prune as a feature.** Skills are merged, renamed, and deleted when they stop pulling weight (`ubiquitous-language`, `design-an-interface`, `qa`, `request-refactor-plan`, `zoom-out`). Prose gets a deletion test ("What would happen if you just deleted that paragraph?"). Contrast `/system-self-improve`'s rule that checks can never be deleted.
9. **Distribution.**
   - Claude Code plugin in a marketplace, plus `npx skills@latest add mattpocock/skills`.
   - A symlink script for maintainers, and changesets for releases.
   - Each skill has a documentation page with an "It's working if" list.
   - User-invoked skills set `disable-model-invocation: true` and get one-line human-facing descriptions.

**Don't copy:** Matt has no automated evaluations, and his own documentation admits the architecture skill "rarely" says the code is fine. Keep your deterministic scorer, and add an explicit "no material findings" outcome.

### From Lauren Tan (`pstack`, `cursor/plugins/pstack`, version 0.15.15, 5 October 2026)

1. **Visible judgement buckets.** `interrogate`'s lead judgment sorts findings into Act on, Consider, Noted, and Dismissed, using five named filters:
   - Nitpick Gravity;
   - Hypothetical versus Actual ("Trace the call site");
   - Premature Abstraction;
   - "I Would Have Done It Differently";
   - Missing Context.

   "If your 'Act On' list has more than 5 items, you're probably not filtering hard enough." "The 'Dismissed' section is not busywork. It's a trust mechanism." Your Top 5 becomes Act on, and `findings.md` gains a Dismissed section with reasons.
2. **Evidence tiers.** Direct, Supported, Inferred, Speculative, Unknown, and "Every claim carries its evidence or its label in the same sentence." A violation requires Direct or Supported. Unknown must list what was searched.
3. **Design red flags written for agent contributors** (`architect/references/design-red-flags.md`):
   - shallow module;
   - information leakage;
   - temporal decomposition;
   - pass-through method;
   - split ownership;
   - two ways to do one task;
   - importable internals;
   - hand-synced list.

   The last four exist because "an agent takes the shortest path that compiles". They are the right replacement for the fan-in and barrel checks. This repository has its own hand-synced lists: SKILL.md tables mirrored in `checks.json`, and the README groups that MergeFlow scrapes.
4. **`/correct`'s enforcement ladder.**
   1. Fix a recurring mistake class with architecture first.
   2. Then types.
   3. Then "a lint whose error names the fix".
   4. Then a test.
   5. Then documentation last.

   "Prove each new check fails on a real past mistake." Keep a rule-to-enforcer table in the instruction file. This is the ordering every implementation plan should use. It is also the missing core of `/agentic-audit`: flag instruction-file rules that nothing enforces.
5. **The tautology test** (`principle-test-behavior-not-implementation`): "If the test would still pass when every imported function returns `undefined`, rewrite the assertion or delete the test." Five named shapes:
   - weak assertion;
   - mock-only;
   - self-referential;
   - constant pin;
   - fixture asserts fixture.

   This is the same idea as your `no-tautology` skill.
6. **Concision as a maintained property.** pstack averages about 600 words per skill, against about 3,500 here. "When in doubt, delete. Keep only prose that changes a decision." Its history shows regular measured pruning passes as models improved ("cut 19 more instructions Opus 5.5 does not need").
7. **Blind evaluation of skill changes.** The eval playbook hides that it is an evaluation from candidates, judges on a different model family, and grades from files the candidate actually read rather than its claims. Use this for `/system-self-improve`'s red-to-green step.
8. **Measurement discipline.** `benchmark-checklist` and the performance mantras ("Don't do it" first, then "Do it, but don't do it again", and so on, stopping when an earlier mantra meets the target) belong in `/performance-audit`'s ranking.
9. **"Interview the repository, not the user."** "If the answer is a fact you could observe by running something … it is not the human's to answer." This is the rule for the context intake.

**Don't copy:**

- **Autonomy defaults.** pstack proceeds on external actions without asking, and its autopilot merges pull requests. Your ask-before-plan and ask-before-post posture is right for audits.
- **Cursor-specific mechanics.**
- **Model names hard-coded in skill bodies.**
- **React expertise, which isn't there.** Despite her React Compiler background, pstack ships no React skill, so the React audit's update has to come from the React documentation and the compiler lint rules, not from pstack.

### From Garry Tan (`garrytan/gstack`, version 1.91.29.0, 6 October 2026)

1. **Score honesty from `/health`.** "Wrap, don't replace" (run the project's own tools). "Skipped is not failed." Report "N/A — no checks ran" rather than a perfect score for an empty run. Mark "N/A — capture failed" when output could not be captured. Write history only for numeric runs. Compare trends only like-for-like ("Coverage changed — scores are not comparable"). Rank recommendations by weight multiplied by deficit. Your calculator already separates coverage from score; add the history, the like-for-like trend rule, and deficit ranking.
2. **The confidence gate** (`scripts/resolvers/confidence.ts`). "If you cannot quote the motivating line(s), the finding is unverified." Force confidence to 4–5, and move findings scored 3–4 to an appendix. For generated code: "I read the source that creates this symbol", not "I grep'd for the name and didn't find it."
3. **Suppression rules that do not rot.** Each review skill carries a "DO NOT flag" list. A prior finding is suppressed only when the user skipped the same unchanged finding, and "never `fixed` or `auto-fixed`" ones. Inline markers carry a reason. For security, `/cso` version 3 removed version 2's blanket exclusions. Copy the version 3 stance: a supported finding needs "a concrete attacker-controlled entrypoint, a path across an intended security boundary, demonstrated impact, and a challenge of relevant protective controls", and "Missing hardening alone needs a concrete failure scenario before becoming a finding."
4. **Generated skills from one template.**
   - `SKILL.md.tmpl` plus resolvers render committed `SKILL.md` files, with a continuous integration freshness check ("Hand-maintained docs always drift from code").
   - Tests assert structure and tokens, not English sentences.
   - A catalog test caps total description bytes.

   Your fourteen audits share about 617 lines of protocol, so this is the cheapest drift fix available. `/system-self-improve` would then patch templates, never generated files.
5. **Sections loaded on demand, with a hard pointer:** "**STOP.** Before <trigger>, Read <path> and execute it in full. Do not work from memory". An evaluation checks that the agent actually read the section before the step. Use it for layer detail, `--learn` text, and phase 2 plans.
6. **Seeded-defect evaluations.**
   - Fixture repositories with a ground-truth file of planted issues.
   - A deterministic detection rate computed from the output JSON, with a judge model only for fuzzy matching.
   - Minimum detection and maximum false-positive thresholds.
   - A "touchfiles" map, so only affected audits re-run.
   - "Each failed paid eval becomes one row in the detector's owner test."
7. **Fix-First triage for plans.** "If the fix is mechanical and a senior engineer would apply it without discussion, it's AUTO-FIX. If reasonable engineers could disagree about the fix, it's ASK." Security, races, design, removals, and anything user-visible are always ASK. Tag every Act on item this way; it makes "reply 2b" much more useful.
8. **A review army for `/ben-architect-review`.**
   - Detect the diff's scope (authentication, backend, frontend, migrations, application programming interface).
   - Dispatch matching specialist sub-agents with their own checklists (security, performance, data migration, interface contract, testing, maintainability).
   - Each returns one JSON line per finding, with severity, confidence, path, line, and fingerprint.
   - Merge by fingerprint, adding confidence for agreement.
   - Add a red-team pass for large diffs and an always-on fresh-context adversarial pass.
   - Auto-gate a specialist after ten silent runs, except security, which is never gated.
9. **Plan completion and scope drift** (`/review` step 1.5). Extract the deliverables from the linked plan or issue, and grade each DONE, PARTIAL, NOT DONE, CHANGED, or UNVERIFIABLE, with a root cause for every gap. This is the Spec axis `/ben-architect-review` lacks.
10. **Freshness by tree fingerprint.** An append-only audit log records each run with a working-tree hash, so the score and `/preflight` can show each audit as CURRENT or STALE. "Never substitute an older pass for a newer failure."
11. **`/test-audit` as the model for the testing audit's judgement layer.**
    - A mechanical grep pre-filter (no assertion, source grep, near-duplicate) before the model reads anything.
    - A discovery budget, with a resumable partial report.
    - A "retirement card" per low-value test.
    - A JSON sidecar.
12. **Failure-mode discipline from `/plan-eng-review`.** "One realistic production failure per new path"; a failure mode with "no test AND no error handling AND would be silent" is a critical gap. Use this as the error-handling audit's top check and as a `/ben-architect-review` lens.

**Don't copy:**

- **Size.** The shared startup text is about 64 percent of `/health`'s file, and `/review` loads about 19,000 tokens before it starts.
- **Legal-style procedure** and per-question ceremony. gstack's own backlog records question fatigue.
- **The dependency stack:** Bun binaries, a browser product, Codex, and gbrain.
- **Churn.** There have been 469 releases in seven months.
- **Brand promotion** written into the skills.

### Where all three agree

These are the strongest signals, because three independent authors converged on them:

1. **Deterministic work belongs in scripts.** pstack calls this "Build the lever" and "encode lessons in structure". gstack keeps about 100 `bin/` helpers. Matt's `retro` says "a deterministic check, full stop" for mechanical problems.
2. **Every finding quotes its evidence:**
   - pstack: evidence tiers.
   - gstack: "quote the motivating line" or confidence drops.
   - Matt: "cite the standard (file + the rule)", and a command "already run at least once".
3. **Remember what the user decided:**
   - Matt: architecture decision records and `.out-of-scope/`.
   - gstack: suppress skipped findings in unchanged files.
   - pstack: learned skip patterns with "Do not skip when".
4. **Independent verification beats self-review:**
   - pstack: model diversity in `interrogate`.
   - gstack: adversarial and red-team passes, and an independent challenge in `/cso`.
   - Matt: isolated review sub-agents. His documentation quotes a reader: "Same context reviewing itself isn't review, it's confirmation bias with a slash command."
5. **An empty result must be honest:**
   - gstack: "N/A — no checks ran".
   - pstack: "A gap does not count as a pass".
   - Matt: his documentation admits the architecture skill rarely reports that the code is fine, and treats that as a known weakness.
6. **User-invoked by default, kept short, pruned on purpose:**
   - pstack: `disable-model-invocation` on 50 of 51 skills.
   - Matt: 23 of 38.
   - gstack: description byte budgets.
   - All three regularly delete instructions as models improve.

## Target shape of an audit

This is what every audit should converge on. Pilot it with one audit before rolling it out.

```text
<audit>/
  SKILL.md              about 150 lines: when to use, flags, steps, the judgement rubric
                        for model checks, stop rules, chat format
  checks.json           catalog: checkId, layer, title, severity, method (tool | model),
                        ownerCheckId, rationale, lastVerified, deprecated
  references/
    baseline.md         the human-readable tables and rationale, read when writing
                        findings.md and in --learn mode
  scripts/collect.py    deterministic facts and tool-check statuses -> facts.json
  evals/                fixtures with planted issues and expected check statuses
```

**Run sequence:**

1. `begin-run` writes the skeleton: run identity, exact commit, cleanliness measured by the calculator's own function, and every catalog check pre-filled as not evaluated.
2. `collect.py` resolves the `method: tool` checks.
3. The model evaluates the `method: model` checks. It uses `path:line` evidence and an evidence tier, honours `decisions.json`, and uses the context intake.
4. Verification:
   - re-read every cited line;
   - for critical and high findings, a fresh sub-agent tries to refute the failure scenario;
   - sort into Act on, Consider, Noted, and Dismissed.
5. `validate-findings` must pass. A skill-scoped `Stop` hook with `once: true` makes this structural rather than advisory.
6. Chat output:
   - up to five Act on items, ranked by severity, then hotspot weight, then effort;
   - the Dismissed count;
   - coverage;
   - the report path.
7. Offer phase 2:
   - for most audits, a plan of agent-executable briefs ordered by the enforcement ladder;
   - for `/architecture-audit`, a design session on one chosen candidate.

## Proposed sequence

Each item is one pull request unless noted.

### Phase 0: correctness fixes, no design change

1. Record unevaluated checks as `applicable` and `not-evaluated`, never `partial`, in all seven affected audits. Add a validator rule that rejects "degrade to `partial`".
2. Remove the stale `--include`, `--exclude`, and `--stage` references from eleven audits.
3. Replace `/architecture-audit`'s pre-2.0.0 `metadata.json` example.
4. Give `/accessibility-audit` a Layer 0 and a fourth layer (split Component patterns into "semantics and naming" and "keyboard, focus, and motion"), plus the standard sections.
5. Keep `/pre-audit-setup` artefacts out of the tracked tree: `.claude/settings.local.json` and `.git/info/exclude`.
6. Reconcile the installers' deletion behaviour, remove stub references, and resolve the playbook root with `${CLAUDE_SKILL_DIR}`.
7. Drop `trigger:` from frontmatter, or move it to `metadata:`, and update the frontmatter rule in `CLAUDE.md`, `.agents/CONVENTIONS.md`, `CONTRIBUTING.md`, the validator, and its tests to match. MergeFlow already falls back to `/<folder>`. Remove the README "Why each skill exists" requirement from `CONTRIBUTING.md`, or write the section.
8. Make the validator scan tracked files only (`git ls-files`).
9. Keep one copy of the GitNexus block.
10. Move `--worktree` to `.worktrees/<audit>` to match your workspace convention, or document why sibling directories are needed.

### Phase 1: make audits reliable

1. Shared `begin-run` and `validate-findings` scripts, reusing the calculator's validation code, plus the `Stop` hook.
2. Shared protocol text (run identity, chat format, plan offer, `--learn`) generated into every `SKILL.md` from one template. The validator checks that generated output matches. This keeps skills self-contained under copy installs and removes the 617 hand-synced lines.
3. Evaluations:
   - Start with three audits: testing, security, architecture.
   - Each gets three to five fixture repositories with planted issues and expected statuses.
   - Use the method you already run in `no-tautology-eval`: planted commits, programmatic grading, and blind judgement review.
   - Make it a release gate for any `SKILL.md` change.
4. Run the full pipeline end to end on one real TypeScript and React repository (StyleProof or MergeFlow), and get the first official score. Fix what breaks before adding anything else.

### Phase 2: signal quality

1. Catalog schema 1.2.0 with `severity`, `method`, `ownerCheckId`, `rationale`, `lastVerified`, and `deprecated`.
2. Score policy 2.0.0 with severity weights and one owner per cross-cutting concern, recorded in Architecture Decision Record 0003.
3. Collectors for the most mechanical audits first: quality gates, linting, TypeScript, dependency, testing.
4. Evidence tiers, the verification pass, judgement buckets, and the Dismissed section.
5. `decisions.json` for accepted risks, honoured by every audit and reported separately by the score. Coordinate with MergeFlow's `approvedPatterns`.
6. A pruning pass. Retire or merge checks that tools own, that misfire, or that duplicate another audit.

### Phase 3: workflow

1. Context intake in `/pre-audit-setup`, writing `project-profile.json` (observed) and `context.md` (asked, in grilling rounds).
2. Diff-scoped mode: `--since=<ref>`, "no new violations".
3. Plans as agent briefs ordered by the enforcement ladder, optionally published as tickets.
4. `/ben-architect-review`:
   - adversarial pass;
   - Spec axis;
   - gap-report drafting;
   - calibration log;
   - references split out to get under 500 lines.
5. `/system-self-improve`: red-to-green fixtures, check retirement, `--staleness`.
6. Plugin and marketplace packaging; `disable-model-invocation`, `argument-hint`, `allowed-tools`.
7. `playbook.json` manifest, then migrate MergeFlow's X-Ray off README and table scraping.

### Phase 4: content refresh

One pull request per audit, in this order of risk reduction:

1. Security (server-side code, large-language-model features).
2. Testing (any TypeScript, effectiveness).
3. React (compiler era).
4. Dependency (supply chain).
5. Quality gates (run them, branch protection, Actions hardening).
6. Architecture (red flags, churn, deletion test).
7. Agentic (2026 surface).
8. Accessibility (WCAG 2.2 gaps, browser mode).
9. Performance (measured mode).
10. Linting.
11. Bundle and build.
12. Error handling.
13. Documentation.

## Sources

**Comparison collections, read at the commits stated:**

- Matt Pocock, `mattpocock/skills`: https://github.com/mattpocock/skills
  - Architecture method: `skills/engineering/improve-codebase-architecture/SKILL.md`.
  - Vocabulary: `skills/engineering/codebase-design/`.
  - Interviewing: `skills/productivity/grilling/SKILL.md`.
- Lauren Tan, `pstack`: https://github.com/cursor/plugins/tree/main/pstack
  - Red flags: `skills/architect/references/design-red-flags.md`.
  - Judgement buckets: `skills/interrogate/references/lead-judgment.md`.
  - Enforcement ladder: `skills/correct/SKILL.md`.
  - Tautology test: `skills/principle-test-behavior-not-implementation/SKILL.md`.
- Garry Tan, `gstack`: https://github.com/garrytan/gstack
  - Score honesty: `health/SKILL.md`.
  - Confidence gate: `scripts/resolvers/confidence.ts`.
  - Fix-First and suppressions: `review/checklist.md`.
  - Supported findings: `cso/SKILL.md`.
  - Template generation: `scripts/gen-skill-docs.ts`.

**Claude Code documentation** (frontmatter fields, the 500-line guidance, unknown keys ignored, skill-scoped hooks, marketplaces):

- https://code.claude.com/docs/en/skills
- https://code.claude.com/docs/en/hooks
- https://code.claude.com/docs/en/plugin-marketplaces

**Ecosystem facts cited:**

- `@axe-core/react` does not support React 18 or later: https://www.npmjs.com/package/@axe-core/react
- `eslint-plugin-react-hooks` recommended rules, which now include the compiler rules: https://react.dev/reference/eslint-plugin-react-hooks
- OWASP Top 10:2025: https://owasp.org/Top10/2025/
- pnpm 11 defaults, including a one-day `minimumReleaseAge`: https://www.cryptika.com/pnpm-11-turns-on-minimum-release-age-by-default-to-reduce-npm-supply-chain-risk/

**Local evidence:**

- `~/Projects/MergeFlow/.architect-audits/`
- `~/Projects/MergeFlow/apps/server/src/architectPlaybookCatalogue.ts`
- `~/Projects/MergeFlow/apps/server/src/xRayRunner.ts`
- `~/Projects/no-tautology-eval/`
