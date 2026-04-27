---
name: dependency-audit
description: Audit Node.js dependency tree (security, health, compliance, hygiene). Static-first with optional --with-network enrichment for vulnerabilities, outdated, and abandonment data. Optionally generates an implementation plan.
trigger: /dependency-audit
---

# /dependency-audit

Audit a Node.js project's dependency tree against an opinionated baseline organised in four layers — **security**, **health**, **compliance**, **hygiene** — preceded by a diagnostic snapshot. Then offer to generate an implementation plan for the gaps.

The default mental model is TypeScript and React. The skill works on any Node.js project that uses one of the recognised package managers: npm, pnpm, yarn, or bun.

## Static-first design with optional network enrichment

Vulnerability data, "is this package outdated", and "is this package abandoned" all need information that does not live in the repository. This skill is read-only and never installs, updates, or modifies anything. It runs in two modes:

- **Static (default).** Read `package.json`, the lockfile, and (when present) the `package.json` of each installed package under `node_modules/`. Security and outdated checks degrade with a clear "needs `--with-network`" gap.
- **Static plus opt-in `--with-network`.** When the flag is passed, the skill additionally runs the package manager's own read-only audit and outdated commands (`npm audit --json`, `npm outdated --json`, or pnpm/yarn/bun equivalents) and parses their output. No install, no update, no lockfile rewrite — only the read-only registry queries the package manager already exposes.

Running `npm install`, `npm update`, or any mutating operation is the responsibility of the user or a separate fix-and-validate skill. Keeping this audit fully read-only is what makes it safe to run in any working tree at any time.

## The three input tiers

The audit's accuracy scales with what is available. Each check declares which tier it needs; tier-dependent checks degrade with a clear gap rather than silently passing or failing.

| Tier | What is read | What it unlocks |
| --- | --- | --- |
| 1 — Lockfile only | `package.json`, lockfile (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`, or `bun.lockb`) | Versions, transitive tree, duplicates, misplaced dependencies, lockfile hygiene. License and abandonment are unknown. |
| 2 — Lockfile + `node_modules` (default static run) | Everything in tier 1 plus the `package.json` of each installed package | Adds license fields, install scripts, disk size per package. |
| 3 — Lockfile + `node_modules` + network (`--with-network`) | Everything above plus registry queries via the package manager's own audit and outdated commands | Adds vulnerabilities, outdated versions, abandonment signals, deprecation messages. |

The skill detects which tier is available and records it in the diagnostic snapshot. There is no automatic install: if `node_modules` is absent, the audit runs at tier 1 and tier-2 checks degrade.

## Usage

```
/dependency-audit                                              # full two-phase static run (tier 1 or 2)
/dependency-audit --with-network                               # tier 3: enrich with audit and outdated registry data
/dependency-audit --report-only                                # phase 1 only: write findings, do not ask about a plan
/dependency-audit --plan                                       # skip phase 1 if findings already exist; jump to plan generation
/dependency-audit --layer=security                             # restrict the audit to a single layer (repeatable)
/dependency-audit --layer=health
/dependency-audit --layer=compliance
/dependency-audit --layer=hygiene
/dependency-audit --include=<check-name>                       # include only the named check (repeatable)
/dependency-audit --exclude=<check-name>                       # skip the named check (repeatable)
/dependency-audit --threshold-major-versions-behind=3          # override default 2
/dependency-audit --threshold-abandonment-months=18            # override default 24
/dependency-audit --security-critical-packages=react,next,@remix-run/react   # override the default critical-package list
/dependency-audit --target=<path>                                            # operate on this directory instead of cwd (default: cwd)
```

The skill never accepts `--apply`. The implementation plan is descriptive Markdown.

The defaults baked into the skill are the recommended baseline. Threshold flags exist as an escape hatch; the canonical path to evolving the defaults themselves is `/system-self-improve`.

When `--target=<path>` is set, the skill operates on that path instead of the current working directory. File reads, file globbing, regex searches, and any subprocess commands (including `npm audit` and `npm outdated` under `--with-network`) all run scoped to the target. Findings land at `<target>/audits/dependency-audit/`. The default is the current working directory. This is the building block that lets `/worktree <name>` create a worktree and audit it from a chat opened elsewhere — all in one chat.

**💡 Pro tip**: Use `/worktree dependency` to run this against a Git worktree (creates the worktree and runs the audit in this same chat). Useful for branch isolation and for running multiple audits in true parallel across separate chats.

**💡 Pro tip**: Run `/preflight --audit=dependency` first to confirm the package manager and lockfile are detected — `--with-network` calls the package manager's audit subcommand (`npm audit`, `pnpm audit`, `yarn audit`, or `bun pm audit`), so no install is needed, but a missing or unrecognised lockfile will silently degrade the enrichment.

## The opinionated baseline

A check resolves to one of four statuses:

- **present** — the invariant holds.
- **partial** — most signals resolve, with a small number of exceptions, or the check needs a higher tier than what the run provided.
- **missing** — a structural prerequisite is absent.
- **violation** — concrete evidence in the project breaks the invariant.

Layer 0 is informational only and has no status.

### Layer 0 — Diagnostic snapshot (always written, no pass/fail)

- Detected package manager and version (npm 10.x, pnpm 9.x, yarn 4.x, bun 1.x).
- Lockfile path and last-modified timestamp.
- `node_modules` present: yes/no.
- Counts: direct dependencies, dev dependencies, transitive total.
- Tree depth statistics: deepest path, average depth.
- Top 10 largest direct dependencies on disk (when `node_modules` present).
- Duplicate package count: same package name, multiple resolved versions in the lockfile.
- Workspace count when the project is a monorepo.
- Tier-of-input the audit ran with (1, 2, or 3).

### Layer 1 — Security

| Check | Tier | Expectation | Violation signal |
| --- | --- | --- | --- |
| No high or critical vulnerabilities | 3 | The package manager's audit reports no high or critical advisories. | Any high or critical advisory. Reported with package, severity, advisory ID, and fix availability. |
| No moderate vulnerabilities | 3 | Soft check. Moderate advisories report as `partial` rather than `violation`. | Moderate advisories present. |
| Lockfile present and committed | 1 | A lockfile exists and is tracked by git (not in `.gitignore`). | No lockfile, or lockfile in `.gitignore`. |
| Lockfile integrity verifiable | 1 | The lockfile carries integrity hashes and continuous integration uses the frozen-install variant (`npm ci`, `pnpm install --frozen-lockfile`, `yarn install --immutable`, `bun install --frozen-lockfile`). | Lockfile in a format without integrity hashes; or continuous-integration scripts not using the frozen-install variant. |
| No install scripts from untrusted packages | 2 | Direct dependencies that declare `postinstall` (or other lifecycle) scripts are limited to a known-trusted allowlist (typing definitions, build tools, framework-installed essentials such as `husky`, `playwright`, `puppeteer`, `esbuild`, `sharp`). | Direct-dependency lifecycle scripts present from packages outside the allowlist. Reported with the package, the script, and the script body. |
| Continuous-integration vulnerability scanning enabled | 1 | A continuous-integration workflow runs the package manager's audit, or Dependabot/Snyk/Renovate configuration is present. | No such automation detected (no `dependabot.yml`, no `renovate.json`/`renovate.json5`, no `npm audit` step in any workflow). |

### Layer 2 — Health

| Check | Tier | Expectation | Violation signal |
| --- | --- | --- | --- |
| No dependencies more than two majors behind | 3 | Each direct dependency is at most the threshold number of major versions behind its latest release (default 2; tunable via `--threshold-major-versions-behind`). | Direct dependency exceeds the threshold. Reported with current and latest. |
| No deprecated-major usage | 3 | When a package's current major is upstream-deprecated, even a one-minor lag is reported as `partial`. | Deprecated-major usage. |
| No abandoned packages | 3 | No direct dependency has gone more than the threshold months without a publish (default 24; tunable via `--threshold-abandonment-months`). Soft check — reported as `partial`. | Direct dependencies whose latest publish is older than the threshold. |
| No officially deprecated packages | 3 | No direct dependency carries a `deprecated` flag in the registry. | Deprecated direct dependency. Reported with the deprecation message and the recommended replacement when the registry provides one. |
| Peer dependencies satisfied | 2 | Every declared peer dependency in the tree resolves to a satisfying version. | Unsatisfied peer dependency warnings from `npm ls --all`/`pnpm list --depth Infinity`/equivalent. |

### Layer 3 — Compliance

| Check | Tier | Expectation | Violation signal |
| --- | --- | --- | --- |
| Every package has a license declared | 2 | No package in the dependency tree is missing a `license` field. | Packages with `license: "UNLICENSED"`, `"SEE LICENSE IN ..."` without a resolvable file, or a missing field. |
| Strong-copyleft licenses are flagged for human review | 2 | The audit surfaces, but does not enforce, the presence of GPL, AGPL, or LGPL packages in the tree. When `.architect-playbook/licenses.json` declares an allowlist that includes them, they pass; otherwise the situation is reported as `partial` with a recommendation to consult legal and decide. | GPL, AGPL, or LGPL in the tree with no allowlist file. |
| Source-available restrictive licenses are flagged for human review | 2 | Same posture as copyleft: surface the presence of SSPL, BUSL, Commons Clause, or similar. Allowlist via `.architect-playbook/licenses.json`. | Restrictive licenses present without allowlist. |
| License-checker tool present | 1 | A license-enforcement tool (`license-checker`, `license-compliance`, or equivalent) is configured and a continuous-integration step runs it. | No license tool detected, or tool present without a continuous-integration step invoking it. |

The skill **never** encodes a legal policy. Compliance findings are signals for a human (and where appropriate, a lawyer) to act on; they are not pass/fail gates with built-in opinions about which licenses are acceptable.

### Layer 4 — Hygiene

| Check | Tier | Expectation | Violation signal |
| --- | --- | --- | --- |
| No unused dependencies | 1 (low confidence) or graph-enhanced (high confidence) | Every entry in `dependencies` and `devDependencies` is imported somewhere in the project. **When the Graphify graph is present, this check uses the graph as the import truth (high confidence).** Without the graph, falls back to a regex/AST sweep across source and configuration files (low confidence). | Packages declared but never imported. The findings record the confidence level. |
| No misplaced dependencies | 1 (low confidence) or graph-enhanced | Packages used in production code (under `src/`, framework conventions) live in `dependencies`; packages used only in tests, build, or development tooling live in `devDependencies`. | A `devDependency` imported by production code, or a `dependency` imported only by test/build files. |
| No duplicate packages | 1 | Each package appears at exactly one resolved version in the lockfile. | Same package name with multiple versions in the lockfile. Reported with the package and the conflicting versions. |
| No package-manager mixing | 1 | Exactly one lockfile is present. The continuous-integration workflow uses the same package manager that produced the lockfile. | Multiple lockfiles in the repository, or a continuous-integration workflow using a different package manager than the lockfile implies. |
| Security-critical packages exact-pinned | 1 | A configurable allowlist of security-critical packages (default: `react`, `react-dom`, `next`, `@remix-run/react`, `@remix-run/node`) is exact-pinned in `package.json` (no `^`, no `~`). Soft check — reported as `partial`. | Security-critical packages declared with `^` or `~` ranges. |
| `engines` field declared | 1 | `package.json` declares the supported `node` version range. | Field absent. |

## What this skill does

1. **Reads the knowledge graph when present.** Soft dependency: when `graphify-out/graph.json` exists, the unused-dependency and misplaced-dependency checks use the graph as the import truth (high confidence). When absent, both checks fall back to a regex/AST sweep and record `confidence: "low"` on their findings. The audit still runs in full either way.
2. **Confirms a Node.js project.** Detects `package.json`. If absent, the skill stops and tells the user it currently supports Node.js projects only.
3. **Detects the package manager and tier.** Infers the package manager from the lockfile present (and falls back to the `packageManager` field in `package.json`). Determines the input tier based on the presence of `node_modules` and the `--with-network` flag.
4. **When `--with-network` is set**, runs the package manager's read-only audit and outdated commands and captures their JSON output. Never runs install, update, or any mutating operation.
5. **Writes Layer 0 — the diagnostic snapshot** to `audits/dependency-audit/snapshot.md` and prepends the same content to `findings.md`.
6. **Walks each check in the active layer list**, applying any `--include`, `--exclude`, threshold overrides, and the security-critical-package list. Tier-dependent checks running below their required tier emit `partial` with a "needs tier N — pass `--with-network` (or run `npm install` first)" gap.
7. **Writes phase 1 outputs** to `audits/dependency-audit/`:
   - `findings.md` — diagnostic snapshot followed by check results, grouped by layer.
   - `findings.json` — machine-readable.
   - `snapshot.md` — diagnostic snapshot on its own.
   - `metadata.json` — skill version, run timestamp, Graphify revision (when present), package manager, tier, applied thresholds, applied filters.
8. **Phase 2 — offers to plan the gaps.** Summarises the findings in chat and asks the user a single yes-or-no question:

   > "Generate an implementation plan for the dependency gaps? (yes/no)"

   On `yes`, writes `audits/dependency-audit/implementation-plan.md` describing exactly which packages to upgrade, which to remove, which to relocate between `dependencies` and `devDependencies`, which licenses to review with a human, and which continuous-integration steps to add. The plan does not modify any project files.

   On `no`, exits cleanly. The user can re-run with `--plan` later to skip phase 1.

## Implementation steps

### Step 1 — Confirm the prerequisites

```bash
test -f package.json || { echo "dependency-audit: no package.json detected. This skill currently supports Node.js projects only."; exit 1; }
```

Detect the package manager:

- `package-lock.json` → npm.
- `pnpm-lock.yaml` → pnpm.
- `yarn.lock` → yarn (Berry detected by absence of `node-modules` linker default and presence of `.yarnrc.yml`).
- `bun.lockb` → bun.

When multiple lockfiles are present, fall back to the `packageManager` field in `package.json`. When none of these resolves, stop and tell the user.

### Step 2 — Determine the input tier

```bash
test -d node_modules && tier=2 || tier=1
[ "$WITH_NETWORK" = "1" ] && tier=3
```

Record the resolved tier in `metadata.json`.

### Step 3 — Run network commands when tier 3

When `--with-network` is set, run the package manager's read-only commands and capture their JSON:

- npm: `npm audit --json` and `npm outdated --json`.
- pnpm: `pnpm audit --json` and `pnpm outdated --format=json`.
- yarn (Berry): `yarn npm audit --json --recursive` and `yarn outdated --json` (or via the relevant Berry plugin).
- bun: `bun pm audit --json` (when supported) and `bun outdated --json`.

If any of these commands fails (network blocked, registry unreachable), record the failure in `metadata.json`, degrade tier-3 checks to `partial`, and continue.

### Step 4 — Build the diagnostic snapshot

Compute the items listed in Layer 0. Write `snapshot.md` and prepend the same content to `findings.md`.

### Step 5 — Resolve each check

For each check in the active layer list, walk its detection logic. Tier-dependent checks running below their required tier record `partial` with a gap explaining the missing tier and the flag or command needed to unlock it.

### Step 6 — Write phase 1 outputs

Create `audits/dependency-audit/` if needed. Write `findings.md`, `findings.json`, `snapshot.md`, `metadata.json`. Overwrite previous runs of these four; preserve `implementation-plan.md` unless the user agrees to regenerate it.

### Step 7 — Print the chat summary and offer phase 2

Print the snapshot's headline numbers (package manager, tier, dependency counts, duplicate count) plus a short table mirroring the four layers with counts of present, partial, missing, and violation per layer. Highlight the top three violations across the security and health layers (those are the highest stakes). Then ask the single yes-or-no question. Do not proceed to phase 2 without an explicit affirmative.

### Step 8 — Phase 2: generate the implementation plan

When the user agrees, build `implementation-plan.md`:

1. **Header** — repository name, baseline version, package manager, tier, timestamp, total counts per layer.
2. **Layer 1 — security plan**: per advisory, the upgrade or replacement command. Continuous-integration scanning step snippet when missing. Frozen-install switch for the relevant package manager when needed.
3. **Layer 2 — health plan**: per outdated package, the upgrade target and the migration notes available from the registry. Per abandoned or deprecated package, the recommended replacement.
4. **Layer 3 — compliance plan**: per flagged license, the package, the license type, and the recommended next step (allowlist via `.architect-playbook/licenses.json` after legal review, or remove). License-checker tool installation and configuration snippet when missing.
5. **Layer 4 — hygiene plan**: per unused package, the removal command. Per misplaced package, the move command. Per duplicate, the resolution strategy (overrides, dedupe, version alignment). Per package-manager-mixing finding, the cleanup command.
6. **Closing checklist** — flat checkbox list mirroring the gaps, suitable for pasting into a pull-request description.

The plan is descriptive, not executable. It does not run any install, upgrade, or removal commands.

## Findings file shape

`findings.json`:

```json
{
  "skillVersion": "1.0.0",
  "runStartedAt": "2026-04-26T13:47:00Z",
  "runFinishedAt": "2026-04-26T13:47:14Z",
  "packageManager": { "name": "pnpm", "version": "9.4.0" },
  "tier": 3,
  "thresholds": { "majorVersionsBehind": 2, "abandonmentMonths": 24 },
  "securityCriticalPackages": ["react", "react-dom", "next", "@remix-run/react", "@remix-run/node"],
  "snapshot": {
    "lockfile": "pnpm-lock.yaml",
    "lockfileLastModified": "2026-04-22T11:18:00Z",
    "nodeModulesPresent": true,
    "directDependencyCount": 47,
    "devDependencyCount": 31,
    "transitiveTotal": 1284,
    "treeDepth": { "deepest": 9, "average": 3.4 },
    "topByDiskSize": [
      { "name": "next", "bytes": 71000000 }
    ],
    "duplicatePackageCount": 3,
    "workspaceCount": 1
  },
  "summary": {
    "security":   { "present": 3, "partial": 1, "missing": 1, "violation": 1 },
    "health":     { "present": 2, "partial": 2, "missing": 0, "violation": 1 },
    "compliance": { "present": 2, "partial": 1, "missing": 1, "violation": 0 },
    "hygiene":    { "present": 4, "partial": 0, "missing": 0, "violation": 2 }
  },
  "checks": [
    {
      "layer": "hygiene",
      "check": "no-unused-dependencies",
      "status": "violation",
      "tier": 1,
      "confidence": "high",
      "evidence": [],
      "samples": [
        { "package": "moment", "declaredIn": "dependencies" }
      ],
      "expectation": "Every entry in dependencies and devDependencies is imported somewhere in the project.",
      "gap": "1 declared dependency has no import in the codebase (Graphify graph used as import truth).",
      "remediation": "Remove with `pnpm remove moment` after confirming no dynamic require strings reference it."
    }
  ]
}
```

`findings.md` mirrors the same content in human-readable form, with the diagnostic snapshot at the top and one section per check, grouped by layer. `snapshot.md` contains only the snapshot. `metadata.json` carries skill identity, timestamps, Graphify revision (when present), and the configuration of the run (including `withNetwork: true|false` and `networkCommandsExecuted: ["npm audit --json", "npm outdated --json"]`).

## Idempotency rules

- Re-running with no flags overwrites `findings.md`, `findings.json`, `snapshot.md`, and `metadata.json` in place.
- `implementation-plan.md` is preserved across runs unless the user agrees to regenerate it.
- Filter flags, threshold overrides, the `--with-network` flag, and the security-critical-package list are recorded in `metadata.json` so a partial run can be reproduced.
- Network-derived data (vulnerabilities, outdated, abandonment) is timestamp-tagged in the findings; staleness is the user's responsibility to manage by re-running.

## Failure modes and remediation

| Symptom | Cause | Fix |
| --- | --- | --- |
| `no package.json detected` | The skill is run outside a Node.js project root. | Change directory into the project root and re-run. |
| No supported package manager | None of the recognised lockfiles is present and `packageManager` is unset. | Stop. Inform the user that v1 supports npm, pnpm, yarn, and bun only. |
| `node_modules` absent | The user has not run install, or `node_modules` is in a non-standard location. | Continue at tier 1. Record `tier: 1` in metadata. Tier-2 checks degrade to `partial` with the gap "needs tier 2 — run install first". |
| `--with-network` set but network command fails | Registry unreachable, authentication required, audit endpoint rate-limited. | Record the failure and the command output in metadata. Tier-3 checks degrade to `partial`. Do not crash. |
| Multiple lockfiles present | Mid-migration between package managers, or accidental commit. | Pick the lockfile referenced by the `packageManager` field if set; otherwise the most recently modified. Record the conflict in the snapshot. The package-manager-mixing hygiene check will report `violation`. |
| Knowledge graph missing | `/pre-audit-setup` has not been run. | Continue. Record `noGraphify: true` in metadata. The unused-dependency and misplaced-dependency checks fall back to regex/AST sweep with `confidence: "low"`. |
| `.architect-playbook/licenses.json` is malformed | User has hand-edited the file. | Treat as if absent; record the parse error in metadata; report compliance flagged-licenses checks as `partial` rather than `present`. |

## What this skill explicitly does NOT do

- Install, update, or remove any package.
- Modify `package.json`, the lockfile, or `node_modules/`.
- Encode or enforce a license policy. Compliance findings surface signals; the human (and, where appropriate, legal counsel) makes the decision.
- Make registry calls beyond the package manager's own read-only audit and outdated commands.
- Open pull requests or commit anything to git.
- Audit non-Node.js ecosystems. Python, Go, Rust, and Ruby dependency audits are out of scope for v1.
- Audit individual workspaces in a monorepo separately. The skill audits the repository root and reports cross-workspace duplication; per-workspace deep-dives are recommended as follow-up.
- Run any kind of supply-chain analysis beyond what the package manager's audit and the local installation surface — full provenance, signature verification, and attestations are out of scope for this version.
