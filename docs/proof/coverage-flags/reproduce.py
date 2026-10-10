#!/usr/bin/env python3
"""Print the real collector's verdict for disabled, provider-only and enabled coverage."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile

arguments = [argument for argument in sys.argv[1:] if argument != "--review"]
collector = Path(arguments[0]) if arguments else Path(__file__).resolve().parents[3] / "testing-audit/scripts/collect.py"
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "gc.auto", "0"], check=True)
    (root / "src").mkdir()
    (root / "src/arithmetic.js").write_text("export function add(a, b) { return a + b; }\nexport function subtract(a, b) { return a - b; }\n")
    (root / "src/arithmetic.test.js").write_text("import { expect, test } from 'vitest';\nimport { add } from './arithmetic.js';\ntest('adds two numbers', () => { expect(add(2, 3)).toBe(5); });\n")
    (root / "vitest.config.js").write_text("export default { test: { coverage: { provider: 'v8', thresholds: { lines: 100, functions: 100 } } } };\n")
    scenarios = [(flag, None) for flag in ("--coverage=false", "--coverage.provider=v8", "--coverage.enabled")]
    if "--review" in sys.argv:
        scenarios = [
            ("--coverage=false --coverage=true", None),
            ("--coverage --testNamePattern 'adds|subtracts' --coverage=false", None),
            ("", "Replace nyc coverage with Vitest"),
        ]
    for flag, workflow_name in scenarios:
        workflow = root / ".github/workflows/ci.yml"
        if workflow_name:
            workflow.parent.mkdir(parents=True, exist_ok=True)
            workflow.write_text(f"jobs:\n  test:\n    steps:\n      - name: {workflow_name}\n        run: npm test\n")
        else:
            workflow.unlink(missing_ok=True)
        package = {"name": "coverage-flag-fixture", "private": True, "type": "module", "scripts": {"test": f"vitest run {flag}"}, "devDependencies": {"vitest": "4.0.0"}}
        (root / "package.json").write_text(json.dumps(package) + "\n")
        subprocess.run(["git", "-C", str(root), "add", "."], check=True)
        result = subprocess.run([sys.executable, str(collector), "--repository", str(root)], check=True, capture_output=True, text=True)
        report = json.loads(result.stdout)
        check = report["checks"]["testing-audit.coverage-thresholds-configured"]
        print(json.dumps({"script": package["scripts"]["test"], **({"workflowStepName": workflow_name} if workflow_name else {}), "status": check["status"], "collectedBy": report["snapshot"]["coverage"]["collectedBy"]}, indent=2))
