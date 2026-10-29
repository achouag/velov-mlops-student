# Runbook : API velov-availability

> Template à compléter au fil des sessions. Public cible : une personne d'astreinte
> qui ne connaît pas le projet et doit rétablir le service à 3 h du matin.

## 1. Vue d'ensemble
- Ce que fait le service, en deux phrases :
- Schéma d'architecture (lien) :
- Dépendances (base de données, stockage du modèle...) :

## 2. Démarrer / arrêter
```bash
# démarrer
# arrêter
# redémarrer un seul service
```

## 3. Vérifier que tout va bien
| Vérification | Commande | Résultat attendu |
|---|---|---|
| Le process répond | `curl -s http://<hôte>:8000/health` | `{"status":"ok"}` |
| Le modèle est chargé | `curl -s http://<hôte>:8000/ready` | HTTP 200 + version |
| | | |

## 4. Incidents connus
| Symptôme | Cause probable | Diagnostic | Action |
|---|---|---|---|
| `/ready` renvoie 503 | Modèle absent ou corrompu | Vérifier les logs : `docker compose logs api` | Réentraîner (`python -m velov.train`) ou restaurer `models/model.joblib` |
| La collecte s'arrête / Bronze ne grossit plus | Cron arrêté ou flux Grand Lyon indisponible | Vérifier la date du dernier JSON : `ls -l data/bronze/station_status/*/*.json \| tail -n 5` | Relancer manuellement `python -m velov.ingest`, vérifier la connectivité curl vers `https://download.data.grandlyon.com` |
| Échec `dbt build` (verrou DuckDB) | Base ouverte en écriture par un autre process | Vérifier les processus actifs : `lsof data/velov.duckdb` | S'assurer que les notebooks ou APIs ouvrent DuckDB en `read_only=True` |
| Anomalie de capteur bloquante sur gold | Contrat de données dbt violé | Lire l'erreur dbt : `cat velov_dbt/logs/dbt.log` | Identifier la station anomalique, corriger ou adapter le seuil `warn_if` / `error_if` |

## 5. Exploitation du pipeline de données (S3)

### Vérifier la fraîcheur de la collecte
```bash
# Vérifier les derniers instantanés ingérés
ls -lt data/bronze/station_status/dt=$(date +%Y-%m-%d)/ | head -n 5
```

### Relancer ou rattraper une période (Backfill)
Grâce à l'idempotence (EX-10), le calcul silver et gold peut être rejoué à tout moment depuis les fichiers bronze sans risque de doublon :
```bash
# 1. Relancer l'ingestion (ne réécrit aucun fichier déjà présent)
python -m velov.ingest --bronze data/bronze

# 2. Reconstruire l'intégralité des vues silver et de la table gold
cd velov_dbt && dbt build --profiles-dir . && cd ..

# 3. Réentraîner le modèle sur la table gold fraîche
python -m velov.train --data data/velov.duckdb --out models/
```

## 6. Exploitation MLflow et livraison du modèle (S4)

### Démarrer et administrer la stack MLflow sur la VM
```bash
# Démarrer le serveur MLflow et sa base PostgreSQL
docker compose -f compose.yaml -f compose.mlflow.yaml up -d --build

# Vérifier la santé des conteneurs
docker compose -f compose.yaml -f compose.mlflow.yaml ps

# Suivre les journaux du serveur MLflow
docker compose -f compose.yaml -f compose.mlflow.yaml logs -f mlflow
```
L'interface est accessible sur `http://<ip-vm>:5000` (port unique par binôme).

### Déployer une nouvelle version du modèle (@champion)
Le passage en production est orchestré par le script de release :
```bash
# Exporte le @champion depuis le registre, build l'image immuable et redémarre l'API
./scripts/release.sh

# Vérifier l'identité et le run MLflow du modèle servi
curl -s http://127.0.0.1:8000/v1/model | jq .registry
```

### Retour arrière immédiat (Rollback @previous-champion)
En cas de dérive de performance, anomalie de prédiction ou incident critique :
```bash
# 1. Rollback via l'alias de secours
./scripts/release.sh previous-champion

# 2. Vérification immédiate que l'ancien modèle est rétabli
curl -s http://127.0.0.1:8000/v1/model | jq .registry
curl -s http://127.0.0.1:8000/ready
```

## 7. Contacts et escalade
-
