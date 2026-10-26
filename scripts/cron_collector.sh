#!/usr/bin/env bash
# Script de boucle de collecte continue (micro-batch 5 min)
# À lancer en arrière-plan avec nohup ou tmux sur la VM :
#   nohup ./scripts/cron_collector.sh > /tmp/collector.log 2>&1 &

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_DIR}"

PYTHON_BIN="${PROJECT_DIR}/.venv/bin/python"
if [ ! -f "${PYTHON_BIN}" ]; then
    PYTHON_BIN="python3"
fi

echo "[$(date -u)] Démarrage du collecteur Vélo'v (intervalle : 300 s)..."

while true; do
    echo "[$(date -u)] Collecte des flux GBFS et météo..."
    "${PYTHON_BIN}" -m velov.ingest --bronze data/bronze || echo "[$(date -u)] Attention : échec lors du run de collecte"
    sleep 300
done
