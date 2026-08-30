# 数据接入层（Ingestion）

这个项目不把所有数据源强行套进同一种采集方式，而是按源系统的变化语义拆成三条生产路径：

```text
Shopify SaaS                    Internal MySQL                  Behavior Events
    │                                │                               │
GraphQL API                        binlog                         HTTPS SDK
    │                                │                               │
Python Extractor                 Flink CDC                    FastAPI Collector
    │                                │                               │
updated_at + Lookback            Flink SQL                         Kafka
Cursor / Throttle                  │                               │
    │                                │                         PyFlink DataStream
    └───────────────┬────────────────┴───────────────────────────────┘
                    ▼
                  Iceberg
```

## 三种源语义

- **API Observation（API 观察）**：平台只能看到 SaaS API 这一次返回了什么，因此允许重复观察，后续再判断业务版本。
- **CDC Change（CDC 变更）**：内部数据库通过 binlog 直接暴露 INSERT / UPDATE / DELETE 变化，适合维护 current state。
- **Behavior Event（行为事件）**：用户点击、浏览、加购天然是 append-oriented event stream，重点是 Event Time、乱序、State 与恢复。

## 目录

- `shopify/`：第三方 Shopify Admin GraphQL API。
- `mysql_cdc/`：MySQL Item / Store → Flink CDC + Flink SQL → Iceberg。
- `behavior/`：FastAPI Collector → Kafka → PyFlink DataStream → Iceberg。

新增的 MySQL / Behavior 两条链直接按生产结构编写，不额外维护 Fixture 分支。真实集群运行证据仍需在工作站执行 Runtime Acceptance 后再标记 PASS。
