"""Fixtures partagées : un petit modèle entraîné une seule fois par session de tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from velov.data import simulate
from velov.train import train


@pytest.fixture(scope="session")
def model_dir(tmp_path_factory) -> Path:
    workdir = tmp_path_factory.mktemp("model")
    data_path = workdir / "history.csv"
    simulate(days=20, n_stations=3, seed=0).to_csv(data_path, index=False)
    out = workdir / "models"
    train(data_path, out, version="0.0.0-test", test_days=3)
    return out


@pytest.fixture(scope="session")
def trained(tmp_path_factory) -> tuple[Path, dict]:
    """Entraîne un modèle de test sur 30 jours simulés, 5 stations, test sur 7 jours (EX-05)."""
    workdir = tmp_path_factory.mktemp("trained_model")
    data_path = workdir / "history.csv"
    simulate(days=30, n_stations=5, seed=42).to_csv(data_path, index=False)
    out = workdir / "models"
    meta = train(data_path, out, version="0.1.0-test", test_days=7, seed=42)
    return out, meta


@pytest.fixture
def client(model_dir, monkeypatch):
    monkeypatch.setenv("MODEL_DIR", str(model_dir))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from velov.api.main import app

    with TestClient(app) as c:  # le "with" déclenche le lifespan (chargement du modèle)
        yield c


@pytest.fixture
def valid_payload() -> dict:
    return {
        "station_id": 2,
        "timestamp": "2026-10-06T08:00:00+02:00",  # 8 h à Lyon, soit 6 h UTC
        "capacity": 20,
        "bikes_available": 12,
        "temperature": 14.5,
        "is_raining": False,
    }
