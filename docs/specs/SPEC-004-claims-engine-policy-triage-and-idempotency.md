# SPEC-004: Moteur de Réclamations, Triage de Politiques & Idempotence

## 1. Description & Fonctionnement du Composant
- **Rôle Métier** : Gestion des réclamations clients (avaries, produits ouverts, réactions allergiques, rétractation), application déterministe de la politique de retour commerciale, mise en attente d'arbitrage humain et exécution des remboursements bancaires.
- **Fichiers Source** : [`advisor/claims.py`](../../advisor/claims.py), [`claims/tools.py`](../../claims/tools.py), [`claims/policy.py`](../../claims/policy.py), [`claims/service.py`](../../claims/service.py).
- **Fonctionnement Technique** :
  - **Moteur d'État Pur ADK (Zéro LangGraph)** : Remplace l'ancienne dépendance LangGraph par une machine à états finis déterministe en Python pur adossée à SQLite (`claims.db`).
  - **Concurrence & Thread Safety** : Utilisation d'un verrou réentrant global `threading.RLock()` pour toutes les écritures et mutations d'état dans SQLite.
  - **Remboursements Idempotents** : Chaque transaction de remboursement vers l'API bancaire / retail transmet l'en-tête `Idempotency-Key: claim_id`. En cas de retry réseau ou d'interruption, aucun double remboursement n'est possible.
  - **Triage Métier Automatisé** :
    - *Auto-remboursement* : Produits non ouverts dans le délai légal de 14 jours.
    - *Mise en attente (`pending_review`)* : Produits ouverts signalant une réaction allergique documentée (`HYGIENE_EXCEPTION_ADVERSE_REACTION`).
    - *Rejet immédiat* : Produits ouverts sans réaction médicale (`opened_product_not_returnable`).

## 2. Interfaces & Contrats
- **Schéma Base de Données (`claims`)** :
  - `claim_id TEXT PRIMARY KEY` (Format SHA-256 : `CLM-XXXXXX`)
  - `order_id TEXT`, `sku TEXT`, `amount_eur REAL`
  - `opened INTEGER`, `skin_reaction INTEGER`, `photo_provided INTEGER`
  - `status TEXT` (`pending_review`, `approved`, `refunded`, `refund_failed`, `rejected`)
  - `history_json TEXT`, `triage_json TEXT`, `refund_json TEXT`
- **Projection de Sortie Sécurisée (`_SAFE`)** :
  - Seules les clés autorisées sont transmises à l'agent conversationnel : `("claim_id", "status", "message", "rule", "amount_eur", "refund_id")`.

## 3. Tests à Passer (Gate de Validation)
- **Suite Unitaire Pytest** :
  - `tests/test_enterprise_adk_patterns.py::test_claims_workflow_state_machine_transition`
  - `tests/test_enterprise_adk_patterns.py::test_idempotent_refund_execution`
  - `tests/test_enterprise_adk_patterns.py::test_scoped_state_prefixes_in_claims_tool`
- **Suite Évaluation ADK Offline** :
  - `unknown_claim_lookup_no_refund_promise` (Recherche d'un dossier inconnu : doit retourner `unknown_claim` et ne promettre aucun remboursement).

## 4. Risques de Régression & Mitigations
- **Double Remboursement Financier sur Retry Réseau** :
  - *Risque* : Un timeout HTTP lors de l'appel `/refunds` réexécute le virement et débite deux fois le compte commerçant.
  - *Mitigation* : `_execute_refund` injecte impérativement `Idempotency-Key = claim_id` ; l'API bancaire mockée et réelle ignore les doublons.
- **Faille IDOR (Insecure Direct Object Reference)** :
  - *Risque* : Un utilisateur modifie l'identifiant pour clôturer ou consulter le dossier d'un autre client.
  - *Mitigation* : Résolution de l'identité via `tool_context.state["user:id"]` issu de la session sécurisée, et masquage des données personnelles dans la projection `_SAFE`.
- **Verrouillage SQLite sous Forte Charge Concurrente** :
  - *Risque* : `sqlite3.OperationalError: database is locked` lors d'appels simultanés.
  - *Mitigation* : `check_same_thread=False` avec `threading.RLock()` local en dev, et bascule vers Cloud SQL PostgreSQL (`DatabaseSessionService`) en production.

