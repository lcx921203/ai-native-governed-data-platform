"""Asset Check compute: verify Raw contains observations for the effective source window.

The check is intentionally window-aware so a Dagster partition checks the same source
slice that Raw/Normalize read. A zero-count result exits non-zero and is surfaced by the
(non-blocking) Dagster Asset Check as observability evidence.
"""

from __future__ import annotations

import argparse

from pyspark.sql import SparkSession, functions as F


RAW_TABLE = "polaris.raw.raw_shopify_order_payload"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--window-start", required=True, help="Inclusive ISO-8601 UTC start")
    parser.add_argument("--window-end", required=True, help="Exclusive ISO-8601 UTC end")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    spark = SparkSession.builder.appName("check-raw-observations").getOrCreate()

    rows = (
        spark.table(RAW_TABLE)
        .where(F.col("order_updated_at") >= F.lit(args.window_start).cast("timestamp"))
        .where(F.col("order_updated_at") < F.lit(args.window_end).cast("timestamp"))
    )
    count = rows.count()
    print(
        f"raw window observations: table={RAW_TABLE} count={count} "
        f"window=[{args.window_start}, {args.window_end})"
    )
    if count <= 0:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
