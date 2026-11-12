#!/usr/bin/env bash
# ==============================================================================
# Beauty Ops Copilot — Script de lancement "1-Clic" pour la Démo Entretien
# ==============================================================================
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

echo "🔍 [1/4] Vérification de l'environnement..."

if ! command -v uv &> /dev/null; then
    echo "❌ Erreur: 'uv' n'est pas installé. Veuillez installer uv (curl -LsSf https://astral.sh/uv/install.sh | sh)"
    exit 1
fi

# Libérer les ports 8090 et 8000 si d'anciens processus tournent encore
for port in 8090 8000; do
    PID=$(lsof -ti :$port 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo "⚠️  Port $port occupé par le PID $PID. Arrêt du processus..."
        kill -9 $PID 2>/dev/null || true
    fi
done

echo "🚀 [2/4] Démarrage du serveur Retail API & Stocks (Port 8090)..."
uv run uvicorn mock_api.stock_api:app --port 8090 > /tmp/mock_api.log 2>&1 &
MOCK_PID=$!

echo "🚀 [3/4] Démarrage de l'API Copilot & UI (Port 8000)..."
uv run uvicorn backend.app:app --port 8000 --reload > /tmp/backend.log 2>&1 &
BACKEND_PID=$!

# Trap pour arrêter proprement les serveurs sur Ctrl+C
cleanup() {
    echo ""
    echo "🛑 Arrêt des serveurs de démonstration..."
    kill $MOCK_PID 2>/dev/null || true
    kill $BACKEND_PID 2>/dev/null || true
    # Sécurité supplémentaire sur les ports
    for port in 8090 8000; do
        PID=$(lsof -ti :$port 2>/dev/null || true)
        if [ -n "$PID" ]; then
            kill -9 $PID 2>/dev/null || true
        fi
    done
    echo "✅ Serveurs arrêtés proprement. Bonne chance pour votre entretien !"
    exit 0
}
trap cleanup SIGINT SIGTERM EXIT

# Attente de disponibilité des services (max 10 secondes)
echo "⏳ [4/4] En attente de la disponibilité des services..."
for i in {1..20}; do
    if curl -s http://127.0.0.1:8000/health > /dev/null 2>&1 && curl -s http://127.0.0.1:8090/stores > /dev/null 2>&1; then
        break
    fi
    sleep 0.5
done

# Affichage du bandeau de présentation
clear || true
cat << "EOF"
================================================================================
  🌸💼  BEAUTY OPS COPILOT — ENVIRONNEMENT DE DÉMONSTRATION PRÊT
================================================================================
  • Interface Web Dual-Agent : http://127.0.0.1:8000
      - Onglet 1 : Conseillère Léa (Client B2C : RAG, Devis PDF, Réclamations)
      - Onglet 2 : Copilot Opérations (B2B : M365, Stock, Arbitrage HITL)
  • API Retail & Stock Mock  : http://127.0.0.1:8090
  • Google Cloud Trace       : https://console.cloud.google.com/traces
  • Script Démo 15 min       : docs/DEMO_15MIN.md
  • Dossier Architecture PDF : docs/beauty_ops_copilot_architecture_dossier.pdf
================================================================================
  [Ctrl+C pour quitter et arrêter proprement tous les serveurs]
================================================================================
EOF

# Ouvrir automatiquement le navigateur sur macOS
if command -v open &> /dev/null; then
    open "http://127.0.0.1:8000"
elif command -v xdg-open &> /dev/null; then
    xdg-open "http://127.0.0.1:8000"
fi

# Maintenir le script actif jusqu'à Ctrl+C
wait

