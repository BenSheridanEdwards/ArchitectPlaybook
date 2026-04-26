---
name: accessibility-audit
description: Audit a TypeScript/React frontend against an opinionated WCAG 2.2 AA baseline (tooling, component patterns, application shell) with optional implementation plan.
trigger: /accessibility-audit
---

# /accessibility-audit

Audit a TypeScript and React frontend against an opinionated accessibility baseline organised in three layers — **tooling and automation**, **component patterns**, and **application shell** — then offer to generate an implementation plan for the gaps. Targets WCAG 2.2 Level AA. The audit is fully static: it reads the codebase, never starts the development server, and never runs a live accessibility scanner. Screen-reader behaviour and focus-order verification are explicitly out of scope — they can only be verified reliably by a human.

The default mental model is React (any flavour: Vite, Create React App, Next.js, Remix). Detection is framework-agnostic, with extra hints emitted when Next.js or Remix is present because route-announcement expectations differ.

## Usage

```
/accessibility-audit                                 # full two-phase run: audit, then ask about a plan
/accessibility-audit --report-only                   # phase 1 only: write findings, do not ask about a plan
/accessibility-audit --plan                          # skip phase 1 if findings already exist; jump to plan generation
/accessibility-audit --layer=tooling                 # restrict the audit to a single layer (repeatable)
/accessibility-audit --layer=component-patterns
/accessibility-audit --layer=application-shell
/accessibility-audit --include=<check-name>          # include only the named check (repeatable)
/accessibility-audit --exclude=<check-name>          # skip the named check (repeatable)
/accessibility-audit --severity=error                # report only violations and missing-required checks
```

This skill never accepts `--apply`. Mutating the codebase is the responsibility of a separate fix step. The implementation plan is descriptive Markdown.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the check is fully satisfied; the audit found everything it expected.
- **partial** — some required signals resolved, others did not. The check is half-implemented.
- **missing** — the tooling, configuration, or pattern is not present at all.
- **violation** — the audit found code that actively contradicts the check (for example, an interactive `<div>` with no keyboard handler).

`missing` and `violation` are distinct on purpose. `missing` means "the safety net isn't in place"; `violation` means "the safety net is missing *and* there is concrete code that the safety net would have caught."

### Layer 1 — Tooling and automation

| Check | Expectation | Primary detection signals |
| --- | --- | --- |
| jsx-accessibility lint plugin | `eslint-plugin-jsx-a11y` is installed and the `recommended` (or `strict`) ruleset is enabled in the active ESLint configuration. | `package.json` devDependency on `eslint-plugin-jsx-a11y`; `extends` entry in `eslint.config.*` or `.eslintrc*` referencing `plugin:jsx-a11y/recommended` or `plugin:jsx-a11y/strict`; no file-level or directory-level disables of the plugin. |
| Axe in development | `@axe-core/react` is initialised in development mode so violations are logged to the browser console during local work. | devDependency on `@axe-core/react`; an entry-point file (typically `src/main.tsx`, `src/index.tsx`, or `pages/_app.tsx`) imports it and calls `axe(React, ReactDOM, ...)` behind a development guard. |
| Component-test integration | `jest-axe` or `vitest-axe` is available, and at least one component test asserts `expect(...).toHaveNoViolations()`. | devDependency on `jest-axe` or `vitest-axe`; at least one test file imports the matcher and uses it. |
| End-to-end accessibility scan | `@axe-core/playwright` (or the Cypress equivalent) is configured, and at least one end-to-end specification runs an axe scan against a rendered page. | devDependency on `@axe-core/playwright` or `cypress-axe`; a spec file imports and invokes the scanner. |
| Storybook accessibility addon | If Storybook is in use, `@storybook/addon-a11y` is registered. | Storybook configuration file (`.storybook/main.*`) lists the addon in `addons`. Skipped silently if Storybook is not detected. |
| Continuous integration enforcement | A continuous-integration workflow step runs the unit and end-to-end accessibility checks and fails the build on violations. | Workflow file under `.github/workflows/`, `.gitlab-ci.yml`, or equivalent contains a step invoking the relevant test command. |

### Layer 2 — Component patterns

These checks require reading components. Use the knowledge graph to find the right entry points (god nodes for component clusters), then sample broadly. The audit reports counts and representative file references, not an exhaustive list.

| Check | Expectation | What counts as a violation |
| --- | --- | --- |
| Semantic HTML over generic containers | Interactive elements use the right tag (`<button>`, `<a>`, `<input>`, `<select>`); lists use `<ul>`, `<ol>`, or `<dl>`. | A `<div>` or `<span>` with an `onClick` and no `role`, no keyboard handler, and no `tabIndex`. A list-shaped UI rendered with `<div>` children. A clickable item that should be a `<button>` or `<a>`. |
| ARIA used correctly | ARIA attributes appear only on elements that support them, are not redundant, and are present where required. | `aria-*` on an element where it has no effect (for example, `aria-checked` on a `<div>` without `role="checkbox"`). Redundant ARIA (`role="button"` on `<button>`). Icon-only buttons with no `aria-label` or `aria-labelledby`. Disclosure triggers without `aria-expanded`. Modals without `aria-modal="true"` and a labelling strategy. |
| Keyboard support | Every interactive element is reachable by Tab and operable by keyboard. Focus is visible. | `outline: none` (or `outline: 0`) without a replacement focus style. Custom widgets that do not implement the WAI-ARIA Authoring Practices keyboard model. Keydown handlers that block default behaviour without re-implementing it. |
| Focus management | Modals trap focus and restore on close; route changes move focus to a sensible target; a skip-to-content link is present and is the first focusable element. | A modal component without focus trap and without focus restoration. A custom router with no focus handling on navigation. Auto-focus on page load on a non-form-first page. |
| Forms | Every input has an associated `<label>`. Errors are programmatically associated with their fields. Required fields are indicated for both screen readers and sighted users. Grouped controls use `<fieldset>` and `<legend>`. | An `<input>` whose only label is a placeholder. An error message that is rendered visually but not tied to its field by `aria-describedby`. A required field marked only with an asterisk. A radio or checkbox group without `<fieldset>`/`<legend>`. |
| Images and media | Every `<img>` has an `alt` attribute (empty `alt=""` is acceptable for purely decorative images). SVG icons used as content have `<title>` or `aria-label`. Videos have captions. Auto-playing media is muted and has a pause control. | `<img>` without `alt`. `<svg>` used as a content icon with no accessible name. `<video autoplay>` without `muted` and without a pause control. |
| Colour and contrast | Text colours meet WCAG AA contrast against their backgrounds. Information is never conveyed by colour alone. | When design tokens are statically defined (CSS custom properties, Tailwind theme, theme objects), text/background pairings that fall below 4.5:1 (or 3:1 for large text). UI states differentiated only by colour (for example, an error indicated only by red text). |
| Motion and animation | Long animations respect `prefers-reduced-motion`. No essential information is delivered through animation alone. | Animations longer than five seconds with no pause control. Global stylesheets with no `@media (prefers-reduced-motion: reduce)` block. Auto-rotating carousels without pause. |
| Text and content structure | Headings are in document order with no skipped levels. Link text is descriptive in isolation. | A page that jumps from `<h1>` to `<h3>` with no `<h2>`. Multiple `<h1>` per page (outside an `<article>` context that warrants it). Link text such as "click here", "read more", or a bare URL. |

### Layer 3 — Application shell

| Check | Expectation | Primary detection signals |
| --- | --- | --- |
| Document language | `<html lang="...">` is set to a real language code. | The HTML shell (`index.html`, `app/layout.tsx`, `pages/_document.tsx`, `app/root.tsx`) sets a non-empty `lang`. |
| Per-route document titles | Document title updates on every navigation. | Use of the framework's title primitive (`<title>` in Next.js metadata, `<Helmet>` in Vite-React, `meta` exports in Remix) on every routed view. |
| Skip-to-content link | A skip link to the main landmark exists and is the first focusable element on every page. | An `<a href="#main">` (or equivalent) rendered in the application shell, styled to be visible on focus. |
| Route announcement | Client-side navigations are announced to assistive technology. | A live region in the shell, or use of the framework-provided announcer (Next.js `app/` router's `RouterAnnouncer`, Remix's built-in announcement, or a custom `aria-live` region). |
| Viewport meta | The viewport meta tag is present and does not disable scaling. | `<meta name="viewport" content="width=device-width, initial-scale=1">`. The check fails if `user-scalable=no` or `maximum-scale=1` appears. |
| Landmark roles | The application shell renders the standard landmarks once per page: `banner`, `main`, `contentinfo`. `navigation` and `complementary` appear when the design has them. | The shell uses `<header>`, `<main>`, `<footer>`, `<nav>` (or the explicit `role` equivalents). No duplicate landmarks of the same role without an `aria-label` differentiating them. |
| Error and not-found pages | Custom error and 404 pages have a heading, manage focus, and offer a way back to the main application. | Presence of an error route (`app/not-found.tsx`, `app/error.tsx`, or framework equivalent). Heading present. A link or button that returns to a known landing route. |
| Authentication pages | Sign-in and sign-up forms support password-manager autofill, declare input purposes, and do not trap users in modal flows that break with a screen reader. | Inputs use the correct `autoComplete` values (`username`, `current-password`, `new-password`, `email`). Forms are not nested inside non-dismissible modals. |

## What this skill does

1. **Reads the knowledge graph first.** If `graphify-out/graph.json` exists, read `graphify-out/GRAPH_REPORT.md` to identify component clusters, the application shell, and route entry points before searching raw files. The PreToolUse hook installed by `/pre-audit-setup` reminds you of this on every Glob and Grep — respect it.
2. **Confirms a React project.** Detects React via `package.json` dependencies (`react`, `react-dom`, or a meta-framework that depends on them). If absent, the skill stops and tells the user it currently supports React only.
3. **Detects the framework variant** (plain React, Next.js `pages/` router, Next.js `app/` router, Remix, Vite-React) by looking at dependencies and directory layout. The variant influences which application-shell signals are checked and which remediation snippets are produced.
4. **Walks each check in the active layer list**, applying any `--include`, `--exclude`, or `--severity` filters. Records a status per check and a list of representative file references.
5. **Writes phase 1 outputs** to `audits/accessibility-audit/`:
   - `findings.md` — grouped by layer, one section per check.
   - `findings.json` — machine-readable.
   - `metadata.json` — skill version, run timestamp, graphify revision hash, framework variant.
6. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the missing or violating accessibility checks? (yes/no)"

   On `yes`, writes `audits/accessibility-audit/implementation-plan.md` describing exactly which packages to install, which configuration files to add, which application-shell elements to wire up, and which component-pattern violations to remediate — ordered by layer and then by severity. The plan does not modify any project files.

   On `no`, exits cleanly. The user can re-run with `--plan` later to skip phase 1.

## Implementation steps

### Step 1 — Confirm the working directory and graph

```bash
test -f package.json || { echo "accessibility-audit: no package.json detected. This skill currently supports React projects only."; exit 1; }
test -f graphify-out/graph.json && echo "graphify: knowledge graph present" || echo "graphify: knowledge graph missing — run /pre-audit-setup first for richer context"
```

The skill does not require the knowledge graph, but the component-pattern layer is significantly more accurate when god nodes and component communities are available. Without the graph, sample component files broadly and report reduced confidence in `metadata.json`.

### Step 2 — Detect React and the framework variant

Read `package.json`. Resolve the framework variant from dependencies and directory layout:

- `next` dependency plus `app/` directory → Next.js App Router.
- `next` dependency plus `pages/` directory → Next.js Pages Router.
- `@remix-run/*` dependency → Remix.
- `vite` plus `react` → Vite-React.
- `react-scripts` → Create React App.
- `react` only → plain React. Application-shell checks degrade to "framework-agnostic" expectations.

Record the variant in `metadata.json`.

### Step 3 — Resolve each check

For each check in the active layer list, walk its detection signals:

- **All required signals resolve and no contradicting code is found →** `present`.
- **Some signals resolve →** `partial`.
- **No signals resolve →** `missing`.
- **Contradicting code is found →** `violation`. Always include at least one file reference.

Record every matching path in the `evidence` array. For component-pattern checks, also record a `samples` array of up to ten representative file references — never an exhaustive list.

### Step 4 — Write phase 1 outputs

Create `audits/accessibility-audit/` if it does not exist. Write the three files. If a previous run exists, overwrite `findings.md`, `findings.json`, and `metadata.json`. `implementation-plan.md` is preserved unless the user agrees to regenerate it.

### Step 5 — Print the chat summary and offer phase 2

In the chat session, print a short table that mirrors the three layers, with a count of present, partial, missing, and violation per layer. Highlight the top three violations by severity. Then ask the user the single yes-or-no question described above. Do not proceed to phase 2 without an explicit affirmative.

### Step 6 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, framework variant, timestamp, total counts per layer.
2. **Tooling layer plan**, in order:
   - Packages to install with the exact command for the detected package manager.
   - Configuration files to create or modify with full content snippets.
   - One-line note on why the check matters.
3. **Component-patterns layer plan**, ordered by severity (violations first, then missing). For each item:
   - The pattern at issue.
   - One representative file and line reference.
   - The recommended replacement, with a short code snippet.
   - A note on whether this is a one-off fix or a codebase-wide pattern that should be addressed with a search-and-replace pass.
4. **Application-shell layer plan**, in order:
   - The shell element to add or change.
   - The exact file to edit (informed by the framework variant).
   - The full content snippet.
5. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not install packages and it does not modify components.

## Findings file shape

`findings.json` has this top-level structure:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:18Z",
  "framework": "next-app-router",
  "wcagTarget": "2.2-AA",
  "summary": {
    "tooling":              { "present": 2, "partial": 1, "missing": 3, "violation": 0 },
    "componentPatterns":    { "present": 3, "partial": 2, "missing": 0, "violation": 4 },
    "applicationShell":     { "present": 5, "partial": 0, "missing": 2, "violation": 1 }
  },
  "checks": [
    {
      "layer": "component-patterns",
      "check": "semantic-html-over-generic-containers",
      "status": "violation",
      "evidence": [],
      "samples": [
        "src/components/RowMenu.tsx:42",
        "src/components/Toolbar.tsx:118",
        "src/features/Inbox/MessageRow.tsx:67"
      ],
      "expectation": "Interactive elements use the right semantic tag (button, a, input).",
      "gap": "12 occurrences of a div with onClick and no keyboard handler or tabIndex.",
      "remediation": "Replace with <button type=\"button\"> when the action stays in-page; with <a> when navigating; if the element must remain a div, add role, tabIndex, and a keyboard handler."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, grouped by layer with one section per check.

`metadata.json`:

```json
{
  "skillName": "accessibility-audit",
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:18Z",
  "graphifyRevision": "<hash from graphify-out/metadata if present>",
  "framework": "next-app-router",
  "wcagTarget": "2.2-AA",
  "filtersApplied": { "layers": ["tooling", "component-patterns", "application-shell"], "include": [], "exclude": [], "severity": null }
}
```

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filters (`--layer`, `--include`, `--exclude`, `--severity`) are recorded in `metadata.json` so a partial run can be reproduced.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is being run outside a Node.js project root. | Change directory into the project root and re-run. |
| React not detected | The project does not depend on `react` or any meta-framework that brings it. | Stop and inform the user. The skill currently supports React only. |
| Framework variant ambiguous | Both `next` and a custom Vite configuration are present. | Mark the framework as `mixed` in `metadata.json` and degrade application-shell checks to framework-agnostic expectations. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue, but tag the run with `noGraphify: true` in `metadata.json`. Component-pattern checks operate on a broad sample rather than graph-guided entry points. |
| Design tokens not statically defined | Colour and contrast cannot be checked without runtime. | Mark the colour-and-contrast check as `partial` with the gap "tokens defined dynamically; static contrast verification not possible". The implementation plan recommends adding a contrast linter or a runtime axe scan. |
| Storybook addons configured but Storybook itself missing | Stale dependency. | Mark the Storybook accessibility addon check as `missing` with a gap explaining the inconsistency. |

## What this skill explicitly does NOT do

- Start the development server, run a live axe scan, or interact with a real browser. The audit is fully static.
- Verify screen-reader behaviour. That requires a human and assistive technology.
- Verify focus order in a rendered application. The audit can flag missing focus management primitives, but cannot replay a tab sequence.
- Install any package or dependency.
- Create, modify, or delete any file outside `audits/accessibility-audit/`.
- Modify components, configuration, or routes.
- Open pull requests or commit anything to git.
- Audit any framework other than React. Vue, Svelte, Angular, and Solid are out of scope for this version.
