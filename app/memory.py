"""
Sistema de memoria con ChromaDB y embeddings de Ollama.
Permite al agente recordar informacion del usuario entre sesiones.
"""

import hashlib
import re
import time
import unicodedata
from typing import Any, Optional

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

    @staticmethod
    def _normalize_text_key(value: str) -> str:
        """Normaliza texto para comparar frases con ruido de acentos/puntuacion."""
        text = unicodedata.normalize("NFKD", str(value or ""))
        text = "".join(ch for ch in text if not unicodedata.combining(ch))
        text = text.lower()
        text = re.sub(r"[^a-z0-9\s]", " ", text)
        return " ".join(text.split())

    def _find_lexical_matches(
        self,
        entries: list[dict[str, Any]],
        query: str,
        max_items: int = 3,
    ) -> list[dict[str, Any]]:
        """Busca coincidencias directas por texto para minimizar falsos positivos."""
        norm_query = self._normalize_text_key(query)
        if not norm_query:
            return []

        matches: list[dict[str, Any]] = []
        for entry in entries:
            document = str(entry.get("document", ""))
            norm_doc = self._normalize_text_key(document)
            if not norm_doc:
                continue
            if norm_query in norm_doc or norm_doc in norm_query:
                matches.append(entry)
            if len(matches) >= max_items:
                break
        return matches

    def _filter_entries_for_user(
        self,
        entries: list[dict[str, Any]],
        user_id: Optional[str],
    ) -> list[dict[str, Any]]:
        """Filtra entradas por user_id cuando hay metadata, conservando compatibilidad legacy."""
        if not user_id:
            return entries

        user_key = str(user_id).strip()
        with_user_id = [
            entry
            for entry in entries
            if str(self._safe_metadata(entry.get("metadata")).get("user_id", "")).strip() == user_key
        ]
        if with_user_id:
            return with_user_id

        # Fallback legacy: entradas antiguas sin user_id.
        without_user_id = [
            entry
            for entry in entries
            if not str(self._safe_metadata(entry.get("metadata")).get("user_id", "")).strip()
        ]
        return without_user_id or entries

    @staticmethod
    def _safe_metadata(value: Any) -> dict[str, Any]:
        """Retorna metadata como dict incluso si Chroma entrega nulos."""
        if isinstance(value, dict):
            return value
        return {}

    async def list_user_profile_entries(
        self,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """Lista entradas crudas de perfil con IDs para operaciones de edición/borrado."""
        try:
            if self.user_profile.count() == 0:
                return []

            payload = self.user_profile.get(
                limit=max(1, min(limit, 2000)),
                include=["documents", "metadatas"],
            )
            ids = payload.get("ids", []) or []
            documents = payload.get("documents", []) or []
            metadatas = payload.get("metadatas", []) or []

            rows: list[dict[str, Any]] = []
            for idx, doc_id in enumerate(ids):
                rows.append(
                    {
                        "id": doc_id,
                        "document": documents[idx] if idx < len(documents) else "",
                        "metadata": self._safe_metadata(
                            metadatas[idx] if idx < len(metadatas) else {}
                        ),
                    }
                )
            return rows
        except Exception as e:
            logger.error(f"Error al listar perfil de memoria: {e}")
            return []

    async def search_user_profile_entries(
        self,
        query: str,
        n: int = 5,
    ) -> list[dict[str, Any]]:
        """Busca entradas de perfil por similitud semántica (incluye distancia)."""
        try:
            total = self.user_profile.count()
            if total == 0:
                return []

            result = self.user_profile.query(
                query_texts=[query],
                n_results=max(1, min(n, total)),
                include=["documents", "distances", "metadatas"],
            )
            ids = (result.get("ids") or [[]])[0]
            documents = (result.get("documents") or [[]])[0]
            distances = (result.get("distances") or [[]])[0]
            metadatas = (result.get("metadatas") or [[]])[0]

            rows: list[dict[str, Any]] = []
            for idx, doc_id in enumerate(ids):
                distance = distances[idx] if idx < len(distances) else None
                rows.append(
                    {
                        "id": doc_id,
                        "document": documents[idx] if idx < len(documents) else "",
                        "distance": float(distance) if isinstance(distance, (int, float)) else None,
                        "metadata": self._safe_metadata(
                            metadatas[idx] if idx < len(metadatas) else {}
                        ),
                    }
                )

            rows.sort(key=lambda item: item.get("distance", 99.0))
            return rows
        except Exception as e:
            logger.error(f"Error al buscar entradas de perfil: {e}")
            return []

    async def delete_user_facts_by_query(
        self,
        query: str,
        user_id: Optional[str] = None,
        max_items: int = 3,
        max_distance: float = 0.32,
    ) -> dict[str, Any]:
        """
        Elimina recuerdos del perfil por query semántica/lexical.
        Prioriza coincidencia textual y usa embeddings como fallback.
        """
        query_clean = str(query or "").strip()
        if len(query_clean) < 2:
            return {"deleted_count": 0, "deleted_documents": []}

        entries = await self.list_user_profile_entries(limit=500)
        entries = self._filter_entries_for_user(entries, user_id=user_id)
        lexical = self._find_lexical_matches(entries, query=query_clean, max_items=max_items)

        selected: list[dict[str, Any]] = list(lexical)
        if not selected:
            candidates = await self.search_user_profile_entries(query_clean, n=max_items * 4)
            candidates = self._filter_entries_for_user(candidates, user_id=user_id)
            for item in candidates:
                distance = item.get("distance")
                if distance is None or distance > max_distance:
                    continue
                selected.append(item)
                if len(selected) >= max_items:
                    break

        ids = [item["id"] for item in selected if item.get("id")]
        if not ids:
            return {"deleted_count": 0, "deleted_documents": []}

        try:
            self.user_profile.delete(ids=ids)
            logger.info(f"Memoria eliminada por query='{query_clean}': {len(ids)} item(s)")
            return {
                "deleted_count": len(ids),
                "deleted_documents": [str(item.get("document", "")) for item in selected],
            }
        except Exception as e:
            logger.error(f"Error al eliminar memorias por query: {e}")
            return {"deleted_count": 0, "deleted_documents": []}

    async def update_user_fact(
        self,
        target_query: str,
        replacement_facts: list[str],
        user_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """
        Reemplaza un recuerdo existente por nuevos hechos.
        Estrategia: localizar mejor match y hacer delete + add.
        """
        query_clean = str(target_query or "").strip()
        replacements = [str(item).strip() for item in replacement_facts if str(item).strip()]
        if len(query_clean) < 2 or not replacements:
            return {
                "updated": False,
                "reason": "invalid_input",
                "replaced_document": "",
                "stored_count": 0,
            }

        entries = await self.list_user_profile_entries(limit=500)
        entries = self._filter_entries_for_user(entries, user_id=user_id)
        lexical = self._find_lexical_matches(entries, query=query_clean, max_items=1)

        selected: dict[str, Any] | None = lexical[0] if lexical else None
        if not selected:
            candidates = await self.search_user_profile_entries(query_clean, n=4)
            candidates = self._filter_entries_for_user(candidates, user_id=user_id)
            if candidates:
                top = candidates[0]
                distance = top.get("distance")
                if isinstance(distance, (float, int)) and distance <= 0.32:
                    selected = top

        if not selected or not selected.get("id"):
            return {
                "updated": False,
                "reason": "not_found",
                "replaced_document": "",
                "stored_count": 0,
            }

        try:
            self.user_profile.delete(ids=[selected["id"]])
        except Exception as e:
            logger.error(f"Error al eliminar memoria previa para update: {e}")
            return {
                "updated": False,
                "reason": "delete_failed",
                "replaced_document": "",
                "stored_count": 0,
            }

        stored_count = 0
        for fact in replacements[:6]:
            metadata: dict[str, Any] = {"source": "memory_update"}
            if user_id:
                metadata["user_id"] = str(user_id)
            stored = await self.store_user_info(
                fact,
                metadata=metadata,
            )
            if stored:
                stored_count += 1

        if stored_count == 0:
            return {
                "updated": False,
                "reason": "replacement_not_stored",
                "replaced_document": str(selected.get("document", "")),
                "stored_count": 0,
            }

        return {
            "updated": True,
            "reason": "ok",
            "replaced_document": str(selected.get("document", "")),
            "stored_count": stored_count,
        }

    async def purge_all_memory(self) -> dict[str, int]:
        """Borra toda la memoria persistente (perfil + conversaciones)."""
        def _normalize_ids(raw_ids: Any) -> list[str]:
            if not raw_ids:
                return []
            if isinstance(raw_ids, list) and raw_ids and isinstance(raw_ids[0], list):
                flattened = [item for group in raw_ids for item in group]
            elif isinstance(raw_ids, list):
                flattened = raw_ids
            else:
                return []
            return [str(item).strip() for item in flattened if str(item).strip()]

        def _purge_collection(collection: Any, label: str, batch_size: int = 250) -> int:
            deleted_total = 0
            for _ in range(5000):
                try:
                    payload = collection.get(limit=batch_size)
                except TypeError:
                    payload = collection.get()
                ids = _normalize_ids(payload.get("ids", []) if payload else [])
                if not ids:
                    break
                collection.delete(ids=ids)
                deleted_total += len(ids)
            else:
                logger.warning(
                    "Purgado de %s alcanzó el máximo de iteraciones; revisa colección.",
                    label,
                )
            return deleted_total

        profile_deleted = 0
        conversations_deleted = 0
        try:
            profile_deleted = _purge_collection(self.user_profile, "user_profile")
        except Exception as e:
            logger.error(f"Error al purgar perfil de memoria: {e}")

        try:
            conversations_deleted = _purge_collection(self.conversations, "conversations")
        except Exception as e:
            logger.error(f"Error al purgar conversaciones de memoria: {e}")

        logger.info(
            "Purgado total de memoria completado: perfil=%s conversaciones=%s",
            profile_deleted,
            conversations_deleted,
        )
        return {
            "profile_deleted": profile_deleted,
            "conversations_deleted": conversations_deleted,
        }

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
        llm_engine,
        user_id: Optional[str] = None,
    ) -> None:
        """
        Usa el LLM para extraer informacion nueva del usuario
        desde la conversacion y guardarla automaticamente.
        """
        if not conversation:
            return

        # Extraer solo texto del usuario para evitar contaminar memoria
        # con reformulaciones del asistente o contexto inyectado.
        recent_user_messages: list[str] = []
        for item in conversation[-10:]:
            if str(item.get("role", "")).strip().lower() != "user":
                continue
            content = str(item.get("content", "")).strip()
            if content:
                recent_user_messages.append(content)

        if not recent_user_messages:
            return

        conv_text = "\n".join(f"user: {msg}" for msg in recent_user_messages[-6:])

        extraction_prompt = (
            "Analiza la siguiente conversacion y extrae SOLO datos personales "
            "nuevos del usuario (nombre, gustos, preferencias, datos importantes). "
            "Si hay informacion nueva, responde con una lista, un dato por linea. "
            "Ignora preguntas sin datos nuevos, texto ambiguo y comandos del sistema.\n"
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
                words = re.findall(r"[a-zA-Z0-9áéíóúñü]+", info, flags=re.IGNORECASE)
                if len(words) < 2:
                    continue
                metadata: dict[str, Any] = {"source": "auto_extraction"}
                if user_id:
                    metadata["user_id"] = str(user_id)
                await self.store_user_info(
                    info,
                    metadata=metadata,
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
