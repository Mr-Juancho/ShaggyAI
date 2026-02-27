import unittest

from app.main import (
    _extract_explicit_memory_target,
    _looks_like_multi_reminder_request,
    _looks_like_reminder_creation_request,
)


class ReminderIntentTests(unittest.TestCase):
    def test_implicit_reminder_without_datetime_is_not_forced(self):
        message = (
            "Recuerdame que tenemos que mejorarte estas cosas: "
            "1) interfaz 2) recordatorios"
        )
        self.assertFalse(_looks_like_reminder_creation_request(message))

    def test_implicit_reminder_with_datetime_is_detected(self):
        self.assertTrue(
            _looks_like_reminder_creation_request(
                "Recuerdame tomar cafe en 10 minutos"
            )
        )

    def test_explicit_reminder_keyword_is_detected_even_without_datetime(self):
        self.assertTrue(
            _looks_like_reminder_creation_request(
                "Crea un recordatorio para pagar la luz"
            )
        )

    def test_reminder_list_query_is_not_creation(self):
        self.assertFalse(_looks_like_reminder_creation_request("Que recordatorios tengo activos?"))

    def test_memory_long_term_delete_phrase_is_not_reminder_creation(self):
        self.assertFalse(
            _looks_like_reminder_creation_request(
                "Elimina de la memoria a largo plazo el dato mejorar los recordatorios"
            )
        )

    def test_plural_recordatorios_with_datetime_context_is_not_creation(self):
        self.assertFalse(
            _looks_like_reminder_creation_request(
                "Hoy estuve pensando en mejorar los recordatorios del asistente"
            )
        )

    def test_extract_explicit_memory_target_prefers_quoted_text(self):
        message = 'Eliminar el recuerdo en conversaciones anteriores de "mejorar los recordatorios"'
        target = _extract_explicit_memory_target(message)
        self.assertEqual(target, "mejorar los recordatorios")

    def test_extract_explicit_memory_target_uses_token_with_digits(self):
        message = "Eliminar de memoria el item TOKENDEL99881"
        target = _extract_explicit_memory_target(message)
        self.assertEqual(target, "TOKENDEL99881")

    def test_detects_multi_reminder_with_y_para_pattern(self):
        self.assertTrue(
            _looks_like_multi_reminder_request(
                "activa un recordatorio para ir a tomar cafe en 5 minutos y para salir a caminar a las 18"
            )
        )


if __name__ == "__main__":
    unittest.main()
