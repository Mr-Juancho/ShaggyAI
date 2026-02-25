"""
Utilidades generales del agente de IA.
Funciones auxiliares usadas en varios modulos.
"""

import re
from datetime import datetime
from typing import Optional

from app.config import logger


def truncate_text(text: str, max_length: int = 500) -> str:
    """Trunca texto al largo maximo, cortando en palabra completa."""
    if len(text) <= max_length:
        return text
    truncated = text[:max_length]
    last_space = truncated.rfind(" ")
    if last_space > max_length * 0.7:
        truncated = truncated[:last_space]
    return truncated + "..."


def clean_text(text: str) -> str:
    """Limpia texto removiendo espacios extra y caracteres no deseados."""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def format_timestamp(timestamp: Optional[float] = None) -> str:
    """Formatea un timestamp como string legible."""
    if timestamp is None:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def contains_datetime_reference(text: str) -> bool:
    """
    Detecta si un texto contiene referencias a fecha/hora.
    Util para que el agente sugiera crear recordatorios.
    """
    patterns = [
        r'\b(manana|mañana|pasado\s*mañana|hoy|esta\s*noche)\b',
        r'\b(lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo)\b',
        r'\b(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\b',
        r'\b\d{1,2}[:/]\d{2}\b',  # Horas como 9:00, 14:30
        r'\b\d{1,2}\s*(am|pm|AM|PM)\b',  # 9am, 3PM
        r'\b(a\s*las\s*\d|en\s*\d+\s*(minutos?|horas?|dias?)|dentro\s*de\s*\d+\s*(minutos?|horas?|dias?))\b',
        r'\b(recordar|recordatorio|recuerdame|recuérdame|avísame|avisame|no\s*olvidar)\b',
    ]

    text_lower = text.lower()
    for pattern in patterns:
        if re.search(pattern, text_lower):
            return True
    return False


def extract_search_intent(text: str) -> Optional[str]:
    """
    Detecta si un mensaje del usuario requiere busqueda web.
    Retorna la consulta de busqueda o None.
    """
    search_patterns = [
        (
            r'(?:puedes\s+|podrias\s+|podrías\s+|me\s+)?'
            r'(?:buscar|busca|buscame|búscame|investiga|consulta|averigua)'
            r'(?:\s+en\s+(?:la\s+)?(?:web|internet|google))?'
            r'(?:\s+sobre)?\s+(.+)'
        ),
        r'(?:que\s*noticias|noticias\s*(?:sobre|de)|ultimas\s*noticias)\s+(.+)',
        r'(?:que\s*(?:es|son|significa)|quien\s*es|donde\s*(?:queda|esta))\s+(.+)',
        r'(?:cuanto\s*(?:cuesta|vale))\s+(.+)',
        r'(?:precio|cotizaci[oó]n|cotizacion|valor)(?:\s+actual)?(?:\s+(?:de|del))?\s+(.+)',
        r'(.+?)\s+(?:precio|cotizaci[oó]n|cotizacion|valor)(?:\s+actual)?\b',
    ]

    text_lower = text.lower().strip(" \t\n\r?¡!.,;:")
    invalid_queries = {"actual", "hoy", "ahora", "de", "del"}
    for pattern in search_patterns:
        match = re.search(pattern, text_lower)
        if match:
            query = match.group(1).strip(" \t\n\r?¡!.,;:")
            if query and query not in invalid_queries:
                return query
    return None
