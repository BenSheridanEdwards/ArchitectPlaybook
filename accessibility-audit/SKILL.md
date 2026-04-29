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
/accessibility-audit                              # default: concise Top 5 + full report saved + ask about plan
/accessibility-audit --learn                      # mid-level engineer teaching mode (detailed explanations + file/line examples)
/accessibility-audit --teach                      # alias for --learn
/accessibility-audit --severity=error             # report only violations and missing-required checks
```

**💡 Pro tip**: Use `/worktree accessibility` to run this in an isolated worktree.

This skill never accepts `--apply`. Mutating the codebase is the responsibility of a separate fix step. The implementation plan is descriptive Markdown.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the check is fully satisfied; the audit found everything it expected.
- **partial** — some required signals resolved, others did not. The check is half-implemented.
- **missing** — the tooling, configuration, or pattern is not present at all.
- **violation** — the audit found code that actively contradicts the check (for example, an interactive `<div>` with no keyboard handler).

`missing` and `violation` are distinct on purpose. `missing` means "the safety net isn't in place"; `violation` means "the safety net is missing _and_ there is concrete code that the safety net would have caught."

### Layer 1 — Tooling and automation

| Check                              | Expectation                                                                                                                                           | Primary detection signals                                                                                                                                                                                                                      |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| jsx-accessibility lint plugin      | `eslint-plugin-jsx-a11y` is installed and the `recommended` (or `strict`) ruleset is enabled in the active ESLint configuration.                      | `package.json` devDependency on `eslint-plugin-jsx-a11y`; `extends` entry in `eslint.config.*` or `.eslintrc*` referencing `plugin:jsx-a11y/recommended` or `plugin:jsx-a11y/strict`; no file-level or directory-level disables of the plugin. |
| Axe in development                 | `@axe-core/react` is initialised in development mode so violations are logged to the browser console during local work.                               | devDependency on `@axe-core/react`; an entry-point file (typically `src/main.tsx`, `src/index.tsx`, or `pages/_app.tsx`) imports it and calls `axe(React, ReactDOM, ...)` behind a development guard.                                          |
| Component-test integration         | `jest-axe` or `vitest-axe` is available, and at least one component test asserts `expect(...).toHaveNoViolations()`.                                  | devDependency on `jest-axe` or `vitest-axe`; at least one test file imports the matcher and uses it.                                                                                                                                           |
| End-to-end accessibility scan      | `@axe-core/playwright` (or the Cypress equivalent) is configured, and at least one end-to-end specification runs an axe scan against a rendered page. | devDependency on `@axe-core/playwright` or `cypress-axe`; a spec file imports and invokes the scanner.                                                                                                                                         |
| Storybook accessibility addon      | If Storybook is in use, `@storybook/addon-a11y` is registered.                                                                                        | Storybook configuration file (`.storybook/main.*`) lists the addon in `addons`. Skipped silently if Storybook is not detected.                                                                                                                 |
| Continuous integration enforcement | A continuous-integration workflow step runs the unit and end-to-end accessibility checks and fails the build on violations.                           | Workflow file under `.github/workflows/`, `.gitlab-ci.yml`, or equivalent contains a step invoking the relevant test command.                                                                                                                  |

### Layer 2 — Component patterns

These checks require reading components. Use the knowledge graph to find the right entry points (god nodes for component clusters), then sample broadly. The audit reports counts and representative file references, not an exhaustive list.

| Check                                 | Expectation                                                                                                                                                                                                                            | What counts as a violation                                                                                                                                                                                                                                                                                                                 |
| ------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Semantic HTML over generic containers | Interactive elements use the right tag (`<button>`, `<a>`, `<input>`, `<select>`); lists use `<ul>`, `<ol>`, or `<dl>`.                                                                                                                | A `<div>` or `<span>` with an `onClick` and no `role`, no keyboard handler, and no `tabIndex`. A list-shaped UI rendered with `<div>` children. A clickable item that should be a `<button>` or `<a>`.                                                                                                                                     |
| ARIA used correctly                   | ARIA attributes appear only on elements that support them, are not redundant, and are present where required.                                                                                                                          | `aria-*` on an element where it has no effect (for example, `aria-checked` on a `<div>` without `role="checkbox"`). Redundant ARIA (`role="button"` on `<button>`). Icon-only buttons with no `aria-label` or `aria-labelledby`. Disclosure triggers without `aria-expanded`. Modals without `aria-modal="true"` and a labelling strategy. |
| Keyboard support                      | Every interactive element is reachable by Tab and operable by keyboard. Focus is visible.                                                                                                                                              | `outline: none` (or `outline: 0`) without a replacement focus style. Custom widgets that do not implement the WAI-ARIA Authoring Practices keyboard model. Keydown handlers that block default behaviour without re-implementing it.                                                                                                       |
| Focus management                      | Modals trap focus and restore on close; route changes move focus to a sensible target; a skip-to-content link is present and is the first focusable element.                                                                           | A modal component without focus trap and without focus restoration. A custom router with no focus handling on navigation. Auto-focus on page load on a non-form-first page.                                                                                                                                                                |
| Forms                                 | Every input has an associated `<label>`. Errors are programmatically associated with their fields. Required fields are indicated for both screen readers and sighted users. Grouped controls use `<fieldset>` and `<legend>`.          | An `<input>` whose only label is a placeholder. An error message that is rendered visually but not tied to its field by `aria-describedby`. A required field marked only with an asterisk. A radio or checkbox group without `<fieldset>`/`<legend>`.                                                                                      |
| Images and media                      | Every `<img>` has an `alt` attribute (empty `alt=""` is acceptable for purely decorative images). SVG icons used as content have `<title>` or `aria-label`. Videos have captions. Auto-playing media is muted and has a pause control. | `<img>` without `alt`. `<svg>` used as a content icon with no accessible name. `<video autoplay>` without `muted` and without a pause control.                                                                                                                                                                                             |
| Colour and contrast                   | Text colours meet WCAG AA contrast against their backgrounds. Information is never conveyed by colour alone.                                                                                                                           | When design tokens are statically defined (CSS custom properties, Tailwind theme, theme objects), text/background pairings that fall below 4.5:1 (or 3:1 for large text). UI states differentiated only by colour (for example, an error indicated only by red text).                                                                      |
| Motion and animation                  | Long animations respect `prefers-reduced-motion`. No essential information is delivered through animation alone.                                                                                                                       | Animations longer than five seconds with no pause control. Global stylesheets with no `@media (prefers-reduced-motion: reduce)` block. Auto-rotating carousels without pause.                                                                                                                                                              |
| Text and content structure            | Headings are in document order with no skipped levels. Link text is descriptive in isolation.                                                                                                                                          | A page that jumps from `<h1>` to `<h3>` with no `<h2>`. Multiple `<h1>` per page (outside an `<article>` context that warrants it). Link text such as "click here", "read more", or a bare URL.                                                                                                                                            |

### Layer 3 — Application shell

| Check                     | Expectation                                                                                                                                                            | Primary detection signals                                                                                                                                                            |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| Document language         | `<html lang="...">` is set to a real language code.                                                                                                                    | The HTML shell (`index.html`, `app/layout.tsx`, `pages/_document.tsx`, `app/root.tsx`) sets a non-empty `lang`.                                                                      |
| Per-route document titles | Document title updates on every navigation.                                                                                                                            | Use of the framework's title primitive (`<title>` in Next.js metadata, `<Helmet>` in Vite-React, `meta` exports in Remix) on every routed view.                                      |
| Skip-to-content link      | A skip link to the main landmark exists and is the first focusable element on every page.                                                                              | An `<a href="#main">` (or equivalent) rendered in the application shell, styled to be visible on focus.                                                                              |
| Route announcement        | Client-side navigations are announced to assistive technology.                                                                                                         | A live region in the shell, or use of the framework-provided announcer (Next.js `app/` router's `RouterAnnouncer`, Remix's built-in announcement, or a custom `aria-live` region).   |
| Viewport meta             | The viewport meta tag is present and does not disable scaling.                                                                                                         | `<meta name="viewport" content="width=device-width, initial-scale=1">`. The check fails if `user-scalable=no` or `maximum-scale=1` appears.                                          |
| Landmark roles            | The application shell renders the standard landmarks once per page: `banner`, `main`, `contentinfo`. `navigation` and `complementary` appear when the design has them. | The shell uses `<header>`, `<main>`, `<footer>`, `<nav>` (or the explicit `role` equivalents). No duplicate landmarks of the same role without an `aria-label` differentiating them. |
| Error and not-found pages | Custom error and 404 pages have a heading, manage focus, and offer a way back to the main application.                                                                 | Presence of an error route (`app/not-found.tsx`, `app/error.tsx`, or framework equivalent). Heading present. A link or button that returns to a known landing route.                 |
| Authentication pages      | Sign-in and sign-up forms support password-manager autofill, declare input purposes, and do not trap users in modal flows that break with a screen reader.             | Inputs use the correct `autoComplete` values (`username`, `current-password`, `new-password`, `email`). Forms are not nested inside non-dismissible modals.                          |

## What this skill does

1. **Reads the knowledge graph first.** If `graphify-out/graph.json` exists, read `graphify-out/GRAPH_REPORT.md` to identify component clusters, the application shell, and route entry points before searching raw files.
2. **Confirms a React project.** Detects React via `package.json` dependencies. If absent, the skill stops and tells the user it currently supports React only.
3. **Detects the framework variant** and records it in `metadata.json`.
4. **Walks every check** across the three layers. Records a status per check and representative file references.
5. **Writes phase 1 outputs** to `.architect-audits/accessibility-audit/` (findings.md, findings.json, metadata.json, snapshot.md).
6. **Phase 2** — prints the concise Top 5 recommendations in chat and asks whether to generate an implementation plan.

## Implementation steps

### Step 1 — Read the knowledge graph

If `graphify-out/graph.json` exists, read `graphify-out/GRAPH_REPORT.md` to identify component clusters and their god-node entry points, the application shell (root layout or document wrapper), and route entry points. Use these to guide targeted file reads in Steps 3 and 4 rather than walking the full tree blindly.

If the graph does not exist, fall back to heuristic discovery: `find src -name "*.tsx"`, locate `package.json`, and look for the HTML shell and CI configuration files directly.

### Step 2 — Confirm React and detect the framework variant

Check `package.json` for `react` in `dependencies` or `devDependencies`. If React is absent, stop and tell the user this skill currently supports React projects only.

Detect the framework variant and record it in `metadata.json`:
- **Next.js** — `next` in dependencies; presence of `app/` or `pages/` directory.
- **Remix** — `@remix-run/react` in dependencies.
- **Vite-React** — `vite` + `react` without Next.js or Remix markers.
- **Create React App** — `react-scripts` in dependencies.

Emit framework-specific hints throughout the audit where relevant: route-announcement expectations, document-title primitives, and error-page conventions differ across frameworks.

### Step 3 — Walk Layer 1 (tooling and automation)

Check each Layer 1 item against `package.json`, ESLint configuration files, test files, and CI workflow files. For each check, record a status (`present` / `partial` / `missing` / `violation`) and up to three representative file references.

Checks to walk (see baseline tables above for detection signals):
- jsx-accessibility lint plugin
- Axe in development
- Component-test integration
- End-to-end accessibility scan
- Storybook accessibility addon (skip silently if Storybook is absent)
- Continuous integration enforcement

### Step 4 — Walk Layer 2 (component patterns) and Layer 3 (application shell)

**Layer 2** requires reading component source files. Use the knowledge-graph god nodes as entry points; sample broadly but do not read every file exhaustively. For each check, record a status, a count of violations found, and up to three representative `file:line` references.

**Layer 3** requires reading the HTML shell, root layout, and framework-specific entry points. Record status and representative file references for each check.

When `--severity=error` is active, record only checks with status `violation` or `missing`. Skip `partial` and `present`.

### Step 5 — Write phase 1 outputs

Write four files to `.architect-audits/accessibility-audit/`:

- `findings.md` — full human-readable layered report, one row per check, with status and representative references.
- `findings.json` — machine-readable equivalent for downstream skills.
- `snapshot.md` — key diagnostic facts: React version, framework variant, Storybook present or absent, CI provider detected.
- `metadata.json` — skill name, version, run timestamp, graphify revision hash (if available).

Do not print the full findings in chat.

### Step 6 — Print the concise chat summary and offer phase 2

Print a human-first, scannable summary in the chat (the Top 5 recommendations). The full layered findings are written to disk only. When `--learn` or `--teach` is used, expand into mid-level engineer teaching mode with specific file and line references.

After printing, ask:  
_"Generate an implementation plan for the gaps identified above? (yes/no)"_

Do not proceed to phase 2 without an explicit affirmative.

## What this skill explicitly does NOT do

- **Does not start the development server.** The entire audit is static.
- **Does not run a live accessibility scanner.** It never invokes axe-core, Playwright, or any browser runtime against a running application.
- **Does not verify screen-reader behaviour.** Announced text, reading order, and virtual-cursor navigation can only be verified by a human with assistive technology.
- **Does not verify focus order by interaction.** Tab-order correctness under real keyboard conditions is out of scope.
- **Does not mutate the codebase.** This skill never accepts `--apply`. All outputs are descriptive Markdown and JSON. A separate fix step is responsible for code changes.
- **Does not audit non-React frontends.** Vue, Svelte, Angular, and plain HTML projects are not supported. The skill stops and says so if React is absent.
- **Does not compute contrast ratios for runtime-generated colours.** It can only evaluate statically defined design tokens (CSS custom properties, Tailwind theme values, theme objects). Dynamic colour computation requires a browser.
- **Does not produce an implementation plan without explicit confirmation.** Phase 2 requires the user to answer "yes" at the end of Step 6.
