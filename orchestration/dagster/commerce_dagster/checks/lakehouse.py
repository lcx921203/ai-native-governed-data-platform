"""Custom pre-dbt Asset Checks; dbt-owned rules remain in dbt."""

import dagster as dg

from ..assets.lakehouse import RAW_ASSET_KEY, SHOPIFY_SOURCE_ASSET_KEYS
from ..partitions import shopify_source_window_args
from ..resources import SparkComposeResource


@dg.asset_check(
    asset=RAW_ASSET_KEY,
    name="raw_has_observations",
    description="Raw Iceberg must contain at least one observation for the executed slice.",
)
def raw_has_observations(
    context: dg.AssetCheckExecutionContext,
    spark: SparkComposeResource,
):
    try:
        spark.spark_submit(
            "lakehouse/jobs/check_raw_observations.py",
            context,
            script_args=shopify_source_window_args(context),
        )
    except Exception as exc:
        return dg.AssetCheckResult(passed=False, metadata={"error": str(exc)})
    return dg.AssetCheckResult(passed=True)


@dg.asset_check(
    asset=SHOPIFY_SOURCE_ASSET_KEYS["orders"],
    name="structured_source_idempotency",
    description=(
        "Control check for business-key + record-hash uniqueness across Shopify Source tables."
    ),
)
def structured_source_idempotency(
    context: dg.AssetCheckExecutionContext,
    spark: SparkComposeResource,
):
    try:
        spark.spark_submit(
            "lakehouse/jobs/check_source_idempotency.py",
            context,
            script_args=shopify_source_window_args(context),
        )
    except Exception as exc:
        return dg.AssetCheckResult(passed=False, metadata={"error": str(exc)})
    return dg.AssetCheckResult(
        passed=True,
        metadata={"scope": "all Shopify Structured Source tables"},
    )
