"""Tests for Milestone 10: model-driven planning with rules fallback.

A canned planning-capable client stands in for the live model so the
planning, validation, and fallback logic is proven offline and
deterministically. The live probe proves the real model plans.

Run from the project folder: python -m unittest
"""

import json
import unittest
from datetime import datetime, timezone

import central
from model_client import ModelClient, ModelError, ModelResponse, StubProvider


class _CannedClient(ModelClient):
    name = "canned-1"
    supports_planning = True

    def __init__(self, text):
        self._text = text

    def generate(self, *, system, messages, response_format=None, max_output_tokens=None):
        self.seen_format = response_format
        self.seen_cap = max_output_tokens
        return ModelResponse(
            text=self._text, model=self.name, input_chars=1, output_chars=len(self._text),
            input_tokens=7, output_tokens=9,
        )


class _FailingClient(ModelClient):
    name = "failing-1"
    supports_planning = True

    def generate(self, *, system, messages, response_format=None, max_output_tokens=None):
        raise ModelError("boom")


class PlanningTest(unittest.TestCase):
    def _now(self):
        return datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)

    def test_valid_plan_parsed(self):
        canned = _CannedClient(
            json.dumps(
                {
                    "assignments": [
                        {"agent": "reminder", "objective": "call mother", "deadline": "2026-11-05T09:00:00+00:00"},
                        {"agent": "daily-organiser", "objective": "", "deadline": ""},
                    ],
                    "notes": ["heads up"],
                }
            )
        )
        plan = central._route("whatever", now=self._now(), client=canned)
        self.assertEqual(
            plan["assignments"],
            [
                {"agent": "reminder", "objective": "call mother", "deadline": "2026-11-05T09:00:00+00:00"},
                {"agent": "daily-organiser"},
            ],
        )
        self.assertEqual(plan["notes"], ["heads up"])
        self.assertEqual(plan["planned_by"], "canned-1")
        self.assertEqual(plan["plan_tokens"], (7, 9))
        self.assertEqual(canned.seen_format, central.PLAN_FORMAT)
        self.assertEqual(canned.seen_cap, central.PLAN_MAX_OUTPUT_TOKENS)

    def test_invalid_items_dropped_with_notes(self):
        canned = _CannedClient(
            json.dumps(
                {
                    "assignments": [
                        {"agent": "teleporter", "objective": "x", "deadline": "y"},
                        {"agent": "reminder", "objective": "no time", "deadline": ""},
                        {"agent": "reminder", "objective": "bad time", "deadline": "someday"},
                        42,
                        {"agent": "daily-organiser", "objective": "", "deadline": ""},
                    ],
                    "notes": [],
                }
            )
        )
        plan = central._route("whatever", now=self._now(), client=canned)
        self.assertEqual(plan["assignments"], [{"agent": "daily-organiser"}])
        self.assertEqual(len(plan["notes"]), 4)

    def test_deadline_kept_verbatim(self):
        canned = _CannedClient(
            json.dumps(
                {
                    "assignments": [
                        {"agent": "reminder", "objective": "call", "deadline": "2026-11-05T09:00:00+05:30"}
                    ],
                    "notes": [],
                }
            )
        )
        plan = central._route("whatever", now=self._now(), client=canned)
        self.assertEqual(plan["assignments"][0]["deadline"], "2026-11-05T09:00:00+05:30")

    def test_non_json_falls_back_to_rules(self):
        text = "remind me to drink water on 2999-01-01T09:00:00+00:00"
        plan = central._route(text, now=self._now(), client=_CannedClient("sorry, no json"))
        rules = central._route_rules(text, now=self._now())
        self.assertEqual(plan["assignments"], rules["assignments"])
        self.assertEqual(plan["planned_by"], "rules-fallback")

    def test_model_error_falls_back_to_rules(self):
        text = "give me my briefing"
        plan = central._route(text, now=self._now(), client=_FailingClient())
        rules = central._route_rules(text, now=self._now())
        self.assertEqual(plan["assignments"], rules["assignments"])
        self.assertEqual(plan["planned_by"], "rules-fallback")

    def test_empty_plan_means_no_assignments(self):
        canned = _CannedClient(json.dumps({"assignments": [], "notes": []}))
        plan = central._route("whatever", now=self._now(), client=canned)
        self.assertEqual(plan["assignments"], [])
        self.assertEqual(plan["planned_by"], "canned-1")

    def test_stub_client_uses_rules(self):
        text = "remind me to drink water on 2999-01-01T09:00:00+00:00"
        plan = central._route(text, now=self._now(), client=StubProvider())
        rules = central._route_rules(text, now=self._now())
        self.assertEqual(plan["assignments"], rules["assignments"])
        self.assertEqual(plan["planned_by"], "rules")


class ToolPlanTest(unittest.TestCase):
    """Chat tool lookups: harmless tools accepted, everything else dropped."""

    def _plan(self, item):
        return central._validate_plan({"assignments": [item], "notes": []})

    def test_valid_places_lookup_accepted(self):
        item = {
            "agent": "tool",
            "objective": "maps_search_places",
            "deadline": "",
            "params": '{"query": "dentist", "near": "Andheri West"}',
        }
        assignments, notes = self._plan(item)
        self.assertEqual(
            assignments,
            [{"agent": "tool", "objective": "maps_search_places", "params": {"query": "dentist", "near": "Andheri West"}}],
        )
        self.assertEqual(notes, [])

    def test_calendar_day_defaults_timezone_to_utc(self):
        item = {
            "agent": "tool",
            "objective": "calendar_read_day",
            "deadline": "",
            "params": '{"date": "2026-11-07"}',
        }
        assignments, _ = self._plan(item)
        self.assertEqual(assignments[0]["params"], {"date": "2026-11-07", "time_zone": "UTC"})

    def test_consequential_tools_dropped(self):
        for name in ("calendar_add_event", "gmail_send", "export_calendar", "demo_book_cab"):
            item = {"agent": "tool", "objective": name, "deadline": "", "params": "{}"}
            assignments, notes = self._plan(item)
            self.assertEqual(assignments, [])
            self.assertTrue(any("not available via chat" in note for note in notes), name)

    def test_unknown_tool_dropped(self):
        item = {"agent": "tool", "objective": "teleport", "deadline": "", "params": "{}"}
        assignments, notes = self._plan(item)
        self.assertEqual(assignments, [])
        self.assertTrue(any("not available via chat" in note for note in notes))

    def test_bad_params_dropped(self):
        bad = [
            '{"query": "x", "smuggle": "y"}',
            '{"query": 42}',
            '{"query": "' + "x" * 201 + '"}',
            "not json",
            '["an", "array"]',
        ]
        for params in bad:
            item = {"agent": "tool", "objective": "maps_search_places", "deadline": "", "params": params}
            assignments, notes = self._plan(item)
            self.assertEqual(assignments, [], params)
            self.assertEqual(len(notes), 1, params)

    def test_missing_params_tolerated(self):
        item = {"agent": "tool", "objective": "task_stats", "deadline": ""}
        assignments, _ = self._plan(item)
        self.assertEqual(assignments, [{"agent": "tool", "objective": "task_stats", "params": {}}])


if __name__ == "__main__":
    unittest.main()
