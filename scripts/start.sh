#!/bin/bash
# Script de inicio para el Agente de IA Local
# Uso: bash scripts/start.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

echo "=== Agente de IA Local ==="
echo "Directorio: $PROJECT_DIR"

# Verificar que Ollama este corriendo
if ! curl -s http://localhost:11434/api/tags > /dev/null 2>&1; then
    echo "[WARN] Ollama no esta corriendo. Ejecuta: ollama serve"
fi

# Activar venv si existe
if [ -d "venv" ]; then
    source venv/bin/activate
    echo "Entorno virtual activado"
elif [ -d ".venv" ]; then
    source .venv/bin/activate
    echo "Entorno virtual activado"
fi

# Iniciar servidor
echo "Iniciando servidor en http://localhost:8000 ..."
python -m app.main
