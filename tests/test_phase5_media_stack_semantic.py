import unittest

from app.media_stack import (
    infer_media_stack_action_semantic,
    looks_like_media_stack_semantic_candidate,
)


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def generate_response(self, messages, system_prompt):
        if self.outputs:
            return self.outputs.pop(0)
        return '{"action":"none","confidence":0.0,"rationale":""}'


class Phase5MediaStackSemanticTests(unittest.IsolatedAsyncioTestCase):
    def test_semantic_candidate_detects_stack_intent_without_exact_words(self):
        self.assertTrue(
            looks_like_media_stack_semantic_candidate(
                "pon en marcha el stack multimedia",
                recent_messages=[],
            )
        )

    def test_semantic_candidate_avoids_false_positive_movie_request(self):
        self.assertFalse(
            looks_like_media_stack_semantic_candidate(
                "quiero ver la pelicula Inception",
                recent_messages=[],
            )
        )

    def test_semantic_candidate_uses_recent_context_for_short_followup(self):
        recent = ["Protocolo peliculas apagado. Estado: Radarr:OFF | Prowlarr:OFF"]
        self.assertTrue(looks_like_media_stack_semantic_candidate("hazlo", recent))

    def test_semantic_candidate_detects_quiero_activarlo_with_context(self):
        recent = ["estado del protocolo peliculas", "Protocolo peliculas apagado."]
        self.assertTrue(looks_like_media_stack_semantic_candidate("quiero activarlo", recent))

    async def test_semantic_classifier_maps_followup_to_start(self):
        llm = FakeLLM(['{"action":"start","confidence":0.88,"rationale":"follow-up"}'])
        recent = ["estado del protocolo peliculas", "Protocolo peliculas apagado."]
        decision = await infer_media_stack_action_semantic(
            message="ponlo en marcha",
            recent_messages=recent,
            llm_engine=llm,
            min_confidence=0.62,
        )
        self.assertEqual(decision.action, "start")
        self.assertGreaterEqual(decision.confidence, 0.8)

    async def test_semantic_classifier_rejects_low_confidence(self):
        llm = FakeLLM(['{"action":"stop","confidence":0.40,"rationale":"duda"}'])
        recent = ["Protocolo peliculas activo en segundo plano."]
        decision = await infer_media_stack_action_semantic(
            message="detenlo",
            recent_messages=recent,
            llm_engine=llm,
            min_confidence=0.62,
        )
        self.assertEqual(decision.action, "none")


if __name__ == "__main__":
    unittest.main()
