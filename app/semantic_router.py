"""Semantic intent router with strict JSON contract."""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.capability_registry import CapabilityRegistry
from app.config import logger
from app.json_guard import generate_validated_json
from app.product_scope import ProductScope
from app.time_policy import has_temporal_reference
from app.utils import extract_search_intent

WEB_HINT_RE = re.compile(
    r"\b(busca|buscar|investiga|consulta|averigua|google|internet|web|"
    r"noticias?|precio|cotizacion|cotización|valor|actual)\b",
    flags=re.IGNORECASE,
)
NEWS_HINT_RE = re.compile(r"\b(noticias?|news|titulares|actualidad)\b", flags=re.IGNORECASE)
REMINDER_HINT_RE = re.compile(
    r"\b(recordatorio|recordatorios|recuerdame|recuerdame|avisame|avísame|"
    r"elimina\s+recordatorio|lista\s+recordatorios|pendientes)\b",
    flags=re.IGNORECASE,
)
MEMORY_PURGE_HINT_RE = re.compile(
    r"\b(protocolo\s+de\s+borrado|borrado\s+de\s+memoria|"
    r"resetea(?:r)?\s+memoria|reinicia(?:r)?\s+memoria)\b|"
    r"\b(borra|elimina|limpia|olvida)\b.{0,35}\b(toda|todo)\b.{0,35}\b(memoria|conversaciones?)\b",
    flags=re.IGNORECASE,
)
MEMORY_UPDATE_HINT_RE = re.compile(
    r"\b(actualiza|corrige|edita|modifica|cambia)\b.{0,45}\b("
    r"memoria|recuerdo|dato|lo\s+que\s+recuerdas|perfil)\b",
    flags=re.IGNORECASE,
)
MEMORY_DELETE_HINT_RE = re.compile(
    r"\b(olvida|borra|elimina|quita|remueve)\b.{0,45}\b("
    r"memoria|recuerdo|dato|lo\s+que\s+recuerdas|perfil)\b",
    flags=re.IGNORECASE,
)
MEMORY_RECALL_HINT_RE = re.compile(
    r"\b(que\s+recuerdas|que\s+sabes\s+de\s+mi|mi\s+perfil|"
    r"lo\s+que\s+tienes\s+guardado|recuerdos?\s+sobre)\b",
    flags=re.IGNORECASE,
)
MEMORY_STORE_HINT_RE = re.compile(
    r"\b(recuerda\s+que|acu[eé]rdate\s+de|guarda(?:r)?\s+en\s+(?:tu\s+)?memoria|"
    r"ten\s+presente\s+que|anota(?:r)?\s+en\s+tu\s+memoria)\b",
    flags=re.IGNORECASE,
)


class RouteDecision(BaseModel):
    """Structured route decision returned by the classifier."""

    intent: str = "general_chat"
    entities: dict[str, Any] = Field(default_factory=dict)
    candidate_tools: list[str] = Field(default_factory=lambda: ["chat_general"])
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    needs_clarification: bool = False
    clarification_question: str = ""


class SemanticRouter:
    """Routes messages into intents and candidate tool chains."""

    def __init__(
        self,
        llm_engine: Any,
        capability_registry: CapabilityRegistry,
        product_scope: ProductScope,
    ) -> None:
        self.llm_engine = llm_engine
        self.capability_registry = capability_registry
        self.product_scope = product_scope

    async def route(self, message: str, history: list[dict[str, str]]) -> RouteDecision:
        """Runs heuristic route and upgrades via LLM JSON classifier when possible."""
        heuristic = self._heuristic_route(message)
        semantic = await self._semantic_route_with_llm(message, history)

        decision = heuristic
        if semantic and semantic.confidence >= max(0.35, heuristic.confidence - 0.10):
            decision = semantic

        decision = self._sanitize_decision(message, decision)
        logger.info(
            "Router decision: intent=%s confidence=%.2f tools=%s",
            decision.intent,
            decision.confidence,
            decision.candidate_tools,
        )
        return decision

    def _heuristic_route(self, message: str) -> RouteDecision:
        """Fast deterministic classifier used as base and fallback."""
        message_clean = (message or "").strip()
        message_lower = message_clean.lower()
        temporal = has_temporal_reference(message_clean)

        if REMINDER_HINT_RE.search(message_clean):
            return RouteDecision(
                intent="reminder_management",
                entities={"temporal_reference": temporal},
                candidate_tools=["reminder_create", "reminder_list", "reminder_delete"],
                confidence=0.80,
            )

        if MEMORY_PURGE_HINT_RE.search(message_clean):
            return RouteDecision(
                intent="memory_purge",
                entities={"temporal_reference": temporal},
                candidate_tools=["memory_purge_all", "chat_general"],
                confidence=0.76,
            )

        if MEMORY_UPDATE_HINT_RE.search(message_clean):
            return RouteDecision(
                intent="memory_update",
                entities={"temporal_reference": temporal},
                candidate_tools=["memory_update_user_fact", "memory_recall_profile"],
                confidence=0.72,
            )

        if MEMORY_DELETE_HINT_RE.search(message_clean):
            return RouteDecision(
                intent="memory_delete",
                entities={"temporal_reference": temporal},
                candidate_tools=["memory_delete_user_fact", "memory_recall_profile"],
                confidence=0.72,
            )

        if MEMORY_RECALL_HINT_RE.search(message_clean):
            return RouteDecision(
                intent="memory_recall",
                entities={"temporal_reference": temporal},
                candidate_tools=["memory_recall_profile", "memory_retrieval"],
                confidence=0.73,
            )

        if MEMORY_STORE_HINT_RE.search(message_clean):
            return RouteDecision(
                intent="memory_store",
                entities={"temporal_reference": temporal},
                candidate_tools=["memory_store_user_fact", "memory_store_summary"],
                confidence=0.73,
            )

        extracted_query = extract_search_intent(message_clean)
        explicit_web = bool(WEB_HINT_RE.search(message_clean))
        if extracted_query or explicit_web:
            query = extracted_query or self._normalize_query(message_clean)
            primary = "web_search_news" if NEWS_HINT_RE.search(message_lower) else "web_search_general"
            return RouteDecision(
                intent="web_search",
                entities={
                    "query": query,
                    "temporal_reference": temporal,
                    "prefer_news": primary == "web_search_news",
                },
                candidate_tools=[primary, "web_search_general", "chat_general"],
                confidence=0.78,
            )

        if temporal:
            return RouteDecision(
                intent="time_sensitive_answer",
                entities={"temporal_reference": True},
                candidate_tools=["get_current_datetime", "chat_general"],
                confidence=0.70,
            )

        return RouteDecision(
            intent="general_chat",
            entities={"temporal_reference": False},
            candidate_tools=["chat_general"],
            confidence=0.55,
        )

    async def _semantic_route_with_llm(
        self,
        message: str,
        history: list[dict[str, str]],
    ) -> RouteDecision | None:
        """Asks the model for semantic route decision in strict JSON."""
        allowed_tools = self.capability_registry.all_ids()
        if not allowed_tools:
            return None

        history_tail = history[-4:] if history else []
        history_text = "\n".join(
            f"- {item.get('role', 'unknown')}: {item.get('content', '')[:160]}"
            for item in history_tail
        )

        system_prompt = (
            "Eres un clasificador semantico de intenciones para un agente SaaS. "
            "Debes devolver solo JSON con alta precision."
        )
        user_prompt = (
            "Clasifica el siguiente mensaje y propone herramientas candidatas.\n"
            f"Mensaje actual: {message}\n"
            f"Historial corto:\n{history_text or '- (sin historial)'}\n"
            "Intenciones permitidas: general_chat, web_search, time_sensitive_answer, "
            "reminder_management, memory_store, memory_recall, memory_update, "
            "memory_delete, memory_purge.\n"
            f"Herramientas permitidas: {', '.join(allowed_tools)}\n"
            "Schema requerido:\n"
            "{\n"
            '  "intent": "...",\n'
            '  "entities": {"query": "...", "temporal_reference": true},\n'
            '  "candidate_tools": ["tool_a", "tool_b"],\n'
            '  "confidence": 0.0,\n'
            '  "needs_clarification": false,\n'
            '  "clarification_question": ""\n'
            "}"
        )

        parsed, trace = await generate_validated_json(
            llm_engine=self.llm_engine,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema_model=RouteDecision,
            max_retries=2,
        )

        if not parsed:
            if trace.last_error:
                logger.warning(f"Router JSON invalido, usando heuristica. Error: {trace.last_error}")
            return None
        return parsed

    def _sanitize_decision(self, message: str, decision: RouteDecision) -> RouteDecision:
        """Ensures route decision complies with product scope and registry."""
        intent_alias = {
            "memory_edit": "memory_update",
            "memory_forget": "memory_delete",
            "memory_wipe": "memory_purge",
            "memory_reset": "memory_purge",
        }
        decision.intent = intent_alias.get(decision.intent, decision.intent)

        allowed_tools = set(self.capability_registry.all_ids())
        filtered = [tool_id for tool_id in decision.candidate_tools if tool_id in allowed_tools]
        if not filtered:
            filtered = ["chat_general"] if "chat_general" in allowed_tools else []

        temporal = bool(decision.entities.get("temporal_reference")) or has_temporal_reference(message)
        if temporal and "get_current_datetime" in allowed_tools and "get_current_datetime" not in filtered:
            filtered.insert(0, "get_current_datetime")

        if decision.intent == "web_search":
            query = str(decision.entities.get("query", "")).strip()
            if not query:
                query = extract_search_intent(message) or self._normalize_query(message)
                decision.entities["query"] = query

            if "web_search_general" in allowed_tools and not any(
                tool in {"web_search_general", "web_search_news"} for tool in filtered
            ):
                filtered.insert(0, "web_search_general")
        elif decision.intent == "memory_store":
            if "memory_store_user_fact" in allowed_tools and "memory_store_user_fact" not in filtered:
                filtered.insert(0, "memory_store_user_fact")
        elif decision.intent == "memory_recall":
            if "memory_recall_profile" in allowed_tools and "memory_recall_profile" not in filtered:
                filtered.insert(0, "memory_recall_profile")
        elif decision.intent == "memory_update":
            if "memory_update_user_fact" in allowed_tools and "memory_update_user_fact" not in filtered:
                filtered.insert(0, "memory_update_user_fact")
        elif decision.intent == "memory_delete":
            if "memory_delete_user_fact" in allowed_tools and "memory_delete_user_fact" not in filtered:
                filtered.insert(0, "memory_delete_user_fact")
        elif decision.intent == "memory_purge":
            if "memory_purge_all" in allowed_tools and "memory_purge_all" not in filtered:
                filtered.insert(0, "memory_purge_all")

        decision.candidate_tools = self.product_scope.filter_allowed(filtered)
        if not decision.candidate_tools:
            decision.candidate_tools = ["chat_general"] if self.product_scope.is_allowed("chat_general") else []

        if decision.needs_clarification and not decision.clarification_question:
            decision.clarification_question = "Podrias darme un poco mas de contexto para ayudarte mejor?"

        if decision.confidence < 0.05:
            decision.confidence = 0.05
        elif decision.confidence > 1.0:
            decision.confidence = 1.0

        return decision

    @staticmethod
    def _normalize_query(message: str) -> str:
        cleaned = (message or "").strip()
        cleaned = re.sub(
            r"^(puedes|podrias|podr[ií]as|me\s+puedes|me\s+podr[ií]as)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(
            r"^(buscar|busca|investiga|consulta|averigua|googlea)\s+",
            "",
            cleaned,
            flags=re.IGNORECASE,
        )
        return cleaned.strip(" \t\n\r?¡!.,;:")
