#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"
export PYTHONPATH="$ROOT${PYTHONPATH:+:$PYTHONPATH}"

# Source/static closure must never inherit a live gate from the caller.
export PHASE7A_ALLOW_RUNTIME_BOOTSTRAP=false
export PHASE7A_ALLOW_DATAHUB_BOOTSTRAP=false
export PHASE7A_ALLOW_DATAHUB_GOVERNANCE_WRITE=false
export PHASE7A_ALLOW_AGENT_DATAHUB_READ=false
export PHASE7A_ALLOW_AGENT_SEMANTIC_RUNTIME=false
export PHASE7A_ALLOW_AGENT_DAGSTER_READ=false
export PHASE7A_ALLOW_OPENAI_PROVIDER=false
export PHASE7B_ALLOW_KNOWLEDGE_REINDEX=false
export PHASE7B_ALLOW_KNOWLEDGE_RETRIEVAL=false
export PHASE7B_ALLOW_KNOWLEDGE_RERANK=false
export PHASE7C_ALLOW_MCP_RUNTIME=false
export PHASE7_ALLOW_FINAL_RUNTIME_CLOSURE=false

# Runtime truth is deliberately outside source closure.  A stale local evidence tree
# must not make static validation appear stronger than it is.
rm -rf .runtime

bash infra/runtime/run_phase6_static_closure.sh
bash infra/runtime/run_phase7a_static.sh
bash infra/runtime/run_phase7b_static.sh
bash infra/runtime/run_phase7c_static.sh
python -m pytest -q tests/test_phase7_source_integrity.py tests/test_phase7a_live_cutover_static.py

python -m compileall -q agent mcp_server metadata/datahub/tools infra/runtime/phase7 orchestration/dagster/commerce_dagster
python - <<'PY'
from pathlib import Path
import json, yaml
root=Path('.')
errors=[]
for path in sorted(root.rglob('*')):
    if not path.is_file() or any(part in {'.git','.pytest_cache','__pycache__','.runtime'} for part in path.parts):
        continue
    try:
        if path.suffix in {'.yml','.yaml'}:
            yaml.safe_load(path.read_text(encoding='utf-8'))
        elif path.suffix == '.json':
            json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        errors.append((str(path),str(exc)))
if errors:
    for path,err in errors: print(f'PARSE FAIL: {path}: {err}')
    raise SystemExit(1)
print('Phase 7 YAML/JSON parse: PASS')
PY

for script in infra/runtime/*.sh; do
  bash -n "$script"
  first_line="$(head -n 1 "$script")"
  if [[ "$first_line" != '#!/usr/bin/env bash' ]]; then
    echo "FAIL: invalid shebang first line: $script" >&2
    exit 1
  fi
done
echo "Phase 7 shell syntax/shebang audit: PASS"

live_scripts=(
  infra/runtime/run_phase7a_core_bootstrap.sh
  infra/runtime/run_phase7a_datahub_bootstrap.sh
  infra/runtime/run_phase7a_datahub_acceptance.sh
  infra/runtime/run_phase7a_agent_metadata_live.sh
  infra/runtime/run_phase7a_agent_semantic_live.sh
  infra/runtime/run_phase7a_dagster_operational_live.sh
  infra/runtime/run_phase7a_openai_agent_live.sh
  infra/runtime/run_phase7b_rag_live.sh
  infra/runtime/run_phase7b_retrieval_live.sh
  infra/runtime/run_phase7c_mcp_acceptance.sh
  infra/runtime/run_phase7_final_runtime_closure.sh
)
for script in "${live_scripts[@]}"; do
  set +e
  output="$(bash "$script" 2>&1)"
  rc=$?
  set -e
  if [[ $rc -ne 2 ]] || [[ "$output" != *"REFUSED"* ]]; then
    echo "FAIL: $script must REFUSE with exit 2 while live gates are false." >&2
    echo "$output" >&2
    exit 1
  fi
done

if [[ -e .runtime ]]; then
  echo "FAIL: source closure must not leave runtime evidence in the source tree." >&2
  find .runtime -maxdepth 4 -type f -print >&2 || true
  exit 1
fi

# Keep the packaged source tree clean after compile/pytest validation.
find . -type d -name '__pycache__' -prune -exec rm -rf {} +
find . -type f -name '*.pyc' -delete
rm -rf .pytest_cache tests/.pytest_cache

printf 'Phase 7 full source/engineering closure: PASS\n'
printf 'Runtime evidence: DEFERRED (no Docker/DataHub/MetricFlow/Dagster/Qdrant/MCP/OpenAI runtime certification was inferred).\n'
