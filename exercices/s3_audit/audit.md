# Audit d'une jointure écrite par une IA (S3 - TP3)

## Contexte
Demande initiale faite à l'assistant :
> « Ajoute à la table horaire la moyenne des vélos sur l'heure et la météo ».

Constat : La requête s'exécute sans erreur SQL, et les métriques hors-ligne (MAE, $R^2$) s'améliorent de façon spectaculaire.

**Question : Faut-il la garder en production ?**
**Réponse : NON, absolument pas.** Le gain hors-ligne est un leurre causé par une fuite temporelle majeure (*data leakage* / *look-ahead bias*).

---

## 1. Revue ligne par ligne des problèmes

| Ligne SQL | Problème identifié | Conséquence en production | Correction point-in-time |
|---|---|---|---|
| `date_trunc('hour', s.reported_at) as hour_ts` | Regroupe par heure de début de tranche $[h, h+1\text{ h}[$. | À l'instant de prédiction $h$, les relevés de $h$ à $h+1\text{ h}$ n'ont pas encore eu lieu. | Utiliser $]h-1\text{ h}, h]$ : `date_trunc('hour', reported_at - interval 1 microsecond) + interval 1 hour`. |
| `avg(s.bikes_available) as avg_bikes_hour` | Calcule la moyenne des vélos sur l'heure future (qui inclut l'instant cible $h+1\text{ h}$). | Fuite de données (*target leakage*). Le modèle "voit" le futur. En temps réel à l'instant $h$, cette moyenne n'existe pas. L'inférence échouera ou produira un *training-serving skew*. | Utiliser uniquement la dernière disponibilité observée *avant ou à l'instant* $h$ : `qualify row_number() over (partition by station_id, hour_ts order by reported_at desc) = 1`. |
| `join stg_weather w on date_trunc('hour', s.reported_at) = w.hour_ts` | Jointure sur la météo *observée* et constatée de l'heure cible. | À l'instant $h$, la météo réelle de $h+1\text{ h}$ n'est pas encore enregistrée dans l'historique (seule une prévision météo serait disponible). | Joindre la météo observée connue à l'instant $h$ ou utiliser des prévisions horodatées avant $h$. |

---

## 2. Règle d'or MLOps (EX-11)
> **EX-11 : Aucune feature n'utilise une information postérieure à l'instant de prédiction.**

- **Point-in-time correctness** : Un modèle entraîné hors-ligne doit se trouver dans les mêmes conditions d'information qu'une API en temps réel à l'instant $t$.
- Si un score hors-ligne paraît "trop beau pour être vrai", il y a 99 % de chances qu'une feature temporelle regarde dans le futur.

---

## 3. Ce que l'IA a trouvé, raté et inventé

1. **Ce que l'IA a respecté** :
   - Elle a répondu littéralement à la consigne textuelle (« ajouter la moyenne des vélos sur l'heure et la météo »).
   - La syntaxe SQL est valide et DuckDB / PostgreSQL l'exécute sans erreur.

2. **Ce que l'IA a raté** :
   - Elle n'a pas anticipé la dimension causale / temporelle du Machine Learning (*point-in-time join* / *as-of join*).
   - Elle n'a pas posé la question essentielle : « À quel moment cette requête est-elle appelée en production ? ».

3. **Ce que l'assistant invente parfois** :
   - L'illusion d'une optimisation de modèle : une IA non avertie se félicitera d'avoir fait baisser la MAE de 1.3 à 0.2, sans réaliser qu'elle a transformé un problème de prédiction en un simple calcul d'interpolation sur le futur.
