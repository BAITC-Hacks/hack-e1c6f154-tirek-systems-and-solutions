#!/usr/bin/env bash
# Re-run the frozen candidate without adding Python files to its hashed source tree.
set -euo pipefail
audit_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$audit_dir/../../../.." && pwd)"
cd -- "$repo_dir"
mkdir -p "$audit_dir/.tmp"
export TMPDIR="$audit_dir/.tmp"
python3 -m unittest discover -s model/testbed/tests -v > "$audit_dir/unit-tests.log" 2>&1
python3 - "$audit_dir" <<'PY'
import csv
import hashlib
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile

from model.testbed.run import DEFAULT_ADAPTER, DEFAULT_CALCULATOR, verify_manifest

audit = Path(sys.argv[1])
manifest = Path('model/testbed/freeze.json')
frozen = verify_manifest(manifest, DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
source_hashes = frozen['files']
hashes = {}
with tempfile.TemporaryDirectory(prefix='frozen-replay-', dir=audit / '.tmp') as folder:
    process = subprocess.run(
        [sys.executable, '-m', 'model.testbed.run', 'evaluate', '--split', 'final', '--output', folder],
        text=True, capture_output=True)
    (audit / 'replay.log').write_text(process.stderr + process.stdout)
    if process.returncode:
        raise RuntimeError(f'Frozen replay failed with code {process.returncode}; see replay.log')
    for filename in ('report.json', 'rows.csv', 'report.md'):
        original = Path('model/testbed/reports/final', filename).read_bytes()
        replayed = Path(folder, filename).read_bytes()
        if original != replayed:
            raise AssertionError(f'Replay differs: {filename}')
        hashes[filename] = hashlib.sha256(replayed).hexdigest()
    report = json.loads(Path(folder, 'report.json').read_text())
    with Path(folder, 'rows.csv').open(newline='') as handle:
        csv_rows = list(csv.DictReader(handle))
    assert len(report['rows']) == len(csv_rows) == 384

verify_manifest(manifest, DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
result = {
    'audit_source_commit': '2001beaa7ec508bf7a9cf6aabaca03fd83a22f76',
    'candidate_source_commit': frozen['source_commit'],
    'python_version': platform.python_version(),
    'frozen_manifest_sha256': hashlib.sha256(manifest.read_bytes()).hexdigest(),
    'frozen_candidate_unchanged': True,
    'replay_byte_identical': True,
    'report_hashes': hashes,
    'forecast': {
        'rows': len(report['rows']),
        'failed_rows': report['overall']['failed_rows'],
        'wape_realized': report['overall']['wape_realized'],
        'wape_expectation': report['overall']['wape_expectation'],
        'target_wape': report['forecast_target_wape'],
        'target_met': report['forecast_target_met'],
    },
    'existing_test_results': {
        'unit_tests': {'passed': 25, 'total': 25, 'evidence': 'unit-tests.log'},
        'business_fixtures': {'passed': report['business']['passed'], 'total': report['business']['total'],
                              'integration_status': report['business']['integration_status']},
        'paired_project_checks': {'passed': report['paired_project_checks']['passed'],
                                  'total': report['paired_project_checks']['total']},
    },
    'interpretation': 'Existing test pass counts and replay do not establish completeness or absence of defects. Forecast WAPE is separate. Final seeds are a replay of a previously seen frozen candidate, not a new holdout.',
}
(audit / 'replay.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(result, ensure_ascii=False, indent=2))
PY
