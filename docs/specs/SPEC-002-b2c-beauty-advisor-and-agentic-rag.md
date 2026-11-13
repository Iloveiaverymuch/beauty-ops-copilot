# SPEC-002: Léa (B2C Beauty Advisor) & Agentic RAG Architecture

## 1. Description & Fonctionnement du Composant
- **Rôle Métier** : Conseillère beauté virtuelle B2C conversationnelle pour le diagnostic de peau, la recommandation de soins du catalogue et la réponse aux questions de politique commerciale (retours, livraison, routines).
- **Fichiers Source** : [`beauty_advisor/agent.py`](../../beauty_advisor/agent.py), [`advisor/tools.py`](../../advisor/tools.py), [`advisor/data/`](../../advisor/data/).
- **Fonctionnement Technique** :
  - **Agentic RAG sur Demande** : La recherche documentaire (`search_knowledge`) n'est pas injectée systématiquement. L'agent décide de manière autonome de requêter la base documentaire avec reformulation de requête.
  - **Recherche Hybride & Citations** : Recherche vectorielle dense (`gemini-embedding-001`) + BM25 français sur les chunks markdown de la base de connaissances. Reranking cross-encoder avec citations obligatoires `[pol-...]` ou `[fiche-...]`.
  - **Contrôle des Budgets d'Outils** : Budget maximum de 2 recherches documentaires par tour (`MAX_KB_SEARCHES_PER_TURN = 2`). Si le budget est épuisé, `drop_exhausted_tools` purge dynamiquement la déclaration de l'outil du prompt pour contraindre le modèle à répondre.
  - **Compaction d'Historique Multi-Tours** : `compact_history` remplace les résultats d'outils volumineux des tours passés par des stubs minimaux (SKUs, noms, citations), réduisant les tokens d'entrée de 40% sans altérer la mémoire du client.

## 2. Interfaces & Contrats
- **Outils Autorisés** :
  - `search_products(skin_type, category, concern, budget_max)`
  - `compare_products(skus)`
  - `estimate_quantity(sku, days, uses_per_day)`
  - `search_knowledge(question)`
  - `check_availability(sku, store)`
  - `delivery_options(postcode)`
  - `open_return_claim(order_id, reason, product_opened, skin_reaction)`
  - `get_claim_status(claim_id)`
  - `request_human_advisor(reason)`
- **Événements UI Émis** : `cards` (fiches produits), `suggestions` (chips contextuels), `text` (streaming SSE).

## 3. Tests à Passer (Gate de Validation)
- **Suite Unitaire Pytest** :
  - `tests/test_backend.py::test_cards_come_from_tool_results_not_from_text`
  - `tests/test_backend.py::test_suggestions_follow_what_just_happened`
  - `tests/test_backend.py::test_retail_client_retries_then_falls_back`
  - `tests/test_backend.py::test_retail_client_does_not_retry_4xx`
- **Suite Évaluation ADK Offline** :
  - `clarify_before_recommending` (Doit poser une question de clarification si peau/besoin inconnu).
  - `product_search_filter_compliance` (Doit respecter filtres et ordre de retour).
  - `current_policy_with_citation` (Doit citer la source officielle `[pol-retours#...]`).
  - `off_topic_no_tools_redirection` (Refus poli des questions hors sujet sans appel d'outil).

## 4. Risques de Régression & Mitigations
- **Hallucination d'Ingrédients ou d'Effets Médicaux** :
  - *Risque* : Le modèle promet des bénéfices cosmétiques absents de la fiche produit.
  - *Mitigation* : Consigne d'instruction impérative limitant les affirmations aux champs de la fiche produit + éval ADK `current_policy_with_citation`.
- **Boucle Infinie de Reformulation RAG** :
  - *Risque* : Si la réponse est introuvable, le modèle reformule indéfiniment en consommant latence et quota.
  - *Mitigation* : `drop_exhausted_tools` coupe physiquement l'outil au bout de 2 tentatives et force l'escalade vers un conseiller.
- **Explosion des Tokens sur Sessions Longues** :
  - *Risque* : Chaque recherche produit réinjectée alourdit le prompt (de 2.3k à 28k tokens en 7 tours).
  - *Mitigation* : `compact_history` dans `before_model_callback` élague les attributs secondaires des tours passés.

