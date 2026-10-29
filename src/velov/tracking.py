"""Traçabilité des entraînements avec MLflow (Session 4 - EX-13).

Ce module centralise les interactions avec MLflow lors de l'entraînement.
Il n'est JAMAIS importé par l'image de serving (découplage strict).

Chaque run tracé porte :
- Paramètres : seed, learning_rate, max_iter, max_leaf_nodes, test_days
- Métriques de test : mae_model, mae_baseline_persistence, mae_model_peak_hours
- Tags : commit git, git_dirty, empreinte des données (SHA-256 du contenu), période couverte
- Artefacts : le modèle avec sa signature, et le paquet model.joblib + metadata.json
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
from pathlib import Path

import pandas as pd
from sklearn.pipeline import Pipeline

from velov.artifact import MODEL_FILENAME
from velov.features import FEATURES, RAW_COLUMNS

logger = logging.getLogger("velov.tracking")

SKOPS_TRUSTED_TYPES = [
    "sklearn.ensemble._hist_gradient_boosting.predictor.TreePredictor",
]
MODEL_NAME = "velov-availability"


def git_state() -> dict[str, str]:
    """Récupère l'empreinte git du commit actuel et signale si le repo a des modifications non commitées."""
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],  # noqa: S603, S607
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except Exception:
        commit = "unknown"

    try:
        dirty_out = subprocess.check_output(
            ["git", "status", "--porcelain"],  # noqa: S603, S607
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        dirty = "true" if dirty_out else "false"
    except Exception:
        dirty = "unknown"

    return {"git_commit": commit, "git_dirty": dirty}


def compute_data_fingerprint(df: pd.DataFrame) -> str:
    """Calcule une empreinte SHA-256 du contenu de la table triée par station_id et timestamp.

    Le SHA-256 d'un fichier SQLite/DuckDB change dès que la base est réécrite.
    On hache donc le contenu tabulaire trié de manière reproductible.
    """
    cols = [c for c in RAW_COLUMNS if c in df.columns]
    sorted_df = df[cols].sort_values(by=["station_id", "timestamp"]).reset_index(drop=True)
    csv_bytes = sorted_df.to_csv(index=False).encode("utf-8")
    return hashlib.sha256(csv_bytes).hexdigest()


def track_training(
    pipeline: Pipeline,
    train_df: pd.DataFrame,
    metadata: dict,
    out_dir: Path,
    data_path: Path,
    register: bool = False,
    model_name: str = MODEL_NAME,
) -> str:
    """Journalise un run d'entraînement dans MLflow et enregistre éventuellement le modèle dans le registre."""
    import mlflow
    from mlflow import MlflowClient
    from mlflow.models import infer_signature

    # Fallback sqlite local si aucun serveur n'est configuré
    if not os.environ.get("MLFLOW_TRACKING_URI"):
        os.environ["MLFLOW_TRACKING_URI"] = "sqlite:///mlflow.db"

    mlflow.set_experiment(model_name)
    run_name = f"v{metadata.get('model_version', '0.1.0')}"

    with mlflow.start_run(run_name=run_name) as run:
        # 1. Paramètres
        params = metadata.get("params", {})
        if params:
            mlflow.log_params(params)

        # 2. Métriques sur le test temporel
        metrics = metadata.get("metrics", {})
        maes = {k: v for k, v in metrics.items() if k.startswith("mae")}
        if maes:
            mlflow.log_metrics(maes)

        # 3. Tags de contexte
        tags = {
            **git_state(),
            "data_sha256": metadata.get("data_sha256", ""),
            "data_start": metadata.get("data_period", {}).get("start", ""),
            "data_end": metadata.get("data_period", {}).get("end", ""),
            "model_version": metadata.get("model_version", ""),
        }
        mlflow.set_tags(tags)

        # 4. Données d'entraînement (dataset)
        try:
            dataset = mlflow.data.from_pandas(train_df, name="station_hourly", source=str(data_path))
            mlflow.log_input(dataset, context="training")
        except Exception as e:
            logger.warning("Échec de log_input dataset dans MLflow : %s", e)

        # 5. Modèle scikit-learn avec signature et skops
        sample = train_df[FEATURES].head(2)
        sample_pred = pipeline.predict(sample)
        sig = infer_signature(sample, sample_pred)

        mlflow.sklearn.log_model(
            pipeline,
            name="model",
            signature=sig,
            input_example=sample,
            serialization_format="skops",
            skops_trusted_types=SKOPS_TRUSTED_TYPES,
            registered_model_name=model_name if register else None,
        )

        # 6. Paquet de serving (model.joblib + metadata.json) en tant qu'artefact
        if (out_dir / MODEL_FILENAME).exists():
            mlflow.log_artifacts(str(out_dir), artifact_path="package")

        run_id = run.info.run_id

    # 7. Si le modèle a été enregistré dans le registre, poser l'alias challenger
    if register:
        client = MlflowClient()
        try:
            # Récupération de la version qui vient d'être enregistrée pour ce run
            versions = client.search_model_versions(f"name='{model_name}' and run_id='{run_id}'")
            if versions:
                new_version = versions[0].version
                client.set_registered_model_alias(model_name, "challenger", new_version)
                logger.info("Modèle %s v%s marqué avec l'alias @challenger", model_name, new_version)
        except Exception as e:
            logger.warning("Échec de l'assignation de l'alias challenger : %s", e)

    return run_id
