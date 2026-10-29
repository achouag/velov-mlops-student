"""Export d'un modèle depuis le registre MLflow vers le paquet de serving (EX-13, EX-14).

Résout l'alias du registre (ex: @champion ou @previous-champion) avant le build Docker,
télécharge le paquet (model.joblib + metadata.json), enrichit metadata.json avec les
informations du registre (.registry), et vérifie l'empreinte SHA-256.

Usage :
    python -m velov.export --alias champion --out models/
    python -m velov.export --alias previous-champion --out models/
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from velov.artifact import METADATA_FILENAME, MODEL_FILENAME, sha256_of

logger = logging.getLogger("velov.export")
MODEL_NAME = "velov-availability"


def export_model(
    alias: str = "champion",
    out_dir: Path = Path("models"),
    model_name: str = MODEL_NAME,
) -> dict:
    """Télécharge l'artefact correspondant à l'alias dans out_dir et enrichit ses métadonnées."""
    from mlflow import MlflowClient

    if not os.environ.get("MLFLOW_TRACKING_URI"):
        os.environ["MLFLOW_TRACKING_URI"] = "sqlite:///mlflow.db"

    client = MlflowClient()

    # 1. Résolution de la version par alias
    try:
        ver = client.get_model_version_by_alias(model_name, alias)
    except Exception as e:
        raise RuntimeError(f"Impossible de trouver le modèle '{model_name}' avec l'alias '@{alias}' : {e}") from e

    out_dir.mkdir(parents=True, exist_ok=True)

    # 2. Téléchargement des artefacts du paquet depuis MLflow
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_path = Path(tmp_dir)
        try:
            # On tente de télécharger le dossier "package" loggé par tracking.py
            downloaded = client.download_artifacts(run_id=ver.run_id, path="package", dst_path=str(tmp_path))
            pkg_path = Path(downloaded)
            if (pkg_path / MODEL_FILENAME).exists():
                shutil.copy(pkg_path / MODEL_FILENAME, out_dir / MODEL_FILENAME)
                shutil.copy(pkg_path / METADATA_FILENAME, out_dir / METADATA_FILENAME)
            else:
                raise FileNotFoundError("Artefacts 'package' incomplets dans le run")
        except Exception as e:
            logger.warning("Échec téléchargement artefacts package (%s), tentative via URI de modèle...", e)
            # Fallback : charger via models:/{model_name}/{ver.version}
            import mlflow

            pipeline = mlflow.sklearn.load_model(f"models:/{model_name}/{ver.version}")
            import joblib

            joblib.dump(pipeline, out_dir / MODEL_FILENAME)
            # Générer un metadata de fallback si manquant
            fallback_meta = {
                "model_name": model_name,
                "model_version": f"v{ver.version}",
                "artifact": {"file": MODEL_FILENAME, "sha256": sha256_of(out_dir / MODEL_FILENAME)},
            }
            (out_dir / METADATA_FILENAME).write_text(json.dumps(fallback_meta, indent=2), encoding="utf-8")

    # 3. Enrichissement de metadata.json avec le bloc .registry (EX-13)
    meta_path = out_dir / METADATA_FILENAME
    metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    metadata["registry"] = {
        "name": model_name,
        "version": str(ver.version),
        "alias": alias,
        "run_id": str(ver.run_id),
        "exported_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    meta_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")

    # 4. Vérification de sécurité de l'empreinte SHA-256 (EX-02)
    model_path = out_dir / MODEL_FILENAME
    calc_sha = sha256_of(model_path)
    expected_sha = metadata["artifact"]["sha256"]
    if calc_sha != expected_sha:
        raise RuntimeError(f"Empreinte corrompue pour {model_path} : calculée {calc_sha} != attendue {expected_sha}")

    print(f"{model_name} v{ver.version} (@{alias}, run {ver.run_id[:8]}) exporté dans {out_dir}")
    return metadata


def main() -> None:
    parser = argparse.ArgumentParser(description="Exporte un modèle du registre MLflow vers le dossier de serving.")
    parser.add_argument(
        "--alias", default="champion", help="alias du modèle à exporter (ex: champion, previous-champion)"
    )
    parser.add_argument("--out", type=Path, default=Path("models"))
    parser.add_argument("--model", default=MODEL_NAME)
    args = parser.parse_args()

    export_model(alias=args.alias, out_dir=args.out, model_name=args.model)


if __name__ == "__main__":
    main()
