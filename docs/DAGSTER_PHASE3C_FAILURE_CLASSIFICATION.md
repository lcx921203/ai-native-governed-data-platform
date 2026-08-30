# Phase 3C — Structured Failure Classification（结构化失败分类）

Failure Message（失败信息） ≠ Failure Class（失败类别）.

Automation only trusts structured evidence:

```text
Docker missing / required service down  → infrastructure_unavailable
Command timeout while service alive     → transient_runtime
dbt parse non-zero                       → deterministic_code
dbt compile non-zero without proof       → unknown
dbt test node status = fail              → data_contract
Other non-zero / ambiguous outcomes      → unknown
```

`UNKNOWN` deliberately fails closed at both retry layers: it receives neither bounded
Step Retry nor cross-run automatic replay. Free-text log keywords are never used to
upgrade ambiguous evidence into a replay-safe class.
