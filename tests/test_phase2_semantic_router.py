import unittest

from app.capability_registry import CapabilityRegistry
from app.product_scope import ProductScope
from app.semantic_router import SemanticRouter


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def generate_response(self, messages, system_prompt):
        if self.outputs:
            return self.outputs.pop(0)
        return '{"intent":"general_chat","entities":{},"candidate_tools":["chat_general"],"confidence":0.6,"needs_clarification":false,"clarification_question":""}'


class Phase2SemanticRouterTests(unittest.IsolatedAsyncioTestCase):
    async def test_router_classifies_web_and_injects_datetime_tool(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        router = SemanticRouter(FakeLLM(["invalid json"]), registry, scope)

        decision = await router.route(
            message="Busca el clima hoy en Pamplona",
            history=[{"role": "user", "content": "Necesito plan para hoy"}],
        )

        self.assertEqual(decision.intent, "web_search")
        self.assertIn("web_search_general", decision.candidate_tools)
        self.assertIn("get_current_datetime", decision.candidate_tools)
        self.assertTrue(decision.entities.get("temporal_reference"))

    async def test_router_accepts_valid_json_output(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        llm_output = (
            '{"intent":"time_sensitive_answer","entities":{"temporal_reference":true},'
            '"candidate_tools":["get_current_datetime","chat_general"],'
            '"confidence":0.91,"needs_clarification":false,"clarification_question":""}'
        )
        router = SemanticRouter(FakeLLM([llm_output]), registry, scope)

        decision = await router.route(
            message="Que fecha es hoy?",
            history=[],
        )

        self.assertEqual(decision.intent, "time_sensitive_answer")
        self.assertIn("get_current_datetime", decision.candidate_tools)
        self.assertGreaterEqual(decision.confidence, 0.8)

    async def test_router_fallback_detects_memory_delete(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        router = SemanticRouter(FakeLLM(["invalid json"]), registry, scope)

        decision = await router.route(
            message="Olvida de tu memoria que me gusta el cafe",
            history=[],
        )

        self.assertEqual(decision.intent, "memory_delete")
        self.assertIn("memory_delete_user_fact", decision.candidate_tools)

    async def test_router_reminder_management_includes_postpone_and_update_tools(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        llm_output = (
            '{"intent":"reminder_management","entities":{},'
            '"candidate_tools":["reminder_create"],'
            '"confidence":0.90,"needs_clarification":false,"clarification_question":""}'
        )
        router = SemanticRouter(FakeLLM([llm_output]), registry, scope)

        decision = await router.route(
            message="mueve mi recordatorio de pagar luz para mañana",
            history=[],
        )

        self.assertEqual(decision.intent, "reminder_management")
        self.assertIn("reminder_create", decision.candidate_tools)
        self.assertIn("reminder_update", decision.candidate_tools)
        self.assertIn("reminder_postpone", decision.candidate_tools)

    async def test_heuristic_lowers_confidence_for_ambiguous_reminder_phrase(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        router = SemanticRouter(FakeLLM(["invalid json"]), registry, scope)

        decision = router._heuristic_route("Recuérdame que debemos mejorar la interfaz")

        self.assertEqual(decision.intent, "reminder_management")
        self.assertLessEqual(decision.confidence, 0.60)

    async def test_router_prefers_memory_delete_over_reminder_when_memory_is_explicit(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        router = SemanticRouter(FakeLLM(["invalid json"]), registry, scope)

        decision = await router.route(
            message="Elimina de la memoria a largo plazo el tener que mejorar los recordatorios",
            history=[],
        )

        self.assertEqual(decision.intent, "memory_delete")
        self.assertIn("memory_delete_user_fact", decision.candidate_tools)

    async def test_router_treats_non_action_reminder_mentions_as_general_chat(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        router = SemanticRouter(FakeLLM(["invalid json"]), registry, scope)

        decision = await router.route(
            message="Hoy estuve pensando en mejorar los recordatorios del asistente",
            history=[],
        )

        self.assertEqual(decision.intent, "general_chat")
        self.assertIn("chat_general", decision.candidate_tools)


if __name__ == "__main__":
    unittest.main()
