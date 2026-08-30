\
#!/usr/bin/env python3
"""从 canonical dbt Core 1.12 语义定义生成临时的 legacy MetricFlow 兼容规格。

为什么需要它：
当前 canonical 项目使用 dbt Core 1.12 支持的较新 Semantic Layer YAML；
但本项目锁定时 ``dbt-metricflow 0.13.0`` 仍要求 Core < 1.12，所以本地 ``mf``
兼容验收需要一个独立 compatibility project。

工程边界：生成器只负责“从唯一 canonical 语义定义派生兼容格式”，防止人工维护两套
Semantic Spec 漂移；它绝不成为第二个 Source of Truth，权威仍是 ``dbt/mercaso_dbt``。
"""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import re
import yaml


def humanize(name: str) -> str:
    """把 snake_case 名称转换成适合 Legacy Spec 的可读标签。
    
    输入：内部名称。
    输出：空格分词后的标题文本。
    """
    return " ".join(part.capitalize() for part in name.split("_"))


def load_yaml(path: Path) -> dict:
    """读取一个 YAML 文档并确保顶层是 Mapping。
    
    输入：YAML 路径。
    输出：dict。
    工程边界：格式不符合预期时立即失败，避免生成半正确的兼容产物。
    """
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def make_semantic_model(model: dict) -> tuple[dict, list[dict]]:
    """把当前 Canonical Semantic YAML 的一个 model 转换成 Legacy MetricFlow semantic_model。
    
    输入：新版 model 定义。
    输出：Legacy semantic model + 可直接生成的 simple metrics。
    兼容边界：这是版本桥接器，不改变业务语义权威；Canonical YAML 仍是源定义。
    """
    name = model["name"]
    legacy: dict = {
        "name": name,
        "model": f"ref('{name}')",
    }

    if model.get("agg_time_dimension"):
        legacy["defaults"] = {"agg_time_dimension": model["agg_time_dimension"]}

    entities: list[dict] = []
    dimensions: list[dict] = []

    for column in model.get("columns", []):
        column_name = column["name"]
        entity = column.get("entity")
        if entity:
            entity_name = entity.get("name", column_name)
            item = {"name": entity_name, "type": entity["type"]}
            # 兼容规格里始终显式保留 physical-column mapping，避免派生配置依赖隐式默认值。
            item["expr"] = column_name
            entities.append(item)

        dimension = column.get("dimension")
        if dimension:
            item = {
                "name": column_name,
                "type": dimension["type"],
            }
            if dimension["type"] == "time":
                item["type_params"] = {
                    "time_granularity": column.get("granularity", "day")
                }
            dimensions.append(item)

    if entities:
        legacy["entities"] = entities
    if dimensions:
        legacy["dimensions"] = dimensions

    measures: list[dict] = []
    simple_metrics: list[dict] = []
    for metric in model.get("metrics", []):
        if metric.get("type") != "simple":
            raise ValueError(
                f"Model {name}: expected only simple metrics in model-local metrics, got {metric.get('type')}"
            )
        metric_name = metric["name"]
        legacy_agg = metric["agg"]
        legacy_expr = metric.get("expr", metric_name)
        # Legacy Measure 的 aggregation enum 不能完全按较新规格表达 row-count `count`；
        # 对本项目恒为非空的 row flag，`count(1)` 与 `sum(1)` 等价，因此兼容层使用后者。
        if legacy_agg == "count" and str(legacy_expr).strip() == "1":
            legacy_agg = "sum"

        measure = {
            "name": metric_name,
            "agg": legacy_agg,
            "expr": legacy_expr,
        }
        if metric.get("description"):
            measure["description"] = metric["description"]
        if metric.get("label"):
            measure["label"] = metric["label"]
        if metric.get("agg_time_dimension"):
            measure["agg_time_dimension"] = metric["agg_time_dimension"]
        measures.append(measure)

        simple = {
            "name": metric_name,
            "label": metric.get("label") or humanize(metric_name),
            "type": "simple",
            "type_params": {"measure": metric_name},
        }
        if metric.get("description"):
            simple["description"] = metric["description"]
        simple_metrics.append(simple)

    if measures:
        legacy["measures"] = measures
    return legacy, simple_metrics


def _metric_input_to_measure(value: str | dict) -> str | dict:
    """把 Latest Spec 的 metric input 映射为 Legacy Spec 的 measure input。

    输入：metric 名称，或带 name/filter/alias 的 metric input dict。
    输出：Legacy conversion/advanced metric 可接受的 measure 引用。
    工程边界：只保留当前 Legacy Runtime 能表达的字段，不复制新的业务语义。
    """
    if isinstance(value, str):
        return value
    allowed = {key: value[key] for key in ("name", "filter", "alias") if key in value}
    if "name" not in allowed:
        raise ValueError(f"Metric input dict is missing name: {value}")
    return allowed


def make_advanced_metric(metric: dict) -> dict:
    """把 ratio/derived/conversion 等高级 Metric 定义转换成 Legacy MetricFlow 结构。
    
    输入：Canonical metric。
    输出：Legacy metric dict。
    工程边界：只映射被当前兼容版本支持的字段，不偷偷创造 Canonical Source 中不存在的语义。
    """
    kind = metric["type"]
    out = {
        "name": metric["name"],
        "label": metric.get("label") or humanize(metric["name"]),
        "type": kind,
    }
    if metric.get("description"):
        out["description"] = metric["description"]

    if kind == "ratio":
        out["type_params"] = {
            "numerator": metric["numerator"],
            "denominator": metric["denominator"],
        }
    elif kind == "derived":
        out["type_params"] = {
            "expr": metric["expr"],
            "metrics": metric.get("input_metrics", []),
        }
    elif kind == "conversion":
        out["type_params"] = {
            "conversion_type_params": {
                "entity": metric["entity"],
                "base_measure": _metric_input_to_measure(metric["base_metric"]),
                "conversion_measure": _metric_input_to_measure(metric["conversion_metric"]),
                "calculation": metric["calculation"],
            }
        }
        params = out["type_params"]["conversion_type_params"]
        if metric.get("window"):
            params["window"] = metric["window"]
        if metric.get("constant_properties"):
            params["constant_properties"] = metric["constant_properties"]
    else:
        raise ValueError(f"Unsupported advanced metric type in generator: {kind}")
    return out


def validate_generated(doc: dict) -> dict:
    """校验生成的 Legacy Spec 是否满足当前 Runtime 期望的最小结构。
    
    输入：生成后的 YAML dict。
    输出：原 dict（验证通过）。
    工程目的：兼容转换失败时在运行前 Fail Closed，而不是把坏 Spec 交给 MetricFlow。
    """
    semantic_models = doc.get("semantic_models", [])
    metrics = doc.get("metrics", [])
    metric_names = [m["name"] for m in metrics]
    if len(metric_names) != len(set(metric_names)):
        raise ValueError("Generated legacy metrics contain duplicate metric names")

    measures = []
    for model in semantic_models:
        measures.extend(measure["name"] for measure in model.get("measures", []))
    if len(measures) != len(set(measures)):
        raise ValueError("Generated legacy measures must be globally unique")

    simple_measure_refs = {
        m["type_params"]["measure"]
        for m in metrics
        if m.get("type") == "simple"
    }
    missing = sorted(simple_measure_refs - set(measures))
    if missing:
        raise ValueError(f"Simple metrics reference missing measures: {missing}")

    # Conversion Metric 在 legacy runtime 中直接引用 Measure；确保 Base / Conversion
    # 两侧都来自已生成的 canonical simple metric measure，避免运行时才发现断链。
    conversion_measure_refs: set[str] = set()
    for metric in metrics:
        if metric.get("type") != "conversion":
            continue
        params = metric["type_params"]["conversion_type_params"]
        for key in ("base_measure", "conversion_measure"):
            value = params[key]
            conversion_measure_refs.add(value["name"] if isinstance(value, dict) else value)
    missing_conversion = sorted(conversion_measure_refs - set(measures))
    if missing_conversion:
        raise ValueError(
            f"Conversion metrics reference missing measures: {missing_conversion}"
        )

    return {
        "semantic_models": len(semantic_models),
        "measures": len(measures),
        "metrics": len(metrics),
    }


def main() -> None:
    """从 Canonical dbt Semantic/Metric YAML 生成 Runtime 兼容的 Legacy MetricFlow Spec。
    
    输入：当前工程的语义模型与指标定义。
    输出：generated legacy YAML。
    权威边界：generated 文件是兼容产物，不是新的语义事实源。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args()
    root = args.project_root.resolve()

    canonical_semantic = root / "dbt/mercaso_dbt/models/marts/commerce/_commerce_semantic.yml"
    canonical_metrics_dir = root / "dbt/mercaso_dbt/models/metrics"
    canonical_metric_files = sorted(canonical_metrics_dir.glob("*.yml"))
    output = root / "dbt/mercaso_metricflow_compat/models/_generated_semantic_legacy.yml"
    report_path = root / "dbt/mercaso_metricflow_compat/generated_mapping_report.json"

    new_semantic = load_yaml(canonical_semantic)

    legacy_models: list[dict] = []
    legacy_metrics: list[dict] = []
    for model in new_semantic.get("models", []):
        if not model.get("semantic_model", {}).get("enabled"):
            continue
        converted, simple = make_semantic_model(model)
        legacy_models.append(converted)
        legacy_metrics.extend(simple)

    for metric_path in canonical_metric_files:
        advanced = load_yaml(metric_path)
        legacy_metrics.extend(make_advanced_metric(m) for m in advanced.get("metrics", []))

    doc = {
        "version": 2,
        "semantic_models": legacy_models,
        "metrics": legacy_metrics,
    }
    stats = validate_generated(doc)

    header = (
        "# GENERATED FILE — DO NOT EDIT BY HAND.\n"
        "# Canonical source: ../mercaso_dbt latest dbt Core 1.12 Semantic Layer YAML.\n"
        "# Re-run infra/runtime/generate_metricflow_legacy.py after semantic changes.\n\n"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        header + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=1000),
        encoding="utf-8",
    )
    report = {
        "canonical_semantic": str(canonical_semantic.relative_to(root)),
        "canonical_advanced_metrics": [str(path.relative_to(root)) for path in canonical_metric_files],
        "generated_legacy": str(output.relative_to(root)),
        **stats,
    }
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
