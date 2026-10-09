# Evaluations

Behavioural evaluations for the playbook's skills, run with Claude Code's
built-in `claude plugin eval`. Each case builds a small repository with
planted problems, runs a real slash command against it with this plugin
loaded, and grades what the audit published.

They complement the unit tests. The tests check the scripts deterministically
and run in continuous integration. Evaluations check that a model following a
skill reaches the right conclusions. They cost money and vary from run to run,
so they are a maintainer's check before changing a skill, not a CI gate.

## Run them

Evaluation runs are sandboxed, and the sandbox cannot read your home directory.
Run them against a copy of the plugin outside it:

```bash
rsync -a --delete --exclude .git --exclude evals/results ./ /tmp/architect-playbook-eval/
cd /tmp/architect-playbook-eval
claude plugin eval . --scaffold --no-publish --ablation none --allow-tools Bash --judge-model sonnet
```

- `--scaffold` runs each case's `fixture.sh`, which builds the fixture
  repository with its Git history. It runs bash you can read in the case
  folder.
- `--no-publish` keeps the HTML report on your machine.
- `--ablation none` skips the no-plugin baseline, which cannot run a slash
  command it does not have.
- `--allow-tools Bash` lets the audit run the protocol script and Git. Every
  command runs in the OS sandbox, which confines writes to the run's
  workspace. Narrower grants such as `Bash(python3:*)` deny the multi-line
  protocol commands the audit writes, and the run stalls before publishing.
- Add `--case architecture-tangled-store` to run one case, `--runs 1` for a
  quick check, `--concurrency 2` to run two at once, and `--max-cost-usd
  <amount>` to cap spending. An architecture run costs about a dollar and a
  security run about a dollar and a half.
- Add `--judge-model sonnet` for the `llm` graders. They read the whole
  findings report, often over 30,000 characters, and the default judge model
  gives noisy verdicts at that length. On the same security report, it voted
  to fail a list that met every point of its rubric, while `sonnet` passed it
  unanimously.

**macOS:** `/usr/bin/git` and `/usr/bin/python3` are Xcode shims that cannot
start inside the sandbox. Put the real tools first on `PATH`:

```bash
PATH="/Library/Developer/CommandLineTools/usr/bin:$PATH" claude plugin eval . ...
```

Results go to `evals/results/<timestamp>/` in the copy.

## Cases

| Case | What it measures |
| --- | --- |
| `architecture-audit/tangled-store` | Recall. A storefront with an import cycle, no boundary tooling, a component importing another feature's internal file, Zustand and Redux both writing cart state, two HTTP clients, effect-based fetching beside a query layer, storage access in components, a wire type rendered in a component, and an untested checkout hotspot. |
| `architecture-audit/well-kept-store` | Precision. The same storefront built well, so every finding it raises is a false positive. |
| `security-audit/vulnerable-app` | Recall. A Next.js invoicing app with a committed live-format Stripe key (assembled when the fixture is built), no ignore rule for local environment files, an unauthenticated Server Action that deletes any invoice by id, a route handler and a page that return any user's invoice, middleware-only authorization on a bypassable Next.js version, server-side request forgery in a link preview, a customer note rendered as unsanitized HTML, an open redirect after sign-in that also runs `javascript:` URLs, no sign-in attempt limit, a token in localStorage, and an assistant tool that refunds any invoice the model names. |
| `security-audit/hardened-app` | Precision. The same app with a data-access layer, input validation, a hardened session cookie, rate-limited sign-in, a random payment-link token, a safe redirect, sanitized HTML, an oEmbed allowlist, a nonce-based Content Security Policy, and scoped, rate-limited assistant tools, so every finding it raises is a false positive. |

## Grading

Each case has graders in its `graders/` folder:

- **`regex` graders** read the "All checks" table in
  `.architect-audits/<audit>/findings.md` and require each planted check's
  status. Where reasonable engineers could choose either status, both are
  allowed, such as `violation|partial`. Tool checks decided by the collector
  must match exactly.
- **`file_exists`** requires the run to publish `findings.json`, which the
  protocol writes only after the score calculator's contract code accepts it.
- **`llm` graders** judge the quality of the act-on list against a rubric.

A run's score is the share of its graders that pass. A case's score is the mean
over its runs.

## Adding a case

1. Write `fixture.sh` so it builds the repository from nothing. Give commits
   dates relative to today, because audits read recent history. The scripts
   need bash and a GNU or BSD `date`; BusyBox `date` is not supported.
2. Keep fixture code plausible. Do not leave comments that point at the planted
   problems.
3. Write one grader per expected status, anchored to the "All checks" table, and an `llm` grader for the judgement
   the regexes cannot capture.
4. Run the case at least three times before trusting its result.
