"""Runtime/static executor for governed MetricFlow dimension-value discovery."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Callable

import yaml

from agent.dimension_values.contracts import DimensionValuePlan, DimensionValueResult, DimensionValueStatus
from agent.dimension_values.planner import GovernedDimensionValuePlanner


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
_BORDER_CHARS = set("-_=+|│┃┏┓┗┛┣┫┳┻╋━─═╔╗╚╝╠╣╦╩╬┡┩└┘┌┐")


class MetricFlowDimensionValueExecutor:
    def __init__(
        self,
        project_root: Path | str,
        *,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ):
        self.root = Path(project_root).resolve()
        self.runner = runner
        self.planner = GovernedDimensionValuePlanner(self.root)
        self.policy = yaml.safe_load(
            (self.root / "agent/contracts/dimension_value_policy.yml").read_text(encoding="utf-8")
        )

    def execute(self, plan: DimensionValuePlan) -> DimensionValueResult:
        if plan.status is not DimensionValueStatus.READY or plan.spec is None:
            return DimensionValueResult(
                status=plan.status,
                evidence="STATIC_CONTRACT",
                plan=plan,
                warnings=list(plan.warnings),
                validation="PLAN_NOT_READY",
            )

        gate = self.policy["runtime"]["allow_env"]
        if os.getenv(gate, "false").lower() != "true":
            values = self.planner.static_seed_values(plan.spec.dimension)
            return DimensionValueResult(
                status=DimensionValueStatus.DEFERRED,
                evidence="STATIC_CONTRACT",
                plan=plan,
                values=values[: plan.spec.limit],
                source_mode="STATIC_SEED_FALLBACK",
                warnings=[
                    f"MetricFlow dimension-value runtime is disabled; set {gate}=true only in the intended workstation runtime.",
                    "Returned values are repo-managed seed reference values, not a runtime-observed MetricFlow value universe.",
                ],
                truncated=len(values) > plan.spec.limit,
                validation="STATIC_FALLBACK_ONLY",
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
        cmd = [str(mf), *self.planner.command_args(plan.spec)[1:]]
        result = self._run(cmd, project_dir, env, timeout)
        if result.returncode != 0:
            return DimensionValueResult(
                status=DimensionValueStatus.BLOCKED,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                warnings=[
                    "MetricFlow rejected the requested metric/dimension value discovery.",
                    self._bounded_error(result),
                ],
                validation="METRICFLOW_DIMENSION_VALUE_REJECTED",
            )

        values = self._parse_dimension_values(result.stdout, plan.spec.dimension)
        if not values:
            return DimensionValueResult(
                status=DimensionValueStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                warnings=["MetricFlow returned success but no parseable dimension values were found."],
                validation="EMPTY_OR_UNPARSEABLE_DIMENSION_VALUES",
            )

        max_length = int(self.policy["limits"]["max_value_length"])
        if any(len(value) > max_length for value in values):
            return DimensionValueResult(
                status=DimensionValueStatus.ERROR,
                evidence="RUNTIME_VERIFIED",
                plan=plan,
                warnings=["MetricFlow returned a dimension value longer than the governed maximum."],
                validation="VALUE_LENGTH_VIOLATION",
            )

        return DimensionValueResult(
            status=DimensionValueStatus.COMPLETE,
            evidence="RUNTIME_VERIFIED",
            plan=plan,
            values=values[: plan.spec.limit],
            source_mode="METRICFLOW_RUNTIME",
            truncated=len(values) > plan.spec.limit,
            validation="METRICFLOW_DIMENSION_VALUES_PASS",
        )

    @classmethod
    def _parse_dimension_values(cls, stdout: str, dimension: str) -> list[str]:
        text = _ANSI_RE.sub("", stdout or "")
        values: list[str] = []
        dimension_labels = {
            dimension.lower(),
            dimension.split("__")[-1].lower(),
            dimension.replace("__", " ").lower(),
            "dimension value",
            "dimension values",
            "value",
        }

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            if line.startswith(("✔", "Success", "success", "Dimension Values", "The list of")):
                continue
            if all(ch.isspace() or ch in _BORDER_CHARS for ch in line):
                continue

            cells: list[str] = []
            if "│" in line or "┃" in line:
                cells = [cell.strip() for cell in re.split(r"[│┃]", line) if cell.strip()]
            elif "|" in line:
                cells = [cell.strip() for cell in line.split("|") if cell.strip()]
            elif line.startswith(("- ", "* ")):
                cells = [line[2:].strip()]

            for cell in cells:
                normalized = cell.lower().strip()
                if normalized in dimension_labels:
                    continue
                if not cell or all(ch in _BORDER_CHARS for ch in cell):
                    continue
                # Rich/markdown separator cells are not values.
                if set(cell) <= set("-: "):
                    continue
                if cell not in values:
                    values.append(cell)
        return values

    def _metricflow_binary(self) -> Path:
        configured = os.getenv(self.policy["runtime"]["metricflow_bin_env"], "").strip()
        if configured:
            return Path(configured).expanduser().resolve()
        return (self.root / self.policy["runtime"]["default_metricflow_bin"]).resolve()

    def _run(self, cmd: list[str], cwd: Path, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess[str]:
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
            return subprocess.CompletedProcess(cmd, 124, stdout=exc.stdout or "", stderr="MetricFlow command timed out")

    @staticmethod
    def _bounded_error(result: subprocess.CompletedProcess[str]) -> str:
        text = (result.stderr or result.stdout or "MetricFlow command failed").strip().replace("\x00", "")
        return text[-1200:]

    def _deferred(self, plan: DimensionValuePlan, reason: str) -> DimensionValueResult:
        assert plan.spec is not None
        values = self.planner.static_seed_values(plan.spec.dimension)
        return DimensionValueResult(
            status=DimensionValueStatus.DEFERRED,
            evidence="STATIC_CONTRACT",
            plan=plan,
            values=values[: plan.spec.limit],
            source_mode="STATIC_SEED_FALLBACK",
            warnings=[reason, "Static seed values are reference-only and are not runtime-observed MetricFlow dimension values."],
            truncated=len(values) > plan.spec.limit,
            validation="STATIC_FALLBACK_ONLY",
        )
