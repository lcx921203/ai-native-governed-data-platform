#!/usr/bin/env bash
set -euo pipefail
if [[ "${PHASE4C_ALLOW_DATAHUB_WRITE:-false}" != "true" ]]; then
  echo "REFUSED: set PHASE4C_ALLOW_DATAHUB_WRITE=true explicitly before DataHub Runtime mutation." >&2
  exit 2
fi
echo "DEFERRED: real DataHub ingestion / identity resolution / governance mutation requires the workstation Runtime." >&2
exit 3
