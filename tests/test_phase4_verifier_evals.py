import unittest

from app.evals import EvalTrace, phase_gate, summarize_metrics
from app.response_verifier import verify_response


class DummyRoute:
    def __init__(self, intent, temporal):
        self.intent = intent
        self.entities = {"temporal_reference": temporal}


class Phase4VerifierEvalsTests(unittest.TestCase):
    def test_verifier_injects_absolute_date_for_temporal_response(self):
        route = DummyRoute(intent="time_sensitive_answer", temporal=True)
        result = verify_response(
            response_text="Hoy podrias hacerlo en la tarde.",
            route=route,
            web_results=[],
            datetime_payload={"date": "2026-02-27"},
        )
        self.assertIn("2026-02-27", result.response)

    def test_verifier_removes_source_claim_when_no_results(self):
        route = DummyRoute(intent="general_chat", temporal=False)
        result = verify_response(
            response_text="Respuesta base.\nFuentes:\n- ejemplo.com",
            route=route,
            web_results=[],
            datetime_payload={},
        )
        self.assertNotIn("Fuentes", result.response)

    def test_eval_metrics_and_phase_gate(self):
        traces = [
            EvalTrace(phase=4, case_id="ok_1", tool_requested=True, tool_success=True, critical_failure=False),
            EvalTrace(phase=4, case_id="ok_2", tool_requested=True, tool_success=True, critical_failure=False),
            EvalTrace(phase=4, case_id="ok_3", tool_requested=False, tool_success=False, critical_failure=False),
        ]
        metrics = summarize_metrics(traces)
        passed, reasons = phase_gate(metrics, observed_days=14)

        self.assertTrue(passed)
        self.assertEqual(reasons, [])


if __name__ == "__main__":
    unittest.main()
