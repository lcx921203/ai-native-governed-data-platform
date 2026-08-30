# Dagster Phase 3C — Acceptance（验收）

## 1. 为什么 3C 需要独立验收

3C 不是“代码存在就完成”。它控制生产运行，因此必须验证：

```text
什么时候运行
什么时候等待
什么时候自动补跑
什么时候停止自动化
什么时候转人工
```

验收对象是行为，而不是 Python 文件数量。

## 2. Independent Oracle（独立真值）

生产 Recovery Policy 不能自己证明自己正确。因此项目新增：

```text
acceptance/phase3c/scenarios.py
```

里面由人工维护输入状态与 Expected Action（预期动作）。生产代码只接受比较。

```text
Hand-authored Scenario
        ↓
Expected Action
        ↕ compare
Recovery Policy Actual Action
```

这和第六章 Golden Dataset / Oracle / Comparator 的思想完全一致，只是对象从“业务数字”变成“生产自动化行为”。

## 3. 核心场景

```text
正常预算内                         → WAIT
漏调度 / No Run                   → AUTO_REPLAY
已有 Active Run                   → WAIT
基础设施仍不可用                  → ALERT_AND_WAIT
历史基础设施失败 + 当前已恢复      → AUTO_REPLAY
瞬时运行失败 + 已恢复              → AUTO_REPLAY
确定性代码错误                     → ALERT_MANUAL
数据契约失败                       → ALERT_MANUAL
未知错误                           → ALERT_MANUAL
自动恢复预算耗尽                   → ALERT_MANUAL
精确分区已完整                     → NO_ACTION
Run SUCCESS 但精确分区不完整        → ALERT_MANUAL
```

## 4. Acceptance Ladder（验收阶梯）

```text
DESIGN CONTRACT
恢复规则是否清楚？
        ↓
PURE POLICY ACCEPTANCE
固定场景能否得到固定动作？
        ↓
DAGSTER RUNTIME ACCEPTANCE
真实 Sensor / Run / Tags 是否符合？
        ↓
DATA RUNTIME ACCEPTANCE
恢复后 exact partition 是否真正 9/9 完整？
```

前两层通过不等于后两层通过。

## 5. 当前状态

```text
Hand-authored acceptance matrix       ✅ implemented
Independent expected actions          ✅ implemented
Policy-vs-oracle comparator            ✅ implemented
Critical recovery scenario coverage   ✅ implemented
Runtime acceptance plan               ✅ implemented

Real Dagster schedule evidence         ⏸ DEFERRED
Real Sensor evaluation                 ⏸ DEFERRED
Real RunRequest / run_key evidence     ⏸ DEFERRED
Real failure-class run tags            ⏸ DEFERRED
Real 9/9 Mart recovery evidence        ⏸ DEFERRED
```

因此当前可以说 **3C Design / Static Acceptance 完成**，不能说 **3C Runtime Acceptance 完成**。
