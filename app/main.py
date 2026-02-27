"""
Servidor principal FastAPI del agente de IA.
Integra chat, memoria, Telegram, recordatorios y busqueda web.
"""

import asyncio
import re
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from html import unescape
from typing import Any, Optional
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import FRONTEND_DIR, HOST, MAX_CONTEXT_MESSAGES, PORT, RELOAD, logger
from app.llm_engine import OllamaEngine
from app.media_stack import (
    build_media_stack_status_response,
    get_media_stack_status,
    looks_like_media_stack_start_request,
    looks_like_media_stack_stop_request,
    looks_like_media_stack_status_request,
    start_media_stack_headless,
    stop_media_stack_headless,
)
from app.system_prompt import build_system_prompt
from app.utils import contains_datetime_reference, extract_search_intent, truncate_text

try:
    from app.memory import MemoryManager
except Exception as exc:
    MemoryManager = None  # type: ignore[assignment]
    logger.error(f"No se pudo cargar MemoryManager: {exc}")

try:
    from app.reminders import ReminderManager
except Exception as exc:
    ReminderManager = None  # type: ignore[assignment]
    logger.error(f"No se pudo cargar ReminderManager: {exc}")

try:
    from app.web_search import WebSearchEngine
except Exception as exc:
    WebSearchEngine = None  # type: ignore[assignment]
    logger.error(f"No se pudo cargar WebSearchEngine: {exc}")

try:
    from app.telegram_bot import TelegramBot
except Exception as exc:
    TelegramBot = None  # type: ignore[assignment]
    logger.error(f"No se pudo cargar TelegramBot: {exc}")

try:
    from app.media_handler import RadarrClient
except Exception as exc:
    RadarrClient = None  # type: ignore[assignment]
    logger.error(f"No se pudo cargar RadarrClient: {exc}")


# --- Modelos Pydantic ---
class ChatRequest(BaseModel):
    """Modelo de request para el endpoint /chat."""

    message: str
    user_id: str = "default"
    source: str = "api"  # api, desktop, telegram, test


class ChatResponse(BaseModel):
    """Modelo de response para el endpoint /chat."""

    response: str
    movie: Optional[dict[str, Any]] = None  # Datos de película (Fase 6.5)


class RememberRequest(BaseModel):
    """Modelo de request para /remember."""

    info: str
    user_id: str = "default"
    metadata: Optional[dict[str, Any]] = None


class RememberResponse(BaseModel):
    """Modelo de response para /remember."""

    stored: bool
    message: str


class ReminderCreateRequest(BaseModel):
    """Modelo de request para POST /reminders."""

    text: str = Field(..., min_length=3)
    datetime: Optional[str] = None
    recurring: bool = False
    interval: Optional[str] = None


class ReminderDeleteResponse(BaseModel):
    """Modelo de response para DELETE /reminders/{id}."""

    deleted: bool
    message: str


class RemindersResponse(BaseModel):
    """Modelo de response para GET /reminders."""

    reminders: list[dict[str, Any]]


class HealthResponse(BaseModel):
    """Modelo de response para el endpoint /health."""

    status: str
    ollama: bool
    radarr: Optional[bool] = None


# --- Estado global ---
conversation_history: dict[str, list[dict[str, str]]] = {}
pending_reminder_by_user: dict[str, dict[str, str]] = {}
llm_engine = OllamaEngine()

memory_manager = None
if MemoryManager is not None:
    try:
        memory_manager = MemoryManager()
    except Exception as exc:
        logger.error(f"No se pudo inicializar MemoryManager: {exc}")

reminder_manager = None
if ReminderManager is not None:
    try:
        reminder_manager = ReminderManager()
    except Exception as exc:
        logger.error(f"No se pudo inicializar ReminderManager: {exc}")

web_search_engine = None
if WebSearchEngine is not None:
    try:
        web_search_engine = WebSearchEngine()
    except Exception as exc:
        logger.error(f"No se pudo inicializar WebSearchEngine: {exc}")

radarr_client = None
if RadarrClient is not None:
    try:
        radarr_client = RadarrClient()
    except Exception as exc:
        logger.error(f"No se pudo inicializar RadarrClient: {exc}")

telegram_bot = None
telegram_task: Optional[asyncio.Task] = None
media_stack_start_task: Optional[asyncio.Task] = None

REMINDER_REQUEST_RE = re.compile(
    r"\b(recu[eé]rdame|recordarme|no\s+olvidar|av[ií]same|avisame)\b|"
    r"\b(crea|crear|activa|activar|programa|programar|pon|poner|agenda|agendar)\b.{0,40}\b(recordatorio|aviso)\b",
    flags=re.IGNORECASE,
)
REMINDER_DATETIME_HINT_RE = re.compile(
    r"\b("
    r"mañana|manana|pasado\s+mañana|hoy|esta\s+noche|"
    r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[áa]bado|domingo|"
    r"\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm)|"
    r"a\s*las\s*\d{1,2}|en\s*\d+\s*(?:minutos?|horas?|d[ií]as?)|"
    r"dentro\s*de\s*\d+\s*(?:minutos?|horas?|d[ií]as?)"
    r")\b",
    flags=re.IGNORECASE,
)
REMINDER_FOLLOWUP_TASK_RE = re.compile(
    r"^\s*(para\s+|ir\s+a\s+|tomar|hacer|comer|correr|leer|estudiar|comprar|"
    r"pagar|llamar|enviar|revisar|terminar|empezar)\b",
    flags=re.IGNORECASE,
)
REMINDER_PENDING_CANCEL_RE = re.compile(
    r"\b(cancela|cancelar|olvida|olvidalo|olvídalo|ya\s+no|mejor\s+no)\b",
    flags=re.IGNORECASE,
)
REMINDER_PENDING_TTL_MINUTES = 15
# Detecta preguntas/quejas sobre recordatorios para NO confundirlas con crear uno.
REMINDER_QUESTION_RE = re.compile(
    r"\b(por\s*qu[eé]|porque|como\s+es\s+que|no\s+(?:activaste|funciono|funcionó|llego|llegó|sono|sonó|me\s+avisaste|se\s+activo|se\s+activó))\b"
    r".{0,60}\b(recordatorio|recordatorios|aviso|avisos)\b",
    flags=re.IGNORECASE,
)
REMINDER_LIST_RE = re.compile(
    r"\b(cu[aá]les?|que|qu[eé])\s+(?:recordatorios?|pendientes)\s+(?:tengo|hay)\b|"
    r"\b(?:listar|lista|ver|mostrar|muestrame|mu[eé]strame|consultar|consulta|ens[eé]ñame|dime)\b.{0,40}\b(recordatorios?|pendientes)\b|"
    r"^\s*/reminders\s*$",
    flags=re.IGNORECASE,
)
REMINDER_DELETE_RE = re.compile(
    r"\b(elimina|eliminar|borra|borrar|quita|quitar|cancela|cancelar|remueve|remover|completa|completar)\b"
    r"(?:.{0,60}\b(recordatorio|recordatorios|pendiente|pendientes)\b|"
    r"\s+[a-f0-9]{8}\b)",
    flags=re.IGNORECASE,
)
REMINDER_DELETE_ALL_RE = re.compile(
    r"\b(elimina|eliminar|borra|borrar|quita|quitar|cancela|cancelar|completa|completar)\b"
    r".{0,50}\b(todos|todas|todo)\b.{0,30}\b(recordatorios?|pendientes)\b",
    flags=re.IGNORECASE,
)
REMINDER_ID_RE = re.compile(r"\b([a-f0-9]{8})\b", flags=re.IGNORECASE)
REMINDER_DENIAL_RE = re.compile(
    r"(no\s+puedo\s+(?:crear|activar|poner).{0,40}recordatorio|"
    r"no\s+tengo\s+acceso.{0,40}recordatorio|"
    r"no\s+puedo\s+crear\s+el\s+recordatorio\s+por\s+ti)",
    flags=re.IGNORECASE,
)
WEB_INTENT_HINT_RE = re.compile(
    r"\b(busca|buscar|investiga|consulta|averigua|actualiza|actualizar|web|internet|google|precio|cotizaci[oó]n|valor|noticias?|qu[ií]en|presidente|actual)\b",
    flags=re.IGNORECASE,
)
TABLE_SEPARATOR_LINE_RE = re.compile(
    r"^\|?\s*[:\-\u2013\u2014\u2500]{3,}(?:\s*\|\s*[:\-\u2013\u2014\u2500]{3,})+\s*\|?$"
)
HTML_TABLE_TAG_RE = re.compile(r"</?(table|thead|tbody|tr|th|td)\b", flags=re.IGNORECASE)
GENERIC_REFUSAL_RE = re.compile(
    r"^(lo\s+siento[, ]*)?(pero\s+)?no\s+puedo\s+ayudar\s+con\s+eso\.?\s*$",
    flags=re.IGNORECASE,
)
BENIGN_OPINION_HINT_RE = re.compile(
    r"\b(opini[oó]n|opinas|pel[ií]cula|pel[ií]culas|serie|series|libro|m[uú]sica|juego|recomienda|truman)\b",
    flags=re.IGNORECASE,
)
HTML_TAG_RE = re.compile(r"<[^>]+>")
WEB_QUERY_STOPWORDS = {
    "a",
    "actual",
    "actuales",
    "actualiza",
    "actualizar",
    "actualizado",
    "actualizada",
    "actualizados",
    "actualizadas",
    "ahora",
    "averigua",
    "buscar",
    "busca",
    "compara",
    "comparame",
    "comparar",
    "con",
    "consulta",
    "de",
    "del",
    "dato",
    "datos",
    "dinero",
    "bien",
    "el",
    "en",
    "es",
    "esta",
    "este",
    "esto",
    "google",
    "haz",
    "hace",
    "hoy",
    "informacion",
    "internet",
    "investiga",
    "investigar",
    "la",
    "las",
    "los",
    "me",
    "mi",
    "nueva",
    "nuevas",
    "nuevo",
    "nuevos",
    "otra",
    "otravez",
    "para",
    "por",
    "que",
    "repite",
    "repetir",
    "sobre",
    "tabla",
    "ultimas",
    "ultimos",
    "ultimo",
    "ultima",
    "un",
    "una",
    "uno",
    "web",
    "y",
}
WEB_FOLLOWUP_REFRESH_RE = re.compile(
    r"\b(actualiza|actualizar|ultim[oa]s?|datos?\s+recientes|de\s+nuevo|otra\s+vez|nuevamente|repite)\b",
    flags=re.IGNORECASE,
)
WEB_EXPLICIT_REQUEST_RE = re.compile(
    r"^\s*/search\b|"
    r"\b(busca|buscar|buscame|búscame|investiga|investigar|consulta|consultar|"
    r"averigua|averiguar|googlea|googlear|search)\b|"
    r"\b(en\s+(?:la\s+)?(?:web|internet|google))\b",
    flags=re.IGNORECASE,
)


def _normalize_web_query(text: str) -> str:
    """Limpia una consulta para usarla en busqueda web."""
    cleaned = text.strip()
    cleaned = re.sub(
        r"^(puedes|podrias|podr[ií]as|me\s+puedes|me\s+podr[ií]as)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(
        r"^(buscar|busca|investigar|investiga|consultar|consulta)\s+",
        "",
        cleaned,
        flags=re.IGNORECASE,
    )
    return cleaned.strip(" \t\n\r?¡!.,;:")


def _channel_prompt_addendum(source: str) -> str:
    """Instrucciones extra de estilo segun el canal de salida."""
    if source == "telegram":
        return (
            "--- Modo Telegram ---\n"
            "Responde breve y clara.\n"
            "Maximo 6 lineas en respuestas normales.\n"
            "Usa bullets simples cuando ayude.\n"
            "No uses markdown decorativo como **texto** o __texto__.\n"
            "No pongas listas '1) 2) 3)' en la misma linea; cada punto debe ir en linea separada.\n"
            "Evita tablas y bloques largos.\n"
            "Si no hay contexto de busqueda web, no inventes ni cites fuentes externas.\n"
            "No agregues un encabezado de 'Fuentes' si no tienes fuentes reales verificables.\n"
            "Si el usuario pide detalle, entonces amplia."
        )
    return ""


def _is_explicit_web_request(message: str) -> bool:
    """Solo activa web search cuando el usuario lo pide explicitamente."""
    return bool(WEB_EXPLICIT_REQUEST_RE.search(message or ""))


def _tokenize_web_terms(text: str) -> list[str]:
    """Tokeniza texto para estimar si una consulta web tiene señal semantica."""
    return re.findall(r"[a-z0-9áéíóúñ+.-]{2,}", text.lower())


def _is_low_signal_web_query(query: str) -> bool:
    """Detecta consultas genericas tipo 'ultimos datos y haz tabla de nuevo'."""
    tokens = _tokenize_web_terms(query)
    if not tokens:
        return True
    meaningful = [token for token in tokens if token not in WEB_QUERY_STOPWORDS]
    return len(meaningful) == 0


def _extract_topic_hint_from_text(text: str, max_terms: int = 7) -> str:
    """
    Extrae terminos tema de un mensaje previo para enriquecer follow-ups web.
    Ejemplo: 'compara Disney vs Netflix en tabla' -> 'Disney Netflix'.
    """
    cleaned = _normalize_web_query(text)
    cleaned = re.sub(
        r"\b(haz|hace|dame|quiero|necesito)\b",
        " ",
        cleaned,
        flags=re.IGNORECASE,
    )
    cleaned = re.sub(r"\b(de\s+nuevo|otra\s+vez|nuevamente)\b", " ", cleaned, flags=re.IGNORECASE)

    terms: list[str] = []
    seen: set[str] = set()
    for raw_token in re.findall(r"[A-Za-z0-9ÁÉÍÓÚáéíóúÑñ+.-]{2,}", cleaned):
        token_l = raw_token.lower()
        if token_l in WEB_QUERY_STOPWORDS:
            continue
        if token_l.isdigit():
            continue
        if token_l in seen:
            continue
        seen.add(token_l)
        terms.append(raw_token)
        if len(terms) >= max_terms:
            break
    return " ".join(terms).strip()


def _infer_topic_hint_from_history(
    history: list[dict[str, str]],
    current_message: str
) -> str:
    """Busca el ultimo mensaje del usuario con tema util para busqueda web."""
    if not history:
        return ""

    current_l = current_message.strip().lower()
    # Recorremos hacia atras y tomamos el ultimo mensaje de usuario con señal tematica.
    for item in reversed(history[:-1]):
        if item.get("role") != "user":
            continue
        content = item.get("content", "").strip()
        if not content:
            continue
        if content.strip().lower() == current_l:
            continue
        topic_hint = _extract_topic_hint_from_text(content)
        if topic_hint:
            return topic_hint
    return ""


def _remove_reminder_denials(text: str) -> str:
    """Elimina frases donde el modelo niega poder crear recordatorios."""
    lines = [line for line in text.splitlines() if not REMINDER_DENIAL_RE.search(line)]
    cleaned = "\n".join(lines).strip()
    if not cleaned:
        return "Listo, ya programe tu recordatorio."
    return cleaned


def _sanitize_web_text(value: str, max_length: int = 220) -> str:
    """Limpia texto proveniente de buscadores (quita HTML y compacta espacios)."""
    if not value:
        return ""
    cleaned = unescape(str(value))
    cleaned = HTML_TAG_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\n\r-–—|")
    if not cleaned:
        return ""
    return truncate_text(cleaned, max_length=max_length)


def _source_markdown_link(url: str) -> str:
    """Convierte URL en markdown corto con dominio como etiqueta."""
    safe_url = (url or "").strip()
    if not safe_url:
        return ""
    try:
        parsed = urlparse(safe_url)
        domain = (parsed.netloc or "").lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain:
            return f"[{domain}]({safe_url})"
    except Exception:
        pass
    return safe_url


def _format_web_results_for_user(query: str, results: list[dict[str, str]], max_results: int = 5) -> str:
    """Da formato legible y consistente a resultados web para mostrar al usuario."""
    safe_query = _sanitize_web_text(query, max_length=120) or "tu consulta"
    lines = [f"Resultados web para **{safe_query}**:"]

    for idx, result in enumerate(results[:max_results], 1):
        title = _sanitize_web_text(result.get("title", ""), max_length=140) or "Resultado sin titulo"
        snippet = _sanitize_web_text(result.get("snippet", ""), max_length=220)
        source_md = _source_markdown_link(result.get("url", ""))

        detail_parts: list[str] = []
        if snippet:
            detail_parts.append(snippet)
        if source_md:
            detail_parts.append(f"Fuente: {source_md}")

        if detail_parts:
            lines.append(f"{idx}. **{title}** — {' | '.join(detail_parts)}")
        else:
            lines.append(f"{idx}. **{title}**")

    return "\n".join(lines)


def _append_telegram_sources_block(text: str, results: list[dict[str, str]], max_sources: int = 3) -> str:
    """Agrega fuentes reales (dominio) en Telegram cuando hubo busqueda web."""
    if not text:
        return text
    if not results:
        return text
    if re.search(r"\bfuentes?\b", text, flags=re.IGNORECASE):
        return text

    sources: list[str] = []
    seen_domains: set[str] = set()
    for result in results:
        url = (result.get("url", "") or "").strip()
        if not url:
            continue
        try:
            parsed = urlparse(url)
            domain = (parsed.netloc or "").lower()
            if domain.startswith("www."):
                domain = domain[4:]
            if not domain or domain in seen_domains:
                continue
            seen_domains.add(domain)
            sources.append(domain)
            if len(sources) >= max_sources:
                break
        except Exception:
            continue

    if not sources:
        return text

    source_lines = ["Fuentes:", *[f"- {domain}" for domain in sources]]
    return f"{text.strip()}\n\n" + "\n".join(source_lines)


def _get_pending_reminder(user_id: str) -> Optional[dict[str, str]]:
    """Retorna recordatorio pendiente del usuario si no expiro."""
    pending = pending_reminder_by_user.get(user_id)
    if not pending:
        return None

    created_at = pending.get("created_at", "")
    try:
        created_dt = datetime.fromisoformat(created_at)
    except Exception:
        pending_reminder_by_user.pop(user_id, None)
        return None

    if datetime.now() - created_dt > timedelta(minutes=REMINDER_PENDING_TTL_MINUTES):
        pending_reminder_by_user.pop(user_id, None)
        return None
    return pending


def _looks_like_reminder_creation_request(message: str) -> bool:
    """Detecta peticiones de creacion de recordatorio, incluso sin verbo explicito."""
    if REMINDER_REQUEST_RE.search(message):
        return True

    if REMINDER_QUESTION_RE.search(message) or REMINDER_LIST_RE.search(message):
        return False
    if REMINDER_DELETE_RE.search(message):
        return False

    lower = message.lower()
    if re.search(r"\b(recordatorio|aviso)\b", lower) and REMINDER_DATETIME_HINT_RE.search(message):
        return True
    return False


def _looks_like_reminder_task_followup(message: str) -> bool:
    """
    Detecta respuesta de seguimiento con la accion del recordatorio
    (ej: 'para ir a correr').
    """
    cleaned = message.strip()
    if not cleaned:
        return False

    if REMINDER_FOLLOWUP_TASK_RE.search(cleaned):
        return True

    # Mensajes cortos sin signos de pregunta suelen ser accion directa.
    if (
        "?" not in cleaned
        and not _looks_like_reminder_creation_request(cleaned)
        and len(cleaned.split()) <= 8
    ):
        return True
    return False


def _is_generic_refusal(text: str) -> bool:
    """Detecta respuestas de rechazo generico que no ayudan al usuario."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    if len(cleaned) > 140:
        return False
    return bool(GENERIC_REFUSAL_RE.match(cleaned))


def _sanitize_memory_context(context: str) -> str:
    """Quita frases de rechazo generico del contexto de memoria recuperado."""
    if not context:
        return ""

    cleaned_lines: list[str] = []
    for raw_line in context.splitlines():
        line = raw_line.strip()
        if not line:
            cleaned_lines.append(raw_line)
            continue
        if _is_generic_refusal(line):
            continue
        if re.search(r"no\s+puedo\s+ayudar\s+con\s+eso", line, flags=re.IGNORECASE):
            continue
        if "no pude generar una respuesta" in line.lower():
            continue
        cleaned_lines.append(raw_line)

    return "\n".join(cleaned_lines).strip()


def _sanitize_history_for_generation(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """
    Evita mandar al modelo respuestas de rechazo generico del asistente
    para romper bucles de repeticion.
    """
    filtered: list[dict[str, str]] = []
    for item in history:
        role = item.get("role", "")
        content = item.get("content", "")
        if role == "assistant" and _is_generic_refusal(content):
            continue
        filtered.append(item)
    if len(filtered) > MAX_CONTEXT_MESSAGES:
        return filtered[-MAX_CONTEXT_MESSAGES:]
    return filtered


def _normalize_response_format(text: str) -> str:
    """
    Normaliza respuestas malformadas (tablas con pipes rotos o HTML crudo)
    para que sean legibles en web/Telegram.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned

    cleaned = re.sub(r"<br\s*/?>", "\n", cleaned, flags=re.IGNORECASE)
    cleaned = cleaned.replace("｜", "|")

    if HTML_TABLE_TAG_RE.search(cleaned):
        cleaned = re.sub(r"</?(table|thead|tbody)\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<tr\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"</tr>", "\n", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<t[hd]\b[^>]*>", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"</t[hd]>", " | ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<[^>]+>", "", cleaned)

    lines = cleaned.splitlines()

    # Si ya es una tabla markdown valida, NO reescribirla.
    for idx in range(len(lines) - 1):
        header = lines[idx].strip().replace("｜", "|")
        separator = lines[idx + 1].strip().replace("—", "-").replace("–", "-").replace("─", "-")
        if "|" not in header:
            continue
        if not TABLE_SEPARATOR_LINE_RE.match(separator):
            continue

        header_cols = [segment.strip() for segment in header.strip("|").split("|") if segment.strip()]
        if len(header_cols) < 2:
            continue

        row_count = 0
        consistent_rows = 0
        for row_line in lines[idx + 2 :]:
            row = row_line.strip().replace("｜", "|")
            if not row:
                if row_count > 0:
                    break
                continue
            if "|" not in row:
                if row_count > 0:
                    break
                continue
            row_count += 1
            row_cols = [segment.strip() for segment in row.strip("|").split("|") if segment.strip()]
            if len(row_cols) >= max(2, len(header_cols) - 1):
                consistent_rows += 1
        if row_count >= 2 and consistent_rows >= 2:
            return cleaned

    pipe_lines = [line for line in lines if "|" in line]
    has_pipe_noise = len(pipe_lines) >= 3 and any(
        "•" in line or line.strip().endswith("|") or line.strip().startswith("|")
        for line in pipe_lines
    )

    if not has_pipe_noise:
        return cleaned

    rewritten_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if rewritten_lines and rewritten_lines[-1]:
                rewritten_lines.append("")
            continue

        line = re.sub(r"^>\s*", "", line)
        line = re.sub(r"\s+\|\s*$", "", line)

        normalized_sep = line.replace("—", "-").replace("–", "-").replace("─", "-")
        if TABLE_SEPARATOR_LINE_RE.match(normalized_sep):
            continue

        if "|" in line and re.search(r"\bservicio\b", line, flags=re.IGNORECASE):
            continue

        row_match = re.match(
            r"^\|?\s*\*{0,3}([^|*][^|]{1,80}?)\*{0,3}\s*\|\s*([^|]{1,220}?)(?:\s*\|.*)?$",
            line,
        )
        if row_match and re.search(r"[A-Za-zÁÉÍÓÚáéíóúÑñ]", row_match.group(1)):
            service = re.sub(r"^\*+|\*+$", "", row_match.group(1)).strip(" :")
            price = row_match.group(2).strip(" :")
            is_valid_service = bool(
                re.match(r"^[A-Za-zÁÉÍÓÚáéíóúÑñ][A-Za-zÁÉÍÓÚáéíóúÑñ0-9+ .,'/()\-]{1,80}$", service)
            )
            if is_valid_service and service.lower() not in {
                "servicio",
                "pros",
                "contras",
                "resumen",
                "modelo",
                "feature",
            }:
                if rewritten_lines and rewritten_lines[-1]:
                    rewritten_lines.append("")
                rewritten_lines.append(f"### {service}")
                if price:
                    rewritten_lines.append(f"- Precio: {price}")
                continue

        bullet_match = re.match(r"^\s*[•●▪◦\-*+]\s+(.+)$", line)
        if bullet_match:
            bullet_text = bullet_match.group(1).strip().rstrip("|").strip()
            bullet_segments = [segment.strip() for segment in bullet_text.split("|") if segment.strip()]
            for segment in bullet_segments:
                segment = re.sub(r"^\s*[•●▪◦\-*+]\s+", "", segment).strip()
                if segment:
                    rewritten_lines.append(f"- {segment}")
            continue

        if "|" in line:
            segments = [segment.strip() for segment in line.split("|") if segment.strip()]
            for segment in segments:
                segment_bullet = re.match(r"^\s*[•●▪◦\-*+]\s+(.+)$", segment)
                if segment_bullet:
                    segment_text = segment_bullet.group(1).strip()
                    if segment_text:
                        rewritten_lines.append(f"- {segment_text}")
                elif (
                    rewritten_lines
                    and rewritten_lines[-1].startswith("- Precio:")
                    and re.match(r"^[\d$€].{2,}$", segment)
                ):
                    rewritten_lines.append(f"- Precio: {segment}")
                elif segment.lower() in {"pros", "contras", "precio", "servicio"}:
                    continue
                else:
                    rewritten_lines.append(segment)
            continue

        if (
            rewritten_lines
            and rewritten_lines[-1].startswith("- Precio:")
            and re.match(r"^[\d$€].{2,}$", line)
        ):
            extra_price = line.strip().rstrip("|").strip()
            if extra_price:
                rewritten_lines.append(f"- Precio: {extra_price}")
            continue

        line = line.strip("| ").strip()
        line = re.sub(r"\s+\|\s*$", "", line)
        if line:
            rewritten_lines.append(line)

    rewritten = "\n".join(rewritten_lines)
    rewritten = re.sub(r"\n{3,}", "\n\n", rewritten).strip()

    if not rewritten:
        return cleaned

    # Evitar devolver un resumen excesivamente truncado por un parser agresivo.
    if len(rewritten) < max(40, int(len(cleaned) * 0.35)):
        return cleaned

    logger.info("Respuesta normalizada para mejorar formato visual.")
    return rewritten


def clear_user_history(user_id: str) -> bool:
    """Limpia el historial de un usuario en memoria de sesion."""
    had_history = bool(conversation_history.get(user_id))
    conversation_history[user_id] = []
    return had_history


async def _build_web_context(
    message: str,
    history: Optional[list[dict[str, str]]] = None,
    allow_web_search: bool = True,
) -> tuple[str, list[dict[str, str]], str]:
    """Detecta intencion de busqueda y construye contexto web."""
    if not web_search_engine:
        return "", [], ""
    if not allow_web_search:
        return "", [], ""

    query = extract_search_intent(message)
    if query:
        query = _normalize_web_query(query)
    message_lower = message.lower()

    # Fallback: si no detectamos regex exacta pero hay señales fuertes,
    # usamos el mensaje limpio como query.
    if not query and WEB_INTENT_HINT_RE.search(message):
        query = _normalize_web_query(message)

    if not query:
        return "", [], ""

    # Cuando el usuario dice algo tipo "actualiza los ultimos datos y rehace tabla",
    # reutilizamos el tema del turno previo para evitar queries basura.
    if (
        history
        and _is_low_signal_web_query(query)
        and WEB_FOLLOWUP_REFRESH_RE.search(message_lower)
    ):
        topic_hint = _infer_topic_hint_from_history(history, message)
        if topic_hint:
            query = f"{topic_hint} datos actuales"
            logger.info(f"Query web enriquecida por contexto: '{query}'")

    if re.search(r"\b(precio|cotizaci[oó]n|valor)\b", message_lower):
        if len(query.split()) <= 3 and "precio" not in query and "cotizacion" not in query:
            query = f"precio actual de {query}"

    use_news = any(word in message_lower for word in ("noticias", "news", "actualidad", "hoy"))
    logger.info(f"Intencion web detectada. Query='{query}', news={use_news}")

    if use_news:
        results = await web_search_engine.search_news(query, max_results=5)
    else:
        results = await web_search_engine.search(query, max_results=5)

    if not results:
        logger.warning(f"Busqueda web sin resultados para query='{query}'")
        return query, [], ""

    lines = ["Resultados recientes de busqueda web para apoyar la respuesta:"]
    for idx, result in enumerate(results, 1):
        title = _sanitize_web_text(result.get("title", ""), max_length=140)
        snippet = _sanitize_web_text(result.get("snippet", ""), max_length=220)
        url = result.get("url", "").strip()
        lines.append(f"{idx}. {title}")
        if snippet:
            lines.append(f"   Resumen: {snippet}")
        if url:
            lines.append(f"   Fuente: {url}")

    return query, results, "\n".join(lines)


async def _try_auto_reminder(message: str) -> Optional[str]:
    """
    Crea recordatorio automatico cuando el usuario lo pide explicitamente
    y hay fecha/hora detectable en el mensaje.
    """
    if not reminder_manager:
        return None

    if not REMINDER_REQUEST_RE.search(message):
        return None

    if REMINDER_QUESTION_RE.search(message):
        return None

    # Si detectamos pedido de recordatorio, intentamos parsear fecha aunque
    # el detector rapido de datetime no lo identifique.
    has_datetime_hint = contains_datetime_reference(message)
    try:
        reminder = await reminder_manager.create_from_natural_language(message)
    except ValueError as exc:
        reason = str(exc).strip().lower()
        if reason == "missing_datetime" and has_datetime_hint:
            logger.info("Se detecto intencion de recordatorio, pero no se pudo parsear fecha.")
        return None

    if not reminder:
        if has_datetime_hint:
            logger.info("Se detecto intencion de recordatorio, pero no se pudo parsear fecha.")
        return None

    logger.info(f"Recordatorio creado automaticamente: {reminder['id']}")
    reminder_date = reminder.get("datetime", "")
    if reminder_manager and hasattr(reminder_manager, "format_datetime_for_user"):
        reminder_date = reminder_manager.format_datetime_for_user(reminder_date)
    return (
        "He creado un recordatorio automatico para esto.\n"
        f"- ID: {reminder['id']}\n"
        f"- Texto: {reminder['text']}\n"
        f"- Fecha: {reminder_date}"
    )


def _extract_reminder_delete_query(message: str) -> str:
    """Extrae el texto objetivo a eliminar de una orden natural."""
    cleaned = message.strip()
    patterns = [
        r"(?:recordatorio|recordatorios)\s+(?:de|del|para|sobre|que\s+dice|que\s+diga)\s+(.+)$",
        (
            r"(?:elimina|eliminar|borra|borrar|quita|quitar|cancela|cancelar|"
            r"remueve|remover|completa|completar)\s+"
            r"(?:el|la|los|las|mi|mis|un|una)?\s*"
            r"(?:recordatorio|recordatorios)?\s*(.+)$"
        ),
    ]
    for pattern in patterns:
        match = re.search(pattern, cleaned, flags=re.IGNORECASE)
        if not match:
            continue
        query = match.group(1).strip(" \t\n\r?¡!.,;:")
        query = re.sub(r"^(de|del|para|sobre)\s+", "", query, flags=re.IGNORECASE)
        if query and query.lower() not in {"recordatorio", "recordatorios"}:
            return query
    return ""


async def _handle_reminder_action(message: str, user_id: str) -> Optional[str]:
    """
    Maneja acciones de recordatorios de forma deterministica en chat libre:
    crear, listar/consultar y eliminar/quitar.
    """
    if not reminder_manager:
        return None

    message_clean = message.strip()
    message_lower = message_clean.lower()

    if REMINDER_LIST_RE.search(message_clean):
        return reminder_manager.format_active_reminders_for_chat()

    if REMINDER_DELETE_RE.search(message_clean):
        if REMINDER_DELETE_ALL_RE.search(message_clean):
            deleted_count = reminder_manager.delete_all_active()
            if deleted_count == 0:
                return "No habia recordatorios activos para eliminar."
            return f"Listo, elimine {deleted_count} recordatorio(s) activos."

        id_match = REMINDER_ID_RE.search(message_lower)
        if id_match:
            reminder_id = id_match.group(1)
            deleted = reminder_manager.delete_reminder(reminder_id)
            if deleted:
                return f"Listo, elimine el recordatorio [{reminder_id}]."
            return f"No encontre un recordatorio activo con ID {reminder_id}."

        query = _extract_reminder_delete_query(message_clean)
        if not query:
            return (
                "Puedo eliminarlo, pero necesito el ID o parte del texto. "
                "Ejemplo: 'elimina el recordatorio para tomar cafe'."
            )

        deleted_reminder = reminder_manager.delete_reminder_by_text(query)
        if not deleted_reminder:
            return "No encontre un recordatorio activo que coincida con eso."

        formatted_dt = reminder_manager.format_datetime_for_user(deleted_reminder["datetime"])
        return (
            "Listo, elimine este recordatorio:\n"
            f"- [{deleted_reminder['id']}] {deleted_reminder['text']}\n"
            f"- Fecha: {formatted_dt}"
        )

    pending = _get_pending_reminder(user_id)
    is_creation_request = _looks_like_reminder_creation_request(message_clean)

    if pending and not is_creation_request:
        if REMINDER_PENDING_CANCEL_RE.search(message_clean):
            pending_reminder_by_user.pop(user_id, None)
            return "Listo, cancele ese recordatorio pendiente."

        if _looks_like_reminder_task_followup(message_clean):
            try:
                reminder = reminder_manager.create_reminder(
                    text=message_clean,
                    dt=pending["datetime"],
                )
            except ValueError as exc:
                reason = str(exc).strip().lower()
                if reason == "missing_task":
                    return (
                        "Aun me falta la accion concreta del recordatorio. "
                        "Ejemplo: 'para ir a tomar cafe'."
                    )
                pending_reminder_by_user.pop(user_id, None)
                return "No pude crear el recordatorio pendiente. Intentemos de nuevo."

            pending_reminder_by_user.pop(user_id, None)
            reminder_date = reminder_manager.format_datetime_for_user(reminder["datetime"])
            return (
                "Listo, cree este recordatorio:\n"
                f"- ID: {reminder['id']}\n"
                f"- Texto: {reminder['text']}\n"
                f"- Fecha: {reminder_date}"
            )

    # Si es pregunta/queja sobre recordatorios, dejar que el LLM responda.
    if REMINDER_QUESTION_RE.search(message_clean):
        return None

    if is_creation_request:
        try:
            reminder = await reminder_manager.create_from_natural_language(message_clean)
        except ValueError as exc:
            reason = str(exc).strip().lower()
            if reason == "missing_task":
                parsed_dt = None
                if hasattr(reminder_manager, "_extract_text_and_datetime"):
                    try:
                        _, parsed_dt = reminder_manager._extract_text_and_datetime(message_clean)
                    except Exception:
                        parsed_dt = None

                if parsed_dt:
                    pending_reminder_by_user[user_id] = {
                        "datetime": parsed_dt.isoformat(),
                        "created_at": datetime.now().isoformat(),
                    }
                return (
                    "Ya tengo la hora del recordatorio, pero me falta para que es. "
                    "Dime la accion. Ejemplo: 'para ir a tomar cafe'."
                )
            return (
                "Puedo crear el recordatorio, pero necesito fecha/hora clara. "
                "Ejemplo: 'recuerdame tomar cafe en 10 minutos'."
            )

        if not reminder:
            return (
                "Puedo crear el recordatorio, pero necesito fecha/hora clara. "
                "Ejemplo: 'recuerdame tomar cafe en 10 minutos'."
            )

        pending_reminder_by_user.pop(user_id, None)
        reminder_date = reminder_manager.format_datetime_for_user(reminder["datetime"])
        return (
            "Listo, cree este recordatorio:\n"
            f"- ID: {reminder['id']}\n"
            f"- Texto: {reminder['text']}\n"
            f"- Fecha: {reminder_date}"
        )

    return None


async def _handle_media_stack_action(message: str) -> Optional[str]:
    """
    Maneja acciones del protocolo de peliculas:
    - iniciar stack multimedia bajo demanda (headless)
    - consultar estado actual
    """
    if looks_like_media_stack_status_request(message):
        return build_media_stack_status_response()

    if looks_like_media_stack_stop_request(message):
        before = build_media_stack_status_response()
        success, status, detail = await stop_media_stack_headless(timeout_seconds=120)
        after = build_media_stack_status_response(status)

        if success:
            return (
                "Listo. Apague el protocolo peliculas y detuve los procesos asociados.\n"
                f"{after}"
            )
        if detail:
            return (
                "Intente apagar el protocolo peliculas, pero quedo parcial.\n"
                f"{after}\n"
                f"Detalle tecnico: {truncate_text(detail, max_length=240)}"
            )
        return (
            "Intente apagar el protocolo peliculas, pero quedo parcial.\n"
            f"Estado previo: {before}\n"
            f"Estado actual: {after}"
        )

    if not looks_like_media_stack_start_request(message):
        return None

    global media_stack_start_task

    status_now = get_media_stack_status()
    if all(status_now.values()):
        return (
            "El protocolo peliculas ya esta activo.\n"
            f"{build_media_stack_status_response(status_now)}"
        )

    if media_stack_start_task and not media_stack_start_task.done():
        return (
            "Ya estoy activando el protocolo peliculas en segundo plano.\n"
            f"{build_media_stack_status_response(status_now)}\n"
            "Consulta en unos segundos con: estado del protocolo peliculas."
        )

    media_stack_start_task = asyncio.create_task(
        start_media_stack_headless(timeout_seconds=180),
        name="media-stack-start",
    )

    def _on_media_stack_start_done(task: asyncio.Task) -> None:
        try:
            success, status, detail = task.result()
            if success:
                logger.info(f"Arranque de protocolo peliculas completado: {status}")
            else:
                logger.warning(
                    "Arranque de protocolo peliculas incompleto: "
                    f"status={status}, detail={truncate_text(detail, max_length=200)}"
                )
        except asyncio.CancelledError:
            logger.info("Tarea de arranque de protocolo peliculas cancelada.")
        except Exception as exc:
            logger.error(f"Fallo en tarea de arranque de protocolo peliculas: {exc}")

    media_stack_start_task.add_done_callback(_on_media_stack_start_done)

    return (
        "Listo, active el protocolo peliculas en segundo plano.\n"
        "Puede tardar unos segundos mientras suben los servicios.\n"
        "Verifica avance con: estado del protocolo peliculas."
    )


async def process_chat_message(message: str, user_id: str, source: str = "api") -> str:
    """
    Flujo principal de chat compartido por API y Telegram.
    """
    logger.info(f"[{source}] Mensaje de {user_id}: {message[:120]}...")

    history = conversation_history.setdefault(user_id, [])
    history.append({"role": "user", "content": message})
    if len(history) > MAX_CONTEXT_MESSAGES:
        del history[:-MAX_CONTEXT_MESSAGES]

    response_text: str
    deterministic_media_response = await _handle_media_stack_action(message)
    if deterministic_media_response is not None:
        response_text = deterministic_media_response
    else:
        deterministic_reminder_response = await _handle_reminder_action(message, user_id)
        if deterministic_reminder_response is not None:
            response_text = deterministic_reminder_response
        else:
            explicit_web_request = _is_explicit_web_request(message)
            memory_context = ""
            if memory_manager:
                memory_context = await memory_manager.search_relevant_context(query=message, n=5)
                memory_context = _sanitize_memory_context(memory_context)

            web_query, web_results, web_context = await _build_web_context(
                message,
                history=history,
                allow_web_search=explicit_web_request,
            )
            combined_context = "\n\n".join(
                part for part in (memory_context.strip(), web_context.strip()) if part
            )

            active_reminders = ""
            if reminder_manager:
                active_reminders = reminder_manager.get_active_reminders_text()

            system_prompt = build_system_prompt(
                memory_context=combined_context or None,
                active_reminders=active_reminders or None,
            )
            channel_addendum = _channel_prompt_addendum(source)
            if channel_addendum:
                system_prompt = f"{system_prompt}\n\n{channel_addendum}"

            model_history = _sanitize_history_for_generation(history)
            response_text = await llm_engine.generate_response(
                messages=model_history,
                system_prompt=system_prompt,
            )

            # Fallback cuando el modelo falle y si tenemos resultados web estructurados.
            if web_results and (
                not response_text.strip()
                or response_text.startswith("Error:")
                or response_text.strip().upper() == "NADA"
                or len(response_text.strip()) <= 8
                or "no pude generar una respuesta" in response_text.lower()
            ):
                response_text = _format_web_results_for_user(web_query, web_results, max_results=5)

            # Segundo fallback: si el LLM falla y no hubo contexto web inicial,
            # intentamos una busqueda directa con el mensaje completo.
            if (
                web_search_engine
                and explicit_web_request
                and not web_results
                and (
                    not response_text.strip()
                    or response_text.startswith("Error:")
                    or "no pude generar una respuesta" in response_text.lower()
                    or response_text.strip().upper() == "NADA"
                    or len(response_text.strip()) <= 8
                )
            ):
                rescue_query = _normalize_web_query(message)
                if rescue_query:
                    rescue_results = await web_search_engine.search(rescue_query, max_results=5)
                    if rescue_results:
                        response_text = _format_web_results_for_user(
                            rescue_query,
                            rescue_results,
                            max_results=5,
                        )

            if source == "telegram" and web_results:
                response_text = _append_telegram_sources_block(response_text, web_results)

            # Reintento defensivo: cuando el modelo responde rechazo generico
            # en una consulta claramente benigna (opiniones, entretenimiento, etc.).
            if _is_generic_refusal(response_text) and BENIGN_OPINION_HINT_RE.search(message):
                logger.warning("Rechazo generico detectado en consulta benigna. Reintentando una vez...")
                retry_prompt = (
                    f"{system_prompt}\n\n"
                    "La solicitud actual es benigna y permitida. "
                    "Responde de forma util y breve; no devuelvas rechazos genericos."
                )
                retry_response = await llm_engine.generate_response(
                    messages=_sanitize_history_for_generation(history),
                    system_prompt=retry_prompt,
                )
                if retry_response.strip() and not _is_generic_refusal(retry_response):
                    response_text = retry_response

            auto_reminder_note = await _try_auto_reminder(message)
            if auto_reminder_note:
                response_text = _remove_reminder_denials(response_text)
                response_text = f"{response_text}\n\n{auto_reminder_note}"

    response_text = _normalize_response_format(response_text)

    history.append({"role": "assistant", "content": response_text})
    if len(history) > MAX_CONTEXT_MESSAGES:
        del history[:-MAX_CONTEXT_MESSAGES]

    if memory_manager and not _is_generic_refusal(response_text):
        await memory_manager.extract_and_store_info(history, llm_engine)
        summary = truncate_text(
            f"Usuario: {message}\nAsistente: {response_text}",
            max_length=900,
        )
        await memory_manager.store_conversation_summary(
            summary,
            metadata={"source": source, "user_id": user_id},
        )
    elif memory_manager:
        logger.info("No se guardo resumen en memoria: respuesta de rechazo generico.")

    logger.info(f"[{source}] Respuesta para {user_id}: {response_text[:120]}...")
    return response_text


async def _telegram_supervisor_loop(retry_seconds: int = 20) -> None:
    """
    Supervisa el bot de Telegram y reintenta arranque si falla por red/DNS.
    Evita que el servicio quede sin Telegram despues de reinicio.
    """
    while True:
        await asyncio.sleep(retry_seconds)
        if not telegram_bot:
            continue
        try:
            if not telegram_bot.is_running():
                logger.warning("Bot de Telegram no activo. Reintentando inicio...")
                await telegram_bot.start_polling()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(f"Error en supervisor de Telegram: {exc}")


# --- Lifespan (startup/shutdown) ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Manejo de inicio y cierre del servidor."""
    global telegram_bot, telegram_task

    logger.info("Iniciando servidor del agente de IA...")
    ollama_ok = await llm_engine.check_health()
    if ollama_ok:
        logger.info("Ollama esta disponible y listo")
    else:
        logger.warning(
            "Ollama NO esta disponible. "
            "Asegurate de ejecutar: ollama serve"
        )

    if TelegramBot is not None:
        try:
            telegram_bot = TelegramBot(
                chat_handler=process_chat_message,
                memory_manager=memory_manager,
                reminder_manager=reminder_manager,
                web_search=web_search_engine,
                clear_history_handler=clear_user_history,
                media_handler=radarr_client,
            )
            if reminder_manager and telegram_bot:
                reminder_manager.telegram_send_fn = telegram_bot.send_message
            await telegram_bot.start_polling()
            telegram_task = asyncio.create_task(
                _telegram_supervisor_loop(),
                name="telegram-supervisor",
            )
        except Exception as exc:
            logger.error(f"No se pudo iniciar el bot de Telegram: {exc}")
            telegram_bot = None
            telegram_task = None

    if reminder_manager:
        reminder_manager.start_scheduler()

    try:
        yield
    finally:
        if reminder_manager:
            reminder_manager.stop_scheduler()

        if telegram_bot:
            await telegram_bot.stop()

        if telegram_task:
            telegram_task.cancel()
            with suppress(asyncio.CancelledError):
                await telegram_task

        if radarr_client:
            await radarr_client.close()

        await llm_engine.close()
        logger.info("Servidor detenido")


# --- Aplicacion FastAPI ---
app = FastAPI(
    title="Agente de IA Local",
    description="Agente de IA personal con Ollama",
    version="1.0.0",
    lifespan=lifespan,
)


# --- Endpoints ---
# --- Regex para detección de intención de película en web ---
_MOVIE_HINT_WEB_RE = re.compile(
    r"\b(quiero\s+ver|descargar|descarga|baja|bajame|ponme|pon\s+la\s+peli|"
    r"busca(?:me)?\s+la\s+peli|peli(?:cula)?|movie)\b",
    flags=re.IGNORECASE,
)


def _extract_movie_title_heuristic(message: str) -> Optional[str]:
    """Extrae título con reglas simples cuando el extractor LLM falla."""
    text = (message or "").strip()
    if not text:
        return None

    quoted = re.search(r"[\"“”'‘’]([^\"“”'‘’]{2,100})[\"“”'‘’]", text)
    if quoted:
        candidate = quoted.group(1).strip()
        if candidate:
            return candidate

    pattern = re.compile(
        r"(?:quiero\s+ver|ver|descarga(?:r)?|baja(?:me)?|"
        r"pon(?:me)?(?:\s+la\s+peli(?:cula)?)?|"
        r"busca(?:me)?(?:\s+la\s+peli(?:cula)?)?|movie)\s+(.+)$",
        flags=re.IGNORECASE,
    )
    match = pattern.search(text)
    if not match:
        return None

    candidate = match.group(1).strip()
    candidate = re.sub(
        r"^(la|el|una|un)\s+(pel[ií]cula|movie)\s+(de\s+)?",
        "",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"^de\s+", "", candidate, flags=re.IGNORECASE)
    candidate = re.sub(r"\b(por\s+favor|pls|please)\b", "", candidate, flags=re.IGNORECASE)
    candidate = candidate.strip(" \t\n\r.,!?¿¡:;\"'()[]{}")

    if not candidate or len(candidate) > 100:
        return None
    return candidate


async def _extract_movie_title(message: str) -> Optional[str]:
    """Usa el LLM para extraer título de película del mensaje del usuario."""
    if not _MOVIE_HINT_WEB_RE.search(message):
        return None

    heuristic_title = _extract_movie_title_heuristic(message)

    extraction_prompt = (
        "Eres un extractor de intenciones. El usuario quiere ver o descargar una pelicula.\n"
        "Extrae SOLO el titulo de la pelicula del mensaje.\n"
        "Si NO hay intencion de pelicula, responde exactamente: NONE\n"
        "Si hay titulo, responde SOLO con el titulo, sin comillas ni explicacion.\n\n"
        "Ejemplos:\n"
        "- 'Quiero ver Inception' -> Inception\n"
        "- 'Descarga The Matrix' -> The Matrix\n"
        "- 'Ponme la peli de Batman Begins' -> Batman Begins\n"
        "- 'Que hora es?' -> NONE\n"
        "- 'Bajame Interstellar' -> Interstellar\n"
    )
    try:
        result = await llm_engine.generate_response(
            messages=[{"role": "user", "content": message}],
            system_prompt=extraction_prompt,
        )
        cleaned = (result or "").strip().strip('"').strip("'").strip()
        if cleaned and cleaned.upper() != "NONE" and len(cleaned) <= 100:
            return cleaned
        return heuristic_title
    except Exception as exc:
        logger.error(f"Error extrayendo título de película: {exc}")
        return heuristic_title


@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest) -> ChatResponse:
    """Endpoint principal de chat."""

    # Detectar intención de película para fuentes web/desktop
    media_stack_command = (
        looks_like_media_stack_start_request(request.message)
        or looks_like_media_stack_stop_request(request.message)
        or looks_like_media_stack_status_request(request.message)
    )
    if (
        radarr_client
        and radarr_client.enabled
        and request.source in ("desktop", "api")
        and not media_stack_command
    ):
        movie_title = await _extract_movie_title(request.message)
        if movie_title:
            logger.info(f"[{request.source}] Intención de película detectada: '{movie_title}'")
            results = await radarr_client.search_movie(movie_title)
            if results:
                movie = results[0]
                return ChatResponse(
                    response=f"Encontré esta película: **{movie['title']}** ({movie['year']})",
                    movie=movie,
                )

    response_text = await process_chat_message(
        message=request.message,
        user_id=request.user_id,
        source=request.source,
    )
    return ChatResponse(response=response_text)


# --- Endpoints de películas (Fase 6.5 web) ---

class MovieReleasesRequest(BaseModel):
    tmdb_id: int
    title: str = ""
    year: int = 0


class MovieGrabRequest(BaseModel):
    guid: str
    indexer_id: int
    tmdb_id: Optional[int] = None


@app.post("/movie/add-and-releases")
async def movie_add_and_releases(request: MovieReleasesRequest) -> dict[str, Any]:
    """Añade película a Radarr y busca releases disponibles."""
    if not radarr_client or not radarr_client.enabled:
        raise HTTPException(status_code=503, detail="Radarr no configurado.")

    # 1) Añadir a Radarr sin búsqueda automática
    result = await radarr_client.add_movie(
        tmdb_id=request.tmdb_id,
        title=request.title,
        year=request.year,
        search_for_movie=False,
    )

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    # 2) Obtener ID interno de Radarr
    radarr_id = result.get("radarr_id")
    if not radarr_id:
        existing = await radarr_client.get_movie_by_tmdb(request.tmdb_id)
        if existing:
            radarr_id = existing.get("id")

    if not radarr_id:
        return {"releases": [], "message": "Película añadida pero no se encontró su ID en Radarr."}

    # 3) Buscar releases
    releases = await radarr_client.search_releases(radarr_id)
    grouped = radarr_client.get_grouped_releases(releases)

    return {
        "radarr_id": radarr_id,
        "releases": grouped,
        "total_found": len(releases),
        "approved": len([r for r in releases if not r.get("rejected")]),
    }


@app.post("/movie/grab")
async def movie_grab(request: MovieGrabRequest) -> dict[str, Any]:
    """Descarga un release específico y activa monitoreo."""
    if not radarr_client or not radarr_client.enabled:
        raise HTTPException(status_code=503, detail="Radarr no configurado.")

    result = await radarr_client.grab_release(
        guid=request.guid,
        indexer_id=request.indexer_id,
    )

    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])

    # Activar monitoreo ahora que el usuario eligió el release manualmente
    if request.tmdb_id:
        await radarr_client.set_monitored(request.tmdb_id, monitored=True)

    return result


@app.post("/movie/remove-duplicates")
async def movie_remove_duplicates() -> dict[str, Any]:
    """Busca y elimina películas duplicadas en Radarr."""
    if not radarr_client or not radarr_client.enabled:
        raise HTTPException(status_code=503, detail="Radarr no configurado.")

    result = await radarr_client.remove_duplicates()
    return result


@app.post("/remember", response_model=RememberResponse)
async def remember(request: RememberRequest) -> RememberResponse:
    """Guarda informacion manualmente en memoria de largo plazo."""
    if not memory_manager:
        raise HTTPException(status_code=503, detail="Sistema de memoria no disponible.")

    info = request.info.strip()
    if len(info) < 3:
        raise HTTPException(status_code=400, detail="La informacion es demasiado corta.")

    metadata = {"source": "manual_api", "user_id": request.user_id}
    if request.metadata:
        metadata.update(request.metadata)

    stored = await memory_manager.store_user_info(info=info, metadata=metadata)
    if stored:
        return RememberResponse(stored=True, message="Informacion guardada correctamente.")
    return RememberResponse(stored=False, message="La informacion ya existia en memoria.")


@app.get("/reminders", response_model=RemindersResponse)
async def list_reminders() -> RemindersResponse:
    """Lista todos los recordatorios."""
    if not reminder_manager:
        raise HTTPException(status_code=503, detail="Sistema de recordatorios no disponible.")
    return RemindersResponse(reminders=reminder_manager.get_all_reminders())


@app.post("/reminders")
async def create_reminder(request: ReminderCreateRequest) -> dict[str, Any]:
    """Crea un recordatorio desde texto natural o fecha explicita."""
    if not reminder_manager:
        raise HTTPException(status_code=503, detail="Sistema de recordatorios no disponible.")

    try:
        if request.datetime:
            reminder = reminder_manager.create_reminder(
                text=request.text,
                dt=request.datetime,
                recurring=request.recurring,
                interval=request.interval,
            )
        else:
            reminder = await reminder_manager.create_from_natural_language(request.text)
    except ValueError as exc:
        reason = str(exc).strip().lower()
        if reason == "missing_task":
            detail = (
                "Falta el objetivo del recordatorio. "
                "Ejemplo: 'recordatorio para las 10:11 para ir a comer'."
            )
        elif reason == "missing_datetime":
            detail = "No se pudo interpretar la fecha/hora del recordatorio."
        else:
            detail = str(exc)
        raise HTTPException(status_code=400, detail=detail) from exc

    if not reminder:
        raise HTTPException(
            status_code=400,
            detail="No se pudo interpretar la fecha del recordatorio.",
        )
    return reminder


@app.delete("/reminders/{reminder_id}", response_model=ReminderDeleteResponse)
async def delete_reminder(reminder_id: str) -> ReminderDeleteResponse:
    """Marca un recordatorio como completado."""
    if not reminder_manager:
        raise HTTPException(status_code=503, detail="Sistema de recordatorios no disponible.")

    deleted = reminder_manager.delete_reminder(reminder_id)
    if deleted:
        return ReminderDeleteResponse(deleted=True, message="Recordatorio eliminado.")
    return ReminderDeleteResponse(deleted=False, message="Recordatorio no encontrado.")


# --- Fase 7: Webhook de Radarr ---
@app.post("/webhook/radarr")
async def radarr_webhook(payload: dict[str, Any]) -> dict[str, str]:
    """
    Recibe notificaciones de Radarr cuando una pelicula se descarga/importa.
    Radarr envia webhooks con eventType: 'Download', 'MovieFileDelete', etc.
    Configurar en Radarr: Settings > Connect > Webhook > URL: http://<host>:8000/webhook/radarr
    """
    event_type = payload.get("eventType", "Unknown")
    logger.info(f"Webhook Radarr recibido: eventType={event_type}")

    # Solo procesamos eventos de descarga/importacion completada
    if event_type not in ("Download", "MovieAdded", "MovieFileDelete"):
        logger.debug(f"Webhook Radarr ignorado: eventType={event_type}")
        return {"status": "ignored", "eventType": event_type}

    # Extraer titulo de la pelicula
    movie_data = payload.get("movie", {})
    title = movie_data.get("title", "Pelicula desconocida")
    year = movie_data.get("year", "")

    if event_type == "Download":
        # Pelicula descargada e importada a la biblioteca
        is_upgrade = payload.get("isUpgrade", False)
        if is_upgrade:
            message = f"La pelicula {title} ({year}) se ha actualizado a mejor calidad en Jellyfin!"
        else:
            message = f"La pelicula {title} ({year}) ya esta descargada y lista para ver en Jellyfin!"

        # Enviar notificacion proactiva via Telegram
        if telegram_bot:
            sent = await telegram_bot.send_message(message)
            if sent:
                logger.info(f"Notificacion Telegram enviada: {title}")
            else:
                logger.warning(f"No se pudo enviar notificacion Telegram para: {title}")
        else:
            logger.warning("Bot de Telegram no disponible para notificacion de Radarr.")

        return {"status": "notified", "title": title}

    return {"status": "processed", "eventType": event_type}


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Verifica el estado del servidor y la conexion a Ollama y Radarr."""
    ollama_ok = await llm_engine.check_health()
    radarr_ok = None
    if radarr_client and radarr_client.enabled:
        radarr_ok = await radarr_client.check_health()
    return HealthResponse(status="ok", ollama=ollama_ok, radarr=radarr_ok)


# --- Servir frontend ---
@app.get("/")
async def serve_index():
    """Sirve la pagina principal del frontend."""
    index_path = FRONTEND_DIR / "index.html"
    if index_path.exists():
        return FileResponse(
            str(index_path),
            headers={
                "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    return {"message": "Frontend no disponible. Se habilitara en la Fase 3."}


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


# --- Punto de entrada ---
if __name__ == "__main__":
    logger.info(f"Iniciando servidor en http://{HOST}:{PORT}")
    uvicorn.run(
        "app.main:app" if RELOAD else app,
        host=HOST,
        port=PORT,
        reload=RELOAD,
        log_level="info",
    )
