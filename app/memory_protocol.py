"""Deterministic command grammar for destructive memory protocol."""

from __future__ import annotations

import re

MEMORY_PURGE_ACTIVATION_PHRASE = "activa el protocolo de borrado total de memoria"
MEMORY_PURGE_CONFIRMATION_WORD = "confirmar"
MEMORY_PURGE_CANCEL_WORD = "cancelar"
PROTOCOL_OVERVIEW_RE = re.compile(
    r"\b(que|cu[aá]les?|cuales?|tienes?|manejas?|disponibles?)\b.{0,40}\bprotocolos?\b|"
    r"\bprotocolos?\b.{0,40}\b(que|cu[aá]les?|cuales?|tienes?|manejas?|disponibles?)\b",
    flags=re.IGNORECASE,
)


def normalize_command_text(text: str) -> str:
    """Normalizes command text for exact-command comparisons."""
    normalized = str(text or "").strip().lower()
    normalized = re.sub(r"[¿?¡!.,;:]+", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def is_memory_purge_activation_command(text: str) -> bool:
    """True only for the single allowed activation phrase."""
    return normalize_command_text(text) == MEMORY_PURGE_ACTIVATION_PHRASE


def is_memory_purge_confirmation_word(text: str) -> bool:
    """True only for the single allowed confirmation word."""
    return normalize_command_text(text) == MEMORY_PURGE_CONFIRMATION_WORD


def is_memory_purge_cancel_word(text: str) -> bool:
    """True only for deterministic cancellation command."""
    return normalize_command_text(text) == MEMORY_PURGE_CANCEL_WORD


def is_protocols_overview_query(text: str) -> bool:
    """Detects protocol catalog questions without hijacking purge activation."""
    normalized = normalize_command_text(text)
    if not normalized:
        return False
    if is_memory_purge_activation_command(normalized):
        return False
    return bool(PROTOCOL_OVERVIEW_RE.search(normalized))
