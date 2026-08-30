#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Closure evidence is deliberately static. Force every live gate closed so generated
# samples cannot accidentally become runtime/network evidence.
export PHASE4G_ALLOW_OPENAI_CALL=false
export PHASE5B_ALLOW_METRICFLOW_QUERY=false
export PHASE5C_ALLOW_METRICFLOW_DISCOVERY=false
export PHASE5D_ALLOW_DIMENSION_RESOLUTION=false
export PHASE5E_ALLOW_CONTINUATION_EXECUTION=false
export PHASE5F_ALLOW_SESSION_EXECUTION=false
export PHASE5G_ALLOW_COMPARATIVE_QUERY=false
export PHASE5H_ALLOW_BREAKDOWN_QUERY=false

builders=(
  agent/build_context_samples.py
  agent/build_routing_samples.py
  agent/build_answer_samples.py
  agent/build_semantic_query_samples.py
  agent/build_dimension_value_samples.py
  agent/build_dimension_resolution_samples.py
  agent/build_clarification_samples.py
  agent/build_analysis_session_samples.py
  agent/build_time_comparison_samples.py
  agent/build_comparative_breakdown_samples.py
)
for builder in "${builders[@]}"; do
  python "$builder" >/dev/null
done

python - <<'PY'
from pathlib import Path
import json, yaml
root=Path('.')
for path in sorted(root.glob('agent/contracts/*.yml')) + sorted(root.glob('metadata/datahub/governance/*.yml')) + sorted(root.glob('metadata/datahub/contracts/*.yml')):
    yaml.safe_load(path.read_text(encoding='utf-8'))
for path in sorted(root.glob('agent/contracts/*.json')) + sorted(root.glob('agent/generated/*.json')) + sorted(root.glob('metadata/datahub/generated/*.json')):
    json.loads(path.read_text(encoding='utf-8'))
print('Phase 5 YAML/JSON parse: PASS')
PY

python -m compileall -q agent
for script in infra/runtime/run_phase5*.sh; do bash -n "$script"; done

python -m pytest -q

live_scripts=(
  infra/runtime/run_phase5a_metricflow_live.sh
  infra/runtime/run_phase5b_metricflow_live.sh
  infra/runtime/run_phase5c_metricflow_discovery_live.sh
  infra/runtime/run_phase5d_metricflow_resolution_live.sh
  infra/runtime/run_phase5e_clarification_live.sh
  infra/runtime/run_phase5f_analysis_session_live.sh
  infra/runtime/run_phase5g_time_comparison_live.sh
  infra/runtime/run_phase5h_comparative_breakdown_live.sh
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

printf 'Phase 5 static closure: PASS\n'
printf 'NOTE: DataHub/Dagster/MetricFlow/Spark/Polaris/OpenAI real runtime evidence remains DEFERRED.\n'
