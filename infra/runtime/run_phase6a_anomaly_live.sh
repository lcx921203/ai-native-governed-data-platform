#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE6A_ALLOW_ANOMALY_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE6A_ALLOW_ANOMALY_QUERY=true explicitly before live Phase 6A anomaly querying." >&2
  exit 2
fi
if [[ "${PHASE5B_ALLOW_METRICFLOW_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: Phase 6A also requires PHASE5B_ALLOW_METRICFLOW_QUERY=true." >&2
  exit 2
fi
cat <<'EOF'
Phase 6A live gate accepted.
The detector will execute one current aggregate MetricFlow query plus seven equal-length baseline queries.
Real workstation/runtime acceptance remains an explicit manual step.
EOF
