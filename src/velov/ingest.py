"""Module d'ingestion bronze idempotente pour Vélo'v (GBFS) et météo.

Architecture médaillon - Couche Bronze :
- Données brutes stockées telles quelles en JSON, sans modification.
- Partitionnement temporel : data/bronze/<feed>/dt=YYYY-MM-DD/<key>.json
- Clé d'idempotence issue de la donnée : payload['last_updated']
- Écriture atomique via fichier temporaire (.tmp) puis renommage (atomic replace).
- Relancer sur une période existante ne crée aucun doublon (EX-10).

Usage :
    # Collecter les flux en direct depuis la Métropole de Lyon
    python -m velov.ingest --bronze data/bronze

    # Générer le jeu bronze de démonstration (48h, 576 instantanés, 20 stations)
    python -m velov.ingest --bronze data/bronze --seed-demo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import math
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import Request, urlopen

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

GBFS_BASE_URL = "https://download.data.grandlyon.com/files/rdata/jcd_jcdecaux.jcdvelov"
GBFS_STATUS_URL = f"{GBFS_BASE_URL}/station_status.json"
GBFS_INFO_URL = f"{GBFS_BASE_URL}/station_information.json"
OPEN_METEO_URL = (
    "https://api.open-meteo.com/v1/forecast?latitude=45.75&longitude=4.85"
    "&current=temperature_2m,rain&timezone=UTC"
)


def fetch_json(url: str, timeout: float = 10.0) -> dict[str, Any]:
    """Récupère un payload JSON distant avec timeout de 10 secondes."""
    req = Request(url, headers={"User-Agent": "velov-mlops-pipeline/1.0"})
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ingest_payload(payload: dict[str, Any], bronze: Path, feed: str) -> tuple[Path, bool]:
    """Écrit un payload brut dans la couche bronze de manière atomique et idempotente.

    Returns:
        tuple[Path, bool]: (chemin cible, True si écrit / False si déjà présent).
    """
    key = payload.get("last_updated")
    if key is None:
        # Repli pour les flux sans last_updated (ex: empreinte ou horodatage interne)
        if "current" in payload and "time" in payload["current"]:
            dt_cur = datetime.fromisoformat(payload["current"]["time"]).replace(tzinfo=UTC)
            key = int(dt_cur.timestamp())
        else:
            raw_bytes = json.dumps(payload, sort_keys=True).encode("utf-8")
            digest = hashlib.sha256(raw_bytes).hexdigest()[:16]
            key = f"hash_{digest}"

    if isinstance(key, (int, float)):
        ts = datetime.fromtimestamp(key, tz=UTC)
        partition = f"dt={ts:%Y-%m-%d}"
        filename = f"{int(key)}.json"
    else:
        now = datetime.now(UTC)
        partition = f"dt={now:%Y-%m-%d}"
        filename = f"{key}.json"

    target = bronze / feed / partition / filename
    if target.exists():
        return target, False

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(target)
    return target, True


def ingest_feed(url: str, bronze: Path, feed: str) -> tuple[Path | None, bool]:
    """Extrait et charge un flux distant vers bronze."""
    try:
        payload = fetch_json(url)
        return ingest_payload(payload, bronze, feed)
    except (URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("Échec de la collecte pour %s (%s) : %s", feed, url, e)
        return None, False


def seed_demonstration_bronze(bronze_dir: Path, n_stations: int = 20, hours: int = 48) -> dict[str, int]:
    """Génère le bronze de démonstration du cours (48h, instantanés toutes les 5 min = 576 instantanés).

    Comprend :
    - 20 stations sur 48h
    - Un instantané toutes les 5 min (576 runs)
    - Une anomalie injectée (99 vélos sur station 10004) pour valider l'alerte du test dbt
    - Météo horaire associée
    """
    logger.info("Génération du bronze de démonstration (48h, %d stations)...", n_stations)
    start_ts = int(datetime(2026, 10, 5, 0, 0, tzinfo=UTC).timestamp())
    snapshots_count = hours * 12  # 576
    step_seconds = 300  # 5 minutes

    written = {"station_information": 0, "station_status": 0, "weather": 0}

    # 1. station_information (stations 10001 à 10020)
    # 20 stations avec capacités cohérentes avec le flux réel et les slides (10003=32, 10004=10/38)
    real_capacities = {
        10001: 30, 10002: 55, 10003: 32, 10004: 10, 10005: 35,
        10006: 46, 10007: 20, 10008: 24, 10009: 22, 10010: 16,
        10011: 16, 10012: 16, 10013: 20, 10014: 28, 10015: 18,
        10016: 17, 10017: 15, 10018: 24, 10019: 30, 10020: 25,
    }

    stations_info = []
    base_capacities = {}
    for i in range(1, n_stations + 1):
        st_id = 10000 + i
        cap = real_capacities.get(st_id, 25)
        base_capacities[st_id] = cap
        stations_info.append(
            {
                "station_id": str(st_id),
                "name": f"STATION_{st_id}",
                "address": f"{i} rue de Lyon",
                "lat": round(45.75 + (i * 0.005), 6),
                "lon": round(4.83 + (i * 0.005), 6),
                "capacity": cap,
            }
        )

    info_payload = {
        "last_updated": start_ts,
        "ttl": 3600,
        "version": "2.3",
        "data": {"stations": stations_info},
    }
    _, is_new = ingest_payload(info_payload, bronze_dir, "station_information")
    if is_new:
        written["station_information"] += 1

    # 2. weather horaire
    for h in range(hours + 1):
        w_ts = start_ts + h * 3600
        hour_of_day = (datetime.fromtimestamp(w_ts, tz=UTC).hour)
        temp = round(9.0 + 5.0 * math.sin((hour_of_day - 6) / 24 * 2 * math.pi), 1)
        is_rain = (h % 11 == 0)
        w_payload = {
            "last_updated": w_ts,
            "hour_ts": w_ts,
            "temperature": temp,
            "is_raining": is_rain,
        }
        _, is_new = ingest_payload(w_payload, bronze_dir, "weather")
        if is_new:
            written["weather"] += 1

    # 3. station_status toutes les 5 min (576 instantanés)
    for step in range(snapshots_count):
        cur_ts = start_ts + (step * step_seconds)
        hour_fraction = (step % 288) / 12.0  # heure dans la journée 0..24
        status_list = []

        for st_id, cap in base_capacities.items():
            # Profil réaliste : baisse le matin, hausse le soir
            base_occ = 0.5 + 0.3 * math.sin((hour_fraction - 8) / 24 * 2 * math.pi)
            bikes = int(min(max(0, round(base_occ * cap)), cap))

            # Injection volontaire de l'anomalie documentée slide 27
            # Station 10004 à 02:33 (step correspondant) -> 99 vélos (pour simuler capteur défaillant)
            if st_id == 10004 and step == 31:
                bikes = 99

            # Station 10002 parfois muette pour tester stations_silencieuses
            if st_id == 10002 and step > 450:
                last_rep = start_ts + 450 * step_seconds
            else:
                last_rep = cur_ts - (step % 3) * 60

            status_list.append(
                {
                    "station_id": str(st_id),
                    "num_bikes_available": bikes,
                    "num_docks_available": max(0, cap - bikes),
                    "is_installed": 1,
                    "is_renting": 1,
                    "is_returning": 1,
                    "last_reported": last_rep,
                }
            )

        snap_payload = {
            "last_updated": cur_ts,
            "ttl": 300,
            "version": "2.3",
            "data": {"stations": status_list},
        }
        _, is_new = ingest_payload(snap_payload, bronze_dir, "station_status")
        if is_new:
            written["station_status"] += 1

    logger.info(
        "Bronze démo : %d info, %d status, %d weather écrits.",
        written["station_information"],
        written["station_status"],
        written["weather"],
    )
    return written


def run_ingestion(bronze_dir: Path) -> dict[str, bool]:
    """Exécute l'ingestion de tous les flux configurés."""
    results = {}

    # Ingestion GBFS Grand Lyon
    _, new_status = ingest_feed(GBFS_STATUS_URL, bronze_dir, "station_status")
    results["station_status"] = new_status

    _, new_info = ingest_feed(GBFS_INFO_URL, bronze_dir, "station_information")
    results["station_information"] = new_info

    # Ingestion météo Open-Meteo
    try:
        w_payload = fetch_json(OPEN_METEO_URL)
        cur = w_payload.get("current", {})
        ts_cur = int(datetime.fromisoformat(cur.get("time", datetime.now(UTC).isoformat())).timestamp())
        weather_clean = {
            "last_updated": ts_cur,
            "hour_ts": ts_cur,
            "temperature": float(cur.get("temperature_2m", 15.0)),
            "is_raining": bool(cur.get("rain", 0.0) > 0.0),
        }
        _, new_weather = ingest_payload(weather_clean, bronze_dir, "weather")
        results["weather"] = new_weather
    except Exception as e:
        logger.warning("Échec collecte météo Open-Meteo : %s", e)
        results["weather"] = False

    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingestion bronze idempotente des flux Vélo'v")
    parser.add_argument("--bronze", type=Path, default=Path("data/bronze"))
    parser.add_argument("--seed-demo", action="store_true", help="générer le jeu de démo 48h (576 instantanés)")
    args = parser.parse_args()

    args.bronze.mkdir(parents=True, exist_ok=True)

    if args.seed_demo:
        seed_demonstration_bronze(args.bronze)
        return

    # Si le bronze est entièrement vide, on propose ou initialise le dataset de démo pour permettre à dbt build de tourner
    status_files = list(args.bronze.glob("station_status/*/*.json"))
    if not status_files:
        logger.info("Bronze vide détecté : initialisation avec le jeu de démonstration 48h...")
        seed_demonstration_bronze(args.bronze)

    logger.info("Lancement de la collecte en direct...")
    results = run_ingestion(args.bronze)
    logger.info("Résultats de la collecte : %s", results)


if __name__ == "__main__":
    main()
