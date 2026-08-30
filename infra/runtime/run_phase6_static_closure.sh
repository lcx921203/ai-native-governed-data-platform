#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

export PHASE4G_ALLOW_OPENAI_CALL=false
export PHASE5B_ALLOW_METRICFLOW_QUERY=false
export PHASE5C_ALLOW_METRICFLOW_DISCOVERY=false
export PHASE5D_ALLOW_DIMENSION_RESOLUTION=false
export PHASE5E_ALLOW_CONTINUATION_EXECUTION=false
export PHASE5F_ALLOW_SESSION_EXECUTION=false
export PHASE5G_ALLOW_COMPARATIVE_QUERY=false
export PHASE5H_ALLOW_BREAKDOWN_QUERY=false
export PHASE6A_ALLOW_ANOMALY_QUERY=false
export PHASE6B_ALLOW_DRIVER_ATTRIBUTION=false
export PHASE6C_ALLOW_DIAGNOSTIC=false
export PHASE6D_ALLOW_INCIDENT_DRILLDOWN=false
export PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING=false
export PHASE6F_ALLOW_APPROVAL_WORKFLOW=false
export PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE=false

python infra/runtime/sync_phase5_contracts.py --repair

# Rebuild deterministic/static Phase 6 evidence samples before validation.
python agent/build_diagnostic_samples.py >/dev/null
python agent/build_incident_drilldown_samples.py >/dev/null
python agent/build_incident_response_samples.py >/dev/null
python agent/build_approval_workflow_samples.py >/dev/null

python -m compileall -q agent
for script in infra/runtime/run_phase5*.sh infra/runtime/run_phase6*.sh; do bash -n "$script"; done
python - <<'PY'
from pathlib import Path
import json,yaml
root=Path('.')
for path in sorted(root.glob('agent/contracts/*.yml')):
    yaml.safe_load(path.read_text(encoding='utf-8'))
for path in sorted(root.glob('agent/contracts/*.json')):
    json.loads(path.read_text(encoding='utf-8'))
print('Phase 6 YAML/JSON parse: PASS')
PY
python -m pytest -q

live_scripts=(
  infra/runtime/run_phase6a_anomaly_live.sh
  infra/runtime/run_phase6b_driver_attribution_live.sh
  infra/runtime/run_phase6c_diagnostic_live.sh
  infra/runtime/run_phase6d_incident_live.sh
  infra/runtime/run_phase6e_incident_response_live.sh
  infra/runtime/run_phase6f_approval_workflow_live.sh
)
for script in "${live_scripts[@]}"; do
  set +e
  output="$($script 2>&1)"
  rc=$?
  set -e
  if [[ $rc -ne 2 ]] || [[ "$output" != *"REFUSED"* ]]; then
    echo "FAIL: $script must refuse with exit 2 while live gates are false." >&2
    echo "$output" >&2
    exit 1
  fi
done
printf 'Phase 6 static closure: PASS\n'
printf 'NOTE: real MetricFlow/Dagster/DataHub/Spark/Polaris/OpenAI runtime evidence remains DEFERRED.\n'
