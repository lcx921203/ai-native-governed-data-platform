{{ config(materialized='table', file_format='iceberg') }}

-- MetricFlow Time Spine
-- Demo fixtures 位于 2026 年；覆盖 2020-01-01 ~ 2030-12-31 足够本项目学习与验证。
select
    explode(
        sequence(
            to_date('2020-01-01'),
            to_date('2030-12-31'),
            interval 1 day
        )
    ) as date_day
