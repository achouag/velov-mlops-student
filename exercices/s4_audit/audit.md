# Audit d'une chaîne CI/CD écrite par une IA (S4 - TP4)

## Contexte
Demande initiale faite à l'assistant :
> « Écris un workflow GitHub Actions pour tester mon projet ML FastAPI avec pytest, vérifier la sécurité et construire l'image Docker. »

Constat : Le workflow s'exécute, tous les jobs sont au vert et l'image Docker est construite.

**Question : Faut-il l'accepter en production ?**
**Réponse : NON.** Cette CI donne un faux sentiment de sécurité. Elle présente des failles de sécurité supply chain majeures, des permissions excessives, un scan de secrets inefficace et ignore totalement la dimension Machine Learning (aucun test de qualité de modèle).

---

## 1. Revue ligne par ligne des problèmes

| Ligne CI | Problème identifié | Risque / Conséquence en production | Correction proposée |
|---|---|---|---|
| Ligne 10 : `permissions: write-all` | Viol du principe de moindre privilège. Le `GITHUB_TOKEN` dispose des droits d'écriture sur tout le dépôt (code, releases, packages). | Si une dépendance ou une action compromise s'exécute, elle peut modifier l'historique git, injecter du code malveillant ou pousser de fausses releases. | Restreindre strictement aux besoins : `permissions: contents: read`. |
| Lignes 16, 18, 30, 42 : `actions/checkout@v4`, `actions/setup-python@v5` | Actions tierces référencées par des tags mobiles (`@v4`, `@v5`) et non par leur SHA de commit. | **Attaque Supply Chain** : Un attaquant compromettant le compte d'un mainteneur d'action peut déplacer le tag vers un commit exfiltrant les secrets (incident réel Aqua trivy-action du 19 mars 2026, CVE-2026-33634). | Épingler chaque action par son empreinte de commit immuable (ex. `actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1 # v7.0.1`). |
| Ligne 22 : `pip install -r requirements.txt pytest` | Installation des dépendances de prod + pytest au lieu de `requirements-dev.txt` (qui fige `mlflow`, `pandera`, `dbt`, `ruff`). Pas de linter (`ruff`). | Divergence d'environnement entre dev, test et prod. Non-détection des régressions de style ou erreurs de syntaxe. | Installer `requirements-dev.txt` épinglé et exécuter `ruff check .` ainsi que `ruff format --check .`. |
| Ligne 25 : `pytest tests/test_api.py` | Seuls les tests d'API web sont exécutés. Aucun test de modèle ML (`test_model_quality.py`). | **Régression silencieuse du modèle (EX-05)** : Un hyperparamètre erroné ou une fuite de données rendant le modèle pire que la persistance passe la CI sans lever d'erreur. | Exécuter l'ensemble des tests (`pytest`), incluant le test temporel face à la baseline de persistance et la règle de promotion. |
| Lignes 31-32 : `fetch-depth: 1` | Clone superficiel (shallow clone) limité au dernier commit. | **Évasion de détection de secrets (EX-06)** : Tout secret commité par erreur dans un commit précédent puis "supprimé" reste dans l'historique mais échappe totalement au scanner gitleaks. | Configurer `fetch-depth: 0` pour scanner l'intégralité de l'historique git. |
| Lignes 35-37 : `curl ... | tar -xz` | Téléchargement direct d'un binaire sans validation d'intégrité cryptographique. | Attaque de type Man-in-the-Middle ou substitution d'artefact sur la release GitHub. | Vérifier la somme de contrôle SHA-256 du binaire téléchargé avant exécution. |
| Ligne 40 : `needs: [test]` | Le job de build/déploiement ne dépend que de `test` et ignore le résultat de `security`. | Si `security` détecte une fuite de secret, l'image Docker est quand même construite et déployée. | Déclarer `needs: [tests, secrets]`. |
| Lignes 45, 48 : `docker build -t velov-api:latest .` | Utilisation du tag mutable `:latest` et absence de test fonctionnel du conteneur. | Traçabilité nulle : impossible de savoir quel modèle ou quel commit tourne en production (violation EX-03 et EX-13). De plus, l'image n'est pas testée sur `/ready`. | Taguer avec `velov-api:m<version>-<commit>`, instancier le conteneur en CI et attendre que `/ready` réponde 200 (sinon afficher `docker logs` et échouer). |

---

## 2. Règles d'or CI MLOps (EX-05, EX-06, EX-13, EX-15)
- **EX-15 : Rien n'arrive sur main sans CI au vert.** La CI doit valider le code, les secrets, le modèle et l'image conteneurisée.
- **Le code ne suffit pas dans un projet ML** : Un modèle peut s'effondrer sans qu'aucune exception Python ne soit levée. La CI doit intégrer un test automatisé de qualité (battre la persistance).
- **Sécurité et intégrité** : Épinglage des actions par SHA immuable, historique complet inspecté par gitleaks, permissions minimales.
