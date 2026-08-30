"""行为事件 DataStream 的核心函数：校验、时间、State 去重与窗口聚合。"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Iterable

from pyflink.common import Time
from pyflink.common.watermark_strategy import TimestampAssigner
from pyflink.datastream.functions import (
    AggregateFunction,
    KeyedProcessFunction,
    ProcessFunction,
    ProcessWindowFunction,
    RuntimeContext,
)
from pyflink.datastream.state import StateTtlConfig, ValueStateDescriptor

from ingestion.behavior.flink.types import (
    BEHAVIOR_EVENT_TYPE,
    EVENT_ID,
    EVENT_TIME_MS,
)


def _parse_iso_millis(value: str) -> int:
    """把 ISO-8601 时间转成 UTC epoch milliseconds。"""
    # Python 3.11+ 的 fromisoformat 能识别 +00:00；先把 Z 归一成显式 UTC offset。
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("时间必须带时区")
    return int(parsed.astimezone(timezone.utc).timestamp() * 1000)


class ParseBehaviorEvent(ProcessFunction):
    """JSON 解析 + 业务必填字段校验。

    主输出：合法的 BehaviorEvent tuple。
    Side Output：由调用方传入 invalid_tag；保存 raw JSON + 错误原因。

    Python Side Output 语法：``yield output_tag, value``，而普通主流直接 ``yield value``。
    """

    def __init__(self, invalid_tag):
        """保存 invalid Side Output 的 OutputTag。
        
        输入：调用方创建的 invalid_tag。
        输出：初始化后的 ProcessFunction。
        Flink API：Side Output 让契约不合法数据与合法主流分开处理，而不是混进正常事件。
        """
        self.invalid_tag = invalid_tag

    def process_element(self, raw_json: str, ctx: ProcessFunction.Context):
        """解析单条 JSON 行为事件并做最小源契约校验。
        
        输入：Kafka 中的一条 raw JSON。
        主输出：规范化 BehaviorEvent tuple；异常输出：``invalid_tag`` 上的 raw JSON + 原因。
        时间语义：event_time 是业务 Event Time，collector_received_at/observed_at 是技术观察时间。
        工程边界：invalid（格式/契约错误）与 too-late（合法但迟到）必须分开。
        """
        observed_at_ms = int(time.time() * 1000)
        try:
            data = json.loads(raw_json)
            event_id = str(data["event_id"]).strip()
            event_name = str(data["event_name"]).strip()
            if not event_id or not event_name:
                raise ValueError("event_id / event_name 不能为空")

            event_time_ms = _parse_iso_millis(str(data["event_time"]))
            received_raw = data.get("collector_received_at")
            received_at_ms = (
                _parse_iso_millis(str(received_raw)) if received_raw else observed_at_ms
            )

            # 工程边界：properties 统一序列化成 JSON STRING，先保留半结构化扩展能力；
            # 真正进入指标口径的字段再在契约里提升为显式列。
            properties_json = json.dumps(
                data.get("properties") or {},
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

            yield (
                event_id,
                event_name,
                str(data.get("user_id") or ""),
                str(data.get("session_id") or ""),
                str(data.get("item_id") or ""),
                str(data.get("store_id") or ""),
                event_time_ms,
                received_at_ms,
                str(data.get("page_url") or ""),
                str(data.get("device_type") or ""),
                properties_json,
                raw_json,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            # invalid 是“契约不合法”；不要和合法但迟到的 too-late 混为一谈。
            yield self.invalid_tag, (raw_json, str(exc), observed_at_ms)


class EventTimestampAssigner(TimestampAssigner):
    """告诉 Flink：tuple 第 7 个字段（下标 6）才是业务 Event Time。"""

    def extract_timestamp(self, value, record_timestamp: int) -> int:
        """从规范化事件 tuple 中提取业务 Event Time 毫秒值。
        
        输入：BehaviorEvent tuple。
        输出：epoch milliseconds。
        Flink API：TimestampAssigner 的返回值会参与 Watermark 与 Event Time Window 计算；不能误用 Kafka 到达时间代替业务发生时间。
        """
        return int(value[EVENT_TIME_MS])


class DeduplicateByEventId(KeyedProcessFunction):
    """按 event_id 做 Keyed State 去重，并给 State 配 TTL。

    Exactly-once 不等于“网络上永远没有重复消息”。HTTP / Kafka 重试仍可能让同一 event_id
    被观察多次。这里的 ValueState 记录“这个 key 最近是否已经处理过”。
    """

    def __init__(self, ttl_hours: int):
        """保存去重 State 的 TTL，并预留运行时 State 句柄。
        
        输入：TTL 小时数。
        输出：尚未绑定 RuntimeContext 的 KeyedProcessFunction。
        工程目的：重复网络投递可以发生，但同一 event_id 在治理窗口内只保留一次业务处理。
        """
        self.ttl_hours = ttl_hours
        self.seen = None

    def open(self, runtime_context: RuntimeContext):
        """在 Flink Runtime 中创建带 TTL 的 ValueState。
        
        输入：RuntimeContext。
        输出：把 ``self.seen`` 绑定到 Flink 托管 State。
        Flink API：StateTtlConfig 控制状态生命周期；NeverReturnExpired 避免过期 key 被当作仍已处理。
        工程边界：State 是否能故障恢复取决于 Checkpoint 成功，不由这段定义代码单独证明。
        """
        descriptor = ValueStateDescriptor("seen-event-id", BEHAVIOR_EVENT_TYPE)
        ttl = (
            StateTtlConfig.new_builder(Time.hours(self.ttl_hours))
            .set_update_type(StateTtlConfig.UpdateType.OnCreateAndWrite)
            .set_state_visibility(StateTtlConfig.StateVisibility.NeverReturnExpired)
            .build()
        )
        descriptor.enable_time_to_live(ttl)
        self.seen = runtime_context.get_state(descriptor)

    def process_element(self, value, ctx: KeyedProcessFunction.Context):
        """按当前 key（event_id）执行去重。
        
        输入：一条规范化 BehaviorEvent。
        输出：首次出现时向下游 yield；已存在于 State 时不再输出。
        工程边界：这里解决的是业务事件幂等，不等价于 Kafka/Flink/Iceberg 整条链路的 Exactly-once 证明。
        """
        if self.seen.value() is not None:
            return

        # 保存完整事件而不只是 bool，调试 State 时更容易理解当前 key 对应哪条事件。
        self.seen.update(value)
        yield value


class ProductViewCount(AggregateFunction):
    """窗口内增量计算 product_view 数量，避免把窗口所有事件都缓存到内存。"""

    def create_accumulator(self) -> int:
        """创建窗口增量聚合的初始计数器。
        
        输出：0。
        Flink API：AggregateFunction 使用 accumulator 避免把整个窗口事件列表缓存到内存。
        """
        return 0

    def add(self, value, accumulator: int) -> int:
        """把一条窗口内事件累加到计数器。
        
        输入：事件与当前 accumulator。
        输出：``accumulator + 1``。
        数据语义：调用前应已过滤到目标事件类型，因此这里不再次解释业务过滤条件。
        """
        return accumulator + 1

    def get_result(self, accumulator: int) -> int:
        """把 accumulator 转成窗口聚合结果。
        
        输入：当前计数。
        输出：同一个整数计数。
        """
        return accumulator

    def merge(self, a: int, b: int) -> int:
        """合并两个窗口 accumulator。
        
        输入：两个局部计数。
        输出：二者之和。
        Flink API：当运行时需要合并局部聚合结果时使用，保持聚合函数可组合。
        """
        return a + b


class AddWindowMetadata(ProcessWindowFunction):
    """把聚合 count 加上 item_id / window_start / window_end。

    ``elements`` 此时通常只含一个“已经增量聚合好的 count”，因此既有窗口元数据，
    又不需要缓存完整窗口事件。
    """

    def process(
        self,
        key: str,
        context: ProcessWindowFunction.Context,
        elements: Iterable[int],
    ):
        """给窗口聚合结果补充业务 key 与窗口边界。
        
        输入：item_id key、Window Context、已聚合的 count。
        输出：``(item_id, window_start, window_end, count, emitted_at_ms)``。
        时间语义：window_start/end 是 Event Time 窗口；emitted_at_ms 是技术发射时间，可区分 main firing 与 late firing。
        """
        count = next(iter(elements))
        window = context.window()
        # emitted_at_ms 帮助判断这是 main firing 还是后续 late firing 的更新版本。
        yield (key, window.start, window.end, count, int(time.time() * 1000))
