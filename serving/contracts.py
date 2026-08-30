"""Serving Contract（服务消费合同）的加载与 Fail-Closed 校验。

业务逻辑：固定 BI / API 需求只声明“要哪些受治理 Metric、按哪些 Dimension 分组、写到哪张 Serving Table”，
不允许在 Serving 层重新写指标公式或任意 SQL。
输入：``serving/contracts/*.yml``；输出：不可变 ``ServingContract``。
数据语义：Serving Contract 描述消费 Projection，不创造新的 Business Truth。
工程边界：Metric Authority 仍属于 MetricFlow；合同出现 raw SQL、非法标识符或不完整列映射时直接拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_QUALIFIED_TABLE = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*\.[A-Za-z_][A-Za-z0-9_]*$"
)
_ALLOWED_SPARK_TYPES = {
    "STRING",
    "DATE",
    "BIGINT",
    "DOUBLE",
    "TIMESTAMP",
}
_DECIMAL = re.compile(r"^DECIMAL\(\d{1,2},\d{1,2}\)$")


@dataclass(frozen=True)
class ServingColumn:
    """一列 MetricFlow 输出到 Serving Table 的确定性映射。

    ``source`` 是 MetricFlow 结果列名，``name`` 是 Serving 稳定接口列名；``spark_type`` 决定物化时的显式类型。
    """

    source: str
    name: str
    spark_type: str
    nullable: bool


@dataclass(frozen=True)
class ServingReadiness:
    """固定消费在导出前必须已经物化的 exact daily Asset。"""

    required_daily_assets: tuple[str, ...]


@dataclass(frozen=True)
class SemanticServingQuery:
    """固定消费的受治理 MetricFlow 查询规格；日期窗口由 Dagster 分区在运行时注入。"""

    metrics: tuple[str, ...]
    group_by: tuple[str, ...]
    row_limit: int


@dataclass(frozen=True)
class ServingTarget:
    """Serving Table 物理投影合同。

    ``partition_by`` 只用于物理布局；``primary_key`` 用于静态唯一性语义检查，不替代上游业务 Grain。
    """

    table: str
    partition_by: tuple[str, ...]
    primary_key: tuple[str, ...]
    columns: tuple[ServingColumn, ...]


@dataclass(frozen=True)
class ServingContract:
    """一个固定消费面从 MetricFlow 到 Iceberg Serving 的完整声明。"""

    version: int
    name: str
    description: str
    consumers: tuple[str, ...]
    readiness: ServingReadiness
    semantic_query: SemanticServingQuery
    target: ServingTarget

    @property
    def expected_metricflow_columns(self) -> tuple[str, ...]:
        """返回 CSV 中必须存在的列，供 Export Runner 在写湖前做结构校验。"""

        return tuple(column.source for column in self.target.columns)

    def metricflow_args(self, *, start_time: str, end_time: str, csv_path: Path) -> list[str]:
        """把固定合同转换成 MetricFlow CLI 参数。

        这里只拼受治理参数，不接受 caller-supplied SQL / where。日期边界来自 Dagster Logical Partition。
        """

        return [
            "query",
            "--metrics",
            ",".join(self.semantic_query.metrics),
            "--group-by",
            ",".join(self.semantic_query.group_by),
            "--start-time",
            start_time,
            "--end-time",
            end_time,
            "--limit",
            str(self.semantic_query.row_limit),
            "--csv",
            str(csv_path),
        ]


def _validate_identifier(value: str, label: str) -> str:
    """校验 Contract 中可进入 CLI / SQL Identifier 位置的简单标识符。

    输入：YAML 字符串与字段标签；输出：校验后的原字符串。
    工程边界：拒绝点号、空格、引号等可改变语义的字符，避免 Serving Contract 变成任意 SQL 注入面。
    """
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label} must be a simple identifier: {value!r}")
    return value


def _validate_spark_type(value: str) -> str:
    """把 YAML 类型限制在 Serving Writer 明确支持的 Spark SQL 类型白名单。

    返回统一大写类型；未知类型直接失败，避免把 Contract 字符串原样拼进 Iceberg DDL。
    """
    normalized = value.upper()
    if normalized in _ALLOWED_SPARK_TYPES or _DECIMAL.fullmatch(normalized):
        return normalized
    raise ValueError(f"unsupported serving spark_type: {value!r}")


def _load_column(raw: dict[str, Any]) -> ServingColumn:
    """把单列 YAML 映射转换成不可变 ``ServingColumn``，并在对象创建前完成名称/类型校验。"""
    return ServingColumn(
        source=_validate_identifier(str(raw["source"]), "column.source"),
        name=_validate_identifier(str(raw["name"]), "column.name"),
        spark_type=_validate_spark_type(str(raw["spark_type"])),
        nullable=bool(raw.get("nullable", True)),
    )


def load_serving_contract(path: Path | str) -> ServingContract:
    """读取并严格校验一个 Serving YAML。

    输入文件必须只包含声明式 Metric / Dimension / Target Metadata；任何 ``sql`` / ``formula`` 字段都会被拒绝，
    防止固定报表在 Serving 层悄悄形成第二套指标口径。
    """

    contract_path = Path(path)
    raw = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"serving contract must be a mapping: {contract_path}")

    forbidden = {"sql", "query_sql", "formula", "expression"}
    serialized_keys: set[str] = set()

    def walk(value: Any) -> None:
        """递归收集所有 YAML Key，用于禁止隐藏在任意层级的 SQL / formula 字段。"""
        if isinstance(value, dict):
            for key, child in value.items():
                serialized_keys.add(str(key).lower())
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(raw)
    hit = sorted(forbidden & serialized_keys)
    if hit:
        raise ValueError(f"Serving contract must not define metric SQL/formulas: {hit}")

    readiness_raw = raw.get("readiness", {})
    query_raw = raw["semantic_query"]
    target_raw = raw["target"]
    metrics = tuple(_validate_identifier(str(v), "metric") for v in query_raw["metrics"])
    group_by = tuple(_validate_identifier(str(v), "group_by") for v in query_raw["group_by"])
    if not metrics:
        raise ValueError("serving contract requires at least one metric")
    if not group_by:
        raise ValueError("serving contract requires at least one group_by")

    row_limit = int(query_raw.get("row_limit", 10000))
    if row_limit <= 0 or row_limit > 100000:
        raise ValueError("row_limit must be in 1..100000")

    columns = tuple(_load_column(v) for v in target_raw["columns"])
    if not columns:
        raise ValueError("serving target requires columns")
    source_names = [c.source for c in columns]
    target_names = [c.name for c in columns]
    if len(source_names) != len(set(source_names)):
        raise ValueError("duplicate MetricFlow source column in serving mapping")
    if len(target_names) != len(set(target_names)):
        raise ValueError("duplicate target column in serving mapping")

    expected_sources = set(metrics) | set(group_by)
    if set(source_names) != expected_sources:
        missing = sorted(expected_sources - set(source_names))
        extra = sorted(set(source_names) - expected_sources)
        raise ValueError(
            f"serving column mapping must cover exactly metrics + group_by; missing={missing}, extra={extra}"
        )

    table = str(target_raw["table"])
    if not _QUALIFIED_TABLE.fullmatch(table):
        raise ValueError(f"target.table must be catalog.schema.table: {table!r}")

    partition_by = tuple(
        _validate_identifier(str(v), "partition_by") for v in target_raw.get("partition_by", [])
    )
    primary_key = tuple(
        _validate_identifier(str(v), "primary_key") for v in target_raw.get("primary_key", [])
    )
    target_set = set(target_names)
    if not set(partition_by).issubset(target_set):
        raise ValueError("partition_by must reference target columns")
    if not primary_key or not set(primary_key).issubset(target_set):
        raise ValueError("primary_key must be non-empty and reference target columns")

    consumers = tuple(str(v) for v in raw.get("consumers", []))
    if not consumers or not set(consumers).issubset({"bi", "api"}):
        raise ValueError("consumers must contain only bi/api and cannot be empty")

    required_daily_assets = tuple(
        _validate_identifier(str(v), "readiness.required_daily_assets")
        for v in readiness_raw.get("required_daily_assets", [])
    )
    if not required_daily_assets:
        raise ValueError("readiness.required_daily_assets cannot be empty")
    if len(required_daily_assets) != len(set(required_daily_assets)):
        raise ValueError("duplicate readiness.required_daily_assets entry")

    return ServingContract(
        version=int(raw.get("version", 1)),
        name=_validate_identifier(str(raw["name"]), "contract.name"),
        description=str(raw.get("description", "")).strip(),
        consumers=consumers,
        readiness=ServingReadiness(required_daily_assets=required_daily_assets),
        semantic_query=SemanticServingQuery(
            metrics=metrics,
            group_by=group_by,
            row_limit=row_limit,
        ),
        target=ServingTarget(
            table=table,
            partition_by=partition_by,
            primary_key=primary_key,
            columns=columns,
        ),
    )
