"""Run the real score CLI on controlled canonical findings, using test fixtures."""
from pathlib import Path
import importlib.util
import sys

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "score_tests", ROOT / "tests/test_repository_quality_score.py"
)
assert spec and spec.loader
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
if len(sys.argv) == 2:
    module.CALCULATOR = Path(sys.argv[1]).resolve()
fixture = module.RepositoryQualityScoreTests()
fixture.setUp()
try:
    unsupported = fixture._canonical_findings("audit-one", ["present", "present"])
    for check in unsupported["checks"]:
        check["evidence"] = []
    fixture._write_findings("audit-one", unsupported)
    fixture._write_findings(
        "audit-two", fixture._canonical_findings("audit-two", ["present"])
    )
    for label in ("Unsupported audit + valid audit", "Only unsupported audit"):
        completed = fixture._run_score()
        result = fixture._score_json()
        print(label)
        print(f"exit={completed.returncode}; status={result['status']}; score={result['overallScore']}")
        print(f"auditsSelected={result['coverage']['auditsSelected']}; checksEvaluated={result['coverage']['checksEvaluated']}")
        print(f"excludedCandidates={len(result['excludedCandidates'])}")
        print()
        (fixture.repository / ".architect-audits/audit-two/findings.json").unlink(missing_ok=True)
finally:
    fixture.doCleanups()
