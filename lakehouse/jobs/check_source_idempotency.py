"""Asset Check compute: verify Business Key + record_hash uniqueness for touched Source rows.

Structured Source keeps distinct business-content versions. Re-observing the same version
must update observation metadata rather than create a duplicate business_key + record_hash.
Only rows touched by the Dagster effective source window are scanned for this execution.
"""

from __future__ import annotations

import argparse

from pyspark.sql import SparkSession, functions as F


BUSINESS_KEYS = {
    "polaris.source.shopify_order": ["order_id"],
    "polaris.source.shopify_order_item": ["line_item_id"],
    "polaris.source.shopify_line_item_discount_allocation": [
        "line_item_id",
        "discount_application_index",
    ],
    "polaris.source.shopify_transaction": ["transaction_id"],
    "polaris.source.shopify_refund": ["refund_id"],
    "polaris.source.shopify_refund_item": ["refund_line_item_id"],
    "polaris.source.shopify_refund_transaction": ["refund_id", "transaction_id"],
    "polaris.source.shopify_fulfillment": ["fulfillment_id"],
    "polaris.source.shopify_fulfillment_item": ["fulfillment_line_item_id"],
    "polaris.source.shopify_fulfillment_event": ["fulfillment_event_id"],
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-start", required=True, help="Inclusive ISO-8601 UTC start")
    parser.add_argument("--window-end", required=True, help="Exclusive ISO-8601 UTC end")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spark = SparkSession.builder.appName("check-source-idempotency").getOrCreate()
    failures: list[str] = []

    for table, business_keys in BUSINESS_KEYS.items():
        # A Business Version can be re-observed later. Use overlap between its
        # observation interval and this execution window so replaying an older
        # partition does not hide a version whose last observation moved forward.
        touched = (
            spark.table(table)
            .where(
                F.col("first_source_updated_at")
                < F.lit(args.window_end).cast("timestamp")
            )
            .where(
                F.col("last_source_updated_at")
                >= F.lit(args.window_start).cast("timestamp")
            )
        )
        touched_count = touched.count()
        duplicate_groups = (
            touched.groupBy(*business_keys, "record_hash")
            .count()
            .where(F.col("count") > 1)
            .count()
        )
        print(
            f"source idempotency: table={table} touched={touched_count} "
            f"duplicate_groups={duplicate_groups} "
            f"window=[{args.window_start}, {args.window_end})"
        )
        if duplicate_groups:
            failures.append(
                f"{table}: {duplicate_groups} duplicate business_key + record_hash groups"
            )

    if failures:
        print("SOURCE IDEMPOTENCY CHECK FAILED")
        for failure in failures:
            print(f" - {failure}")
        raise SystemExit(1)

    print("SOURCE IDEMPOTENCY CHECK PASSED")


if __name__ == "__main__":
    main()
