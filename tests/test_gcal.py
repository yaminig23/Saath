"""Tests for the Google Calendar adapter (first real integration).

Proves over real HTTP with a throwaway database, plus direct tool
runs: honest status in every state, loopback-only auth URLs, the
code exchange storing a refresh token, approval-gated event creation
returning Google's real event ID, day reads, revoked sign-ins marked
broken with the remedy, and user isolation. Google's side is mocked
httpx: no network calls here, ever.

Run from the project folder: python -m unittest
"""

import json
import os
import shutil
import tempfile
import unittest
import urllib.parse
from unittest import mock

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import db
import gcal
import kernel
import main
import models
import tools
from config import settings


def _resp(status_code, payload=None):
    response = mock.Mock()
    response.status_code = status_code
    if payload is None:
        response.json.side_effect = ValueError("no json")
    else:
        response.json.return_value = payload
    return response


def _token_ok(*args, **kwargs):
    data = kwargs.get("data", {})
    if data.get("grant_type") == "authorization_code":
        return _resp(200, {"refresh_token": "refresh-1", "access_token": "access-1"})
    return _resp(200, {"access_token": "access-2"})


def _userinfo_ok(*args, **kwargs):
    return _resp(200, {"email": "asha@example.com"})


def _insert_ok(*args, **kwargs):
    return _resp(200, {"id": "google-event-9", "htmlLink": "https://calendar.google.com/e9"})


def _list_ok(*args, **kwargs):
    return _resp(
        200,
        {
            "items": [
                {
                    "id": "a1",
                    "summary": "Dentist",
                    "start": {"dateTime": "2026-11-05T10:00:00+05:30"},
                    "end": {"dateTime": "2026-11-05T10:30:00+05:30"},
                    "htmlLink": "https://calendar.google.com/a1",
                },
                {
                    "id": "a2",
                    "summary": "Diwali",
                    "start": {"date": "2026-11-08"},
                    "end": {"date": "2026-11-09"},
                },
            ]
        },
    )


class GcalApiTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        factory = sessionmaker(bind=self.engine)

        def override_get_session():
            session = factory()
            try:
                yield session
            finally:
                session.close()

        main.app.dependency_overrides[db.get_session] = override_get_session
        self.client = TestClient(main.app)
        self._old_id = settings.gcal_client_id
        self._old_secret = settings.gcal_client_secret
        settings.gcal_client_id = "test-client-id"
        settings.gcal_client_secret = "test-client-secret"
        self.media = tempfile.mkdtemp(prefix="saath-gcal-")
        self._old_media = settings.media_dir
        settings.media_dir = self.media

    def tearDown(self):
        settings.gcal_client_id = self._old_id
        settings.gcal_client_secret = self._old_secret
        settings.media_dir = self._old_media
        gcal._pending.clear()
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)
        shutil.rmtree(self.media, ignore_errors=True)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def _connect(self, headers, redirect="http://127.0.0.1:8091/calendar/callback"):
        started = self.client.post("/calendar/auth-url", json={"redirect_uri": redirect}, headers=headers)
        self.assertEqual(started.status_code, 200)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(started.json()["url"]).query)
        with mock.patch.object(gcal.httpx, "post", side_effect=_token_ok):
            with mock.patch.object(gcal.httpx, "get", side_effect=_userinfo_ok):
                done = self.client.get(f"/calendar/callback?state={query['state'][0]}&code=auth-code-1")
        self.assertEqual(done.status_code, 200)
        self.assertIn("connected", done.text)

    def test_calendar_needs_login(self):
        self.assertEqual(self.client.get("/calendar/status").status_code, 401)
        self.assertEqual(self.client.post("/calendar/auth-url", json={"redirect_uri": "x"}).status_code, 401)
        self.assertEqual(self.client.post("/calendar/disconnect").status_code, 401)
        self.assertEqual(self.client.get("/calendar/day?date=2026-11-05").status_code, 401)

    def test_status_states(self):
        headers = self._login_headers()
        body = self.client.get("/calendar/status", headers=headers).json()
        self.assertTrue(body["client_configured"])
        self.assertFalse(body["connected"])
        self.assertIn("connect your Google account", body["detail"])
        settings.gcal_client_id = ""
        settings.gcal_client_secret = ""
        missing = self.client.get("/calendar/status", headers=headers).json()
        self.assertFalse(missing["client_configured"])
        self.assertIn("GOOGLE_CALENDAR_CLIENT_ID", missing["detail"])

    def test_auth_url_validates_redirect(self):
        headers = self._login_headers()
        for bad in ("", "https://evil.example/cb", "http://127.0.0.1:8091/other", "http://example.com/calendar/callback"):
            refused = self.client.post("/calendar/auth-url", json={"redirect_uri": bad}, headers=headers)
            self.assertEqual(refused.status_code, 422)
            self.assertIn("this machine", refused.json()["detail"])
        settings.gcal_client_id = ""
        refused = self.client.post(
            "/calendar/auth-url", json={"redirect_uri": "http://127.0.0.1:8091/calendar/callback"}, headers=headers
        )
        self.assertEqual(refused.status_code, 422)
        self.assertIn("GOOGLE_CALENDAR_CLIENT_ID", refused.json()["detail"])
        settings.gcal_client_id = "test-client-id"
        made = self.client.post(
            "/calendar/auth-url", json={"redirect_uri": "http://127.0.0.1:8091/calendar/callback"}, headers=headers
        ).json()
        parsed = urllib.parse.urlparse(made["url"])
        self.assertEqual(parsed.netloc, "accounts.google.com")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["scope"], [gcal.SCOPES])
        self.assertTrue(query["state"][0])

    def test_connect_and_disconnect(self):
        headers = self._login_headers()
        bad = self.client.get("/calendar/callback?state=nope&code=x")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("expired", bad.text)
        self._connect(headers)
        body = self.client.get("/calendar/status", headers=headers).json()
        self.assertTrue(body["connected"])
        self.assertEqual(body["email"], "asha@example.com")
        other = self._login_headers(username="ivan", password="ivan-password-99")
        self.assertFalse(self.client.get("/calendar/status", headers=other).json()["connected"])
        removed = self.client.post("/calendar/disconnect", headers=headers).json()
        self.assertTrue(removed["disconnected"])
        again = self.client.post("/calendar/disconnect", headers=headers).json()
        self.assertFalse(again["disconnected"])
        self.assertFalse(self.client.get("/calendar/status", headers=headers).json()["connected"])

    def test_connect_refuses_without_refresh_token(self):
        headers = self._login_headers()
        started = self.client.post(
            "/calendar/auth-url",
            json={"redirect_uri": "http://127.0.0.1:8091/calendar/callback"},
            headers=headers,
        ).json()
        state = urllib.parse.parse_qs(urllib.parse.urlparse(started["url"]).query)["state"][0]
        with mock.patch.object(gcal.httpx, "post", return_value=_resp(200, {"access_token": "only"})):
            bad = self.client.get(f"/calendar/callback?state={state}&code=x")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("permissions", bad.text)
        self.assertFalse(self.client.get("/calendar/status", headers=headers).json()["connected"])

    def test_read_day_over_http(self):
        headers = self._login_headers()
        refused = self.client.get("/calendar/day?date=2026-11-05&time_zone=Asia/Kolkata", headers=headers)
        self.assertEqual(refused.status_code, 422)
        self.assertIn("not connected", refused.json()["detail"])
        self.assertEqual(
            self.client.get("/calendar/day?date=tomorrow", headers=headers).status_code, 422
        )
        self._connect(headers)
        with mock.patch.object(gcal.httpx, "post", side_effect=_token_ok):
            with mock.patch.object(gcal.httpx, "get", side_effect=_list_ok):
                body = self.client.get(
                    "/calendar/day?date=2026-11-05&time_zone=Asia/Kolkata", headers=headers
                ).json()
        self.assertEqual(body["date"], "2026-11-05")
        self.assertEqual(len(body["events"]), 2)
        self.assertEqual(body["events"][0]["title"], "Dentist")
        self.assertIn("2026-11-05T10:00", body["events"][0]["start"])
        self.assertEqual(body["events"][1]["start"], "2026-11-08")

    def test_provider_calendar_row(self):
        headers = self._login_headers()
        rows = {row["name"]: row for row in self.client.get("/providers", headers=headers).json()}
        self.assertEqual(rows["calendar"]["status"], "available")
        unchecked = self.client.post("/providers/calendar/check", headers=headers).json()
        self.assertFalse(unchecked["ok"])
        self.assertIn("not connected", unchecked["detail"])
        self._connect(headers)
        with mock.patch.object(gcal.httpx, "post", side_effect=_token_ok):
            with mock.patch.object(gcal.httpx, "get", side_effect=_list_ok):
                checked = self.client.post("/providers/calendar/check", headers=headers).json()
        self.assertTrue(checked["ok"])
        self.assertIn("asha@example.com", checked["detail"])
        settings.gcal_client_id = ""
        settings.gcal_client_secret = ""
        rows = {row["name"]: row for row in self.client.get("/providers", headers=headers).json()}
        self.assertEqual(rows["calendar"]["status"], "missing")
        self.assertIn("GOOGLE_CALENDAR_CLIENT_ID", rows["calendar"]["detail"])


class GcalToolTest(unittest.TestCase):
    def setUp(self):
        handle, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(handle)
        self.engine = create_engine(f"sqlite:///{self.db_path}", connect_args={"check_same_thread": False})
        models.Base.metadata.create_all(self.engine)
        self.session = sessionmaker(bind=self.engine)()
        self._old_id = settings.gcal_client_id
        self._old_secret = settings.gcal_client_secret
        settings.gcal_client_id = "test-client-id"
        settings.gcal_client_secret = "test-client-secret"

    def tearDown(self):
        settings.gcal_client_id = self._old_id
        settings.gcal_client_secret = self._old_secret
        gcal._pending.clear()
        self.session.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _task(self, user_id=1):
        task = kernel.create_task(
            self.session, user_id=user_id, objective="add dentist to calendar",
            department="daily-life", agent="central", priority="normal",
        )
        kernel.transition_task(
            self.session, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="go"
        )
        return task

    def _connection(self, user_id=1):
        row = models.CalendarConnection(
            user_id=user_id, email="asha@example.com", refresh_token="refresh-1",
            broken_note="", created_at="2026-11-04T00:00:00+00:00",
            updated_at="2026-11-04T00:00:00+00:00",
        )
        self.session.add(row)
        self.session.commit()
        return row

    def _args(self, **overrides):
        args = {
            "title": "Dentist", "date": "2026-11-05", "start": "10:00", "end": "10:30",
            "time_zone": "Asia/Kolkata", "description": "cleaning", "location": "Clinic",
        }
        args.update(overrides)
        return args

    def test_add_event_is_approval_gated(self):
        task = self._task()
        asked = tools.request_tool(
            self.session, user_id=1, task_id=task.id, tool_name="calendar_add_event", args=self._args()
        )
        self.assertEqual(asked["status"], "awaiting_approval")
        self.assertEqual(kernel.get_task(self.session, 1, task.id).status, "AWAITING_USER")
        self._connection()
        with mock.patch.object(gcal.httpx, "post", side_effect=[_token_ok(), _insert_ok()]) as posted:
            done = tools.decide_approval(
                self.session, user_id=1, approval_id=asked["approval_id"], approve=True, note="yes"
            )
        self.assertEqual(done["status"], "executed")
        self.assertEqual(kernel.get_task(self.session, 1, task.id).status, "COMPLETED")
        self.assertEqual(done["result"]["event_id"], "google-event-9")
        result = json.loads(kernel.get_task(self.session, 1, task.id).result)
        self.assertEqual(result["event_id"], "google-event-9")
        self.assertIn("calendar.google.com", result["link"])
        sent = posted.call_args_list[1].kwargs["json"]
        self.assertEqual(sent["summary"], "Dentist")
        self.assertEqual(sent["start"]["dateTime"], "2026-11-05T10:00:00")
        self.assertEqual(sent["start"]["timeZone"], "Asia/Kolkata")

    def test_deny_fails_task(self):
        task = self._task()
        asked = tools.request_tool(
            self.session, user_id=1, task_id=task.id, tool_name="calendar_add_event", args=self._args()
        )
        denied = tools.decide_approval(
            self.session, user_id=1, approval_id=asked["approval_id"], approve=False, note="no"
        )
        self.assertEqual(denied["status"], "denied")
        self.assertEqual(kernel.get_task(self.session, 1, task.id).status, "FAILED")

    def test_add_event_validates(self):
        task = self._task()
        handler = tools.TOOLS["calendar_add_event"].handler
        for bad in (
            self._args(title="  "),
            self._args(date="tomorrow"),
            self._args(start="10am"),
            self._args(start="11:00", end="10:00"),
            self._args(time_zone=""),
            self._args(title="x" * 201),
        ):
            with self.assertRaises(gcal.GcalError):
                handler(self.session, user_id=1, task=task, args=bad)

    def test_add_event_without_connection_fails_honestly(self):
        task = self._task()
        asked = tools.request_tool(
            self.session, user_id=1, task_id=task.id, tool_name="calendar_add_event", args=self._args()
        )
        with self.assertRaises(tools.ToolError):
            tools.decide_approval(
                self.session, user_id=1, approval_id=asked["approval_id"], approve=True, note="yes"
            )
        self.assertEqual(kernel.get_task(self.session, 1, task.id).status, "FAILED")
        failures = self.session.query(models.Failure).filter_by(task_id=task.id).all()
        self.assertEqual(len(failures), 1)
        self.assertIn("not connected", failures[0].error)

    def test_revoked_sign_in_marks_broken(self):
        self._connection()
        task = self._task()
        asked = tools.request_tool(
            self.session, user_id=1, task_id=task.id, tool_name="calendar_add_event", args=self._args()
        )
        bad_refresh = _resp(400, {"error": "invalid_grant", "error_description": "Token revoked"})
        with mock.patch.object(gcal.httpx, "post", return_value=bad_refresh):
            with self.assertRaises(tools.ToolError):
                tools.decide_approval(
                    self.session, user_id=1, approval_id=asked["approval_id"], approve=True, note="yes"
                )
        self.assertEqual(kernel.get_task(self.session, 1, task.id).status, "FAILED")
        row = self.session.query(models.CalendarConnection).filter_by(user_id=1).first()
        self.assertIn("connect again", row.broken_note.lower())
        self.assertIn("connect again", gcal.status(self.session, user_id=1)["detail"].lower())
        with self.assertRaises(gcal.GcalError):
            gcal.list_day(self.session, user_id=1, date="2026-11-05", time_zone="UTC")

    def test_read_day_rejects_unknown_zone(self):
        self._connection(user_id=1)
        with self.assertRaises(gcal.GcalError) as raised:
            gcal.list_day(self.session, user_id=1, date="2026-11-05", time_zone="Mars/Olympus")
        self.assertIn("unknown time_zone", str(raised.exception))

    def test_read_day_tool_and_isolation(self):
        self._connection(user_id=1)
        task = self._task(user_id=1)
        with mock.patch.object(gcal.httpx, "post", side_effect=_token_ok):
            with mock.patch.object(gcal.httpx, "get", side_effect=_list_ok):
                found = tools.request_tool(
                    self.session, user_id=1, task_id=task.id, tool_name="calendar_read_day",
                    args={"date": "2026-11-05", "time_zone": "Asia/Kolkata"},
                )
        self.assertEqual(found["status"], "done")
        self.assertEqual(len(found["result"]["events"]), 2)
        stranger = self._task(user_id=2)
        with self.assertRaises(tools.ToolError) as raised:
            tools.request_tool(
                self.session, user_id=2, task_id=stranger.id, tool_name="calendar_read_day",
                args={"date": "2026-11-05", "time_zone": "UTC"},
            )
        self.assertIn("not connected", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
