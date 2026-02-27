import unittest

from pydantic import BaseModel

from app.capability_registry import CapabilityRegistry
from app.json_guard import generate_validated_json
from app.product_scope import ProductScope


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def generate_response(self, messages, system_prompt):
        if self.outputs:
            return self.outputs.pop(0)
        return "{}"


class ExampleSchema(BaseModel):
    intent: str
    confidence: float


class Phase3JsonPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def test_json_pipeline_repairs_markdown_and_trailing_comma(self):
        llm = FakeLLM(["```json\n{\"intent\":\"web_search\",\"confidence\":0.88,}\n```"])
        parsed, trace = await generate_validated_json(
            llm_engine=llm,
            system_prompt="Devuelve JSON",
            user_prompt="Clasifica",
            schema_model=ExampleSchema,
            max_retries=2,
        )

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.intent, "web_search")
        self.assertGreaterEqual(parsed.confidence, 0.88)
        self.assertGreaterEqual(len(trace.outputs), 1)

    def test_registry_resolve_chain_follows_fallback_order(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        chain = registry.resolve_chain("web_search_news")

        self.assertGreaterEqual(len(chain), 2)
        self.assertEqual(chain[0], "web_search_news")
        self.assertIn("web_search_general", chain)


if __name__ == "__main__":
    unittest.main()
