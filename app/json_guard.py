"""Strict JSON generation, validation, and repair helpers."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)


@dataclass
class JsonGuardTrace:
    """Debug trace for JSON generation attempts."""

    outputs: list[str] = field(default_factory=list)
    last_error: str = ""


def _strip_fences(text: str) -> str:
    """Removes markdown fences around JSON."""
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def _extract_first_json_object(text: str) -> str:
    """Extracts first balanced JSON object from text."""
    cleaned = _strip_fences(text)
    if not cleaned:
        return ""

    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned

    start = cleaned.find("{")
    if start < 0:
        return ""

    depth = 0
    in_string = False
    escaped = False
    for idx, char in enumerate(cleaned[start:], start=start):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return cleaned[start : idx + 1]
    return ""


def _local_json_repair(raw: str) -> str:
    """Applies cheap local repairs before asking model to regenerate."""
    candidate = _extract_first_json_object(raw)
    if not candidate:
        candidate = _strip_fences(raw)

    # Remove trailing commas in objects/arrays.
    candidate = re.sub(r",\s*([}\]])", r"\1", candidate)
    return candidate.strip()


def validate_json_output(raw: str, schema_model: type[T]) -> T:
    """Parses raw output and validates against a Pydantic model."""
    candidate = _extract_first_json_object(raw)
    if not candidate:
        raise ValueError("No se encontro objeto JSON en la salida.")

    data = json.loads(candidate)
    return schema_model.model_validate(data)


async def generate_validated_json(
    llm_engine: Any,
    system_prompt: str,
    user_prompt: str,
    schema_model: type[T],
    max_retries: int = 2,
) -> tuple[T | None, JsonGuardTrace]:
    """
    Generation -> Schema validation -> Repair loop (max_retries).

    Returns parsed model or None plus trace for observability.
    """
    trace = JsonGuardTrace()

    base_system_prompt = (
        f"{system_prompt.strip()}\n\n"
        "Debes responder SOLO con un objeto JSON valido. "
        "No uses markdown, no agregues texto adicional."
    )

    messages = [{"role": "user", "content": user_prompt}]
    attempts = max(0, max_retries) + 1

    for attempt in range(attempts):
        raw = await llm_engine.generate_response(
            messages=messages,
            system_prompt=base_system_prompt,
        )
        trace.outputs.append(raw)

        for candidate in (raw, _local_json_repair(raw)):
            try:
                parsed = validate_json_output(candidate, schema_model)
                return parsed, trace
            except (ValueError, json.JSONDecodeError, ValidationError) as exc:
                trace.last_error = str(exc)

        if attempt >= attempts - 1:
            break

        repair_request = (
            "Tu salida no cumple el schema JSON requerido. "
            f"Error de validacion: {trace.last_error}\n"
            "Devuelve solo JSON valido, sin markdown y sin texto extra."
        )
        messages = [
            {"role": "user", "content": user_prompt},
            {"role": "assistant", "content": raw},
            {"role": "user", "content": repair_request},
        ]

    return None, trace
