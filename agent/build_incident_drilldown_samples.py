from __future__ import annotations

import json
from pathlib import Path

from agent.incident_drilldown import (
    FailedRunEvidence,
    IncidentDrilldownResult,
    IncidentDrilldownStatus,
    IncidentEvidenceComposer,
    PartitionIncidentEvidence,
    RecoveryPolicySnapshot,
)

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'agent/generated/incident_drilldown_samples.json'

result=IncidentDrilldownResult(
    status=IncidentDrilldownStatus.COMPLETE,
    evidence='RUNTIME_VERIFIED',
    partitions=(PartitionIncidentEvidence(
        partition_key='2026-08-05',
        freshness_overdue=True,
        exact_partition_complete=False,
        missing_mart_asset_keys=('orders','order_items'),
        run_ids=('run-failed-1',),
        failed_run_ids=('run-failed-1',),
        successful_run_ids=(),
        latest_failed_run=FailedRunEvidence(
            run_id='run-failed-1',status='FAILURE',failure_class='data_contract',
            failure_source='dbt_artifact',failure_component='dbt:build',failure_reason='dbt_data_test_failed',failure_stage='dbt:build',
        ),
        recovery=RecoveryPolicySnapshot(
            action='alert_manual',reason_code='data_contract_failure',explanation='Data contract failures require repair.',
            observed_auto_replay_attempts=0,
        ),
        infrastructure_healthy=True,
    ),),
    validation='STATIC_EXAMPLE_ONLY_NOT_RUNTIME_EVIDENCE',
)
payload={
    'notice':'Illustrative contract sample only; values below are not real runtime evidence.',
    'incident_result':result.to_dict(),
    'response_envelope':IncidentEvidenceComposer(ROOT).compose('为什么 Gross Sales 的数据链路异常？','gross_sales',result).to_dict(),
}
OUT.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
print(OUT)
