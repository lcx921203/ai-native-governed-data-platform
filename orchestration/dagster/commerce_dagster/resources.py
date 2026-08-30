"""Dagster Asset 使用的 Runtime Resource（运行资源）适配层。

Dagster 继续只做 Control Plane；Spark / Iceberg 计算仍在既有 ``spark-thrift`` Runtime 中执行。
本层负责把外部命令执行结果翻译成结构化 Phase 3C Failure Evidence，并把可重试边界交给
Failure Classification，而不是在 Resource 内根据异常文本自行猜根因。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Sequence

import dagster as dg

from .failure_classification import (
    CommandFailureObservation,
    FailureClassSource,
    allow_step_retry,
    classify_command_failure,
    failure_class_tags,
)


class SparkComposeResource(dg.ConfigurableResource):
    """把 Docker Compose 中的 Spark Runtime 暴露成 Dagster ConfigurableResource。

    Dagster API：``ConfigurableResource`` 负责把项目目录、服务名、超时等配置注入 Asset。
    工程边界：它只执行既有 Spark 命令并记录结构化失败，不把 Dagster 变成 Spark 计算引擎。
    """

    project_dir: str
    service: str = "spark-thrift"
    command_timeout_seconds: int = 3600

    def _service_running(self) -> bool:
        """探测配置的 Docker Compose Spark 服务当前是否处于 running。
        
        Docker 不存在、命令超时、compose 返回非零或服务名缺失都返回 False；这是当前健康信号，不代表历史失败根因。
        """
        try:
            result = subprocess.run(
                ["docker", "compose", "ps", "--services", "--status", "running"],
                cwd=Path(self.project_dir),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=15,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False
        if result.returncode != 0:
            return False
        return self.service in {
            line.strip() for line in result.stdout.splitlines() if line.strip()
        }

    def _record_structured_failure(self, context, failure_class, *, stage: str | None = None) -> None:
        """把已经分类的执行失败写入当前 Dagster Run Tags。
        
        只有 AssetExecutionContext 才写；写 Tag 自身失败只记录 warning，不能覆盖原始执行异常。
        """
        if not isinstance(context, dg.AssetExecutionContext):
            return
        tags = failure_class_tags(
            failure_class,
            source=FailureClassSource.EXECUTION_ADAPTER,
            component=self.service,
            stage=stage,
        )
        try:
            context.instance.add_run_tags(context.run_id, tags)
        except Exception as exc:
            context.log.warning(
                "Could not persist failure tags for run %s: %s", context.run_id, exc
            )

    def _raise_classified_failure(self, context, *, observation, description: str, stage: str | None = None) -> None:
        """完成“分类 → 持久化 Tag → 抛 Dagster Failure”这一条失败出口。
        
        是否允许 Step Retry 只由 ``allow_step_retry`` 决定，UNKNOWN 等类别不会在这里被放宽。
        """
        failure_class = classify_command_failure(observation)
        self._record_structured_failure(context, failure_class, stage=stage)
        raise dg.Failure(
            description=description,
            metadata={
                "failure_class": failure_class.value,
                "classification_source": FailureClassSource.EXECUTION_ADAPTER.value,
                "component": self.service,
            },
            allow_retries=allow_step_retry(failure_class),
        )

    def _run(self, args: Sequence[str], context, *, stage: str | None = None) -> None:
        """在 Spark Compose 服务中执行一条命令，并把失败转换成结构化 Dagster 证据。
        
        负责 stdout 日志、超时、当前服务健康检查、退出码判断和分类抛错；只有 exit code=0 才正常返回。
        """
        command = ["docker", "compose", "exec", "-T", self.service, *args]
        context.log.info("Executing: %s", " ".join(command))
        try:
            result = subprocess.run(
                command,
                cwd=Path(self.project_dir),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=self.command_timeout_seconds,
            )
        except FileNotFoundError as exc:
            self._raise_classified_failure(
                context,
                observation=CommandFailureObservation(
                    command_available=False, service_running=False
                ),
                description=f"Docker command is unavailable: {exc}",
                stage=stage,
            )
        except subprocess.TimeoutExpired:
            self._raise_classified_failure(
                context,
                observation=CommandFailureObservation(
                    command_available=True,
                    timed_out=True,
                    service_running=self._service_running(),
                ),
                description=(
                    f"Docker/Spark command exceeded {self.command_timeout_seconds}s: "
                    f"{' '.join(command)}"
                ),
                stage=stage,
            )

        if result.stdout:
            for line in result.stdout.splitlines():
                context.log.info(line)
        if result.returncode != 0:
            self._raise_classified_failure(
                context,
                observation=CommandFailureObservation(
                    command_available=True,
                    service_running=self._service_running(),
                    return_code=result.returncode,
                ),
                description=(
                    f"Docker Compose command failed with exit code {result.returncode}: "
                    f"{' '.join(command)}"
                ),
                stage=stage,
            )

    def spark_submit(
        self,
        project_relative_script: str,
        context,
        *,
        script_args: Sequence[str] = (),
    ) -> None:
        """通过 ``spark-submit`` 执行一个项目内 Spark 脚本。
        
        输入项目相对路径和可选脚本参数；具体日志、超时、分类与重试边界全部复用 ``_run``。
        """
        self._run(
            [
                "/opt/spark/bin/spark-submit",
                f"/opt/project/{project_relative_script}",
                *script_args,
            ],
            context,
            stage=project_relative_script,
        )
