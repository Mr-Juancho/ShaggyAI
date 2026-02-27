"""Semantic memory write/read helpers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.json_guard import generate_validated_json


class MemoryWritePlan(BaseModel):
    """Structured plan for explicit long-term memory writes."""

    should_store: bool = False
    facts: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_question: str = ""


def _clean_facts(facts: list[str], max_items: int = 6) -> list[str]:
    """Normalizes and deduplicates extracted facts."""
    cleaned: list[str] = []
    seen: set[str] = set()

    for raw in facts:
        fact = " ".join(str(raw).strip().split())
        if len(fact) < 3:
            continue
        key = fact.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(fact)
        if len(cleaned) >= max_items:
            break
    return cleaned


async def extract_memory_write_plan(
    llm_engine: Any,
    message: str,
    history: list[dict[str, str]],
) -> MemoryWritePlan | None:
    """Extracts explicit user facts to persist in long-term memory."""
    short_history = history[-4:] if history else []
    history_text = "\n".join(
        f"- {item.get('role', 'unknown')}: {item.get('content', '')[:180]}"
        for item in short_history
    )

    system_prompt = (
        "Eres un analizador semantico para memoria de largo plazo. "
        "Detecta cuando el usuario pide guardar informacion personal."
    )
    user_prompt = (
        "Analiza el mensaje y determina si el usuario quiere guardar memoria de largo plazo.\n"
        "Guarda solo datos personales estables o relevantes (relaciones, preferencias, nombres, contexto personal).\n"
        "No guardes: tareas temporales, recordatorios, instrucciones del sistema, ni texto ambiguo.\n"
        f"Mensaje actual: {message}\n"
        f"Historial corto:\n{history_text or '- (sin historial)'}\n"
        "Devuelve JSON con schema:\n"
        "{\n"
        '  "should_store": true,\n'
        '  "facts": ["..."],\n'
        '  "confidence": 0.0,\n'
        '  "clarification_question": ""\n'
        "}"
    )

    parsed, _ = await generate_validated_json(
        llm_engine=llm_engine,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_model=MemoryWritePlan,
        max_retries=2,
    )
    if not parsed:
        return None

    parsed.facts = _clean_facts(parsed.facts)
    if parsed.should_store and not parsed.facts:
        parsed.should_store = False
    return parsed
