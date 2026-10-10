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
fixture path in the receipts is replaced with `<fixture>`; other output is
retained. An initial isolated Vitest attempt with Vite 7.3.7 failed before tests
ran (`Unknown method: getBuiltins`); the successful receipts use the pinned
Vite version above. Those proof dependencies are not shipped dependencies.

Automated regressions cover scripts and workflow lines, bare/valued/quoted
booleans, coverage sub-options, later overrides, separate shell commands,
node:test and c8/nyc controls, and the public collector CLI. The collector stays
a static inventory; it does not evaluate arbitrary shell/config expressions or
prove that every observed command is executed in CI. No UI changed, so
screenshots and video are not applicable.
