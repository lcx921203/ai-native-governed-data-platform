#!/usr/bin/env sh
set -eu

trino --server http://localhost:8080 --execute "SELECT 1"
trino --server http://localhost:8080 --execute "SHOW CATALOGS"
trino --server http://localhost:8080 --execute "SHOW SCHEMAS FROM iceberg"
