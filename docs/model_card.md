# Model card : velov-availability

> Documentée selon Mitchell et al., "Model Cards for Model Reporting" (2019).
> Mise à jour : Session 4 (29/10/2026) - Traçabilité MLflow, promotion et CI.

## 1. Identité
- **Nom du modèle** : `velov-availability`
- **Version courante** : `0.1.0` (alias `@champion` dans le registre MLflow)
- **Tâche** : Régression supervisée - prédiction du nombre de vélos disponibles à H+1 par station
- **Date d'entraînement** : 2026-10-29
- **Responsables** : Binôme Adam & Parvin (Master M2 Big Data & IA)
- **Emplacement des artefacts** :
  - Paquet local / serving : `models/model.joblib` et `models/metadata.json`
  - Registre MLflow : `models:/velov-availability@champion` (serveur PostgreSQL/volume mlartifacts)
  - Image Docker immuable : `velov-api:m0.1.0-<commit_sha>`

## 2. Usage prévu
- **Ce que le modèle prédit** : Le nombre estimé de vélos disponibles dans 1 heure ($t + 1\text{ h}$) pour une station donnée.
- **Utilisateurs et décisions alimentées** : Usagers de l'application mobile Vélo'v (choix de déplacement) et régulateurs réseau (planification de rééquilibrage).
- **Usages hors périmètre** :
  - Ne prédit pas au-delà de $t + 1\text{ h}$ (pour $t+2\text{ h}$ ou plus, réentraîner un modèle dédié).
  - Ne prédit pas les places de stationnement libres directement (calculer `capacity - bikes_predicted`).
  - Ne prédit pas le type de vélo (mécanique vs électrique).

## 3. Données
- **Source** : Flux temps réel GBFS de la Métropole de Lyon (collecté en bronze, nettoyé en silver dbt, agrégé en gold DuckDB `station_hourly`).
- **Volume et période** : 90 jours d'historique (20 stations, 43 200 relevés horaires).
- **Découpage entraînement / test** : Découpage temporel strict (`temporal_split` sur les 14 derniers jours). Interdiction d'un découpage aléatoire (qui créerait une fuite temporelle majeure).
- **Empreinte des données** : SHA-256 déterministe du contenu tabulaire trié par `(station_id, timestamp)` enregistré dans le run MLflow et dans `metadata.json`.

## 4. Performance
| Métrique | Modèle v0.1.0 | Baseline (persistance) | Seuil d'acceptation |
|---|---|---|---|
| **MAE globale (vélos)** | **1.339** | 1.879 | < persistance (EX-05) |
| **MAE heures de pointe (7h-9h, 17h-19h)** | **1.415** | 2.050 | Non-dégradation |

### Analyse de variance et justification du seuil de promotion
- Entraînement avec seed 42 : MAE = 1.339 vélos (artefact sha256 `e0fc0cc3fda4...`)
- Entraînement avec seed 7 : MAE = 1.345 vélos (artefact sha256 `f47df8adb747...`)
- Écart mesuré lié uniquement à l'aléa de la graine : 0.006 vélo (soit ~0.45 % à 1.6 % selon l'historique).
- **Justification du seuil EX-14** : Le seuil de gain minimal exigé pour promouvoir un nouveau modèle champion face à l'ancien est fixé à **2 %** (`min_gain = 0.02`). Tout gain inférieur à 2 % est statistiquement indiscernable du bruit d'échantillonnage de la graine.

## 5. Contrat d'entrée / sortie
- **Entrée (Pydantic / `PredictionRequest`)** :
  - `station_id` : int > 0
  - `timestamp` : datetime avec fuseau horaire obligatoire (UTC)
  - `capacity` : int entre 1 et 80
  - `bikes_available` : int entre 0 et `capacity`
  - `temperature` : float entre -25 °C et 50 °C
  - `is_raining` : bool
- **Sortie (`PredictionResponse`)** :
  - `station_id` : int
  - `target_timestamp` : datetime UTC ($t + 1\text{ h}$)
  - `predicted_bikes` : float borné dans $[0, \text{capacity}]$
  - `model_version` : str (ex: `0.1.0`)

## 6. Risques et limites
- **Point-in-time correctness (EX-11)** : Aucune feature future n'est utilisée.
- **Données personnelles** : Aucune donnée à caractère personnel (RGPD conforme). Seuls des agrégats d'état de bornes publiques et météo sont collectés.
- **Dérive attendue** : Changement d'habitudes selon la météo extrême, travaux de voirie fermant une station.

## 7. Exploitation
- **Latence cible** : < 50 ms par requête (mesurée à ~47.9 ms).
- **Règle de promotion (EX-14)** :
  1. Évaluation sur la même fenêtre récente (14 jours).
  2. MAE inférieure à la baseline de persistance.
  3. MAE inférieure à celle du champion actuel d'au moins 2 % (`mae_challenger <= mae_champion * 0.98`).
- **Gestion des alias** : `@challenger` (candidat), `@champion` (production), `@previous-champion` (secours).
- **Fréquence de réentraînement** : Hebdomadaire (chaque lundi à 06h00 UTC via cron `python -m velov.retrain`).
- **Lien vers le runbook** : Voir `docs/runbook.md`.
