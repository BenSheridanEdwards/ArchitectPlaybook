# Contributing to the Architect Playbook

Two paths for contributing: **add a new skill**, or **improve an existing one**. The improve path has its own automated workflow via `/system-self-improve`; the add path follows the conventions below.

The full set of project-wide rules lives in [CLAUDE.md](CLAUDE.md) — read it first if you have not already. The architectural intent behind those rules lives in [ARCHITECTURE.md](ARCHITECTURE.md).

## Adding a new skill

1. **Pick a folder name in full words.** Skill folder names match their slash-command trigger. No abbreviations, anywhere — see CLAUDE.md.
2. **Create the SKILL.md with the standard frontmatter:**
   ```yaml
   ---
   name: <folder-name>
   description: <one-line description used by the Claude Code skill matcher>
   trigger: /<folder-name>
   ---
   ```
3. **Follow the body structure** that every implemented skill uses. The canonical sections are:
   - Purpose paragraph.
   - "How this differs from neighbouring skills" with a verbatim boundary table mapping cross-cutting concerns to their owning skill.
   - Posture statement (static-only, static-with-`--with-X`, etc.).
   - `## Usage` with the slash command and every flag.
   - The opinionated baseline organised in **four layers plus a Layer 0 diagnostic snapshot**. Layer 0 is informational only and has no status; layers 1–4 grade against the four-status taxonomy (`present | partial | missing | violation`).
   - "What this skill does" — a numbered walk through the skill's behaviour.
   - "Implementation steps" — the concrete steps Claude follows when invoked.
   - "Findings file shape" with a JSON example.
   - "Idempotency rules", "Failure modes and remediation", "What this skill explicitly does NOT do".
   - The two-phase flow (report → ask → optional plan) is non-negotiable. Mutating audits are not part of the playbook.
4. **Add a row to the appropriate sub-table in the README skill list** (Setup utilities, Audits, or Meta) — same commit as the SKILL.md, not a separate one.
5. **Add a 2–3 sentence entry to the README "Why each skill exists" section** — same commit. The entry explains *why* the skill is a separate concern, not what it does.
6. **Make the audit accept `--target=<path>`.** Every audit operates on the current working directory by default, but defers to `--target=<path>` when set. All file reads, file globbing, regex searches, subprocess invocations, and findings-output paths are scoped to the target. The `--target` flag is what makes `/worktree <name>` work — without it, `/worktree` could only create the worktree, not audit it from the same chat.
7. **Insert the target-resolution paragraph and the worktree Pro Tip block** right after the `## Usage` section if your skill is an audit:
   ```markdown
   When `--target=<path>` is set, the skill operates on that path instead of the current working directory. File reads, file globbing, regex searches, and any subprocess commands all run scoped to the target. Findings land at `<target>/.architect-audits/<skill-name>/`. The default is the current working directory. This is the building block that lets `/worktree <name>` create a worktree and audit it from a chat opened elsewhere — all in one chat.

   **💡 Pro tip**: Use `/worktree <short-name>` to run this against a Git worktree (creates the worktree and runs the audit in this same chat). Useful for branch isolation and for running multiple audits in true parallel across separate chats.
   ```
   Setup and install skills do *not* get `--target` or the Pro Tip — their effects need to land in the main checkout.
8. **Commit with a Conventional Commits subject:**
   ```
   feat: implement <skill-name> skill
   ```

## Improving an existing skill

The preferred path is the playbook's own self-improvement loop:

1. Run the audit against a real project.
2. Implement the fixes in the same chat.
3. Run a review pass against the worktree containing the fix (re-running the originating audit *is* the review).
4. If the review surfaces something the audit missed, write a `review-gap-report.md` in the audit's findings directory.
5. From inside the playbook clone, run `/system-self-improve` pointing at the gap report. It will propose a minimal, reversible edit to the originating audit's `SKILL.md`.

This loop ensures changes to audit baselines are grounded in a real gap rather than speculation.

Direct hand edits are also welcome when the change is too small or too obvious for the loop. Apply the same Conventional Commits and README-update-in-same-commit rules.

## Conventions

- **Conventional Commits** for every commit. `feat:` for new skills or new checks, `fix:` for corrected detection, `chore:` for threshold tweaks, `docs:` for prose-only refinement, `refactor:` for restructuring without behaviour change.
- **Subject lines use the imperative present tense.** "Add X" not "Added X".
- **No abbreviations.** Spell every word out: Documentation (not docs), Performance (not perf), Accessibility (not a11y), Configuration (not config in prose), Repository (not repo). Filenames may keep their canonical form (`.gitignore`, `tsconfig.json`).
- **Stage files explicitly.** Avoid `git add -A` and `git add .` in committed examples and recommendations.
- **Read-only by default.** Audits never mutate code. The only skill with an `--apply` mode is `/system-self-improve`, and it always prompts for explicit confirmation.
- **No hard-coded absolute paths** in skill bodies. Derive from the current working directory or `$HOME`.
- **The four-layer convention is non-negotiable.** Audits use four layers plus Layer 0. Variations are reserved for the meta layer (`/system-self-improve` uses four sequential stages instead of layers).
- **The Testing Philosophy is non-negotiable.** Behaviour over implementation, snapshots are a smell, semantic tokens over utility classes. This applies to any test code Claude writes anywhere, not just to the playbook itself.

## Pull request checklist

- [ ] SKILL.md follows the canonical body structure.
- [ ] README skill index has a row for the skill in the right sub-table.
- [ ] README "Why each skill exists" has a 2–3 sentence entry.
- [ ] `--target=<path>` flag is supported (audits only) and the target-resolution paragraph plus worktree Pro Tip are in place.
- [ ] No abbreviations introduced anywhere.
- [ ] Conventional Commits subject on every commit in the branch.
- [ ] If the change touches behaviour shared across skills (boundary tables, status taxonomy, two-phase flow), the change has been justified in an ADR under `docs/decisions/` or via `/system-self-improve` with a real gap report.

## Reporting a gap

If you find a gap in an existing audit but don't want to run the full self-improvement loop, open an issue describing:

- Which audit you ran.
- What it reported.
- What you expected it to report (the gap).
- A minimal repro if possible.

Gaps reported this way feed directly into `/system-self-improve --gap` later.
