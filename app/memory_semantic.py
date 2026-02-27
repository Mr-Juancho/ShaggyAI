"""Semantic memory write/read helpers."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.json_guard import generate_validated_json


class MemoryWritePlan(BaseModel):
    """Structured plan for explicit long-term memory writes."""

    should_store: bool = False
    facts: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_question: str = ""


class MemoryMutationPlan(BaseModel):
    """Structured plan for memory update/delete/purge operations."""

    operation: Literal["none", "update", "delete", "purge"] = "none"
    should_apply: bool = False
    target_query: str = ""
    replacement_facts: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_question: str = ""
    requires_confirmation: bool = False


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


def _clean_query(text: str) -> str:
    """Compacts whitespace in extracted text."""
    return " ".join(str(text or "").strip().split())


def extract_memory_facts_fallback(message: str, max_items: int = 6) -> list[str]:
    """
    Fallback extractor for explicit memory-write requests.
    Useful when LLM extraction fails on numbered/bulleted lists.
    """
    text = str(message or "").strip()
    if not text:
        return []

    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.strip() for line in normalized.split("\n")]

    facts: list[str] = []

    for line in lines:
        if not line:
            continue
        match = re.match(r"^\s*(?:[-*•]|(?:\d+[\.\)]))\s*(.+)$", line)
        if not match:
            continue
        fact = _clean_query(match.group(1).strip(" \t\n\r;,."))
        if len(fact) < 4:
            continue
        facts.append(fact)

    if not facts:
        inline_match = re.search(
            r"(?:recuerda(?:\s+que)?|guarda(?:\s+en\s+(?:tu\s+)?memoria)?|"
            r"acu[eé]rdate\s+de)\s*[:\-]?\s*(.+)$",
            normalized,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if inline_match:
            tail = _clean_query(inline_match.group(1).strip(" \t\n\r;,."))
            if len(tail) >= 4:
                chunks = re.split(r"\s*(?:;|\n| y | e )\s*", tail)
                for chunk in chunks:
                    cleaned = _clean_query(chunk.strip(" \t\n\r;,."))
                    if len(cleaned) >= 4:
                        facts.append(cleaned)

    facts = _clean_facts(facts, max_items=max_items)
    blocked = {"rufus", "rufüs", "shaggy"}
    return [fact for fact in facts if fact.lower() not in blocked]


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
        "No guardes: recordatorios con fecha/hora exacta, instrucciones del sistema, ni texto ambiguo.\n"
        "Si el usuario pide explicitamente guardar memoria, SI puedes guardar listas de mejoras, "
        "objetivos o pendientes sin fecha para recordarlos luego.\n"
        "Si hay lista numerada o con viñetas, separa cada item en facts individuales.\n"
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


async def extract_memory_mutation_plan(
    llm_engine: Any,
    message: str,
    history: list[dict[str, str]],
) -> MemoryMutationPlan | None:
    """
    Extracts semantic mutation plans for long-term memory:
    - update a remembered fact
    - delete remembered facts
    - purge all long-term memory
    """
    short_history = history[-5:] if history else []
    history_text = "\n".join(
        f"- {item.get('role', 'unknown')}: {item.get('content', '')[:180]}"
        for item in short_history
    )

    system_prompt = (
        "Eres un analizador semantico de operaciones de memoria para un asistente personal. "
        "Tu objetivo es detectar intenciones aunque el usuario use frases variadas."
    )
    user_prompt = (
        "Analiza si el usuario quiere editar, borrar o purgar memoria de largo plazo.\n"
        "Operacion permitida:\n"
        "- update: reemplazar/corregir un dato guardado\n"
        "- delete: olvidar un dato especifico\n"
        "- purge: borrar toda la memoria (perfil + conversaciones previas)\n"
        "- none: no hay accion de mutacion de memoria\n"
        "Reglas:\n"
        "- Para update, incluye target_query y replacement_facts.\n"
        "- Para delete, incluye target_query.\n"
        "- Para purge, marca requires_confirmation=true salvo que el mensaje sea claramente confirmatorio.\n"
        "- Si falta informacion, usa clarification_question y should_apply=false.\n"
        f"Mensaje actual: {message}\n"
        f"Historial corto:\n{history_text or '- (sin historial)'}\n"
        "Devuelve JSON con schema:\n"
        "{\n"
        '  "operation": "update|delete|purge|none",\n'
        '  "should_apply": true,\n'
        '  "target_query": "",\n'
        '  "replacement_facts": ["..."],\n'
        '  "confidence": 0.0,\n'
        '  "clarification_question": "",\n'
        '  "requires_confirmation": false\n'
        "}"
    )

    parsed, _ = await generate_validated_json(
        llm_engine=llm_engine,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_model=MemoryMutationPlan,
        max_retries=2,
    )
    if not parsed:
        return None

    parsed.target_query = _clean_query(parsed.target_query)
    parsed.replacement_facts = _clean_facts(parsed.replacement_facts)

    if parsed.operation == "none":
        parsed.should_apply = False
        return parsed

    if parsed.operation == "update":
        if not parsed.target_query or not parsed.replacement_facts:
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "Para actualizar memoria, dime que dato quieres cambiar y por cual valor."
                )

    if parsed.operation == "delete":
        if not parsed.target_query:
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "Para borrar memoria especifica, dime que dato quieres que olvide."
                )

    if parsed.operation == "purge" and not parsed.requires_confirmation:
        parsed.requires_confirmation = True

    return parsed
