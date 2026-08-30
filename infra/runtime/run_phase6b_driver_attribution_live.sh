#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE6B_ALLOW_DRIVER_ATTRIBUTION:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE6B_ALLOW_DRIVER_ATTRIBUTION=true explicitly before live Phase 6B driver attribution." >&2
  exit 2
fi
if [[ "${PHASE6A_ALLOW_ANOMALY_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: Phase 6B also requires PHASE6A_ALLOW_ANOMALY_QUERY=true." >&2
  exit 2
fi
if [[ "${PHASE5B_ALLOW_METRICFLOW_QUERY:-false}" != "true" ]]; then
  echo "REFUSED: Phase 6B also requires PHASE5B_ALLOW_METRICFLOW_QUERY=true." >&2
  exit 2
fi
cat <<'EOF'
Phase 6B live gate accepted.
Driver attribution requires a real Phase 6A RUNTIME_VERIFIED anomaly result with healthy
operational evidence. Every driver lens still executes through MetricFlow Explain -> Query.
EOF
