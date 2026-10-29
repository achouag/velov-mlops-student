"""Règle de promotion d'un modèle et gestion des alias dans le registre (EX-14).

Règle en trois conditions, vérifiées dans cet ordre strict (Slide 23) :
1. Même fenêtre : challenger et champion sont évalués sur la même fenêtre récente (ex. 14 derniers jours).
2. Battre la persistance : sinon le modèle ne justifie pas sa complexité (EX-05).
3. Battre le champion d'au moins 2 % : en dessous, l'écart est de l'ordre du bruit lié à la seed (1.6 %).

Alias gérés :
- @challenger : le modèle candidat ré-entraîné
- @champion : le modèle en production servi par l'API
- @previous-champion : l'ancien champion (pour retour arrière immédiat)
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error

from velov.features import FEATURES, TARGET, make_training_frame
from velov.train import load_training_data, temporal_split

logger = logging.getLogger("velov.promote")
MODEL_NAME = "velov-availability"


def decide(
    challenger_metrics: dict[str, float],
    champion_metrics: dict[str, float] | None,
    min_gain: float = 0.02,
) -> tuple[bool, str, float | None]:
    """Décide si le challenger doit être promu champion selon les 3 règles (EX-14).

    Returns:
        (promoted, reason, target_mae)
    """
    # Règle 2 : Battre la persistance (EX-05)
    c_mae = challenger_metrics["mae_model"]
    c_persist = challenger_metrics["mae_baseline_persistence"]
    if c_mae >= c_persist:
        return False, "ne bat pas la persistance", None

    # Règle 3 : Premier champion ou gain >= min_gain (2 %)
    if champion_metrics is None or "mae_model" not in champion_metrics:
        return True, "premier champion", None

    champ_mae = champion_metrics["mae_model"]
    target = champ_mae * (1.0 - min_gain)

    if c_mae <= target:
        gain_pct = (champ_mae - c_mae) / champ_mae * 100.0
        return True, f"gain de {gain_pct:.1f}% sur le champion (seuil {min_gain * 100:.0f}%)", round(target, 3)
    else:
        gain_pct = (champ_mae - c_mae) / champ_mae * 100.0
        return False, f"gain insuffisant sur le champion ({gain_pct:.1f}% < {min_gain * 100:.0f}%)", round(target, 3)


def evaluate_models_on_test_window(
    challenger_model: object,
    champion_model: object | None,
    test_df: pd.DataFrame,
) -> tuple[dict[str, float], dict[str, float] | None]:
    """Évalue challenger et champion sur la même fenêtre temporelle."""
    y_test = test_df[TARGET]
    X_test = test_df[FEATURES]
    cap = test_df["capacity"]

    # Challenger
    c_pred = np.clip(challenger_model.predict(X_test), 0, cap)
    c_mae = float(mean_absolute_error(y_test, c_pred))
    persist_mae = float(mean_absolute_error(y_test, test_df["bikes_available"]))

    challenger_metrics = {
        "mae_model": round(c_mae, 3),
        "mae_baseline_persistence": round(persist_mae, 3),
    }

    # Champion
    champion_metrics = None
    if champion_model is not None:
        champ_pred = np.clip(champion_model.predict(X_test), 0, cap)
        champ_mae = float(mean_absolute_error(y_test, champ_pred))
        champion_metrics = {
            "mae_model": round(champ_mae, 3),
            "mae_baseline_persistence": round(persist_mae, 3),
        }

    return challenger_metrics, champion_metrics


def promote(
    data_path: Path,
    model_name: str = MODEL_NAME,
    min_gain: float = 0.02,
    test_days: int = 14,
    apply: bool = False,
) -> bool:
    """Compare challenger et champion sur la même fenêtre et applique la promotion si demandée."""
    import mlflow
    from mlflow import MlflowClient

    if not os.environ.get("MLFLOW_TRACKING_URI"):
        os.environ["MLFLOW_TRACKING_URI"] = "sqlite:///mlflow.db"

    client = MlflowClient()

    # 1. Résolution de la version @challenger
    try:
        challenger_ver = client.get_model_version_by_alias(model_name, "challenger")
    except Exception:
        # Si aucun alias challenger n'est posé, prendre la dernière version
        versions = client.search_model_versions(f"name='{model_name}'")
        if not versions:
            print(f"Aucun modèle trouvé pour '{model_name}'.")
            return False
        challenger_ver = versions[0]

    # 2. Résolution du @champion existant
    champion_ver = None
    try:
        champion_ver = client.get_model_version_by_alias(model_name, "champion")
    except Exception as e:
        logger.debug("Aucun champion actuel trouvé dans le registre : %s", e)

    # 3. Chargement des données et extraction de la fenêtre de test
    raw = load_training_data(data_path)
    frame = make_training_frame(raw)
    _, test_df = temporal_split(frame, test_days)

    window_start = test_df["timestamp"].min().strftime("%Y-%m-%d %H:%M")
    window_end = test_df["timestamp"].max().strftime("%Y-%m-%d %H:%M")

    # 4. Chargement des pipelines sklearn
    challenger_model = mlflow.sklearn.load_model(f"models:/{model_name}/{challenger_ver.version}")
    champion_model = None
    if champion_ver is not None and champion_ver.version != challenger_ver.version:
        try:
            champion_model = mlflow.sklearn.load_model(f"models:/{model_name}/{champion_ver.version}")
        except Exception as e:
            logger.warning("Impossible de charger le champion actuel : %s", e)

    # 5. Évaluation sur la même fenêtre
    c_metrics, champ_metrics = evaluate_models_on_test_window(challenger_model, champion_model, test_df)

    promoted, reason, target = decide(c_metrics, champ_metrics, min_gain=min_gain)

    # 6. Affichage au format du cours (Slide 24)
    print(f"Challenger enregistré : version {challenger_ver.version} (run {challenger_ver.run_id[:8]})")
    print(f"Fenêtre de test       : {window_start} -> {window_end} UTC, {len(test_df)} lignes")
    print(f"challenger v{challenger_ver.version:<3}       : MAE {c_metrics['mae_model']:.3f}")
    if champion_ver and champ_metrics:
        print(f"champion v{champion_ver.version:<5}       : MAE {champ_metrics['mae_model']:.3f}")
    else:
        print("champion              : aucun")
    print(f"persistance           : MAE {c_metrics['mae_baseline_persistence']:.3f}")

    if promoted:
        if target is not None:
            pct = min_gain * 100
            print(f"Décision : PROMOUVOIR (MAE {c_metrics['mae_model']:.3f} <= {target:.3f} (champion - {pct:.0f}%))")
        else:
            print(f"Décision : PROMOUVOIR ({reason})")
    else:
        print(f"Décision : REFUSER ({reason})")

    # 7. Application si demandée
    if apply and promoted:
        if champion_ver and champion_ver.version != challenger_ver.version:
            client.set_registered_model_alias(model_name, "previous-champion", champion_ver.version)
            print(f"Ancien champion v{champion_ver.version} marqué @previous-champion")

        client.set_registered_model_alias(model_name, "champion", challenger_ver.version)
        client.set_model_version_tag(model_name, challenger_ver.version, "promotion_decision", "promoted")
        client.set_model_version_tag(model_name, challenger_ver.version, "promotion_reason", reason)
        client.set_model_version_tag(model_name, challenger_ver.version, "promoted_at", datetime.now(UTC).isoformat())
        client.set_model_version_tag(model_name, challenger_ver.version, "promoted_by", os.getenv("USER", "binome"))
        print(f"Version {challenger_ver.version} promue au statut @champion.")
    elif not apply and promoted:
        print("Pour valider la promotion dans le registre, relancez avec l'option --apply.")

    return promoted


def main() -> None:
    parser = argparse.ArgumentParser(description="Évalue et promeut un modèle candidat selon la règle en 3 conditions.")
    parser.add_argument("--data", type=Path, default=Path("data/velov_history.csv"))
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--min-gain", type=float, default=0.02)
    parser.add_argument("--test-days", type=int, default=14)
    parser.add_argument("--apply", action="store_true", help="appliquer la promotion dans le registre")
    args = parser.parse_args()

    promote(
        data_path=args.data,
        model_name=args.model,
        min_gain=args.min_gain,
        test_days=args.test_days,
        apply=args.apply,
    )


if __name__ == "__main__":
    main()
