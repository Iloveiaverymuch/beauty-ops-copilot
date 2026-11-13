# SPEC-003: Pipeline de Devis Déterministe & SequentialAgent

## 1. Description & Fonctionnement du Composant
- **Rôle Métier** : Calcul de routine cosmétique sur mesure (durée en jours, posologie), calcul arithmétique de remise commerciale par palier de volume, et génération d'un devis officiel certifié au format PDF téléchargeable.
- **Fichiers Source** : [`advisor/structured_quote.py`](../../advisor/structured_quote.py), [`advisor/tools.py:estimate_quantity`](../../advisor/tools.py), [`backend/app.py:devis_pdf`](../../backend/app.py).
- **Fonctionnement Technique** :
  - **Pattern Expert SequentialAgent** : Résout la contrainte fondamentale de l'ADK et de Gemini où l'imposition d'un `output_schema` Pydantic désactive les appels d'outils sur le même tour.
  - **Étape 1 (Collector Agent)** : Agent doté d'outils (`estimate_quantity`, `search_products`). Il calcule en Python l'arithmétique exacte (flacons nécessaires, volume total, prix remisé) et écrit le résultat brut dans l'état de session.
  - **Étape 2 (Formatter Agent)** : Agent sans aucun outil (`tools=[]`, `include_contents="none"`), contraint par `output_schema=DevisConfiguratorSchema`. Il lit l'état de session et produit exclusivement le JSON certifié sans risque de calcul mental erroné.
  - **Moteur PDF Découplé** : Rendu HTML/CSS vers PDF via Chrome Headless (`--headless --print-to-pdf`), avec fallback automatique sur un générateur PDF léger si Chrome n'est pas présent sur l'hôte.

## 2. Interfaces & Contrats
- **Schéma Pydantic Strict (`DevisConfiguratorSchema`)** :
  ```python
  class DevisItemSchema(BaseModel):
      sku: str
      product_name: str
      quantity_units: int
      duration_days: int
      unit_price_eur: float
      subtotal_eur: float

  class DevisConfiguratorSchema(BaseModel):
      quote_reference: str
      items: list[DevisItemSchema]
      total_before_discount_eur: float
      discount_percentage: float
      total_final_eur: float
      legal_notice: str
  ```
- **Endpoint HTTP** : `GET /devis/pdf` télécharge le document binaire `application/pdf`.

## 3. Tests à Passer (Gate de Validation)
- **Suite Unitaire Pytest** :
  - `tests/test_enterprise_adk_patterns.py::test_sequential_agent_pipeline_separation` (Valide l'étanchéité des deux étapes et l'absence de fuite d'outils dans le formateur).
  - `tests/test_backend.py::test_devis_pdf_generation` (Vérifie la génération de l'en-tête PDF `%PDF-1.4` et le fallback sans Chrome).
- **Suite Évaluation ADK Offline** :
  - `quantity_and_cost_estimation` (Vérifie que l'agent appelle systématiquement `estimate_quantity` et n'invente aucun prix).

## 4. Risques de Régression & Mitigations
- **Erreur Arithmétique par Hallucination LLM** :
  - *Risque* : Le modèle tente d'appliquer lui-même une réduction de 20% et produit un total incohérent avec les lignes du devis.
  - *Mitigation* : L'outil `estimate_quantity` calcule la formule en Python pur ; le schéma Pydantic rejette toute incohérence.
- **Blocage du SequentialAgent par Manque d'Outils** :
  - *Risque* : Si le premier agent ne parvient pas à appeler `estimate_quantity`, le second agent reçoit un état vide et échoue à valider le schéma.
  - *Mitigation* : Gestion d'erreurs avec valeurs par défaut et statut `"calculation_failed"` déclenchant une alerte UI.
- **Délai d'Exécution Chrome Headless** :
  - *Risque* : Le rendu PDF dépasse le timeout de la requête HTTP sous forte charge.
  - *Mitigation* : Timeout strict de 3 secondes sur le sous-processus Chrome avec bascule immédiate sur le moteur de fallback.

