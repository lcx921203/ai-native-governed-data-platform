# Modeling Guide

## 1. 分层

### Source
外部上游结构化对象。允许同一个业务对象存在多个内容版本。

### Staging
- 尽量一对一映射 Source
- Rename / Cast / 基础标准化
- 不主动取 latest
- 不主动大 Join
- 不主动改变 Grain

### Intermediate
只有在存在明确转换目的时创建：
- Canonicalization
- Re-grain
- Join
- Aggregate
- Complex preparation

### Marts
面向业务实体 / 事件：
- orders
- order_items
- payment_transactions
- refunds
- refund_items
- fulfillments
- fulfillment_items
- fulfillment_events

## 2. Canonical Version

Canonical 不是“整个订单只保留最新一条事件”。

例如 Payment：

```text
T1 PENDING
T1 SUCCESS
T2 CAPTURE SUCCESS
```

按 `transaction_id` 收敛后：

```text
T1 SUCCESS
T2 CAPTURE SUCCESS
```

T1 / T2 是不同业务事件，都保留。

## 3. Business Time

不要用一个模糊 `dt`。

```text
order_time
payment_time
refund_time
in_transit_at
delivered_at
event_time
```

## 4. Fanout

```text
Order PRIMARY
↓
OrderItem FOREIGN
```

从 Order 向 OrderItem 是 1:N，Metric 可能被放大。

所以：
- `order_count by category` 不应直接从 Order Metric 强行访问 Category。
- 应定义 `orders_with_item = count_distinct(order_id)`，Owner 在 OrderItem Grain。

## 5. Order Lifecycle Accumulating Snapshot

Current State 与 Accumulating Snapshot（累计快照）回答的是两个不同问题：

```text
Current State
  -> 订单现在是什么状态？

Accumulating Snapshot
  -> 这个订单已经经历了哪些生命周期里程碑？每个里程碑什么时候发生？
```

当前实现：

```text
dbt/mercaso_dbt/models/marts/commerce/order_lifecycle_snapshot.sql

Operational contract: the accumulating snapshot is included in the governed **nine-Mart Freshness / Recovery SLA**. This promotion is source/static; real Dagster/Iceberg completion evidence remains DEFERRED.
```

Grain 固定为：

```text
1 order_id = 1 row
```

模型从 Order / Transaction / Refund / Fulfillment / FulfillmentEvent 的 Canonical Current State
收集可被源数据明确证明的里程碑：

```text
order_time
processed_at
first_authorized_at
first_paid_at
first_refund_at
first_fulfillment_at
first_in_transit_at
first_delivered_at
latest_delivered_at
cancelled_at
closed_at
```

`first_paid_at` 只认 `SUCCESS + CAPTURE/SALE`。Authorization 是授权成功，不等于已经收款。

当前源契约没有可靠 `picked_at`，因此累计快照不会根据状态、时间差或其他字段猜一个拣货时间。

### Incremental recompute

Execution Window 只用于发现受影响订单：

```text
changed Order
changed Transaction
changed Refund
changed Fulfillment
changed FulfillmentEvent
        ↓
 affected_order_ids
        ↓
read complete CURRENT child rows for those Orders
        ↓
recompute full lifecycle snapshot
        ↓
MERGE by order_id
```

这样既避免每次全表重算，也不会因为只读“本窗口内的子行”而丢失订单之前已经发生的生命周期里程碑。

### Current runtime boundary

该模型当前是 **SOURCE DEFINED / STATIC TESTED**。
它带有未来 Runtime 执行时使用的 dbt singular test，用于检查明显的业务时间倒序。
当前包没有 dbt Core / Spark / Iceberg Runtime Evidence，因此不能把它写成已真实运行通过。
