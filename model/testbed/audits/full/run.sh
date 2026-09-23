#!/usr/bin/env bash
# Historical corrected-* artifacts are deliberately never overwritten.
set -euo pipefail
AUDIT_FULL="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$AUDIT_FULL/../../../.."
export PYTHONDONTWRITEBYTECODE=1
python3 - <<'PY'
from pathlib import Path
from model.testbed.run import verify_manifest, DEFAULT_ADAPTER, DEFAULT_CALCULATOR
verify_manifest(Path('model/testbed/full-freeze.json'), DEFAULT_ADAPTER, DEFAULT_CALCULATOR)
print('Full candidate hashes verified.')
PY
python3 -m unittest discover -s model/testbed/tests -v > "$AUDIT_FULL/unit-tests.log" 2>&1 || {
  cat "$AUDIT_FULL/unit-tests.log"; exit 1;
}
python3 -m model.testbed.requirement_checks --output "$AUDIT_FULL/requirements.json"
python3 -m model.testbed.run evaluate --split development --manifest model/testbed/full-freeze.json --output model/testbed/reports/full-development > "$AUDIT_FULL/development.log" 2>&1 || {
  cat "$AUDIT_FULL/development.log"; exit 1;
}
set +e
python3 -m model.testbed.run evaluate --split final --manifest model/testbed/full-freeze.json --require-target --output model/testbed/reports/full-final > "$AUDIT_FULL/final.log" 2>&1
threshold_exit=$?
set -e
python3 - "$threshold_exit" <<'PY'
import json, sys
from pathlib import Path
report = json.loads(Path('model/testbed/reports/full-final/report.json').read_text())
assert int(sys.argv[1]) == (0 if report['forecast_target_met'] else 3), 'Execution failed; see final.log'
print('Synthetic final WAPE:', report['overall']['wape_realized'], 'threshold exit:', sys.argv[1])
PY
python3 -m model.testbed.full_evidence
