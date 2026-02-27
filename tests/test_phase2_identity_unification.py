import unittest

from app.main import CANONICAL_USER_ID, resolve_user_identity


class IdentityUnificationTests(unittest.TestCase):
    def test_desktop_source_maps_to_canonical_identity(self):
        resolved = resolve_user_identity(user_id="desktop_user", source="desktop")
        self.assertEqual(resolved, CANONICAL_USER_ID)

    def test_telegram_source_maps_to_canonical_identity(self):
        resolved = resolve_user_identity(user_id="5969223083", source="telegram")
        self.assertEqual(resolved, CANONICAL_USER_ID)

    def test_api_custom_user_id_is_preserved(self):
        resolved = resolve_user_identity(user_id="cliente_api_1", source="api")
        self.assertEqual(resolved, "cliente_api_1")


if __name__ == "__main__":
    unittest.main()
