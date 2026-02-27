"""Deterministic command grammar for destructive memory protocol."""

from __future__ import annotations

import re
import unicodedata

MEMORY_PURGE_ACTIVATION_PHRASE = "activa el protocolo de borrado total de memoria"
MEMORY_PURGE_CONFIRMATION_WORD = "confirmar"
MEMORY_PURGE_CANCEL_WORD = "cancelar"

RESTART_RUFUS_ACTIVATION_PHRASE = "activa el protocolo reinicio rufus"
RESTART_RUFUS_ACTIVATION_ALIASES = {
    RESTART_RUFUS_ACTIVATION_PHRASE,
    "activa el protocolo de reinicio rufus",
    "inicia el protocolo reinicio rufus",
    "inicia el protocolo de reinicio rufus",
    "ejecuta el protocolo reinicio rufus",
    "ejecuta el protocolo de reinicio rufus",
}
PROTOCOL_OVERVIEW_RE = re.compile(
    r"\b(que|cu[aá]les?|cuales?|tienes?|manejas?|disponibles?)\b.{0,40}\bprotocolos?\b|"
    r"\bprotocolos?\b.{0,40}\b(que|cu[aá]les?|cuales?|tienes?|manejas?|disponibles?)\b",
    flags=re.IGNORECASE,
)


def normalize_command_text(text: str) -> str:
    """Normalizes command text for exact-command comparisons."""
    normalized = str(text or "").strip().lower()
    normalized = "".join(
        ch for ch in unicodedata.normalize("NFKD", normalized) if not unicodedata.combining(ch)
    )
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


def is_restart_rufus_command(text: str) -> bool:
    """True for deterministic aliases of restart protocol activation."""
    return normalize_command_text(text) in RESTART_RUFUS_ACTIVATION_ALIASES


def is_protocols_overview_query(text: str) -> bool:
    """Detects protocol catalog questions without hijacking purge activation."""
    normalized = normalize_command_text(text)
    if not normalized:
        return False
    if is_memory_purge_activation_command(normalized):
        return False
    if is_restart_rufus_command(normalized):
        return False
    return bool(PROTOCOL_OVERVIEW_RE.search(normalized))
