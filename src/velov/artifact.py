"""Constantes et fonctions utilitaires pour les artefacts de modèles (indépendantes de train.py).

Permet à l'API de serving d'importer le nom des fichiers et le calcul d'empreinte
sans dépendre des librairies d'entraînement (DuckDB, Pandera).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

MODEL_FILENAME = "model.joblib"
METADATA_FILENAME = "metadata.json"


def sha256_of(path: Path) -> str:
    """Calcule l'empreinte SHA-256 d'un fichier."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
