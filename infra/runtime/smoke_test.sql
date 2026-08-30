-- Minimal engine/catalog/table-format/storage smoke test.
-- If this succeeds, Spark -> Polaris -> Iceberg -> RustFS is connected.

USE polaris;
CREATE NAMESPACE IF NOT EXISTS runtime_smoke;

CREATE TABLE IF NOT EXISTS runtime_smoke.connectivity_test (
    id BIGINT,
    message STRING
) USING iceberg;

INSERT INTO runtime_smoke.connectivity_test VALUES (1, 'spark-polaris-iceberg-rustfs-ok');

SELECT *
FROM runtime_smoke.connectivity_test
ORDER BY id DESC
LIMIT 5;
