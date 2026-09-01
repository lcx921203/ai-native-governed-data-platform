# Context Planner 增量覆盖包

基线：GitHub `lcx921203/ai-native-governed-data-platform` 的 `main` 分支。

本包新增/修改：

- `agent/context/contracts.py`
- `agent/context/planner.py`
- `agent/context/__init__.py`（保留现有 `GovernedContextRepository`，新增 Context Planner 相关导出）
- `agent/contracts/context_planner_policy.yml`
- `tests/test_context_planner.py`

用途：在现有 Router 之后增加 Route-driven Context Planner（路由驱动的上下文规划器），先生成“需要哪些上下文”的计划，不在该层执行查询或重新判断 Intent。

在 Working Copy 中把本 ZIP 解压并覆盖到仓库根目录后提交即可。
