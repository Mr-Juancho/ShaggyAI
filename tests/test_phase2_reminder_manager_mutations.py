import unittest
from datetime import datetime, timedelta

try:
    from app.reminders import ReminderManager
except Exception:  # pragma: no cover - entorno sin dependencias opcionales
    ReminderManager = None  # type: ignore[assignment]


@unittest.skipIf(ReminderManager is None, "ReminderManager/dateparser no disponible en este entorno")
class ReminderManagerMutationTests(unittest.TestCase):
    def setUp(self):
        self.manager = ReminderManager()
        self.manager._save_reminders = lambda: None  # type: ignore[assignment]
        base_dt = datetime.now() + timedelta(hours=2)
        self.manager.reminders = [
            {
                "id": "abc12345",
                "text": "tomar cafe",
                "datetime": base_dt.isoformat(),
                "recurring": False,
                "interval": None,
                "status": "active",
            }
        ]

    def test_update_reminder_changes_text_and_datetime(self):
        updated = self.manager.update_reminder(
            reminder_id="abc12345",
            new_text="tomar te",
            new_datetime_text="en 45 minutos",
        )
        self.assertEqual(updated["id"], "abc12345")
        self.assertIn("te", updated["text"].lower())
        self.assertGreater(datetime.fromisoformat(updated["datetime"]), datetime.now())

    def test_postpone_reminder_relative(self):
        before = datetime.fromisoformat(self.manager.reminders[0]["datetime"])
        updated = self.manager.postpone_reminder("abc12345", "en 30 minutos")
        after = datetime.fromisoformat(updated["datetime"])
        self.assertGreater(after, before)

    def test_create_reminder_parses_time_only_as_hour_not_day_number(self):
        created = self.manager.create_reminder(
            text="ir a caminar",
            dt="a las 16",
        )
        parsed = datetime.fromisoformat(created["datetime"])
        self.assertEqual(parsed.hour, 16)
        self.assertEqual(parsed.minute, 0)
        self.assertGreater(parsed, datetime.now())

    def test_create_reminder_rolls_forward_if_parser_returns_old_past_date(self):
        created = self.manager.create_reminder(
            text="revisar reporte",
            dt="16/02/2026",
        )
        parsed = datetime.fromisoformat(created["datetime"])
        self.assertGreater(parsed, datetime.now())

    def test_extract_multiple_reminder_drafts_from_single_sentence(self):
        drafts = self.manager.extract_multiple_reminder_drafts(
            "activa un recordatorio para ir a tomar cafe en 5 minutos y para salir a caminar a las 18"
        )
        self.assertGreaterEqual(len(drafts), 2)
        self.assertTrue(any("cafe" in item["text"].lower() for item in drafts))
        self.assertTrue(any("caminar" in item["text"].lower() for item in drafts))

    def test_extract_multiple_reminder_drafts_with_semicolon_and_morning_hour(self):
        drafts = self.manager.extract_multiple_reminder_drafts(
            "crea recordatorio para pagar luz mañana a las 8; llamar a mamá en 2 horas; caminar a las 19"
        )
        self.assertGreaterEqual(len(drafts), 3)
        self.assertTrue(any("pagar luz" in item["text"].lower() for item in drafts))
        self.assertTrue(any("llamar a mamá" in item["text"].lower() for item in drafts))
        self.assertTrue(any("caminar" in item["text"].lower() for item in drafts))


if __name__ == "__main__":
    unittest.main()
