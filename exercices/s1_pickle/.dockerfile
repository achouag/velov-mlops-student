FROM python:latest

WORKDIR /app
COPY . .

RUN pip install fastapi uvicorn scikit-learn pandas joblib

ENV PYTHONPATH=/app/src
ENV MODEL_DIR=/app/models
ENV API_KEY=sk-prod-7f3a9c2e41b8

EXPOSE 8000
HEALTHCHECK CMD curl -f http://localhost:8000/health || exit 1
CMD uvicorn velov.api.main:app --host 0.0.0.0 --port 8000 --reload


# probleme notable, concernant le dockfile Api key en dur, 