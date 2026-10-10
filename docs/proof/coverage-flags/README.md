# Coverage collection flag proof

The same 100% coverage floor is configured in all collector scenarios. Before
this change, disabled and provider-only flags earned `present`; after it, they
are `partial` with no collection evidence. Explicit enable remains `present`.

Run from the repository root (Python standard library and Git only):

```sh
python3 docs/proof/coverage-flags/reproduce.py
```

The optional first argument selects another collector source file, so the
same fixture can run against an exported main version for before/after proof.
`before.txt` was produced against main before this change; `after.txt` was
produced against this branch. The script creates an isolated tracked fixture,
runs the collector CLI and prints only the relevant result fields. It installs
nothing and executes no consumer JavaScript.

The runner receipts independently show why the verdict matters. With Node
22.23.3, Vitest 4.0.0, @vitest/coverage-v8 4.0.0 and Vite 7.1.12, the arithmetic
fixture has one passing `add(2, 3) === 5` test and an untested `subtract` function.
Its Vitest configuration sets global lines and functions thresholds to 100%.

- `vitest run --coverage=false`: exit 0; one passing test; no coverage report
  (`vitest-disabled.txt`).
- `vitest run --coverage.enabled`: exit 1; the same test passes, but 50%
  lines/functions fail the configured floor (`vitest-enabled.txt`).

The fixture source is in `reproduce.py`. To repeat the runner corroboration,
copy its package/config/source files to a disposable directory, install the
four exact tool versions there, and run those two commands. The absolute runner
fixture path in the receipts is replaced with `<fixture>` and trailing spaces
are removed; other output is retained. An initial isolated Vitest attempt with Vite 7.3.7 failed before tests
ran (`Unknown method: getBuiltins`); the successful receipts use the pinned
Vite version above. Those proof dependencies are not shipped dependencies.

Automated regressions cover scripts and workflow lines, bare/valued/quoted
booleans, coverage sub-options, duplicate options, quoted separators, shell commands,
node:test and c8/nyc controls, and the public collector CLI. The collector stays
a static inventory; it does not evaluate arbitrary shell/config expressions or
prove that every observed command is executed in CI. No UI changed, so
screenshots and video are not applicable.

## Independent adversarial review

The review of the initial PR head found two P2 findings: wrapper names in
workflow descriptions had acquired collection credit, and the new last-value
precedence assumption was invalid for Vitest. A quoted separator could also
split a single invocation before its duplicate flag. All are now covered by
regressions and corrected. Duplicate collection options are conservatively
uncredited; shell separators are recognized outside quotes; package wrapper
evidence is preserved without extending it to workflow descriptions.

```sh
python3 docs/proof/coverage-flags/reproduce.py --review
```

`review-before.txt` uses the initial PR collector; `review-after.txt` uses the
corrected branch. `vitest-duplicate.txt` records the real runner rejecting a
duplicated coverage option before tests run, with exit 1. These review cases
failed in 16 regression subcases on the initial PR head. Quoted workflow run
values retain their positive/negative controls. The original three-scenario
before/after receipts above remain valid.
