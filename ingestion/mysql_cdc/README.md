# MySQL Master Data CDC

这条生产链负责内部 MySQL 的 `item` / `store` 主数据接入：

```text
MySQL item / store
       │ binlog
       ▼
Flink CDC 3.6 + Flink SQL
       │ initial snapshot + continuous binlog
       ▼
Iceberg v2 current-state tables
       │
       ├── source.item_current
       └── source.store_current
```

## 为什么这里用 Flink SQL

这条链的主要问题是 **CDC / Changelog（变更日志）语义**，而不是复杂的用户自定义状态。
Flink SQL 能清楚表达：

- `scan.startup.mode='initial'`：第一次先读存量快照，再持续读 binlog；
- `PRIMARY KEY (...) NOT ENFORCED`：告诉 Flink Changelog 的业务键；
- 独立 `server-id` 范围：两个 CDC Reader 不互相冲突；
- Iceberg v2 + `write.upsert.enabled='true'`：把 INSERT / UPDATE / DELETE 变成当前状态表。

## 生产运行

源码不保存数据库密码。先在部署环境设置：

```bash
export MYSQL_HOST=mysql.internal
export MYSQL_PORT=3306
export MYSQL_DATABASE=commerce_master
export MYSQL_CDC_USER=flink_cdc
export MYSQL_CDC_PASSWORD='***'
export MYSQL_SERVER_TIME_ZONE=UTC
```

然后：

```bash
ingestion/mysql_cdc/flink/run.sh
```

`run.sh` 会把环境变量渲染进临时 SQL，再交给 Flink SQL Client。模板本身没有真实密钥。

## 运行前 MySQL 条件

生产 MySQL 需要启用 row-based binlog，并给 CDC 用户最小必要复制/读取权限。
具体权限应由数据库管理员按环境治理，不把高权限账号写进项目源码。

## Runtime Evidence

当前包只完成 **source/static engineering**；没有在本会话里启动真实 MySQL / Flink / Iceberg 集群，
因此不把这条链标成 live runtime PASS。


## 故障恢复

Flink SQL 模板显式启用 Exactly-once Checkpoint，并把 Checkpoint 写入持久存储。
Checkpoint 会保存 CDC 读取进度与需要恢复的算子状态；任务挂掉后从最近成功 Checkpoint
恢复，再继续消费对应 binlog 位置。Iceberg Sink 也必须参与 Checkpoint Commit，
否则只能做到“Source 不丢”，做不到端到端最终结果不重复。
