import unittest

from app.capability_registry import CapabilityRegistry
from app.product_scope import ProductScope


class Phase1ScopeRegistryTests(unittest.TestCase):
    def test_scope_has_core_capabilities(self):
        scope = ProductScope()
        self.assertGreaterEqual(len(scope.capabilities), 10)
        self.assertIn("chat_general", scope.capabilities)
        self.assertIn("web_search_general", scope.capabilities)
        self.assertIn("get_current_datetime", scope.capabilities)

    def test_registry_and_scope_are_consistent(self):
        scope = ProductScope()
        registry = CapabilityRegistry(product_scope=scope)
        missing_in_registry, _ = registry.ensure_scope_consistency()
        self.assertEqual(missing_in_registry, set())
        self.assertIsNotNone(registry.get("web_search_general"))


if __name__ == "__main__":
    unittest.main()
