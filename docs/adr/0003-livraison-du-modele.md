# ADR 0003 : Mode de livraison du modèle depuis le registre MLflow

## Contexte
L'alias `@champion` dans le registre de modèles MLflow désigne la version validée pour la production selon la règle de promotion (EX-14).
Cependant, l'image Docker de serving (`velov-api`) ne contient aucune dépendance MLflow (ni scikit-learn lourd, ni dbt/DuckDB), respectant ainsi la séparation stricte entre environnement d'entraînement et environnement d'inférence.

Se posent alors les questions suivantes :
1. Comment l'API accède-t-elle au modèle champion ?
2. À quel moment l'alias `@champion` est-il résolu ?
3. Comment garantir la résilience et la traçabilité en production ?

## Décision
**L'alias `@champion` est résolu en amont du build Docker via le script `python -m velov.export --alias champion --out models/`.**
L'artefact (`model.joblib` et `metadata.json` enrichi de la section `.registry`) est copié dans le dossier `models/`, vérifié par SHA-256, puis encapsulé dans une image Docker immuable taguée `velov-api:m<version>-<commit>`.

## Options écartées
1. **Charger dynamiquement `models:/velov-availability@champion` au démarrage du conteneur API** :
   - *Refusé* : Nécessite d'embarquer la bibliothèque `mlflow` et ses dépendances dans l'image de serving (image plus lourde, surface d'attaque accrue).
   - *Refusé* : Couplage fort au runtime : si le serveur MLflow est indisponible au démarrage ou au redémarrage d'un worker, l'API ne peut pas démarrer (indisponibilité en cascade).
   - *Refusé* : Violation du principe d'immuabilité : redémarrer un même conteneur pourrait lui faire charger un modèle différent sans rebuild ni traçabilité de version dans le registre Docker.
2. **Rechargement à chaud en mémoire (*hot reloading*) sans redémarrage** :
   - *Refusé* : Complexité inutile : risque de concurrence mémoire et de divergence d'état entre plusieurs workers ou réplicas en production.

## Conséquences
- **Avantages** :
  - Découplage complet : l'image de serving reste légère et autonome.
  - Résilience : l'API fonctionne même si le serveur MLflow est éteint.
  - Traçabilité totale (EX-13) : le tag Docker `velov-api:m<version>-<commit>` fige exactement le code et le modèle servis.
  - Rollback instantané : pour revenir en arrière, il suffit de redéployer l'image précédente déjà construite ou d'exécuter `./scripts/release.sh previous-champion`.
- **Inconvénients / Contraintes** :
  - Changer de modèle nécessite une étape de build et de redémarrage de service (sans coupure via rolling restart en environnement conteneurisé).
