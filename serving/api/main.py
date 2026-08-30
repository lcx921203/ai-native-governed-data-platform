"""Commerce Serving API — 给固定业务消费暴露只读 REST Interface。

业务逻辑：BI 可以直接通过 Trino 查询 Serving Table；产品/运营系统则通过本 FastAPI 获得稳定 JSON Contract。
输入：固定日期 / Region 参数；输出：已经由 MetricFlow + Dagster 预计算的 Serving 行。
工程边界：API 不接受任意 SQL、Metric 名或 Dimension 组合；动态分析仍交给 Agent + MetricFlow。
"""

from __future__ import annotations

from datetime import date

from fastapi import Depends, FastAPI, HTTPException

from .models import DailyExecutiveRow, HealthResponse
from .repository import TrinoServingRepository


app = FastAPI(
    title="Commerce Serving API",
    version="1.0.0",
    description="Read-only BI/API serving surface over Trino + Iceberg.",
)


def get_repository() -> TrinoServingRepository:
    """FastAPI Dependency：每个请求得到一个轻量 Repository；连接在查询方法内打开并关闭。"""

    return TrinoServingRepository()


@app.get("/health/live", response_model=HealthResponse)
def live() -> HealthResponse:
    """Liveness：只证明 FastAPI 进程可响应，不证明 Trino / Iceberg 已可查询。"""
    return HealthResponse(status="ok")


@app.get("/health/ready", response_model=HealthResponse)
def ready(repository: TrinoServingRepository = Depends(get_repository)) -> HealthResponse:
    """Readiness：通过 Repository 执行最小 Trino 查询；失败统一返回 HTTP 503。

    工程边界：Ready 只证明查询入口可用，不证明某个业务日 Serving 分区已经 Fresh。
    """
    try:
        if repository.ping():
            return HealthResponse(status="ready")
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Trino not ready: {exc}") from exc
    raise HTTPException(status_code=503, detail="Trino not ready")


@app.get(
    "/api/v1/executive/daily",
    response_model=list[DailyExecutiveRow],
)
def executive_daily(
    business_date: date,
    repository: TrinoServingRepository = Depends(get_repository),
) -> list[DailyExecutiveRow]:
    """返回一个业务日按 Region 预计算的 Executive Metrics。"""

    return [DailyExecutiveRow.model_validate(row) for row in repository.executive_daily(business_date)]


@app.get(
    "/api/v1/regions/{region}/daily",
    response_model=list[DailyExecutiveRow],
)
def region_daily(
    region: str,
    start_date: date,
    end_date: date,
    repository: TrinoServingRepository = Depends(get_repository),
) -> list[DailyExecutiveRow]:
    """返回一个 Region 的日粒度 Serving Rows；不在 API 层重新聚合 AOV / Net Sales 公式。"""

    if end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must be >= start_date")
    rows = repository.region_daily(region=region, start_date=start_date, end_date=end_date)
    return [DailyExecutiveRow.model_validate(row) for row in rows]
