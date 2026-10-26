{{ config(materialized='view') }}

select
    to_timestamp(cast(hour_ts as bigint)) as hour_ts,
    cast(temperature as float)            as temperature,
    cast(is_raining as boolean)           as is_raining
from read_json('{{ var("bronze") }}/weather/*/*.json')
qualify row_number() over (
    partition by hour_ts
    order by last_updated desc
) = 1
