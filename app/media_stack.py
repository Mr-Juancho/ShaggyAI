"""
Control del stack multimedia (Radarr, Prowlarr, Transmission, Jellyfin).

Permite:
- Detectar intenciones tipo "inicia protocolo peliculas".
- Detectar intenciones tipo "apaga protocolo peliculas".
- Levantar servicios en segundo plano (headless) usando run_service.sh.
- Consultar estado por puertos locales.
"""

from __future__ import annotations

import asyncio
import os
import re
import socket
from pathlib import Path
from typing import Optional

from app.config import BASE_DIR, logger

MEDIA_SERVICE_PORTS: dict[str, int] = {
    "Radarr": 7878,
    "Prowlarr": 9696,
    "Transmission": 9091,
    "Jellyfin": 8096,
}

_MEDIA_START_VERB_RE = re.compile(
    r"\b(inicia|iniciar|arranca|arrancar|activa|activar|enciende|encender|"
    r"levanta|levantar|prende|habilita|habilitar)\b",
    flags=re.IGNORECASE,
)
_MEDIA_SCOPE_RE = re.compile(
    r"\b(protocolo|stack|servicios?|modo)\b.{0,35}\b(pel[ií]culas?|cine|media)\b|"
    r"\b(pel[ií]culas?|cine|media)\b.{0,35}\b(protocolo|stack|servicios?|modo)\b",
    flags=re.IGNORECASE,
)
_MEDIA_STATUS_RE = re.compile(
    r"\b(estado|estatus|status|activo|activos|encendido|encendidos|"
    r"disponible|disponibles)\b.{0,40}\b(protocolo|stack|pel[ií]culas?|media)\b|"
    r"\b(protocolo|stack)\b.{0,40}\b(pel[ií]culas?|media)\b.{0,40}\b(estado|activo)\b",
    flags=re.IGNORECASE,
)
_MEDIA_STOP_VERB_RE = re.compile(
    r"\b(apaga|apagar|det[eé]n|deten|detener|desactiva|desactivar|"
    r"cierra|cerrar|termina|terminar|mata|matar|kill|apaga)\b",
    flags=re.IGNORECASE,
)
_NEGATED_START_RE = re.compile(
    r"\b(no|nunca)\b.{0,10}\b(inicies?|arranques?|actives?|enciendas?|"
    r"levantes?|prendas?|habilites?)\b",
    flags=re.IGNORECASE,
)
_NEGATED_STOP_RE = re.compile(
    r"\b(no|nunca)\b.{0,12}\b(apagues?|detengas?|desactives?|cierres?|"
    r"termines?|mates?)\b",
    flags=re.IGNORECASE,
)


def _is_local_port_open(port: int, timeout_seconds: float = 0.35) -> bool:
    """Verifica si un puerto local está escuchando."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout_seconds)
    try:
        return sock.connect_ex(("127.0.0.1", port)) == 0
    except OSError:
        return False
    finally:
        sock.close()


def get_media_stack_status() -> dict[str, bool]:
    """Retorna estado actual del stack multimedia."""
    return {
        service: _is_local_port_open(port)
        for service, port in MEDIA_SERVICE_PORTS.items()
    }


def looks_like_media_stack_start_request(message: str) -> bool:
    """Detecta si el usuario quiere iniciar el protocolo de películas."""
    text = (message or "").strip()
    if not text:
        return False

    if re.match(r"^\s*/movie_on\s*$", text, flags=re.IGNORECASE):
        return True
    if _NEGATED_START_RE.search(text):
        return False
    return bool(_MEDIA_START_VERB_RE.search(text) and _MEDIA_SCOPE_RE.search(text))


def looks_like_media_stack_status_request(message: str) -> bool:
    """Detecta si el usuario consulta estado del protocolo de películas."""
    text = (message or "").strip()
    if not text:
        return False
    if re.match(r"^\s*/movie_status\s*$", text, flags=re.IGNORECASE):
        return True
    return bool(_MEDIA_STATUS_RE.search(text))


def looks_like_media_stack_stop_request(message: str) -> bool:
    """Detecta si el usuario quiere apagar el protocolo de películas."""
    text = (message or "").strip()
    if not text:
        return False

    if re.match(r"^\s*/movie_off\s*$", text, flags=re.IGNORECASE):
        return True
    if _NEGATED_STOP_RE.search(text):
        return False
    return bool(_MEDIA_STOP_VERB_RE.search(text) and _MEDIA_SCOPE_RE.search(text))


def _format_status_line(status: dict[str, bool]) -> str:
    """Construye línea compacta de estado legible para chat."""
    parts: list[str] = []
    for service, is_up in status.items():
        icon = "OK" if is_up else "OFF"
        parts.append(f"{service}:{icon}")
    return " | ".join(parts)


def _build_env_for_subprocess() -> dict[str, str]:
    """Prepara entorno con PATH explícito para lanzar servicios desde app."""
    env = os.environ.copy()
    path_value = env.get("PATH", "")
    required_paths = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    merged: list[str] = []
    for path_item in required_paths + path_value.split(":"):
        path_item = path_item.strip()
        if not path_item:
            continue
        if path_item not in merged:
            merged.append(path_item)
    env["PATH"] = ":".join(merged)

    # Evita que procesos .NET (*arr) hereden puerto del backend (PORT=8000).
    for key in (
        "PORT",
        "URLS",
        "ASPNETCORE_URLS",
        "ASPNETCORE_HTTP_PORTS",
        "ASPNETCORE_HTTPS_PORTS",
        "DOTNET_URLS",
    ):
        env.pop(key, None)

    return env


async def start_media_stack_headless(timeout_seconds: int = 120) -> tuple[bool, dict[str, bool], str]:
    """
    Inicia stack multimedia de forma headless y retorna:
    (success, status_por_servicio, detalle_error_opcional).
    """
    script_path: Path = BASE_DIR / "scripts" / "run_service.sh"
    if not script_path.exists():
        return False, get_media_stack_status(), f"No existe script: {script_path}"

    cmd = ["bash", str(script_path), "--start-media-stack-only"]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(BASE_DIR),
            env=_build_env_for_subprocess(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return False, get_media_stack_status(), "Timeout iniciando stack multimedia."

        stdout_text = (stdout_raw or b"").decode("utf-8", errors="ignore").strip()
        stderr_text = (stderr_raw or b"").decode("utf-8", errors="ignore").strip()
        status = get_media_stack_status()

        if process.returncode != 0:
            detail = stderr_text or stdout_text or f"Codigo de salida: {process.returncode}"
            return False, status, detail[:400]

        if not any(status.values()):
            detail = stderr_text or stdout_text or "Ningun servicio quedo activo."
            return False, status, detail[:400]

        return True, status, ""
    except Exception as exc:
        logger.error(f"Error arrancando stack multimedia headless: {exc}")
        return False, get_media_stack_status(), str(exc)


async def stop_media_stack_headless(timeout_seconds: int = 120) -> tuple[bool, dict[str, bool], str]:
    """
    Apaga stack multimedia de forma headless y retorna:
    (success, status_por_servicio, detalle_error_opcional).
    """
    script_path: Path = BASE_DIR / "scripts" / "run_service.sh"
    if not script_path.exists():
        return False, get_media_stack_status(), f"No existe script: {script_path}"

    cmd = ["bash", str(script_path), "--stop-media-stack-only"]
    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(BASE_DIR),
            env=_build_env_for_subprocess(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return False, get_media_stack_status(), "Timeout apagando stack multimedia."

        stdout_text = (stdout_raw or b"").decode("utf-8", errors="ignore").strip()
        stderr_text = (stderr_raw or b"").decode("utf-8", errors="ignore").strip()
        status = get_media_stack_status()

        if process.returncode != 0:
            detail = stderr_text or stdout_text or f"Codigo de salida: {process.returncode}"
            return False, status, detail[:400]

        if any(status.values()):
            detail = stderr_text or stdout_text or "Quedaron servicios activos."
            return False, status, detail[:400]

        return True, status, ""
    except Exception as exc:
        logger.error(f"Error apagando stack multimedia headless: {exc}")
        return False, get_media_stack_status(), str(exc)


def build_media_stack_status_response(status: Optional[dict[str, bool]] = None) -> str:
    """Respuesta legible para usuario con estado de stack multimedia."""
    resolved = status or get_media_stack_status()
    up = [service for service, ok in resolved.items() if ok]
    down = [service for service, ok in resolved.items() if not ok]

    if up and not down:
        return (
            "Protocolo peliculas activo en segundo plano.\n"
            f"Estado: {_format_status_line(resolved)}"
        )
    if not up:
        return (
            "Protocolo peliculas apagado.\n"
            f"Estado: {_format_status_line(resolved)}"
        )
    return (
        "Protocolo peliculas activo parcialmente.\n"
        f"Estado: {_format_status_line(resolved)}"
    )
