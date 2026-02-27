"""Final response verifier for temporal coherence and source safety."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

RELATIVE_TEMPORAL_RE = re.compile(r"\b(hoy|manana|mañana|pasado\s+manana|actualmente|ahora)\b", re.IGNORECASE)
ABSOLUTE_DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
SOURCE_LINE_RE = re.compile(r"\bfuentes?\b|\bfuente:\b", flags=re.IGNORECASE)
GENERIC_FAILURE_RE = re.compile(r"\b(no\s+se|no\s+tengo\s+datos|no\s+puedo\s+ayudar)\b", flags=re.IGNORECASE)


@dataclass
class VerificationResult:
    """Verification result with optional rewritten response."""

    passed: bool
    issues: list[str] = field(default_factory=list)
    response: str = ""


def _domains_from_results(results: list[dict[str, str]], max_domains: int = 2) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()

    for result in results:
        url = (result.get("url", "") or "").strip()
        if not url:
            continue
        try:
            netloc = urlparse(url).netloc.lower()
            if netloc.startswith("www."):
                netloc = netloc[4:]
            if not netloc or netloc in seen:
                continue
            seen.add(netloc)
            domains.append(netloc)
            if len(domains) >= max_domains:
                break
        except Exception:
            continue
    return domains


def _urls_from_results(results: list[dict[str, str]], max_urls: int = 2) -> list[str]:
    urls: list[str] = []
    seen: set[str] = set()
    for result in results:
        url = (result.get("url", "") or "").strip()
        if not url:
            continue
        key = url.lower()
        if key in seen:
            continue
        seen.add(key)
        urls.append(url)
        if len(urls) >= max_urls:
            break
    return urls


def verify_response(
    response_text: str,
    route: Any,
    web_results: list[dict[str, str]] | None = None,
    datetime_payload: dict[str, str] | None = None,
    append_sources_block: bool = True,
) -> VerificationResult:
    """Applies lightweight quality checks before returning response to user."""
    web_results = web_results or []
    text = (response_text or "").strip()
    issues: list[str] = []

    if not text:
        issues.append("empty_response")
        return VerificationResult(
            passed=False,
            issues=issues,
            response="No pude completar la respuesta. Podrias reformular la pregunta con mas detalle?",
        )

    temporal = bool(getattr(route, "entities", {}).get("temporal_reference"))
    if temporal and RELATIVE_TEMPORAL_RE.search(text) and not ABSOLUTE_DATE_RE.search(text):
        if datetime_payload and datetime_payload.get("date"):
            issues.append("missing_absolute_date")
            text = f"{text}\n\nFecha de referencia usada: {datetime_payload['date']}."

    if not web_results and SOURCE_LINE_RE.search(text):
        issues.append("source_claim_without_results")
        lines = [line for line in text.splitlines() if not SOURCE_LINE_RE.search(line)]
        text = "\n".join(lines).strip()

    if append_sources_block and web_results and not SOURCE_LINE_RE.search(text):
        urls = _urls_from_results(web_results)
        if urls:
            issues.append("sources_appended")
            source_lines = ["Fuentes:", *[f"- {url}" for url in urls]]
            text = f"{text}\n\n" + "\n".join(source_lines)

    if getattr(route, "intent", "") == "web_search" and not web_results and GENERIC_FAILURE_RE.search(text):
        issues.append("unhelpful_failure_message")
        clarification = "No encontre resultados verificables. Dime termino exacto, pais o fecha para refinar la busqueda."
        if clarification.lower() not in text.lower():
            text = f"{text}\n\n{clarification}"

    return VerificationResult(
        passed=len(issues) == 0,
        issues=issues,
        response=text,
    )
