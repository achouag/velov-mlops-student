"""Réentraînement automatisé et évaluation continue (Continuous Training - S4).

Exécute le pipeline d'entraînement complet, enregistre une version @challenger dans MLflow,
compare le modèle au @champion actuel sur la fenêtre de test, et affiche la décision de promotion.

Usage (ex. dans cron chaque lundi) :
    python -m velov.retrain --data data/velov.duckdb --version 0.2.0
    python -m velov.retrain --data data/velov.duckdb --version 0.3.0 --auto-promote
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime
from pathlib import Path

import joblib

from velov.artifact import MODEL_FILENAME
from velov.features import make_training_frame
from velov.promote import promote
from velov.tracking import track_training
from velov.train import load_training_data, temporal_split, train

logger = logging.getLogger("velov.retrain")


def retrain(
    data_path: Path,
    out_dir: Path = Path("models"),
    version: str | None = None,
    seed: int = 42,
    learning_rate: float = 0.1,
    max_iter: int = 200,
    max_leaf_nodes: int = 31,
    test_days: int = 14,
    auto_promote: bool = False,
    min_gain: float = 0.02,
) -> bool:
    """Entraîne une nouvelle version, l'enregistre en tant que challenger, et évalue sa promotion."""
    if version is None:
        version = datetime.now(UTC).strftime("%Y.%m.%d")

    print(f"=== Début du réentraînement v{version} ===")
    metadata = train(
        data_path=data_path,
        out_dir=out_dir,
        version=version,
        test_days=test_days,
        seed=seed,
        learning_rate=learning_rate,
        max_iter=max_iter,
        max_leaf_nodes=max_leaf_nodes,
    )

    # Enregistrement dans MLflow avec alias @challenger
    pipeline = joblib.load(out_dir / MODEL_FILENAME)
    raw = load_training_data(data_path)
    frame = make_training_frame(raw)
    train_df, _ = temporal_split(frame, test_days)

    run_id = track_training(
        pipeline=pipeline,
        train_df=train_df,
        metadata=metadata,
        out_dir=out_dir,
        data_path=data_path,
        register=True,
    )
    print(f"Challenger v{version} enregistré (run {run_id[:8]})")

    # Évaluation de la promotion face au champion actuel
    print("\n=== Évaluation de la règle de promotion ===")
    promoted = promote(
        data_path=data_path,
        min_gain=min_gain,
        test_days=test_days,
        apply=auto_promote,
    )
    return promoted


def main() -> None:
    parser = argparse.ArgumentParser(description="Réentraînement continu du modèle Vélo'v.")
    default_data = Path("data/velov.duckdb") if Path("data/velov.duckdb").exists() else Path("data/velov_history.csv")
    parser.add_argument("--data", type=Path, default=default_data)
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--version", default=None, help="version du modèle (défaut : YYYY.MM.DD)")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--learning-rate", type=float, default=0.1)
    parser.add_argument("--max-iter", type=int, default=200)
    parser.add_argument("--max-leaf-nodes", type=int, default=31)
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument(
        "--auto-promote", action="store_true", help="appliquer la promotion automatiquement si éligible"
    )
    parser.add_argument("--min-gain", type=float, default=0.02)
    args = parser.parse_args()

    retrain(
        data_path=args.data,
        out_dir=args.out,
        version=args.version,
        seed=args.seed,
        learning_rate=args.learning_rate,
        max_iter=args.max_iter,
        max_leaf_nodes=args.max_leaf_nodes,
        test_days=args.test_days,
        auto_promote=args.auto_promote,
        min_gain=args.min_gain,
    )


if __name__ == "__main__":
    main()
