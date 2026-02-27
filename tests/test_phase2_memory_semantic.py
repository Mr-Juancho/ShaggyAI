import unittest

from app.capability_registry import CapabilityRegistry
from app.memory_semantic import (
    extract_memory_facts_fallback,
    extract_memory_mutation_plan,
    extract_memory_write_plan,
)
from app.product_scope import ProductScope
from app.semantic_router import SemanticRouter


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def generate_response(self, messages, system_prompt):
        if self.outputs:
            return self.outputs.pop(0)
        return '{"intent":"general_chat","entities":{},"candidate_tools":["chat_general"],"confidence":0.6,"needs_clarification":false,"clarification_question":""}'


class Phase2MemorySemanticTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_memory_write_plan(self):
        llm_output = (
            '{"should_store":true,"facts":["Los nombres de mis padres son Ana y Luis"],'
            '"confidence":0.93,"clarification_question":""}'
        )
        plan = await extract_memory_write_plan(
            llm_engine=FakeLLM([llm_output]),
            message="Recuerda que los nombres de mis padres son Ana y Luis",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertTrue(plan.should_store)
        self.assertIn("Ana y Luis", plan.facts[0])

    async def test_router_supports_memory_store_intent(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        llm_output = (
            '{"intent":"memory_store","entities":{},'
            '"candidate_tools":["memory_store_user_fact"],'
            '"confidence":0.89,"needs_clarification":false,"clarification_question":""}'
        )
        router = SemanticRouter(FakeLLM([llm_output]), registry, scope)

        decision = await router.route(
            message="Recuerda que mi madre se llama Ana",
            history=[],
        )

        self.assertEqual(decision.intent, "memory_store")
        self.assertIn("memory_store_user_fact", decision.candidate_tools)

    async def test_extract_memory_mutation_update_plan(self):
        llm_output = (
            '{"operation":"update","should_apply":true,'
            '"target_query":"me gusta el cafe",'
            '"replacement_facts":["prefiero el te"],'
            '"confidence":0.92,"clarification_question":"",'
            '"requires_confirmation":false}'
        )
        plan = await extract_memory_mutation_plan(
            llm_engine=FakeLLM([llm_output]),
            message="Actualiza tu memoria: ya no me gusta el cafe, prefiero el te",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.operation, "update")
        self.assertTrue(plan.should_apply)
        self.assertIn("prefiero el te", plan.replacement_facts[0].lower())

    async def test_router_supports_memory_purge_intent(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        llm_output = (
            '{"intent":"memory_purge","entities":{},'
            '"candidate_tools":["memory_purge_all"],'
            '"confidence":0.90,"needs_clarification":false,"clarification_question":""}'
        )
        router = SemanticRouter(FakeLLM([llm_output]), registry, scope)

        decision = await router.route(
            message="Activa el protocolo de borrado total de memoria",
            history=[],
        )

        self.assertEqual(decision.intent, "memory_purge")
        self.assertIn("memory_purge_all", decision.candidate_tools)

    def test_extract_memory_facts_fallback_from_numbered_list(self):
        text = (
            "guarda en tu memoria: mejoras a implementar:\n"
            "1. protocolo de borrado de memoria\n"
            "2. mejora de interfaz"
        )
        facts = extract_memory_facts_fallback(text)
        self.assertEqual(len(facts), 2)
        self.assertIn("protocolo de borrado de memoria", facts[0].lower())
        self.assertIn("mejora de interfaz", facts[1].lower())


if __name__ == "__main__":
    unittest.main()
