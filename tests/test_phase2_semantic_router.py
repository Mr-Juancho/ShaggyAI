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


if __name__ == "__main__":
    unittest.main()
