# Phase 5E · Governed Clarification & Plan Continuation

> 受治理的澄清与查询计划续跑。目标不是让 Agent 更“会猜”，而是在模糊筛选值出现时暂停查询、请求用户确认，并在确认后从被冻结的 Query Plan 继续，而不是重新理解整句话。

## 1. 为什么需要这一层

Phase 5D 已经可以把 `Coca Colaa` 识别为一个高相似候选 `Coca-Cola`，但它故意不会自动把 fuzzy candidate 变成查询谓词。

如果没有 Phase 5E，系统只有两个坏选择：

1. 为了方便，直接猜 `Coca-Cola` 并查询；
2. 用户确认后，把原问题重新交给 Router / Planner，从头理解一遍。

前者可能查询错，后者会造成多轮状态漂移。Phase 5E 增加一个受控的 Continuation State。

```text
Original Question
    ↓
Phase 5A-5D Planner
    ↓
CLARIFICATION_REQUIRED
    ↓
Freeze already validated query context
    ├── metrics
    ├── time window
    ├── group-by
    ├── already resolved filters
    └── governed candidate set
    ↓
Ask user one clarification
    ↓
User confirms one stored candidate
    ↓
Append exactly one confirmed Structured Filter
    ↓
READY Semantic Query Plan
    ↓
MetricFlow Explain
    ↓
MetricFlow Query
```

## 2. 核心边界

`clarification_policy.yml` 固定以下规则：

- original query context is immutable；
- resume 时不得重新 parse / route 原始问题；
- 一次 Continuation 最多一个待确认 filter；
- 用户只能选择 Continuation 里已经存在的 governed candidate；
- fuzzy candidate 必须用户确认；
- reject / unknown reply 不允许触发查询；
- raw SQL / raw where 仍然不存在。

因此：

```text
“对”
```

不是一个新的自由输入 predicate。它只在“当前 continuation 恰好只有一个候选”时有意义。

## 3. Semantic Query Plan 的变化

`SemanticQueryPlan` 新增：

```python
continuation_spec: SemanticQuerySpec | None
clarification: SemanticQueryClarification | None
```

当用户问：

```text
2026-08-05 美国 品牌为 Coca Colaa 的 gross_sales 是多少？
```

Planner 不再只返回一条 warning，而是返回：

```text
status = CLARIFICATION_REQUIRED

continuation_spec:
  metric      = gross_sales
  time        = 2026-08-05
  filters     = store__country = US

pending clarification:
  raw         = Coca Colaa
  candidate   = item__brand = Coca-Cola
  mode        = FUZZY_CANDIDATE
```

注意 `US` 已经被冻结。确认品牌以后，系统不会再重新从原句里解析“美国”。

## 4. Continuation State

路径：

```text
agent/clarification/contracts.py
agent/clarification/continuation.py
```

Continuation 包含：

```text
continuation_id
original_question
base_spec
raw_value
dimension_hint
candidates
clarification_prompt
evidence
source_mode
integrity_checksum
```

`integrity_checksum` 是对冻结状态的 SHA-256 校验，用于检测状态被意外修改。它不是身份认证签名；真实 Session Store / Auth 仍属于后续 Runtime 层。

## 5. Confirmation 规则

### 单候选 + 明确认可

```text
Agent: 你指的是 Coca-Cola 吗？
User: 对
```

结果：

```text
item__brand = Coca-Cola
source = user_confirmed:FUZZY_CANDIDATE:STATIC_CONTRACT
```

### 多候选

可以通过：

```text
1
2
CAND01
CAND02
```

显式选择。

如果多个候选 value 相同、dimension 不同，也可以明确回答：

```text
品牌
```

或：

```text
地区
```

来选择候选 Dimension。

### 拒绝

```text
不是
```

结果：

```text
REJECTED
query execution = 0
```

### 无法唯一选择

```text
随便吧
```

结果仍是：

```text
CLARIFICATION_REQUIRED
query execution = 0
```

## 6. 为什么确认后不重新走 Router

Resume 的实现不会调用：

```python
planner.plan(...)
planner.plan_metrics(...)
```

它只做：

```text
validate frozen continuation
→ select stored candidate
→ append one Structured Filter
→ build READY plan
```

因此多轮对话不会因为第二句话“对”而丢失第一轮已经解析出的 metric、日期、group-by 或其他 filter。

## 7. Runtime 边界

Phase 5E 自己不拥有 MetricFlow Runtime。确认以后如果 `execute=false`，只产生 READY Plan。

如果要继续真实查询：

```text
PHASE5E_ALLOW_CONTINUATION_EXECUTION=true
+
PHASE5B_ALLOW_METRICFLOW_QUERY=true
```

随后仍然进入原有：

```text
MetricFlow Explain
→ PASS
→ MetricFlow Query
```

没有新的 SQL 旁路。

当前手机/static 环境：

```text
Continuation prepare       PASS
Candidate confirmation     PASS
READY plan continuation    PASS
Real MetricFlow query      DEFERRED
```

## 8. CLI

创建 Continuation：

```bash
PYTHONPATH=. python agent/clarification_cli.py start \
  "2026-08-05 品牌为 Coca Colaa 的 gross_sales 是多少？" \
  --metric gross_sales \
  --state /tmp/clarification.json
```

确认并只生成 READY Plan：

```bash
PYTHONPATH=. python agent/clarification_cli.py resume \
  --state /tmp/clarification.json \
  --reply "对"
```

真实 Runtime 执行还需 `--execute` 与 Runtime gates。

## 9. Evidence / Answer Boundary

Router Executor 现在把 Tool 的 `CLARIFICATION_REQUIRED` 映射为独立执行状态，而不是泛化成 `STOPPED`。

Response Envelope 也新增：

```text
AnswerStatus.CLARIFICATION_REQUIRED
ClaimKind.CLARIFICATION_REQUEST
```

因此 Answer Renderer 能明确向用户提出确认问题，而不是把它包装成“部分答案”。

## 10. Source ownership 没有变化

Phase 5E 只保存 Query Continuation State，不重新定义任何业务真相：

```text
Metric formula / semantic relation  → dbt + MetricFlow
Dimension value universe            → Phase 5C / MetricFlow Runtime
Natural-language aliases            → governed policies
User confirmation                   → Phase 5E session state
Physical source                     → Iceberg / Polaris
Runtime execution                    → MetricFlow + Dagster
```

dbt Shopify Source YAML 本阶段没有改变；Agent 仍然只消费 Semantic Layer，而不侵入 Source / Staging 的职责。

## 11. Static acceptance

入口：

```bash
./infra/runtime/run_phase5e_clarification_static.sh
```

覆盖：

- structured resumable clarification；
- deterministic continuation id/checksum；
- no-replan resume；
- confirm / reject / unknown reply；
- tamper block；
- ordinal candidate selection；
- resolved filter preservation；
- runtime gate handoff；
- explicit clarification response status；
- clarification state is orchestrator state, not an LLM-callable tool。
