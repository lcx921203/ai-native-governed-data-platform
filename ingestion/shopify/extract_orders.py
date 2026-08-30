"""Shopify Admin GraphQL 订单增量采集器。

它负责真实 API 这一侧的 Raw 采集，但不直接写 Iceberg。职责拆成两段：

1. 本文件：负责 HTTP / GraphQL、增量窗口、顶层分页、嵌套分页和有限重试；
2. ``load_api_observations.py``：把完整 Order Observation 写入 Raw Iceberg。

这样做的工程好处是：网络采集和 Spark / Lakehouse 写入彼此独立，
Fixture 与 Production 最终仍然遵守同一 Raw 契约。

本项目需要独立游标分页的 Connection（连接）包括：
- Order.lineItems
- Refund.refundLineItems
- Refund.transactions
- Fulfillment.fulfillmentLineItems
- Fulfillment.events

而 ``Order.transactions``、``Order.refunds``、``Order.fulfillments`` 在当前查询中
按数组读取，不使用这里的 Cursor Loop（游标循环）。
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests


QUERY_DIR = Path(__file__).parent / "queries"
GRAPHQL_FILE = QUERY_DIR / "orders.graphql"
ORDER_LINE_ITEMS_PAGE_FILE = QUERY_DIR / "order_line_items_page.graphql"
REFUND_LINE_ITEMS_PAGE_FILE = QUERY_DIR / "refund_line_items_page.graphql"
REFUND_TRANSACTIONS_PAGE_FILE = QUERY_DIR / "refund_transactions_page.graphql"
FULFILLMENT_LINE_ITEMS_PAGE_FILE = QUERY_DIR / "fulfillment_line_items_page.graphql"
FULFILLMENT_EVENTS_PAGE_FILE = QUERY_DIR / "fulfillment_events_page.graphql"

# 这些 HTTP 状态通常是限流或临时服务异常，可以有限次数重试。
RETRYABLE_HTTP_STATUS = {429, 500, 502, 503, 504}


def read_query(path: Path) -> str:
    """读取一个独立的 ``.graphql`` 查询文件。

    工程意图：GraphQL 字段很多，单独放文件更容易做 Code Review（代码审查），
    也避免 Python 控制流程里混入一大段查询字符串。
    """
    return path.read_text(encoding="utf-8")


def build_updated_at_query(window_start: datetime, window_end: datetime) -> str:
    """构造 Shopify ``updated_at`` 半开区间 ``[start, end)`` 查询条件。"""

    # isoformat() 把 datetime 转成 ISO-8601 文本；replace() 把 +00:00 改成常见的 Z。
    return (
        f"updated_at:>='{window_start.isoformat().replace('+00:00', 'Z')}' "
        f"updated_at:<'{window_end.isoformat().replace('+00:00', 'Z')}'"
    )


def _graphql_error_is_throttled(errors: list[dict[str, Any]]) -> bool:
    """判断 GraphQL errors 中是否包含 Shopify 的 THROTTLED（限流）。"""

    for error in errors:
        # ``or {}``：如果 extensions 是 None，就用空字典，避免后面 .get() 报错。
        extensions = error.get("extensions") or {}
        if str(extensions.get("code", "")).upper() == "THROTTLED":
            return True
    return False



def _verify_api_version(response: requests.Response, requested_version: str) -> None:
    """校验 Shopify 实际执行版本，防止 API 版本被悄悄 fall-forward。

    Shopify 会在响应头 ``X-Shopify-API-Version`` 返回真正执行的版本。
    如果请求版本已经不可用，服务端可能用另一个受支持版本执行；这对 Schema Contract
    属于高风险变化，所以生产采集选择 Fail Closed，而不是静默继续。
    """
    actual = response.headers.get("X-Shopify-API-Version")
    if actual and actual != requested_version:
        raise RuntimeError(
            "Shopify API 版本不一致："
            f"requested={requested_version}, actual={actual}；"
            "停止 Structured ingestion，先检查版本升级与字段契约。"
        )


def _proactive_throttle_delay(
    payload: dict[str, Any],
    *,
    reserve_points: float,
) -> float:
    """根据 GraphQL ``extensions.cost.throttleStatus`` 计算主动等待时间。

    当前请求已经成功后，Shopify 会告诉我们：
    - requestedQueryCost：这类 Query 的请求成本；
    - currentlyAvailable：桶里当前还剩多少点；
    - restoreRate：每秒恢复多少点。

    这里把“当前请求的 requested cost + reserve”当作下一次相似请求的预算参考。
    如果余额不够，就在下一次 HTTP 前主动等待，减少撞到 THROTTLED 再被动重试的概率。
    """
    cost = (payload.get("extensions") or {}).get("cost") or {}
    throttle = cost.get("throttleStatus") or {}

    try:
        requested = float(cost.get("requestedQueryCost") or 0.0)
        available = float(throttle.get("currentlyAvailable") or 0.0)
        restore_rate = float(throttle.get("restoreRate") or 0.0)
    except (TypeError, ValueError):
        # extensions.cost 属于运行时调度信息；格式异常时不猜测 sleep，但主数据仍按响应正常处理。
        return 0.0

    if requested <= 0 or restore_rate <= 0:
        return 0.0

    required = requested + max(0.0, reserve_points)
    shortage = required - available
    return max(0.0, shortage / restore_rate)


def graphql_request(
    query: str,
    variables: dict[str, Any],
    *,
    api_version: str | None = None,
    timeout_seconds: int = 60,
    max_retries: int = 5,
    backoff_base_seconds: float = 1.0,
    throttle_reserve_points: float = 20.0,
    enforce_api_version: bool = True,
) -> dict[str, Any]:
    """执行一次 Shopify Admin GraphQL 请求，并做有限重试。

    Python 语法：参数列表里的 ``*`` 表示后面的参数必须写成关键字形式，
    例如 ``timeout_seconds=60``，这样调用时更不容易把多个数字参数传错位置。

    这里检查两层错误：
    1. HTTP 层：429 / 5xx 等；
    2. GraphQL 层：HTTP 200 但响应 JSON 里的 ``errors`` 不为空。

    重试必须有上限。超过预算后让任务失败，再交给 Dagster 做运行恢复，
    而不是在采集代码里无限循环。
    """

    # 安全边界：真实店铺域名和 Token 只从运行环境读取，不写进源码。
    shop_domain = os.environ["SHOPIFY_SHOP_DOMAIN"]
    token = os.environ["SHOPIFY_ADMIN_ACCESS_TOKEN"]
    resolved_api_version = api_version or os.getenv("SHOPIFY_API_VERSION", "2026-07")
    url = f"https://{shop_domain}/admin/api/{resolved_api_version}/graphql.json"

    # range(max_retries + 1)：如果 max_retries=5，总尝试次数最多是 6 次（首次 + 5 次重试）。
    for attempt in range(max_retries + 1):
        response = requests.post(
            url,
            headers={
                "X-Shopify-Access-Token": token,
                "Content-Type": "application/json",
            },
            # GraphQL 通常把查询文本和变量一起放在 JSON Body 中。
            json={"query": query, "variables": variables},
            timeout=timeout_seconds,
        )

        if enforce_api_version:
            _verify_api_version(response, resolved_api_version)

        # 429 / 部分 5xx：优先读取服务器给的 Retry-After；没有时做指数退避。
        if response.status_code in RETRYABLE_HTTP_STATUS and attempt < max_retries:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                delay = float(retry_after)
            else:
                # 2 ** attempt：1、2、4、8...，避免高频重试继续压垮服务。
                delay = backoff_base_seconds * (2 ** attempt)
            time.sleep(delay)
            continue

        # raise_for_status()：4xx / 5xx 会抛异常；正常 2xx 才继续解析 JSON。
        response.raise_for_status()
        payload = response.json()
        errors = payload.get("errors") or []

        # GraphQL 可能 HTTP=200，但业务层仍返回 THROTTLED。
        if errors and _graphql_error_is_throttled(errors) and attempt < max_retries:
            time.sleep(backoff_base_seconds * (2 ** attempt))
            continue

        if errors:
            raise RuntimeError(
                f"Shopify GraphQL 返回错误: {json.dumps(errors, ensure_ascii=False)}"
            )

        data = payload.get("data")
        if data is None:
            raise RuntimeError("Shopify GraphQL 响应缺少 data")

        # 主动限流：不是等到 THROTTLED 才 sleep，而是利用响应中的实时预算提前节流。
        proactive_delay = _proactive_throttle_delay(
            payload,
            reserve_points=throttle_reserve_points,
        )
        if proactive_delay > 0:
            time.sleep(proactive_delay)

        return data

    raise RuntimeError("Shopify GraphQL 请求已耗尽重试次数")


def append_connection_pages(
    connection: dict[str, Any],
    *,
    fetch_page: Callable[[str], dict[str, Any]],
) -> dict[str, Any]:
    """把一个 Connection 剩余的所有页面继续追加到当前结果中。

    先记住三个词：
    - ``first``：每次最多取多少条，不是游标；
    - ``after``：从哪个游标后面继续；
    - ``endCursor``：Shopify 返回的“本页最后位置”。

    Python 语法：``Callable[[str], dict[str, Any]]`` 表示 ``fetch_page`` 是一个函数：
    输入一个字符串游标，返回一个字典形式的 Connection。
    """

    # list(...) / dict(...) 复制一份容器，后续合并时不直接复用原引用。
    nodes = list(connection.get("nodes") or [])
    page_info = dict(connection.get("pageInfo") or {})

    # while：只要 Shopify 说还有下一页，就持续取下一页。
    while page_info.get("hasNextPage"):
        cursor = page_info.get("endCursor")
        if not cursor:
            # 数据契约不一致时立即失败，不能在不知道位置的情况下继续翻页。
            raise RuntimeError("Connection 的 hasNextPage=true，但 endCursor 为空")

        next_connection = fetch_page(str(cursor))
        # extend() 一次把下一页的多条 node 追加进列表；append() 则只追加一个对象。
        nodes.extend(next_connection.get("nodes") or [])
        page_info = dict(next_connection.get("pageInfo") or {})

    connection["nodes"] = nodes
    connection["pageInfo"] = page_info
    return connection


def _request_nested_connection(
    *,
    query_file: Path,
    node_id: str,
    connection_name: str,
    page_size: int,
    after: str,
    request_options: dict[str, Any],
) -> dict[str, Any]:
    """根据 Shopify Node ID 再请求某个嵌套 Connection 的一页。"""

    data = graphql_request(
        read_query(query_file),
        {"id": node_id, "first": page_size, "after": after},
        # Python 语法：**dict 会把字典展开成关键字参数。
        **request_options,
    )
    node = data.get("node")
    if not node:
        raise RuntimeError(f"翻页 {connection_name} 时找不到 Shopify Node: {node_id}")

    connection = node.get(connection_name)
    if connection is None:
        raise RuntimeError(
            f"Shopify Node {node_id} 没有返回 Connection {connection_name}"
        )
    return connection


def complete_nested_pagination(
    order: dict[str, Any],
    *,
    nested_page_size: int,
    request_options: dict[str, Any],
) -> dict[str, Any]:
    """补齐一条 Order Observation 中所有需要游标分页的嵌套 Connection。"""

    # 1) Order → lineItems：先补齐订单商品这个独立 Connection。
    line_items = order.get("lineItems")
    if line_items:
        append_connection_pages(
            line_items,
            # lambda 是一个匿名小函数：append_connection_pages 每拿到一个 after，
            # 就调用它去请求 Order.lineItems 的下一页。
            fetch_page=lambda after: _request_nested_connection(
                query_file=ORDER_LINE_ITEMS_PAGE_FILE,
                node_id=order["id"],
                connection_name="lineItems",
                page_size=nested_page_size,
                after=after,
                request_options=request_options,
            ),
        )

    # 2) Order → refunds[] → refundLineItems / transactions：逐个 Refund 补自己的子 Connection。
    # ``order.get("refunds") or []``：refunds 缺失或为 None 时，当成空列表安全遍历。
    for refund in order.get("refunds") or []:
        refund_id = refund["id"]

        refund_line_items = refund.get("refundLineItems")
        if refund_line_items:
            append_connection_pages(
                refund_line_items,
                # ``refund_id=refund_id`` 把当前循环里的 ID 固定进 lambda，
                # 避免 lambda 真正执行时拿到后续循环的新值。
                fetch_page=lambda after, refund_id=refund_id: _request_nested_connection(
                    query_file=REFUND_LINE_ITEMS_PAGE_FILE,
                    node_id=refund_id,
                    connection_name="refundLineItems",
                    page_size=nested_page_size,
                    after=after,
                    request_options=request_options,
                ),
            )

        refund_transactions = refund.get("transactions")
        if refund_transactions:
            append_connection_pages(
                refund_transactions,
                fetch_page=lambda after, refund_id=refund_id: _request_nested_connection(
                    query_file=REFUND_TRANSACTIONS_PAGE_FILE,
                    node_id=refund_id,
                    connection_name="transactions",
                    page_size=nested_page_size,
                    after=after,
                    request_options=request_options,
                ),
            )

    # 3) Order → fulfillments[] → fulfillmentLineItems / events：逐个 Fulfillment 补自己的子 Connection。
    for fulfillment in order.get("fulfillments") or []:
        fulfillment_id = fulfillment["id"]

        fulfillment_line_items = fulfillment.get("fulfillmentLineItems")
        if fulfillment_line_items:
            append_connection_pages(
                fulfillment_line_items,
                fetch_page=lambda after, fulfillment_id=fulfillment_id: _request_nested_connection(
                    query_file=FULFILLMENT_LINE_ITEMS_PAGE_FILE,
                    node_id=fulfillment_id,
                    connection_name="fulfillmentLineItems",
                    page_size=nested_page_size,
                    after=after,
                    request_options=request_options,
                ),
            )

        events = fulfillment.get("events")
        if events:
            append_connection_pages(
                events,
                fetch_page=lambda after, fulfillment_id=fulfillment_id: _request_nested_connection(
                    query_file=FULFILLMENT_EVENTS_PAGE_FILE,
                    node_id=fulfillment_id,
                    connection_name="events",
                    page_size=nested_page_size,
                    after=after,
                    request_options=request_options,
                ),
            )

    return order


def extract_orders_in_window(
    window_start: datetime,
    window_end: datetime,
    *,
    page_size: int = 100,
    nested_page_size: int = 100,
    api_version: str | None = None,
    timeout_seconds: int = 60,
    max_retries: int = 5,
    backoff_base_seconds: float = 1.0,
    throttle_reserve_points: float = 20.0,
    enforce_api_version: bool = True,
    nested_pagination: bool = True,
) -> list[dict[str, Any]]:
    """采集已经解析好的 ``[window_start, window_end)`` 窗口内所有更新订单。"""

    query = read_query(GRAPHQL_FILE)
    request_options = {
        "api_version": api_version,
        "timeout_seconds": timeout_seconds,
        "max_retries": max_retries,
        "backoff_base_seconds": backoff_base_seconds,
        "throttle_reserve_points": throttle_reserve_points,
        "enforce_api_version": enforce_api_version,
    }

    # 第一次请求 after=None，表示从当前查询结果的开头开始。
    after: str | None = None
    all_orders: list[dict[str, Any]] = []

    while True:
        variables = {
            # first = 每一页最多返回多少个 Order，不是 cursor。
            "first": page_size,
            # after = 上一页的 endCursor；第一次为 None。
            "after": after,
            # nestedFirst = 主查询里每个嵌套 Connection 首屏最多取多少条。
            "nestedFirst": nested_page_size,
            "query": build_updated_at_query(window_start, window_end),
        }

        data = graphql_request(query, variables, **request_options)
        connection = data["orders"]

        for order in connection.get("nodes") or []:
            if nested_pagination:
                complete_nested_pagination(
                    order,
                    nested_page_size=nested_page_size,
                    request_options=request_options,
                )
            all_orders.append(order)

        page_info = connection["pageInfo"]
        if not page_info["hasNextPage"]:
            break

        # Shopify 返回本页最后一条 Order 的位置；下一次从这个位置之后继续。
        after = page_info["endCursor"]

    return all_orders


def extract_orders(
    last_watermark: datetime,
    current_watermark: datetime,
    lookback_minutes: int = 5,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """便捷入口：把上次水位向前扩 Lookback，再调用真实窗口采集。"""

    # timedelta(minutes=5) 表示 5 分钟时间差。
    # 这里宁可重复读，也优先避免源端延迟或边界更新造成漏数。
    effective_start = last_watermark - timedelta(minutes=lookback_minutes)
    return extract_orders_in_window(effective_start, current_watermark, **kwargs)


if __name__ == "__main__":
    # Python 语法：只有直接运行这个文件时才进入这里；被 Dagster import 时不会执行。
    # 这是本地手工 smoke test（冒烟测试），正式运行由 Dagster 提供窗口。
    now = datetime.now(timezone.utc)
    last = now - timedelta(hours=1)
    rows = extract_orders(last, now)
    print(f"采集订单数: {len(rows)}")
