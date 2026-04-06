"""Shared test fixtures — jira-emulator server for integration tests."""
import base64
import json
import os
import socket
import threading
import time
import urllib.request

import pytest


def _find_free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _jira_request(base_url, method, path, body=None):
    """Make a request to the jira-emulator."""
    url = f"{base_url}{path}"
    data = json.dumps(body).encode() if body is not None else None
    creds = base64.b64encode(b"admin:admin").decode()
    headers = {
        "Authorization": f"Basic {creds}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req) as resp:
        if resp.status == 204:
            return None
        body_bytes = resp.read()
        return json.loads(body_bytes) if body_bytes else None


@pytest.fixture(scope="session")
def jira_emu():
    """Start a jira-emulator server for the test session.

    Returns the base URL (e.g. http://127.0.0.1:PORT).
    The server runs in a daemon thread and is shut down when
    the session ends.
    """
    port = _find_free_port()

    os.environ["DATABASE_URL"] = "sqlite+aiosqlite://"
    os.environ["AUTH_MODE"] = "none"
    os.environ["SEED_DATA"] = "true"

    # Import inside the fixture so env vars are set first
    from jira_emulator.config import get_settings
    get_settings.cache_clear()
    from jira_emulator.database import reset_engine
    reset_engine()
    from jira_emulator.app import create_app
    import uvicorn

    app = create_app()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)

    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    # Wait for server readiness
    base_url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{base_url}/")
            break
        except Exception:
            time.sleep(0.05)

    yield base_url
    server.should_exit = True


@pytest.fixture
def jira(jira_emu):
    """Per-test fixture: resets emulator state and provides helpers.

    Usage:
        def test_foo(jira):
            jira.create("RHAIRFE-1", "Summary", "Description text")
            # ... run code under test against jira.url ...
    """
    # Patch emulator seed data to include link types this project needs
    from jira_emulator.services import seed_service
    _extra_link_types = [
        {"name": "Issue split",
         "inward_description": "is split from",
         "outward_description": "split to"},
    ]
    _orig = seed_service.LINK_TYPES
    seed_service.LINK_TYPES = _orig + [
        lt for lt in _extra_link_types
        if lt["name"] not in {x["name"] for x in _orig}
    ]

    # Reset all data before each test (re-seeds with patched link types)
    req = urllib.request.Request(
        f"{jira_emu}/api/admin/reset", method="POST", data=b"")
    urllib.request.urlopen(req)

    class JiraHelper:
        url = jira_emu

        @staticmethod
        def create(key, summary, description, labels=None,
                   components=None):
            """Import an issue with a specific key."""
            issue = {
                "key": key,
                "summary": summary,
                "project": key.split("-")[0],
                "issue_type": "Feature Request",
                "description": description,
            }
            if labels:
                issue["labels"] = labels
            if components:
                issue["components"] = [{"name": c} for c in components]
            _jira_request(jira_emu, "POST", "/api/admin/import",
                          {"issues": [issue]})

        @staticmethod
        def get(key):
            """GET an issue, return parsed JSON."""
            return _jira_request(jira_emu, "GET",
                                 f"/rest/api/3/issue/{key}")

        @staticmethod
        def search(jql, fields="key,description,labels"):
            """JQL search, return list of issues."""
            from urllib.parse import quote
            path = (f"/rest/api/3/search/jql"
                    f"?jql={quote(jql, safe='')}&fields={fields}")
            data = _jira_request(jira_emu, "GET", path)
            return data.get("issues", [])

        @staticmethod
        def request(method, path, body=None):
            """Make an arbitrary API request to the emulator."""
            return _jira_request(jira_emu, method, path, body)

    return JiraHelper()
