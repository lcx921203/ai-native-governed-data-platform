"""FastAPI 行为事件 Collector。

浏览器 / App 不直接暴露 Kafka Broker，而是通过 HTTPS 调这个薄服务。
Collector 负责入口契约和传输，不在这里做窗口统计或 Flink State 逻辑。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import FastAPI, Header, Response, status

from ingestion.behavior.collector.models import BehaviorEventIn
from ingestion.behavior.collector.producer import BehaviorKafkaProducer


app = FastAPI(title="Commerce Behavior Event Collector", version="1.0.0")
producer = BehaviorKafkaProducer()


@app.post("/v1/events", status_code=status.HTTP_202_ACCEPTED)
def collect_event(
    event: BehaviorEventIn,
    response: Response,
    x_request_id: str | None = Header(default=None),
) -> dict[str, str]:
    """校验事件并写入 Kafka，成功后返回 202 Accepted。

    Python / Pydantic：``model_dump(mode='json')`` 会把 datetime 转成 JSON 可序列化形式。
    工程语义：202 表示 Collector 已收到 Kafka Broker 的写入确认；
    但它仍不代表下游 Flink / Iceberg 已经完成处理。
    """
    request_id = x_request_id or str(uuid.uuid4())
    payload = event.model_dump(mode="json")
    payload["collector_received_at"] = datetime.now(timezone.utc).isoformat()
    payload["collector_request_id"] = request_id

    producer.publish(payload)
    response.headers["X-Request-ID"] = request_id
    return {"status": "accepted", "event_id": event.event_id, "request_id": request_id}


@app.on_event("shutdown")
def close_producer() -> None:
    """在 FastAPI 进程退出时关闭 Kafka Producer。
    
    框架 API：FastAPI shutdown hook 在应用停止阶段调用此函数。
    工程目的：先 flush/close Producer，避免进程退出时仍有缓冲区消息未发送。
    """
    producer.flush()
