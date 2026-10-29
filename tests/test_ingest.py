from datetime import UTC, datetime
from pathlib import Path

from velov.ingest import ingest_payload


def test_ingest_idempotence_and_atomic_write(tmp_path: Path):
    bronze = tmp_path / "bronze"
    payload = {
        "last_updated": 1793010701,
        "ttl": 300,
        "data": {"stations": [{"station_id": "10001", "num_bikes_available": 5}]},
    }

    # Premier passage : écriture
    target, is_new = ingest_payload(payload, bronze, "station_status")
    assert is_new is True
    assert target.exists()
    assert not target.with_suffix(".tmp").exists()

    ts = datetime.fromtimestamp(1793010701, tz=UTC)
    expected_path = bronze / "station_status" / f"dt={ts:%Y-%m-%d}" / "1793010701.json"
    assert target == expected_path

    # Deuxième passage : aucun doublon ni réécriture
    target_second, is_new_second = ingest_payload(payload, bronze, "station_status")
    assert is_new_second is False
    assert target_second == target


def _fake_feeds(url: str, timeout: float = 10.0) -> dict:
    """Réponses figées des trois flux, sans réseau."""
    if "open-meteo" in url:
        # Open-Meteo avec timezone=UTC : heure naïve, au quart d'heure
        return {"current": {"time": "2026-10-26T14:45", "temperature_2m": 14.0, "rain": 0.0}}
    stations = [{"station_id": "1024", "capacity": 16, "num_bikes_available": 4, "last_reported": 1793023000}]
    return {"last_updated": 1793023180, "ttl": 1, "data": {"stations": stations}}


def test_collecte_sur_bronze_vide_sans_jeu_de_demo(tmp_path: Path, monkeypatch):
    """Un bronze vide ne doit pas être rempli avec les données de démo : elles pollueraient l'historique réel."""
    import velov.ingest as ingest

    monkeypatch.setattr(ingest, "fetch_json", _fake_feeds)
    bronze = tmp_path / "bronze"
    monkeypatch.setattr("sys.argv", ["velov.ingest", "--bronze", str(bronze)])

    ingest.main()

    status_files = list(bronze.glob("station_status/*/*.json"))
    assert [f.name for f in status_files] == ["1793023180.json"]  # uniquement l'instantané réel


def test_meteo_horodatee_en_utc_quel_que_soit_le_fuseau(tmp_path: Path, monkeypatch):
    """L'heure Open-Meteo est en UTC : le fichier bronze ne doit pas dépendre du fuseau de la machine."""
    import time

    import velov.ingest as ingest

    monkeypatch.setattr(ingest, "fetch_json", _fake_feeds)
    monkeypatch.setenv("TZ", "Europe/Paris")
    time.tzset()
    try:
        ingest.run_ingestion(tmp_path)
    finally:
        monkeypatch.delenv("TZ")
        time.tzset()

    expected = int(datetime(2026, 10, 26, 14, 45, tzinfo=UTC).timestamp())
    assert [f.name for f in tmp_path.glob("weather/*/*.json")] == [f"{expected}.json"]
