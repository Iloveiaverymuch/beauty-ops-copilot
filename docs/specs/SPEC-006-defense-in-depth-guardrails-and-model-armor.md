# SPEC-006: Sécurité en Profondeur, Model Armor & Callbacks Guardrails

## 1. Description & Fonctionnement du Composant
- **Rôle Métier** : Protection globale du système contre les tentatives de jailbreak, d'injections de prompt directes ou indirectes, de fuites d'instructions système et d'exfiltration de données confidentielles.
- **Fichiers Source** : [`guardrails/model_armor.py`](../../guardrails/model_armor.py), [`beauty_advisor/agent.py:prepare_request`](../../beauty_advisor/agent.py), [`copilot/governance.py`](../../copilot/governance.py).
- **Fonctionnement Technique** :
  - **Couche 1 : Assainissement Périmétrique (Google Cloud Model Armor)** :
    - Filtre en entrée de requête HTTP (`backend/app.py:145`) les attaques connues (tentatives de désactivation des consignes, extraction de variables d'environnement).
    - En environnement de test ou hors-connexion, bascule sur un moteur de règles regex local déterministe.
  - **Couche 2 : Short-Circuiting ADK à Coût Zéro (`before_model_callback`)** :
    - Avant chaque appel Gemini, le callback inspecte les parties textuelles du message utilisateur.
    - S'il détecte une tentative d'injection caractérisée (`"ignore previous instructions"`, `"drop all tables"`), il retourne immédiatement un objet `LlmResponse` synthétique déterministe.
    - **Bénéfice** : Zéro appel réseau vers l'API Vertex AI, latence d'interception inférieure à 10 ms, zéro coût de tokens.
  - **Couche 3 : Callbacks Guardrails d'Outils (`before_tool_callback`)** :
    - Validation stricte des arguments d'outils (ex: format `CMD-XXXX` obligatoire) et règles d'hygiène impératives en code Python.
  - **Couche 4 : Scoped States Anti-IDOR** :
    - Ségrégation stricte des clés dans `tool_context.state` : `user:id` pour l'authentification pérenne, et `temp:` pour le contexte éphémère d'un tour unique.

## 2. Interfaces & Contrats
- **Fonction de Filtrage Périmétrique** :
  ```python
  def sanitize_prompt(prompt: str) -> tuple[bool, str]:
      # Retourne (is_blocked: bool, reason: str)
  ```
- **Événement SSE Émis en cas de Blocage** :
  - `event: governance_blocked`
  - `data: {"message": "Requête bloquée par les garde-fous de sécurité ADK (tentative d'injection détectée)."}`

## 3. Tests à Passer (Gate de Validation)
- **Suite Unitaire Pytest** :
  - `tests/test_backend.py::test_model_armor_blocks_prompt_injection` (Vérifie l'émission de `governance_blocked` et l'absence d'appel au modèle).
- **Suite Évaluation ADK Offline** :
  - `staff_code_social_engineering_defense` (Tentative d'ingénierie sociale pour obtenir un code interne ou une remise staff : refus catégorique).
  - `off_topic_no_tools_redirection` (Questions hors sujet : redirection polie sans aucun appel d'outil).

## 4. Risques de Régression & Mitigations
- **Faux Positifs sur Réclamations Réelles** :
  - *Risque* : Un client décrivant une brûlure cutanée ou un problème chimique est assimilé à une tentative d'attaque dangereuse.
  - *Mitigation* : Paramétrage précis des seuils Model Armor et des filtres Vertex AI (`BLOCK_ONLY_HIGH`) pour préserver les requêtes de support client légitimes.
- **Contournement par Obfuscation Unicode ou Injections Indirectes** :
  - *Risque* : L'attaquant dissimule des instructions dans un avis produit ou une citation markdown.
  - *Mitigation* : Consigne explicite dans le prompt d'instruction (`INSTRUCTION`) stipulant que le texte retourné par les outils est une donnée factuelle et ne doit jamais être interprété comme une consigne exécutable.
- **Surcoût de Latence sur le Scan d'Entrée** :
  - *Risque* : Le scan Model Armor ajoute 500 ms de latence avant chaque réponse.
  - *Mitigation* : Exécution asynchrone non-bloquante avec mise en cache des signatures de requêtes saines.

