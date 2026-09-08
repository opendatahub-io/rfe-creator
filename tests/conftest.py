"""Shared test fixtures — jira-emulator server for integration tests."""

import base64
import json
import os
import socket
import threading
import time
import urllib.request

import pytest
import yaml

TYPES_DIR = os.path.join(os.path.dirname(__file__), "..", "types")


# ─── Drop-in type descriptors ─────────────────────────────────────────────────


def _set_dotted(data, dotted, value):
    node = data
    keys = dotted.split(".")
    for key in keys[:-1]:
        node = node.setdefault(key, {})
    node[keys[-1]] = value


def _del_dotted(data, dotted):
    node = data
    keys = dotted.split(".")
    for key in keys[:-1]:
        node = node[key]
    del node[keys[-1]]


# A drop-in derived from types/rfe with its own binding, id grammar and layout, so it registers
# next to the shipped types (split_submit refuses two types on one (project, issue_type) pair)
# and scans its own directories. Everything else (labels, schema facts, snapshot, reporting)
# stays the rfe value, so every adopted table builds for it at import.
MEMO_OVERRIDES = {
    "display": {"entity": "Memo", "entity_plural": "Memos"},
    "identity.jira.project": "MEMO",
    "identity.jira.issue_type": "Memo",
    "identity.jira.key_prefixes": ["MEMO-"],
    "identity.local_prefix": "MEMO-",
    "identity.local_id_pattern": r"^MEMO-\d+$",
    "identity.id_field": "memo_id",
    "dirs.tasks": "artifacts/memo-tasks",
    "dirs.originals": "artifacts/memo-originals",
    "dirs.reviews": "artifacts/memo-reviews",
    "conventions.type_label": "Memo",
    "conventions.parent_key_patterns": [r"MEMO-\d+"],
    "snapshot.prefix": "memo-snapshot-",
}


def write_drop_in(root, name, base="rfe", overrides=None, drop=()):
    """Write ``<root>/<name>/type.yaml``: the shipped ``base`` descriptor renamed to ``name``,
    with the dotted ``drop`` paths removed and the dotted ``overrides`` applied. Returns the
    descriptor path. ``root`` is what RFE_CREATOR_EXTRA_TYPES takes."""
    with open(os.path.join(TYPES_DIR, base, "type.yaml"), encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    data["type"] = name
    for dotted in drop:
        _del_dotted(data, dotted)
    for dotted, value in (overrides or {}).items():
        _set_dotted(data, dotted, value)
    path = os.path.join(root, name, "type.yaml")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
    return path


class DropInRoot:
    """One extra registry root under tmp_path; ``path`` is the RFE_CREATOR_EXTRA_TYPES value."""

    def __init__(self, path):
        self.path = path

    def add(self, name, base="rfe", overrides=None, drop=()):
        write_drop_in(self.path, name, base=base, overrides=overrides, drop=drop)
        return self.path

    def memo(self, name="memo", drop=()):
        """An rfe copy on its own (MEMO, Memo) binding, minus the ``drop`` paths."""
        return self.add(name, overrides=MEMO_OVERRIDES, drop=drop)


@pytest.fixture
def drop_in_root(tmp_path):
    root = tmp_path / "extra-types"
    root.mkdir()
    return DropInRoot(str(root))


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
    import uvicorn
    from jira_emulator.app import create_app

    app = create_app()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
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
        {
            "name": "Work item split",
            "inward_description": "is split from",
            "outward_description": "split to",
        },
    ]
    _orig = seed_service.LINK_TYPES
    seed_service.LINK_TYPES = _orig + [
        lt for lt in _extra_link_types if lt["name"] not in {x["name"] for x in _orig}
    ]

    # Patch RHAIRFE workflow to add global "Approve" transition (matches
    # production Jira where the workflow is fully open).
    _wf = seed_service.WORKFLOWS.get("RHAIRFE Workflow", [])
    _global_approve = (None, "Approve", "Approved")
    if _global_approve not in _wf:
        seed_service.WORKFLOWS["RHAIRFE Workflow"] = _wf + [_global_approve]

    # Patch RHOAIENG workflow for initiative tests.
    # RHOAIENG uses "Default Workflow" in seed data, so copy it and add
    # the approve transition, then wire the project to the new workflow.
    _default_wf = seed_service.WORKFLOWS.get("Default Workflow", [])
    _iwf = list(_default_wf)
    if _global_approve not in _iwf:
        _iwf.append(_global_approve)
    seed_service.WORKFLOWS["RHOAIENG Workflow"] = _iwf
    seed_service.PROJECT_WORKFLOWS["RHOAIENG"] = "RHOAIENG Workflow"

    # Reset all data before each test (re-seeds with patched data)
    req = urllib.request.Request(f"{jira_emu}/api/admin/reset", method="POST", data=b"")
    urllib.request.urlopen(req)

    class JiraHelper:
        url = jira_emu

        @staticmethod
        def create(key, summary, description, labels=None, components=None, issue_type=None):
            """Import an issue with a specific key."""
            issue = {
                "key": key,
                "summary": summary,
                "project": key.split("-")[0],
                "issue_type": issue_type or "Feature Request",
                "description": description,
            }
            if labels:
                issue["labels"] = labels
            if components:
                issue["components"] = [{"name": c} for c in components]
            _jira_request(jira_emu, "POST", "/api/admin/import", {"issues": [issue]})

        @staticmethod
        def get(key):
            """GET an issue, return parsed JSON."""
            return _jira_request(jira_emu, "GET", f"/rest/api/3/issue/{key}")

        @staticmethod
        def search(jql, fields="key,description,labels"):
            """JQL search, return list of issues."""
            from urllib.parse import quote

            path = f"/rest/api/3/search/jql?jql={quote(jql, safe='')}&fields={fields}"
            data = _jira_request(jira_emu, "GET", path)
            return data.get("issues", [])

        @staticmethod
        def request(method, path, body=None):
            """Make an arbitrary API request to the emulator."""
            return _jira_request(jira_emu, method, path, body)

    return JiraHelper()
