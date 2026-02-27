"""
Crea un acceso directo en el escritorio para abrir el Agente IA
en el navegador (http://localhost:8000).
Compatible con Windows, macOS y Linux.
"""

import os
import sys
import platform
import stat
import shutil
import shlex
import subprocess
import json
import tempfile
from pathlib import Path


def get_desktop_path() -> Path:
    """Obtiene la ruta del escritorio del usuario."""
    system = platform.system()

    if system == "Windows":
        # Usar variable de entorno o ruta por defecto
        desktop = os.environ.get("USERPROFILE", "")
        if desktop:
            return Path(desktop) / "Desktop"
        return Path.home() / "Desktop"

    elif system == "Darwin":  # macOS
        return Path.home() / "Desktop"

    else:  # Linux
        # Intentar obtener de xdg-user-dirs
        try:
            import subprocess
            result = subprocess.run(
                ["xdg-user-dir", "DESKTOP"],
                capture_output=True, text=True
            )
            if result.returncode == 0 and result.stdout.strip():
                return Path(result.stdout.strip())
        except FileNotFoundError:
            pass
        return Path.home() / "Desktop"


def create_windows_shortcut(desktop: Path, agent_name: str, url: str) -> Path:
    """Crea un archivo .bat en Windows."""
    shortcut_path = desktop / f"{agent_name}.bat"
    content = f"""@echo off
REM Acceso directo para {agent_name}
start "" "{url}"
"""
    shortcut_path.write_text(content, encoding="utf-8")
    print(f"  Creado: {shortcut_path}")

    # Tambien crear .url (acceso directo de internet)
    url_path = desktop / f"{agent_name}.url"
    url_content = f"""[InternetShortcut]
URL={url}
IconIndex=0
"""
    url_path.write_text(url_content, encoding="utf-8")
    print(f"  Creado: {url_path}")
    return shortcut_path


def _build_icns_from_image(source_image: Path, out_icns: Path) -> bool:
    """Convierte una imagen PNG/JPG a .icns usando sips + iconutil (macOS)."""
    if not source_image.exists():
        return False

    iconutil = shutil.which("iconutil")
    sips = shutil.which("sips")
    if not iconutil or not sips:
        return False

    icon_names = [
        (16, "icon_16x16.png"),
        (32, "icon_16x16@2x.png"),
        (32, "icon_32x32.png"),
        (64, "icon_32x32@2x.png"),
        (128, "icon_128x128.png"),
        (256, "icon_128x128@2x.png"),
        (256, "icon_256x256.png"),
        (512, "icon_256x256@2x.png"),
        (512, "icon_512x512.png"),
        (1024, "icon_512x512@2x.png"),
    ]

    try:
        with tempfile.TemporaryDirectory() as tmp:
            iconset_dir = Path(tmp) / "shaggy.iconset"
            iconset_dir.mkdir(parents=True, exist_ok=True)

            for size, name in icon_names:
                subprocess.run(
                    [sips, "-z", str(size), str(size), str(source_image), "--out", str(iconset_dir / name)],
                    check=True,
                    capture_output=True,
                    text=True,
                )

            subprocess.run(
                [iconutil, "-c", "icns", str(iconset_dir), "-o", str(out_icns)],
                check=True,
                capture_output=True,
                text=True,
            )
        return out_icns.exists()
    except Exception:
        return False


def create_macos_shortcut(desktop: Path, agent_name: str, url: str, project_dir: Path) -> Path:
    """
    Crea un launcher para macOS:
    - Archivo .command que levanta Ollama/servidor si hace falta y abre la web.
    - App .app (doble clic) con icono de aplicacion.
    """
    shortcut_path = desktop / f"{agent_name}.command"
    launcher_log = project_dir / "data" / "logs" / "launcher.log"
    server_log = project_dir / "data" / "logs" / "server.log"
    ollama_log = project_dir / "data" / "logs" / "ollama.log"

    project_q = shlex.quote(str(project_dir))
    url_q = shlex.quote(url)
    launcher_q = shlex.quote(str(launcher_log))
    server_q = shlex.quote(str(server_log))
    ollama_q = shlex.quote(str(ollama_log))
    health_q = shlex.quote(f"{url}/health")

    content = f"""#!/bin/bash
# Acceso directo para {agent_name}
set -euo pipefail

PROJECT_DIR={project_q}
APP_URL={url_q}
HEALTH_URL={health_q}
LAUNCHER_LOG={launcher_q}
SERVER_LOG={server_q}
OLLAMA_LOG={ollama_q}

mkdir -p "$(dirname "$LAUNCHER_LOG")"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] Lanzador iniciado" >> "$LAUNCHER_LOG"

# 1) Backend FastAPI (+ stack multimedia via run_service.sh)
if ! curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
    echo "[INFO] Iniciando backend..." >> "$LAUNCHER_LOG"
    cd "$PROJECT_DIR"
    if [ -x "$PROJECT_DIR/scripts/run_service.sh" ]; then
        nohup "$PROJECT_DIR/scripts/run_service.sh" >> "$SERVER_LOG" 2>&1 &
    else
        # Fallback de compatibilidad
        if ! curl -sf "http://localhost:11434/api/tags" > /dev/null 2>&1; then
            echo "[INFO] Iniciando ollama serve (fallback)..." >> "$LAUNCHER_LOG"
            nohup ollama serve >> "$OLLAMA_LOG" 2>&1 &
            sleep 2
        fi
        if [ -d "venv" ]; then
            source venv/bin/activate
        elif [ -d ".venv" ]; then
            source .venv/bin/activate
        fi
        nohup python -m app.main >> "$SERVER_LOG" 2>&1 &
    fi
fi

# 2) Esperar a que este listo y abrir navegador
for _ in {{1..15}}; do
    if curl -sf "$HEALTH_URL" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

open "$APP_URL"
"""
    shortcut_path.write_text(content, encoding="utf-8")
    # Hacer ejecutable
    shortcut_path.chmod(shortcut_path.stat().st_mode | stat.S_IEXEC)
    print(f"  Creado: {shortcut_path}")

    # Crear .app para doble clic con icono de app
    app_path = desktop / f"{agent_name}.app"
    shell_cmd = f"bash {shlex.quote(str(shortcut_path))} >/dev/null 2>&1 &"
    jxa_script = (
        "var app = Application.currentApplication();\n"
        "app.includeStandardAdditions = true;\n"
        f"app.doShellScript({json.dumps(shell_cmd)});\n"
    )

    try:
        subprocess.run(
            [
                "osacompile",
                "-l",
                "JavaScript",
                "-o",
                str(app_path),
                "-e",
                jxa_script,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        print(f"  Creado: {app_path}")

        applet_icon = app_path / "Contents" / "Resources" / "applet.icns"
        icon_applied = False

        # Prioridad 1: icono personalizado del proyecto.
        custom_icon_candidates = [
            project_dir / "frontend" / "shaggy.png",
            project_dir / "frontend" / "shaggy.PNG",
            project_dir.parent / "imagen_shaggy.PNG",
            project_dir.parent / "imagen_shaggy.png",
        ]
        for candidate in custom_icon_candidates:
            if not candidate.exists():
                continue
            with tempfile.TemporaryDirectory() as tmp:
                custom_icns = Path(tmp) / "shaggy.icns"
                if _build_icns_from_image(candidate, custom_icns) and applet_icon.exists():
                    shutil.copyfile(custom_icns, applet_icon)
                    print(f"  Icono personalizado aplicado desde: {candidate}")
                    icon_applied = True
                    break

        # Prioridad 2: fallback al icono generico del sistema.
        if not icon_applied:
            system_icon = Path(
                "/System/Library/CoreServices/CoreTypes.bundle/Contents/Resources/GenericApplicationIcon.icns"
            )
            if system_icon.exists() and applet_icon.exists():
                shutil.copyfile(system_icon, applet_icon)
                print("  Icono generico aplicado al .app (fallback)")
    except FileNotFoundError:
        print("  Aviso: osacompile no disponible. Solo se creo el .command.")
    except subprocess.CalledProcessError as exc:
        print(f"  Aviso: no se pudo crear .app: {exc.stderr.strip()}")

    return shortcut_path


def create_linux_shortcut(desktop: Path, agent_name: str, url: str) -> Path:
    """Crea un archivo .desktop en Linux."""
    shortcut_path = desktop / f"{agent_name.lower().replace(' ', '-')}.desktop"
    content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={agent_name}
Comment=Abrir {agent_name} en el navegador
Exec=xdg-open {url}
Icon=web-browser
Terminal=false
Categories=Utility;
"""
    shortcut_path.write_text(content, encoding="utf-8")
    # Hacer ejecutable
    shortcut_path.chmod(shortcut_path.stat().st_mode | stat.S_IEXEC)
    print(f"  Creado: {shortcut_path}")
    return shortcut_path


def main():
    agent_name = "Shaggy"
    url = "http://localhost:8000"
    project_dir = Path(__file__).resolve().parent.parent
    system = platform.system()

    print(f"=== Creando acceso directo para {agent_name} ===")
    print(f"Sistema: {system}")
    print(f"URL: {url}")

    desktop = get_desktop_path()

    if not desktop.exists():
        print(f"Error: No se encontro el escritorio en {desktop}")
        print("Puedes crear el acceso directo manualmente.")
        sys.exit(1)

    print(f"Escritorio: {desktop}")
    print()

    try:
        if system == "Windows":
            create_windows_shortcut(desktop, agent_name, url)
        elif system == "Darwin":
            create_macos_shortcut(desktop, agent_name, url, project_dir)
        else:
            create_linux_shortcut(desktop, agent_name, url)

        print()
        print("Acceso directo creado exitosamente.")
        if system == "Darwin":
            print(f"Haz doble clic en '{agent_name}.app' (o '{agent_name}.command') en tu escritorio.")
        else:
            print(f"Haz doble clic en '{agent_name}' en tu escritorio para abrir el agente.")

    except PermissionError:
        print(f"Error: Sin permisos para escribir en {desktop}")
        print("Ejecuta el script con permisos de administrador o crealo manualmente.")
        sys.exit(1)
    except Exception as e:
        print(f"Error inesperado: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
