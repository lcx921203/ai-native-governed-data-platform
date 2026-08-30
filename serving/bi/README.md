# BI Serving through Trino

BI tools consume the fixed Iceberg Serving projection through Trino. They do not connect to object-storage files directly and do not reimplement MetricFlow formulas.

Local connection shape:

```text
JDBC URL : jdbc:trino://localhost:8088/iceberg/serving
Catalog  : iceberg
Schema   : serving
Table    : bi_daily_executive
```

The Dashboard query surface is intentionally simple because `net_sales`, `order_count`, and `average_order_value` have already been computed through the governed MetricFlow contract before materialization.

Example read-only SQL lives in `dashboard_queries.sql`.

Production deployments should put Trino behind the organization’s normal authentication/TLS/network boundary. The local Compose profile is a development topology, not an internet-facing BI endpoint.
