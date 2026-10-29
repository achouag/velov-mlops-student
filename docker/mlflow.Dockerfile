# syntax=docker/dockerfile:1
# Serveur MLflow du projet (S4) : suivi des runs + registre de modèles.
# L'image officielle ghcr.io/mlflow/mlflow n'embarque pas de pilote PostgreSQL :
# on construit la nôtre, versions épinglées, utilisateur non-root (checklist S2).
FROM python:3.12-slim
ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1 PYTHONUNBUFFERED=1 \
    MLFLOW_DISABLE_TELEMETRY=true
RUN pip install "mlflow==3.16.1" "psycopg[binary]==3.3.6" \
    && useradd --create-home --uid 10001 mlflow \
    && mkdir /mlartifacts && chown mlflow /mlartifacts
USER mlflow
EXPOSE 5000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/health', timeout=2)"]
CMD ["mlflow", "server", "--host", "0.0.0.0", "--port", "5000", "--workers", "2", \
     "--artifacts-destination", "/mlartifacts"]
