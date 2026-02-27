import unittest

from app.reminder_semantic import extract_multi_reminder_plan, extract_reminder_action_plan


class FakeLLM:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    async def generate_response(self, messages, system_prompt):
        if self.outputs:
            return self.outputs.pop(0)
        return (
            '{"operation":"none","should_apply":false,"target_id":"","target_query":"",'
            '"task_text":"","datetime_text":"","delete_all":false,'
            '"confidence":0.1,"clarification_question":""}'
        )


class ReminderSemanticPlanTests(unittest.IsolatedAsyncioTestCase):
    async def test_extract_create_plan(self):
        llm_output = (
            '{"operation":"create","should_apply":true,'
            '"target_id":"","target_query":"",'
            '"task_text":"pagar la luz","datetime_text":"mañana a las 8",'
            '"delete_all":false,"confidence":0.91,"clarification_question":""}'
        )
        plan = await extract_reminder_action_plan(
            llm_engine=FakeLLM([llm_output]),
            message="No dejes que se me pase pagar la luz mañana a las 8",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.operation, "create")
        self.assertTrue(plan.should_apply)
        self.assertEqual(plan.task_text, "pagar la luz")
        self.assertIn("mañana", plan.datetime_text.lower())

    async def test_postpone_requires_target(self):
        llm_output = (
            '{"operation":"postpone","should_apply":true,'
            '"target_id":"","target_query":"",'
            '"task_text":"","datetime_text":"30 minutos",'
            '"delete_all":false,"confidence":0.75,"clarification_question":""}'
        )
        plan = await extract_reminder_action_plan(
            llm_engine=FakeLLM([llm_output]),
            message="posponlo 30 minutos",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.operation, "postpone")
        self.assertFalse(plan.should_apply)
        self.assertIn("Qué recordatorio", plan.clarification_question)

    async def test_create_without_datetime_and_without_temporal_reference_is_ambiguous(self):
        llm_output = (
            '{"operation":"create","should_apply":true,'
            '"target_id":"","target_query":"",'
            '"task_text":"mejorar la interfaz","datetime_text":"",'
            '"delete_all":false,"confidence":0.78,"clarification_question":""}'
        )
        plan = await extract_reminder_action_plan(
            llm_engine=FakeLLM([llm_output]),
            message="Recuérdame mejorar la interfaz del producto",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.operation, "create")
        self.assertFalse(plan.should_apply)
        self.assertIn("memoria", plan.clarification_question.lower())

    async def test_create_without_datetime_keeps_apply_if_message_has_temporal_reference(self):
        llm_output = (
            '{"operation":"create","should_apply":true,'
            '"target_id":"","target_query":"",'
            '"task_text":"pagar la luz","datetime_text":"",'
            '"delete_all":false,"confidence":0.82,"clarification_question":""}'
        )
        plan = await extract_reminder_action_plan(
            llm_engine=FakeLLM([llm_output]),
            message="Recuérdame pagar la luz mañana",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertEqual(plan.operation, "create")
        self.assertTrue(plan.should_apply)

    async def test_extract_multi_reminders_plan(self):
        llm_output = (
            '{'
            '"should_apply":true,'
            '"reminders":['
            '{"task_text":"tomar cafe","datetime_text":"en 10 minutos"},'
            '{"task_text":"ir a caminar","datetime_text":"a las 16:30"}'
            '],'
            '"confidence":0.93,'
            '"clarification_question":""'
            '}'
        )
        plan = await extract_multi_reminder_plan(
            llm_engine=FakeLLM([llm_output]),
            message="Crea un recordatorio para tomar cafe en 10 minutos y otro para ir a caminar a las 16:30",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertTrue(plan.should_apply)
        self.assertEqual(len(plan.reminders), 2)
        self.assertEqual(plan.reminders[0].task_text, "tomar cafe")

    async def test_extract_multi_reminders_needs_clarification_when_only_one_has_datetime(self):
        llm_output = (
            '{'
            '"should_apply":true,'
            '"reminders":['
            '{"task_text":"tomar cafe","datetime_text":"en 10 minutos"},'
            '{"task_text":"caminar","datetime_text":""}'
            '],'
            '"confidence":0.75,'
            '"clarification_question":""'
            '}'
        )
        plan = await extract_multi_reminder_plan(
            llm_engine=FakeLLM([llm_output]),
            message="Crea un recordatorio para tomar cafe en 10 minutos y otro para caminar",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertFalse(plan.should_apply)
        self.assertIn("varios recordatorios", plan.clarification_question.lower())

    async def test_extract_multi_reminders_detects_y_para_pattern(self):
        llm_output = (
            '{'
            '"should_apply":true,'
            '"reminders":['
            '{"task_text":"ir a tomar cafe","datetime_text":"en 5 minutos"},'
            '{"task_text":"salir a caminar","datetime_text":"a las 18"}'
            '],'
            '"confidence":0.91,'
            '"clarification_question":""'
            '}'
        )
        plan = await extract_multi_reminder_plan(
            llm_engine=FakeLLM([llm_output]),
            message="activa un recordatorio para ir a tomar cafe en 5 minutos y para salir a caminar a las 18",
            history=[],
        )

        self.assertIsNotNone(plan)
        assert plan is not None
        self.assertTrue(plan.should_apply)
        self.assertEqual(len(plan.reminders), 2)


if __name__ == "__main__":
    unittest.main()
