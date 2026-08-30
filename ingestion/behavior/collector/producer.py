"""Kafka Producer 封装。

注意：Kafka Producer 的 idempotence 只能降低 Producer 重试产生的 Kafka 重复，
不能把“HTTP 客户端重试 → Collector”自动变成端到端 Exactly-once。
所以 SDK 必须生成稳定的 event_id，Flink 仍会按 event_id 做有状态去重。
"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from confluent_kafka import Producer


class BehaviorKafkaProducer:
    """行为事件 Collector 使用的 Kafka Producer 封装。

    负责统一 Topic、幂等 Producer、ACK 等待与退出 Flush；但 Producer Idempotence 只能降低
    Producer 自身重试重复，端到端去重仍依赖稳定 ``event_id`` + Flink Stateful Dedup。
    """

    def __init__(self) -> None:
        """创建行为事件 Kafka Producer，并加载 Broker / Topic 等运行配置。
        
        输入：环境变量中的 Kafka 连接参数。
        输出：可复用的 Producer 封装对象。
        工程边界：连接失败应暴露异常，不在这里静默降级到本地文件或假成功。
        """
        self.topic = os.getenv("BEHAVIOR_KAFKA_TOPIC", "commerce.behavior.events")
        self._producer = Producer(
            {
                "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
                # Kafka 幂等 Producer 的关键开关；acks=all 让 leader 等待 ISR 确认。
                "enable.idempotence": True,
                "acks": "all",
                "client.id": os.getenv("BEHAVIOR_COLLECTOR_CLIENT_ID", "behavior-collector"),
                "compression.type": os.getenv("BEHAVIOR_KAFKA_COMPRESSION", "lz4"),
            }
        )

    def publish(self, event: dict[str, Any]) -> None:
        """把一条 HTTP 事件写入 Kafka，并等待 Broker 确认后再返回。

        Kafka key 使用 event_id，同一个 event_id 会稳定路由到同一分区，便于下游按 key 处理。
        ``json.dumps(..., ensure_ascii=False)`` 保留中文，不把它转成 ``\\uXXXX``。

        为什么这里要等 delivery callback：
        ``Producer.produce()`` 只表示消息进入本进程 Producer 缓冲区，不代表 Kafka 已经持久化。
        如果 Collector 立刻返回 HTTP 202 后进程崩溃，缓冲区里的消息可能丢失。
        因此本示例在返回 202 前等待 Broker ACK。高吞吐生产环境可以改成批量异步 ACK，
        但“什么时候可以向客户端确认成功”这个可靠性边界不能省略。
        """
        payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        delivery = {"done": False, "error": None}

        def on_delivery(error, _message) -> None:
            # Python 闭包：内部函数可以修改外层 dict 的内容，用它记录异步回调结果。
            """处理 Kafka Producer 的异步投递回调。
            
            输入：Kafka 客户端返回的 error/message。
            输出：无返回值；失败时抛出/记录明确错误。
            Kafka API：回调发生在异步 send 完成后，用于区分“调用 produce 成功”与“Broker 真正确认写入”。
            """
            delivery["done"] = True
            delivery["error"] = error

        self._producer.produce(
            topic=self.topic,
            key=str(event["event_id"]).encode("utf-8"),
            value=payload,
            on_delivery=on_delivery,
        )

        timeout_seconds = float(os.getenv("BEHAVIOR_KAFKA_ACK_TIMEOUT_SECONDS", "10"))
        deadline = time.monotonic() + timeout_seconds
        while not delivery["done"]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("等待 Kafka Broker ACK 超时")
            # poll() 会驱动 delivery callback；最多阻塞很短一段，避免 busy loop 空转 CPU。
            self._producer.poll(min(0.1, remaining))

        if delivery["error"] is not None:
            raise RuntimeError(f"Kafka 写入失败: {delivery['error']}")

    def flush(self, timeout: float = 10.0) -> None:
        """进程退出前等待已入队消息发送完成。"""
        remaining = self._producer.flush(timeout)
        if remaining:
            raise RuntimeError(f"Kafka Producer 退出时仍有 {remaining} 条消息未确认")
