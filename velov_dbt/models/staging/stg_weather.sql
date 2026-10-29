{{ config(materialized='view') }}

-- Open-Meteo publie la météo au quart d'heure (ex : 14:45), la table gold joint à l'heure pile.
-- Même convention que stg_station_status -> station_hourly : l'heure h couvre ]h - 1 h, h].
-- On garde la dernière observation de l'intervalle : jamais d'information postérieure à h (EX-11).
with observations as (
    select
        to_timestamp(cast(hour_ts as bigint)) as observed_at,
        last_updated,
        cast(temperature as float)            as temperature,
        cast(is_raining as boolean)           as is_raining
    from read_json('{{ var("bronze") }}/weather/*/*.json')
)

select
    date_trunc('hour', observed_at - interval 1 microsecond) + interval 1 hour as hour_ts,
    temperature,
    is_raining
from observations
qualify row_number() over (
    partition by date_trunc('hour', observed_at - interval 1 microsecond) + interval 1 hour
    order by observed_at desc, last_updated desc
) = 1
