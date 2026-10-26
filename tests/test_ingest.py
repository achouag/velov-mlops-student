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
