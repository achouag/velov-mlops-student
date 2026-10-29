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
| `tp1` | Schemas Pydantic, routes /health, /ready, /v1/predict |
| `tp2` | Dockerfile multi-stage non-root, Docker Compose avec PostgreSQL, journalisation des prédictions (EX-08) |
| `tp3` | Pipeline médaillon bronze/silver/gold avec DuckDB + dbt, contrats Pandera, point-in-time correctness (EX-10 à EX-12) |
| `tp4` | Stack MLflow, runs tracés, registre et promotion 3 règles (@champion/@previous-champion), CI GitHub Actions (EX-13 à EX-15) |

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

## MLOps : Traçabilité, Registre et CI/CD (S4 - TP4)

La session 4 met en place la traçabilité complète de l'entraînement, la gouvernance de déploiement par un registre d'artefacts MLflow, et la sécurisation par intégration continue.

### 1. Démarrer le serveur MLflow sur la VM
La stack MLflow s'exécute séparément de l'API avec une base PostgreSQL dédiée et un volume persistant d'artefacts (`mlartifacts`) :
```bash
# Démarrer le serveur MLflow (port 5000) et mlflow-db
docker compose -f compose.mlflow.yaml up -d --build
docker compose -f compose.mlflow.yaml ps
```
Interface accessible sur `http://192.168.1.2xx:5000` (ou en local avec `export MLFLOW_TRACKING_URI=sqlite:///mlflow.db`).

### 2. Tracer un entraînement
Chaque entraînement tracé via `velov.train --mlflow` enregistre :
- **Paramètres** : `seed`, `learning_rate`, `max_iter`, `max_leaf_nodes`, `test_days`.
- **Métriques** : `mae_model`, `mae_baseline_persistence`, `mae_model_peak_hours` (7h-9h et 17h-19h Europe/Paris).
- **Tags de contexte** : commit git immuable, indicateur `git_dirty`, empreinte SHA-256 du contenu de données triées (`data_sha256`), période couverte.
- **Artefacts** : signature d'inférence MLflow, format sécurisé `skops`, et paquet distribuable (`model.joblib` + `metadata.json`).

```bash
# Entraînement tracé avec seed 42
python -m velov.train --data data/velov_history.csv --out models/ --mlflow --seed 42

# Comparaison avec seed 7
python -m velov.train --data data/velov_history.csv --out models/ --mlflow --seed 7
```

#### Écart de MAE mesuré entre seeds (Justification du seuil EX-14) :
- **Seed 42** : MAE modèle = **1.339** vélos (persistance = 1.879 vélos, pic = 1.415 vélos).
- **Seed 7** : MAE modèle = **1.345** vélos (persistance = 1.879 vélos, pic = 1.427 vélos).
- **Écart mesuré** : 0.006 vélo (+0.45 % à 1.6 % selon l'échantillon).
- **Conclusion** : Cet écart dû au hasard seul justifie le seuil strict de **2 %** imposé dans `velov.promote` : un modèle ne peut détrôner le champion que si son amélioration dépasse ce niveau de bruit.

### 3. Registre de modèles et règle de promotion (EX-14)
Règle en trois conditions :
1. Évaluation sur la même fenêtre récente (14 jours).
2. Le challenger bat la baseline de persistance.
3. Le challenger bat le champion d'au moins 2 % (`mae_challenger <= mae_champion * 0.98`).

```bash
# Entraîner et enregistrer un challenger
python -m velov.retrain --data data/velov_history.csv --version 0.2.0

# Évaluer la promotion (lecture seule)
python -m velov.promote

# Valider la promotion dans le registre (déplace @champion et archive @previous-champion)
python -m velov.promote --apply
```

### 4. Livraison et Rollback sur la VM (ADR-0003)
L'image Docker ne dépend pas de MLflow : elle résout l'alias `@champion` avant la livraison et vérifie l'empreinte SHA-256 de l'artefact :
```bash
# Livrer le champion courant
./scripts/release.sh

# Revenir en arrière en cas d'anomalie
./scripts/release.sh previous-champion

# Vérifier la version et le run MLflow servis en production (EX-13)
curl -s http://127.0.0.1:8000/v1/model | jq .registry
```

### 5. Intégration Continue (CI GitHub Actions - EX-15)
Le workflow `.github/workflows/ci.yml` s'exécute à chaque push et pull request :
- `tests` : Linter `ruff`, format check, pytest (incluant `test_model_quality.py`).
- `secrets` : Scan `gitleaks` sur l'ensemble de l'historique git (`fetch-depth: 0`) avec binaire validé par SHA-256.
- `image` : Construction de l'image Docker avec un modèle de test, instanciation et validation du healthcheck `/ready` (HTTP 200).

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
