"""固定 Serving API 查询模板。

API 只允许读取已物化的 Serving Table，不接受 caller-supplied SQL / table / metric name。
日期先解析成 ``date``，字符串值做 SQL Literal escaping；因此业务 API 是稳定接口，不是任意查询代理。
"""

from __future__ import annotations

from datetime import date


def sql_string_literal(value: str) -> str:
    """把业务维度值安全编码为 SQL 字符串字面量；单引号按 ANSI SQL 规则双写。"""

    return "'" + value.replace("'", "''") + "'"


def qualified_daily_table(catalog: str, schema: str) -> str:
    """拼出固定 Executive Serving Table 的三段式 Trino 名称。

    ``catalog`` / ``schema`` 已由 Settings 的 Identifier 白名单校验，本函数不接受 caller-supplied table。
    """
    return f"{catalog}.{schema}.bi_daily_executive"


def executive_daily_sql(*, table: str, business_date: date) -> str:
    """返回某业务日全 Region 的固定 Dashboard 行。"""

    return f"""
SELECT
  business_date,
  region,
  gross_sales,
  sales_before_reversal,
  net_sales,
  order_count,
  average_order_value
FROM {table}
WHERE business_date = DATE '{business_date.isoformat()}'
ORDER BY region
""".strip()


def region_daily_sql(*, table: str, region: str, start_date: date, end_date: date) -> str:
    """返回一个 Region 在闭区间日期内的预计算日粒度指标，不在 API 层二次定义指标公式。"""

    if end_date < start_date:
        raise ValueError("end_date must be >= start_date")
    region_literal = sql_string_literal(region)
    return f"""
SELECT
  business_date,
  region,
  gross_sales,
  sales_before_reversal,
  net_sales,
  order_count,
  average_order_value
FROM {table}
WHERE region = {region_literal}
  AND business_date BETWEEN DATE '{start_date.isoformat()}' AND DATE '{end_date.isoformat()}'
ORDER BY business_date
""".strip()
