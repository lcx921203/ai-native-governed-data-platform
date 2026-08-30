{#
  Singular dbt Test：生命周期里程碑时间顺序保护。

  输入：order_lifecycle_snapshot（1 order_id 一行）。
  输出：只返回违反时间不变量的“错误行”；dbt singular test 约定返回 0 行才 PASS。
  业务不变量：授权/收款/退款/履约/运输/送达/取消/关闭不能早于 order_time，且 delivered 不能早于 in_transit。
  工程边界：这个 Test 能证明模型输出满足时间顺序契约；没有真实 dbt build/test 运行日志时仍不能写 Runtime PASS。
#}

-- Accumulating Snapshot 业务时间顺序保护。
-- dbt singular test：返回 0 行表示 PASS；任何返回行都需要人工检查源事实或建模逻辑。

select *
from {{ ref('order_lifecycle_snapshot') }}
where
       (first_authorized_at is not null and first_authorized_at < order_time)
    or (first_paid_at is not null and first_paid_at < order_time)
    or (first_refund_at is not null and first_refund_at < order_time)
    or (first_fulfillment_at is not null and first_fulfillment_at < order_time)
    or (first_in_transit_at is not null and first_in_transit_at < order_time)
    or (first_delivered_at is not null and first_delivered_at < order_time)
    or (
        first_in_transit_at is not null
        and first_delivered_at is not null
        and first_delivered_at < first_in_transit_at
    )
    or (cancelled_at is not null and cancelled_at < order_time)
    or (closed_at is not null and closed_at < order_time)
