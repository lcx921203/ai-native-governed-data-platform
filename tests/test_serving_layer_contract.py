from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from serving.api.queries import executive_daily_sql, region_daily_sql, sql_string_literal
from serving.contracts import load_serving_contract


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "serving" / "contracts" / "bi_daily_executive.yml"


def _semantic_metric_names() -> set[str]:
    semantic = yaml.safe_load(
        (ROOT / "dbt" / "mercaso_dbt" / "models" / "marts" / "commerce" / "_commerce_semantic.yml").read_text(
            encoding="utf-8"
        )
    )
    names: set[str] = set()
    for model in semantic.get("models", []):
        for metric in model.get("metrics", []):
            names.add(metric["name"])
    sales = yaml.safe_load(
        (ROOT / "dbt" / "mercaso_dbt" / "models" / "metrics" / "sales.yml").read_text(encoding="utf-8")
    )
    for metric in sales.get("metrics", []):
        names.add(metric["name"])
    return names


def test_serving_contract_uses_only_governed_metrics_and_has_no_sql_formula():
    contract = load_serving_contract(CONTRACT)
    assert set(contract.semantic_query.metrics).issubset(_semantic_metric_names())
    assert contract.target.table == "polaris.serving.bi_daily_executive"
    assert contract.target.partition_by == ("business_date",)
    assert contract.target.primary_key == ("business_date", "region")
    assert set(contract.consumers) == {"bi", "api"}
    assert contract.readiness.required_daily_assets == ("orders", "order_items", "refund_items")

    text = CONTRACT.read_text(encoding="utf-8").lower()
    assert "query_sql:" not in text
    assert "formula:" not in text


def test_metricflow_export_args_are_fixed_contract_not_arbitrary_sql(tmp_path):
    contract = load_serving_contract(CONTRACT)
    args = contract.metricflow_args(
        start_time="2026-08-20",
        end_time="2026-08-21",
        csv_path=tmp_path / "result.csv",
    )
    joined = " ".join(args)
    assert args[0] == "query"
    assert "activity_net_sales" in joined
    assert "store__region" in joined
    assert "--start-time 2026-08-20" in joined
    assert "--end-time 2026-08-21" in joined
    assert "SELECT " not in joined.upper()
    assert "--where" not in args


def test_api_query_surface_is_fixed_and_escapes_region_values():
    table = "iceberg.serving.bi_daily_executive"
    daily = executive_daily_sql(table=table, business_date=date(2026, 8, 20))
    assert "FROM iceberg.serving.bi_daily_executive" in daily
    assert "DATE '2026-08-20'" in daily

    assert sql_string_literal("O'Hare") == "'O''Hare'"
    region = region_daily_sql(
        table=table,
        region="O'Hare",
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 20),
    )
    assert "region = 'O''Hare'" in region
    assert "BETWEEN DATE '2026-08-01' AND DATE '2026-08-20'" in region


def test_trino_compose_and_catalog_are_wired_to_polaris_iceberg():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    trino = compose["services"]["trino"]
    assert trino["image"].endswith("trinodb/trino:483}") or "trinodb/trino:483" in trino["image"]
    assert "./infra/trino/catalog:/etc/trino/catalog:ro" in trino["volumes"]

    catalog = (ROOT / "infra" / "trino" / "catalog" / "iceberg.properties").read_text(encoding="utf-8")
    assert "connector.name=iceberg" in catalog
    assert "iceberg.catalog.type=rest" in catalog
    assert "iceberg.rest-catalog.uri=http://polaris:8181/api/catalog" in catalog
    assert "iceberg.rest-catalog.vended-credentials-enabled=true" in catalog
    assert "fs.s3.enabled=true" in catalog
    assert "s3.endpoint=http://rustfs:9000" in catalog
    assert "iceberg.rest-catalog.http-headers=Polaris-Realm:${ENV:POLARIS_REALM}" in catalog


def test_serving_is_registered_in_dagster_and_architecture_source():
    definitions = (ROOT / "orchestration" / "dagster" / "commerce_dagster" / "definitions.py").read_text(
        encoding="utf-8"
    )
    jobs = (ROOT / "orchestration" / "dagster" / "commerce_dagster" / "jobs.py").read_text(encoding="utf-8")
    schedules = (ROOT / "orchestration" / "dagster" / "commerce_dagster" / "schedules.py").read_text(
        encoding="utf-8"
    )
    assert "bi_daily_executive" in definitions
    assert "serving_daily_export_job" in jobs
    assert "serving_daily_export_schedule" in schedules

    diagram = (ROOT / "docs" / "architecture" / "AI_NATIVE_DATA_AGENT.mmd").read_text(encoding="utf-8")
    for token in ("MetricFlow", "Dagster", "Iceberg Serving Tables", "Trino", "BI / Dashboard", "FastAPI"):
        assert token in diagram
    assert (ROOT / "docs" / "architecture" / "AI_NATIVE_DATA_AGENT.dot").exists()
    assert (ROOT / "docs" / "architecture" / "AI_NATIVE_DATA_AGENT.svg").exists()


def test_serving_export_fails_closed_on_upstream_readiness_and_exact_partition_replace():
    asset = (ROOT / "orchestration" / "dagster" / "commerce_dagster" / "assets" / "serving.py").read_text(encoding="utf-8")
    readiness = (ROOT / "orchestration" / "dagster" / "commerce_dagster" / "serving_readiness.py").read_text(encoding="utf-8")
    materializer = (ROOT / "serving" / "jobs" / "materialize_export.py").read_text(encoding="utf-8")

    assert "missing_daily_asset_partitions" in asset
    assert "required exact-partition upstream assets" in asset
    assert "asset_partitions=[partition_key]" in readiness
    assert ".overwrite(" in materializer
    assert "== F.lit(partition_day)" in materializer
    assert ".overwritePartitions()" not in materializer
    assert "violates target grain uniqueness" in materializer


def test_serving_bi_examples_read_projection_without_redefining_metrics():
    sql = (ROOT / "serving" / "bi" / "dashboard_queries.sql").read_text(encoding="utf-8")
    assert "FROM iceberg.serving.bi_daily_executive" in sql
    lowered = sql.lower()
    assert "sum(" not in lowered
    assert "count(" not in lowered
    assert "gross_sales -" not in lowered


def test_serving_api_routes_are_fixed_contracts_with_dependency_override():
    from decimal import Decimal

    from fastapi.testclient import TestClient

    from serving.api.main import app, get_repository

    class FakeRepository:
        def ping(self):
            return True

        def executive_daily(self, business_date):
            return [{
                "business_date": business_date,
                "region": "West",
                "gross_sales": Decimal("100.00"),
                "sales_before_reversal": Decimal("90.00"),
                "net_sales": Decimal("85.00"),
                "order_count": 5,
                "average_order_value": Decimal("18.00"),
            }]

        def region_daily(self, *, region, start_date, end_date):
            return self.executive_daily(start_date)

    app.dependency_overrides[get_repository] = lambda: FakeRepository()
    try:
        client = TestClient(app)
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json() == {"status": "ready"}

        response = client.get("/api/v1/executive/daily?business_date=2026-08-20")
        assert response.status_code == 200
        assert response.json()[0]["region"] == "West"
        assert response.json()[0]["net_sales"] == "85.00"

        invalid = client.get(
            "/api/v1/regions/West/daily?start_date=2026-08-20&end_date=2026-08-01"
        )
        assert invalid.status_code == 422
    finally:
        app.dependency_overrides.clear()
