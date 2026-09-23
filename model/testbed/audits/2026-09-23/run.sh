#!/usr/bin/env bash
# Exit 0 means the audit ran, not that the audited candidate has no defects.
set -euo pipefail
audit_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd -- "$audit_dir/../../../.." && pwd)"
cd -- "$repo_dir"
mkdir -p "$audit_dir/.tmp"
export TMPDIR="$audit_dir/.tmp"
export PYTHONDONTWRITEBYTECODE=1
bash "$audit_dir/replay.sh" > "$audit_dir/replay-driver.log" 2>&1
python3 - "$audit_dir" <<'PY'
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from model.testbed.run import DEFAULT_ADAPTER, DEFAULT_CALCULATOR, verify_manifest

audit_dir = Path(sys.argv[1])
verify_manifest('model/testbed/freeze.json', DEFAULT_ADAPTER, DEFAULT_CALCULATOR)

def execute(name):
    process = subprocess.run(['bash', str(audit_dir / (name + '.sh'))], capture_output=True, text=True)
    (audit_dir / (name + '-run.log')).write_text(process.stdout + process.stderr)
    return {'name': name, 'exit_code': process.returncode, 'log': name + '-run.log'}

with ThreadPoolExecutor(max_workers=3) as pool:
    executions = list(pool.map(execute, ('generator', 'metrics', 'business')))
if any(row['exit_code'] for row in executions):
    raise RuntimeError(f'Audit execution failed; inspect logs: {executions}')

reports = {name: json.loads((audit_dir / (name + '.json')).read_text())
           for name in ('generator', 'metrics', 'business', 'replay')}
assert not reports['generator']['unexpected_invariant_failures']
assert reports['metrics']['checks_failed'] == 0
assert reports['business']['original_fixture_checks']['passed'] == 54
assert reports['business']['independent_quantity_status_checks']['passed'] == 54
assert reports['replay']['replay_byte_identical']
for key in ('wape_realized', 'wape_expectation'):
    assert math.isclose(reports['replay']['forecast'][key],
                        reports['metrics']['summaries']['final']['independent_overall'][key], rel_tol=1e-12)
findings = []
for area in ('generator', 'metrics', 'business'):
    for finding in reports[area]['findings']:
        findings.append({
            'id': finding['id'], 'area': area, 'severity': finding['severity'],
            'title': finding.get('title', finding.get('problem')),
            'status': 'contract_ambiguity' if finding.get('classification') == 'reference_contract_ambiguity' else 'confirmed_open',
            'evidence': area + '.json',
        })
verify_manifest('model/testbed/freeze.json', DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
summary = {
    'schema_version': 'testbed-audit-summary-v1',
    'audited_commit': '2001beaa7ec508bf7a9cf6aabaca03fd83a22f76',
    'candidate_source_commit': reports['replay']['candidate_source_commit'],
    'audit_date': '2026-09-23',
    'subagents': ['audit_generator', 'audit_metrics', 'audit_business'],
    'root_reran_all_probes': True,
    'frozen_candidate_unchanged': True,
    'audit_status': 'findings_open',
    'executions': executions,
    'forecast_quality': reports['replay']['forecast'],
    'existing_checks': reports['replay']['existing_test_results'],
    'independent_checks': {
        'generator_cases': reports['generator']['cases'],
        'generator_sku_cases': reports['generator']['sku_cases'],
        'metric_rows': 672,
        'metric_assertions_confirmed': reports['metrics']['checks_passed'],
        'quantity_status_expectations_passed': reports['business']['independent_quantity_status_checks']['passed'],
        'business_probes': reports['business']['counts'],
    },
    'findings': findings,
    'finding_counts': dict(Counter(finding['severity'] for finding in findings)),
    'confirmed_findings': sum(finding['status'] == 'confirmed_open' for finding in findings),
    'contract_ambiguities': sum(finding['status'] == 'contract_ambiguity' for finding in findings),
    'evidence_sha256': {name + '.json': hashlib.sha256((audit_dir / (name + '.json')).read_bytes()).hexdigest()
                        for name in reports},
    'interpretation': 'Forecast errors, existing test pass counts and audit findings are separate. Known defects are intentionally reproduced by successful audit probes. No combined quality percentage is defined. The final replay is not a new holdout.',
}
(audit_dir / 'audit.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'audit_status': summary['audit_status'], 'finding_counts': summary['finding_counts'],
                  'confirmed_findings': summary['confirmed_findings'], 'contract_ambiguities': summary['contract_ambiguities'],
                  'forecast_quality': summary['forecast_quality']}, ensure_ascii=False, indent=2))
PY
