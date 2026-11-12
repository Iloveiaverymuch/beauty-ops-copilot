#!/usr/bin/env bash
# ==============================================================================
# Beauty Ops Copilot — Script de lancement "1-Clic" pour la Démo Entretien
# ==============================================================================
set -euo pipefail

# Résolution absolue et robuste du répertoire racine du projet
if git rev-parse --show-toplevel > /dev/null 2>&1; then
    ROOT_DIR="$(git rev-parse --show-toplevel)"
else
    ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
cd "$ROOT_DIR"

echo "🔍 [1/4] Vérification de l'environnement dans $ROOT_DIR..."

# Libérer les ports 8090 et 8000 si d'anciens processus tournent encore
for port in 8090 8000; do
    PID=$(lsof -ti :$port 2>/dev/null || true)
    if [ -n "$PID" ]; then
        echo "⚠️  Port $port occupé par le PID $PID. Arrêt du processus..."
        kill -9 $PID 2>/dev/null || true
    fi
done

# Déterminer la commande uvicorn (priorité à uv, puis .venv direct)
if command -v uv &> /dev/null; then
    UVICORN_CMD="uv run uvicorn"
elif [ -f ".venv/bin/uvicorn" ]; then
    UVICORN_CMD=".venv/bin/uvicorn"
else
    UVICORN_CMD="uvicorn"
fi

echo "🚀 [2/4] Démarrage du serveur Retail API & Stocks (Port 8090)..."
$UVICORN_CMD mock_api.stock_api:app --port 8090 > /tmp/mock_api.log 2>&1 &
MOCK_PID=$!

echo "🚀 [3/4] Démarrage de l'API Copilot & UI (Port 8000)..."
$UVICORN_CMD backend.app:app --port 8000 --reload > /tmp/backend.log 2>&1 &
BACKEND_PID=$!

# Fonction de nettoyage sur arrêt (Ctrl+C ou fermeture)
cleanup() {
    echo ""
    echo "🛑 Arrêt des serveurs de démonstration..."
    kill "$MOCK_PID" 2>/dev/null || true
    kill "$BACKEND_PID" 2>/dev/null || true
    for port in 8090 8000; do
        PID=$(lsof -ti :$port 2>/dev/null || true)
        if [ -n "$PID" ]; then
            kill -9 "$PID" 2>/dev/null || true
        fi
    done
    echo "✅ Serveurs arrêtés proprement. Bonne chance pour votre entretien !"
    exit 0
}
trap cleanup SIGINT SIGTERM

# Attente active de disponibilité des services (max 15 secondes)
echo "⏳ [4/4] En attente de la disponibilité des services..."
READY=0
for i in {1..30}; do
    if curl -s http://127.0.0.1:8000/health > /dev/null 2>&1 && curl -s http://127.0.0.1:8090/stores > /dev/null 2>&1; then
        READY=1
        break
    fi
    sleep 0.5
done

if [ "$READY" -ne 1 ]; then
    echo "❌ Erreur au démarrage des serveurs. Voici les logs d'erreur :"
    echo "--- LOG MOCK API (/tmp/mock_api.log) ---"
    cat /tmp/mock_api.log 2>/dev/null || true
    echo "--- LOG BACKEND (/tmp/backend.log) ---"
    cat /tmp/backend.log 2>/dev/null || true
    cleanup
    exit 1
fi

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
  [Appuyez sur Ctrl+C pour quitter et arrêter proprement tous les serveurs]
================================================================================
EOF

# Ouvrir automatiquement le navigateur sur macOS ou Linux
if command -v open &> /dev/null; then
    open "http://127.0.0.1:8000"
elif command -v xdg-open &> /dev/null; then
    xdg-open "http://127.0.0.1:8000"
fi

# Boucle de maintien en vie : surveille que les processus tournent
while kill -0 "$MOCK_PID" 2>/dev/null && kill -0 "$BACKEND_PID" 2>/dev/null; do
    sleep 1
done
