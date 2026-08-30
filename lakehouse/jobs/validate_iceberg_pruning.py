"""Collect runtime evidence for Phase 3B Iceberg physical layout.

This validator intentionally distinguishes three levels:

1. table layout metadata exists;
2. Spark/Iceberg accepts business/source-time filters and exposes query plans;
3. empirical file-scan reduction at production scale.

The first two can be inspected in the project runtime. The third requires a scaled
benchmark dataset and is deliberately NOT claimed by this script.
"""
from __future__ import annotations

from pyspark.sql import SparkSession


PARTITIONED_TABLES = {
    "polaris.raw.raw_shopify_order_payload": "order_updated_at",
    "polaris.analytics.orders": "order_time",
    "polaris.analytics.order_lifecycle_snapshot": "order_time",
    "polaris.analytics.order_items": "order_time",
    "polaris.analytics.payment_transactions": "transaction_processed_at",
    "polaris.analytics.refunds": "refund_time",
    "polaris.analytics.refund_items": "refund_time",
    "polaris.analytics.fulfillments": "fulfillment_created_at",
    "polaris.analytics.fulfillment_events": "event_time",
}

UNPARTITIONED_CURRENT_STATE = [
    "polaris.analytics.int_shopify__orders_canonical",
    "polaris.analytics.int_shopify__order_items_canonical",
    "polaris.analytics.int_shopify__discount_allocations_canonical",
    "polaris.analytics.int_shopify__order_item_discounts",
    "polaris.analytics.int_shopify__transactions_canonical",
    "polaris.analytics.int_shopify__refunds_canonical",
    "polaris.analytics.int_shopify__refund_items_canonical",
    "polaris.analytics.int_shopify__fulfillments_canonical",
    "polaris.analytics.int_shopify__fulfillment_items_canonical",
    "polaris.analytics.int_shopify__fulfillment_events_canonical",
]


def print_rows(title: str, df, truncate: bool = False) -> None:
    print(f"\n--- {title} ---")
    df.show(200, truncate=truncate, vertical=False)


def explain(spark: SparkSession, label: str, sql: str) -> None:
    print(f"\n--- EXPLAIN · {label} ---")
    rows = spark.sql(f"EXPLAIN FORMATTED {sql}").collect()
    print("\n".join(str(row[0]) for row in rows))


def main() -> None:
    spark = SparkSession.builder.appName("iceberg-physical-layout-validation").getOrCreate()

    print("Phase 3B Iceberg physical-layout evidence")
    print("Runtime evidence only; no production-scale pruning benchmark is claimed.")

    for table, clock in PARTITIONED_TABLES.items():
        print_rows(f"SHOW CREATE TABLE · {table}", spark.sql(f"SHOW CREATE TABLE {table}"))
        print_rows(f"PARTITIONS · {table}", spark.sql(f"SELECT * FROM {table}.partitions"))
        print_rows(
            f"FILES · {table}",
            spark.sql(
                f"SELECT file_path, record_count, file_size_in_bytes, sort_order_id, readable_metrics "
                f"FROM {table}.files"
            ),
            truncate=True,
        )

        explain(
            spark,
            f"{table} / {clock} day filter",
            f"SELECT count(*) FROM {table} "
            f"WHERE {clock} >= TIMESTAMP '2026-08-05 00:00:00' "
            f"AND {clock} < TIMESTAMP '2026-08-06 00:00:00'",
        )

    # Current State is intentionally unpartitioned. We still inspect its file metrics
    # and a technical-window query because write ordering may improve file-stat pruning.
    for table in UNPARTITIONED_CURRENT_STATE:
        print_rows(f"SHOW CREATE TABLE · {table}", spark.sql(f"SHOW CREATE TABLE {table}"))
        print_rows(
            f"FILES · {table}",
            spark.sql(
                f"SELECT file_path, record_count, file_size_in_bytes, sort_order_id, readable_metrics "
                f"FROM {table}.files"
            ),
            truncate=True,
        )

    explain(
        spark,
        "Current State technical window",
        "SELECT count(*) FROM polaris.analytics.int_shopify__orders_canonical "
        "WHERE source_updated_at >= TIMESTAMP '2026-08-05 00:00:00' "
        "AND source_updated_at < TIMESTAMP '2026-08-06 00:00:00'",
    )

    print("\nEVIDENCE COMPLETE")
    print("Next runtime gate: compare scanned files/bytes on a scaled dataset before and after layout changes.")


if __name__ == "__main__":
    main()
