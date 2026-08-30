# Flink Runtime Baseline

Source baseline:

- Apache Flink `1.20.5`
- Apache Flink CDC `3.6.0` (supports Flink 1.20.x)
- Iceberg `1.10.1` Flink runtime for 1.20
- Kafka connector line compatible with Flink 1.20

## Required connector/runtime JARs

The production Flink image / cluster must provide connector JARs under `$FLINK_HOME/lib` (or equivalent job dependency mechanism):

```text
flink-sql-connector-mysql-cdc-3.6.0.jar
mysql-connector-j (compatible MySQL driver)
iceberg-flink-runtime-1.20-1.10.1.jar
flink-connector-kafka for Flink 1.20
```

Connector versions must be validated as a set before live acceptance. The project does not claim those binaries were downloaded or started in this chat environment.

## Fault tolerance intent

```text
Checkpoint mode        EXACTLY_ONCE
Checkpoint storage     S3-compatible durable storage (RustFS in local runtime)
State backend          EmbeddedRocksDBStateBackend + incremental checkpoints
Max concurrent CP      1
Externalized CP        RETAIN_ON_CANCELLATION
Unaligned CP           false by default; optional under severe backpressure
Savepoints             separate durable URI for controlled upgrades/rescaling
```

`Checkpoint` is automatic failure recovery state. `Savepoint` is an operator-triggered lifecycle artifact for upgrades / migration / rescaling.
