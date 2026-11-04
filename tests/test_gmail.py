"""Tests for the Gmail adapter (second real integration).

Proves over real HTTP with a throwaway database, plus direct tool
runs: honest status in every state, loopback-only auth URLs, the
code exchange storing a refresh token, approval-gated sending
returning Google's real message ID, revoked sign-ins marked broken
with the remedy, and user isolation. Google's side is mocked httpx:
no network calls here, ever.

Run from the project folder: python -m unittest
"""

import base64
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
import gmail
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


def _send_ok(*args, **kwargs):
    return _resp(200, {"id": "msg-abc-123", "threadId": "thread-1", "labelIds": ["SENT"]})


class GmailApiTest(unittest.TestCase):
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

    def tearDown(self):
        settings.gcal_client_id = self._old_id
        settings.gcal_client_secret = self._old_secret
        gmail._pending.clear()
        main.app.dependency_overrides.clear()
        self.engine.dispose()
        os.remove(self.db_path)

    def _login_headers(self, username="asha", password="correct-horse-99"):
        self.client.post("/auth/register", json={"username": username, "password": password})
        login = self.client.post("/auth/login", json={"username": username, "password": password})
        return {"Authorization": f"Bearer {login.json()['token']}"}

    def _connect(self, headers, redirect="http://127.0.0.1:8091/gmail/callback"):
        started = self.client.post("/gmail/auth-url", json={"redirect_uri": redirect}, headers=headers)
        self.assertEqual(started.status_code, 200)
        query = urllib.parse.parse_qs(urllib.parse.urlparse(started.json()["url"]).query)
        with mock.patch.object(gmail.httpx, "post", side_effect=_token_ok):
            with mock.patch.object(gmail.httpx, "get", side_effect=_userinfo_ok):
                done = self.client.get(f"/gmail/callback?state={query['state'][0]}&code=auth-code-1")
        self.assertEqual(done.status_code, 200)
        self.assertIn("connected", done.text)

    def test_gmail_needs_login(self):
        self.assertEqual(self.client.get("/gmail/status").status_code, 401)
        self.assertEqual(self.client.post("/gmail/auth-url", json={"redirect_uri": "x"}).status_code, 401)
        self.assertEqual(self.client.post("/gmail/disconnect").status_code, 401)

    def test_status_states(self):
        headers = self._login_headers()
        body = self.client.get("/gmail/status", headers=headers).json()
        self.assertTrue(body["client_configured"])
        self.assertFalse(body["connected"])
        self.assertIn("connect your Google account", body["detail"])
        settings.gcal_client_id = ""
        settings.gcal_client_secret = ""
        missing = self.client.get("/gmail/status", headers=headers).json()
        self.assertFalse(missing["client_configured"])
        self.assertIn("GOOGLE_CALENDAR_CLIENT_ID", missing["detail"])

    def test_auth_url_validates_redirect(self):
        headers = self._login_headers()
        for bad in ("", "https://evil.example/cb", "http://127.0.0.1:8091/other", "http://127.0.0.1:8091/calendar/callback"):
            refused = self.client.post("/gmail/auth-url", json={"redirect_uri": bad}, headers=headers)
            self.assertEqual(refused.status_code, 422)
            self.assertIn("this machine", refused.json()["detail"])
        made = self.client.post(
            "/gmail/auth-url", json={"redirect_uri": "http://127.0.0.1:8091/gmail/callback"}, headers=headers
        ).json()
        parsed = urllib.parse.urlparse(made["url"])
        self.assertEqual(parsed.netloc, "accounts.google.com")
        query = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["scope"], [gmail.SCOPES])
        self.assertIn("gmail.send", query["scope"][0])

    def test_connect_and_disconnect(self):
        headers = self._login_headers()
        bad = self.client.get("/gmail/callback?state=nope&code=x")
        self.assertEqual(bad.status_code, 400)
        self.assertIn("expired", bad.text)
        self._connect(headers)
        body = self.client.get("/gmail/status", headers=headers).json()
        self.assertTrue(body["connected"])
        self.assertEqual(body["email"], "asha@example.com")
        other = self._login_headers(username="ivan", password="ivan-password-99")
        self.assertFalse(self.client.get("/gmail/status", headers=other).json()["connected"])
        removed = self.client.post("/gmail/disconnect", headers=headers).json()
        self.assertTrue(removed["disconnected"])
        again = self.client.post("/gmail/disconnect", headers=headers).json()
        self.assertFalse(again["disconnected"])

    def test_provider_gmail_row(self):
        headers = self._login_headers()
        rows = {row["name"]: row for row in self.client.get("/providers", headers=headers).json()}
        self.assertEqual(rows["gmail"]["status"], "available")
        self.assertNotIn("email", rows)
        unchecked = self.client.post("/providers/gmail/check", headers=headers).json()
        self.assertFalse(unchecked["ok"])
        self.assertIn("not connected", unchecked["detail"])
        self._connect(headers)
        with mock.patch.object(gmail.httpx, "post", side_effect=_token_ok):
            checked = self.client.post("/providers/gmail/check", headers=headers).json()
        self.assertTrue(checked["ok"])
        self.assertIn("no email sent by this check", checked["detail"])


class GmailToolTest(unittest.TestCase):
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
        gmail._pending.clear()
        self.session.close()
        self.engine.dispose()
        os.remove(self.db_path)

    def _task(self, user_id=1):
        task = kernel.create_task(
            self.session, user_id=user_id, objective="email the hotel",
            department="daily-life", agent="central", priority="normal",
        )
        kernel.transition_task(
            self.session, user_id=user_id, task_id=task.id, to_status="IN_PROGRESS", note="go"
        )
        return task

    def _connection(self, user_id=1):
        row = models.GmailConnection(
            user_id=user_id, email="asha@example.com", refresh_token="refresh-1",
            broken_note="", created_at="2026-11-04T00:00:00+00:00",
            updated_at="2026-11-04T00:00:00+00:00",
        )
        self.session.add(row)
        self.session.commit()
        return row

    def _args(self, **overrides):
        args = {"to": "hotel@example.com", "subject": "Early check-in?", "body": "Hello, may we check in early?"}
        args.update(overrides)
        return args

    def test_send_is_approval_gated(self):
        task = self._task()
        asked = tools.request_tool(
            self.session, user_id=1, task_id=task.id, tool_name="gmail_send", args=self._args()
        )
        self.assertEqual(asked["status"], "awaiting_approval")
        self.assertEqual(kernel.get_task(self.session, 1, task.id).status, "AWAITING_USER")
        self._connection()
        with mock.patch.object(gmail.httpx, "post", side_effect=[_token_ok(), _send_ok()]) as posted:
            done = tools.decide_approval(
                self.session, user_id=1, approval_id=asked["approval_id"], approve=True, note="yes"
            )
        self.assertEqual(done["status"], "executed")
        self.assertEqual(kernel.get_task(self.session, 1, task.id).status, "COMPLETED")
        self.assertEqual(done["result"]["message_id"], "msg-abc-123")
        raw = posted.call_args_list[1].kwargs["json"]["raw"]
        decoded = base64.urlsafe_b64decode(raw).decode()
        self.assertIn("hotel@example.com", decoded)
        self.assertIn("Early check-in?", decoded)

    def test_deny_fails_task(self):
        task = self._task()
        asked = tools.request_tool(
            self.session, user_id=1, task_id=task.id, tool_name="gmail_send", args=self._args()
        )
        denied = tools.decide_approval(
            self.session, user_id=1, approval_id=asked["approval_id"], approve=False, note="no"
        )
        self.assertEqual(denied["status"], "denied")
        self.assertEqual(kernel.get_task(self.session, 1, task.id).status, "FAILED")

    def test_send_validates(self):
        task = self._task()
        handler = tools.TOOLS["gmail_send"].handler
        for bad in (
            self._args(to="not-an-email"),
            self._args(to="a@b, c@d.com"),
            self._args(to=""),
            self._args(subject="  "),
            self._args(subject="x" * 201),
            self._args(body="  "),
            self._args(body="x" * 20001),
        ):
            with self.assertRaises(gmail.GmailError):
                handler(self.session, user_id=1, task=task, args=bad)

    def test_send_without_connection_fails_honestly(self):
        task = self._task()
        asked = tools.request_tool(
            self.session, user_id=1, task_id=task.id, tool_name="gmail_send", args=self._args()
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
            self.session, user_id=1, task_id=task.id, tool_name="gmail_send", args=self._args()
        )
        bad_refresh = mock.Mock()
        bad_refresh.status_code = 400
        bad_refresh.json.return_value = {"error": "invalid_grant", "error_description": "Token revoked"}
        with mock.patch.object(gmail.httpx, "post", return_value=bad_refresh):
            with self.assertRaises(tools.ToolError):
                tools.decide_approval(
                    self.session, user_id=1, approval_id=asked["approval_id"], approve=True, note="yes"
                )
        row = self.session.query(models.GmailConnection).filter_by(user_id=1).first()
        self.assertIn("Connect again", row.broken_note)
        self.assertIn("Connect again", gmail.status(self.session, user_id=1)["detail"])

    def test_calendar_link_does_not_send_mail(self):
        calendar_row = models.CalendarConnection(
            user_id=1, email="asha@example.com", refresh_token="calendar-refresh",
            broken_note="", created_at="2026-11-04T00:00:00+00:00",
            updated_at="2026-11-04T00:00:00+00:00",
        )
        self.session.add(calendar_row)
        self.session.commit()
        task = self._task()
        asked = tools.request_tool(
            self.session, user_id=1, task_id=task.id, tool_name="gmail_send", args=self._args()
        )
        with self.assertRaises(tools.ToolError):
            tools.decide_approval(
                self.session, user_id=1, approval_id=asked["approval_id"], approve=True, note="yes"
            )
        failures = self.session.query(models.Failure).filter_by(task_id=task.id).all()
        self.assertIn("not connected", failures[0].error)


if __name__ == "__main__":
    unittest.main()
