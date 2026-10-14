"""Tests for session-capture bookkeeping (not the actual browser interaction).

`capture_session()` opens a real, visible browser window and blocks on a
person logging in by hand -- that part is meant to be run for real, once,
by a person, and isn't something a unit test should try to drive. What IS
tested here is `has_saved_session()`, the cheap existence check other code
(like the future drawing-entry feature) will use to tell someone "you
haven't captured a session for this account yet" before trying to reuse one.
"""

from __future__ import annotations

import json

import pytest

from restock_sentinel import sessions


def test_has_saved_session_false_when_never_captured(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")

    assert sessions.has_saved_session("mine") is False


def test_has_saved_session_true_after_profile_dir_has_content(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    profile_dir = tmp_path / "sessions" / "mine"
    profile_dir.mkdir(parents=True)
    (profile_dir / "Default").mkdir()  # stand-in for real Chromium profile contents

    assert sessions.has_saved_session("mine") is True


def test_has_saved_session_false_for_empty_profile_dir(tmp_path, monkeypatch):
    """A directory that exists but was never actually populated (capture
    never completed) shouldn't count as a real saved session.
    """
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    (tmp_path / "sessions" / "mine").mkdir(parents=True)

    assert sessions.has_saved_session("mine") is False


def test_accounts_are_isolated_by_label(tmp_path, monkeypatch):
    """Two accounts' sessions must be tracked independently -- capturing one
    shouldn't make the other look "already saved."
    """
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    mine_dir = tmp_path / "sessions" / "mine"
    mine_dir.mkdir(parents=True)
    (mine_dir / "Default").mkdir()

    assert sessions.has_saved_session("mine") is True
    assert sessions.has_saved_session("wife") is False


class _FakeContext:
    """Stands in for a Playwright persistent context, recording what would
    have been injected instead of actually launching a browser."""

    def __init__(self):
        self.added_cookies = None
        self.closed = False

    def add_cookies(self, cookies):
        self.added_cookies = cookies

    def close(self):
        self.closed = True


class _FakeChromium:
    def __init__(self, context):
        self._context = context

    def launch_persistent_context(self, *_args, **_kwargs):
        return self._context


class _FakePlaywright:
    def __init__(self, context):
        self.chromium = _FakeChromium(context)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_convert_cookie_editor_cookie_maps_same_site_values():
    """Cookie-Editor's lowercase sameSite values need mapping to the
    capitalized ones Playwright's add_cookies() actually accepts."""
    strict = sessions._convert_cookie_editor_cookie(
        {"name": "a", "value": "v", "domain": ".x.com", "path": "/", "sameSite": "strict"}
    )
    assert strict["sameSite"] == "Strict"

    no_restriction = sessions._convert_cookie_editor_cookie(
        {"name": "a", "value": "v", "domain": ".x.com", "path": "/", "sameSite": "no_restriction"}
    )
    assert no_restriction["sameSite"] == "None"

    # A missing sameSite (or Chrome's own "unspecified") has no direct
    # Playwright equivalent -- this checks it falls back to "Lax" rather
    # than raising, and that the other optional fields default sensibly.
    missing = sessions._convert_cookie_editor_cookie(
        {"name": "a", "value": "v", "domain": ".x.com", "path": "/"}
    )
    assert missing["sameSite"] == "Lax"
    assert missing["httpOnly"] is False
    assert missing["secure"] is False
    assert missing["expires"] == -1


def test_import_cookies_from_file_converts_and_injects(tmp_path, monkeypatch):
    """End-to-end (minus the real browser): a Cookie-Editor export on disk
    should come out the other side as a properly-converted add_cookies()
    call against the right account's profile directory.
    """
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    fake_context = _FakeContext()
    monkeypatch.setattr(sessions, "sync_playwright", lambda: _FakePlaywright(fake_context))

    cookies_path = tmp_path / "export.json"
    cookies_path.write_text(
        json.dumps(
            [
                {
                    "domain": ".example.com",
                    "expirationDate": 1999999999.5,
                    "httpOnly": True,
                    "name": "session_token",
                    "path": "/",
                    "sameSite": "lax",
                    "secure": True,
                    "session": False,
                    "value": "fake-test-value-not-real",
                },
                {
                    "domain": ".example.com",
                    "name": "temp_flag",
                    "path": "/",
                    "sameSite": "unspecified",
                    "secure": False,
                    "session": True,
                    "value": "1",
                },
            ]
        )
    )

    profile_dir = sessions.import_cookies_from_file("mine", cookies_path)

    assert profile_dir == tmp_path / "sessions" / "mine"
    assert fake_context.closed is True
    assert fake_context.added_cookies == [
        {
            "name": "session_token",
            "value": "fake-test-value-not-real",
            "domain": ".example.com",
            "path": "/",
            "httpOnly": True,
            "secure": True,
            "sameSite": "Lax",
            "expires": 1999999999.5,
        },
        {
            "name": "temp_flag",
            "value": "1",
            "domain": ".example.com",
            "path": "/",
            "httpOnly": False,
            "secure": False,
            "sameSite": "Lax",
            "expires": -1,
        },
    ]


def test_import_cookies_from_file_rejects_non_list_json(tmp_path, monkeypatch):
    """A malformed/unexpected export shape should fail loudly and early,
    rather than silently injecting zero cookies and looking like it worked.
    """
    monkeypatch.setattr(sessions, "SESSIONS_DIR", tmp_path / "sessions")
    cookies_path = tmp_path / "bad.json"
    cookies_path.write_text(json.dumps({"not": "a list"}))

    with pytest.raises(ValueError):
        sessions.import_cookies_from_file("mine", cookies_path)
