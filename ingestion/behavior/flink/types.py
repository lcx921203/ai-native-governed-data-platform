"""PyFlink 行为流里的 Tuple 数据形状。

为了让 Python UDF 与 Java Flink 之间有明确的序列化类型，这里显式定义 TypeInformation。
源码使用 tuple 而不是随意传 dict，能让 DataStream 的字段位置和 Sink Contract 更稳定。
"""

from pyflink.common.typeinfo import Types


# BehaviorEvent tuple 下标，统一集中定义，避免代码里到处出现“神秘数字”。
EVENT_ID = 0
EVENT_NAME = 1
USER_ID = 2
SESSION_ID = 3
ITEM_ID = 4
STORE_ID = 5
EVENT_TIME_MS = 6
COLLECTOR_RECEIVED_AT_MS = 7
PAGE_URL = 8
DEVICE_TYPE = 9
PROPERTIES_JSON = 10
RAW_JSON = 11

BEHAVIOR_EVENT_TYPE = Types.TUPLE(
    [
        Types.STRING(),  # event_id
        Types.STRING(),  # event_name
        Types.STRING(),  # user_id
        Types.STRING(),  # session_id
        Types.STRING(),  # item_id
        Types.STRING(),  # store_id
        Types.LONG(),    # event_time_ms
        Types.LONG(),    # collector_received_at_ms
        Types.STRING(),  # page_url
        Types.STRING(),  # device_type
        Types.STRING(),  # properties_json
        Types.STRING(),  # raw_json
    ]
)

RAW_OBSERVATION_TYPE = Types.TUPLE([Types.STRING(), Types.LONG()])
INVALID_EVENT_TYPE = Types.TUPLE([Types.STRING(), Types.STRING(), Types.LONG()])
PRODUCT_VIEW_WINDOW_TYPE = Types.TUPLE(
    [Types.STRING(), Types.LONG(), Types.LONG(), Types.LONG(), Types.LONG()]
)
