"""Dagster code location for Commerce Modern Data Platform — Phase 3C."""

import dagster as dg
from dagster_dbt import DbtCliResource

from .assets.dbt import (
    commerce_dbt_assets,
    commerce_staging_dbt_assets,
    commerce_windowed_dbt_assets,
)
from .assets.lakehouse import raw_shopify_order_payload, shopify_structured_source
from .assets.serving import bi_daily_executive
from .checks.lakehouse import raw_has_observations, structured_source_idempotency
from .freshness import apply_shopify_daily_freshness_policy
from .jobs import commerce_dbt_foundation_job, serving_daily_export_job, shopify_daily_partition_job
from .project import DBT_PROFILES_DIR, DBT_PROJECT_DIR, PROJECT_ROOT
from .resources import SparkComposeResource
from .schedules import serving_daily_export_schedule, shopify_daily_partition_schedule
from .sensors import shopify_daily_recovery_sensor


base_defs = dg.Definitions(
    assets=[
        raw_shopify_order_payload,
        shopify_structured_source,
        commerce_staging_dbt_assets,
        commerce_dbt_assets,
        commerce_windowed_dbt_assets,
        bi_daily_executive,
    ],
    asset_checks=[raw_has_observations, structured_source_idempotency],
    jobs=[commerce_dbt_foundation_job, shopify_daily_partition_job, serving_daily_export_job],
    schedules=[shopify_daily_partition_schedule, serving_daily_export_schedule],
    sensors=[shopify_daily_recovery_sensor],
    resources={
        "spark": SparkComposeResource(project_dir=str(PROJECT_ROOT)),
        "dbt": DbtCliResource(
            project_dir=str(DBT_PROJECT_DIR),
            profiles_dir=str(DBT_PROFILES_DIR),
        ),
    },
)

# Freshness is metadata/service policy on consumer marts; compute ownership is unchanged.
defs = apply_shopify_daily_freshness_policy(base_defs)
