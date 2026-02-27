import unittest

try:
    from app.memory import MemoryManager
except Exception:  # pragma: no cover - entorno sin dependencias opcionales
    MemoryManager = None  # type: ignore[assignment]


class _FakeCollection:
    def __init__(self):
        self.deleted_ids: list[str] = []

    def delete(self, ids):
        self.deleted_ids.extend([str(item) for item in (ids or [])])


@unittest.skipIf(MemoryManager is None, "MemoryManager/chromadb no disponible en este entorno")
class MemoryDeleteAcrossCollectionsTests(unittest.IsolatedAsyncioTestCase):
    async def test_delete_query_removes_match_from_conversations_when_profile_has_none(self):
        class Harness:
            _normalize_text_key = staticmethod(MemoryManager._normalize_text_key)  # type: ignore[attr-defined]
            _safe_metadata = staticmethod(MemoryManager._safe_metadata)  # type: ignore[attr-defined]
            _find_lexical_matches = MemoryManager._find_lexical_matches  # type: ignore[attr-defined]
            _filter_entries_for_user = MemoryManager._filter_entries_for_user  # type: ignore[attr-defined]
            _has_meaningful_delete_query = MemoryManager._has_meaningful_delete_query  # type: ignore[attr-defined]
            delete_user_facts_by_query = MemoryManager.delete_user_facts_by_query  # type: ignore[attr-defined]

            def __init__(self):
                self.user_profile = _FakeCollection()
                self.conversations = _FakeCollection()
                self._profile_rows = []
                self._conversation_rows = [
                    {
                        "id": "conv_1",
                        "document": "Usuario: Recuerdame mejorar los recordatorios para Rufus",
                        "metadata": {"user_id": "u1"},
                    }
                ]

            async def list_user_profile_entries(self, limit: int = 300):
                return list(self._profile_rows)

            async def search_user_profile_entries(self, query: str, n: int = 5):
                return []

            async def list_conversation_entries(self, limit: int = 300):
                return list(self._conversation_rows)

            async def search_conversation_entries(self, query: str, n: int = 5):
                return []

        harness = Harness()

        result = await harness.delete_user_facts_by_query(  # type: ignore[attr-defined]
            query="mejorar los recordatorios",
            user_id="u1",
            max_items=3,
        )

        self.assertEqual(result["deleted_count"], 1)
        self.assertEqual(result["profile_deleted"], 0)
        self.assertEqual(result["conversations_deleted"], 1)
        self.assertIn("conv_1", harness.conversations.deleted_ids)

    async def test_delete_query_does_not_use_semantic_fallback_by_default(self):
        class Harness:
            _normalize_text_key = staticmethod(MemoryManager._normalize_text_key)  # type: ignore[attr-defined]
            _safe_metadata = staticmethod(MemoryManager._safe_metadata)  # type: ignore[attr-defined]
            _find_lexical_matches = MemoryManager._find_lexical_matches  # type: ignore[attr-defined]
            _filter_entries_for_user = MemoryManager._filter_entries_for_user  # type: ignore[attr-defined]
            _has_meaningful_delete_query = MemoryManager._has_meaningful_delete_query  # type: ignore[attr-defined]
            delete_user_facts_by_query = MemoryManager.delete_user_facts_by_query  # type: ignore[attr-defined]

            def __init__(self):
                self.user_profile = _FakeCollection()
                self.conversations = _FakeCollection()
                self._profile_rows = [
                    {
                        "id": "profile_1",
                        "document": "Me gusta Iron Man",
                        "metadata": {"user_id": "u1"},
                    }
                ]
                self._conversation_rows = []

            async def list_user_profile_entries(self, limit: int = 300):
                return list(self._profile_rows)

            async def search_user_profile_entries(self, query: str, n: int = 5):
                # Simula un match semántico cercano, que no debe borrarse por defecto.
                return [
                    {
                        "id": "profile_1",
                        "document": "Me gusta Iron Man",
                        "distance": 0.01,
                        "metadata": {"user_id": "u1"},
                    }
                ]

            async def list_conversation_entries(self, limit: int = 300):
                return list(self._conversation_rows)

            async def search_conversation_entries(self, query: str, n: int = 5):
                return []

        harness = Harness()

        result = await harness.delete_user_facts_by_query(  # type: ignore[attr-defined]
            query="TOKEN_NO_MATCH_12345",
            user_id="u1",
            max_items=3,
        )

        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(result["profile_deleted"], 0)
        self.assertEqual(result["conversations_deleted"], 0)
        self.assertEqual(harness.user_profile.deleted_ids, [])

    async def test_delete_query_rejects_generic_memory_terms_only(self):
        class Harness:
            _normalize_text_key = staticmethod(MemoryManager._normalize_text_key)  # type: ignore[attr-defined]
            _safe_metadata = staticmethod(MemoryManager._safe_metadata)  # type: ignore[attr-defined]
            _find_lexical_matches = MemoryManager._find_lexical_matches  # type: ignore[attr-defined]
            _filter_entries_for_user = MemoryManager._filter_entries_for_user  # type: ignore[attr-defined]
            _has_meaningful_delete_query = MemoryManager._has_meaningful_delete_query  # type: ignore[attr-defined]
            delete_user_facts_by_query = MemoryManager.delete_user_facts_by_query  # type: ignore[attr-defined]

            def __init__(self):
                self.user_profile = _FakeCollection()
                self.conversations = _FakeCollection()
                self._profile_rows = [
                    {
                        "id": "profile_1",
                        "document": "Me gusta el cafe",
                        "metadata": {"user_id": "u1"},
                    }
                ]
                self._conversation_rows = []

            async def list_user_profile_entries(self, limit: int = 300):
                return list(self._profile_rows)

            async def search_user_profile_entries(self, query: str, n: int = 5):
                return []

            async def list_conversation_entries(self, limit: int = 300):
                return list(self._conversation_rows)

            async def search_conversation_entries(self, query: str, n: int = 5):
                return []

        harness = Harness()
        result = await harness.delete_user_facts_by_query(  # type: ignore[attr-defined]
            query="eliminar recuerdos de memoria y conversaciones",
            user_id="u1",
        )
        self.assertEqual(result["deleted_count"], 0)
        self.assertEqual(harness.user_profile.deleted_ids, [])


if __name__ == "__main__":
    unittest.main()
