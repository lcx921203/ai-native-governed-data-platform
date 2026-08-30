"""Canonical dbt project exposed as three non-overlapping Dagster execution groups."""

from __future__ import annotations

import json

import dagster as dg
from dagster_dbt import (
    DbtCliResource,
    DagsterDbtTranslator,
    DagsterDbtTranslatorSettings,
    dbt_assets,
)

from ..dbt_failure_adapter import execute_classified_dbt
from ..partitions import SHOPIFY_DAILY_PARTITIONS, shopify_dbt_window_vars
from ..project import commerce_dbt_project


DBT_STAGING_SELECT = "path:models/staging"
DBT_FOUNDATION_SELECT = "seed_items seed_stores items stores time_spine_daily"
DBT_WINDOWED_SELECT = "tag:shopify_windowed"


dbt_translator = DagsterDbtTranslator(
    settings=DagsterDbtTranslatorSettings(
        enable_asset_checks=True,
        enable_source_tests_as_checks=True,
    )
)


@dbt_assets(
    manifest=commerce_dbt_project.manifest_path,
    project=commerce_dbt_project,
    select=DBT_STAGING_SELECT,
    name="commerce_staging_dbt_assets",
    dagster_dbt_translator=dbt_translator,
)
def commerce_staging_dbt_assets(
    context: dg.AssetExecutionContext,
    dbt: DbtCliResource,
):
    """Create/reconcile global Staging Views during deployment/model changes."""

    yield from execute_classified_dbt(context=context, dbt=dbt, args=["build"])


@dbt_assets(
    manifest=commerce_dbt_project.manifest_path,
    project=commerce_dbt_project,
    select=DBT_FOUNDATION_SELECT,
    name="commerce_dbt_foundation_assets",
    dagster_dbt_translator=dbt_translator,
)
def commerce_dbt_assets(
    context: dg.AssetExecutionContext,
    dbt: DbtCliResource,
):
    """Build seeds/master-data/time-spine assets that do not own Shopify daily time."""

    yield from execute_classified_dbt(context=context, dbt=dbt, args=["build"])


@dbt_assets(
    manifest=commerce_dbt_project.manifest_path,
    project=commerce_dbt_project,
    select=DBT_WINDOWED_SELECT,
    name="commerce_windowed_dbt_assets",
    partitions_def=SHOPIFY_DAILY_PARTITIONS,
    dagster_dbt_translator=dbt_translator,
    backfill_policy=dg.BackfillPolicy.multi_run(),
)
def commerce_windowed_dbt_assets(
    context: dg.AssetExecutionContext,
    dbt: DbtCliResource,
):
    """Build one exact daily execution partition with the shared effective window."""

    dbt_vars = shopify_dbt_window_vars(context)
    yield from execute_classified_dbt(
        context=context,
        dbt=dbt,
        args=["build", "--vars", json.dumps(dbt_vars)],
    )
