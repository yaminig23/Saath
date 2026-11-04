"""Tests for Milestone 9: the live model provider mapping.

Proves request building and response parsing against the documented
Responses API shapes, plus stub/live selection. No network is touched
here; the live round-trip is proven by probe with a real key.

Run from the project folder: python -m unittest
"""

import unittest

from config import settings
from model_client import ModelError, MuseProvider, StubProvider, _build_responses_body, _parse_responses_body, get_model_client

# Shape taken from the official quickstart's example response object.
SAMPLE_RESPONSE = {
    "id": "resp_abc123",
    "object": "response",
    "status": "completed",
    "model": "muse-spark-1.3",
    "output": [
        {"id": "rs_abc123", "type": "reasoning", "summary": [], "status": "completed"},
        {
            "id": "msg_abc123",
            "type": "message",
            "role": "assistant",
            "status": "completed",
            "content": [{"type": "output_text", "text": "The capital of France is Paris.", "annotations": []}],
        },
    ],
    "usage": {
        "input_tokens": 15,
        "input_tokens_details": {"cached_tokens": 0},
        "output_tokens": 258,
        "output_tokens_details": {"reasoning_tokens": 241},
        "total_tokens": 273,
    },
}


class ModelMappingTest(unittest.TestCase):
    def test_stub_reports_zero_tokens(self):
        response = StubProvider().generate(system="sys", messages=[{"role": "user", "content": "hi"}])
        self.assertEqual(response.model, "stub-1")
        self.assertIn("[stub]", response.text)
        self.assertEqual((response.input_tokens, response.output_tokens), (0, 0))

    def test_builds_single_turn_body(self):
        body = _build_responses_body(
            model="muse-spark-1.3", system="Be brief.", messages=[{"role": "user", "content": "Hi"}]
        )
        self.assertEqual(body, {"model": "muse-spark-1.3", "input": "Hi", "instructions": "Be brief."})

    def test_builds_body_without_system(self):
        body = _build_responses_body(model="muse-spark-1.3", system="  ", messages=[{"role": "user", "content": "Hi"}])
        self.assertEqual(body, {"model": "muse-spark-1.3", "input": "Hi"})

    def test_builds_multi_turn_body(self):
        body = _build_responses_body(
            model="muse-spark-1.3",
            system="sys",
            messages=[{"role": "user", "content": "one"}, {"role": "assistant", "content": "two"}],
        )
        self.assertEqual(
            body["input"],
            [
                {"type": "message", "role": "user", "content": [{"type": "input_text", "text": "one"}]},
                {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "two"}]},
            ],
        )
        self.assertEqual(body["instructions"], "sys")

    def test_multi_turn_order_and_roles_preserved(self):
        body = _build_responses_body(
            model="m",
            system="s",
            messages=[
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
        )
        roles = [item["role"] for item in body["input"]]
        texts = [item["content"][0]["text"] for item in body["input"]]
        self.assertEqual(roles, ["user", "assistant", "user"])
        self.assertEqual(texts, ["a", "b", "c"])

    def test_parses_response_text_and_usage(self):
        text, input_tokens, output_tokens = _parse_responses_body(SAMPLE_RESPONSE)
        self.assertEqual(text, "The capital of France is Paris.")
        self.assertEqual((input_tokens, output_tokens), (15, 258))

    def test_parse_rejects_bad_shapes(self):
        with self.assertRaises(ModelError):
            _parse_responses_body({})
        with self.assertRaises(ModelError):
            _parse_responses_body("nope")
        failed = dict(SAMPLE_RESPONSE, status="failed")
        with self.assertRaises(ModelError):
            _parse_responses_body(failed)
        silent = dict(SAMPLE_RESPONSE, output=[{"type": "reasoning"}])
        with self.assertRaises(ModelError):
            _parse_responses_body(silent)

    def test_parse_tolerates_missing_usage(self):
        bare = {"status": "completed", "output": SAMPLE_RESPONSE["output"]}
        text, input_tokens, output_tokens = _parse_responses_body(bare)
        self.assertEqual(text, "The capital of France is Paris.")
        self.assertEqual((input_tokens, output_tokens), (0, 0))

    def test_builds_format_and_cap(self):
        body = _build_responses_body(
            model="m",
            system="s",
            messages=[{"role": "user", "content": "Hi"}],
            response_format={"type": "json_schema"},
            max_output_tokens=600,
        )
        self.assertEqual(body["text"], {"format": {"type": "json_schema"}})
        self.assertEqual(body["max_output_tokens"], 600)

    def test_generate_needs_messages(self):
        provider = MuseProvider(api_key="dummy", model_id="muse-spark-1.3")
        with self.assertRaises(ModelError):
            provider.generate(system="sys", messages=[])

    def test_provider_name_is_model_id(self):
        provider = MuseProvider(api_key="dummy", model_id="muse-spark-1.3")
        self.assertEqual(provider.name, "muse-spark-1.3")


class ModelSelectionTest(unittest.TestCase):
    def setUp(self):
        self._old_key = settings.meta_api_key

    def tearDown(self):
        settings.meta_api_key = self._old_key

    def test_selects_stub_without_key(self):
        settings.meta_api_key = ""
        self.assertIsInstance(get_model_client(), StubProvider)

    def test_selects_live_with_key(self):
        settings.meta_api_key = "dummy-key-for-selection"
        client = get_model_client()
        self.assertIsInstance(client, MuseProvider)
        self.assertEqual(client.name, settings.model_id)


if __name__ == "__main__":
    unittest.main()
