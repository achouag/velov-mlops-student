{{ config(warn_if = '>0', error_if = '>10') }}

-- Test singulier : vérifie que le nombre de vélos disponibles reste dans [0, capacity]
-- Déclenche un WARN dès la 1ère anomalie (pour observation), et un ERROR bloquant si > 10 anomalies.
select
    s.station_id,
    s.reported_at,
    s.bikes_available,
    i.capacity
from {{ ref('stg_station_status') }} s
join {{ ref('stg_station_information') }} i
    on s.station_id = i.station_id
where s.bikes_available < 0
   or s.bikes_available > i.capacity
