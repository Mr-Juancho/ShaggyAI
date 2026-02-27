"""Semantic planner for reminder operations."""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.json_guard import generate_validated_json
from app.time_policy import has_temporal_reference


class ReminderActionPlan(BaseModel):
    """Structured reminder operation plan inferred from intent."""

    operation: Literal["none", "create", "list", "delete", "update", "postpone"] = "none"
    should_apply: bool = False
    target_id: str = ""
    target_query: str = ""
    task_text: str = ""
    datetime_text: str = ""
    delete_all: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_question: str = ""


class ReminderDraft(BaseModel):
    """Single reminder draft extracted from a potentially multi-reminder message."""

    task_text: str = ""
    datetime_text: str = ""


class MultiReminderPlan(BaseModel):
    """Structured plan for creating multiple reminders in one user message."""

    should_apply: bool = False
    reminders: list[ReminderDraft] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    clarification_question: str = ""


def _clean(value: str) -> str:
    return " ".join(str(value or "").strip().split())


async def extract_reminder_action_plan(
    llm_engine: Any,
    message: str,
    history: list[dict[str, str]],
) -> ReminderActionPlan | None:
    """
    Extracts semantic action plan for reminders:
    - create, list, delete, update, postpone
    """
    short_history = history[-5:] if history else []
    history_text = "\n".join(
        f"- {item.get('role', 'unknown')}: {item.get('content', '')[:180]}"
        for item in short_history
    )

    system_prompt = (
        "Eres un analizador semantico de recordatorios para un asistente personal. "
        "Debes inferir intenciones aunque el usuario use frases variadas."
    )
    user_prompt = (
        "Analiza si el usuario quiere gestionar recordatorios.\n"
        "Operaciones permitidas:\n"
        "- create: crear recordatorio nuevo\n"
        "- list: listar recordatorios activos\n"
        "- delete: eliminar uno o varios recordatorios\n"
        "- update: editar texto y/o fecha de un recordatorio existente\n"
        "- postpone: posponer/mover fecha de un recordatorio existente\n"
        "- none: no es gestion de recordatorios\n"
        "Reglas importantes:\n"
        "- No confundas notas de memoria con recordatorios. "
        "Si no hay intencion de aviso futuro, usa operation='none'.\n"
        "- Para create, incluye task_text y datetime_text cuando sea posible.\n"
        "- Para update/postpone/delete, intenta extraer target_id o target_query.\n"
        "- Si pide borrar todos, marca delete_all=true.\n"
        "- Si falta información crítica, usa should_apply=false y clarification_question.\n"
        f"Mensaje actual: {message}\n"
        f"Historial corto:\n{history_text or '- (sin historial)'}\n"
        "Devuelve JSON con schema:\n"
        "{\n"
        '  "operation": "none|create|list|delete|update|postpone",\n'
        '  "should_apply": true,\n'
        '  "target_id": "",\n'
        '  "target_query": "",\n'
        '  "task_text": "",\n'
        '  "datetime_text": "",\n'
        '  "delete_all": false,\n'
        '  "confidence": 0.0,\n'
        '  "clarification_question": ""\n'
        "}"
    )

    parsed, _ = await generate_validated_json(
        llm_engine=llm_engine,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_model=ReminderActionPlan,
        max_retries=2,
    )
    if not parsed:
        return None

    parsed.target_id = _clean(parsed.target_id).lower()
    parsed.target_query = _clean(parsed.target_query)
    parsed.task_text = _clean(parsed.task_text)
    parsed.datetime_text = _clean(parsed.datetime_text)

    if parsed.operation == "none":
        parsed.should_apply = False
        return parsed

    if parsed.operation == "list":
        parsed.should_apply = True
        return parsed

    if parsed.operation == "create":
        # Guardrail: sin señal temporal, probablemente es una nota para memoria y no alarma.
        if not parsed.datetime_text and not has_temporal_reference(message):
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "¿Quieres guardarlo en memoria (sin alarma) o crear un recordatorio con fecha/hora?"
                )
            return parsed
        if not parsed.task_text and not parsed.datetime_text:
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "¿Qué recordatorio quieres crear y para cuándo?"
                )
        return parsed

    if parsed.operation == "delete":
        if not parsed.delete_all and not parsed.target_id and not parsed.target_query:
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "¿Qué recordatorio quieres eliminar? Dime ID o parte del texto."
                )
        return parsed

    if parsed.operation == "update":
        if not parsed.target_id and not parsed.target_query:
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "¿Qué recordatorio quieres editar? Dime ID o una parte del texto."
                )
        elif not parsed.task_text and not parsed.datetime_text:
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "¿Qué cambio hago en ese recordatorio: texto, fecha, o ambos?"
                )
        return parsed

    if parsed.operation == "postpone":
        if not parsed.target_id and not parsed.target_query:
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "¿Qué recordatorio quieres posponer? Dime ID o una parte del texto."
                )
        elif not parsed.datetime_text:
            parsed.should_apply = False
            if not parsed.clarification_question:
                parsed.clarification_question = (
                    "¿Cuánto quieres posponerlo? Ejemplo: '30 minutos' o 'mañana a las 8'."
                )
        return parsed

    return parsed


def _looks_like_multi_reminder_request(message: str) -> bool:
    """Heuristic for user messages that likely ask for more than one reminder."""
    text = str(message or "")
    lowered = text.lower()
    datetime_hits = re.findall(
        r"\b("
        r"mañana|manana|pasado\s+mañana|hoy|esta\s+noche|"
        r"lunes|martes|mi[eé]rcoles|jueves|viernes|s[áa]bado|domingo|"
        r"\d{1,2}:\d{2}|\d{1,2}\s*(?:am|pm)|"
        r"a\s*las\s*\d{1,2}|en\s*\d+\s*(?:minutos?|horas?|d[ií]as?)|"
        r"dentro\s*de\s*\d+\s*(?:minutos?|horas?|d[ií]as?)"
        r")\b",
        lowered,
        flags=re.IGNORECASE,
    )
    if len(datetime_hits) >= 2 and re.search(r"\by\b", lowered):
        return True
    if re.search(r"\by\s+para\b", lowered) and len(re.findall(r"\bpara\b", lowered)) >= 2:
        return True
    if re.search(r"\b(y\s+otro|y\s+otra|adem[aá]s|tambi[eé]n|dos|2)\b", lowered):
        return True
    if re.search(r"\n\s*\d+[.)]\s*", text):
        return True
    return False


async def extract_multi_reminder_plan(
    llm_engine: Any,
    message: str,
    history: list[dict[str, str]],
) -> MultiReminderPlan | None:
    """
    Extracts multiple reminder drafts from one message.
    Returns should_apply=True only when at least 2 valid reminders are extracted.
    """
    short_history = history[-4:] if history else []
    history_text = "\n".join(
        f"- {item.get('role', 'unknown')}: {item.get('content', '')[:180]}"
        for item in short_history
    )
    system_prompt = (
        "Eres un extractor semantico de recordatorios. "
        "Tu tarea es separar un mensaje en uno o mas recordatorios independientes."
    )
    user_prompt = (
        "Si el mensaje contiene varios recordatorios, extraelos todos.\n"
        "Cada recordatorio debe tener:\n"
        "- task_text: accion concreta\n"
        "- datetime_text: fecha/hora asociada\n"
        "Reglas:\n"
        "- Si solo hay un recordatorio, devuelve una lista de un elemento.\n"
        "- Si falta fecha/hora en algun item, no inventes datos.\n"
        f"Mensaje actual: {message}\n"
        f"Historial corto:\n{history_text or '- (sin historial)'}\n"
        "Devuelve JSON con schema:\n"
        "{\n"
        '  "should_apply": true,\n'
        '  "reminders": [\n'
        '    {"task_text": "", "datetime_text": ""}\n'
        "  ],\n"
        '  "confidence": 0.0,\n'
        '  "clarification_question": ""\n'
        "}"
    )
    parsed, _ = await generate_validated_json(
        llm_engine=llm_engine,
        system_prompt=system_prompt,
        user_prompt=user_prompt,
        schema_model=MultiReminderPlan,
        max_retries=2,
    )
    if not parsed:
        return None

    normalized: list[ReminderDraft] = []
    seen_keys: set[tuple[str, str]] = set()
    for item in parsed.reminders:
        task = _clean(item.task_text)
        dt_text = _clean(item.datetime_text)
        if not task or not dt_text:
            continue
        dedup_key = (task.lower(), dt_text.lower())
        if dedup_key in seen_keys:
            continue
        seen_keys.add(dedup_key)
        normalized.append(ReminderDraft(task_text=task, datetime_text=dt_text))

    parsed.reminders = normalized
    parsed.should_apply = len(parsed.reminders) >= 2

    if not parsed.should_apply and _looks_like_multi_reminder_request(message):
        if not parsed.clarification_question:
            parsed.clarification_question = (
                "Puedo crear varios recordatorios en un solo mensaje, "
                "pero necesito que cada uno tenga accion y fecha/hora."
            )

    return parsed
