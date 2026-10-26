{{ config(materialized='table') }}

with hourly_status as (
    select
        s.station_id,
        date_trunc('hour', s.reported_at - interval 1 microsecond) + interval 1 hour as hour_ts,
        s.reported_at,
        s.bikes_available
    from {{ ref('stg_station_status') }} s
    where s.is_installed = true
    qualify row_number() over (
        partition by s.station_id, date_trunc('hour', s.reported_at - interval 1 microsecond) + interval 1 hour
        order by s.reported_at desc
    ) = 1
)

select
    cast(h.station_id as integer)                 as station_id,
    cast(h.hour_ts as timestamp with time zone)   as timestamp,
    cast(i.capacity as integer)                   as capacity,
    cast(h.bikes_available as integer)            as bikes_available,
    cast(w.temperature as float)                  as temperature,
    cast(w.is_raining as boolean)                 as is_raining
from hourly_status h
join {{ ref('stg_station_information') }} i
    on h.station_id = i.station_id
join {{ ref('stg_weather') }} w
    on h.hour_ts = w.hour_ts
