# SPEC-005: Copilot Opérations (B2B) & Connecteurs Google Workspace MCP

## 1. Description & Fonctionnement du Composant
- **Rôle Métier** : Assistant back-office pour les gestionnaires retail. Connecté aux outils de travail Google Workspace (Gmail, Calendar, Drive), il arbitre les litiges clients escaladés et prépare les communications internes sous contrôle de gouvernance strict.
- **Fichiers Source** : [`copilot/agent.py`](../../copilot/agent.py), [`copilot/workspace_agent.py`](../../copilot/workspace_agent.py), [`workspace_mcp/server.py`](../../workspace_mcp/server.py), [`copilot/governance.py`](../../copilot/governance.py).
- **Fonctionnement Technique** :
  - **Composition Multi-Agents Sélective** : Le routeur racine supporte deux modes (`ROUTER_MODE=transfer` ou `agenttool`). Il délègue les tâches de productivité à `workspace_agent` et les consultations de stocks à `catalog_agent`.
  - **Connecteur MCP Google Workspace** : Serveur Model Context Protocol exposant un contrat strict de 4 outils (`search_mail`, `list_events`, `search_files`, `create_draft`). Supporte le transport standard stdio ou HTTP avec validation des scopes OAuth2 (`Mail.Read`, `Mail.ReadWrite`).
  - **Gouvernance Déterministe (`GovernancePlugin`)** :
    - *Quota d'appels* : Maximum 10 invocations d'outils par tour.
    - *Quota d'écritures* : Maximum 1 brouillon d'email par tour (`create_draft`).
    - *Allowlist d'adresses* : Blocage strict de tout destinataire dont le domaine n'appartient pas à l'entreprise (`@retail-group.com`, `@beauty-corp.com`, etc.).
  - **Human-In-The-Loop Natif** : L'outil `review_claim` et la création de brouillons sensibles déclarent `require_confirmation=True`. L'exécution est mise en pause jusqu'à validation explicite de l'opérateur.

## 2. Interfaces & Contrats
- **Outils MCP Exposés** :
  - `search_mail(query, max_results)`
  - `list_events(time_min, time_max)`
  - `search_files(query, mime_type)`
  - `create_draft(recipient, subject, body)` [Gouverné par allowlist]
- **Outils Opérationnels ADK** :
  - `list_pending_claims()`
  - `review_claim(claim_id, decision)` [Nécessite confirmation HITL]

## 3. Tests à Passer (Gate de Validation)
- **Suite Unitaire Pytest** :
  - `tests/test_backend.py::test_chat_confirmation_and_resume_stream` (Valide l'émission de `confirmation_required` et la reprise fluide après confirmation).
  - `tests/test_enterprise_adk_patterns.py::test_governance_blocks_external_exfiltration` (Valide le blocage en code d'une adresse `@gmail.com`).
  - `tests/test_enterprise_adk_patterns.py::test_call_budget_limits_tool_recursion` (Valide l'arrêt net à 10 appels d'outils).

## 4. Risques de Régression & Mitigations
- **Exfiltration de Données Clients vers l'Extérieur** :
  - *Risque* : Le modèle est manipulé par prompt injection pour envoyer les litiges clients à un tiers.
  - *Mitigation* : `GovernancePlugin.before_tool` filtre l'argument `recipient` via une liste blanche en code avant tout envoi.
- **Déconnexion ou Crash du Processus MCP Stdio** :
  - *Risque* : Le sous-processus `workspace_mcp` s'arrête brutalement, faisant échouer les appels de l'agent.
  - *Mitigation* : Gestionnaires d'exceptions avec statuts de secours `unavailable` et timeouts HTTP stricts.
- **Blocage de Tour lors d'un Refus Humain** :
  - *Risque* : L'opérateur refuse l'action (`confirmed: False`), laissant la session dans un état bloqué.
  - *Mitigation* : Le flux ADK injecte `{"confirmed": False}` dans la réponse de fonction, permettant au modèle de reconnaître le refus et de proposer une alternative.

