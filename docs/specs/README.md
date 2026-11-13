# Spécifications Techniques & Règles de Non-Régression

Ce répertoire regroupe les spécifications canoniques de l'ensemble des sous-systèmes de **Beauty Ops Copilot**. Chaque spécification décrit en détail :
1. Le **fonctionnement métier et technique** du composant.
2. Les **interfaces et contrats** (schémas Pydantic, signatures d'outils, formats d'événements SSE).
3. Les **tests à passer** (tests unitaires pytest et cas d'évaluation ADK bloquants).
4. Les **risques de régression** et les mécanismes de mitigation en code.

---

### Cartographie des Spécifications

| N° | Spécification | Périmètre Composant | Fichiers Clés | Tests Associés |
| :--- | :--- | :--- | :--- | :--- |
| **SPEC-001** | [Claims HITL Lock & State Safety](SPEC-001-claims-hitl-lock-and-state-safety.md) | Validation `/chat/confirm`, verrous et reprise | `backend/app.py` | `test_confirm_request_validation` |
| **SPEC-002** | [Léa B2C & RAG Agentique](SPEC-002-b2c-beauty-advisor-and-agentic-rag.md) | RAG hybride, budgets d'outils, citations, compaction | `beauty_advisor/agent.py`, `advisor/tools.py` | `test_cards_come_from_tool_results_not_from_text`, évals ADK |
| **SPEC-003** | [Devis Déterministe & SequentialAgent](SPEC-003-deterministic-quotes-and-sequential-agent.md) | Pipeline en 2 étapes, `output_schema`, PDF print-ready | `advisor/structured_quote.py` | `test_sequential_agent_pipeline_separation`, `test_devis_pdf_generation` |
| **SPEC-004** | [Moteur de Réclamations & Idempotence](SPEC-004-claims-engine-policy-triage-and-idempotency.md) | Machine à états SQLite, remboursements idempotents | `advisor/claims.py`, `claims/tools.py` | `test_idempotent_refund_execution`, `test_claims_workflow_state_machine_transition` |
| **SPEC-005** | [Copilot Opérations & MCP Workspace](SPEC-005-operations-copilot-and-workspace-mcp.md) | Routeur multi-agents, outils MCP, GovernancePlugin | `copilot/agent.py`, `workspace_mcp/server.py` | `test_governance_blocks_external_exfiltration`, `test_call_budget_limits_tool_recursion` |
| **SPEC-006** | [Sécurité en Profondeur & Model Armor](SPEC-006-defense-in-depth-guardrails-and-model-armor.md) | Scan périmètre, short-circuiting <10ms, scoped states | `guardrails/model_armor.py`, `beauty_advisor/agent.py` | `test_model_armor_blocks_prompt_injection`, évals canaris |
| **SPEC-007** | [Suite d'Évaluation & Pipeline CI/CD](SPEC-007-evaluation-suite-and-verification-pipeline.md) | Two-tier eval, script canonique `scripts/verify.py` | `evals/adk/`, `evals/vertex/`, `scripts/verify.py` | `uv run scripts/verify.py` (<10s, 131 tests, 7/7 évals) |
| **SPEC-008** | [API FastAPI, Streaming SSE & Concurrence](SPEC-008-backend-api-sse-streaming-and-concurrency.md) | Passerelle SSE, verrous de thread, mode `/chat/sync` | `backend/app.py`, `backend/engines.py` | `test_chat_stream_contract_with_a_fake_engine`, `test_sync_chat_buffers_one_turn` |

---

### Gabarit pour Nouveau Besoin
Pour tout nouveau développement ou correction de bug, copier [`TEMPLATE.md`](TEMPLATE.md) vers `SPEC-<numéro>-<titre>.md` avant d'écrire la moindre ligne de code.

