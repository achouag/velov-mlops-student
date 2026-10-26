{{ config(materialized='view') }}

select
    cast(s.station_id as integer) as station_id,
    cast(s.capacity as integer)   as capacity,
    cast(s.name as varchar)       as station_name,
    cast(s.lat as double)         as latitude,
    cast(s.lon as double)         as longitude
from (
    select last_updated, unnest(data.stations) as s
    from read_json('{{ var("bronze") }}/station_information/*/*.json')
)
qualify row_number() over (
    partition by s.station_id
    order by last_updated desc
) = 1
