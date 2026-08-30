"""Runtime assertions after loading the Shopify fixtures twice and normalizing.

This is deliberately a Spark job so it validates the same Polaris/Iceberg path
used by the ingestion and normalization jobs.
"""

from pyspark.sql import SparkSession, functions as F


EXPECTED_COUNTS = {
    "polaris.source.shopify_order": 5,
    "polaris.source.shopify_order_item": 5,
    "polaris.source.shopify_line_item_discount_allocation": 1,
    "polaris.source.shopify_transaction": 7,
    "polaris.source.shopify_refund": 1,
    "polaris.source.shopify_refund_item": 1,
    "polaris.source.shopify_refund_transaction": 1,
    "polaris.source.shopify_fulfillment": 1,
    "polaris.source.shopify_fulfillment_item": 1,
    "polaris.source.shopify_fulfillment_event": 2,
}

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


def main():
    spark = SparkSession.builder.appName("validate-commerce-runtime").getOrCreate()
    failures = []

    raw_count = spark.table("polaris.raw.raw_shopify_order_payload").count()
    if raw_count < 10:
        failures.append(
            f"raw observations expected >= 10 after two fixture loads, got {raw_count}"
        )

    print(f"raw observations: {raw_count}")

    for table, expected in EXPECTED_COUNTS.items():
        df = spark.table(table)
        actual = df.count()
        print(f"{table}: {actual} (expected {expected})")
        if actual != expected:
            failures.append(f"{table}: expected {expected}, got {actual}")

        keys = BUSINESS_KEYS[table]
        duplicate_versions = (
            df.groupBy(*keys, "record_hash")
            .count()
            .filter(F.col("count") > 1)
            .count()
        )
        if duplicate_versions:
            failures.append(
                f"{table}: {duplicate_versions} duplicate business_key + record_hash groups"
            )

    if failures:
        print("\nRUNTIME VALIDATION FAILED")
        for failure in failures:
            print(f" - {failure}")
        raise SystemExit(1)

    print("\nRUNTIME VALIDATION PASSED")
    print("At-least-once Raw ingestion + Structured Source idempotency are consistent.")


if __name__ == "__main__":
    main()
