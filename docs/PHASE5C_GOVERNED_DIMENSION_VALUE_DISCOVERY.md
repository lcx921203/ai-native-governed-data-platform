# Phase 5C · Governed Dimension Value Discovery

## 目标

Phase 5B 已经允许 Agent 把自然语言过滤条件转换成受治理的 Dimension Filter，例如：

```text
美国西部地区
→ store__country = US
→ store__region  = West
```

但生产系统中的维度值会不断变化。品牌、地区、州、品类等不能长期依赖手工维护的自然语言 Alias 表。

Phase 5C 增加一个独立的 **Dimension Value Discovery（维度值发现）** Tool：

```text
User
  ↓
Intent: DIMENSION_VALUE_DISCOVERY
  ↓
Governed Metric Context
  +
Governed Dimension
  ↓
get_dimension_values
  ↓
MetricFlow list dimension-values
  ↓
Runtime Values
```

这层仍然不向 Agent 暴露 SQL，也不允许任意 `where`。

---

## 为什么必须带 Metric Context

维度是否可用并不是全局事实，而是和 MetricFlow Semantic Graph 的可达路径有关。

因此：

```text
“有哪些地区？”
```

不能直接猜一个全局 `region` 值域。

必须变成：

```text
“gross_sales 有哪些地区可以筛？”
```

然后查询：

```text
metrics   = gross_sales
dimension = store__region
```

多指标也允许，但最多 3 个，并共享同一个 MetricFlow 语义上下文。

---

## Source of Truth 边界

### Runtime

真实环境使用：

```bash
mf list dimension-values \
  --metrics gross_sales \
  --dimension store__region
```

可选时间范围：

```bash
mf list dimension-values \
  --metrics gross_sales \
  --dimension store__region \
  --start-time 2026-08-01T00:00:00Z \
  --end-time 2026-08-05T23:59:59Z
```

Runtime 成功后：

```text
source_mode = METRICFLOW_RUNTIME
evidence    = RUNTIME_VERIFIED
status      = COMPLETE
```

### 当前手机 / Static 环境

当前没有 Spark / Polaris / MetricFlow Runtime，所以 Tool 不伪造 Runtime 结果。

它只允许使用 Phase 5B 已经声明的 Repo-managed Seed 作为 **Reference Fallback（参考回退）**：

```text
store__region
→ seed_stores.csv
→ West, South
```

返回：

```text
source_mode = STATIC_SEED_FALLBACK
evidence    = STATIC_CONTRACT
status      = DEFERRED
```

因此：

> Static Seed Reference ≠ 当前生产 Runtime Value Universe。

---

## 与 Phase 5B Alias 的关系

Alias 只负责自然语言翻译：

```text
西部 → West
美国 → US
```

它不是维度值 Source of Truth。

例如 `seed_stores.csv` 当前有：

```text
West
South
```

即使 `South` 没有任何中文 Alias，Phase 5C 仍然会从 Seed / Runtime 发现它。

这避免了：

```text
“Alias YAML 里没有”
=
“业务数据里不存在”
```

这种错误。

---

## Tool Contract

公开 Tool：

```text
get_dimension_values
```

输入：

```json
{
  "metrics": ["gross_sales"],
  "dimension": "store__region",
  "question": "gross_sales 有哪些地区可以筛？",
  "limit": 25
}
```

限制：

```text
metrics       1–3
Dimension     必须在 Governed Filter Dimension Allowlist
values        默认 25，最多 50
时间范围       可选，最长 366 天
SQL           禁止
raw where     禁止
```

---

## Router

新增 Intent：

```text
DIMENSION_VALUE_DISCOVERY
```

例如：

```text
gross_sales 有哪些地区可以筛？
```

规划：

```text
DIMENSION_VALUE_DISCOVERY
  ↓
metric = gross_sales
dimension = store__region
  ↓
get_dimension_values
```

而：

```text
有哪些地区可以筛？
```

由于缺少 Metric Context：

```text
NEEDS_DISCOVERY
```

不会自动挑一个 Metric。

---

## Evidence Contract

### Static

```text
West, South
```

可以作为 Reference Claim 展示，但必须附带：

```text
STATIC_SEED_FALLBACK
STATIC_CONTRACT
Runtime DEFERRED
```

### Runtime

只有真实 `mf list dimension-values` 成功后，才允许：

```text
ClaimKind.DIMENSION_VALUES
runtime_observed = true
evidence = RUNTIME_VERIFIED
```

---

## 工程文件

```text
agent/
├── dimension_values/
│   ├── __init__.py
│   ├── contracts.py
│   ├── planner.py
│   ├── executor.py
│   └── tool.py
│
├── contracts/
│   ├── dimension_value_policy.yml
│   ├── intent_routing.yml
│   └── tool_schemas.json
│
├── dimension_values_cli.py
├── build_dimension_value_samples.py
└── generated/
    └── dimension_value_samples.json

tests/
└── test_phase5c_dimension_value_discovery.py

infra/runtime/
├── run_phase5c_dimension_values_static.sh
└── run_phase5c_metricflow_discovery_live.sh
```

---

## Runtime Gate

真实 Dimension Value Discovery 必须显式：

```bash
PHASE5C_ALLOW_METRICFLOW_DISCOVERY=true
```

否则不会执行 MetricFlow Runtime。

示例：

```bash
PHASE5C_ALLOW_METRICFLOW_DISCOVERY=true \
./infra/runtime/run_phase5c_metricflow_discovery_live.sh
```

当前没有 workstation，所以真实 Runtime Acceptance 继续：

```text
DEFERRED
```

---

## Phase 5C 不做什么

Phase 5C 不会：

- 把 Runtime Value 自动写回 Alias YAML；
- 自动创造中文业务别名；
- 把所有维度值永久缓存成第二套 Source of Truth；
- 绕开 MetricFlow Semantic Graph；
- 执行 SQL；
- 把 Static Seed 说成 Production Runtime Result。

下一阶段可以在这个基础上解决：

```text
用户输入一个没有 Alias 的真实值
→ Dimension Value Resolution
→ 精确匹配 / 候选匹配
→ Structured Filter
→ Semantic Query
```

这会把“发现有哪些值”和“把用户输入值安全转换成 Filter”连接起来。
