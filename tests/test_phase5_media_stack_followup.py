import unittest

from app.media_stack import (
    looks_like_media_stack_followup_start_request,
    looks_like_media_stack_followup_stop_request,
    looks_like_media_stack_start_request,
)


class Phase5MediaStackFollowupTests(unittest.TestCase):
    def test_followup_start_requires_recent_media_context(self):
        recent = [
            "Protocolo peliculas apagado.\nEstado: Radarr:OFF | Prowlarr:OFF | Transmission:OFF | Jellyfin:OFF",
            "estado del protocolo peliculas",
        ]
        self.assertTrue(looks_like_media_stack_followup_start_request("activalo", recent))
        self.assertTrue(looks_like_media_stack_followup_start_request("Actívalo.", recent))

    def test_followup_start_without_context_is_ignored(self):
        recent = ["recordatorio creado para mañana", "gracias"]
        self.assertFalse(looks_like_media_stack_followup_start_request("activalo", recent))
        self.assertFalse(looks_like_media_stack_followup_start_request("activa", recent))

    def test_followup_stop_requires_recent_media_context(self):
        recent = [
            "Protocolo peliculas activo en segundo plano.\nEstado: Radarr:OK | Prowlarr:OK | Transmission:OK | Jellyfin:OK"
        ]
        self.assertTrue(looks_like_media_stack_followup_stop_request("apágalo", recent))
        self.assertTrue(looks_like_media_stack_followup_stop_request("detenlo", recent))

    def test_explicit_scope_start_still_supported(self):
        self.assertTrue(looks_like_media_stack_start_request("activa el protocolo peliculas"))


if __name__ == "__main__":
    unittest.main()
