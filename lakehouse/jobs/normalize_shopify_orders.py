"""Shopify Raw JSON → Structured Source Iceberg 标准化任务。

这份源码实现五条核心工程规则：
1. Raw 保存 API Observation（观察记录）；这里绝不反过来修改或去重 Raw；
2. Shopify 每个 1:N Collection（集合）独立 explode，避免多个数组同时展开产生笛卡尔积；
3. Structured Source（结构化源数据）粒度 = Business Key × distinct business-content version；
4. record_hash 只计算业务内容，不包含 extracted_at、batch_id、order_updated_at 等观察/排序元数据；
5. Business Key + record_hash 通过 Iceberg MERGE 幂等写入。

当前覆盖 Order、LineItem、DiscountAllocation、OrderTransaction、Refund、
RefundLineItem、RefundTransaction bridge、Fulfillment、FulfillmentLineItem、
FulfillmentEvent。专业对象名保留 Shopify / 工程英文，解释与注释优先使用中文。
"""

import argparse

from pyspark.sql import SparkSession, Window, functions as F
from pyspark.sql.types import (
    ArrayType,
    BooleanType,
    DoubleType,
    LongType,
    StringType,
    StructField,
    StructType,
)


RAW_TABLE = "polaris.raw.raw_shopify_order_payload"


# -----------------------------------------------------------------------------
# Shopify JSON 结构定义（Schema）
# -----------------------------------------------------------------------------

money_schema = StructType([
    StructField("amount", StringType()),
    StructField("currencyCode", StringType()),
])

money_set_schema = StructType([
    StructField("shopMoney", money_schema),
])

id_ref_schema = StructType([
    StructField("id", StringType()),
])

node_id_connection_schema = StructType([
    StructField("nodes", ArrayType(id_ref_schema)),
])


discount_application_schema = StructType([
    StructField("__typename", StringType()),
    StructField("index", LongType()),
    StructField("allocationMethod", StringType()),
    StructField("targetSelection", StringType()),
    StructField("targetType", StringType()),
])

discount_allocation_schema = StructType([
    StructField("allocatedAmountSet", money_set_schema),
    StructField("discountApplication", discount_application_schema),
])

line_item_schema = StructType([
    StructField("id", StringType()),
    StructField("sku", StringType()),
    StructField("title", StringType()),
    StructField("quantity", LongType()),
    StructField("currentQuantity", LongType()),
    StructField("refundableQuantity", LongType()),
    StructField("unfulfilledQuantity", LongType()),
    StructField("product", id_ref_schema),
    StructField("variant", id_ref_schema),
    StructField("originalUnitPriceSet", money_set_schema),
    StructField("originalTotalSet", money_set_schema),
    StructField("totalDiscountSet", money_set_schema),
    StructField("discountAllocations", ArrayType(discount_allocation_schema)),
])

transaction_schema = StructType([
    StructField("id", StringType()),
    StructField("kind", StringType()),
    StructField("status", StringType()),
    StructField("gateway", StringType()),
    StructField("createdAt", StringType()),
    StructField("processedAt", StringType()),
    StructField("test", BooleanType()),
    StructField("errorCode", StringType()),
    StructField("parentTransaction", id_ref_schema),
    StructField("amountSet", money_set_schema),
])

refund_line_item_schema = StructType([
    StructField("id", StringType()),
    StructField("quantity", LongType()),
    StructField("restocked", BooleanType()),
    StructField("restockType", StringType()),
    StructField("lineItem", id_ref_schema),
    StructField("subtotalSet", money_set_schema),
    StructField("totalTaxSet", money_set_schema),
])

refund_schema = StructType([
    StructField("id", StringType()),
    StructField("createdAt", StringType()),
    StructField("processedAt", StringType()),
    StructField("updatedAt", StringType()),
    StructField("totalRefundedSet", money_set_schema),
    StructField(
        "refundLineItems",
        StructType([StructField("nodes", ArrayType(refund_line_item_schema))]),
    ),
    # Refund（退款）.transactions 在 GraphQL 中按 Connection 读取，这里保存其中的 Transaction ID。
    StructField("transactions", node_id_connection_schema),
])

fulfillment_line_item_schema = StructType([
    StructField("id", StringType()),
    StructField("quantity", LongType()),
    StructField("lineItem", id_ref_schema),
])

fulfillment_event_schema = StructType([
    StructField("id", StringType()),
    StructField("status", StringType()),
    StructField("createdAt", StringType()),
    StructField("happenedAt", StringType()),
    StructField("estimatedDeliveryAt", StringType()),
    StructField("message", StringType()),
    StructField("city", StringType()),
    StructField("province", StringType()),
    StructField("country", StringType()),
    StructField("zip", StringType()),
    StructField("latitude", DoubleType()),
    StructField("longitude", DoubleType()),
])

fulfillment_schema = StructType([
    StructField("id", StringType()),
    StructField("name", StringType()),
    StructField("status", StringType()),
    StructField("displayStatus", StringType()),
    StructField("createdAt", StringType()),
    StructField("updatedAt", StringType()),
    StructField("inTransitAt", StringType()),
    StructField("deliveredAt", StringType()),
    StructField("estimatedDeliveryAt", StringType()),
    StructField("totalQuantity", LongType()),
    StructField("requiresShipping", BooleanType()),
    StructField(
        "location",
        StructType([
            StructField("id", StringType()),
            StructField("name", StringType()),
        ]),
    ),
    StructField(
        "fulfillmentLineItems",
        StructType([StructField("nodes", ArrayType(fulfillment_line_item_schema))]),
    ),
    StructField(
        "events",
        StructType([StructField("nodes", ArrayType(fulfillment_event_schema))]),
    ),
])

order_schema = StructType([
    StructField("id", StringType()),
    StructField("name", StringType()),
    StructField("createdAt", StringType()),
    StructField("processedAt", StringType()),
    StructField("updatedAt", StringType()),
    StructField("cancelledAt", StringType()),
    StructField("closedAt", StringType()),
    StructField("displayFinancialStatus", StringType()),
    StructField("displayFulfillmentStatus", StringType()),
    StructField("currencyCode", StringType()),
    StructField("customer", id_ref_schema),
    StructField("originalTotalPriceSet", money_set_schema),
    StructField("currentTotalPriceSet", money_set_schema),
    StructField("currentTotalDiscountsSet", money_set_schema),
    StructField("totalRefundedSet", money_set_schema),
    StructField(
        "lineItems",
        StructType([StructField("nodes", ArrayType(line_item_schema))]),
    ),
    StructField("transactions", ArrayType(transaction_schema)),
    StructField("refunds", ArrayType(refund_schema)),
    StructField("fulfillments", ArrayType(fulfillment_schema)),
])


# -----------------------------------------------------------------------------
# 公共辅助函数
# -----------------------------------------------------------------------------

def add_record_hash(df, columns):
    """对规范业务内容计算 record_hash，同时保留 NULL 与字段边界。

    Python / Spark：先用 struct 把字段名和值绑在一起，再转 JSON 后 SHA-256；
    不能简单字符串拼接，否则不同字段组合可能出现边界碰撞。
    """
    content_struct = F.struct(*[F.col(column).alias(column) for column in columns])
    canonical_json = F.to_json(content_struct, options={"ignoreNullFields": "false"})
    return df.withColumn("record_hash", F.sha2(canonical_json, 256))


def prepare_source_versions(df, business_keys, source_updated_column):
    """把同一批次中重复看见的相同业务版本收敛成一行。

    分区键是 Business Key + record_hash；也就是说“重复观察”不会制造新业务版本。
    Window / row_number 只负责从重复观察中选出排序最靠后的代表记录。
    """
    partition_columns = [*business_keys, "record_hash"]
    version_window = Window.partitionBy(*partition_columns)
    latest_source_window = version_window.orderBy(
        F.col(source_updated_column).desc_nulls_last(),
        F.col("extracted_at").desc_nulls_last(),
        F.col("batch_id").desc_nulls_last(),
    )
    return (
        df
        .withColumn("first_observed_at", F.min("extracted_at").over(version_window))
        .withColumn("last_observed_at", F.max("extracted_at").over(version_window))
        .withColumn("first_source_updated_at", F.min(source_updated_column).over(version_window))
        .withColumn("last_source_updated_at", F.max(source_updated_column).over(version_window))
        .withColumn(
            "_latest_batch_id",
            F.max_by(
                F.col("batch_id"),
                F.struct(F.col("extracted_at"), F.col("batch_id")),
            ).over(version_window),
        )
        .withColumn("_version_row_rank", F.row_number().over(latest_source_window))
        .filter(F.col("_version_row_rank") == 1)
        .withColumn("extracted_at", F.col("last_observed_at"))
        .withColumn("batch_id", F.col("_latest_batch_id"))
        .drop("_version_row_rank", "_latest_batch_id")
    )


def merge_source_versions(
    spark,
    df,
    target_table,
    temp_view,
    business_keys,
    source_updated_column,
):
    """按 Business Key + distinct record_hash 幂等合并一个业务版本。

    如果以后再次观察到相同内容版本，保留第一次观察时间，同时扩展最后观察时间和
    最后源更新时间。这样 A → B → A 回退时，第三次 A 会重新命中原来的 A 版本，
    不会再制造一条内容完全相同的第三个版本。
    """
    prepared = prepare_source_versions(df, business_keys, source_updated_column)
    prepared.createOrReplaceTempView(temp_view)

    key_conditions = [f"t.{key} <=> s.{key}" for key in business_keys]
    key_conditions.append("t.record_hash = s.record_hash")
    on_clause = " AND ".join(key_conditions)

    cols = prepared.columns
    insert_cols = ", ".join(cols)
    insert_values = ", ".join(f"s.{c}" for c in cols)

    def min_ts(left, right):
        """返回两个时间表达式中的较早值，并正确处理 NULL。
        
        用途：合并同一业务版本的首次观察时间。
        Spark 语义：任一侧为空时返回另一侧；两侧都有值时取 least。
        """
        return f"CASE WHEN {left} IS NULL THEN {right} WHEN {right} IS NULL THEN {left} ELSE least({left}, {right}) END"

    def max_ts(left, right):
        """返回两个时间表达式中的较晚值，并正确处理 NULL。
        
        用途：合并同一业务版本的最近观察/源更新时间。
        Spark 语义：任一侧为空时返回另一侧；两侧都有值时取 greatest。
        """
        return f"CASE WHEN {left} IS NULL THEN {right} WHEN {right} IS NULL THEN {left} ELSE greatest({left}, {right}) END"

    update_assignments = [
        f"first_observed_at = {min_ts('t.first_observed_at', 's.first_observed_at')}",
        f"last_observed_at = {max_ts('t.last_observed_at', 's.last_observed_at')}",
        f"first_source_updated_at = {min_ts('t.first_source_updated_at', 's.first_source_updated_at')}",
        f"last_source_updated_at = {max_ts('t.last_source_updated_at', 's.last_source_updated_at')}",
        "extracted_at = CASE WHEN t.last_observed_at IS NULL OR s.last_observed_at >= t.last_observed_at THEN s.last_observed_at ELSE t.last_observed_at END",
        "batch_id = CASE WHEN t.last_observed_at IS NULL OR s.last_observed_at >= t.last_observed_at THEN s.batch_id ELSE t.batch_id END",
    ]

    spark.sql(f"""
        MERGE INTO {target_table} t
        USING {temp_view} s
        ON {on_clause}
        WHEN MATCHED THEN UPDATE SET
          {', '.join(update_assignments)}
        WHEN NOT MATCHED THEN
        INSERT ({insert_cols})
        VALUES ({insert_values})
    """)


def parse_args() -> argparse.Namespace:
    """解析命令行参数，得到本次 Normalize 的可选执行窗口。
    
    输入：命令行中的 ``--window-start`` / ``--window-end``。
    输出：``argparse.Namespace``。
    工程边界：这里只解析参数，不读取 Raw、也不决定业务版本；真正的数据过滤在 ``parse_raw`` 中完成。
    """
    parser = argparse.ArgumentParser(
        description="Normalize one effective Shopify source-read window"
    )
    parser.add_argument(
        "--window-start",
        help="Effective source-read start, inclusive, ISO-8601 UTC",
    )
    parser.add_argument(
        "--window-end",
        help="Effective source-read end, exclusive, ISO-8601 UTC",
    )
    args = parser.parse_args()
    if (args.window_start is None) != (args.window_end is None):
        parser.error("--window-start and --window-end must be provided together")
    return args


def parse_raw(
    spark,
    *,
    window_start: str | None = None,
    window_end: str | None = None,
):
    """读取 Raw Shopify Observation，并把 JSON 解析成后续建模可用的结构。
    
    输入：SparkSession 与可选的半开执行窗口 ``[window_start, window_end)``。
    输出：解析后的 Spark DataFrame。
    Spark API：``from_json`` 按显式 ``order_schema`` 解析 payload；窗口只限制本次要处理的 Observation。
    工程边界：Raw 是 append-only 证据层，本方法不会在 Raw 上做业务去重或 latest 选择。
    """
    raw = spark.table(RAW_TABLE)

    if window_start is not None and window_end is not None:
        raw = raw.where(
            (F.col("order_updated_at") >= F.lit(window_start).cast("timestamp"))
            & (F.col("order_updated_at") < F.lit(window_end).cast("timestamp"))
        )

    return raw.withColumn(
        "order",
        F.from_json("payload", order_schema),
    )


# -----------------------------------------------------------------------------
# Order / Sales（订单与销售）
# -----------------------------------------------------------------------------

def build_orders(parsed):
    """构建 Order Structured Source 的独立业务版本。
    
    输入：已经解析的 Raw Observation DataFrame。
    输出：通过 ``Business Key(order_id) + record_hash`` 幂等 MERGE 到 Order Structured Source。
    数据语义：一行代表一个 distinct Order business-content version，而不是一次 API Observation。
    工程边界：``record_hash`` 不包含 extracted_at / batch_id 等观察元数据，避免重复采集制造假版本。
    """
    df = parsed.select(
        F.col("order.id").alias("order_id"),
        F.col("order.name").alias("order_name"),
        F.col("order.customer.id").alias("store_id"),
        F.to_timestamp("order.createdAt").alias("created_at"),
        F.to_timestamp("order.processedAt").alias("processed_at"),
        F.to_timestamp("order.updatedAt").alias("updated_at"),
        F.to_timestamp("order.cancelledAt").alias("cancelled_at"),
        F.to_timestamp("order.closedAt").alias("closed_at"),
        F.col("order.displayFinancialStatus").alias("financial_status"),
        F.col("order.displayFulfillmentStatus").alias("fulfillment_status"),
        F.col("order.currencyCode").alias("currency_code"),
        F.col("order.originalTotalPriceSet.shopMoney.amount")
            .cast("decimal(18,2)").alias("original_total_amount"),
        F.col("order.currentTotalPriceSet.shopMoney.amount")
            .cast("decimal(18,2)").alias("current_total_amount"),
        F.col("order.currentTotalDiscountsSet.shopMoney.amount")
            .cast("decimal(18,2)").alias("current_total_discount_amount"),
        F.col("order.totalRefundedSet.shopMoney.amount")
            .cast("decimal(18,2)").alias("total_refunded_amount"),
        "extracted_at",
        "batch_id",
    )

    return add_record_hash(
        df,
        [
            "order_id", "order_name", "store_id",
            "created_at", "processed_at", "cancelled_at", "closed_at",
            "financial_status", "fulfillment_status", "currency_code",
            "original_total_amount", "current_total_amount",
            "current_total_discount_amount", "total_refunded_amount",
        ],
    )


def build_order_items(parsed):
    """从每个 Order Observation 独立展开 LineItem，并写入 LineItem Structured Source。
    
    输入：解析后的 Order Observation。
    输出：Grain = ``line_item_id × distinct record_hash`` 的 Structured Source 版本。
    Spark API：只对 ``lineItems.nodes`` 做一次 ``explode_outer``，不与 Refund/Fulfillment 数组同时展开。
    工程边界：独立 explode 是为了避免多个 1:N 集合同时展开导致 Fanout / 笛卡尔积。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            F.to_timestamp("order.updatedAt").alias("order_updated_at"),
            "extracted_at",
            "batch_id",
            F.explode("order.lineItems.nodes").alias("item"),
        )
        .select(
            "order_id",
            F.col("item.id").alias("line_item_id"),
            F.col("item.product.id").alias("item_id"),
            F.col("item.variant.id").alias("variant_id"),
            F.col("item.sku").alias("sku"),
            F.col("item.title").alias("item_title"),
            F.col("item.quantity").alias("quantity"),
            F.col("item.currentQuantity").alias("current_quantity"),
            F.col("item.refundableQuantity").alias("refundable_quantity"),
            F.col("item.unfulfilledQuantity").alias("unfulfilled_quantity"),
            F.col("item.originalUnitPriceSet.shopMoney.amount")
                .cast("decimal(18,2)").alias("original_unit_price"),
            F.col("item.originalTotalSet.shopMoney.amount")
                .cast("decimal(18,2)").alias("original_total_amount"),
            F.col("item.totalDiscountSet.shopMoney.amount")
                .cast("decimal(18,2)").alias("source_line_discount_amount"),
            F.col("item.originalTotalSet.shopMoney.currencyCode")
                .alias("currency_code"),
            "order_updated_at",
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        [
            "line_item_id", "order_id", "item_id", "variant_id",
            "sku", "item_title", "quantity", "current_quantity",
            "refundable_quantity", "unfulfilled_quantity",
            "original_unit_price", "original_total_amount",
            "source_line_discount_amount", "currency_code",
        ],
    )


def build_discount_allocations(parsed):
    """从 LineItem 内部独立展开 DiscountAllocation。
    
    输入：解析后的 Order Observation。
    输出：以 LineItem + DiscountApplication 位置/内容为业务身份的折扣分配版本。
    数据语义：DiscountAllocation 是 LineItem 的 1:N 子事实，必须独立建 Grain。
    工程边界：当前源契约没有完整 tombstone 语义，成员“这次没出现”不能自动解释成 DELETE。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            F.to_timestamp("order.updatedAt").alias("order_updated_at"),
            "extracted_at",
            "batch_id",
            F.explode("order.lineItems.nodes").alias("item"),
        )
        .select(
            "order_id",
            "order_updated_at",
            "extracted_at",
            "batch_id",
            F.col("item.id").alias("line_item_id"),
            F.explode_outer("item.discountAllocations").alias("allocation"),
        )
        .filter(F.col("allocation").isNotNull())
        .select(
            "order_id",
            "line_item_id",
            F.col("allocation.discountApplication.index")
                .cast("int").alias("discount_application_index"),
            F.col("allocation.discountApplication.__typename")
                .alias("discount_application_type"),
            F.col("allocation.discountApplication.allocationMethod")
                .alias("allocation_method"),
            F.col("allocation.discountApplication.targetSelection")
                .alias("target_selection"),
            F.col("allocation.discountApplication.targetType")
                .alias("target_type"),
            F.col("allocation.allocatedAmountSet.shopMoney.amount")
                .cast("decimal(18,2)").alias("allocated_amount"),
            F.col("allocation.allocatedAmountSet.shopMoney.currencyCode")
                .alias("currency_code"),
            "order_updated_at",
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        [
            "order_id", "line_item_id", "discount_application_index",
            "discount_application_type", "allocation_method",
            "target_selection", "target_type", "allocated_amount",
            "currency_code",
        ],
    )


# -----------------------------------------------------------------------------
# Payment（支付）
# -----------------------------------------------------------------------------

def build_transactions(parsed):
    """展开 OrderTransaction，并形成交易 Structured Source 版本。
    
    输入：Order Observation 中的 transactions 数组。
    输出：Grain = ``transaction_id × distinct record_hash``。
    业务语义：kind/status/processed_at 等字段描述支付活动，不等价于 Order 当前财务状态。
    工程边界：支付成功与授权等业务含义留给下游模型判断，本层只保存源事实。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            F.to_timestamp("order.updatedAt").alias("order_updated_at"),
            "extracted_at",
            "batch_id",
            F.explode_outer("order.transactions").alias("transaction"),
        )
        .filter(F.col("transaction").isNotNull())
        .select(
            F.col("transaction.id").alias("transaction_id"),
            "order_id",
            F.col("transaction.parentTransaction.id")
                .alias("parent_transaction_id"),
            F.col("transaction.kind").alias("kind"),
            F.col("transaction.status").alias("status"),
            F.col("transaction.gateway").alias("gateway"),
            F.to_timestamp("transaction.createdAt").alias("created_at"),
            F.to_timestamp("transaction.processedAt").alias("processed_at"),
            F.col("transaction.amountSet.shopMoney.amount")
                .cast("decimal(18,2)").alias("amount"),
            F.col("transaction.amountSet.shopMoney.currencyCode")
                .alias("currency_code"),
            F.col("transaction.test").alias("is_test"),
            F.col("transaction.errorCode").alias("error_code"),
            "order_updated_at",
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        [
            "transaction_id", "order_id", "parent_transaction_id",
            "kind", "status", "gateway", "created_at", "processed_at",
            "amount", "currency_code", "is_test", "error_code",
        ],
    )


# -----------------------------------------------------------------------------
# Refund（退款）
# -----------------------------------------------------------------------------

def build_refunds(parsed):
    """展开 Refund 对象并写入 Refund Structured Source。
    
    输入：Order Observation 中 refunds 数组。
    输出：Grain = ``refund_id × distinct record_hash``。
    时间语义：Refund 自己的 created/processed/updated clock 被保留，不能用订单时间替代退款发生时间。
    工程边界：这里只规范化退款对象，不在这里计算销售冲销指标。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            "extracted_at",
            "batch_id",
            F.explode_outer("order.refunds").alias("refund"),
        )
        .filter(F.col("refund").isNotNull())
        .select(
            F.col("refund.id").alias("refund_id"),
            "order_id",
            F.to_timestamp("refund.createdAt").alias("created_at"),
            F.to_timestamp("refund.processedAt").alias("processed_at"),
            F.to_timestamp("refund.updatedAt").alias("updated_at"),
            F.col("refund.totalRefundedSet.shopMoney.amount")
                .cast("decimal(18,2)").alias("total_refunded_amount"),
            F.col("refund.totalRefundedSet.shopMoney.currencyCode")
                .alias("currency_code"),
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        [
            "refund_id", "order_id", "created_at", "processed_at",
            "total_refunded_amount", "currency_code",
        ],
    )


def build_refund_items(parsed):
    """独立展开 RefundLineItem，并建立退款商品级 Structured Source。
    
    输入：Refund.refundLineItems.nodes。
    输出：Grain = ``refund_line_item_id × distinct record_hash``。
    数据语义：退款对象与退款商品是两个 Grain，后续通过 refund_id / line_item_id 关联。
    工程边界：避免把 Refund 与其他订单子集合一次性 flatten 后再聚合。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            "extracted_at",
            "batch_id",
            F.explode_outer("order.refunds").alias("refund"),
        )
        .filter(F.col("refund").isNotNull())
        .select(
            "order_id",
            "extracted_at",
            "batch_id",
            F.col("refund.id").alias("refund_id"),
            F.to_timestamp("refund.updatedAt").alias("refund_updated_at"),
            F.explode_outer("refund.refundLineItems.nodes")
                .alias("refund_item"),
        )
        .filter(F.col("refund_item").isNotNull())
        .select(
            F.col("refund_item.id").alias("refund_line_item_id"),
            "refund_id",
            "order_id",
            F.col("refund_item.lineItem.id").alias("line_item_id"),
            F.col("refund_item.quantity").alias("quantity"),
            F.col("refund_item.restocked").alias("restocked"),
            F.col("refund_item.restockType").alias("restock_type"),
            F.col("refund_item.subtotalSet.shopMoney.amount")
                .cast("decimal(18,2)").alias("subtotal_amount"),
            F.col("refund_item.totalTaxSet.shopMoney.amount")
                .cast("decimal(18,2)").alias("tax_amount"),
            F.col("refund_item.subtotalSet.shopMoney.currencyCode")
                .alias("currency_code"),
            "refund_updated_at",
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        [
            "refund_line_item_id", "refund_id", "order_id", "line_item_id",
            "quantity", "restocked", "restock_type", "subtotal_amount",
            "tax_amount", "currency_code",
        ],
    )


def build_refund_transactions(parsed):
    """构造 Refund ↔ OrderTransaction 关系桥表。

    这张桥表不是指标口径的 owner（权威来源）。它只保存 Refund 与真实金融退款
    Transaction 的关系，避免为了关联支付事实而把 RefundLineItem 直接复制/拼到支付事实里。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            "extracted_at",
            "batch_id",
            F.explode_outer("order.refunds").alias("refund"),
        )
        .filter(F.col("refund").isNotNull())
        .select(
            "order_id",
            "extracted_at",
            "batch_id",
            F.col("refund.id").alias("refund_id"),
            F.to_timestamp("refund.updatedAt").alias("refund_updated_at"),
            F.explode_outer("refund.transactions.nodes")
                .alias("refund_transaction"),
        )
        .filter(F.col("refund_transaction").isNotNull())
        .select(
            "refund_id",
            "order_id",
            F.col("refund_transaction.id").alias("transaction_id"),
            "refund_updated_at",
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        ["refund_id", "order_id", "transaction_id"],
    )


# -----------------------------------------------------------------------------
# Fulfillment（履约）
# -----------------------------------------------------------------------------

def build_fulfillments(parsed):
    """展开 Fulfillment，并保存履约对象的业务版本。
    
    输入：Order.fulfillments。
    输出：Grain = ``fulfillment_id × distinct record_hash``。
    时间语义：created_at / in_transit_at / delivered_at 等都是生命周期业务时间，后续累计快照会使用。
    工程边界：源端没有可靠 picked_at 时不会在这里推导或伪造拣货时间。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            "extracted_at",
            "batch_id",
            F.explode_outer("order.fulfillments").alias("fulfillment"),
        )
        .filter(F.col("fulfillment").isNotNull())
        .select(
            F.col("fulfillment.id").alias("fulfillment_id"),
            "order_id",
            F.col("fulfillment.name").alias("fulfillment_name"),
            F.col("fulfillment.status").alias("fulfillment_status"),
            F.col("fulfillment.displayStatus").alias("display_status"),
            F.to_timestamp("fulfillment.createdAt")
                .alias("fulfillment_created_at"),
            F.to_timestamp("fulfillment.updatedAt")
                .alias("fulfillment_updated_at"),
            F.to_timestamp("fulfillment.inTransitAt").alias("in_transit_at"),
            F.to_timestamp("fulfillment.deliveredAt").alias("delivered_at"),
            F.to_timestamp("fulfillment.estimatedDeliveryAt")
                .alias("estimated_delivery_at"),
            F.col("fulfillment.location.id").alias("fulfillment_location_id"),
            F.col("fulfillment.location.name")
                .alias("fulfillment_location_name"),
            F.col("fulfillment.totalQuantity").alias("total_quantity"),
            F.col("fulfillment.requiresShipping").alias("requires_shipping"),
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        [
            "fulfillment_id", "order_id", "fulfillment_name",
            "fulfillment_status", "display_status", "fulfillment_created_at",
            "in_transit_at", "delivered_at", "estimated_delivery_at",
            "fulfillment_location_id", "fulfillment_location_name",
            "total_quantity", "requires_shipping",
        ],
    )


def build_fulfillment_items(parsed):
    """独立展开 FulfillmentLineItem。
    
    输入：Fulfillment.fulfillmentLineItems.nodes。
    输出：Grain = ``fulfillment_line_item_id × distinct record_hash``。
    关系语义：保留 fulfillment_id 与原 line_item_id，使履约数量能安全回连订单商品。
    工程边界：不与 FulfillmentEvent 同时 explode，避免 1:N × 1:N Fanout。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            "extracted_at",
            "batch_id",
            F.explode_outer("order.fulfillments").alias("fulfillment"),
        )
        .filter(F.col("fulfillment").isNotNull())
        .select(
            "order_id",
            "extracted_at",
            "batch_id",
            F.col("fulfillment.id").alias("fulfillment_id"),
            F.to_timestamp("fulfillment.updatedAt")
                .alias("parent_fulfillment_updated_at"),
            F.explode_outer("fulfillment.fulfillmentLineItems.nodes")
                .alias("fulfillment_item"),
        )
        .filter(F.col("fulfillment_item").isNotNull())
        .select(
            F.col("fulfillment_item.id")
                .alias("fulfillment_line_item_id"),
            "fulfillment_id",
            "order_id",
            F.col("fulfillment_item.lineItem.id").alias("line_item_id"),
            F.col("fulfillment_item.quantity").alias("fulfilled_quantity"),
            "parent_fulfillment_updated_at",
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        [
            "fulfillment_line_item_id", "fulfillment_id", "order_id",
            "line_item_id", "fulfilled_quantity",
        ],
    )


def build_fulfillment_events(parsed):
    """独立展开 FulfillmentEvent，并保存事件级业务版本。
    
    输入：Fulfillment.events.nodes。
    输出：Grain = ``fulfillment_event_id × distinct record_hash``。
    时间语义：优先保留 happened_at 作为事件业务时间，同时保留 created_at 等源时钟。
    工程边界：事件序列是历史事实，不应被 Current State 覆盖成单行状态。
    """
    df = (
        parsed
        .select(
            F.col("order.id").alias("order_id"),
            "extracted_at",
            "batch_id",
            F.explode_outer("order.fulfillments").alias("fulfillment"),
        )
        .filter(F.col("fulfillment").isNotNull())
        .select(
            "order_id",
            "extracted_at",
            "batch_id",
            F.col("fulfillment.id").alias("fulfillment_id"),
            F.to_timestamp("fulfillment.updatedAt")
                .alias("parent_fulfillment_updated_at"),
            F.explode_outer("fulfillment.events.nodes").alias("event"),
        )
        .filter(F.col("event").isNotNull())
        .select(
            F.col("event.id").alias("fulfillment_event_id"),
            "fulfillment_id",
            "order_id",
            F.col("event.status").alias("event_status"),
            F.to_timestamp("event.createdAt").alias("event_created_at"),
            # happenedAt 是业务事件发生时间；createdAt 更接近这条事件记录被创建的时间，两者不要混用。
            F.to_timestamp("event.happenedAt").alias("event_time"),
            F.to_timestamp("event.estimatedDeliveryAt")
                .alias("estimated_delivery_at"),
            F.col("event.message").alias("event_message"),
            F.col("event.city").alias("city"),
            F.col("event.province").alias("province"),
            F.col("event.country").alias("country"),
            F.col("event.zip").alias("zip"),
            F.col("event.latitude").cast("double").alias("latitude"),
            F.col("event.longitude").cast("double").alias("longitude"),
            "parent_fulfillment_updated_at",
            "extracted_at",
            "batch_id",
        )
    )

    return add_record_hash(
        df,
        [
            "fulfillment_event_id", "fulfillment_id", "order_id",
            "event_status", "event_created_at", "event_time",
            "estimated_delivery_at", "event_message", "city", "province",
            "country", "zip", "latitude", "longitude",
        ],
    )


# -----------------------------------------------------------------------------
# 作业入口
# -----------------------------------------------------------------------------

def main():
    """串联 Shopify Raw → Structured Source 的全部 Normalize 构建步骤。
    
    输入：命令行执行窗口与 Spark/Polaris/Iceberg 运行环境。
    输出：更新 Order 及各独立子 Grain 的 Structured Source 表。
    工程边界：源码定义了完整执行流程，但是否真实运行成功必须由 Runtime Evidence 证明，不能由代码存在推断。
    """
    args = parse_args()
    spark = (
        SparkSession.builder
        .appName("normalize-shopify-orders")
        .getOrCreate()
    )

    parsed = parse_raw(
        spark,
        window_start=args.window_start,
        window_end=args.window_end,
    )

    datasets = [
        (
            build_orders(parsed),
            "polaris.source.shopify_order",
            "batch_orders",
            ["order_id"],
            "updated_at",
        ),
        (
            build_order_items(parsed),
            "polaris.source.shopify_order_item",
            "batch_order_items",
            ["line_item_id"],
            "order_updated_at",
        ),
        (
            build_discount_allocations(parsed),
            "polaris.source.shopify_line_item_discount_allocation",
            "batch_discount_allocations",
            ["line_item_id", "discount_application_index"],
            "order_updated_at",
        ),
        (
            build_transactions(parsed),
            "polaris.source.shopify_transaction",
            "batch_transactions",
            ["transaction_id"],
            "order_updated_at",
        ),
        (
            build_refunds(parsed),
            "polaris.source.shopify_refund",
            "batch_refunds",
            ["refund_id"],
            "updated_at",
        ),
        (
            build_refund_items(parsed),
            "polaris.source.shopify_refund_item",
            "batch_refund_items",
            ["refund_line_item_id"],
            "refund_updated_at",
        ),
        (
            build_refund_transactions(parsed),
            "polaris.source.shopify_refund_transaction",
            "batch_refund_transactions",
            ["refund_id", "transaction_id"],
            "refund_updated_at",
        ),
        (
            build_fulfillments(parsed),
            "polaris.source.shopify_fulfillment",
            "batch_fulfillments",
            ["fulfillment_id"],
            "fulfillment_updated_at",
        ),
        (
            build_fulfillment_items(parsed),
            "polaris.source.shopify_fulfillment_item",
            "batch_fulfillment_items",
            ["fulfillment_line_item_id"],
            "parent_fulfillment_updated_at",
        ),
        (
            build_fulfillment_events(parsed),
            "polaris.source.shopify_fulfillment_event",
            "batch_fulfillment_events",
            ["fulfillment_event_id"],
            "parent_fulfillment_updated_at",
        ),
    ]

    for df, target, view, keys, source_updated_column in datasets:
        merge_source_versions(
            spark=spark,
            df=df,
            target_table=target,
            temp_view=view,
            business_keys=keys,
            source_updated_column=source_updated_column,
        )

    print(f"normalize complete: {len(datasets)} structured-source datasets")


if __name__ == "__main__":
    main()