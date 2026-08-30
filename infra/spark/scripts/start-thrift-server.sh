#!/usr/bin/env bash
set -euo pipefail

export SPARK_CONF_DIR=/opt/spark/conf/project
mkdir -p /tmp/spark-events /tmp/spark-warehouse

# `start-thriftserver.sh` daemonizes. We keep the container alive by following
# the generated log after the server starts.
/opt/spark/sbin/start-thriftserver.sh \
  --master local[*] \
  --hiveconf hive.server2.thrift.bind.host=0.0.0.0 \
  --hiveconf hive.server2.thrift.port=10000

LOG_FILE=""
for _ in $(seq 1 60); do
  LOG_FILE="$(find /opt/spark/logs -maxdepth 1 -type f -name '*HiveThriftServer2*.out' -o -name '*ThriftServer*.out' 2>/dev/null | head -n 1 || true)"
  if [[ -n "$LOG_FILE" ]]; then
    break
  fi
  sleep 1
done

if [[ -z "$LOG_FILE" ]]; then
  echo "Spark Thrift Server log file was not created" >&2
  find /opt/spark/logs -maxdepth 1 -type f -print 2>/dev/null || true
  exit 1
fi

echo "Spark Thrift Server started; following $LOG_FILE"
exec tail -n +1 -F "$LOG_FILE"
