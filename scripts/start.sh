#!/bin/bash
# Script de inicio para el Agente de IA Local
# Uso: bash scripts/start.sh
# Nota: delega en run_service.sh para iniciar tambien servicios multimedia.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_SERVICE="$SCRIPT_DIR/run_service.sh"

if [ ! -x "$RUN_SERVICE" ]; then
  echo "[ERROR] No se encontro run_service.sh en: $RUN_SERVICE"
  exit 1
fi

echo "=== Agente de IA Local ==="
echo "Iniciando stack completa (RUFÜS + servicios multimedia)..."
exec "$RUN_SERVICE"
