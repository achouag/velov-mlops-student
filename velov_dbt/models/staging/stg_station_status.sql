{{ config(materialized='view') }}

select
    cast(s.station_id as integer)                 as station_id,
    to_timestamp(cast(s.last_reported as bigint)) as reported_at,
    to_timestamp(cast(last_updated as bigint))    as feed_updated_at,
    cast(s.num_bikes_available as integer)        as bikes_available,
    s.is_installed = 1                            as is_installed
from (
    select last_updated, unnest(data.stations) as s
    from read_json('{{ var("bronze") }}/station_status/*/*.json')
)
qualify row_number() over (
    partition by s.station_id, s.last_reported
    order by last_updated
) = 1
