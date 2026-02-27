import unittest

from app.memory_protocol import (
    MEMORY_PURGE_ACTIVATION_PHRASE,
    MEMORY_PURGE_CONFIRMATION_WORD,
    is_memory_purge_activation_command,
    is_memory_purge_cancel_word,
    is_memory_purge_confirmation_word,
    is_protocols_overview_query,
)


class MemoryProtocolGrammarTests(unittest.TestCase):
    def test_activation_command_is_strict(self):
        self.assertTrue(is_memory_purge_activation_command(MEMORY_PURGE_ACTIVATION_PHRASE))
        self.assertTrue(is_memory_purge_activation_command("  ACTIVA EL PROTOCOLO DE BORRADO TOTAL DE MEMORIA  "))
        self.assertFalse(is_memory_purge_activation_command("activa protocolo de borrado total de memoria"))
        self.assertFalse(is_memory_purge_activation_command("activa protocolo borrado total"))
        self.assertFalse(is_memory_purge_activation_command("inicia borrado total de memoria"))

    def test_confirmation_word_is_single_word(self):
        self.assertTrue(is_memory_purge_confirmation_word(MEMORY_PURGE_CONFIRMATION_WORD))
        self.assertTrue(is_memory_purge_confirmation_word("CONFIRMAR"))
        self.assertFalse(is_memory_purge_confirmation_word("confirmo borrado total"))
        self.assertFalse(is_memory_purge_confirmation_word("si"))

    def test_cancel_word(self):
        self.assertTrue(is_memory_purge_cancel_word("cancelar"))
        self.assertFalse(is_memory_purge_cancel_word("cancela por favor"))

    def test_protocols_overview_query(self):
        self.assertTrue(is_protocols_overview_query("Que protocolos tienes?"))
        self.assertTrue(is_protocols_overview_query("Tienes un protocolo de borrado?"))
        self.assertFalse(is_protocols_overview_query(MEMORY_PURGE_ACTIVATION_PHRASE))


if __name__ == "__main__":
    unittest.main()
