"""FastAPI 到 Trino Serving Catalog 的只读 Repository。

业务逻辑：把固定 REST endpoint 映射到固定 Trino SELECT；返回的是 Serving Projection，不是 Agent 动态语义查询。
Trino API：使用官方 Python DBAPI Client；连接固定 catalog/schema，session timezone=UTC。
工程边界：本模块没有 INSERT/UPDATE/DELETE、没有任意 SQL endpoint，也不直连 MetricFlow。
"""

from __future__ import annotations

from datetime import date
from typing import Any

from .queries import executive_daily_sql, qualified_daily_table, region_daily_sql
from .settings import ServingApiSettings


class TrinoServingRepository:
    """面向 BI/API Serving Table 的只读查询适配器。"""

    def __init__(self, settings: ServingApiSettings | None = None):
        self.settings = settings or ServingApiSettings.from_env()

    @property
    def daily_table(self) -> str:
        """返回当前环境唯一允许访问的 Executive Serving Table。"""
        return qualified_daily_table(self.settings.catalog, self.settings.schema)

    def _connect(self):
        """延迟导入 Trino Client，避免纯合同/静态测试被可选 Runtime Dependency 阻断。"""

        from trino.dbapi import connect

        return connect(
            host=self.settings.host,
            port=self.settings.port,
            user=self.settings.user,
            catalog=self.settings.catalog,
            schema=self.settings.schema,
            http_scheme=self.settings.http_scheme,
            timezone="UTC",
            source="commerce-serving-api",
        )

    def _query(self, sql: str) -> list[dict[str, Any]]:
        """执行内部固定模板 SQL，并把 DB-API 行映射为字段字典。

        连接按请求打开/关闭；该私有方法不会从 API 用户接收任意 SQL 文本。
        """
        connection = self._connect()
        try:
            cursor = connection.cursor()
            cursor.execute(sql)
            rows = cursor.fetchall()
            columns = [item[0] for item in cursor.description or []]
            return [dict(zip(columns, row, strict=True)) for row in rows]
        finally:
            connection.close()

    def ping(self) -> bool:
        """执行 ``SELECT 1`` 作为 Trino Readiness Probe，不读取业务数据。"""
        rows = self._query("SELECT 1 AS ok")
        return bool(rows and rows[0].get("ok") == 1)

    def executive_daily(self, business_date: date) -> list[dict[str, Any]]:
        """读取一个业务日的全部 Region 预计算指标行；不在 Repository 中重新聚合。"""
        return self._query(
            executive_daily_sql(table=self.daily_table, business_date=business_date)
        )

    def region_daily(
        self,
        *,
        region: str,
        start_date: date,
        end_date: date,
    ) -> list[dict[str, Any]]:
        """读取指定 Region 的日期区间 Serving 行，时间区间与字符串转义由 Query Builder 固化。"""
        return self._query(
            region_daily_sql(
                table=self.daily_table,
                region=region,
                start_date=start_date,
                end_date=end_date,
            )
        )
