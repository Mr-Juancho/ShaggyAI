"""Temporal detection and datetime context injection."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

TEMPORAL_REFERENCE_RE = re.compile(
    r"\b("
    r"hoy|manana|mañana|pasado\s+manana|pasado\s+mañana|ayer|"
    r"actual|actualmente|ahora|esta\s+semana|este\s+mes|este\s+ano|este\s+año|"
    r"lunes|martes|miercoles|miércoles|jueves|viernes|sabado|sábado|domingo|"
    r"\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm)"
    r")\b",
    flags=re.IGNORECASE,
)


def has_temporal_reference(text: str) -> bool:
    """Returns True when user text includes temporal references."""
    return bool(TEMPORAL_REFERENCE_RE.search(text or ""))


def current_datetime_payload(now: datetime | None = None) -> dict[str, str]:
    """Creates normalized datetime payload for prompts and verification."""
    current = now or datetime.now()
    return {
        "iso_datetime": current.isoformat(timespec="seconds"),
        "date": current.strftime("%Y-%m-%d"),
        "time": current.strftime("%H:%M:%S"),
        "weekday": current.strftime("%A"),
        "human_readable": current.strftime("%Y-%m-%d %H:%M:%S"),
    }


def build_datetime_context(now: datetime | None = None) -> str:
    """Builds temporal context block to inject in system prompt."""
    payload = current_datetime_payload(now)
    return (
        "Contexto temporal obligatorio:\n"
        f"- Fecha y hora actual (ISO): {payload['iso_datetime']}\n"
        f"- Fecha actual: {payload['date']}\n"
        f"- Dia de la semana: {payload['weekday']}\n"
        "Cuando el usuario use referencias relativas (hoy, manana, actual), "
        "responde usando esta fecha como ancla."
    )


def as_capability_output(now: datetime | None = None) -> dict[str, Any]:
    """Structured output for get_current_datetime capability."""
    payload = current_datetime_payload(now)
    return {
        "iso_datetime": payload["iso_datetime"],
        "human_readable": payload["human_readable"],
    }
