# Canonical evidence validation proof

`reproduce.py` invokes the real score CLI using the existing temporary-repository
fixture from `tests/test_repository_quality_score.py`. The fixture has two audit
catalogs, three checks, a clean current commit and matching metadata. Audit one
claims both checks are present with complete evidence but supplies empty lists;
audit two has a nonblank evidence entry.

Run from the repository root:

```sh
python3 docs/proof/score-evidence/reproduce.py
```

`before.txt` was produced with the calculator from main commit
`f8e0cdfec222c95c5c9f05b1b41ff28719416440`; `after.txt` uses this branch. The first
case changes from official/100 with two selected audits to provisional/100 with
only the valid audit selected. The number 100 remains the valid audit's score;
it does not imply complete audit coverage. Removing the valid audit changes
from a provisional/100 result based on unsupported findings to unavailable/null
with exit 2.

The regression suite additionally covers empty, blank, whitespace and mixed
blank/nonblank evidence for complete and degraded inputs. Existing canonical,
legacy, not-applicable and not-evaluated cases remain covered by the full suite.

This is structural input validation. Nonblank entries in this controlled fixture
are not proof that citation content is true. Repository-backed evidence verification
belongs to the audit protocol. No UI exists for this script-only change, so
screenshots and video are not applicable; actual CLI output is the review proof.
