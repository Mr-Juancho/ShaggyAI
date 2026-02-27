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
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.config import BASE_DIR, logger
from app.json_guard import generate_validated_json

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
_MEDIA_FOLLOWUP_START_RE = re.compile(
    r"^\s*(?:ya\s+)?(?:act[ií]va(?:lo)?|act[ií]var(?:lo)?|"
    r"inicia(?:lo)?|iniciar(?:lo)?|arranca(?:lo)?|arrancar(?:lo)?|"
    r"enci[eé]nde(?:lo)?|encender(?:lo)?|prende(?:lo)?|prender(?:lo)?|"
    r"levanta(?:lo)?|levantar(?:lo)?|habilita(?:lo)?|habilitar(?:lo)?)\s*[.!?¡¿]*\s*$",
    flags=re.IGNORECASE,
)
_MEDIA_FOLLOWUP_STOP_RE = re.compile(
    r"^\s*(?:ya\s+)?(?:ap[aá]ga(?:lo)?|apagar(?:lo)?|desactiva(?:lo)?|desactivar(?:lo)?|"
    r"det[eé]n(?:lo)?|detener(?:lo)?|cierra(?:lo)?|cerrar(?:lo)?|"
    r"termina(?:lo)?|terminar(?:lo)?|m[aá]ta(?:lo)?|matar(?:lo)?)\s*[.!?¡¿]*\s*$",
    flags=re.IGNORECASE,
)
_MEDIA_CONTEXT_HINT_RE = re.compile(
    r"\b(protocolo|stack|pel[ií]culas?|cine|media|radarr|prowlarr|transmission|jellyfin)\b|"
    r"Radarr:|Prowlarr:|Transmission:|Jellyfin:",
    flags=re.IGNORECASE,
)
_MEDIA_STACK_EXPLICIT_HINT_RE = re.compile(
    r"\b(radarr|prowlarr|transmission|jellyfin|"
    r"pel[ií]culas|cine|media|multimedia|"
    r"modo\s+pel[ií]culas?|stack\s+de\s+pel[ií]culas?)\b",
    flags=re.IGNORECASE,
)
_MEDIA_STACK_GENERIC_HINT_RE = re.compile(
    r"\b(protocolo|stack|servicios?|modo)\b",
    flags=re.IGNORECASE,
)
_MEDIA_SEMANTIC_ACTION_HINT_RE = re.compile(
    r"\b(activa(?:r)?(?:lo)?|inicia(?:r)?(?:lo)?|arranca(?:r)?(?:lo)?|"
    r"enciende(?:r)?(?:lo)?|prende(?:r)?(?:lo)?|levanta(?:r)?(?:lo)?|"
    r"habilita(?:r)?(?:lo)?|apaga(?:r)?(?:lo)?|desactiva(?:r)?(?:lo)?|"
    r"det[eé]n(?:er)?(?:lo)?|cierra(?:r)?(?:lo)?|termina(?:r)?(?:lo)?|"
    r"estado|status|estatus|reinicia(?:r)?(?:lo)?|reactiva(?:r)?(?:lo)?|"
    r"encendido|apagado|arriba|caido|caído|"
    r"ponlo|ponla|hazlo|d[eé]jalo)\b",
    flags=re.IGNORECASE,
)
_SHORT_IMPERATIVE_RE = re.compile(
    r"^\s*(?:ok[, ]+)?(?:ya\s+)?(?:hazlo|dale|listo|ponlo|ponla|enciendelo|enciéndelo|"
    r"apágalo|apagalo|actívalo|activalo|deténlo|detenlo|reinícialo|reinicialo)\s*[.!?¡¿]*\s*$",
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


class MediaStackSemanticDecision(BaseModel):
    """Salida estructurada para clasificador semántico del stack multimedia."""

    action: str = "none"  # start | stop | status | none
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    rationale: str = ""


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


def _has_recent_media_stack_context(recent_messages: Optional[list[str]], max_items: int = 6) -> bool:
    """Evalúa si hay contexto reciente del protocolo multimedia en el historial."""
    if not recent_messages:
        return False
    inspected = 0
    for raw in reversed(recent_messages):
        text = (raw or "").strip()
        if not text:
            continue
        if _MEDIA_CONTEXT_HINT_RE.search(text):
            return True
        inspected += 1
        if inspected >= max_items:
            break
    return False


def looks_like_media_stack_semantic_candidate(
    message: str,
    recent_messages: Optional[list[str]] = None,
) -> bool:
    """
    Heurística de gate para decidir cuándo invocar clasificación semántica.
    Evita costo/latencia en mensajes que claramente no son del stack multimedia.
    """
    text = (message or "").strip()
    if not text:
        return False

    has_explicit_media_words = bool(_MEDIA_STACK_EXPLICIT_HINT_RE.search(text))
    if has_explicit_media_words:
        return True

    has_generic_stack_words = bool(_MEDIA_STACK_GENERIC_HINT_RE.search(text))
    has_recent_context = _has_recent_media_stack_context(recent_messages)
    if not has_recent_context:
        return False

    if has_generic_stack_words and bool(_MEDIA_SEMANTIC_ACTION_HINT_RE.search(text)):
        return True

    if _SHORT_IMPERATIVE_RE.match(text):
        return True

    token_count = len(re.findall(r"[a-zA-Z0-9áéíóúñü]+", text))
    return token_count <= 10 and bool(_MEDIA_SEMANTIC_ACTION_HINT_RE.search(text))


def looks_like_media_stack_followup_start_request(
    message: str,
    recent_messages: Optional[list[str]] = None,
) -> bool:
    """
    Detecta follow-ups anafóricos ("actívalo") cuando el contexto reciente
    indica que el usuario venía hablando del protocolo de películas.
    """
    text = (message or "").strip()
    if not text:
        return False
    if _NEGATED_START_RE.search(text):
        return False
    if not _MEDIA_FOLLOWUP_START_RE.match(text):
        return False
    return _has_recent_media_stack_context(recent_messages)


def looks_like_media_stack_followup_stop_request(
    message: str,
    recent_messages: Optional[list[str]] = None,
) -> bool:
    """Detecta follow-ups de apagado ("apágalo") con contexto multimedia."""
    text = (message or "").strip()
    if not text:
        return False
    if _NEGATED_STOP_RE.search(text):
        return False
    if not _MEDIA_FOLLOWUP_STOP_RE.match(text):
        return False
    return _has_recent_media_stack_context(recent_messages)


async def infer_media_stack_action_semantic(
    message: str,
    recent_messages: Optional[list[str]],
    llm_engine: Any,
    min_confidence: float = 0.62,
) -> MediaStackSemanticDecision:
    """
    Clasifica intención del protocolo multimedia por semántica.
    Devuelve action=start|stop|status|none y confidence.
    """
    if not llm_engine or not looks_like_media_stack_semantic_candidate(message, recent_messages):
        return MediaStackSemanticDecision(action="none", confidence=0.0)

    history_lines = "\n".join(
        f"- {line[:180]}"
        for line in (recent_messages or [])[-6:]
    ) or "- (sin historial relevante)"

    system_prompt = (
        "Eres un clasificador semantico para control de un stack multimedia local. "
        "Debes responder SOLO JSON valido."
    )
    user_prompt = (
        "Clasifica la intencion del usuario respecto al stack multimedia "
        "(Radarr, Prowlarr, Transmission, Jellyfin).\n"
        "Devuelve accion exacta: start, stop, status o none.\n"
        "Usa historial para resolver anaforas como 'activalo', 'ponlo en marcha', "
        "'apagalo', 'como va eso'.\n"
        f"Mensaje actual: {message}\n"
        f"Historial reciente:\n{history_lines}\n"
        "Schema requerido:\n"
        "{\n"
        '  "action": "start|stop|status|none",\n'
        '  "confidence": 0.0,\n'
        '  "rationale": "breve"\n'
        "}"
    )

    parsed, trace = await generate_validated_json(
        llm_engine=llm_engine,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_model=MediaStackSemanticDecision,
        max_retries=2,
    )
    if not parsed:
        if trace.last_error:
            logger.warning(f"Clasificador semantico media stack invalido: {trace.last_error}")
        return MediaStackSemanticDecision(action="none", confidence=0.0)

    action = (parsed.action or "").strip().lower()
    if action not in {"start", "stop", "status", "none"}:
        action = "none"

    confidence = float(parsed.confidence or 0.0)
    if action != "none" and confidence < min_confidence:
        return MediaStackSemanticDecision(action="none", confidence=confidence, rationale=parsed.rationale)

    return MediaStackSemanticDecision(action=action, confidence=confidence, rationale=parsed.rationale)


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
