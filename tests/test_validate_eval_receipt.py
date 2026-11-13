import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('receipt', ROOT / 'scripts/validate_eval_receipt.py')
receipt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(receipt)


class ReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('testing-audit/SKILL.md', 'audit-protocol/SKILL.md',
                     'audit-protocol/references/run-protocol.md',
                     'testing-audit/scripts/collect.py', 'testing-audit/checks.json',
                     'testing-audit/references/detection.md',
                     'audit-protocol/scripts/audit_run.py',
                     'evals/testing-audit/sound-suite/case.yaml',
                     'evals/testing-audit/sound-suite/fixture.sh',
                     'evals/testing-audit/sound-suite/graders/clean.md', 'report.json'):
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('fixture\n')
        self.data = json.loads((ROOT / 'tests/fixtures/eval_receipt/metadata.json').read_text())
        self.data['identities'] = {p: receipt.digest(self.root / p)
                                   for p in receipt.identity_paths(self.root)}
        self.data['report']['sha256'] = receipt.digest(self.root / 'report.json')
        self.path = self.root / 'receipt.json'

    def check(self):
        self.path.write_text(json.dumps(self.data))
        return receipt.validate(self.path, self.root)

    def test_valid(self):
        self.assertEqual(self.check()[0], 'valid')

    def test_changed_skill_identity_is_stale(self):
        (self.root / 'testing-audit/SKILL.md').write_text('changed')
        state, reasons = self.check()
        self.assertEqual(state, 'stale')
        self.assertIn('testing-audit/SKILL.md', ' '.join(reasons))

    def test_changed_collector_is_stale(self):
        (self.root / 'testing-audit/scripts/collect.py').write_text('changed')
        state, reasons = self.check()
        self.assertEqual(state, 'stale')
        self.assertIn('testing-audit/scripts/collect.py', ' '.join(reasons))

    def test_changed_catalogue_is_stale(self):
        (self.root / 'testing-audit/checks.json').write_text('changed')
        state, reasons = self.check()
        self.assertEqual(state, 'stale')
        self.assertIn('testing-audit/checks.json', ' '.join(reasons))

    def test_relabelled_sound_suite_result_is_rejected(self):
        self.data['results']['testing-audit/other'] = self.data['results'].pop('testing-audit/sound-suite')
        state, reasons = self.check()
        self.assertEqual(state, 'invalid')
        self.assertIn('missing case testing-audit/sound-suite', reasons)

    def test_absent_receipt_is_distinct_from_failed(self):
        self.assertEqual(receipt.validate(self.path, self.root)[0], 'no-receipt')
        self.data['results']['testing-audit/sound-suite']['clean.md'][0] = False
        self.assertEqual(self.check()[0], 'failed')
        self.assertNotEqual(receipt.EXIT_CODES['no-receipt'], receipt.EXIT_CODES['failed'])

    def test_missing_fields_unverified(self):
        for field in ('tool', 'model', 'command', 'runs', 'report', 'identities', 'results'):
            original = copy.deepcopy(self.data)
            del self.data[field]
            state, reasons = self.check()
            self.assertEqual(state, 'invalid')
            self.assertIn('unverified', ' '.join(reasons))
            self.data = original

    def test_report_tampering(self):
        (self.root / 'report.json').write_text('changed')
        self.assertEqual(self.check()[0], 'stale')

    def test_missing_grader_run(self):
        self.data['results']['testing-audit/sound-suite']['clean.md'].pop()
        self.assertEqual(self.check()[0], 'invalid')

    def test_malformed_receipt(self):
        self.path.write_text('[]')
        self.assertEqual(receipt.validate(self.path, self.root)[0], 'invalid')

    def test_record_matches_validate(self):
        source = ROOT / 'tests/fixtures/eval_receipt/results'
        results = self.root / 'evals/results/fixture'
        shutil.copytree(source, results)
        receipt.record(results / 'metadata.json', results / 'report.txt',
                       self.path, self.root)
        self.assertEqual(receipt.validate(self.path, self.root), ('valid', []))
        data = json.loads(self.path.read_text())
        self.assertEqual(data['identities'], self.data['identities'])
        self.assertEqual(data['results'], self.data['results'])
        self.assertEqual(data['report']['sha256'], receipt.digest(results / 'report.txt'))

    def test_record_cli_rejects_missing_input(self):
        result = subprocess.run(
            [sys.executable, str(ROOT / 'scripts/validate_eval_receipt.py'),
             'record', '--metadata', str(self.root / 'missing.json'),
             '--report', str(self.root / 'report.json'),
             '--output', str(self.path)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['state'], 'invalid')
        self.assertFalse(self.path.exists())

    def test_record_preserves_failed_results(self):
        metadata = self.root / 'metadata.json'
        self.data['results']['testing-audit/sound-suite']['clean.md'][0] = False
        metadata.write_text(json.dumps(self.data))
        receipt.record(metadata, self.root / 'report.json', self.path, self.root)
        self.assertEqual(receipt.validate(self.path, self.root)[0], 'failed')

    def test_record_rejects_incomplete_results_without_overwriting(self):
        metadata = self.root / 'metadata.json'
        self.data['results'] = {}
        metadata.write_text(json.dumps(self.data))
        self.path.write_text('existing')
        with self.assertRaises(ValueError):
            receipt.record(metadata, self.root / 'report.json', self.path, self.root)
        self.assertEqual(self.path.read_text(), 'existing')
