"""导出 FastAPI Contract 为确定性的 OpenAPI JSON，供 DataHub 消费端治理使用。

业务逻辑：把源码中的固定 Endpoint Schema 固化成元数据输入。
工程边界：生成 OpenAPI 文件不代表 API 已运行，也不代表 DataHub 已 ingestion；文件中不包含凭据或实时响应。
"""
from __future__ import annotations

import json
from pathlib import Path

from .main import app


def export_openapi(path: Path | None = None) -> Path:
    """把 ``app.openapi()`` 写成稳定 JSON 文件。

    输入：可选输出路径；默认覆盖源码目录中的 ``openapi.json``。
    输出：OpenAPI 文件路径。
    工程边界：这里只生成静态 API Contract，不执行 DataHub mutation 或 Runtime identity promotion。
    """
    target = path or Path(__file__).with_name("openapi.json")
    target.write_text(json.dumps(app.openapi(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    print(export_openapi())
