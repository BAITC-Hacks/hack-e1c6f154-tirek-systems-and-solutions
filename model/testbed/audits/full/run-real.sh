#!/usr/bin/env bash
# Usage: run-real.sh FORECAST_REPO PYTHON312 INPUTS PRIOR_PRIVATE_PREDICTIONS NEW_PRIVATE_OUTPUT
set -euo pipefail
if [[ $# != 5 ]]; then
  echo 'Usage: run-real.sh FORECAST_REPO PYTHON312 INPUTS PRIOR_PRIVATE_PREDICTIONS NEW_PRIVATE_OUTPUT' >&2
  exit 2
fi
AUDIT_REAL="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$AUDIT_REAL/../../../.."
export PYTHONDONTWRITEBYTECODE=1
python3 -m model.testbed.real_evidence --inputs "$3" --artifacts "$1/model/forecast_v2/artifacts" --predictions "$4" --output "$AUDIT_REAL/real-metrics.json"
python3 -m model.testbed.replay_real --repo "$1" --python "$2" --inputs "$3" --private "$5" --report "$AUDIT_REAL/real-replay.json" --previous "$4/forecast.csv"
