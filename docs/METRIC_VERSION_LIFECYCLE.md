# Metric Version Lifecycle｜指标版本生命周期治理

> 本文描述当前源码已经实现的 Metric Version Lifecycle（指标版本生命周期）静态治理能力。指标公式仍由 **dbt + MetricFlow** 拥有；版本、状态、生效关系和变更审计属于 Governance（治理）契约。真实 Runtime 数字是否通过，仍由 MetricFlow Runtime Acceptance + Golden Reconciliation 证明。

## 1. 为什么需要单独做指标版本治理

Regression Test（回归测试）只能回答：**“这次改动有没有让已有结果发生变化？”**

Metric Version Lifecycle 要回答的是：

- 这次变化是有意还是无意？
- 它是不是 Breaking Change（破坏性变更）？
- 新口径从哪一天开始生效？
- 旧版本什么时候 Deprecated（弃用）或 Retired（退役）？
- 新版本替代的是哪个旧版本？
- 历史数据采用 Backfill（历史回补）还是 Forward Fix（前向修复）？
- Agent / BI / API 当前应该消费哪个版本？

因此项目把“当前可消费注册表”和“历史生命周期账本”拆开。

## 2. 当前源码结构

```text
dbt / MetricFlow
  └─ canonical metric formula
       ↓ definition fingerprint
metadata/datahub/governance/metric_lifecycle.yml
  └─ append-only version history
       ↓ current version
metadata/datahub/governance/metric_registry.yml
  └─ current governed consumer surface
       ↓
Agent / DataHub / Serving / CI
```

关键文件：

```text
metadata/datahub/governance/metric_registry.yml
metadata/datahub/governance/metric_lifecycle.yml
metadata/datahub/tools/validate_metric_lifecycle.py
tests/test_metric_version_lifecycle_contract.py
.github/workflows/ci.yml
```

## 3. Current Registry（当前注册表）

`metric_registry.yml` 只表达**当前允许被受治理消费者使用的版本**：

```yaml
- id: average_order_value
  glossary_term: commerce-metric-average-order-value
  current_version: 1
```

它不保存完整历史，因此 Agent 的普通查询仍然只看到一个明确的当前版本。

## 4. Lifecycle Ledger（生命周期账本）

`metric_lifecycle.yml` 保存历史版本：

```yaml
- metric_id: average_order_value
  version: 1
  status: ACTIVE
  change_type: BASELINE
  governance_adopted_at: '2026-08-30'
  effective_from: null
  effective_to: null
  supersedes_version: null
  business_owner: commerce-analytics
  technical_owner: data-platform
  definition_authority: dbt_metricflow
  definition_fingerprint: sha256:...
```

### 为什么 baseline 的 `effective_from` 可以为空

2026-08-30 是**版本治理能力接入日期**，并不代表这些指标在这一天才开始使用。当前仓库没有足够历史证据证明所有已有指标最初的真实业务生效日期，因此 V1 采用：

```text
governance_adopted_at = 2026-08-30
effective_from = null
change_type = BASELINE
```

这样不会伪造历史事实。

未来新增 V2 / V3 时，`effective_from` 必须明确。

## 5. Lifecycle Status（生命周期状态）

当前契约允许：

| 状态 | 含义 |
|---|---|
| `DRAFT` | 已定义但尚未作为当前生产口径 |
| `ACTIVE` | 当前受治理消费者默认使用的版本 |
| `DEPRECATED` | 仍保留历史语义，但不再推荐作为当前版本 |
| `RETIRED` | 已结束有效期，必须有 `effective_to` |

同一个 Metric 在当前 registry 中必须恰好指向一个 `ACTIVE` 版本。

## 6. Change Type（变更类型）

当前契约允许：

- `BASELINE`：第一次把已有指标纳入生命周期治理。
- `NON_BREAKING`：不改变业务数字语义的兼容变化。
- `BREAKING`：会改变业务含义、数字或合法分析路径的变化。

典型 Breaking Change：

```text
公式变化
SUM ↔ AVG / COUNT DISTINCT
Grain（粒度）变化
Entity（实体）变化
Business Time（业务时间）变化
固定 Filter（过滤条件）变化
Conversion Window（转化窗口）变化
Join Path（关联路径）变化
```

展示文案、描述、Label 变化不会进入 Definition Fingerprint，因此不强制升业务版本。

## 7. Definition Fingerprint（定义指纹）

CI 会对当前 canonical dbt / MetricFlow 语义定义计算 SHA-256：

```text
Metric ID
+ Source Semantic Model
+ 影响计算语义的 Metric 定义
→ canonical JSON
→ SHA-256
```

例如有人直接把：

```yaml
name: gross_sales
agg: sum
expr: gross_sales_amount
```

改成：

```yaml
expr: gross_sales_amount + 1
```

却仍然把 registry 指向 `gross_sales v1`，CI 会失败：

```text
definition fingerprint drift
```

这表示：**当前 ACTIVE 版本被静默重写了。**

如果这是业务批准的新口径，应创建 V2，而不是修改 V1 的历史治理记录。

## 8. 一次 V1 → V2 Breaking Change 应该怎么做

假设 `average_order_value` 从旧公式切换到新公式。

### Step 1｜业务确认 Breaking Change

先明确：

```text
为什么改
新公式是什么
影响哪些 Consumer
生效日期
历史策略
Owner / Reviewer
```

### Step 2｜修改 canonical MetricFlow 定义

公式仍只改 dbt / MetricFlow，不在治理文件里复制公式。

### Step 3｜生命周期账本追加 V2

示意：

```yaml
- metric_id: average_order_value
  version: 1
  status: DEPRECATED
  change_type: BASELINE
  effective_from: null
  effective_to: '2026-08-31'
  supersedes_version: null
  definition_fingerprint: sha256:<v1>

- metric_id: average_order_value
  version: 2
  status: ACTIVE
  change_type: BREAKING
  effective_from: '2026-09-01'
  effective_to: null
  supersedes_version: 1
  definition_fingerprint: sha256:<v2>
```

Lifecycle Ledger（生命周期账本）是 append-only（只追加）历史；旧版本不能被删除后假装从未存在。

### Step 4｜Current Registry 切到 V2

```yaml
- id: average_order_value
  current_version: 2
```

### Step 5｜选择历史策略

**Backfill（历史回补）**：历史数据全部按新口径重算。

适合：业务希望整个时间序列保持统一新口径，并允许历史数字变化。

**Forward Fix（前向修复）**：旧日期保留 V1，新日期开始 V2。

适合：财务封账、历史审计数字不可重开、对外已发布数字需要保留。

Lifecycle 的 `effective_from / effective_to` 主要支撑 Forward Fix 的时间边界；实际查询是否需要版本路由，要由消费层契约显式实现，不能仅靠元数据自动猜测。

### Step 6｜跑 CI Regression

至少包括：

```text
Metric lifecycle validator
Static contracts
dbt tests
MetricFlow validate / explain
Negative Join-Safety
Golden Reconciliation
Consumer contracts
```

### Step 7｜Golden Oracle 只能在人工确认后更新

Breaking Change 导致 Golden Test 失败是预期的“变化探测”。

正确顺序是：

```text
旧 Golden 失败
→ 业务确认新口径
→ 人工检查新预期结果
→ 更新 Golden Oracle
→ 再跑完整 Regression
```

不能为了让 CI 变绿直接重写 Golden 结果。

## 9. CI 门禁

`.github/workflows/ci.yml` 已加入：

```bash
python metadata/datahub/tools/validate_metric_lifecycle.py
pytest -q tests/test_metric_version_lifecycle_contract.py
```

当前静态校验覆盖：

- 所有 governed Metric 都有 lifecycle history；
- `current_version` 必须存在且指向 `ACTIVE`；
- 一个 Metric 必须恰好一个 `ACTIVE`；
- V2+ 必须声明 `effective_from`；
- V2+ 必须声明 `supersedes_version`；
- `RETIRED` 必须有 `effective_to`；
- canonical Metric 定义指纹不能静默漂移；
- Agent Metric Context 可以读到当前业务版本和生命周期状态。

## 10. Agent / Consumer 当前行为

`GovernedContextRepository.metric_context()` 现在会返回：

```text
business_version
lifecycle_status
effective_from
effective_to
supersedes_version
definition_fingerprint
lifecycle_source_of_truth
```

普通受治理查询仍然使用 registry 的 `current_version`，不会让 LLM 自己决定“用 V1 还是 V2”。

如果未来需要“按历史日期自动路由到旧版本”，必须单独实现 Temporal Version Routing（按时间版本路由）并增加 Runtime / Golden Acceptance；当前源码没有把这一能力冒充成已完成。

## 11. Evidence Boundary（证据边界）

当前能力可以声明：

```text
Metric Version Lifecycle Contract     IMPLEMENTED
Definition Fingerprint Drift Guard    IMPLEMENTED
CI Static Gate                        IMPLEMENTED
Agent current-version context         IMPLEMENTED
```

不能仅凭这些静态契约声明：

```text
历史版本已在真实 MetricFlow Runtime 双轨运行
所有 Consumer 已完成 V1 → V2 迁移
真实 Backfill 已执行
真实 Forward Fix 已执行
Runtime business result 已验证
```

这些仍需要对应的 Runtime Evidence。
