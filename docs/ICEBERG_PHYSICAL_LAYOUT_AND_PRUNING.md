# Iceberg Physical Layout & Pruning — Phase 3B

## 1. Why this is a separate layer

Three boundaries must remain independent:

```text
Dagster Partition / Execution Window
→ which source-update interval this run owns

Changed / Affected Keys
→ which business objects must be recomputed

Iceberg Physical Layout
→ which data files Spark can avoid reading
```

A Dagster partition is orchestration state. An Iceberg partition is file-layout metadata.
A run for August 5 may recompute an Order created on August 1, so using the Dagster date as
an Iceberg business partition would mix execution time with business time.

## 2. Baseline layout policy

| Layer / table class | Physical partition | Write ordering | Why |
| --- | --- | --- | --- |
| Raw Observation | `days(order_updated_at)` | partition-local `order_updated_at, order_id, extracted_at` | Raw is append-only and the dominant read is the Shopify source-update window. |
| Structured Source Business Version | none | `last_source_updated_at, business key, record_hash` | `last_source_updated_at` moves when the same content version is re-observed; avoid hard partition movement on a mutable technical clock. |
| Canonical Current State | none | `source_updated_at, business key` | one current row per key; the technical clock is mutable and key/window lookups dominate. |
| Orders / OrderItems | `days(order_time)` | Mart key within partition | dominant analytical clock is Order business time. |
| Payment Transactions | `days(transaction_processed_at)` | transaction key within partition | matches the semantic model's default payment activity time. |
| Refund / RefundItem | `days(refund_time)` | Mart key within partition | matches refund activity analysis. |
| Fulfillment | `days(fulfillment_created_at)` | fulfillment key within partition | stable lifecycle anchor and semantic-model default. |
| Fulfillment Event | `days(event_time)` | event key within partition | event_time is the business-event clock. |
| FulfillmentItem | none, initially | `in_transit_at, delivered_at, fulfillment_line_item_id` | no single immutable lifecycle day safely represents both shipped and delivered analysis. |

The policy is intentionally asymmetric. Consistency of semantics is more important than giving
every table a daily partition.

## 3. Hidden partitioning in dbt-spark

The seven partitioned Marts declare Iceberg transform expressions directly in `partition_by`:

```jinja
{{ config(
    materialized='incremental',
    incremental_strategy='merge',
    unique_key='order_id',
    file_format='iceberg',
    partition_by='days(order_time)'
) }}
```

The dbt-spark adapter renders `partition_by` items directly into Spark's `PARTITIONED BY (...)`
clause, allowing the Iceberg transform expression to remain hidden from the logical model.

## 4. Why Current State is not partitioned by `source_updated_at`

It is tempting to use:

```text
PARTITIONED BY days(source_updated_at)
```

for Current State because every Dagster run filters that column. But Current State rows move in
technical time whenever the entity changes. Hard time partitioning would therefore make ordinary
updates into cross-partition row movement and can create many small daily partitions. The initial
policy keeps Current State unpartitioned and uses write ordering/file statistics as the softer
pruning mechanism.

This is a baseline, not a universal rule. If production evidence later shows that recent-window
scans dominate and write churn is acceptable, Iceberg partition evolution allows the layout to be
changed without changing the logical query contract.

## 5. Business Time partitioning is intentionally different from Dagster time

Example: Order #1003 was created on August 1 and refunded/updated on August 5.

```text
Dagster partition = 2026-08-05
→ recompute the changed keys

orders Iceberg partition = day(order_time) = 2026-08-01
→ the MERGE may rewrite/touch the August 1 business partition

refunds Iceberg partition = day(refund_time) = 2026-08-05
→ refund activity belongs to August 5
```

This is expected. Execution time determines recomputation; business time determines analytical
placement.

## 6. Write order is not the same as query order

`lakehouse/ddl/001_raw.sql` and `002_shopify_source.sql` establish Raw / Structured Source layout
before ingestion writes. `003_iceberg_write_layout.sql` configures dbt-produced table write
ordering/distribution after the analytics tables exist. None of these settings promises that SELECT
results are returned in that order. The goal is file locality and useful
file-level min/max statistics, not presentation ordering.

Changing a sort order also does not rewrite old data files. Existing files should only be rewritten
through an explicit maintenance operation after runtime evidence justifies the cost. Do not place a
full-table rewrite in the daily Dagster path.

## 7. Small-file boundary

Daily partitions can still accumulate small files, especially when incremental MERGE touches small
sets of rows. The production maintenance mechanism is Iceberg `rewrite_data_files`, but its cadence,
strategy (`binpack` vs `sort`) and thresholds must be chosen from observed file counts/sizes. It is
not automatically scheduled in Phase 3B.

## 8. Acceptance ladder

```text
A. Static layout contract
   DDL / dbt configs / validation scripts are internally consistent          ✅

B. Runtime table metadata
   SHOW CREATE TABLE / .partitions / .files match the intended layout         ⏸

C. Runtime query-plan evidence
   EXPLAIN shows source/business-time filters reaching Iceberg scans           ⏸

D. Empirical pruning benchmark
   scaled data proves fewer files/bytes scanned than the baseline              ⏸
```

Only A can be completed in the current environment. B/C/D require a real Spark + Iceberg runtime;
D additionally needs enough files/partitions to make the comparison meaningful.

Future runtime command:

```bash
bash infra/runtime/run_iceberg_physical_layout_validation.sh
```

The script deliberately prints evidence and states that it is not a production-scale benchmark.

## 9. Production decision rule

```text
Filter early, but never earlier than correctness allows.
Partition physically, but only on a clock whose write cost and query benefit are understood.
Benchmark pruning; do not infer it from SQL text alone.
```
