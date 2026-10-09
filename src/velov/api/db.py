"""Module de journalisation des prédictions dans PostgreSQL (Exigence EX-08)."""

from __future__ import annotations

import logging
import psycopg2
from psycopg2.extras import execute_values

from velov.api.schemas import PredictionRequest, PredictionResponse

logger = logging.getLogger("velov.api.db")

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS predictions (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    station_id INT NOT NULL,
    observation_timestamp TIMESTAMPTZ NOT NULL,
    capacity INT NOT NULL,
    bikes_available INT NOT NULL,
    temperature FLOAT NOT NULL,
    is_raining BOOLEAN NOT NULL,
    predicted_bikes FLOAT NOT NULL,
    target_timestamp TIMESTAMPTZ NOT NULL,
    model_version VARCHAR(50) NOT NULL
);
"""


def init_db(database_url: str) -> None:
    """Crée la table predictions si elle n'existe pas."""
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(CREATE_TABLE_SQL)
    logger.info("Table 'predictions' vérifiée/créée avec succès")


def log_prediction(database_url: str, request: PredictionRequest, response: PredictionResponse) -> None:
    """Insère une observation et sa prédiction en base de données."""
    insert_sql = """
    INSERT INTO predictions (
        station_id, observation_timestamp, capacity, bikes_available,
        temperature, is_raining, predicted_bikes, target_timestamp, model_version
    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s);
    """
    with psycopg2.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                insert_sql,
                (
                    request.station_id,
                    request.timestamp,
                    request.capacity,
                    request.bikes_available,
                    request.temperature,
                    request.is_raining,
                    response.predicted_bikes,
                    response.target_timestamp,
                    response.model_version,
                ),
            )
