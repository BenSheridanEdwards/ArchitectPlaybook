#!/usr/bin/env python3
"""Validate maintainer evidence without running evals or accessing the network.

Results map case directory IDs to grader-relative paths to one boolean per run.
Report locations are local paths relative to the repository; remote reports must
be downloaded by the maintainer first. Hashes bind bytes, not provenance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import tempfile

EXIT_CODES = {'valid': 0, 'stale': 1, 'invalid': 2, 'failed': 3, 'no-receipt': 4}
ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def identity_paths(root):
    paths = [str(p.relative_to(root))
             for directory in ('testing-audit', 'audit-protocol')
             for p in (root / directory).rglob('*')
             if p.is_file() and '__pycache__' not in p.relative_to(root).parts
             and p.suffix != '.pyc']
    for case in sorted((root / 'evals/testing-audit').iterdir()):
        if not case.is_dir():
            continue
        paths.extend(str((case / name).relative_to(root))
                     for name in ('case.yaml', 'fixture.sh'))
        paths.extend(str(p.relative_to(root)) for p in sorted((case / 'graders').rglob('*'))
                     if p.is_file())
    return sorted(paths)


def validate(path, root=ROOT):
    if not path.exists():
        return 'no-receipt', ['unverified: receipt absent']
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            raise ValueError('receipt must be an object')
        errors, stale = [], []
        for field in ('tool', 'model', 'command'):
            if not isinstance(data.get(field), str) or not data[field].strip():
                errors.append(f'unverified: {field}')
        runs = data.get('runs')
        if type(runs) is not int or runs < 1:
            errors.append('unverified: runs')
        identities = data.get('identities')
        expected = identity_paths(root)
        if not isinstance(identities, dict):
            identities = {}
            errors.append('unverified: identities')
        for name in expected:
            value = identities.get(name)
            if not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
                errors.append(f'unverified: identity {name}')
            elif digest(root / name) != value:
                stale.append(f'stale skill/protocol/case identity: {name}')
        if set(identities) - set(expected):
            stale.append('stale: removed identity files')
        results = data.get('results')
        if not isinstance(results, dict):
            results = {}
            errors.append('unverified: results')
        cases = sorted((root / 'evals/testing-audit').glob('*/case.yaml'))
        failed = False
        expected_cases = set()
        for case_file in cases:
            case = case_file.parent
            key = str(case.relative_to(root / 'evals'))
            expected_cases.add(key)
            if key not in results:
                errors.append(f'missing case {key}')
                continue
            graders = {str(p.relative_to(case / 'graders')) for p in
                       (case / 'graders').rglob('*') if p.is_file()}
            scores = results[key]
            if not graders or not isinstance(scores, dict) or set(scores) != graders:
                errors.append(f'unverified: graders {key}')
                continue
            for grader, values in scores.items():
                if (not isinstance(values, list) or len(values) != runs
                        or not all(type(v) is bool for v in values)):
                    errors.append(f'unverified: runs {key}/{grader}')
                elif not all(values):
                    failed = True
        if set(results) - expected_cases:
            errors.append('unverified: unexpected case results')
        report = data.get('report')
        if not isinstance(report, dict):
            report = {}
        location, sha = report.get('location'), report.get('sha256')
        if (not isinstance(location, str) or not location.strip()
                or not isinstance(sha, str) or not re.fullmatch('[0-9a-f]{64}', sha)):
            errors.append('unverified: report location/sha256')
        else:
            report_path = (root / location).resolve()
            if not report_path.is_relative_to(root.resolve()):
                errors.append('unverified: report outside repository')
            elif not report_path.is_file():
                errors.append('unverified: report unavailable')
            elif digest(report_path) != sha:
                stale.append('stale: report sha256')
        if errors:
            return 'invalid', errors + stale
        if stale:
            return 'stale', stale
        if failed:
            return 'failed', ['one or more grader runs failed']
        return 'valid', []
    except (OSError, ValueError, TypeError) as exc:
        return 'invalid', [f'unverified: {exc}']



def record(metadata, report, output, root=ROOT):
    """Record explicit local results; reject malformed input before writing."""
    data = json.loads(metadata.read_text())
    if not isinstance(data, dict):
        raise ValueError('metadata must be an object')
    data = {key: data.get(key) for key in
            ('tool', 'model', 'command', 'runs', 'results')}
    report = report.resolve()
    data['report'] = {'location': str(report.relative_to(root.resolve())),
                      'sha256': digest(report)}
    data['identities'] = {name: digest(root / name)
                          for name in identity_paths(root)}
    encoded = json.dumps(data, indent=2, sort_keys=True) + '\n'
    # Reuse validation before replacing any existing receipt. Failed grader
    # runs are evidence too; malformed or stale evidence must not be recorded.
    with tempfile.TemporaryDirectory() as directory:
        candidate = Path(directory) / 'receipt.json'
        candidate.write_text(encoded)
        state, reasons = validate(candidate, root)
    if state not in ('valid', 'failed'):
        raise ValueError('; '.join(reasons))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(encoded)
    return state, reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    if len(sys.argv) > 1 and sys.argv[1] == 'record':
        parser.add_argument('--metadata', required=True, type=Path)
        parser.add_argument('--report', required=True, type=Path)
        parser.add_argument('--output', type=Path,
                            default=ROOT / 'evals/receipts/testing-audit.json')
        args = parser.parse_args(sys.argv[2:])
        try:
            state, reasons = record(args.metadata, args.report, args.output)
        except (OSError, ValueError, TypeError) as exc:
            state, reasons = 'invalid', [f'unverified: {exc}']
        print(json.dumps({'state': state, 'reasons': reasons}))
        return EXIT_CODES[state]
    parser.add_argument('receipt', nargs='?', type=Path,
                        default=ROOT / 'evals/receipts/testing-audit.json')
    args = parser.parse_args()
    state, reasons = validate(args.receipt)
    print(json.dumps({'state': state, 'reasons': reasons}))
    return EXIT_CODES[state]


if __name__ == '__main__':
    raise SystemExit(main())
