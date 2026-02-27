"""
Motor de conexion a Ollama.
Clase OllamaEngine que conecta al API local de Ollama para generar respuestas.
"""

import asyncio
import re
from typing import Any, Optional

import httpx

from app.config import (
    OLLAMA_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    OLLAMA_THINK,
    OLLAMA_THINK_LEVEL,
    logger,
)

THINK_LEVELS = {"low", "medium", "high"}
THINK_TRUE_VALUES = {"1", "true", "yes", "on"}
THINK_FALSE_VALUES = {"0", "false", "no", "off", "none"}


class OllamaEngine:
    """Gestiona la comunicacion con Ollama API (/api/chat)."""

    def __init__(
        self,
        base_url: str = OLLAMA_URL,
        model: str = OLLAMA_MODEL,
        timeout: int = OLLAMA_TIMEOUT,
        think: bool = OLLAMA_THINK,
        think_level: Optional[str] = OLLAMA_THINK_LEVEL,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.think = think
        self.think_level = think_level
        self.chat_url = f"{self.base_url}/api/chat"
        self._client: Optional[httpx.AsyncClient] = None
        logger.info(
            f"OllamaEngine inicializado: modelo={self.model}, "
            f"url={self.base_url}, think={self.think}, think_level={self.think_level}"
        )

    async def _get_client(self) -> httpx.AsyncClient:
        """Retorna un cliente HTTP reutilizable."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self.timeout)
        return self._client

    async def close(self) -> None:
        """Cierra el cliente HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def generate_response(
        self,
        messages: list[dict],
        system_prompt: str,
        model: Optional[str] = None,
        think_mode: Optional[str] = None,
    ) -> str:
        """
        Envia mensajes a Ollama y retorna la respuesta generada.

        Args:
            messages: Lista de mensajes [{role: str, content: str}]
            system_prompt: El prompt de sistema con personalidad + contexto

        Returns:
            Respuesta generada por el modelo como string
        """
        # Construir lista de mensajes con system prompt al inicio
        ollama_messages = [
            {"role": "system", "content": system_prompt}
        ]
        ollama_messages.extend(messages)

        effective_model = (model or self.model or "").strip() or self.model
        think_payload, normalized_mode = self._resolve_think_payload(
            model_name=effective_model,
            think_mode=think_mode,
        )

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": ollama_messages,
            "stream": False,
        }
        if think_payload is not None:
            payload["think"] = think_payload

        try:
            client = await self._get_client()
            logger.debug(
                "Enviando request a Ollama: %s mensajes, model=%s, think_mode=%s",
                len(ollama_messages),
                effective_model,
                normalized_mode,
            )
            response = await client.post(self.chat_url, json=payload)
            response.raise_for_status()

            data = response.json()
            assistant_message = data.get("message", {}).get("content", "")

            # Eliminar bloque <think>...</think> del output visible.
            if payload.get("think") not in (None, False) and assistant_message:
                assistant_message = re.sub(
                    r"<think>[\s\S]*?</think>\s*", "", assistant_message
                ).strip()

            if not assistant_message:
                logger.warning("Ollama retorno una respuesta vacia")
                return "Lo siento, no pude generar una respuesta. Intenta de nuevo."

            logger.info(f"Respuesta generada: {len(assistant_message)} caracteres")
            return assistant_message

        except httpx.ConnectError:
            error_msg = (
                "No se pudo conectar a Ollama. "
                f"Asegurate de que Ollama este corriendo en {self.base_url}. "
                "Ejecuta: ollama serve"
            )
            logger.error(error_msg)
            return f"Error: {error_msg}"

        except httpx.TimeoutException:
            error_msg = (
                f"Ollama tardo mas de {self.timeout}s en responder. "
                "El modelo puede estar cargandose. Intenta de nuevo."
            )
            logger.error(error_msg)
            return f"Error: {error_msg}"

        except httpx.HTTPStatusError as e:
            error_msg = f"Error HTTP de Ollama: {e.response.status_code} - {e.response.text}"
            logger.error(error_msg)
            return f"Error: {error_msg}"

        except Exception as e:
            error_msg = f"Error inesperado al comunicarse con Ollama: {str(e)}"
            logger.error(error_msg)
            return f"Error: {error_msg}"

    @staticmethod
    def infer_think_mode_type(
        model_name: str,
        capabilities: Optional[list[str]] = None,
    ) -> str:
        """
        Devuelve tipo de thinking para un modelo:
        - levels: admite low/medium/high (GPT-OSS)
        - toggle: admite on/off (boolean)
        - none: no thinking expuesto
        """
        normalized = (model_name or "").strip().lower()
        caps = {str(item).strip().lower() for item in (capabilities or []) if str(item).strip()}

        if "gpt-oss" in normalized:
            return "levels"
        if "thinking" in caps:
            return "toggle"

        # Fallback por familias/documentacion oficial de thinking.
        if normalized.startswith("qwen3") or "deepseek-r1" in normalized or "deepseek-v3.1" in normalized:
            return "toggle"

        return "none"

    @classmethod
    def supports_think_levels(
        cls,
        model_name: str,
        capabilities: Optional[list[str]] = None,
    ) -> bool:
        return cls.infer_think_mode_type(model_name, capabilities) == "levels"

    @classmethod
    def supports_thinking(
        cls,
        model_name: str,
        capabilities: Optional[list[str]] = None,
    ) -> bool:
        return cls.infer_think_mode_type(model_name, capabilities) != "none"

    @staticmethod
    def is_chat_model(
        model_name: str,
        capabilities: Optional[list[str]] = None,
    ) -> bool:
        """
        Determina si un modelo es apto para chat.
        Excluye modelos de embeddings puros (ej: nomic-embed*).
        """
        normalized = (model_name or "").strip().lower()
        if not normalized:
            return False

        caps = {str(item).strip().lower() for item in (capabilities or []) if str(item).strip()}

        # Exclusion explicita solicitada por UX: nomic-embed no es LLM conversacional.
        if normalized.startswith("nomic-embed"):
            return False

        # Regla general: si solo soporta embeddings y no completion/chat, no mostrar.
        if "embedding" in caps and "completion" not in caps and "chat" not in caps:
            return False

        return True

    def _resolve_think_payload(
        self,
        model_name: str,
        think_mode: Optional[str],
    ) -> tuple[Optional[Any], str]:
        """
        Normaliza modo de pensamiento y devuelve:
        - payload `think` para Ollama (None/bool/str)
        - modo normalizado para UI/logs
        """
        requested = (think_mode or "").strip().lower()
        default_level = (self.think_level or "").strip().lower()
        think_mode_type = self.infer_think_mode_type(model_name)

        if think_mode_type == "levels":
            if requested in THINK_LEVELS:
                return requested, requested
            if requested in THINK_FALSE_VALUES:
                # gpt-oss no admite apagar thinking; low es la opcion menos profunda.
                return "low", "low"
            if requested in THINK_TRUE_VALUES:
                level = default_level if default_level in THINK_LEVELS else "medium"
                return level, level
            if default_level in THINK_LEVELS:
                return default_level, default_level
            return "medium", "medium"

        if think_mode_type == "toggle":
            if requested in THINK_FALSE_VALUES:
                return False, "off"
            if requested in THINK_LEVELS or requested in THINK_TRUE_VALUES:
                return True, "on"
            if default_level in THINK_LEVELS:
                return True, "on"
            if self.think:
                return True, "on"
            return False, "off"

        return None, "none"

    def get_effective_think_mode(
        self,
        model_name: Optional[str] = None,
        think_mode: Optional[str] = None,
    ) -> str:
        """Devuelve modo de pensamiento normalizado para un modelo dado."""
        resolved_model = (model_name or self.model or "").strip() or self.model
        _, normalized = self._resolve_think_payload(resolved_model, think_mode)
        return normalized

    async def list_models(self) -> list[dict[str, Any]]:
        """Lista modelos disponibles en Ollama usando /api/tags."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            data = response.json()
        except Exception as exc:
            logger.warning(f"No se pudieron listar modelos de Ollama: {exc}")
            return []

        raw_models = data.get("models", [])
        models: list[dict[str, Any]] = []
        seen: set[str] = set()

        async def _fetch_show_payload(name: str) -> dict[str, Any]:
            try:
                show_response = await client.post(
                    f"{self.base_url}/api/show",
                    json={"model": name},
                )
                show_response.raise_for_status()
                data = show_response.json()
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}

        normalized_entries: list[dict[str, Any]] = []

        for entry in raw_models:
            if not isinstance(entry, dict):
                continue

            name = str(entry.get("name") or entry.get("model") or "").strip()
            if not name:
                continue

            dedupe_key = name.lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            normalized_entries.append(entry)

        show_payloads: list[dict[str, Any]] = []
        if normalized_entries:
            tasks = [
                _fetch_show_payload(str(entry.get("name") or entry.get("model") or "").strip())
                for entry in normalized_entries
            ]
            show_payloads = await asyncio.gather(*tasks)

        for idx, entry in enumerate(normalized_entries):
            name = str(entry.get("name") or entry.get("model") or "").strip()
            details = entry.get("details") or {}
            family = ""
            if isinstance(details, dict):
                family = str(details.get("family") or "").strip()

            show_payload = show_payloads[idx] if idx < len(show_payloads) else {}
            show_capabilities = show_payload.get("capabilities") if isinstance(show_payload, dict) else []
            if not isinstance(show_capabilities, list):
                show_capabilities = []

            show_details = show_payload.get("details") if isinstance(show_payload, dict) else {}
            if isinstance(show_details, dict):
                show_family = str(show_details.get("family") or "").strip()
                if show_family:
                    family = show_family

            if not self.is_chat_model(name, show_capabilities):
                continue

            think_mode_type = self.infer_think_mode_type(name, show_capabilities)
            models.append(
                {
                    "name": name,
                    "size": int(entry.get("size") or 0),
                    "modified_at": entry.get("modified_at"),
                    "family": family,
                    "capabilities": show_capabilities,
                    "supports_thinking": think_mode_type != "none",
                    "supports_think_levels": think_mode_type == "levels",
                    "think_mode_type": think_mode_type,
                }
            )

        models.sort(key=lambda item: item["name"].lower())
        return models

    async def check_health(self) -> bool:
        """Verifica si Ollama esta corriendo y accesible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Ollama no esta disponible: {e}")
            return False
