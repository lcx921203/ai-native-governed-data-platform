"""Recovery Gate 使用的轻量 Host-side Runtime Health Probe（运行健康探针）。

它只回答“当前恢复所依赖的 Compose 服务是否都在运行”，属于 Current Recoverability 证据；
不能反向改写历史 Run 当时为什么失败。
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable


def docker_compose_services_running(
    project_dir: str | Path,
    required_services: Iterable[str],
    *,
    timeout_seconds: int = 15,
) -> bool:
    """检查 Recovery 所要求的所有 Docker Compose Runtime Service 当前是否都在运行。
    
    要求集合为空时直接健康；Docker 不存在、超时、compose 错误或少任一服务都返回 False。它描述“现在能不能恢复”，不改写历史失败原因。
    """
    required = frozenset(required_services)
    if not required:
        return True
    try:
        result = subprocess.run(
            ["docker", "compose", "ps", "--services", "--status", "running"],
            cwd=Path(project_dir),
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=timeout_seconds,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    running = frozenset(
        line.strip() for line in result.stdout.splitlines() if line.strip()
    )
    return required.issubset(running)
