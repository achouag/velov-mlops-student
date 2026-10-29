"""Tests de qualité de modèle, reproductibilité, métadonnées et règle de promotion (EX-05, EX-13, EX-14)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from velov.artifact import MODEL_FILENAME, sha256_of
from velov.data import simulate
from velov.export import export_model
from velov.promote import decide, promote
from velov.tracking import track_training
from velov.train import train


def test_model_beats_persistence_baseline(trained):
    """EX-05 : Le modèle doit battre la persistance sur le jeu de test temporel."""
    _, metadata = trained
    m = metadata["metrics"]
    assert m["mae_model"] < m["mae_baseline_persistence"], f"Régression du modèle : {m}"


def test_promotion_rule():
    """EX-14 : La règle en trois conditions doit être respectée dans tous les cas."""
    # Cas 1 : Premier champion (aucun champion existant) -> True
    c_ok = {"mae_model": 1.40, "mae_baseline_persistence": 1.90}
    promoted, reason, _ = decide(c_ok, champion_metrics=None, min_gain=0.02)
    assert promoted is True
    assert "premier champion" in reason

    # Cas 2 : Challenger bat le champion de plus de 2 % -> True
    champ = {"mae_model": 1.50, "mae_baseline_persistence": 1.90}
    c_better = {"mae_model": 1.35, "mae_baseline_persistence": 1.90}
    promoted, reason, target = decide(c_better, champion_metrics=champ, min_gain=0.02)
    assert promoted is True
    assert target == pytest.approx(1.47, abs=1e-3)
    assert "gain de" in reason

    # Cas 3 : Gain trop faible (< 2 %) pour justifier le changement (bruit de seed) -> False
    c_slight = {"mae_model": 1.485, "mae_baseline_persistence": 1.90}
    promoted, reason, target = decide(c_slight, champion_metrics=champ, min_gain=0.02)
    assert promoted is False
    assert "gain insuffisant" in reason

    # Cas 4 : Challenger pire que la persistance -> False
    c_worse_than_baseline = {"mae_model": 2.10, "mae_baseline_persistence": 1.90}
    promoted, reason, _ = decide(c_worse_than_baseline, champion_metrics=champ, min_gain=0.02)
    assert promoted is False
    assert "ne bat pas la persistance" in reason


def test_reproducibility(tmp_path: Path):
    """Même code, mêmes données et même seed produisent un artefact identique au bit près."""
    data_path = tmp_path / "sim.csv"
    simulate(days=15, n_stations=3, seed=42).to_csv(data_path, index=False)

    out1 = tmp_path / "run1"
    out2 = tmp_path / "run2"

    meta1 = train(data_path, out1, version="0.1.0", seed=42, test_days=3)
    meta2 = train(data_path, out2, version="0.1.0", seed=42, test_days=3)

    sha1 = sha256_of(out1 / MODEL_FILENAME)
    sha2 = sha256_of(out2 / MODEL_FILENAME)

    assert sha1 == sha2, "L'artefact n'est pas reproductible avec la même seed"
    assert meta1["artifact"]["sha256"] == meta2["artifact"]["sha256"]
    assert meta1["metrics"]["mae_model"] == meta2["metrics"]["mae_model"]


def test_metadata_contains_params_and_fingerprint(trained):
    """EX-13 : metadata.json doit consigner les paramètres, l'empreinte et la période des données."""
    _, metadata = trained
    # Paramètres
    assert "params" in metadata
    params = metadata["params"]
    assert params["seed"] == 42
    assert params["learning_rate"] == 0.1
    assert params["max_iter"] == 200
    assert params["max_leaf_nodes"] == 31
    assert params["test_days"] == 7

    # Empreinte et période
    assert "data_sha256" in metadata
    assert len(metadata["data_sha256"]) == 64  # SHA-256 complet
    assert "data_period" in metadata
    assert "start" in metadata["data_period"]
    assert "end" in metadata["data_period"]


def test_training_rejects_missing_timezone(tmp_path: Path):
    """Le pipeline refuse d'entraîner sur des données sans fuseau horaire explicite."""
    data_path = tmp_path / "naive.csv"
    df = simulate(days=10, n_stations=2, seed=0)
    # Suppression volontaire du fuseau horaire
    df["timestamp"] = df["timestamp"].astype(str).str.replace("+00:00", "").str.replace("Z", "")
    df.to_csv(data_path, index=False)

    out = tmp_path / "models"
    with pytest.raises(ValueError):
        train(data_path, out, version="0.1.0", test_days=2)


def test_tracking_and_registry_lifecycle(tmp_path: Path, monkeypatch):
    """Teste le cycle de vie MLflow local : run tracé, challenger, promotion et export."""
    db_file = tmp_path / "mlflow_test.db"
    monkeypatch.setenv("MLFLOW_TRACKING_URI", f"sqlite:///{db_file}")

    data_path = tmp_path / "data.csv"
    simulate(days=25, n_stations=4, seed=42).to_csv(data_path, index=False)
    out_dir = tmp_path / "models"

    # 1. Entraîner et enregistrer version 1
    meta1 = train(data_path, out_dir, version="0.1.0", test_days=5, seed=42)
    import joblib

    pipeline = joblib.load(out_dir / MODEL_FILENAME)
    raw = pd.read_csv(data_path, parse_dates=["timestamp"])
    from velov.features import make_training_frame
    from velov.train import temporal_split

    frame = make_training_frame(raw)
    train_df, _ = temporal_split(frame, test_days=5)

    run_id1 = track_training(
        pipeline=pipeline,
        train_df=train_df,
        metadata=meta1,
        out_dir=out_dir,
        data_path=data_path,
        register=True,
        model_name="velov-availability",
    )
    assert run_id1 is not None

    # 2. Promouvoir v1 en tant que premier champion
    promoted1 = promote(data_path, model_name="velov-availability", test_days=5, apply=True)
    assert promoted1 is True

    # 3. Exporter le champion
    export_dir = tmp_path / "exported_champion"
    exported_meta = export_model(alias="champion", out_dir=export_dir, model_name="velov-availability")
    assert (export_dir / MODEL_FILENAME).exists()
    assert "registry" in exported_meta
    assert exported_meta["registry"]["alias"] == "champion"
    assert exported_meta["registry"]["version"] == "1"
