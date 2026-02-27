#!/bin/bash
# Ejecuta Shaggy como servicio en background para launchd (macOS).
# - Verifica/Inicia Ollama si no esta activo.
# - Verifica/Inicia stack multimedia (Radarr, Prowlarr, Transmission, Jellyfin).
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
# Evitar que servicios .NET (*arr) hereden puertos web del backend.
# Si PORT/URLS queda en el entorno (por ejemplo al invocar desde app.main),
# Radarr/Prowlarr pueden intentar abrir en :8000.
unset PORT URLS ASPNETCORE_URLS ASPNETCORE_HTTP_PORTS ASPNETCORE_HTTPS_PORTS DOTNET_URLS || true

# Modos:
# - full (default): inicia stack (si esta habilitado) + API
# - --start-media-stack-only: inicia solo stack multimedia y termina
# - --stop-media-stack-only: detiene stack multimedia y termina
START_MEDIA_STACK_ONLY=0
STOP_MEDIA_STACK_ONLY=0
case "${1:-}" in
  --start-media-stack-only) START_MEDIA_STACK_ONLY=1 ;;
  --stop-media-stack-only) STOP_MEDIA_STACK_ONLY=1 ;;
esac

log() {
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" >> "$SERVICE_LOG"
}

is_port_listening() {
  local port="$1"
  lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
}

kill_rogue_media_on_8000() {
  # Si algun servicio multimedia toma :8000, rompe la URL de Shaggy.
  local rows=""
  rows="$(lsof -nP -iTCP:8000 -sTCP:LISTEN 2>/dev/null | awk 'NR>1 {print $1":"$2}' || true)"
  if [ -z "$rows" ]; then
    return 0
  fi

  local killed=0
  while IFS= read -r row; do
    [ -n "$row" ] || continue
    local cmd="${row%%:*}"
    local pid="${row##*:}"
    case "$cmd" in
      Radarr|Prowlarr|jellyfin|Jellyfin|Transmiss|Transmission)
        log "Detectado servicio multimedia en puerto 8000 ($cmd PID $pid). Matando proceso rogue."
        kill "$pid" >/dev/null 2>&1 || true
        killed=1
        ;;
      *)
        ;;
    esac
  done <<< "$rows"

  if [ "$killed" -eq 1 ]; then
    sleep 1
    # Refuerzo para procesos de Servarr que re-spawnean con otro pid.
    pkill -f "/Applications/Radarr.app/Contents/MacOS/Radarr" >/dev/null 2>&1 || true
    pkill -f "/Applications/Prowlarr.app/Contents/MacOS/Prowlarr" >/dev/null 2>&1 || true
  fi
}

BREW_AVAILABLE=0
BREW_SERVICE_NAMES=""
if command -v brew >/dev/null 2>&1; then
  BREW_AVAILABLE=1
  BREW_SERVICE_NAMES="$(brew services list 2>/dev/null | awk 'NR>1 {print $1}' || true)"
fi

brew_service_exists() {
  local service_name="$1"
  if [ "$BREW_AVAILABLE" -ne 1 ]; then
    return 1
  fi
  echo "$BREW_SERVICE_NAMES" | grep -qx "$service_name"
}

ensure_service_running() {
  local name="$1"
  local port="$2"
  local app_name="$3"
  shift 3
  local brew_candidates=("$@")
  local started=0

  if is_port_listening "$port"; then
    log "$name ya activo en puerto $port"
    return 0
  fi

  log "Intentando iniciar $name (puerto $port)..."

  if [ "$BREW_AVAILABLE" -eq 1 ]; then
    for service in "${brew_candidates[@]}"; do
      if ! brew_service_exists "$service"; then
        continue
      fi
      log "Iniciando $name con: brew services start $service"
      if brew services start "$service" >> "$SERVICE_LOG" 2>&1; then
        started=1
        break
      fi
    done
  fi

  if [ "$started" -eq 0 ] && [ -n "$app_name" ]; then
    local app_path=""
    if [ -d "/Applications/$app_name.app" ]; then
      app_path="/Applications/$app_name.app"
    elif [ -d "$HOME/Applications/$app_name.app" ]; then
      app_path="$HOME/Applications/$app_name.app"
    fi
    if [ -n "$app_path" ]; then
      log "Iniciando $name con app: open -gja $app_path"
      if open -gja "$app_path" >> "$SERVICE_LOG" 2>&1; then
        started=1
      fi
    fi
  fi

  for _ in {1..20}; do
    if is_port_listening "$port"; then
      log "$name disponible en puerto $port"
      return 0
    fi
    sleep 1
  done

  if [ "$started" -eq 1 ]; then
    log "WARN: $name se intento iniciar pero no quedo escuchando en :$port"
  else
    log "WARN: No pude iniciar $name automaticamente (ni brew services ni app)"
  fi

  return 1
}

wait_for_port() {
  local port="$1"
  local tries="${2:-20}"
  for _ in $(seq 1 "$tries"); do
    if is_port_listening "$port"; then
      return 0
    fi
    sleep 1
  done
  return 1
}

ensure_servarr_headless() {
  local name="$1"
  local port="$2"
  local brew_service="$3"
  local bundle_app="$4"
  local executable_name="$5"
  local log_file="$6"
  local data_dir="$HOME/Library/Application Support/$bundle_app"

  if is_port_listening "$port"; then
    log "$name ya activo en puerto $port"
    return 0
  fi

  log "Intentando iniciar $name en modo headless (puerto $port)..."

  local bin_candidates=(
    "/Applications/$bundle_app.app/Contents/MacOS/$executable_name"
    "$HOME/Applications/$bundle_app.app/Contents/MacOS/$executable_name"
  )

  mkdir -p "$data_dir"

  local cfg_path="$data_dir/config.xml"
  if [ -f "$cfg_path" ]; then
    # Blindaje: fijar puerto y evitar apertura de navegador en cada arranque.
    perl -0777 -i -pe \
      "s|<Port>.*?</Port>|<Port>${port}</Port>|s; s|<LaunchBrowser>.*?</LaunchBrowser>|<LaunchBrowser>False</LaunchBrowser>|s" \
      "$cfg_path" 2>/dev/null || true
  fi

  for bin_path in "${bin_candidates[@]}"; do
    if [ ! -x "$bin_path" ]; then
      continue
    fi

    # Primer intento: bandera habitual en apps *arr (evita abrir browser).
    log "Iniciando $name con binario headless: $bin_path -nobrowser -data=$data_dir"
    nohup "$bin_path" -nobrowser "-data=$data_dir" >> "$log_file" 2>&1 &
    if wait_for_port "$port" 18; then
      log "$name disponible en puerto $port (headless)"
      return 0
    fi

    # Segundo intento: sin flags por compatibilidad.
    log "Reintentando $name con binario (solo data): $bin_path -data=$data_dir"
    nohup "$bin_path" "-data=$data_dir" >> "$log_file" 2>&1 &
    if wait_for_port "$port" 18; then
      log "$name disponible en puerto $port (binario)"
      return 0
    fi
  done

  # Si hay una instancia viva en puerto incorrecto, reiniciar y reintentar 1 vez.
  local proc_pattern="/Applications/$bundle_app.app/Contents/MacOS/$executable_name"
  local stale_pids
  stale_pids="$(pgrep -f "$proc_pattern" 2>/dev/null || true)"
  if [ -n "$stale_pids" ]; then
    log "Detectado $name ejecutandose fuera del puerto esperado. Reiniciando proceso ($stale_pids)"
    pkill -f "$proc_pattern" >/dev/null 2>&1 || true
    sleep 2

    for bin_path in "${bin_candidates[@]}"; do
      if [ ! -x "$bin_path" ]; then
        continue
      fi
      log "Reintento final $name: $bin_path -nobrowser -data=$data_dir"
      nohup "$bin_path" -nobrowser "-data=$data_dir" >> "$log_file" 2>&1 &
      if wait_for_port "$port" 20; then
        log "$name disponible en puerto $port (reintento final)"
        return 0
      fi
    done
  fi

  # Fallback final: brew service (solo si no funciono el binario de la app)
  if [ "$BREW_AVAILABLE" -eq 1 ] && brew_service_exists "$brew_service"; then
    log "Fallback $name con: brew services start $brew_service"
    if brew services start "$brew_service" >> "$SERVICE_LOG" 2>&1; then
      if wait_for_port "$port" 20; then
        log "$name disponible en puerto $port (brew fallback)"
        return 0
      fi
      log "WARN: $name iniciado con brew fallback, pero no abrio puerto $port"
    fi
  fi

  log "ERROR: $name no pudo iniciarse en modo headless"
  return 1
}

ensure_jellyfin_running() {
  if is_port_listening "8096"; then
    log "Jellyfin ya activo en puerto 8096"
    return 0
  fi

  ensure_service_running "Jellyfin" "8096" "Jellyfin" "jellyfin" && return 0

  log "Intentando fallback de Jellyfin por binario interno..."
  local candidates=(
    "/Applications/Jellyfin.app/Contents/MacOS/Jellyfin Server"
    "/Applications/Jellyfin.app/Contents/MacOS/jellyfin"
    "$HOME/Applications/Jellyfin.app/Contents/MacOS/Jellyfin Server"
    "$HOME/Applications/Jellyfin.app/Contents/MacOS/jellyfin"
  )

  for bin_path in "${candidates[@]}"; do
    if [ ! -x "$bin_path" ]; then
      continue
    fi

    log "Iniciando Jellyfin con binario: $bin_path"
    nohup "$bin_path" >> "$LOG_DIR/jellyfin.log" 2>&1 &
    sleep 2

    for _ in {1..18}; do
      if is_port_listening "8096"; then
        log "Jellyfin disponible en puerto 8096 (via binario)"
        return 0
      fi
      sleep 1
    done
  done

  log "ERROR: Jellyfin no pudo iniciarse automaticamente (app y binario fallaron)"
  return 1
}

stop_port_listener() {
  local name="$1"
  local port="$2"

  if ! is_port_listening "$port"; then
    log "$name ya estaba detenido (puerto $port)"
    return 0
  fi

  local pids=""
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ')"
  if [ -z "$pids" ]; then
    log "WARN: $name escucha en $port pero no pude resolver PID"
    return 1
  fi

  log "Deteniendo $name en puerto $port (PID: $pids)"
  kill $pids >/dev/null 2>&1 || true

  for _ in {1..12}; do
    if ! is_port_listening "$port"; then
      log "$name detenido correctamente"
      return 0
    fi
    sleep 1
  done

  # Fallback duro si sigue vivo
  pids="$(lsof -nP -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null | tr '\n' ' ')"
  if [ -n "$pids" ]; then
    log "Forzando kill -9 para $name (PID: $pids)"
    kill -9 $pids >/dev/null 2>&1 || true
  fi

  for _ in {1..6}; do
    if ! is_port_listening "$port"; then
      log "$name detenido tras kill -9"
      return 0
    fi
    sleep 1
  done

  log "ERROR: $name sigue activo en puerto $port"
  return 1
}

stop_media_stack() {
  # Intento limpio con brew services (si existen)
  if [ "$BREW_AVAILABLE" -eq 1 ]; then
    for svc in jellyfin transmission-daemon transmission radarr prowlarr; do
      if brew_service_exists "$svc"; then
        log "Intentando stop via brew services: $svc"
        brew services stop "$svc" >> "$SERVICE_LOG" 2>&1 || true
      fi
    done
  fi

  # Garantizar detencion por puerto (incluye procesos fuera de brew)
  stop_port_listener "Radarr" "7878" || true
  stop_port_listener "Prowlarr" "9696" || true
  stop_port_listener "Transmission" "9091" || true
  stop_port_listener "Jellyfin" "8096" || true

  # Fallback por nombre de proceso para instancias en puertos inesperados.
  pkill -f "/Applications/Radarr.app/Contents/MacOS/Radarr" >/dev/null 2>&1 || true
  pkill -f "/Applications/Prowlarr.app/Contents/MacOS/Prowlarr" >/dev/null 2>&1 || true
  pkill -f "/Applications/Jellyfin.app/Contents/MacOS/Jellyfin Server" >/dev/null 2>&1 || true
  pkill -f "/Applications/Jellyfin.app/Contents/MacOS/jellyfin" >/dev/null 2>&1 || true

  local active=0
  for port in 7878 9696 9091 8096; do
    if is_port_listening "$port"; then
      active=1
      break
    fi
  done

  if [ "$active" -eq 0 ]; then
    log "Stack multimedia detenido completamente."
    return 0
  fi

  log "WARN: Stack multimedia detenido parcialmente."
  return 1
}

log "run_service.sh iniciado"

if [ "$STOP_MEDIA_STACK_ONLY" -eq 1 ]; then
  stop_media_stack || true
  log "Modo stop-stack-only finalizado."
  exit 0
fi

# Iniciar servicios multimedia:
# - Siempre en modo start-stack-only.
# - En modo full solo si SHAGGY_START_MEDIA_STACK=true.
STACK_SHOULD_START=0
if [ "$START_MEDIA_STACK_ONLY" -eq 1 ]; then
  STACK_SHOULD_START=1
else
  STACK_ENABLED_RAW="${SHAGGY_START_MEDIA_STACK:-}"
  if [ -z "$STACK_ENABLED_RAW" ] && [ -f "$PROJECT_DIR/.env" ]; then
    STACK_ENABLED_RAW="$(
      awk -F= '/^[[:space:]]*SHAGGY_START_MEDIA_STACK[[:space:]]*=/ {
        value=$2
        sub(/^[[:space:]]+/, "", value)
        sub(/[[:space:]]+$/, "", value)
        gsub(/"/, "", value)
        gsub(/\047/, "", value)
        print value
        exit
      }' "$PROJECT_DIR/.env"
    )"
  fi
  STACK_ENABLED_RAW="${STACK_ENABLED_RAW:-0}"
  STACK_ENABLED="$(echo "$STACK_ENABLED_RAW" | tr '[:upper:]' '[:lower:]')"
  if [[ "$STACK_ENABLED" =~ ^(1|true|yes|on)$ ]]; then
    STACK_SHOULD_START=1
  fi
fi

if [ "$STACK_SHOULD_START" -eq 1 ]; then
  kill_rogue_media_on_8000 || true
  ensure_servarr_headless "Radarr" "7878" "radarr" "Radarr" "Radarr" "$LOG_DIR/radarr.log" || true
  ensure_servarr_headless "Prowlarr" "9696" "prowlarr" "Prowlarr" "Prowlarr" "$LOG_DIR/prowlarr.log" || true
  ensure_service_running "Transmission" "9091" "Transmission" "transmission-daemon" "transmission" || true
  ensure_jellyfin_running || true
  kill_rogue_media_on_8000 || true
else
  log "SHAGGY_START_MEDIA_STACK=${STACK_ENABLED_RAW:-0}, se omite autoinicio de stack multimedia"
fi

if [ "$START_MEDIA_STACK_ONLY" -eq 1 ]; then
  log "Modo stack-only finalizado."
  exit 0
fi

if ! curl -sf "http://localhost:11434/api/tags" >/dev/null 2>&1; then
  if command -v ollama >/dev/null 2>&1; then
    log "Iniciando ollama serve"
    nohup "$(command -v ollama)" serve >> "$OLLAMA_LOG" 2>&1 &
    sleep 2
  else
    log "ERROR: no se encontro comando 'ollama'"
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
  log "Iniciando $VENV_PYTHON -m app.main"
  exec "$VENV_PYTHON" -m app.main
fi

log "WARN: venv no encontrado, usando python del sistema"
exec python -m app.main
