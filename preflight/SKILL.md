---
name: preflight
description: Detect optional enrichment tooling for architect-playbook audits and optionally install missing development dependencies and scaffold project configuration. Read-only by default; mutation is gated behind --install and --scaffold-configs and prompts before every change.
trigger: /preflight
---

# /preflight

A pre-audit readiness check. Several audits in the playbook accept optional enrichment flags (`--with-stats`, `--with-run`, `--with-network`, `--with-lighthouse-results`, `--with-scan`, `--with-link-check`) that depend on tooling being present in the target project. Running an audit, discovering the tooling is missing, installing it, then re-running the audit is wasteful — `/preflight` consolidates that into one pre-step.

Run `/preflight` once before launching audits. The default mode is read-only: it prints a status table for every relevant tool and writes findings to `.architect-audits/preflight/`. With explicit flags, it can install missing development dependencies and scaffold minimal project configuration, prompting before every change.

This skill is also the canonical reference for any audit's preflight needs. Audits do not duplicate the detection logic; they link here from a Pro-tip line in their intro, the same way they link to `/worktree` and `/pre-audit-setup`.

## Usage

```
/preflight                                # detect across every audit; report only
/preflight --audit=<name>                 # scope to one audit (lenient prefix match)
/preflight --install                      # prompt and install missing development dependencies
/preflight --scaffold-configs             # prompt and scaffold missing project configuration
/preflight --install --scaffold-configs   # both
```

`--audit=<name>` accepts the same forms as `/worktree`:

- The full skill name: `security-audit`.
- The short form: `security`.
- An unambiguous prefix: `sec` (resolves to `security-audit` if no other audit starts with `sec`).

`--install` and `--scaffold-configs` are independent. Installs without scaffolding is the common case (the user already has custom configuration); scaffolding without installs is rare but legal. Both default to off. Both prompt before every change. Neither runs in a CI environment (see Step 3).

## What this skill does

For each audit that supports enrichment, `/preflight` checks the target project for the development dependency and the project configuration that make the enrichment flag useful. It prints a single status table and writes the findings, then — only when the corresponding flag is set — prompts to install or scaffold what is missing.

| Audit | Enrichment flag | Development dependency detected | Project configuration detected (read-only) |
| --- | --- | --- | --- |
| bundle-build-audit | `--with-stats` | `webpack-bundle-analyzer`, `@next/bundle-analyzer`, `rollup-plugin-visualizer`, or `vite-bundle-visualizer`, whichever matches the build tool | Plugin reference in `webpack.config.*`, `next.config.*`, `vite.config.*`, or `rollup.config.*` |
| typescript-audit | `--with-run` | `typescript` | `tsconfig.json` exists |
| testing-audit | `--with-run` | `vitest` or `jest` | Coverage configuration block in `vitest.config.*` or `jest.config.*` |
| dependency-audit | `--with-network` | None — the package manager's audit subcommand is built in | n/a; confirms the package manager is detected |
| performance-audit | `--with-lighthouse-results` | `lighthouse` (as a development dependency, not the system CLI) | `.lighthouserc.json` or `.lighthouserc.js` |
| security-audit | `--with-scan` | `eslint-plugin-security`, `eslint-plugin-no-unsanitized`, `eslint-plugin-react-security` | Plugins listed in `.eslintrc*` or `eslint.config.*` |
| linting-audit | `--with-run` | `eslint` or `@biomejs/biome` | A linter configuration file is present |

Audits without enrichment flags — `react-audit`, `error-handling-audit`, `accessibility-audit`, `quality-gates-audit`, `architecture-audit`, `documentation-audit` — are not covered. `documentation-audit` accepts `--with-link-check`, but the check uses `curl` only; nothing is installable.

V1 deliberately treats project-configuration wiring as a read-only check that feeds the status column. It does not block installs. Audits already report wiring as a finding (for example, `bundle-build-audit` Layer 3 "Bundle analyser available"); that channel is left intact.

### Status enum

Each row is one of:

- `present` — the development dependency is in `dependencies` or `devDependencies` of the target's `package.json`, and (if applicable) the project configuration is wired up.
- `installed-but-not-wired` — the development dependency is installed, but the project configuration that makes it useful is missing.
- `missing` — the development dependency is not in the target's `package.json`.
- `built-in` — no install needed (e.g. `--with-network` uses the package manager's audit subcommand).

The status table is printed sorted: `missing` first, `installed-but-not-wired` second, `built-in` third, `present` last. Users want the actionable rows at the top.

## Implementation steps

Follow these steps in order. Stop at the first hard refusal (Steps 1, 2, 3) and report.

### Step 1 — Resolve `--target` and refuse monorepo workspace roots

```bash
target="${target:-.}"
test -d "$target" || { echo "/preflight: --target=$target is not a directory"; exit 1; }
```

Refuse to operate when the target appears to be a monorepo workspace root rather than a single project. Detection:

- `pnpm-workspace.yaml` exists at the target root, OR
- the target's `package.json` has a top-level `workspaces` field, OR
- `nx.json` exists at the target root, OR
- `turbo.json` exists at the target root,

AND the target has no adjacent `src/` directory or single-package `package.json` with a `name` field clearly identifying it as a leaf package.

When a workspace root is detected, print a clear message naming the discovered workspaces and ask the user to pass `--target=<path>` pointing at one of them. Do not pick one automatically.

### Step 2 — Detect the package manager from the lockfile

```bash
if   [ -f "$target/pnpm-lock.yaml" ];     then pm=pnpm
elif [ -f "$target/package-lock.json" ];  then pm=npm
elif [ -f "$target/yarn.lock" ];          then pm=yarn
elif [ -f "$target/bun.lockb" ];          then pm=bun
else echo "/preflight: no lockfile found at $target. Run your package manager's install first."; exit 1
fi
```

Refuse if no lockfile is present. The user can run their package manager's install command first.

### Step 3 — Detect read-only or CI environments

If `process.env.CI === 'true'`, or the target is not writable, or the user is running inside a known CI runner, force `--install` and `--scaffold-configs` off and print a one-line explanation. The detection still runs and findings are still written; mutation is the only thing suppressed.

### Step 4 — Resolve `--audit` and read the target's `package.json`

If `--audit=<name>` is set, resolve it via the same matching ladder as `/worktree`: exact match, then `<arg>-audit` match, then unique prefix match across the audits listed in the table above. On ambiguity, print the candidates and ask. With no `--audit` flag, all rows in the table are in scope.

Read `<target>/package.json`. Build the lookup of installed packages from the union of `dependencies` and `devDependencies`. If `package.json` is missing, refuse with a clear message.

### Step 5 — Compute the status of each row

For each in-scope row:

- **Development dependency check.** Look up each candidate package name in the lookup from Step 4. For rows with multiple candidates (e.g. bundle-build's four bundle-analyzer packages), the row is `present` if any candidate matches.
- **Project configuration check.** Read the target's relevant configuration file(s) listed in the table. A simple regex against the file content is sufficient — the goal is to inform the status column, not to deeply analyse the configuration.

Set the row status using the rules in the Status enum section above.

### Step 6 — Print the status table

Always print the table, sorted by status (`missing`, `installed-but-not-wired`, `built-in`, `present`). Each row shows the audit, the enrichment flag, the candidate development dependency, the configuration file checked, and the status. After the table, print a one-line summary count per status.

### Step 7 — Install missing development dependencies (only if `--install`)

If `--install` is set and any rows are `missing`, prompt per row:

> Install `<package-name>` as a development dependency for `<audit>`'s `--with-<flag>` enrichment? (yes/no)

On `yes`, run the install command for the detected package manager:

| Package manager | Install command |
| --- | --- |
| npm | `npm install --save-dev <pkg>` |
| pnpm | `pnpm add -D <pkg>` |
| yarn | `yarn add --dev <pkg>` |
| bun | `bun add -d <pkg>` |

Capture stderr. On any non-zero exit (peer-dependency conflict, registry error, anything), report the error verbatim, do not retry, and do not partial-install silently. Continue to the next prompt; the failure is recorded in `findings.json` for the affected row.

For rows with multiple candidates (e.g. bundle-build's four bundle analysers), do not pick one. Instead, print the candidates and ask the user which one to install — or `none` to skip. Picking the right bundle analyser depends on the build tool, and `/preflight` does not introspect the build configuration to that depth in V1.

### Step 8 — Scaffold missing project configuration (only if `--scaffold-configs`)

If `--scaffold-configs` is set and any rows have a missing configuration file, prompt per file. Show the proposed file content as a dry-run diff against an empty file. On `yes`, write the file. **Never overwrite an existing file** — only create.

The minimal scaffolds:

- `.lighthouserc.json` — a single-URL configuration pointing at `http://localhost:3000` with the four Core Web Vitals categories enabled.
- `.eslintrc.cjs` plugin entries — only when `eslint-plugin-security` (or any other detected security plugin) is installed but not listed; appended to the `plugins` array. Refuse to write if the project uses flat `eslint.config.*` instead — flat-config edits are too project-specific to scaffold safely; surface the relevant snippet for the user to paste.
- Bundle-analyzer wiring is **not** scaffolded in V1. Build-tool-specific edits to `webpack.config.*`, `next.config.*`, `vite.config.*`, or `rollup.config.*` vary too widely. The preflight prints the recommended snippet per build tool and lets the user paste it.

### Step 9 — Write the findings artefacts

Write to `<target>/.architect-audits/preflight/`:

- `findings.md` — human-readable report containing the status table, the per-row detail, and a "next steps" block listing exactly which install commands or configuration edits were performed (if any).
- `findings.json` — machine-readable list of rows. Each entry includes `audit`, `flag`, `candidates[]`, `configurationFile`, `status`, `installAction` (`null` | `installed` | `failed` | `declined` | `skipped-multi`), `scaffoldAction` (`null` | `created` | `declined` | `not-supported`), and the final exit code of any subprocess.
- `metadata.json` — skill version, run started/finished timestamps, detected package manager, lockfile path, target absolute path, audits scoped, environment flags (`ci: true|false`, `readOnly: true|false`), counts of `missing` / `installed-but-not-wired` / `built-in` / `present` rows.

These artefacts follow the playbook's findings-file contract so downstream skills can read them.

### Step 10 — Print the next-step hint

If any rows are still `missing` and `--install` was not passed, print:

```
[hint] re-run with --install to install the missing development dependencies, prompted per package.
```

If any rows are `installed-but-not-wired` and `--scaffold-configs` was not passed, print the equivalent hint for `--scaffold-configs`. Otherwise print:

```
[hint] preflight clean. Now run /<audit> --with-<flag> in a separate chat.
```

## Idempotency rules

- Re-running with the same flags must produce the same findings file content (modulo timestamps in `metadata.json`).
- Never re-prompt for an already-installed package, an already-scaffolded file, or an already-wired plugin entry.
- Never overwrite an existing configuration file. Only create.
- Never duplicate an entry inside `eslint.config.*` plugins. The check before append is "exact string match against the existing plugins array".

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `/preflight: no lockfile found` | Target project has no `package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, or `bun.lockb`. | Run the package manager's install (e.g. `npm install`) at the target first. |
| `/preflight: --target=<path> is not a directory` | The path passed in `--target` does not exist. | Pass an existing project root. |
| Workspace-root refusal | Target appears to be a monorepo root. | Re-run with `--target=<path>` pointing at one of the workspace packages listed in the refusal message. |
| Install fails with a peer-dependency conflict | The candidate package's peer dependencies clash with what the target already has. | Read the package manager's verbatim error from `findings.json`. Either install manually with the appropriate flag (e.g. `--legacy-peer-deps`) or pick a different candidate (e.g. `vite-bundle-visualizer` instead of `webpack-bundle-analyzer` if the target uses Vite). |
| `--scaffold-configs` declined to write `.eslintrc` because the project uses flat config | Flat-config edits are deliberately not scaffolded; too project-specific. | Paste the snippet `/preflight` printed into your `eslint.config.*` manually. |
| `--install --scaffold-configs` were silently ignored | A CI environment or non-writable target was detected at Step 3. | Re-run on a developer workstation. |

## What this skill explicitly does NOT do

- **Install global CLIs.** Tools normally installed with `npm install -g` or a system package manager (semgrep, the Lighthouse CLI as a global binary, the Snyk CLI) are out of scope. The preflight does not touch the system PATH.
- **Sign up for or configure external services.** Accounts on Snyk, Sentry, or any third-party service are not detected, created, or configured.
- **Mutate `package.json` without `--install`.** The default mode is strictly read-only against the target.
- **Write project configuration without `--scaffold-configs`.** Configuration scaffolding is a separate explicit gate from installs because the user may want one without the other.
- **Run any audit.** The audit itself remains a separate command, run in a separate chat. The preflight only prepares the target.
- **Require `/pre-audit-setup`.** The preflight does not need the graphify knowledge graph. Running `/pre-audit-setup` and `/preflight` in either order is fine.
- **Pick the right bundle analyser automatically.** The four candidates (`webpack-bundle-analyzer`, `@next/bundle-analyzer`, `rollup-plugin-visualizer`, `vite-bundle-visualizer`) map to four different build tools, and detecting the build tool reliably enough to pick is out of scope for V1. The preflight asks the user.
- **Edit build configuration.** Wiring `webpack-bundle-analyzer` into `webpack.config.*` or its peers requires build-tool-specific knowledge that does not belong in a generic preflight. The relevant snippet is printed for the user to paste.
- **Commit anything to git.** The user commits the resulting `package.json` and lockfile change manually, with a Conventional Commits message such as `chore: add development dependencies for bundle-build-audit enrichment`.
