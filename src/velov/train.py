"""Entraînement du modèle v0 et production de l'artefact distribuable.

Ce qu'on distribue (S1) n'est pas "un modèle" mais un paquet :
- le pipeline sklearn complet (prétraitement + modèle) -> models/model.joblib
- ses métadonnées (version, features, métriques, empreinte SHA-256, versions des
  librairies, hash des données) -> models/metadata.json

Usage :
    python -m velov.train --data data/velov_history.csv --out models/
    python -m velov.train --data data/velov_history.csv --out models/ --mlflow --seed 42
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import platform
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import joblib
import numpy as np
import pandas as pd
import pandera.pandas as pa
import sklearn
from pandera.typing import Series
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from velov.artifact import METADATA_FILENAME, MODEL_FILENAME, sha256_of
from velov.features import (
    CATEGORICAL_FEATURES,
    FEATURES,
    NUMERIC_FEATURES,
    RAW_COLUMNS,
    TARGET,
    make_training_frame,
)

logger = logging.getLogger("velov.train")


class StationHourly(pa.DataFrameModel):
    """Contrat de données Pandera pour la table d'entraînement (EX-11, EX-12)."""

    station_id: Series[int] = pa.Field(gt=0, coerce=True)
    timestamp: Series[pd.DatetimeTZDtype] = pa.Field(dtype_kwargs={"unit": "us", "tz": "UTC"})
    capacity: Series[int] = pa.Field(ge=1, le=80, coerce=True)
    bikes_available: Series[int] = pa.Field(ge=0, coerce=True)
    temperature: Series[float] = pa.Field(ge=-25, le=50, coerce=True)
    is_raining: Series[bool]

    @pa.dataframe_check
    def bikes_le_capacity(cls, df: pd.DataFrame) -> Series[bool]:
        return df["bikes_available"] <= df["capacity"]

    class Config:
        strict = True
        unique = ["station_id", "timestamp"]


def load_training_data(data_path: Path) -> pd.DataFrame:
    """Charge les données d'entraînement (CSV ou DuckDB) et valide le contrat Pandera."""
    if not data_path.exists():
        raise FileNotFoundError(f"{data_path} introuvable")

    if data_path.suffix == ".duckdb":
        con = duckdb.connect(str(data_path), read_only=True)
        raw = con.execute("select * from station_hourly order by station_id, timestamp").df()
        con.close()
    else:
        raw = pd.read_csv(data_path, parse_dates=["timestamp"])

    # Normalisation du fuseau en UTC us
    if raw["timestamp"].dt.tz is None:
        raise ValueError("timestamp doit être un datetime avec fuseau horaire explicite (UTC conseillé)")
    raw["timestamp"] = raw["timestamp"].dt.tz_convert("UTC")

    # Validation du contrat avant tout entraînement (EX-12)
    return StationHourly.validate(raw, lazy=True)


def compute_content_fingerprint(df: pd.DataFrame) -> str:
    """Calcule l'empreinte SHA-256 du contenu de la table trié de manière déterministe (EX-13)."""
    cols = [c for c in RAW_COLUMNS if c in df.columns]
    sorted_df = df[cols].sort_values(by=["station_id", "timestamp"]).reset_index(drop=True)
    csv_bytes = sorted_df.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(csv_bytes).hexdigest()


def build_pipeline(
    seed: int = 42,
    learning_rate: float = 0.1,
    max_iter: int = 200,
    max_leaf_nodes: int = 31,
) -> Pipeline:
    preprocess = ColumnTransformer(
        [
            ("station", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_FEATURES),
            ("num", "passthrough", NUMERIC_FEATURES),
        ]
    )
    model = HistGradientBoostingRegressor(
        max_iter=max_iter,
        learning_rate=learning_rate,
        max_leaf_nodes=max_leaf_nodes,
        random_state=seed,
    )
    return Pipeline([("preprocess", preprocess), ("model", model)])


def temporal_split(df: pd.DataFrame, test_days: int = 14) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Découpage temporel : on teste sur le futur, jamais sur un échantillon aléatoire."""
    ts = pd.to_datetime(df["timestamp"])
    total_duration = ts.max() - ts.min()
    if total_duration < pd.Timedelta(days=test_days):
        cutoff = ts.max() - max(pd.Timedelta(hours=6), total_duration * 0.2)
    else:
        cutoff = ts.max() - pd.Timedelta(days=test_days)

    train, test = df[ts <= cutoff], df[ts > cutoff]
    if train.empty or test.empty:
        raise ValueError("Split vide : augmenter l'historique ou réduire test_days")
    return train, test


def train(
    data_path: Path,
    out_dir: Path,
    version: str = "0.1.0",
    test_days: int = 14,
    seed: int = 42,
    learning_rate: float = 0.1,
    max_iter: int = 200,
    max_leaf_nodes: int = 31,
) -> dict:
    """Entraîne, évalue contre une baseline, écrit l'artefact. Retourne les métadonnées."""
    raw = load_training_data(data_path)
    data_fingerprint = compute_content_fingerprint(raw)
    data_start = raw["timestamp"].min().isoformat()
    data_end = raw["timestamp"].max().isoformat()

    frame = make_training_frame(raw)
    train_df, test_df = temporal_split(frame, test_days)

    pipeline = build_pipeline(
        seed=seed,
        learning_rate=learning_rate,
        max_iter=max_iter,
        max_leaf_nodes=max_leaf_nodes,
    )
    pipeline.fit(train_df[FEATURES], train_df[TARGET])

    y_test = test_df[TARGET]
    pred = np.clip(pipeline.predict(test_df[FEATURES]), 0, test_df["capacity"])
    mae_model = float(mean_absolute_error(y_test, pred))

    # Baseline "persistance" : dans 1 h, il y aura autant de vélos que maintenant.
    # Le modèle doit justifier sa complexité face à cette baseline (exigence EX-05).
    mae_baseline = float(mean_absolute_error(y_test, test_df["bikes_available"]))

    # MAE aux heures de pointe (7h-9h et 17h-19h heure de Lyon)
    local_hours = test_df["timestamp"].dt.tz_convert("Europe/Paris").dt.hour
    peak_mask = local_hours.isin([7, 8, 9, 17, 18, 19])
    if peak_mask.any():
        mae_peak = float(mean_absolute_error(y_test[peak_mask], pred[peak_mask]))
    else:
        mae_peak = mae_model

    out_dir.mkdir(parents=True, exist_ok=True)
    model_path = out_dir / MODEL_FILENAME
    joblib.dump(pipeline, model_path)

    metadata = {
        "model_name": "velov-availability",
        "model_version": version,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "task": "Prédire le nombre de vélos disponibles à H+1 par station",
        "input_columns": RAW_COLUMNS,
        "features": FEATURES,
        "target": TARGET,
        "params": {
            "seed": seed,
            "learning_rate": learning_rate,
            "max_iter": max_iter,
            "max_leaf_nodes": max_leaf_nodes,
            "test_days": test_days,
        },
        "metrics": {
            "mae_model": round(mae_model, 3),
            "mae_baseline_persistence": round(mae_baseline, 3),
            "mae_model_peak_hours": round(mae_peak, 3),
            "test_days": test_days,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
        },
        "artifact": {"file": MODEL_FILENAME, "sha256": sha256_of(model_path)},
        "data_sha256": data_fingerprint,
        "data_period": {
            "start": data_start,
            "end": data_end,
        },
        "runtime": {
            "python": platform.python_version(),
            "scikit-learn": sklearn.__version__,
            "pandas": pd.__version__,
            "numpy": np.__version__,
        },
    }
    (out_dir / METADATA_FILENAME).write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Entraîne le modèle Vélo'v avec traçabilité.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default="0.1.0")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--learning-rate", type=float, default=0.1)
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--max-leaf-nodes", type=int, default=31)
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--mlflow", action="store_true", help="journaliser le run dans MLflow")
    parser.add_argument("--register", action="store_true", help="enregistrer le modèle dans le registre MLflow")
    args = parser.parse_args()

    metadata = train(
        data_path=args.data,
        out_dir=args.out,
        version=args.version,
        test_days=args.test_days,
        seed=args.seed,
        learning_rate=args.learning_rate,
        max_iter=args.max_iter,
        max_leaf_nodes=args.max_leaf_nodes,
    )
    m = metadata["metrics"]
    print(f"MAE modèle        : {m['mae_model']:.3f} vélos")
    print(f"MAE persistance   : {m['mae_baseline_persistence']:.3f} vélos")
    print(f"MAE heures pointe : {m['mae_model_peak_hours']:.3f} vélos")
    print(f"Artefact          : {args.out / MODEL_FILENAME} (sha256 {metadata['artifact']['sha256'][:12]}...)")
    start_dt = metadata["data_period"]["start"][:10]
    end_dt = metadata["data_period"]["end"][:10]
    print(f"Empreinte data    : {metadata['data_sha256'][:12]}... ({start_dt} au {end_dt})")

    if args.mlflow or args.register:
        from velov.tracking import track_training

        pipeline = joblib.load(args.out / MODEL_FILENAME)
        raw = load_training_data(args.data)
        frame = make_training_frame(raw)
        train_df, _ = temporal_split(frame, args.test_days)

        run_id = track_training(
            pipeline=pipeline,
            train_df=train_df,
            metadata=metadata,
            out_dir=args.out,
            data_path=args.data,
            register=args.register,
        )
        print(f"Run journalisé dans MLflow (run_id: {run_id})")
        if args.register:
            print("Modèle enregistré dans le registre sous l'alias @challenger")


if __name__ == "__main__":
    main()
