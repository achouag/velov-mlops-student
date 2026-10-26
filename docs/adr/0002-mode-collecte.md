# ADR-0002 : Mode d'ingestion des données (Batch, Micro-batch ou Streaming)

- Statut : accepté
- Date : 2026-10-26
- Auteurs : Adam Chouag (binôme projet)

## Contexte
Le modèle a pour tâche de prédire le nombre de Vélo'v disponibles à une échéance de $H+1$ par station.
La table d'entraînement finale est agrégée au pas horaire (1 ligne par station et par heure).
Le flux de la Métropole de Lyon (GBFS JCDecaux) est rafraîchi toutes les minutes par la source.
Nous devons déterminer le mode de collecte optimal entre batch journalier, micro-batch (quelques minutes) et streaming continu (broker Kafka/Redpanda).

## Options envisagées

| Option | Fréquence / Latence | Avantages | Inconvénients & Coûts |
|---|---|---|---|
| **Batch journalier** | 1 fois par jour (24 h) | Très simple à orchestrer (un simple cron de nuit), faible sollicitation réseau. | Risque de perte irrémédiable de données si le flux ne conserve pas l'historique (GBFS ne fournit que l'état instantané). Impossible de réagir en cas d'incident en cours de journée. |
| **Micro-batch (toutes les 5 min)** | 12 runs par heure (~5 min) | Capture l'évolution intra-horaire fine (12 instantanés/h) ; un run raté est immédiatement compensé par le suivant ; pas de broker lourd à maintenir ; volume modéré (~25 Mo/jour). | Nécessite un planificateur fiable (cron / Prefect / systemd timer) et une logique d'ingestion idempotente. |
| **Streaming temps réel** | Continu (secondes via Kafka/Redpanda) | Latence minimale, idéal pour une alerte temps réel (ex: station vide). | Infrastructure lourde (broker, consommateurs persistants, surveillance 24/7, astreinte) ; disproportionné pour une prédiction à horizon 1 heure ; coût réseau et stockage 5x supérieur sans bénéfice sur la MAE. |

## Ordre de grandeur des volumes et des coûts

- Taille d'un instantané JSON : ~80 Ko pour l'ensemble des 466 stations du réseau (~200 octets par station).
- **En micro-batch (5 min)** :
  - 288 instantanés par jour $\times$ 80 Ko $\approx$ **23 à 25 Mo / jour**.
  - Soit environ **8 à 10 Go par an** de données brutes avant compression Parquet/DuckDB.
  - Coût infrastructure : quasi-nul (tourne sur la VM existante sans serveur de streaming dédié).
- **En streaming / minute** :
  - 1 440 instantanés par jour $\times$ 80 Ko $\approx$ **115 Mo / jour** (~42 Go / an).
  - Nécessite un cluster ou conteneur Redpanda/Kafka avec rétention et gestion de consommateurs actifs en permanence.

## Décision
Nous retenons l'approche **Micro-batch toutes les 5 minutes** :
1. Elle correspond exactement au besoin métier : pour prédire à $H+1$, 12 points par heure suffisent largement à identifier la dynamique temporelle sans saturer le réseau.
2. Le flux GBFS public ne fournissant aucun historique rétrospectif, la collecte fréquente évite les trous de données.
3. Architecture ELT : stockage brut dans `data/bronze/` en JSON partitionné, transformation et déduplication gérées par dbt dans DuckDB.

## Conséquences
- L'ingestion doit être strictement **idempotente** (clé tirée de `last_updated`, écriture atomique `.tmp` $\to$ `.json`, EX-10).
- La VM doit exécuter la collecte en arrière-plan permanent (cron ou Prefect) et survivre à la déconnexion SSH.
- En cas de besoin futur d'alerting en temps réel (détection de station vide en < 3 min), une passerelle de streaming (Redpanda) pourra être ajoutée en amont sans impacter les tables dbt existantes.
