"""
Genera el system prompt dinamicamente.
Lee personality.yaml e inyecta contexto de memoria y recordatorios.
"""

import os
from typing import Optional

import yaml

from app.config import PERSONALITY_FILE, logger

# --- Cache de personalidad ---
_personality_cache: Optional[dict] = None
_personality_mtime: float = 0.0

_DEFAULT_PERSONALITY: dict = {
    "name": "Agente",
    "tone": "amigable",
    "language": "espanol",
    "custom_instructions": "Eres un asistente util.",
}


def load_personality() -> dict:
    """Carga la personalidad desde el archivo YAML (con cache automatico)."""
    global _personality_cache, _personality_mtime

    try:
        current_mtime = os.path.getmtime(PERSONALITY_FILE)
    except OSError:
        logger.warning(f"Archivo de personalidad no encontrado: {PERSONALITY_FILE}")
        return dict(_DEFAULT_PERSONALITY)

    if _personality_cache is not None and current_mtime == _personality_mtime:
        return _personality_cache

    try:
        with open(PERSONALITY_FILE, "r", encoding="utf-8") as f:
            personality = yaml.safe_load(f)
            if not personality:
                personality = dict(_DEFAULT_PERSONALITY)
            _personality_cache = personality
            _personality_mtime = current_mtime
            logger.info("Personalidad cargada correctamente desde personality.yaml")
            return personality
    except yaml.YAMLError as e:
        logger.error(f"Error al parsear personality.yaml: {e}")
        return dict(_DEFAULT_PERSONALITY)


def build_system_prompt(
    memory_context: Optional[str] = None,
    active_reminders: Optional[str] = None
) -> str:
    """
    Construye el system prompt completo combinando:
    - Personalidad del archivo YAML
    - Contexto de memoria (si existe)
    - Recordatorios activos (si existen)
    """
    personality = load_personality()

    name = personality.get("name", "Agente")
    tone = personality.get("tone", "amigable")
    language = personality.get("language", "espanol")
    custom_instructions = personality.get("custom_instructions", "")

    # Prompt base con personalidad
    prompt_parts = [
        f"Tu nombre es {name}.",
        f"Tu tono de comunicacion es {tone}.",
        f"Responde siempre en {language}.",
        "",
        "--- Instrucciones personalizadas ---",
        custom_instructions.strip(),
        "",
        "--- Reglas operativas ---",
        (
            "Si el usuario pide noticias, informacion reciente o datos que cambian "
            "en el tiempo, apoyate en el contexto de busqueda web cuando este disponible."
        ),
        (
            "Si detectas fechas u horas en la conversacion, sugiere crear un recordatorio "
            "cuando ayude al usuario."
        ),
        (
            "Cuando haya una accion automatica de recordatorio disponible, actua como asistente "
            "capaz de programarlo y no digas que no puedes hacerlo."
        ),
        (
            "Formato de respuesta: usa markdown simple y limpio. "
            "No uses HTML (<table>, <br>, etc.)."
        ),
        (
            "Si piden comparativas: por defecto usa secciones y bullets cortos "
            "(Precio, Pros, Contras). Si el usuario pide tabla, usa solo una tabla markdown "
            "simple y compacta (sin viñetas dentro de celdas)."
        ),
        "No inventes datos: si falta contexto, dilo con claridad y pide precision.",
        "",
        "--- Capacidades multimedia (Fase 6/7) ---",
        (
            "Puedes buscar y descargar peliculas via Radarr. Si el usuario pide ver o "
            "descargar una pelicula, el sistema se encarga automaticamente de buscarla, "
            "mostrar el poster con botones de confirmacion, y gestionarla con Radarr/Prowlarr/Transmission."
        ),
        (
            "No intentes comunicarte con APIs externas directamente. Solo extrae la intencion "
            "y el titulo de la pelicula; el backend orquesta la comunicacion."
        ),
    ]

    # Inyectar contexto de memoria (Fase 2 lo llenara)
    if memory_context:
        prompt_parts.extend([
            "",
            "--- Contexto de memoria (informacion que recuerdas del usuario) ---",
            memory_context,
        ])

    # Inyectar recordatorios activos (Fase 5 lo llenara)
    if active_reminders:
        prompt_parts.extend([
            "",
            "--- Recordatorios activos del usuario ---",
            active_reminders,
        ])

    system_prompt = "\n".join(prompt_parts)
    logger.debug(f"System prompt generado ({len(system_prompt)} caracteres)")
    return system_prompt
