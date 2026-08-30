# Phase 5F · Governed Analysis Session

> 受治理的分析会话状态。目标不是保存“聊天记忆”，而是把已经验证过的 Semantic Query Spec 作为结构化状态保存，并让后续追问只提交一个可审计的 Delta。

## 1. 为什么需要 Session State

Phase 5E 解决的是一次候选值确认：

```text
Coca Colaa
→ candidate Coca-Cola
→ user confirms
→ resume frozen plan
```

真实业务分析会继续追问：

```text
2026-08-01 ~ 2026-08-05 按天看 gross_sales
→ 那只看 West 呢？
→ 那再加上 AOV
```

如果每一轮都把完整聊天历史重新交给 Router / Planner，metric、time range、grain、filter 都可能发生状态漂移。Phase 5F 将它们冻结为 `AnalysisSessionState`。

## 2. 状态模型

```text
AnalysisSessionState
├── session_id
├── revision / turn_count
├── original_question
├── current_spec
│   ├── metrics
│   ├── start_time / end_time
│   ├── group_by
│   ├── structured filters
│   └── limit
├── structured turn history
└── integrity_checksum
```

它不保存自由聊天 transcript 作为查询真相。`current_spec` 才是下一轮分析的唯一可执行状态。

## 3. Delta 类型

当前第一版只允许小而明确的变更：

```text
ADD_FILTER
REPLACE_FILTER
REMOVE_FILTER
ADD_METRIC
```

例如：

```text
那只看 West 呢？
```

只产生：

```text
ADD_FILTER
store__region = West
```

原有：

```text
metric = gross_sales
time   = 2026-08-01 .. 2026-08-05
grain  = metric_time__day
```

全部继承。

第二轮：

```text
那再加上 AOV
```

只产生：

```text
ADD_METRIC
average_order_value
```

West filter、日期和 daily grain 不重新解析。

## 4. 同 Dimension Filter 更新

```text
West
→ 下一轮：那看 South 呢？
```

不是：

```text
region = West
AND region = South
```

而是：

```text
REPLACE_FILTER
store__region = South
```

这由 `same_dimension_filter_update_replaces_not_duplicates` 合同锁定。

## 5. Fail-Closed

以下追问不会执行查询：

```text
换个角度看看
where region='West'
region=West
```

结果分别进入 `CLARIFICATION_REQUIRED` 或 `BLOCKED`。未解析的 Delta 不会被忽略，也不会回退成旧条件继续查询。

## 6. Integrity

每个 Session Revision 都基于以下内容重新计算 SHA-256：

```text
session id
original question
current semantic spec
revision / turn count
last question
structured turn history
```

它用于检测状态被意外修改，但不是用户认证签名，也不是生产 Session Store。

## 7. Runtime Boundary

Phase 5F 只拥有 Session State，不拥有 MetricFlow Runtime。

真实查询需要同时满足：

```text
PHASE5F_ALLOW_SESSION_EXECUTION=true
PHASE5B_ALLOW_METRICFLOW_QUERY=true
```

随后仍然走已有：

```text
READY SemanticQueryPlan
→ MetricFlow Explain
→ PASS
→ MetricFlow Query
```

当前手机 / static 环境只证明 Session Contract 与 Query Plan Continuation，真实数值查询保持 `DEFERRED`。

## 8. CLI

初始化：

```bash
PYTHONPATH=. python agent/session_cli.py start \
  "2026-08-01 到 2026-08-05 按天看 gross_sales" \
  --metrics gross_sales \
  --state /tmp/commerce-session.json
```

追问：

```bash
PYTHONPATH=. python agent/session_cli.py follow-up \
  "那只看 West 呢？" \
  --state /tmp/commerce-session.json
```

继续：

```bash
PYTHONPATH=. python agent/session_cli.py follow-up \
  "那再加上 AOV" \
  --state /tmp/commerce-session.json
```

## 9. Source Ownership

Phase 5F 不复制业务定义：

```text
Metric formula / semantic relation → dbt + MetricFlow
Dimension value universe           → Phase 5C / MetricFlow Runtime
Value resolution                   → Phase 5D
Single-value clarification         → Phase 5E
Multi-turn analysis state          → Phase 5F
Physical data                      → Iceberg / Polaris
```

Shopify dbt Source YAML 本阶段不变。

## 10. Static Acceptance

```bash
./infra/runtime/run_phase5f_analysis_session_static.sh
```

覆盖：

- stable initial state；
- no-reparse follow-up；
- filter inheritance；
- metric inheritance；
- same-dimension replacement；
- filter removal；
- unknown follow-up fail-closed；
- SQL / raw where block；
- checksum tamper guard；
- runtime gate handoff；
- session mutation is orchestrator state, not an LLM-callable Tool。
