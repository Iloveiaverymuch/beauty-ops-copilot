# SPEC-007: Suite d'Évaluation à Deux Niveaux & Pipeline CI/CD

## 1. Description & Fonctionnement du Composant
- **Rôle Métier** : Mesure continue de la qualité conversationnelle, de la sécurité et de la non-régression de l'agent avant déploiement en production, sans dépendance coûteuse ou lente lors des cycles de développement locaux.
- **Fichiers Source** : [`evals/adk/`](../../evals/adk/), [`evals/vertex/`](../../evals/vertex/), [`scripts/verify.py`](../../scripts/verify.py), [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml).
- **Fonctionnement Technique** :
  - **Niveau 1 : Évaluation ADK Native Déterministe (Offline Gate)** :
    - Utilise `InMemoryRunner` et le dataset golden `beauty_advisor.evalset.json` (7 cas de référence).
    - Exécute une batterie d'évaluateurs sur mesure en Python pur (`evals/adk/custom_evaluators.py`) :
      - *Détection de fuites de prompt / canaris* (`canary_leak_check`).
      - *Vérification des citations documentaires* (`citation_grounding_check`).
      - *Interdiction de promesses de remboursement non confirmées* (`unconfirmed_refund_check`).
      - *Respect des filtres et de l'ordre de recherche* (`product_search_filter_check`).
    - **Performance** : 7 cas évalués en **moins de 1 seconde**, 0 appel API externe, 0 euro de facturation.
  - **Niveau 2 : Benchmark LLM-as-a-Judge (Vertex AI GenAI Evaluation Service)** :
    - Exécuté manuellement ou en pré-release sur Google Cloud Vertex AI avec Gemini 3.8 Flash comme juge.
    - Évalue 4 rubriques Pointwise notées de 1 à 5 : *Groundedness*, *Safety Refusal*, *Persona Consistency*, *Claim Policy Adherence*.
  - **Script de Vérification Canonique Unique (`scripts/verify.py`)** :
    - Exécute séquentiellement la suite pytest (131 tests) et l'évaluation ADK offline (7/7 cas).
    - Renvoie un code de sortie strict `0` en cas de succès, non-zéro au moindre échec. Budget d'exécution garanti < 10 secondes.

## 2. Interfaces & Contrats
- **Commande de Validation Développeur & CI** :
  ```bash
  uv run scripts/verify.py
  ```
- **Rapports Produits** :
  - `reports/pytest.xml` (Rapport JUnit pour CI GitHub Actions).
  - `reports/adk-eval-offline.json` (Détail des scores et latences par cas d'épreuve).
  - `reports/adk-eval-live.json` (Trace complète des inférences réelles).

## 3. Tests à Passer (Gate de Validation)
- **Suite Unitaire Pytest (`tests/test_adk_eval.py`)** :
  - 14 tests de régression vérifiant la sévérité des évaluateurs personnalisés (les tests doivent détecter des fuites intentionnelles et rejeter les fausses réponses).
- **Pipeline GitHub Actions (`.github/workflows/ci.yml`)** :
  - Job `ci` : Bloquant sur toute Pull Request ou push sur `master`.
  - Exécute `uv run --no-sync pytest -q` et `uv run --no-sync python -m evals.adk.run_adk_eval --offline`.

## 4. Risques de Régression & Mitigations
- **Divergence entre Fixtures Mockées et API Réelle** :
  - *Risque* : Le jeu d'évaluation offline réussit sur un mock périmé alors que l'API de stock réelle a changé de schéma.
  - *Mitigation* : Validation Pydantic stricte des réponses d'outils et exécution périodique du workflow live `live-evaluation` sur Vertex AI.
- **Variabilité / Flakiness du Juge LLM** :
  - *Risque* : Les notes du juge LLM oscillent entre 4.0 et 5.0 pour une réponse identique.
  - *Mitigation* : Rubriques d'évaluation déterministes avec exemples few-shot ancrés dans `evals/vertex/rubrics/` et `temperature: 0.0` sur le juge.
- **Ralentissement de la Boucle de Feedback Développeur** :
  - *Risque* : La CI prend 15 minutes, incitant les développeurs à contourner les tests.
  - *Mitigation* : Élimination complète de Node.js/npm ; suite offline ultra-rapide exécutée en **7.5 secondes** chrono.

