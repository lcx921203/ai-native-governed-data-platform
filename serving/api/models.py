"""Serving API 稳定 Response Schema；接口字段名与 Iceberg Serving Contract 对齐。"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel


class DailyExecutiveRow(BaseModel):
    """Executive Serving 的稳定 JSON Row；字段与 ``bi_daily_executive`` Contract 一一对应。

    Decimal 保留指标金额精度；模型只做响应校验，不承担二次计算。
    """

    business_date: date
    region: str
    gross_sales: Decimal | None = None
    sales_before_reversal: Decimal | None = None
    net_sales: Decimal | None = None
    order_count: int | None = None
    average_order_value: Decimal | None = None


class HealthResponse(BaseModel):
    """健康检查最小响应合同，供容器/调用方区分 live 与 ready 结果。"""

    status: str
