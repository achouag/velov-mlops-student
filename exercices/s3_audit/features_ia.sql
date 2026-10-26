-- Requête proposée par un assistant suite au prompt :
-- "ajoute à la table horaire la moyenne des vélos sur l'heure et la météo"
-- ATTENTION : CETTE REQUÊTE CONTIENT UNE FUITE TEMPORELLE (DATA LEAKAGE)

select
    s.station_id,
    date_trunc('hour', s.reported_at) as hour_ts,
    -- PROBLÈME 1 : Moyenne calculée sur toute l'heure qui commence à hour_ts [hour_ts, hour_ts + 1h]
    avg(s.bikes_available) as avg_bikes_hour,
    -- PROBLÈME 2 : Jointure sur la météo observée de l'heure cible (inconnue à l'instant de prédiction)
    w.temperature,
    w.is_raining
from stg_station_status s
join stg_weather w
    on date_trunc('hour', s.reported_at) = w.hour_ts
group by
    s.station_id,
    date_trunc('hour', s.reported_at),
    w.temperature,
    w.is_raining;
