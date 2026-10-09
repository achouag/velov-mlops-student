FROM python:3.12-slim

# Configuration de l'environnement d'exécution
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    MODEL_DIR=/app/models

WORKDIR /app

# Sécurité : création d'un utilisateur non-root dédié
RUN groupadd -r appgroup && useradd -r -g appgroup -d /app -s /sbin/nologin appuser

# Optimisation du cache des couches Docker : dépendances en amont
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copie du code source et des artefacts de modèle
COPY src/ /app/src/
COPY models/ /app/models/

# Droits stricts sur les fichiers de l'application
RUN chown -R appuser:appgroup /app

# Exécution sous l'utilisateur non-privilégié
USER appuser

EXPOSE 8000

# Healthcheck conforme EX-02 : vérifie la capacité réelle à prédire via /ready
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/ready')" || exit 1

# Commande de démarrage en forme exec (gestion propre des signaux POSIX / SIGTERM)
CMD ["uvicorn", "velov.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
