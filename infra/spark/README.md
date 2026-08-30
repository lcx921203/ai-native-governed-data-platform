# Spark

Spark 是本项目的计算引擎。

目标链路：

```text
dbt
→ Spark Thrift Server
→ Spark SQL
→ Polaris REST Catalog
→ Iceberg
→ RustFS
```
