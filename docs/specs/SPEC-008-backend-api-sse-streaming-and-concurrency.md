# SPEC-008: API FastAPI, Streaming SSE & Gestion de Concurrence

## 1. Description & Fonctionnement du Composant
- **Rôle Métier** : Point d'entrée web et passerelle d'orchestration servant l'interface utilisateur double-agent, assurant le streaming temps-réel des réponses et gérant la synchronisation concurrente des conversations.
- **Fichiers Source** : [`backend/app.py`](../../backend/app.py), [`backend/engines.py`](../../backend/engines.py), [`backend/ui_events.py`](../../backend/ui_events.py), [`backend/telemetry.py`](../../backend/telemetry.py).
- **Fonctionnement Technique** :
  - **Streaming SSE (Server-Sent Events)** :
    - Format normalisé : `event: <nom>\ndata: <json>\n\n`.
    - Séquence ordonnée d'événements : `meta` (identifiants de tour) $\rightarrow$ `progress` (étiquette d'outil) $\rightarrow$ `cards` (produits) $\rightarrow$ `text` (deltas de texte streamés) $\rightarrow$ `suggestions` (chips) $\rightarrow$ `done` (métriques finales de temps, coût et tokens).
  - **Verrouillage par Conversation (`asyncio.Lock`)** :
    - Un verrou asynchrone par `thread_id` (`_locks[thread_id]`) garantit qu'un utilisateur ne peut pas envoyer deux messages simultanés dans la même conversation et désynchroniser la mémoire de session.
  - **Mode Synchrone Sécurisé (`/chat/sync`)** :
    - Bufferise l'ensemble du stream SSE pour les clients tiers (ex: tests automatisés, intégrations ERP/Webhook) et retourne un payload JSON unique structuré avec métriques d'exécution.
  - **Télémétrie OpenTelemetry & KPI JSONL** :
    - Chaque tour conversationnel émet une ligne JSON structurée dans `.data/kpi.jsonl` consignant latence totale, TTFT, tokens d'entrée/sortie, durée des outils et coûts en euros.
    - Export des traces vers Google Cloud Trace sous un span parent `chat.turn` avec masquage RGPD des données textuelles (`NO_CONTENT`).

## 2. Interfaces & Contrats
- **Modèles Pydantic de Requête** :
  ```python
  class ChatRequest(BaseModel):
      message: str = Field(min_length=1, max_length=2000)
      thread_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
      engine: str = "adk"

  class ConfirmRequest(BaseModel):
      thread_id: str = Field(pattern=r"^[a-f0-9]{32}$")
      call_id: str = Field(min_length=1, max_length=128)
      confirmed: bool
      engine: str = "copilot"
  ```
- **Endpoints Clés** :
  - `POST /chat` : Streaming SSE temps-réel.
  - `POST /chat/sync` : Consommation synchrone complète d'un tour.
  - `POST /chat/confirm` : Reprise de tour après validation HITL.
  - `POST /chat/feedback` : Enregistrement des notes utilisateur (`up`/`down`).

## 3. Tests à Passer (Gate de Validation)
- **Suite Unitaire Pytest (`tests/test_backend.py`)** :
  - `test_chat_request_validation` (Validation Pydantic : message vide, message trop long, path traversal sur thread_id).
  - `test_chat_stream_contract_with_a_fake_engine` (Vérifie la séquence stricte des événements SSE).
  - `test_sync_chat_buffers_one_turn_and_preserves_session_and_structured_events` (Bufferisation `/chat/sync`).
  - `test_sync_chat_reports_engine_errors_instead_of_passing_on_fallback_text` (Rapport d'erreur propre en cas de crash).
  - `test_confirm_request_validation` & `test_chat_confirm_empty_call_id_rejected_with_422` (Contrôle strict des identifiants d'arbitrage).

## 4. Risques de Régression & Mitigations
- **Fuite de Mémoire sur les Dictionnaires de Verrous** :
  - *Risque* : Les dictionnaires `_locks` et `_turns` croissent indéfiniment en mémoire pour chaque nouveau `thread_id`.
  - *Mitigation* : Utilisation d'un cache avec politique d'éviction LRU / TTL pour purger les conversations inactives.
- **Blocage de Verrou sur Déconnexion Client Abrupte** :
  - *Risque* : Un client ferme l'onglet pendant le streaming, laissant le verrou `asyncio.Lock` indéfiniment acquis.
  - *Mitigation* : Encadrement systématique de la boucle du générateur dans un bloc `try...finally` garantissant la libération immédiate du verrou.
- **Non-Émission de l'Événement `done` sur Erreur Inattendue** :
  - *Risque* : Le frontend attend indéfiniment la fin du stream si le runner ADK lève une exception non interceptée.
  - *Mitigation* : Bloc `except Exception` global émettant impérativement `event: text` (message de repli) suivi de `event: done` avec `failed: true`.

