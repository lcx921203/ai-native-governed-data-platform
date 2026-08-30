# Phase 3C · R13 Transient Runtime Timeout → Retry → Recovery

R13 isolates the replay-safe `transient_runtime` class from infrastructure outages.
The service may still be healthy while one command execution times out.

```text
spark-thrift reports Running
    +
command timeout
    ↓
transient_runtime
    ↓
Step Retry max_retries=2
    ↓
3 total attempts
    ↓
Run FAILURE
    ↓
Freshness overdue + current runtime healthy
    ↓
AUTO_REPLAY attempt-1
```

## Why this is different from infrastructure_unavailable

`infrastructure_unavailable` means the execution adapter can positively prove that the
command/service boundary itself is unavailable. `transient_runtime` means the command
boundary exists and the service is still reported Running, but this execution exceeded the
bounded timeout.

Neither class is inferred from free-text logs. Both are replay-safe for bounded Step Retry,
but cross-run recovery still requires Freshness breach, current runtime health, no active
owner, incomplete exact partition, and remaining replay budget.

## Acceptance levels

- **R13-A**: local persistent Dagster harness; mock only subprocess timeout/current health.
- **R13-B**: real Docker/Spark timeout with real retry events and persisted failure tags.
- **R13-C**: real daemon creates one recovery attempt after the runtime is healthy.
- **R13-D**: the recovery run completes the same exact partition with 9/9 consumer Marts.

R13-A must not be reported as evidence of a real Spark timeout, daemon-created Run, or data
completion.
