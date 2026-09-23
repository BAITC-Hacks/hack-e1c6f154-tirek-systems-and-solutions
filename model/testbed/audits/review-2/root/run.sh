#!/usr/bin/env bash
# Success means the evidence was reproduced, not forecast acceptance.
set -euo pipefail
ROOT_REVIEW="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_REVIEW="$(git -C "$ROOT_REVIEW" rev-parse --show-toplevel)"
cd "$REPO_REVIEW"
export PYTHONDONTWRITEBYTECODE=1
mkdir -p "$ROOT_REVIEW/../.tmp"
export TMPDIR="$ROOT_REVIEW/../.tmp"
python3 - "$ROOT_REVIEW" <<'PY'
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import platform
import re
import subprocess
import sys
import tempfile

from model.testbed.run import DEFAULT_ADAPTER, DEFAULT_CALCULATOR, verify_manifest
out = Path(sys.argv[1])
manifest_path = Path('model/testbed/freeze.json')
frozen = verify_manifest(manifest_path, DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
hashes_before = frozen['files']
unit = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'model/testbed/tests', '-v'], capture_output=True, text=True)
(out/'unit-tests.log').write_text(unit.stdout + unit.stderr)
assert unit.returncode == 0
match = re.search(r'Ran (\d+) tests?', unit.stderr)
assert match
n_tests = int(match.group(1))
summary = {
    'audited_base_commit': 'c3dbd52',
    'python_version': platform.python_version(),
    'frozen_candidate_commit': frozen['source_commit'],
    'manifest_sha256': hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
    'existing_unit_tests': {'passed': n_tests, 'total': n_tests},
    'replays': {},
    'forecast_quality_is_separate_from_test_pass_rate': True,
    'seeds_status': 'previously inspected; replay is not a new holdout',
}
for split in ('development', 'final'):
    with tempfile.TemporaryDirectory(prefix=split+'-', dir=out.parent/'.tmp') as tmp:
        # Require threshold for final to prove failing acceptance cannot look like success.
        cmd = [sys.executable, '-m', 'model.testbed.run', 'evaluate', '--split', split, '--output', tmp]
        if split == 'final': cmd.append('--require-target')
        run = subprocess.run(cmd, capture_output=True, text=True)
        (out/(split+'-replay.log')).write_text(run.stdout + run.stderr)
        assert run.returncode == (3 if split == 'final' else 0), run.stderr[-1000:]
        reports = {}
        for name in ('report.json', 'rows.csv', 'report.md'):
            new, old = Path(tmp, name).read_bytes(), Path('model/testbed/reports', split, name).read_bytes()
            assert new == old, (split, name)
            reports[name] = hashlib.sha256(new).hexdigest()
        report = json.loads(Path(tmp, 'report.json').read_text())
        rows = report['rows']
        actual = sum(Decimal(str(row['realized_regular_quantity'])) for row in rows)
        expectation = sum(Decimal(str(row['expected_regular_quantity'])) for row in rows)
        absolute = sum(abs(Decimal(str(r['forecast_quantity']))-Decimal(str(r['realized_regular_quantity']))) for r in rows)
        absolute_e = sum(abs(Decimal(str(r['forecast_quantity']))-Decimal(str(r['expected_regular_quantity']))) for r in rows)
        signed = sum(Decimal(str(r['forecast_quantity']))-Decimal(str(r['realized_regular_quantity'])) for r in rows)
        quality = {'rows': len(rows), 'wape_realized': float(absolute/actual),
                   'wape_expectation': float(absolute_e/expectation), 'mae_realized': float(absolute/len(rows)),
                   'bias_realized': float(signed/actual), 'mean_signed_error_units': float(signed/len(rows)),
                   'target_wape': .1, 'target_met': report['forecast_target_met'],
                   'daily_wape_realized': report['overall']['daily_wape_realized']}
        assert abs(quality['wape_realized']-report['overall']['wape_realized']) < 1e-12
        summary['replays'][split] = {'exit_code': run.returncode, 'byte_identical': True, 'report_sha256': reports,
            'forecast_quality': quality,
            'business_checks': {k: report['business'][k] for k in ('passed','total','integration_status')},
            'paired_checks': {k: report['paired_project_checks'][k] for k in ('passed','total')}}
        print(split, quality, flush=True)
verify_manifest(manifest_path, DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
summary['frozen_source_unchanged'] = True
summary['final_threshold_exit_code_correct'] = True
summary['audit_execution_success'] = True
(out/'results.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2, allow_nan=False)+'\n')
print(json.dumps(summary, ensure_ascii=False, indent=2))
PY
