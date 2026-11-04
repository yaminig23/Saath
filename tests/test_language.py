"""Tests for Hindi language support (M9 languages v1).

Proves over a throwaway database with no network: the chat language
falls back to English unless language=hindi is stored, voice picking
never hands Devanagari to an English voice, Hindi lessons ask the
model for Hindi, and bad language codes fail honestly. Run from the
project folder: python -m unittest
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import kernel
import memory
import models
import narration
import tutor
from model_client import ModelResponse


def _fresh_db(test):
    handle, test.db_path = tempfile.mkstemp(suffix=".db")
    os.close(handle)
    test.engine = create_engine(f"sqlite:///{test.db_path}", connect_args={"check_same_thread": False})
    models.Base.metadata.create_all(test.engine)
    test.session = sessionmaker(bind=test.engine)()


def _drop_db(test):
    test.session.close()
    test.engine.dispose()
    os.remove(test.db_path)


class UserLanguageTest(unittest.TestCase):
    def setUp(self):
        _fresh_db(self)

    def tearDown(self):
        _drop_db(self)

    def test_missing_language_is_english(self):
        self.assertEqual(memory.user_language(self.session, user_id=1), "en")

    def test_hindi_memory_selects_hindi(self):
        memory.store_memory(
            self.session, user_id=1, layer="user", category="preferences", key="language", value="hindi"
        )
        self.assertEqual(memory.user_language(self.session, user_id=1), "hi")

    def test_english_memory_stays_english(self):
        memory.store_memory(
            self.session, user_id=1, layer="user", category="preferences", key="language", value="english"
        )
        self.assertEqual(memory.user_language(self.session, user_id=1), "en")

    def test_odd_value_falls_back_to_english(self):
        memory.store_memory(
            self.session, user_id=1, layer="user", category="preferences", key="language", value="klingon"
        )
        self.assertEqual(memory.user_language(self.session, user_id=1), "en")


class VoiceForTest(unittest.TestCase):
    VOICES = [
        {"name": "Microsoft David", "culture": "en-US"},
        {"name": "Microsoft Zira", "culture": "en-US"},
    ]
    HINDI_VOICES = VOICES + [{"name": "Microsoft Kalpana", "culture": "hi-IN"}]

    def test_english_gets_english_voice(self):
        with patch.object(narration, "available_voices", return_value=list(self.VOICES)):
            self.assertEqual(narration.voice_for("en"), "Microsoft David")

    def test_hindi_with_hindi_voice(self):
        with patch.object(narration, "available_voices", return_value=list(self.HINDI_VOICES)):
            self.assertEqual(narration.voice_for("hi"), "Microsoft Kalpana")

    def test_hindi_without_hindi_voice_skips_honestly(self):
        with patch.object(narration, "available_voices", return_value=list(self.VOICES)):
            self.assertEqual(narration.voice_for("hi"), "")

    def test_no_voices_means_no_voice(self):
        with patch.object(narration, "available_voices", return_value=[]):
            self.assertEqual(narration.voice_for("en"), "")
            self.assertEqual(narration.voice_for("hi"), "")


class PlanLanguageTest(unittest.TestCase):
    def setUp(self):
        _fresh_db(self)
        self._real = tutor.model_client.get_model_client

    def tearDown(self):
        tutor.model_client.get_model_client = self._real
        _drop_db(self)

    def _client(self):
        lesson = json.dumps(
            {
                "steps": [{"title": "Ek", "text": "Dheere karo.", "image_prompt": ""}],
                "follow_up": "Kuch poochho.",
            }
        )

        class Capturing:
            name = "capturing-1"
            supports_planning = True
            supports_vision = False

            def __init__(self):
                self.seen = []

            def generate(self, **kwargs):
                self.seen.append(kwargs)
                return ModelResponse(text=lesson, model=self.name, input_chars=5, output_chars=50)

        client = Capturing()
        tutor.model_client.get_model_client = lambda: client
        return client

    def test_hindi_lesson_asks_for_hindi(self):
        client = self._client()
        lesson = tutor.plan_lesson(self.session, user_id=1, topic="use the kettle", language="hi")
        self.assertEqual(len(lesson["steps"]), 1)
        sent = client.seen[0]["messages"][0]["content"]
        self.assertIn("Hindi", sent)
        task = kernel.get_task(self.session, 1, lesson["task_id"])
        self.assertIn("lang=hi", task.evidence)

    def test_english_lesson_does_not_ask_for_hindi(self):
        client = self._client()
        lesson = tutor.plan_lesson(self.session, user_id=1, topic="use the kettle", language="en")
        self.assertEqual(len(lesson["steps"]), 1)
        sent = client.seen[0]["messages"][0]["content"]
        self.assertNotIn("Hindi", sent)

    def test_bad_language_fails_before_auditing(self):
        client = self._client()
        with self.assertRaises(tutor.TutorError):
            tutor.plan_lesson(self.session, user_id=1, topic="use the kettle", language="xx")
        self.assertEqual(client.seen, [])


if __name__ == "__main__":
    unittest.main()
