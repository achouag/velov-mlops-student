"""API de serving du modèle Vélo'v.

TP1, partie 3 : exposez le modèle. Mode : IA déclarée autorisée pour cette partie.

Endpoints attendus (niveaux du TP1 : Must, Should, Stretch) :
    POST /v1/predict        [Must]    une prédiction
    GET  /health            [Should]  liveness : le process répond (ne dépend pas du modèle)
    GET  /ready             [Should]  readiness : 200 si le modèle est chargé, 503 sinon
    GET  /v1/model, POST /v1/predict/batch   [Stretch]

Lancement :
    uvicorn velov.api.main:app --reload
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from datetime import timedelta
import pandas as pd
import numpy as np
import joblib
from fastapi import FastAPI, HTTPException

from velov.api.db import init_db, log_prediction
from velov.api.schemas import PredictionRequest, PredictionResponse  # noqa: F401
from velov.features import FEATURES, add_features  # noqa: F401
from velov.train import METADATA_FILENAME, sha256_of

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("velov.api")

STATE: dict = {"model": None, "metadata": None}


def load_model(model_dir: Path) -> tuple[object, dict]:
    """Fourni : charge le modèle APRÈS avoir vérifié son empreinte SHA-256."""
    metadata_path = model_dir / METADATA_FILENAME
    if not metadata_path.exists():
        raise FileNotFoundError(f"{metadata_path} introuvable")
    metadata = json.loads(metadata_path.read_text())
    model_path = model_dir / metadata["artifact"]["file"]
    if sha256_of(model_path) != metadata["artifact"]["sha256"]:
        raise RuntimeError(f"Empreinte invalide pour {model_path}")
    return joblib.load(model_path), metadata


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Fourni : exécuté une fois au démarrage (avant yield) et à l'arrêt (après yield)."""
    model_dir = Path(os.getenv("MODEL_DIR", "models"))
    try:
        STATE["model"], STATE["metadata"] = load_model(model_dir)
        logger.info("Modèle %s chargé", STATE["metadata"]["model_version"])
    except Exception:
        logger.exception("Échec du chargement du modèle depuis %s", model_dir)

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            init_db(db_url)
        except Exception:
            logger.exception("Échec de l'initialisation de la base de données")
    yield
    STATE.update(model=None, metadata=None)


app = FastAPI(title="Vélo'v availability API", version="1.0.0", lifespan=lifespan)


# TODO 5 [Should] : GET /health -> {"status": "ok"}
@app.get("/health")
def health():
    return {"status": "ok"} 

# TODO 6 [Should] : GET /ready -> 200 + version du modèle si chargé, sinon HTTPException 503
@app.get("/ready")
def ready():
    model = STATE["model"]
    metadata = STATE["metadata"]
    if model is None or metadata is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")
    return {"status": "ok", "model_version": metadata["model_version"]}

# TODO 7 [Must] : POST /v1/predict
@app.post("/v1/predict", response_model=PredictionResponse)
def predict(payload: PredictionRequest):
    # 1. Vérifier si le modèle est chargé
    model = STATE["model"]
    metadata = STATE["metadata"]
    if model is None or metadata is None:
        raise HTTPException(status_code=503, detail="Modèle non chargé")

    # 2. Convertir les données d'entrée en DataFrame Pandas (1 seule ligne)
    df = pd.DataFrame([payload.model_dump()])

    # 3. Calculer les features et garder uniquement celles attendues par le modèle
    df_features = add_features(df)
    X = df_features[FEATURES]

    # 4. Prédire et borner entre 0 et la capacité de la station
    raw_pred = model.predict(X)[0]
    predicted = float(np.clip(raw_pred, 0, payload.capacity))

    # 5. Calculer le timestamp dans 1 heure (t + 1h)
    target_timestamp = payload.timestamp + timedelta(hours=1)

    # 6. Renvoyer la réponse au format PredictionResponse et journaliser
    response = PredictionResponse(
        station_id=payload.station_id,
        target_timestamp=target_timestamp,
        predicted_bikes=predicted,
        model_version=metadata["model_version"],
    )

    db_url = os.getenv("DATABASE_URL")
    if db_url:
        try:
            log_prediction(db_url, payload, response)
        except Exception:
            logger.exception("Échec de journalisation de la prédiction en base")

    return response

#   - entrée : PredictionRequest ; sortie : PredictionResponse
#   - construire un DataFrame d'une ligne, appliquer add_features, sélectionner FEATURES
#   - prédire, borner entre 0 et capacity, target_timestamp = timestamp + 1 h
#     (l'instant porte son fuseau : le contrat l'a validé)
#   - 503 si le modèle n'est pas chargé
#   Question : pourquoi importer add_features plutôt que recalculer les features ici ?
