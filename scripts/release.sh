#!/usr/bin/env bash
# Script de livraison et de rollback d'un modèle sur la VM (S4 - EX-13, EX-14).
#
# Usage :
#   ./scripts/release.sh                    # Exporte et livre le @champion actuel
#   ./scripts/release.sh previous-champion  # Rollback vers l'ancien champion
#
set -euo pipefail

ALIAS="${1:-champion}"
echo "=== Livraison du modèle depuis le registre MLflow (alias: @${ALIAS}) ==="

# 1. Sélection de l'exécutable python
if [ -d ".venv" ]; then
    PYTHON=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
else
    PYTHON="python"
fi

# 2. Export du modèle depuis le registre MLflow
${PYTHON} -m velov.export --alias "${ALIAS}" --out models/

# 3. Extraction de la version et de l'empreinte git pour le tag d'image immuable
MODEL_VER=$(${PYTHON} -c "import json; print(json.load(open('models/metadata.json'))['model_version'])")
COMMIT_SHA=$(git rev-parse --short HEAD 2>/dev/null || echo "latest")
IMAGE_TAG="velov-api:m${MODEL_VER}-${COMMIT_SHA}"

echo "Construction de l'image Docker : ${IMAGE_TAG}"
docker build -t "${IMAGE_TAG}" -t velov-api:latest .

# 4. Redémarrage du conteneur API avec la nouvelle image
echo "Redémarrage du service API..."
if command -v docker-compose >/dev/null 2>&1; then
    docker-compose up -d --no-deps --build api
else
    docker compose up -d --no-deps --build api
fi

# 5. Contrôle de santé sur /ready
echo "Attente de disponibilité de l'API (/ready)..."
for i in {1..30}; do
    STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/ready 2>/dev/null || true)
    if [ "$STATUS" = "200" ]; then
        echo "Modèle livré et prêt à servir (HTTP 200) !"
        curl -s http://127.0.0.1:8000/v1/model | grep -o '"registry":{[^}]*}' || true
        exit 0
    fi
    sleep 1
done

echo "ERREUR : L'API n'a pas répondu 200 sur /ready après 30 s."
docker compose logs api
exit 1
