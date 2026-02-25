#!/bin/bash
# Ejecuta Shaggy como servicio en background para launchd (macOS).
# - Verifica/Inicia Ollama si no esta activo.
# - Arranca FastAPI del proyecto y queda en foreground (launchd lo supervisa).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
LOG_DIR="$PROJECT_DIR/data/logs"
SERVICE_LOG="$LOG_DIR/service.log"
OLLAMA_LOG="$LOG_DIR/ollama.log"

mkdir -p "$LOG_DIR"

# PATH explicito para launchd (que no carga shell profile completo)
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

# Limpiar variables que pueden romper el bootstrap de venv en macOS.
unset PYTHONHOME PYTHONPATH __PYVENV_LAUNCHER__ VIRTUAL_ENV || true

echo "[$(date '+%Y-%m-%d %H:%M:%S')] run_service.sh iniciado" >> "$SERVICE_LOG"

if ! curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
  if command -v ollama >/dev/null 2>&1; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando ollama serve" >> "$SERVICE_LOG"
    nohup "$(command -v ollama)" serve >> "$OLLAMA_LOG" 2>&1 &
    sleep 2
  else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] ERROR: no se encontro comando 'ollama'" >> "$SERVICE_LOG"
  fi
fi

cd "$PROJECT_DIR"

VENV_PYTHON=""
if [ -x "$PROJECT_DIR/venv/bin/python" ]; then
  VENV_PYTHON="$PROJECT_DIR/venv/bin/python"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
  VENV_PYTHON="$PROJECT_DIR/.venv/bin/python"
fi

if [ -n "$VENV_PYTHON" ]; then
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] Iniciando $VENV_PYTHON -m app.main" >> "$SERVICE_LOG"
  exec "$VENV_PYTHON" -m app.main
fi

echo "[$(date '+%Y-%m-%d %H:%M:%S')] WARN: venv no encontrado, usando python del sistema" >> "$SERVICE_LOG"
exec python -m app.main
