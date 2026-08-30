# Phase 3C Runtime Acceptance Plan（运行时验收计划）

这份文件定义真实 Dagster + Docker 环境要保存什么证据。静态检查不能升级为 Runtime PASS。

## Evidence levels（证据层级）

```text
A · Policy Oracle（策略真值）
固定场景 + 人工定义 Expected Action

B · Static / Pure Python（静态 / 纯 Python）
Recovery Policy / Time Contract 是否满足 Oracle

C1 · Dagster Definition Runtime（定义运行时）
真实加载 Definitions，并在固定 scheduled_execution_time 下评估 ScheduleDefinition

C2 · Dagster Daemon Runtime（守护进程运行时）
真实 Schedule Tick / Sensor Tick / Run / Tags

D · Data Runtime（数据运行时）
同一次 exact-partition Run 是否真正完成 9 张 Mart
```

只有需要的 C2 + D 证据都成立，才能把“真实自动运行/自动恢复”写成 Runtime PASS。

## R01 · Normal schedule

R01 必须拆开，避免把历史手工重跑冒充真实 Schedule。

### R01-A · Historical schedule-definition contract

固定时间：

```text
scheduled_execution_time = 2026-08-06 00:15 UTC
expected partition       = 2026-08-05
```

运行真实已加载的 `shopify_daily_partition_schedule.evaluate_tick(...)`，证明：

- 只产生 1 个 RunRequest；
- `dagster/partition = 2026-08-05`；
- `run_key = 2026-08-05`；
- `commerce/automation = daily-schedule`。

它是 **C1 Definition Runtime**，不是 Daemon PASS。

### R01-B · Live daemon tick

启用 Daily Schedule，并观察任意一个真实未来的 `00:15 UTC` tick：

- Daemon 创建 `shopify_daily_partition_job`；
- Run 带 `commerce/automation=daily-schedule`；
- Run 的 partition 是 tick 时点最近已完成的日分区；
- Run 创建时间落在允许的 schedule launch tolerance 内。

因为历史 `2026-08-05` 的真实 tick 已经发生在 `2026-08-06 00:15 UTC`，现在不能再用 Daemon 重新制造它。

### R01-C · Exact-partition consumer completion

对 R01-B 的**同一个 run_id**验证 9 张消费者 Mart：

```text
orders
order_items
payment_transactions
refunds
refund_items
fulfillments
fulfillment_items
fulfillment_events
```

要求：

- 9/9 都存在 exact partition materialization；
- 每个 materialization 的 `event.run_id == schedule_run_id`；
- 全部 materialization timestamp <= partition deadline (`next day 01:00 UTC`)。

仅仅“历史上这个 partition 的 9 张表都出现过”不能 PASS。

## Remaining Runtime scenarios

### R02 · Missed schedule

R02 同样拆成证据层，避免把 Sensor Preview 冒充真实 Daemon Recovery：

```text
R02-A  persistent temp Dagster instance + real SensorDefinition
R02-B  real daemon sensor tick + stable run_key dedup
R02-C  recovery run exact partition 9/9 completion
```

固定故事时间：

```text
now                = 2026-08-06 01:05 UTC
newest overdue     = 2026-08-05
run records        = none
9 Mart partition   = incomplete
infrastructure     = healthy
```

R02-A 必须证明真实 Recovery Sensor 输出：

- `partition_key = 2026-08-05`；
- `run_key = shopify-daily-recovery:2026-08-05:attempt-1`；
- `commerce/automation = recovery-sensor`；
- `commerce/recovery = auto`；
- `commerce/recovery_attempt = 1`；
- `commerce/recovery_reason = missed_schedule_or_no_run`。

安全边界：只有 **newest overdue partition** 可以仅凭“无 Run”自动推断为
missed schedule。更老的历史无 Run 分区必须进入
`historical_no_run_requires_manual_backfill`，防止新部署自动回填过去 7 天。

R02-B 再用真实 Daemon 证明：同一个 stable `run_key` 不会创建第二个真实 Run。
R02-C 最终证明 Recovery Run 自己完成 9/9 exact-partition Mart。

### R03 · Infrastructure outage → recovery

R03 拆成四层，避免把 mock 的 service-down 当成真实 Docker outage：

```text
R03-A1 local Dagster adapter/retry probe
R03-A2 persistent state + real Recovery SensorDefinition transition
R03-B  real Docker spark-thrift outage + real Step Retry / failure tags
R03-C  real daemon recovery run after restore
R03-D  recovery-run same-run 9/9 exact-partition completion
```

固定故事：`2026-08-05` partition，Deadline 后时间 `2026-08-06 01:05 UTC`。

R03-A1 必须证明生产 `SparkComposeResource` 在 service unavailable 时：
- `max_retries=2`，因此总执行尝试数为 3；
- 最终 Run = FAILURE；
- `commerce/failure_class=infrastructure_unavailable`；
- source = `execution_adapter`，component = `spark-thrift`。

R03-A2 必须证明同一个历史失败在当前状态变化时产生不同决策：
- 当前 infrastructure unhealthy → `ALERT_AND_WAIT / infrastructure_unhealthy`，Sensor 不发 RunRequest；
- 当前 infrastructure healthy → `AUTO_REPLAY / infrastructure_failure_after_runtime_recovered`；
- recovery run_key = `shopify-daily-recovery:2026-08-05:attempt-1`；
- exact partition = `2026-08-05`。

R03-B/C/D 再保存真实 Docker、Daemon、Run Event 和 9/9 materialization 证据。

### R04 · Infrastructure still down

R04 验证“等待本身不消耗恢复预算”。固定故事：`2026-08-05` 已有一个
`infrastructure_unavailable` 的失败 Run，Deadline 为 `2026-08-06 01:00 UTC`。

```text
01:05 infra down -> ALERT_AND_WAIT / no RunRequest
01:10 infra down -> ALERT_AND_WAIT / no RunRequest
01:15 infra down -> ALERT_AND_WAIT / no RunRequest
01:20 infra healthy -> AUTO_REPLAY attempt-1
```

R04-A 使用 persistent temp Dagster instance + production SensorDefinition 证明：
- 三次 waiting tick 都是 `SkipReason`；
- exact-partition Run count 不增加；
- `auto_replay_attempts` 在每次 tick 前后都保持 0；
- runtime 恢复后第一次 RunRequest 仍是
  `shopify-daily-recovery:2026-08-05:attempt-1`。

R04-B 再用真实 Daemon + Docker 证明连续多个真实 Sensor Tick 期间没有
`commerce/recovery=auto` Run 被创建；恢复 `spark-thrift` 后才创建 attempt-1。

### R05 · dbt data-contract failure

R05 使用 acceptance-only singular Data Test：
`tests/acceptance/r05_force_data_contract_failure.sql`。默认 var=false 时返回 0 行，
不会污染正常 Daily Build；只有 R05 显式打开
`phase3c_r05_force_data_contract_failure=true` 时才返回 1 条违反记录。

R05-A1 必须通过生产 `execute_classified_dbt` 证明：
- `run_results.json` 中 R05 test result status = `fail`；
- Run = FAILURE；
- Run Tag = `commerce/failure_class=data_contract`；
- source = `dbt_artifact`，component = `dbt:test`；
- 即使外层 Job 配置 `max_retries=2`，Data Contract Failure 也只执行 1 次。

R05-A2 必须通过 persistent Dagster instance + production SensorDefinition 证明：
- exact partition 的失败类可以由 Run Storage 读回为 `data_contract`；
- Recovery Policy = `ALERT_MANUAL / data_contract_failure`；
- Sensor 返回 `SkipReason`，不产生跨 Run Replay；
- waiting/manual investigation 不消耗 auto replay budget。

`test status=error` 不得被过度归类为 Data Contract；它仍是 `unknown` 并 fail closed。
R05-B 再保存真实 Daily Partition / Docker / dbt Artifact 证据。

### R06 · Deterministic dbt project / Jinja failure

R06 使用 acceptance-only model：
`models/acceptance/r06_deterministic_code_probe.sql`。默认 var=false 时项目合法；
只有 R06 显式设置 `phase3c_r06_force_parse_failure=true` 时调用
`exceptions.raise_compiler_error(...)`。

这里刻意使用 `dbt parse --no-partial-parse`，而不是把所有 `dbt compile` 失败都
归类成代码错误：parse 不连接 warehouse；compile 可能需要 warehouse connection /
introspective query，所以 generic compile failure 在没有进一步结构化证据时必须保持
`unknown`。

R06-A1 必须通过生产 `execute_classified_dbt` 证明：
- `dbt parse` 失败；
- Run = FAILURE；
- Run Tag = `commerce/failure_class=deterministic_code`；
- source = `dbt_command`，component = `dbt:parse`；
- reason = `dbt_parse_failed`；
- 即使外层 Job 配置 `max_retries=2`，也只执行 1 次。

R06-A2 必须通过 persistent Dagster instance + production SensorDefinition 证明：
- exact partition 的失败类可以读回为 `deterministic_code`；
- Recovery Policy = `ALERT_MANUAL / deterministic_code_failure`；
- Sensor 返回 `SkipReason`，不产生跨 Run Replay；
- 人工修代码期间不消耗 auto replay budget。

R06-B 再保存真实 Daily Partition 中自然发生/显式注入的 parse/project failure 证据，
并在人工修复后另行验证 corrected replay；修复动作本身不属于自动恢复权限。

### R07 · Duplicate recovery guard

R07 验证 exact partition 的 active-owner 幂等保护。固定场景：历史 Daily Run 已失败，
第一次自动 Recovery attempt-1 已经持久化但仍处于 Active 状态。

```text
failed daily run
    ↓
auto recovery attempt-1 persisted / active
    ↓
Sensor evaluates again
    ↓
WAIT / active_run_owns_partition
    ↓
no second RunRequest / no second recovery Run
```

R07-A 必须通过 persistent Dagster instance + production SensorDefinition 证明：
- active recovery run 被 exact-partition State Reader 识别；
- attempt-1 已持久化，因此 `auto_replay_attempts=1`；
- `active_run` 判断优先于 `auto_replay_budget_exhausted`；
- Sensor 返回 `SkipReason`，不是第二个 `RunRequest`；
- partition Run count 不增加；
- `commerce/recovery=auto` 的 Run 数保持恰好 1；
- Sensor polling 本身不会把 replay budget 从 1 增加到 2。

R07-B 再用真实 Dagster Daemon 证明 stable `run_key` 的 framework-level dedup：
同一 Sensor 对已使用的 recovery run_key 不会创建第二个真实 Run。R07-A 不把这一点
冒充成已完成的 Daemon Runtime Evidence。

### R08 · Replay budget exhausted

R08 验证 cross-run 自动恢复是 bounded recovery，而不是无限重跑。固定场景：
第一次 automatic Recovery attempt-1 已经真实持久化，并最终 FAILURE。

```text
failed daily run
    ↓
auto recovery attempt-1
    ↓
attempt-1 FAILURE
    ↓
active_run=false / auto_replay_attempts=1
    ↓
Sensor evaluates again
    ↓
ALERT_MANUAL / auto_replay_budget_exhausted
    ↓
no attempt-2
```

R08-A 必须通过 persistent Dagster instance + production State Reader / SensorDefinition
证明：
- attempt-1 失败后不再属于 Active Owner；
- persisted `commerce/recovery=auto` Run 仍使 `auto_replay_attempts=1`；
- 即使 latest failure 仍是 replay-safe `transient_runtime`，预算判断也优先阻止第二次自动恢复；
- Sensor 返回 `SkipReason`，不是 attempt-2 `RunRequest`；
- partition Run count 不增加；
- automatic-recovery Run count 保持恰好 1。

R08-B 再用真实 Daemon + Docker 证明 attempt-1 实际执行失败后，后续真实 Sensor Tick
不会创建 attempt-2，并保存人工升级/告警证据。

### R09 · Run success ≠ partition completeness

R09 验证 Run Status 与消费者 exact-partition 完整性不是同一个合同。固定场景：
Daily Run 已经 SUCCESS，但 9 张消费者 Mart 只留下 8/9 的该分区 materialization。

```text
Run SUCCESS
    +
8/9 exact-partition Mart materializations
    ↓
successful_run=true / materialized=false
    ↓
ALERT_MANUAL / successful_run_without_complete_partition
    ↓
no auto replay
```

R09-A 必须通过 persistent Dagster instance +真实 AssetMaterialization events + production
State Reader / SensorDefinition 证明：
- SUCCESS Run 从 Run Storage 可见；
- 7 张 Mart 的 `2026-08-05` materialization 来自同一 SUCCESS run_id；
- 第 9 张 Mart 缺失并由 `missing_mart_asset_keys` 明确暴露；
- `successful_run_without_complete_partition` 优先于 no-run / missed-schedule 分支；
- Sensor 返回 `SkipReason`，不产生 Auto Recovery Run；
- replay budget 保持 0。

R09-B 再保存真实 dbt/Spark Daily Pipeline 中 Run 与消费者完整性不一致的 Runtime Evidence。
R09-A 不把 Dagster materialization presence 冒充成 Iceberg 表内 row-level completeness。

### R10 · Partition already complete

R10 验证 Recovery Decision 以当前 exact-partition 完整性为终止事实，而不是机械依赖
历史 Run Status。固定场景：Daily Run 历史上 FAILURE，但随后独立 repair/backfill run 已经
为 9 张消费者 Mart 写出同一 partition 的完整 materialization。

```text
historical Daily Run FAILURE
    +
9/9 current exact-partition Mart materializations
    ↓
failed_run=true / materialized=true
    ↓
NO_ACTION / partition_already_materialized
    ↓
no automatic recovery
```

R10-A 必须通过 persistent Dagster instance + production State Reader / SensorDefinition 证明：
- historical failed Daily Run 仍然存在且 failure class 仍可见；
- 独立 repair/backfill run 为 9/9 consumer Marts 写出 exact-partition materialization；
- `missing_mart_asset_keys=()` 且 `materialized=true`；
- `partition_already_materialized` 优先于 Freshness、Active Owner、Infrastructure、Replay Budget 和 historical failure class；
- Sensor 返回 `SkipReason`，不产生 Auto Recovery Run；
- replay budget 保持 0。

R10-B 再保存真实 dbt/Spark repair/backfill 后的 exact-partition 数据证据。R10-A 只证明
Dagster orchestration/event-store completeness，不把 8 个 materialization event 冒充成
Iceberg row-level completeness。


### R11 · Freshness guard

R11 验证 Recovery Permission 必须在消费者 Freshness Deadline 之后才出现。固定场景：
`2026-08-05` partition 正常 Schedule tick 为 `2026-08-06 00:15 UTC`，消费者 Deadline 为
`01:00 UTC`，在 `00:40 UTC` 分区仍可能未完成。

```text
00:15 Schedule tick
    ↓
00:40 partition incomplete
    ↓
freshness_overdue=false
    ↓
WAIT / within_freshness_budget
    ↓
no Recovery RunRequest

01:00 deadline
    ↓
partition becomes eligible for recovery evaluation
```

R11-A 必须证明两层 gate：
- Policy Gate：`freshness_overdue=false` 优先于 Active Owner、Infrastructure、Replay Budget 与 Failure Class，返回 `WAIT / within_freshness_budget`；
- Candidate Gate：`overdue_partition_keys(00:40)` 不包含 `2026-08-05`，而在 `01:00` 精确边界开始包含；
- persistent temp Dagster instance 中即使目标 partition 有 active Daily Run 且 9/9 尚未完成，Sensor 在 Deadline 前也不会为目标 partition 创建 Recovery Run；
- Sensor poll 不消耗 replay budget。

R11-B 再保存真实 Daemon + real Freshness/Run timing evidence。R11-A 不把本地固定时钟、
Policy evaluation 或 candidate selection 冒充成真实 Freshness daemon evidence。

### R12 · Unknown failure → fail closed

R12 验证失败分类证据不足时，系统在 Step Retry 和 Cross-run Recovery 两层都必须
fail closed。固定场景：Docker 命令存在、`spark-thrift` 当前也报告 Running，但执行返回
一个无法由结构化证据解释的非零退出码。

```text
command available + service running + ambiguous non-zero
    ↓
UNKNOWN
    ↓
no Step Retry
    ↓
failed exact-partition Run persists UNKNOWN
    ↓
ALERT_MANUAL / unknown_failure_class
    ↓
no Recovery RunRequest
```

R12-A 必须证明：
- `classify_command_failure()` 不从 stdout/stderr 文本猜测根因；
- `UNKNOWN` 已从 Step Retry whitelist 移除，即使 Job 定义 `max_retries=2` 也只执行 1 次；
- missing / invalid failure-class Run Tag 也被 State Reader 安全降级为 UNKNOWN；
- Deadline 后的 UNKNOWN failed partition 返回 `ALERT_MANUAL / unknown_failure_class`；
- production SensorDefinition 返回 `SkipReason`，不创建 Auto Recovery Run，replay budget 保持 0。

R12-B 再保存真实 Docker/Spark ambiguous fault + real daemon/manual escalation evidence。
R12-A 不把模拟非零退出、local Dagster state 或 SkipReason 冒充成生产故障根因诊断。


### R13 · Transient runtime timeout → retry → recovery

R13 验证 `transient_runtime` 的完整 replay-safe 链路。固定场景：`spark-thrift` 仍报告
Running，但一次 Spark/Docker 命令超过执行超时。

```text
service running + command timeout
    ↓
transient_runtime
    ↓
Step Retry max_retries=2
    ↓
3 total attempts / Run FAILURE
    ↓
Freshness overdue + current runtime healthy
    ↓
AUTO_REPLAY / transient_failure_after_runtime_recovered
    ↓
attempt-1 only
```

R13-A 必须通过 production `SparkComposeResource` + persistent Dagster instance +
production State Reader / Recovery Policy / SensorDefinition 证明：
- timeout 且 service 仍 Running 被分类为 `transient_runtime`；
- `transient_runtime` 属于 Step Retry whitelist，`max_retries=2` 对应总共 3 次尝试；
- 最终 failed Daily Run 的结构化 failure class 可由 State Reader 恢复；
- Deadline 后且当前 runtime healthy、无 active owner、budget=0 时，Policy 返回
  `AUTO_REPLAY / transient_failure_after_runtime_recovered`；
- Sensor 只为同一 exact partition 生成稳定的 `attempt-1` RunRequest。

R13-B/C/D 再分别保存真实 Spark timeout、真实 Daemon recovery Run creation、以及
9/9 exact-partition Mart completion 证据。R13-A 不冒充这些 Runtime Evidence。

## R01 commands

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

# C1: fixed historical definition evaluation
python acceptance/phase3c/r01_schedule_definition.py \
  --output acceptance/phase3c/evidence/r01/r01a_schedule_definition.json

# C2 + D: after a real daemon-launched schedule run
python acceptance/phase3c/r01_normal_schedule.py \
  --partition-key YYYY-MM-DD \
  --output acceptance/phase3c/evidence/r01/YYYY-MM-DD.json
```

## R02 commands

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r02_missed_schedule.py \
  --output acceptance/phase3c/evidence/r02/r02a_missed_schedule.json
```

## R03 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r03_infrastructure_recovery.py \
  --output acceptance/phase3c/evidence/r03/r03a_infrastructure_recovery.json
```

## R05 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r05_data_contract_failure.py \
  --output acceptance/phase3c/evidence/r05/r05a_data_contract_failure.json
```

Policy/Sensor-only proof when the dbt data plane is intentionally unavailable:

```bash
python acceptance/phase3c/r05_data_contract_failure.py --skip-dbt-runtime
```

## R04 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r04_infrastructure_still_down.py \
  --output acceptance/phase3c/evidence/r04/r04a_infrastructure_still_down.json
```

## R06 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r06_deterministic_code_failure.py \
  --output acceptance/phase3c/evidence/r06/r06a_deterministic_code_failure.json
```

Policy/Sensor-only proof when dbt packages/runtime are intentionally unavailable:

```bash
python acceptance/phase3c/r06_deterministic_code_failure.py --skip-dbt-runtime
```

## R07 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r07_duplicate_recovery_guard.py \
  --output acceptance/phase3c/evidence/r07/r07a_duplicate_recovery_guard.json
```

## R08 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r08_replay_budget_exhausted.py \
  --output acceptance/phase3c/evidence/r08/r08a_replay_budget_exhausted.json
```

## Required artifacts（必须保存的证据）

每个 Runtime Scenario 保存：

```text
partition_key
run_id / recovery_run_id
run tags
failure_class / reason_code
sensor evaluation result
run_key
9 Mart exact-partition materialization presence
timestamps: scheduled / created / started / completed / deadline
```

Spark / Iceberg Query Plan 属于 Physical Runtime Evidence，不和 Orchestration Acceptance 混为一个 PASS。

## R09 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r09_success_incomplete_partition.py \
  --output acceptance/phase3c/evidence/r09/r09a_success_incomplete_partition.json
```

## R10 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r10_partition_already_complete.py \
  --output acceptance/phase3c/evidence/r10/r10a_partition_already_complete.json
```


## R11 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r11_freshness_guard.py \
  --output acceptance/phase3c/evidence/r11/r11a_freshness_guard.json
```

## R12 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r12_unknown_failure_fail_closed.py \
  --output acceptance/phase3c/evidence/r12/r12a_unknown_failure_fail_closed.json
```


## R13 command

```bash
export DAGSTER_HOME="$PWD/infra/dagster"
export PYTHONPATH="$PWD/orchestration/dagster"

python acceptance/phase3c/r13_transient_runtime_recovery.py \
  --output acceptance/phase3c/evidence/r13/r13a_transient_runtime_recovery.json
```
