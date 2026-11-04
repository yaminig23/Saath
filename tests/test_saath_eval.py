"""Saath evaluation suite (production-grade companion mission).

Fifteen behaviour categories exercised deterministically over a
throwaway database with scripted model clients - no network, no
quota. Each test asserts an observable outcome (stored rows, replies,
captured prompts), never the model's word for it. Categories needing
live-model quality judgement are marked LIVE-ONLY in
docs/saath/eval-baseline.md and stay out of this suite.

Run from the project folder: python -m unittest tests.test_saath_eval
"""

import json
import os
import tempfile
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import central
import kernel
import manager
import memory
import models
import threads
from model_client import ModelResponse


PLAN_JSON = json.dumps({"assignments": [], "notes": []})


class DispatchClient:
    """One scripted client for planner + direct paths.

    Calls carrying response_format get the plan; plain calls get the
    direct answer. Every call is captured for prompt assertions.
    """

    name = "eval-scripted"
    supports_planning = True
    supports_vision = False

    def __init__(self, *, plan_text=PLAN_JSON, direct_text="[eval] direct answer"):
        self.plan_text = plan_text
        self.direct_text = direct_text
        self.seen = []

    def generate(self, *, system="", messages=None, response_format=None, max_output_tokens=None):
        self.seen.append(
            {
                "system": system,
                "messages": list(messages or []),
                "response_format": response_format,
                "max_output_tokens": max_output_tokens,
            }
        )
        if response_format is not None:
            text = self.plan_text
        else:
            text = self.direct_text
        return ModelResponse(text=text, model=self.name, input_chars=10, output_chars=len(text))


class RulesClient:
    """Offline client: forces the rule-based router backstop, then
    answers directly with canned text like the real stub."""

    name = "eval-rules"
    supports_planning = False
    supports_vision = False

    def __init__(self):
        self.seen = []

    def generate(self, *, system="", messages=None, response_format=None, max_output_tokens=None):
        assert response_format is None, "rules client never plans"
        self.seen.append({"system": system, "messages": list(messages or [])})
        text = "[eval-rules] offline direct answer"
        return ModelResponse(text=text, model=self.name, input_chars=10, output_chars=len(text))


class SaathEvalTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self._real_client = central.get_model_client

    def tearDown(self):
        central.get_model_client = self._real_client
        self.session.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _use(self, client):
        central.get_model_client = lambda: client
        return client

    def _tasks_by_agent(self, agent):
        return self.session.query(models.Task).filter_by(user_id=1, agent=agent).all()

    # 1. Simple factual questions.
    def test_01_simple_question_answered_and_audited(self):
        client = self._use(DispatchClient(direct_text="Paris is the capital of France."))
        result = central.chat(self.session, user_id=1, message="What is the capital of France?")
        self.assertEqual(result["reply"], "Paris is the capital of France.")
        task = kernel.get_task(self.session, 1, result["task_id"])
        self.assertEqual(task.status, "COMPLETED")
        self.assertIn("input_tokens=", task.evidence)
        self.assertIn("output_tokens=", task.evidence)
        self.assertEqual(client.seen[0]["response_format"]["name"], "chat_plan")

    # 2. Ambiguous natural-language requests.
    def test_02_ambiguous_reminder_never_faked(self):
        client = self._use(RulesClient())
        result = central.chat(self.session, user_id=1, message="remind me")
        self.assertEqual(self._tasks_by_agent("reminder"), [])
        direct_input = client.seen[0]["messages"][-1]["content"]
        self.assertIn("Router notes", direct_input)
        self.assertIn("need a time", direct_input)
        self.assertIn("[eval-rules]", result["reply"])

    # 3. Multi-step task planning.
    def test_03_multi_step_plan_delegates_and_verifies(self):
        plan = json.dumps(
            {
                "assignments": [
                    {
                        "agent": "reminder",
                        "objective": "call mom",
                        "deadline": "2026-11-05T09:00:00+00:00",
                    },
                    {"agent": "daily-organiser", "objective": "", "deadline": ""},
                ],
                "notes": [],
            }
        )
        self._use(DispatchClient(plan_text=plan))
        result = central.chat(self.session, user_id=1, message="remind me to call mom tomorrow at 9am and brief me")
        self.assertIn("Reminder set", result["reply"])
        self.assertIn("Briefing ready", result["reply"])
        self.assertEqual(len(result["children"]), 1)
        stored = kernel.get_task(self.session, 1, result["task_id"])
        self.assertEqual(stored.result, result["reply"])

    # 4. Conversation continuity.
    def test_04_planner_receives_recent_turns(self):
        client = self._use(DispatchClient())
        thread = threads.create_thread(self.session, user_id=1, title="eval")
        threads.append_message(self.session, user_id=1, thread_id=thread["id"], role="user", text="I have a dentist visit Friday.")
        threads.append_message(self.session, user_id=1, thread_id=thread["id"], role="assistant", text="Noted.")
        result = central.chat(self.session, user_id=1, message="remind me the evening before", thread_id=thread["id"])
        planner_input = client.seen[0]["messages"][0]["content"]
        self.assertIn("Recent conversation", planner_input)
        self.assertIn("dentist visit", planner_input)
        self.assertEqual(result["thread_id"], thread["id"])

    # 5. User corrections and interruptions.
    def test_05_correction_replans_with_context(self):
        client = self._use(DispatchClient())
        thread = threads.create_thread(self.session, user_id=1, title="eval")
        threads.append_message(self.session, user_id=1, thread_id=thread["id"], role="user", text="remind me to call mom tomorrow at 9am")
        threads.append_message(self.session, user_id=1, thread_id=thread["id"], role="assistant", text="Reminder set.")
        central.chat(self.session, user_id=1, message="change it to 10am", thread_id=thread["id"])
        planner_input = client.seen[0]["messages"][0]["content"]
        self.assertIn("call mom tomorrow at 9am", planner_input)
        self.assertIn("change it to 10am", planner_input)

    # 6. Memory retrieval and consent.
    def test_06_sensitive_memory_needs_consent(self):
        with self.assertRaises(kernel.KernelError):
            memory.store_memory(
                self.session, user_id=1, layer="user", category="health", key="condition", value="x"
            )
        memory.set_consent(self.session, user_id=1, category="health", granted=True)
        stored = memory.store_memory(
            self.session, user_id=1, layer="user", category="health", key="condition", value="x"
        )
        self.assertEqual(stored["key"], "condition")

    # 7. Specialist selection.
    def test_07_unknown_specialist_dropped_with_note(self):
        raw = {
            "assignments": [
                {"agent": "teleporter", "objective": "beam me up", "deadline": "x"},
                {"agent": "reminder", "objective": "", "deadline": ""},
            ],
            "notes": [],
        }
        assignments, notes = central._validate_plan(raw)
        self.assertEqual(assignments, [])
        self.assertEqual(len(notes), 2)

    # 8. Tool selection and execution.
    def test_08_rules_route_briefing_and_unknown(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        briefing = central._route_rules("give me my briefing", now=now)
        self.assertEqual(briefing["assignments"], [{"agent": "daily-organiser"}])
        unknown = central._route_rules("xyzzy frobnicate", now=now)
        self.assertEqual(unknown["assignments"], [])
        self.assertTrue(unknown["notes"])

    # 9. Failed tool recovery.
    def test_09_failed_assignment_reported_not_raised(self):
        outcome = manager.handle_department(
            self.session, user_id=1, assignments=[{"agent": "teleporter"}]
        )
        self.assertFalse(outcome["ok"])
        self.assertIn("failed", outcome["reply"])
        row = kernel.get_task(self.session, 1, outcome["task_id"])
        self.assertEqual(row.status, "COMPLETED")
        self.assertEqual(row.result, outcome["reply"])

    # 10. Conflicting instructions.
    def test_10_cancellation_never_claimed(self):
        client = self._use(RulesClient())
        result = central.chat(self.session, user_id=1, message="cancel that reminder")
        lowered = result["reply"].lower()
        self.assertNotIn("cancelled", lowered)
        self.assertNotIn("canceled", lowered)
        direct_input = client.seen[0]["messages"][-1]["content"]
        self.assertIn("need a time", direct_input)

    # 11. Safety and approval enforcement.
    def test_11_planner_cannot_emit_consequential_tools(self):
        prompt = central.PLAN_PROMPT
        for tool in ("gmail_send", "calendar_add_event", "demo_book_cab", "export_calendar"):
            self.assertNotIn(tool, prompt)
        self.assertIn("NOT available via chat", prompt)
        agents = central.PLAN_SCHEMA["properties"]["assignments"]["items"]["properties"]["agent"]["enum"]
        self.assertEqual(sorted(agents), ["daily-organiser", "reminder", "tool"])
        for name in ("gmail_send", "calendar_add_event", "demo_book_cab", "export_calendar"):
            raw = {
                "assignments": [{"agent": "tool", "objective": name, "deadline": "", "params": "{}"}],
                "notes": [],
            }
            assignments, notes = central._validate_plan(raw)
            self.assertEqual(assignments, [], name)
            self.assertTrue(notes, name)

    # 8b. Tool selection and execution, end to end through chat.
    def test_08b_chat_tool_lookup_answers_from_stored_result(self):
        plan = json.dumps(
            {
                "assignments": [{"agent": "tool", "objective": "task_stats", "deadline": "", "params": "{}"}],
                "notes": [],
            }
        )
        self._use(DispatchClient(plan_text=plan))
        result = central.chat(self.session, user_id=1, message="how many tasks do I have?")
        self.assertIn("Lookup done", result["reply"])
        self.assertIn("active task", result["reply"])
        stored = kernel.get_task(self.session, 1, result["task_id"])
        self.assertEqual(stored.result, result["reply"])
        self.assertEqual(len(result["children"]), 1)

    def test_08c_chat_tool_failure_reported_honestly(self):
        import maps
        import tools
        from config import settings

        self.assertIn("maps_search_places", tools.TOOLS)
        old_key = settings.maps_api_key
        settings.maps_api_key = ""
        self.addCleanup(setattr, settings, "maps_api_key", old_key)
        plan = json.dumps(
            {
                "assignments": [
                    {
                        "agent": "tool",
                        "objective": "maps_search_places",
                        "deadline": "",
                        "params": '{"query": "dentist"}',
                    }
                ],
                "notes": [],
            }
        )
        self._use(DispatchClient(plan_text=plan))
        result = central.chat(self.session, user_id=1, message="find me a dentist")
        self.assertIn("failed", result["reply"])
        self.assertNotIn("Live places", result["reply"])

    # 12. Healthcare boundary enforcement.
    def test_12_medical_boundary_in_system_prompt(self):
        prompt = central.SYSTEM_PROMPT.lower()
        for phrase in ("never diagnose", "prescri", "dosage", "healthcare professional"):
            self.assertIn(phrase, prompt)

    # 13. Concise response generation.
    def test_13_direct_answers_carry_a_token_budget(self):
        client = self._use(DispatchClient())
        central.chat(self.session, user_id=1, message="hello there")
        direct_calls = [call for call in client.seen if call["response_format"] is None]
        self.assertEqual(len(direct_calls), 1)
        budget = direct_calls[0]["max_output_tokens"]
        self.assertIsInstance(budget, int)
        self.assertGreater(budget, 0)

    # 14. Multilingual input handling.
    def test_14_hindi_preference_reaches_the_model(self):
        memory.store_memory(
            self.session, user_id=1, layer="user", category="preferences", key="language", value="hindi"
        )
        client = self._use(DispatchClient(direct_text="[eval] namaste"))
        result = central.chat(self.session, user_id=1, message="namaste")
        self.assertEqual(result["reply"], "[eval] namaste")
        direct_calls = [call for call in client.seen if call["response_format"] is None]
        self.assertIn("Hindi", direct_calls[0]["system"])

    # 16. Trace IDs tie one turn's evidence together.
    def test_16_trace_id_links_parent_manager_and_child(self):
        plan = json.dumps(
            {
                "assignments": [{"agent": "tool", "objective": "task_stats", "deadline": "", "params": "{}"}],
                "notes": [],
            }
        )
        self._use(DispatchClient(plan_text=plan))
        result = central.chat(self.session, user_id=1, message="how many tasks do I have?")
        trace = result["trace_id"]
        self.assertEqual(len(trace), 12)
        parent = kernel.get_task(self.session, 1, result["task_id"])
        self.assertIn(f"trace={trace}", parent.evidence)
        manager_id = result["children"][0]
        manager_row = kernel.get_task(self.session, 1, manager_id)
        self.assertIn(f"trace={trace}", manager_row.evidence)

    def test_16b_direct_answer_carries_trace(self):
        self._use(DispatchClient(direct_text="hi"))
        result = central.chat(self.session, user_id=1, message="hello")
        task = kernel.get_task(self.session, 1, result["task_id"])
        self.assertIn(f"trace={result['trace_id']}", task.evidence)

    # 15. Hallucination and unsupported-claim detection.
    def test_15_garbage_plan_dropped_with_reasons(self):
        raw = {
            "assignments": [
                "not-a-dict",
                {"agent": "reminder", "objective": "x", "deadline": "someday-ish"},
                {"agent": "daily-organiser"},
            ],
            "notes": ["model note"],
        }
        assignments, notes = central._validate_plan(raw)
        self.assertEqual(assignments, [{"agent": "daily-organiser"}])
        self.assertEqual(len(notes), 3)


if __name__ == "__main__":
    unittest.main()
