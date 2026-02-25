"""
Sistema de memoria con ChromaDB y embeddings de Ollama.
Permite al agente recordar informacion del usuario entre sesiones.
"""

import hashlib
import time
from typing import Optional

import chromadb
import httpx

from app.config import (
    CHROMA_DIR, OLLAMA_URL, EMBEDDING_MODEL,
    OLLAMA_TIMEOUT, logger
)


class OllamaEmbeddingFunction:
    """Funcion de embedding personalizada que usa Ollama (nomic-embed-text)."""

    def __init__(self, base_url: str = OLLAMA_URL, model: str = EMBEDDING_MODEL):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.embed_url = f"{self.base_url}/api/embed"
        self.legacy_embed_url = f"{self.base_url}/api/embeddings"
        self._api_mode: Optional[str] = None  # "embed" | "embeddings"

    def _extract_embeddings_from_response(self, data: dict, text_count: int) -> list[list[float]]:
        """Normaliza distintas respuestas de Ollama al formato lista de vectores."""
        embeddings = data.get("embeddings")
        if isinstance(embeddings, list) and embeddings:
            return embeddings

        embedding = data.get("embedding")
        if isinstance(embedding, list) and embedding:
            # API legacy devuelve 1 embedding por request.
            if embedding and isinstance(embedding[0], (int, float)):
                return [embedding]
            return embedding

        logger.warning("Ollama no retorno embeddings validos")
        return [[0.0] * 768 for _ in range(text_count)]

    def _embed_with_new_api(self, client: httpx.Client, texts: list[str]) -> list[list[float]]:
        """Usa endpoint nuevo /api/embed con soporte batch."""
        response = client.post(
            self.embed_url,
            json={"model": self.model, "input": texts},
        )
        response.raise_for_status()
        return self._extract_embeddings_from_response(response.json(), text_count=len(texts))

    def _embed_with_legacy_api(self, client: httpx.Client, texts: list[str]) -> list[list[float]]:
        """Usa endpoint legacy /api/embeddings (1 request por texto)."""
        vectors: list[list[float]] = []
        for text in texts:
            response = client.post(
                self.legacy_embed_url,
                json={"model": self.model, "prompt": text},
            )
            response.raise_for_status()
            parsed = self._extract_embeddings_from_response(response.json(), text_count=1)
            vectors.append(parsed[0])
        return vectors

    def __call__(self, input: list[str]) -> list[list[float]]:
        """Genera embeddings para una lista de textos (sincrono para ChromaDB)."""
        texts = input if isinstance(input, list) else [str(input)]
        try:
            with httpx.Client(timeout=OLLAMA_TIMEOUT) as client:
                if self._api_mode == "embed":
                    return self._embed_with_new_api(client, texts)
                if self._api_mode == "embeddings":
                    return self._embed_with_legacy_api(client, texts)

                try:
                    vectors = self._embed_with_new_api(client, texts)
                    self._api_mode = "embed"
                    return vectors
                except httpx.HTTPStatusError as e:
                    # Compatibilidad con versiones antiguas de Ollama.
                    if e.response.status_code != 404:
                        raise
                    vectors = self._embed_with_legacy_api(client, texts)
                    self._api_mode = "embeddings"
                    logger.info(
                        "Usando endpoint legacy de embeddings de Ollama (/api/embeddings)"
                    )
                    return vectors
        except Exception as e:
            logger.error(f"Error al generar embeddings: {e}")
            return [[0.0] * 768 for _ in texts]

    def embed_query(self, input: list[str]) -> list[list[float]]:
        """Embedding para queries (requerido por ChromaDB v1+)."""
        return self.__call__(input=input)

    # ChromaDB >=1.0 requiere metodos extra en EmbeddingFunction.
    def name(self) -> str:
        """Nombre estable de la funcion de embeddings."""
        return f"ollama-{self.model}"

    def get_config(self) -> dict:
        """Retorna configuracion serializable del embedding function."""
        return {
            "base_url": self.base_url,
            "model": self.model,
        }

    @staticmethod
    def build_from_config(config: dict) -> "OllamaEmbeddingFunction":
        """Reconstruye la funcion desde config persistida por ChromaDB."""
        return OllamaEmbeddingFunction(
            base_url=config.get("base_url", OLLAMA_URL),
            model=config.get("model", EMBEDDING_MODEL),
        )


class MemoryManager:
    """
    Gestiona la memoria persistente del agente usando ChromaDB.
    Dos colecciones:
    - user_profile: informacion del usuario (gustos, datos, preferencias)
    - conversations: resumenes de conversaciones relevantes
    """

    def __init__(self, persist_dir: str = CHROMA_DIR):
        self.embedding_fn = OllamaEmbeddingFunction()

        # Cliente ChromaDB persistente
        self.client = chromadb.PersistentClient(path=persist_dir)

        # Colecciones
        self.user_profile = self.client.get_or_create_collection(
            name="user_profile",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )
        self.conversations = self.client.get_or_create_collection(
            name="conversations",
            embedding_function=self.embedding_fn,
            metadata={"hnsw:space": "cosine"}
        )

        logger.info(
            f"MemoryManager inicializado: "
            f"perfil={self.user_profile.count()} items, "
            f"conversaciones={self.conversations.count()} items"
        )

    async def search_relevant_context(
        self,
        query: str,
        n: int = 5
    ) -> str:
        """
        Busca contexto relevante en ambas colecciones.
        Retorna un texto formateado con la informacion encontrada.
        """
        context_parts = []

        try:
            # Buscar en perfil del usuario
            if self.user_profile.count() > 0:
                profile_results = self.user_profile.query(
                    query_texts=[query],
                    n_results=min(n, self.user_profile.count())
                )
                if profile_results and profile_results["documents"][0]:
                    context_parts.append("Datos del usuario:")
                    for doc in profile_results["documents"][0]:
                        context_parts.append(f"  - {doc}")

            # Buscar en conversaciones
            if self.conversations.count() > 0:
                conv_results = self.conversations.query(
                    query_texts=[query],
                    n_results=min(n, self.conversations.count())
                )
                if conv_results and conv_results["documents"][0]:
                    context_parts.append("Conversaciones previas relevantes:")
                    for doc in conv_results["documents"][0]:
                        context_parts.append(f"  - {doc}")

        except Exception as e:
            logger.error(f"Error al buscar contexto: {e}")

        if context_parts:
            result = "\n".join(context_parts)
            logger.debug(f"Contexto encontrado: {len(result)} caracteres")
            return result

        return ""

    async def store_user_info(
        self,
        info: str,
        metadata: Optional[dict] = None
    ) -> bool:
        """
        Guarda informacion del usuario en el perfil.
        Evita duplicados verificando distancia coseno < 0.15.
        """
        try:
            # Verificar duplicados
            if self.user_profile.count() > 0:
                existing = self.user_profile.query(
                    query_texts=[info],
                    n_results=1
                )
                if existing and existing["distances"][0]:
                    min_distance = existing["distances"][0][0]
                    if min_distance < 0.15:
                        logger.debug(
                            f"Info duplicada (distancia={min_distance:.4f}): {info[:50]}..."
                        )
                        return False

            # Generar ID unico
            doc_id = hashlib.md5(
                f"{info}_{time.time()}".encode()
            ).hexdigest()

            # Metadata por defecto
            meta = {
                "timestamp": time.time(),
                "source": "auto"
            }
            if metadata:
                meta.update(metadata)

            self.user_profile.add(
                documents=[info],
                ids=[doc_id],
                metadatas=[meta]
            )
            logger.info(f"Info guardada en perfil: {info[:80]}...")
            return True

        except Exception as e:
            logger.error(f"Error al guardar info del usuario: {e}")
            return False

    async def store_conversation_summary(
        self,
        summary: str,
        metadata: Optional[dict] = None
    ) -> bool:
        """Guarda un resumen de conversacion."""
        try:
            doc_id = hashlib.md5(
                f"conv_{summary}_{time.time()}".encode()
            ).hexdigest()

            meta = {
                "timestamp": time.time(),
                "type": "conversation_summary"
            }
            if metadata:
                meta.update(metadata)

            self.conversations.add(
                documents=[summary],
                ids=[doc_id],
                metadatas=[meta]
            )
            logger.info(f"Resumen de conversacion guardado: {summary[:80]}...")
            return True

        except Exception as e:
            logger.error(f"Error al guardar resumen: {e}")
            return False

    async def extract_and_store_info(
        self,
        conversation: list[dict],
        llm_engine
    ) -> None:
        """
        Usa el LLM para extraer informacion nueva del usuario
        desde la conversacion y guardarla automaticamente.
        """
        if not conversation:
            return

        # Tomar los ultimos mensajes relevantes
        recent = conversation[-6:]
        conv_text = "\n".join(
            f"{m['role']}: {m['content']}" for m in recent
        )

        extraction_prompt = (
            "Analiza la siguiente conversacion y extrae SOLO datos personales "
            "nuevos del usuario (nombre, gustos, preferencias, datos importantes). "
            "Si hay informacion nueva, responde con una lista, un dato por linea. "
            "Si NO hay informacion nueva relevante, responde exactamente: NADA\n\n"
            f"Conversacion:\n{conv_text}"
        )

        try:
            response = await llm_engine.generate_response(
                messages=[{"role": "user", "content": extraction_prompt}],
                system_prompt="Eres un extractor de datos. Responde solo con los datos o NADA."
            )

            if response.strip().upper() == "NADA" or "error" in response.lower()[:10]:
                return

            # Guardar cada dato extraido
            lines = [
                line.strip().lstrip("- ").lstrip("• ")
                for line in response.strip().split("\n")
                if line.strip() and len(line.strip()) > 3
            ]

            for info in lines:
                await self.store_user_info(
                    info,
                    metadata={"source": "auto_extraction"}
                )

        except Exception as e:
            logger.error(f"Error al extraer info de conversacion: {e}")

    async def get_user_profile_summary(self) -> str:
        """Retorna un resumen del perfil completo del usuario."""
        try:
            if self.user_profile.count() == 0:
                return "No hay informacion guardada del usuario."

            all_data = self.user_profile.get()
            if not all_data or not all_data["documents"]:
                return "No hay informacion guardada del usuario."

            items = all_data["documents"]
            summary = "Perfil del usuario:\n"
            for i, item in enumerate(items, 1):
                summary += f"  {i}. {item}\n"

            return summary

        except Exception as e:
            logger.error(f"Error al obtener perfil: {e}")
            return "Error al acceder al perfil."
