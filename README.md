# velov-mlops

Projet fil rouge du cours **Industrialisation de l'IA dans le Cloud** (M2 Data Engineering / IA).

Objectif métier : prédire, pour chaque station Vélo'v, le nombre de vélos disponibles **dans une heure**,
et servir cette prédiction via une API fiable, conteneurisée, puis déployée (on-premise et cloud).

## Progression par session

Chaque étape du cours correspond à un tag git. Absent ou bloqué : repartez du tag de fin de la session précédente.

| Tag | Contenu |
|---|---|
| `s1-start` | Données simulées, features, entraînement. API à compléter (TP1) |
| `s1-end` | Modèle v0 servi par FastAPI, tests pytest |

```bash
git checkout s1-end          # récupérer l'état de fin de S1
git checkout -b mon-binome   # travailler sur sa propre branche
```

## Démarrage rapide

Prérequis : Python 3.11+ (3.12 recommandé), git. Docker sera nécessaire en S2.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
pip install -e .                     # rend le package velov importable

python -m velov.data                 # génère data/velov_history.csv (simulateur, seed fixe)
python -m velov.train                # entraîne et écrit models/model.joblib + models/metadata.json
python -m velov.train --mlflow       # idem + journalisation MLflow (puis : mlflow ui)
pytest                               # tests
uvicorn velov.api.main:app --reload  # API sur http://127.0.0.1:8000/docs (--reload : en dev uniquement)
```

Exemple d'appel :

```bash
curl -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -d '{"station_id": 3, "timestamp": "2026-10-06T08:00:00+02:00", "capacity": 20,
       "bikes_available": 12, "temperature": 14.5, "is_raining": false}'
```

Le `timestamp` doit porter un fuseau (`+02:00`, `Z`...) : sans fuseau, l'API répond 422.
Les instants sont renvoyés et journalisés en UTC (`"target_timestamp": "2026-10-06T07:00:00Z"`).

## Pipeline de données (S3)

L'architecture médaillon ELT remplace les données simulées par les flux réels de la Métropole de Lyon (GBFS) et Open-Meteo :
- **Bronze (`data/bronze/`)** : Instantanés JSON bruts, partitionnés par `dt=YYYY-MM-DD/<key>.json`. Ingestion idempotente et atomique via `src/velov/ingest.py`.
- **Silver (`velov_dbt/models/staging/`)** : Vues SQL sous DuckDB (`stg_station_information`, `stg_station_status`, `stg_weather`), typées, unnest, passées en UTC et dédoublonnées via `qualify row_number()`.
- **Gold (`velov_dbt/models/marts/station_hourly`)** : Table d'entraînement horaire sous contrat strict (`contract: enforced: true`), respectant la règle temporelle $]h - 1\text{ h}, h]$ sans fuite (EX-11, EX-12).

### Exécuter le pipeline :

```bash
# 1. Ingestion bronze (flux en direct ou démo 48h)
python -m velov.ingest --bronze data/bronze
# Pour charger le jeu de démo 48h (576 instantanés, 20 stations) :
python -m velov.ingest --seed-demo

# 2. Modèles dbt et tests de contrat sous DuckDB
cd velov_dbt
dbt build --profiles-dir .
cd ..

# 3. Entraîner le modèle sur la table gold avec validation Pandera
python -m velov.train --data data/velov.duckdb --out models/
```

## Exigences du projet

Le service doit respecter les exigences de [docs/exigences.md](docs/exigences.md), de S1 à S9.
Chaque rendu indique celles qu'il couvre et comment le vérifier (notamment EX-10 à EX-12 pour le pipeline S3).


## Structure

```
src/velov/
  data.py          simulateur de données (remplacé par l'open data en S3, même schéma)
  features.py      features partagées entraînement / API (anti training-serving skew)
  train.py         entraînement, baseline, artefact + metadata.json, option MLflow
  api/main.py      FastAPI : /health, /ready, /v1/model, /v1/predict, /v1/predict/batch
  api/schemas.py   contrat d'entrée / sortie (Pydantic)
tests/             pytest (features, API)
docs/              templates : model card, runbook, ADR
exercices/         démo pickle (S1)
```

## Usage de l'IA générative

Chaque activité indique son mode : **sans IA**, **IA déclarée** ou **IA imposée**.
En mode IA déclarée, ajoutez dans la description de vos commits ou de votre rendu :
outil utilisé, ce que vous lui avez demandé, ce que vous avez vérifié ou corrigé.
