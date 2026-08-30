# dbt Discount Incremental Chain

## Goal

Close the OrderItem discount path without a full-history `row_number()` or a full-table
`GROUP BY` on every daily Dagster partition.

```text
Shopify Window
  -> DiscountAllocation changed composite keys
  -> Incremental DiscountAllocation Current State
  -> affected LineItem ids
  -> re-aggregate complete CURRENT allocations for those LineItems
  -> Incremental LineItem Discount total
  -> order_items Mart affected keys
```

## 1. Current-state grain

`int_shopify__discount_allocations_canonical`

```text
Business Key = line_item_id + discount_application_index
```

The execution window narrows **changed keys**. Only new/re-observed candidate versions
plus the existing materialized Current Row for those keys enter `row_number()`.

## 2. Why the aggregate cannot sum only today's changed rows

Suppose L1 currently has two allocations:

```text
A1 = 5
A2 = 7
Total = 12
```

Today A1 changes to 3. The correct new total is:

```text
A1 = 3
A2 = 7  <- unchanged but still part of CURRENT state
Total = 10
```

Therefore the window first discovers `L1` as affected, then the model reads the **full
CURRENT allocation set for L1** and recomputes the total.

## 3. Materialization

Both models are Iceberg incremental MERGE tables:

```text
int_shopify__discount_allocations_canonical
  unique key: [line_item_id, discount_application_index]

int_shopify__order_item_discounts
  unique key: [line_item_id, currency_code]
```

`order_items` joins discount totals on both `line_item_id` and `currency_code` to avoid
multi-currency fanout.

## 4. Deliberate boundary

Missing nested members are not interpreted as deletes. Snapshot reconciliation / tombstone
semantics require a proven complete Shopify nested snapshot and remain a production gap.
