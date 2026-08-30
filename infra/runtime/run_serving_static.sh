#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

python -m py_compile \
  serving/contracts.py \
  serving/exporter.py \
  serving/export_cli.py \
  serving/api/settings.py \
  serving/api/queries.py \
  serving/api/repository.py \
  serving/api/models.py \
  serving/api/main.py \
  serving/jobs/materialize_export.py

python - <<'PY'
from pathlib import Path
from serving.contracts import load_serving_contract

contract = load_serving_contract(Path("serving/contracts/bi_daily_executive.yml"))
assert contract.target.table == "polaris.serving.bi_daily_executive"
assert "activity_net_sales" in contract.semantic_query.metrics
assert set(contract.consumers) == {"bi", "api"}
print("serving contract validation passed")
PY

python -m pytest -q tests/test_serving_layer_contract.py

echo "Serving static validation passed."
