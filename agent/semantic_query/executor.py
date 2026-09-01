"""受治理 SemanticQueryPlan 的 Fail-Closed MetricFlow 执行器。

执行顺序是 Tenant Scope -> Explain -> Query；
只有计划 READY、Request Scope 合法且 Runtime gate 打开时才允许触达 MetricFlow。

工程边界：
- 本模块不生成公式，不回退任意 SQL；
- 真实数值证据只有实际 Runtime 成功后才能标记 RUNTIME_VERIFIED；
- tenant / row scope 不从 Prompt 获取，只读取可信 RequestContext ContextVar。
"""

from __future__ import annotations

import csv
import os
import subprocess
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

import yaml

from agent.semantic_query.contracts import SemanticQueryPlan, SemanticQueryResult, SemanticQueryStatus
from agent.semantic_query.planner import GovernedSemanticQueryPlanner
from agent.tenancy import GovernedRequestScopeEnforcer, current_request_context


Runner = Callable[..., subprocess.CompletedProcess[str]]


class MetricFlowSemanticQueryExecutor:
    """执行已经通过 Planner 审核的 MetricFlow 查询计划。

    输入 SemanticQueryPlan，输出 SemanticQueryResult；
    负责 tenant scope、Runtime gate、Explain、Query 与有限错误投影。
    """

    def __init__(self, project_root: Path | str, runner: Runner | None = None):
        self.root = Path(project_root).resolve()
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/semantic_query_policy.yml").read_text(encoding="utf-8")
        )
        self.planner = GovernedSemanticQueryPlanner(self.root)
        self.scope_enforcer = GovernedRequestScopeEnforcer(self.root)
        self.runner = runner or subprocess.run

    def execute(self, plan: SemanticQueryPlan) -> SemanticQueryResult:
        """先注入可信 Request Scope，再执行 Explain-before-query。"""

        if plan.status is not SemanticQueryStatus.READY or plan.spec is None:
            return SemanticQueryResult(
                status=plan.status,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=list(plan.warnings),
            )

        # RequestContext 使用 ContextVar 传播，所以 Time Comparison / Breakdown 内部
        # 新建的 MetricFlow Executor 也会自动继承同一 tenant scope。
        scoped_plan, scope_warning = self.scope_enforcer.apply(
            plan,
            current_request_context(),
        )
        if scope_warning:
            return SemanticQueryResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=[scope_warning],
                validation="TENANT_SCOPE_REJECTED",
            )
        plan = scoped_plan
        if plan.spec is not None:
            plan = replace(
                plan,
                command_preview=self.planner.command_args(plan.spec),
            )

        gate = self.policy["runtime"]["allow_env"]
        if os.getenv(gate, "false").lower() != "true":
            return SemanticQueryResult(
                status=SemanticQueryStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=[
                    f"MetricFlow execution is disabled; set {gate}=true only in the intended runtime environment."
                ],
                validation="NOT_EXECUTED",
            )

        mf = self._metricflow_binary()
        if not mf.exists():
            return self._deferred(plan, f"MetricFlow CLI not found at {mf}.")
        project_dir = self.root / self.policy["runtime"]["project_dir"]
        generated_spec = self.root / self.policy["runtime"]["generated_semantic_spec"]
        if not generated_spec.exists():
            return self._deferred(plan, "Generated MetricFlow compatibility semantic spec is missing.")

        env = os.environ.copy()
        env["DBT_PROFILES_DIR"] = str(project_dir)
        timeout = int(self.policy["limits"]["command_timeout_seconds"])

        explain_cmd = [str(mf), *self.planner.explain_args(plan.spec)[1:]]
        explained = self._run(explain_cmd, project_dir, env, timeout)
        if explained.returncode != 0:
            return SemanticQueryResult(
                status=SemanticQueryStatus.BLOCKED,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                warnings=[
                    "MetricFlow explain rejected the semantic query; the data query was not executed.",
                    self._bounded_error(explained),
                ],
                validation="METRICFLOW_EXPLAIN_REJECTED",
            )

        with tempfile.TemporaryDirectory(prefix="commerce-mf-query-") as temp_dir:
            csv_path = Path(temp_dir) / "result.csv"
            query_cmd = [
                str(mf),
                *self.planner.command_args(plan.spec)[1:],
                "--csv",
                str(csv_path),
            ]
            queried = self._run(query_cmd, project_dir, env, timeout)
            if queried.returncode != 0:
                return SemanticQueryResult(
                    status=SemanticQueryStatus.ERROR,
                    evidence="RUNTIME_VERIFIED",
                    plan=plan,
                    warnings=["MetricFlow query failed after explain succeeded.", self._bounded_error(queried)],
                    validation="QUERY_FAILED",
                )
            if not csv_path.exists():
                return SemanticQueryResult(
                    status=SemanticQueryStatus.ERROR,
                    evidence="RUNTIME_VERIFIED",
                    plan=plan,
                    warnings=["MetricFlow reported success but did not create the requested CSV result."],
                    validation="MISSING_RESULT_ARTIFACT",
                )

            with csv_path.open(newline="", encoding="utf-8") as handle:
                reader = csv.DictReader(handle)
                rows = list(reader)
                columns = list(reader.fieldnames or [])

        if len(rows) > plan.spec.limit:
            return SemanticQueryResult(
                status=SemanticQueryStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                warnings=["MetricFlow returned more rows than the governed query limit."],
                validation="ROW_LIMIT_VIOLATION",
            )

        missing_metrics = [metric for metric in plan.spec.metric_names if metric not in columns]
        if missing_metrics:
            return SemanticQueryResult(
                status=SemanticQueryStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                rows=rows,
                columns=columns,
                warnings=[
                    "MetricFlow reported success but the result artifact is missing requested metric column(s): "
                    + ", ".join(missing_metrics)
                ],
                validation="MISSING_METRIC_COLUMNS",
            )

        return SemanticQueryResult(
            status=SemanticQueryStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=plan,
            rows=rows,
            columns=columns,
            validation="METRICFLOW_EXPLAIN_AND_QUERY_PASS",
        )

    def _metricflow_binary(self) -> Path:
        """解析当前环境可用的 MetricFlow CLI 入口；找不到时返回 DEFERRED。"""

        configured = os.getenv(self.policy["runtime"]["metricflow_bin_env"], "").strip()
        return Path(configured).expanduser().resolve() if configured else (
            self.root / self.policy["runtime"]["default_metricflow_bin"]
        ).resolve()

    def _run(self, cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
        """执行一条受限 MetricFlow CLI，并捕获有限运行结果。"""

        try:
            return self.runner(
                cmd,
                cwd=str(cwd),
                env=env,
                text=True,
                capture_output=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return subprocess.CompletedProcess(
                cmd,
                124,
                stdout=exc.stdout or "",
                stderr="MetricFlow command timed out",
            )

    @staticmethod
    def _bounded_error(result: subprocess.CompletedProcess[str]) -> str:
        """裁剪底层 CLI 错误，避免无限日志或敏感信息进入 Agent。"""

        text = (result.stderr or result.stdout or "MetricFlow command failed").strip().replace("\x00", "")
        return text[-1200:]

    @staticmethod
    def _deferred(plan: SemanticQueryPlan, reason: str) -> SemanticQueryResult:
        return SemanticQueryResult(
            status=SemanticQueryStatus.DEFERRED,
            evidence="STATIC_CONTRACT",
            plan=plan,
            warnings=[reason],
            validation="NOT_EXECUTED",
        )
