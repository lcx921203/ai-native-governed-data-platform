# Shopify Real API Ingestion — Fixture / Production Dual Path

Date: 2026-08-20

## 1. Why two source paths exist

The project now keeps two source modes behind one Raw contract:

```text
fixture profile
  local JSON fixtures
       ↓
  Spark Raw append
       ↓
  polaris.raw.raw_shopify_order_payload

production profile
  Shopify Admin GraphQL API
       ↓
  full Order + nested pagination
       ↓
  .runtime JSONL landing
       ↓
  Spark Raw append
       ↓
  polaris.raw.raw_shopify_order_payload
```

数据源切换不需要额外 YAML 文件，运行环境只需要设置：

```bash
# 本地 / Demo / Clean-room
SHOPIFY_SOURCE_MODE=fixture

# 生产真实 API
SHOPIFY_SOURCE_MODE=production
```

没有配置时源码安全默认 `fixture`。生产凭据仍然只放环境变量：

```text
SHOPIFY_SHOP_DOMAIN
SHOPIFY_ADMIN_ACCESS_TOKEN
```

Both paths intentionally end at the same Raw schema and the same append-only / at-least-once contract. Therefore Normalize, dbt, MetricFlow and downstream logic do not need a second production-only branch.

## 2. Production path step by step

### Step 1 — Dagster owns the logical daily partition

Dagster still owns one exact UTC daily partition. The effective source-read start is expanded backward by the existing five-minute Lookback Window.

```text
Logical window:        [2026-08-05 00:00, 2026-08-06 00:00)
Effective source read: [2026-08-04 23:55, 2026-08-06 00:00)
```

The Lookback changes source reading only. It does not change partition ownership.

### Step 2 — environment selects fixture or production

`raw_shopify_order_payload` reads `SHOPIFY_SOURCE_MODE` for every materialization.

- `fixture` → `load_fixtures.py`
- `production` → `extract_orders_in_window()` + `load_api_observations.py`

### Step 3 — Real Shopify query uses `updated_at`

The extractor filters Orders by `updated_at`, not `created_at`, because an old Order can later change due to payment, refund, cancellation or fulfillment.

The interval is half-open:

```text
[start, end)
```

This prevents a boundary timestamp from being counted in both adjacent logical windows while the Lookback still deliberately allows repeated observations.

## 3. `first` and `after` — beginner explanation

Shopify GraphQL uses cursor pagination for connection fields.

```text
first = page size

after = cursor position
```

`first: 100` does **not** mean "cursor 100". It means:

> Give me at most the first 100 nodes after the position identified by `after`.

First request:

```json
{
  "first": 100,
  "after": null
}
```

Response:

```json
{
  "nodes": ["1 ... 100"],
  "pageInfo": {
    "hasNextPage": true,
    "endCursor": "CURSOR_100"
  }
}
```

Second request:

```json
{
  "first": 100,
  "after": "CURSOR_100"
}
```

Now Shopify returns the next page after the last item of the previous page.

The loop stops only when:

```text
hasNextPage = false
```

Shopify documents a maximum of 250 nodes per connection page. This project deliberately keeps the configured default at 100 so request cost and payload size stay bounded while correctness comes from looping until the connection is complete.

## 4. Which nested fields require cursor pagination

The project now completes every cursor connection used by the Order-domain model:

```text
Order
├── lineItems                         connection → cursor loop
├── transactions                     array      → no cursor
├── refunds                          array      → no cursor
│   ├── refundLineItems              connection → cursor loop
│   └── transactions                 connection → cursor loop
└── fulfillments                     array      → no cursor
    ├── fulfillmentLineItems         connection → cursor loop
    └── events                       connection → cursor loop
```

The distinction matters. Cursor logic should not be invented for fields that do not expose `pageInfo`.

For `Order.transactions`, `Order.refunds` and `Order.fulfillments`, the main query does not pass the optional `first` truncation argument, so the extractor does not silently cap those array fields at 100.

## 5. Why nested pagination uses separate GraphQL documents

The first Order query gets the first page of all relevant connections. If one nested connection says `hasNextPage=true`, a small follow-up query retrieves only the missing connection page for its parent Node ID.

Files:

```text
ingestion/shopify/queries/
├── orders.graphql
├── order_line_items_page.graphql
├── refund_line_items_page.graphql
├── refund_transactions_page.graphql
├── fulfillment_line_items_page.graphql
└── fulfillment_events_page.graphql
```

This keeps each cursor loop explicit and independently testable.

## 6. HTTP and GraphQL failure handling

A successful HTTP status is not enough to declare the request successful.

The real extractor checks two layers:

1. HTTP errors
   - retries 429 and transient 5xx responses;
   - honors `Retry-After` when present;
   - otherwise uses bounded exponential backoff.
2. GraphQL errors
   - retries GraphQL `THROTTLED` errors within the same bounded retry budget;
   - raises other GraphQL errors immediately.

The retry loop is bounded because an ingestion task must eventually fail and hand control to Dagster recovery rather than retry forever.

## 7. Why the real HTTP process and Spark writer are separated

The project keeps HTTP extraction in the Dagster host Python process and Iceberg writing in Spark.

```text
Dagster host
  requests + Shopify credentials
       ↓
.runtime/shopify-api/orders-<partition>.jsonl
       ↓
Spark container
  load_api_observations.py
       ↓
Raw Iceberg
```

Reasons:

- Shopify credentials do not need to be installed inside the Spark service.
- The stock Spark container does not need additional HTTP runtime dependencies.
- The Raw writer stays identical in responsibility: add observation metadata and append to Iceberg.
- The JSONL file is a runtime handoff/evidence object under `.runtime/`, not a new source-of-truth layer.

## 8. Raw contract stays unchanged

Regardless of source mode, one Raw row means:

```text
one API observation
```

Raw fields include:

```text
shopify_order_id
order_updated_at
extracted_at
batch_id
source_file
payload
```

Fixture mode records the fixture file name in `source_file`.
Production mode records `shopify-admin-graphql`.

Repeated Lookback observations are intentionally allowed. Structured Source still decides whether the business content is a new version using:

```text
Business Key + record_hash
```

## 9. Production-scale note

Cursor pagination is correct for the incremental API path implemented here. For very large historical backfills, Shopify also recommends GraphQL Bulk Operations as an alternative to repeatedly paginating very large volumes. That is a scale optimization, not a replacement for the project's current incremental semantics.

## 10. Static acceptance added with this enhancement

The source tree now tests:

- `SHOPIFY_SOURCE_MODE` 缺省时安全走 `fixture`;
- 部署环境可以显式设置 `SHOPIFY_SOURCE_MODE=production`;
- root Order cursor passes `endCursor` back as `after`;
- all project nested cursor connections are fully appended;
- every nested page GraphQL document accepts `$after` and returns `pageInfo`;
- Python compilation for the new ingestion modules.

Latest full source test run after this change:

```text
351 passed
```

This remains source/static evidence. It does not claim that a real Shopify shop was contacted in this environment.


## 11. Query Cost / proactive throttling

Shopify Admin GraphQL is limited by calculated query cost, not a fixed “N requests per second”.
The response `extensions.cost` exposes requested/actual cost and `throttleStatus`.

The extractor therefore has two layers:

```text
proactive: currentlyAvailable + restoreRate -> sleep before the next similar request
reactive:  HTTP 429 / GraphQL THROTTLED      -> bounded retry + backoff
```

The client does not hard-code one store plan's bucket size. It consumes the runtime throttle status
returned by Shopify. A single query that receives `MAX_COST_EXCEEDED` is treated as a query design error,
not something that unlimited retry can fix. Large historical initial loads should evaluate Shopify Bulk Operations;
normal incremental windows continue to use `updated_at + lookback + cursor pagination`.

## 12. API version and Schema Drift

Every production response can expose `X-Shopify-API-Version`. The extractor defaults to Fail Closed if the
actual version differs from the requested stable version. That mismatch is an early warning that a version
fall-forward or contract change may have happened.

Schema changes are classified before Structured Source:

```text
safe optional ADD      -> extend parser + Iceberg ADD COLUMN + dbt contract
safe type WIDEN        -> controlled Iceberg widening after compatibility check
RENAME / DROP          -> manual semantic + lineage review
breaking type/meaning  -> Raw keeps payload; Normalize fails closed; contract change + backfill
```

Iceberg Schema Evolution answers **how table structure can evolve safely**. It does not decide whether two
business fields mean the same thing. Business semantic compatibility remains a governed contract decision.
