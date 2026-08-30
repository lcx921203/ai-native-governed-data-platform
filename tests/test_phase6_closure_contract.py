from __future__ import annotations

import os
from pathlib import Path

import yaml

ROOT=Path(__file__).resolve().parents[1]


def load(rel):
    return yaml.safe_load((ROOT/rel).read_text(encoding='utf-8'))


def test_phase6_manifest_closes_6a_6b_6c_file_contracts():
    manifest=load('agent/contracts/phase6_capability_manifest.yml')
    assert set(manifest['phases'])=={'6A','6B','6C','6D','6E','6F'}
    for phase,item in manifest['phases'].items():
        assert item['runtime_evidence']=='DEFERRED'
        for rel in [item['policy'],*item['implementation'],*item['tests'],item['static_runner'],item['live_runner']]:
            assert (ROOT/rel).exists(), f'{phase}: missing {rel}'
        assert os.access(ROOT/item['static_runner'],os.X_OK)
        assert os.access(ROOT/item['live_runner'],os.X_OK)


def test_phase6b_requires_phase6a_runtime_and_metricflow_gates():
    manifest=load('agent/contracts/phase6_capability_manifest.yml')
    gates=set(manifest['phases']['6B']['runtime_gates'])
    assert gates=={
        'PHASE6B_ALLOW_DRIVER_ATTRIBUTION',
        'PHASE6A_ALLOW_ANOMALY_QUERY',
        'PHASE5B_ALLOW_METRICFLOW_QUERY',
    }


def test_phase6c_requires_full_diagnostic_runtime_chain():
    manifest=load('agent/contracts/phase6_capability_manifest.yml')
    gates=set(manifest['phases']['6C']['runtime_gates'])
    assert gates=={
        'PHASE6C_ALLOW_DIAGNOSTIC',
        'PHASE6B_ALLOW_DRIVER_ATTRIBUTION',
        'PHASE6A_ALLOW_ANOMALY_QUERY',
        'PHASE5B_ALLOW_METRICFLOW_QUERY',
    }


def test_phase6b_policy_never_exposes_sql_or_cross_lens_contribution_sum():
    policy=load('agent/contracts/driver_attribution_policy.yml')
    assert policy['principles']['arbitrary_sql'] is False
    assert policy['principles']['arbitrary_where_clause'] is False
    assert policy['principles']['driver_dimensions_are_independent_lenses'] is True
    assert policy['principles']['contributions_must_not_be_summed_across_lenses'] is True
    assert policy['principles']['metricflow_explain_before_each_driver_query'] is True


def test_phase6c_policy_requires_exact_partition_health_and_claim_ledger():
    policy=load('agent/contracts/diagnostic_orchestrator_policy.yml')
    assert policy['principles']['operational_health_uses_exact_partition_current_truth'] is True
    assert policy['principles']['latest_run_status_is_not_operational_health_truth'] is True
    assert policy['principles']['claim_ledger_required_before_llm_rendering'] is True
    assert policy['principles']['runtime_observation_requires_runtime_verified_evidence'] is True
    assert policy['principles']['cross_lens_contributions_must_not_be_summed'] is True
    assert policy['principles']['arbitrary_sql'] is False


def test_all_phase6_live_gates_are_fail_closed_in_env_example():
    text=(ROOT/'.env.example').read_text(encoding='utf-8')
    manifest=load('agent/contracts/phase6_capability_manifest.yml')
    for gate in {g for item in manifest['phases'].values() for g in item['runtime_gates']}:
        assert f'{gate}=false' in text, gate


def test_phase5_canonical_materialization_is_a_phase6_prerequisite():
    manifest=load('agent/contracts/phase6_capability_manifest.yml')
    assert manifest['principles']['phase5_closure_remains_prerequisite'] is True
    assert manifest['principles']['canonical_phase5_materialization_precedes_phase6_tests'] is True
    assert (ROOT/'infra/runtime/sync_phase5_contracts.py').exists()


def test_phase6d_reuses_phase3c_failure_and_recovery_truth():
    policy=load('agent/contracts/operational_incident_policy.yml')
    p=policy['principles']
    assert p['exact_partition_current_truth_precedes_run_status'] is True
    assert p['structured_failure_tags_only'] is True
    assert p['free_text_log_cause_inference'] is False
    assert p['phase3c_failure_classification_is_source_of_truth'] is True
    assert p['phase3c_recovery_policy_is_source_of_truth'] is True
    assert p['recovery_policy_decision_is_not_observed_recovery_execution'] is True


def test_phase6d_requires_incident_and_diagnostic_gates_only():
    manifest=load('agent/contracts/phase6_capability_manifest.yml')
    gates=set(manifest['phases']['6D']['runtime_gates'])
    assert gates=={'PHASE6D_ALLOW_INCIDENT_DRILLDOWN','PHASE6C_ALLOW_DIAGNOSTIC'}


def test_phase6e_is_advisory_only_and_requires_incident_chain():
    manifest=load('agent/contracts/phase6_capability_manifest.yml')
    gates=set(manifest['phases']['6E']['runtime_gates'])
    assert gates=={
        'PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING',
        'PHASE6D_ALLOW_INCIDENT_DRILLDOWN',
        'PHASE6C_ALLOW_DIAGNOSTIC',
    }
    policy=load('agent/contracts/incident_response_policy.yml')
    p=policy['principles']
    assert p['phase3c_recovery_policy_is_execution_authority'] is True
    assert p['agent_never_launches_recovery_run'] is True
    assert p['agent_never_launches_manual_backfill'] is True
    assert p['auto_replay_is_delegated_to_existing_dagster_recovery_sensor'] is True
    assert p['manual_recovery_requires_human_approval'] is True
    assert policy['runtime']['writes_enabled'] is False


def test_phase6f_approval_is_not_execution_and_requires_response_chain():
    manifest=load('agent/contracts/phase6_capability_manifest.yml')
    gates=set(manifest['phases']['6F']['runtime_gates'])
    assert gates=={
        'PHASE6F_ALLOW_APPROVAL_WORKFLOW',
        'PHASE6E_ALLOW_INCIDENT_RESPONSE_PLANNING',
    }
    policy=load('agent/contracts/approval_workflow_policy.yml')
    p=policy['principles']
    assert p['approval_is_not_execution'] is True
    assert p['approval_does_not_override_phase3c_recovery_policy'] is True
    assert p['current_truth_must_be_revalidated_before_external_execution'] is True
    assert p['evidence_change_invalidates_execution_eligibility'] is True
    assert p['agent_cannot_self_approve'] is True
    assert p['agent_cannot_execute_approved_action'] is True
    assert p['audit_events_are_append_only_and_hash_chained'] is True
    assert p['audit_hash_chain_is_not_identity_signature'] is True
    assert policy['runtime']['production_action_writes_enabled'] is False
    env=(ROOT/'.env.example').read_text(encoding='utf-8')
    assert 'PHASE6F_ALLOW_APPROVAL_AUDIT_WRITE=false' in env
