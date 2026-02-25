"""
Motor de conexion a Ollama.
Clase OllamaEngine que conecta al API local de Ollama para generar respuestas.
"""

import re
from typing import Optional

import httpx

from app.config import (
    OLLAMA_URL,
    OLLAMA_MODEL,
    OLLAMA_TIMEOUT,
    OLLAMA_THINK,
    OLLAMA_THINK_LEVEL,
    logger,
)


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
        system_prompt: str
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

        payload: dict = {
            "model": self.model,
            "messages": ollama_messages,
            "stream": False,
        }
        if self.think:
            payload["think"] = True

        try:
            client = await self._get_client()
            logger.debug(f"Enviando request a Ollama: {len(ollama_messages)} mensajes")
            response = await client.post(self.chat_url, json=payload)
            response.raise_for_status()

            data = response.json()
            assistant_message = data.get("message", {}).get("content", "")

            # Eliminar bloque <think>...</think> del output visible.
            if self.think and assistant_message:
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

    async def check_health(self) -> bool:
        """Verifica si Ollama esta corriendo y accesible."""
        try:
            client = await self._get_client()
            response = await client.get(f"{self.base_url}/api/tags", timeout=5.0)
            return response.status_code == 200
        except Exception as e:
            logger.warning(f"Ollama no esta disponible: {e}")
            return False
