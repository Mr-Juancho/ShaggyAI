#!/bin/bash
# Instala autoinicio de RUFÜS en macOS usando LaunchAgent de usuario.
# Copia el runtime a ~/Library/Application Support/RUFUS/runtime para evitar
# bloqueos de permisos de launchd cuando el proyecto vive en Desktop/Documents.
# Uso:
#   bash scripts/install_autostart_macos.sh

set -euo pipefail

SOURCE_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SOURCE_PROJECT_DIR="$(dirname "$SOURCE_SCRIPT_DIR")"
RUNTIME_BASE="$HOME/Library/Application Support/RUFUS"
RUNTIME_DIR="$RUNTIME_BASE/runtime"
LAUNCH_HELPER="$RUNTIME_BASE/launch_rufus.sh"

LAUNCH_AGENT_DIR="$HOME/Library/LaunchAgents"
PLIST_PATH="$LAUNCH_AGENT_DIR/com.rufus.agent.plist"
RUN_SCRIPT="$RUNTIME_DIR/scripts/run_service.sh"
LOG_DIR="$RUNTIME_DIR/data/logs"
NEW_LABEL="com.rufus.agent"

mkdir -p "$LAUNCH_AGENT_DIR" "$LOG_DIR" "$RUNTIME_BASE"

# Sincronizar runtime oculto (fuera de Desktop)
if [ ! -d "$RUNTIME_DIR" ]; then
  mkdir -p "$RUNTIME_DIR"
fi
rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '.pycache_local' \
  --exclude 'Desktop' \
  --exclude 'data/logs/*' \
  "$SOURCE_PROJECT_DIR/" "$RUNTIME_DIR/"

chmod +x "$RUN_SCRIPT"

cat > "$LAUNCH_HELPER" <<EOF
#!/bin/bash
set -euo pipefail

APP_URL='http://localhost:8000'
HEALTH_URL='http://localhost:8000/health'
PRIMARY_LABEL='$NEW_LABEL'
PLIST_PATH='$PLIST_PATH'

if ! curl -sf "\$HEALTH_URL" >/dev/null 2>&1; then
  # Asegurar que el agente actual este cargado (si no lo esta, bootstrap).
  launchctl print "gui/\$(id -u)/\$PRIMARY_LABEL" >/dev/null 2>&1 || {
    launchctl bootstrap "gui/\$(id -u)" "\$PLIST_PATH" 2>/dev/null || true
    launchctl enable "gui/\$(id -u)/\$PRIMARY_LABEL" 2>/dev/null || true
  }

  launchctl kickstart -k "gui/\$(id -u)/\$PRIMARY_LABEL" 2>/dev/null || true

  for _ in {1..25}; do
    if curl -sf "\$HEALTH_URL" >/dev/null 2>&1; then
      break
    fi
    sleep 1
  done
fi

open "\$APP_URL"
EOF

chmod +x "$LAUNCH_HELPER"

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$NEW_LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$RUN_SCRIPT</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$RUNTIME_DIR</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>ThrottleInterval</key>
  <integer>10</integer>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>

  <key>StandardOutPath</key>
  <string>$LOG_DIR/launchd.out.log</string>

  <key>StandardErrorPath</key>
  <string>$LOG_DIR/launchd.err.log</string>
</dict>
</plist>
EOF

# Limpiar variantes anteriores del agente (sin exponer datos personales en labels).
cleanup_legacy_pattern() {
  local pattern="$1"
  for legacy_plist in $pattern; do
    [ -e "$legacy_plist" ] || continue
    if [ "$legacy_plist" = "$PLIST_PATH" ]; then
      continue
    fi
    legacy_label="$(basename "$legacy_plist" .plist)"
    launchctl bootout "gui/$(id -u)/$legacy_label" 2>/dev/null || true
    launchctl disable "gui/$(id -u)/$legacy_label" 2>/dev/null || true
    rm -f "$legacy_plist" || true
  done
}

cleanup_legacy_pattern "$LAUNCH_AGENT_DIR"/com.*shag*.plist
cleanup_legacy_pattern "$LAUNCH_AGENT_DIR"/com.*rufus*.plist

launchctl bootout "gui/$(id -u)" "$PLIST_PATH" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST_PATH"
launchctl enable "gui/$(id -u)/$NEW_LABEL"
launchctl kickstart -k "gui/$(id -u)/$NEW_LABEL"

echo "Autoinicio instalado."
echo "PLIST: $PLIST_PATH"
echo "Runtime: $RUNTIME_DIR"
echo "Launcher App: $LAUNCH_HELPER"
echo "Estado:"
launchctl print "gui/$(id -u)/$NEW_LABEL" | sed -n '1,80p'
