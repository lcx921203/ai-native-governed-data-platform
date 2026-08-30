{{ config(materialized='table', file_format='iceberg') }}

select
    explode(
        sequence(
            to_date('2020-01-01'),
            to_date('2030-12-31'),
            interval 1 day
        )
    ) as date_day
