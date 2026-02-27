import unittest

from app import main as app_main


class _FakeReminderManager:
    def __init__(self, active):
        self._active = list(active)

    def get_active_reminders(self):
        return list(self._active)

    def format_datetime_for_user(self, dt_str: str) -> str:
        return dt_str

    def get_active_reminder_by_id(self, reminder_id: str):
        for reminder in self._active:
            if reminder.get("id") == reminder_id:
                return reminder
        return None

    def find_active_reminders_by_text(self, query: str):
        return [r for r in self._active if query.lower() in str(r.get("text", "")).lower()]


class ReminderTargetResolutionTests(unittest.TestCase):
    def setUp(self):
        self._original_manager = app_main.reminder_manager

    def tearDown(self):
        app_main.reminder_manager = self._original_manager

    def test_single_active_reminder_is_selected_without_id_or_query(self):
        app_main.reminder_manager = _FakeReminderManager(
            [
                {
                    "id": "abc12345",
                    "text": "pagar luz",
                    "datetime": "2026-02-27T20:00:00",
                    "status": "active",
                }
            ]
        )

        reminder, error = app_main._resolve_single_reminder_target()
        self.assertIsNotNone(reminder)
        self.assertIsNone(error)
        assert reminder is not None
        self.assertEqual(reminder["id"], "abc12345")

    def test_multiple_active_reminders_require_explicit_id(self):
        app_main.reminder_manager = _FakeReminderManager(
            [
                {
                    "id": "abc12345",
                    "text": "pagar luz",
                    "datetime": "2026-02-27T20:00:00",
                    "status": "active",
                },
                {
                    "id": "def67890",
                    "text": "comprar cafe",
                    "datetime": "2026-02-27T21:00:00",
                    "status": "active",
                },
            ]
        )

        reminder, error = app_main._resolve_single_reminder_target()
        self.assertIsNone(reminder)
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("ID exacto", error)


if __name__ == "__main__":
    unittest.main()
