#!/usr/bin/env bash
# Successful execution preserves failure evidence; WAPE acceptance is separate.
set -euo pipefail
AUDIT_CORRECTIONS="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$AUDIT_CORRECTIONS/../../../.."
export PYTHONDONTWRITEBYTECODE=1
python3 - <<'PY'
from pathlib import Path
from model.testbed.run import verify_manifest,DEFAULT_ADAPTER,DEFAULT_CALCULATOR
verify_manifest(Path('model/testbed/corrected-freeze.json'),DEFAULT_ADAPTER,DEFAULT_CALCULATOR)
print('Corrected candidate hashes verified.')
PY
if [[ $# -ge 1 ]]; then
  source_args=(--folder "$1" --output "$AUDIT_CORRECTIONS/source-inventory.json")
  if [[ $# -ge 2 ]]; then source_args+=(--previous-inputs "$2"); fi
  python3 -m model.testbed.source_inventory "${source_args[@]}"
fi
python3 -m unittest discover -s model/testbed/tests -v > "$AUDIT_CORRECTIONS/unit-tests.log" 2>&1 || {
  cat "$AUDIT_CORRECTIONS/unit-tests.log"; exit 1;
}
python3 -m model.testbed.requirement_checks --output "$AUDIT_CORRECTIONS/requirements.json"
python3 -m model.testbed.run evaluate --split development --output model/testbed/reports/corrected-development > "$AUDIT_CORRECTIONS/development.log" 2>&1 || {
  cat "$AUDIT_CORRECTIONS/development.log"; exit 1;
}
set +e
python3 -m model.testbed.run evaluate --split final --require-target --output model/testbed/reports/corrected-final > "$AUDIT_CORRECTIONS/final.log" 2>&1
threshold_exit=$?
set -e
python3 - "$threshold_exit" <<'PY'
import json,sys
from pathlib import Path
report=json.loads(Path('model/testbed/reports/corrected-final/report.json').read_text())
assert int(sys.argv[1]) == (0 if report['forecast_target_met'] else 3), 'Final evaluation failed; read final.log'
print('Final WAPE:',report['overall']['wape_realized'],'threshold exit:',sys.argv[1])
PY
python3 -m model.testbed.correction_evidence
python3 - <<'PY'
from collections import Counter
from decimal import Decimal
import hashlib,json,re
from pathlib import Path
from model.testbed.run import verify_manifest,DEFAULT_ADAPTER,DEFAULT_CALCULATOR
root=Path('model/testbed');out=root/'audits/corrections'
manifest=verify_manifest(root/'corrected-freeze.json',DEFAULT_ADAPTER,DEFAULT_CALCULATOR)
tests=(out/'unit-tests.log').read_text();number=int(re.search(r'Ran (\d+) tests',tests).group(1))
assert '\nOK\n' in tests
quality={}
for split in ('development','final'):
    report=json.loads((root/'reports'/('corrected-'+split)/'report.json').read_text())
    quality[split]={'metrics':report['overall'],'by_scenario':report['groups']['scenario'],
                    'business_checks':{k:report['business'][k] for k in ('passed','total','integration_status')},
                    'paired_checks':{k:report['paired_project_checks'][k] for k in ('passed','total')}}
req=json.loads((out/'requirements.json').read_text())
reg=json.loads((out/'regression-evidence.json').read_text())
inventory=json.loads((out/'source-inventory.json').read_text())
summary={'status':'corrections_verified_with_declared_limits','frozen_source_commit':manifest['source_commit'],
         'manifest_sha256':hashlib.sha256((root/'corrected-freeze.json').read_bytes()).hexdigest(),
         'unit_tests':{'passed':number,'total':number},'requirement_controls':{'passed':req['passed'],'total':req['total']},
         'regression_records':len(reg['findings']),'prior_confirmed_defects_covered':12,'ambiguities_resolved':1,
         'source_workbooks':len(inventory['workbooks']),'forecast_quality':quality,
         'final_status':'Previously inspected seeds; retrospective comparison, not independent holdout.',
         'real_sales_accuracy_measured_here':False,
         'production_calculator_validated':False,'raw_partner_data_committed':False}
(out/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
print(json.dumps({'unit_tests':summary['unit_tests'],'requirement_controls':summary['requirement_controls'],
                  'regression_records':summary['regression_records'],'forecast_quality_separate':True}))
PY
