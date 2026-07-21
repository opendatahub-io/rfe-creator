#!/usr/bin/env python3
"""Tests for design-proposals/rfe-merge-capability-probe.py — redaction
correctness, HTTPS enforcement, and error-handling behavior of the
read-only /rfe.merge Jira capability probe. The module filename has
hyphens (not a valid Python identifier), so it's loaded via importlib
rather than a normal import statement."""

import importlib.util
import json
import os
import subprocess
import sys
from unittest import mock

import pytest

SCRIPT = os.path.join(
    os.path.dirname(__file__), "..", "design-proposals", "rfe-merge-capability-probe.py"
)


def _load_probe():
    spec = importlib.util.spec_from_file_location("rfe_merge_capability_probe", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    return _load_probe()


# ── is_https_url ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://redhat.atlassian.net", True),
        ("http://redhat.atlassian.net", False),
        ("redhat.atlassian.net", False),
        ("", False),
        (None, False),
        ("https://", False),
        ("ftp://redhat.atlassian.net", False),
        ("HTTPS://redhat.atlassian.net", True),  # urlsplit lowercases the scheme (RFC 3986)
    ],
)
def test_is_https_url(probe, url, expected):
    assert probe.is_https_url(url) is expected


# ── is_identity_field ────────────────────────────────────────────────────────


def test_is_identity_field_known_names(probe):
    assert probe.is_identity_field("reporter", None) is True
    assert probe.is_identity_field("assignee", {}) is True


def test_is_identity_field_by_schema_type(probe):
    # A custom field's ID gives no hint it's identity-bearing -- only the
    # schema does. This is what closes the "duplicate owner" custom-field
    # leak path documented in the function's docstring.
    assert probe.is_identity_field("customfield_10099", {"type": "user"}) is True
    assert probe.is_identity_field("customfield_10099", {"items": "user"}) is True


def test_is_identity_field_false_for_ordinary_fields(probe):
    assert probe.is_identity_field("priority", {"type": "priority"}) is False
    assert probe.is_identity_field("components", {"type": "array", "items": "component"}) is False


# ── summarize_link ───────────────────────────────────────────────────────────


def test_summarize_link_outward_drops_other_key_and_fields(probe):
    link = {
        "id": "1",
        "type": {"id": "10077", "name": "Related"},
        "outwardIssue": {
            "key": "NVIDIA-694",
            "fields": {"summary": "confidential roadmap item", "assignee": {"displayName": "X"}},
        },
    }
    result = probe.summarize_link(link)
    assert result == {"type_id": "10077", "type_name": "Related", "direction": "outward"}
    serialized = json.dumps(result)
    assert "NVIDIA" not in serialized
    assert "confidential" not in serialized


def test_summarize_link_inward_direction(probe):
    link = {"type": {"id": "10002", "name": "Duplicate"}, "inwardIssue": {"key": "X-1"}}
    assert probe.summarize_link(link)["direction"] == "inward"


def test_summarize_link_unknown_direction(probe):
    link = {"type": {"id": "10002", "name": "Duplicate"}}
    assert probe.summarize_link(link)["direction"] == "unknown"


# ── summarize_issue ──────────────────────────────────────────────────────────


def test_summarize_issue_passes_through_error(probe):
    error = {"_error": {"status": 404, "reason": "Not Found"}}
    assert probe.summarize_issue(error) == error


def test_summarize_issue_full_redaction(probe):
    """The scenario that motivated summarize_issue: nested identity/customer
    content inside a linked issue or parent must never survive, even though
    it isn't the top-level reporter/assignee field."""
    issue = {
        "key": "RHAIRFE-TEST",
        "fields": {
            "reporter": {
                "accountId": "abc123",
                "displayName": "Jane Doe",
                "emailAddress": "jane.doe@example.com",
            },
            "assignee": None,
            "status": {"name": "New", "statusCategory": {"name": "To Do"}},
            "resolution": None,
            "parent": {
                "key": "RHAISTRAT-1",
                "fields": {"summary": "Acme Corp wants faster onboarding"},
            },
            "issuelinks": [
                {
                    "id": "1",
                    "type": {"id": "10077", "name": "Related"},
                    "outwardIssue": {
                        "key": "RHAIRFE-999",
                        "fields": {
                            "summary": "Customer Acme Corp escalation",
                            "assignee": {"displayName": "Nested User"},
                        },
                    },
                }
            ],
            "labels": ["customer-acme-corp", "field-feedback"],
            "components": [{"id": "1", "name": "Acme Integration Team"}],
        },
    }

    result = probe.summarize_issue(issue)

    assert result == {
        "key": "RHAIRFE-TEST",
        "status": "New",
        "status_category": "To Do",
        "resolution": None,
        "reporter_present": True,
        "assignee_present": False,
        "parent_present": True,
        "component_count": 1,
        "label_count": 2,
        "links": [{"type_id": "10077", "type_name": "Related", "direction": "outward"}],
    }

    serialized = json.dumps(result)
    for leak in (
        "Jane Doe",
        "John Smith",
        "jane.doe@example.com",
        "Acme",
        "accountId",
        "displayName",
        "emailAddress",
        "Nested User",
        "RHAIRFE-999",
        "RHAISTRAT-1",
    ):
        assert leak not in serialized, f"leaked: {leak}"


def test_summarize_issue_missing_fields_default_safely(probe):
    result = probe.summarize_issue({"key": "X-1", "fields": {}})
    assert result["reporter_present"] is False
    assert result["component_count"] == 0
    assert result["links"] == []


# ── summarize_link_types / summarize_resolutions ────────────────────────────


def test_summarize_link_types_drops_self_url(probe):
    payload = {
        "issueLinkTypes": [
            {
                "id": "10002",
                "name": "Duplicate",
                "inward": "is duplicated by",
                "outward": "duplicates",
                "self": "https://redhat.atlassian.net/rest/api/3/issueLinkType/10002",
            }
        ]
    }
    result = probe.summarize_link_types(payload)
    assert result == [
        {"id": "10002", "name": "Duplicate", "inward": "is duplicated by", "outward": "duplicates"}
    ]
    assert "redhat.atlassian.net" not in json.dumps(result)


def test_summarize_link_types_passes_through_error(probe):
    error = {"_error": {"reason": "network_error"}}
    assert probe.summarize_link_types(error) == error


def test_summarize_resolutions_drops_self_and_description(probe):
    payload = {
        "values": [
            {
                "id": "10000",
                "name": "Done",
                "isDefault": True,
                "description": "Internal notes mentioning Acme Corp",
                "self": "https://redhat.atlassian.net/rest/api/3/resolution/10000",
            }
        ]
    }
    result = probe.summarize_resolutions(payload)
    assert result == [{"id": "10000", "name": "Done", "is_default": True}]
    serialized = json.dumps(result)
    assert "Acme" not in serialized
    assert "redhat.atlassian.net" not in serialized


# ── summarize_field_values / enum vs opaque ─────────────────────────────────


def test_summarize_field_values_safe_enum_lists_names(probe):
    values = [{"id": "1", "name": "Blocker"}, {"id": "2", "name": "Critical"}]
    assert probe.summarize_field_values("priority", values) == [
        {"id": "1", "name": "Blocker"},
        {"id": "2", "name": "Critical"},
    ]


def test_summarize_field_values_opaque_hides_names(probe):
    values = [{"id": "1", "name": "Secret Customer Name"}]
    result = probe.summarize_field_values("components", values)
    assert result == {"count": 1}
    assert "Secret Customer Name" not in json.dumps(result)


# ── summarize_field_editability / summarize_editability ─────────────────────


def test_summarize_field_editability_identity_field_boolean_only(probe):
    editmeta = {
        "fields": {
            "reporter": {
                "required": True,
                "schema": {"type": "user"},
                "allowedValues": [{"displayName": "Jane Doe"}],
            }
        }
    }
    result = probe.summarize_field_editability(editmeta, "reporter")
    assert result == {
        "present_in_editmeta": True,
        "required": True,
        "schema_type": "user",
        "allowed_values_available": True,
    }
    assert "Jane Doe" not in json.dumps(result)


def test_summarize_field_editability_opaque_custom_field(probe):
    editmeta = {
        "fields": {
            "components": {
                "required": False,
                "schema": {"type": "array"},
                "allowedValues": [{"name": "Secret Customer Name"}],
            }
        }
    }
    result = probe.summarize_field_editability(editmeta, "components")
    assert result["allowed_values"] == {"count": 1}


def test_summarize_field_editability_absent_field(probe):
    result = probe.summarize_field_editability({"fields": {}}, "parent")
    assert result == {"present_in_editmeta": False, "required": None}


def test_summarize_editability_passes_through_error(probe):
    error = {"_error": {"reason": "network_error"}}
    assert probe.summarize_editability(error) == error


# ── summarize_transitions ────────────────────────────────────────────────────


def test_summarize_transitions_drops_custom_field_name_and_full_schema(probe):
    """The exact scenario CodeRabbit and the design doc's own review flagged:
    an admin-defined custom field on a transition screen can be named after
    a customer, and schema.custom can name a third-party vendor plugin."""
    payload = {
        "transitions": [
            {
                "id": "21",
                "name": "Closed",
                "hasScreen": True,
                "isAvailable": True,
                "isConditional": False,
                "to": {"name": "Closed"},
                "fields": {
                    "customfield_99999": {
                        "name": "Customer X Approver",
                        "required": False,
                        "schema": {
                            "type": "user",
                            "custom": "com.vendorname.secretplugin:approver-field",
                            "customId": 99999,
                        },
                        "allowedValues": [],
                    },
                    "resolution": {
                        "name": "Resolution",
                        "required": True,
                        "schema": {"type": "resolution"},
                        "allowedValues": [{"id": "10002", "name": "Duplicate"}],
                    },
                },
            }
        ]
    }
    result = probe.summarize_transitions(payload)
    serialized = json.dumps(result)

    assert "Customer X Approver" not in serialized
    assert "vendorname" not in serialized
    assert "secretplugin" not in serialized

    custom_field = result[0]["fields"]["customfield_99999"]
    assert "name" not in custom_field
    assert "schema" not in custom_field
    assert custom_field["schema_type"] == "user"
    assert custom_field["allowed_values_available"] is False

    resolution_field = result[0]["fields"]["resolution"]
    assert resolution_field["allowed_values"] == [{"id": "10002", "name": "Duplicate"}]


def test_summarize_transitions_passes_through_error(probe):
    error = {"_error": {"reason": "network_error"}}
    assert probe.summarize_transitions(error) == error


# ── request_json ─────────────────────────────────────────────────────────────


class _FakeResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_request_json_success(probe):
    with mock.patch(
        "urllib.request.urlopen", return_value=_FakeResponse(b'{"key": "value"}')
    ):
        result = probe.request_json("https://fake.atlassian.net", "u", "t", "/x")
    assert result == {"key": "value"}


def test_request_json_non_json_200_does_not_crash(probe):
    with mock.patch(
        "urllib.request.urlopen",
        return_value=_FakeResponse(b"<html>SSO login page</html>"),
    ):
        result = probe.request_json("https://fake.atlassian.net", "u", "t", "/x")
    assert result == {"_error": {"status": 200, "reason": "invalid_json_response"}}


def test_request_json_non_dict_json_does_not_crash(probe):
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b"[1, 2, 3]")):
        result = probe.request_json("https://fake.atlassian.net", "u", "t", "/x")
    assert result == {"_error": {"status": 200, "reason": "unexpected_json_shape"}}


def test_request_json_null_json_does_not_crash(probe):
    with mock.patch("urllib.request.urlopen", return_value=_FakeResponse(b"null")):
        result = probe.request_json("https://fake.atlassian.net", "u", "t", "/x")
    assert result == {"_error": {"status": 200, "reason": "unexpected_json_shape"}}


def test_request_json_http_error_hides_error_messages(probe):
    import urllib.error

    class _FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__(url="x", code=400, msg="Bad Request", hdrs=None, fp=None)

        def read(self):
            return b'{"errorMessages": ["accountId 5b1... does not have permission"]}'

    with mock.patch("urllib.request.urlopen", side_effect=_FakeHTTPError()):
        result = probe.request_json("https://fake.atlassian.net", "u", "t", "/x")

    assert result == {"_error": {"status": 400, "reason": "Bad Request"}}
    assert "accountId" not in json.dumps(result)


def test_request_json_url_error_hides_os_message(probe):
    import urllib.error

    with mock.patch(
        "urllib.request.urlopen",
        side_effect=urllib.error.URLError(OSError("hostname internal-jira.corp.local unreachable")),
    ):
        result = probe.request_json("https://fake.atlassian.net", "u", "t", "/x")

    assert result["_error"]["reason"] == "network_error"
    assert "exception_type" in result["_error"]
    assert "internal-jira.corp.local" not in json.dumps(result)


# ── main() / CLI ──────────────────────────────────────────────────────────────


def test_cli_help_exits_zero():
    result = subprocess.run(
        [sys.executable, SCRIPT, "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "issue_keys" in result.stdout


def test_cli_missing_env_vars_exits_nonzero():
    env = {k: v for k, v in os.environ.items() if not k.startswith("JIRA_")}
    result = subprocess.run(
        [sys.executable, SCRIPT, "RHAIRFE-1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1
    assert "Set JIRA_SERVER" in result.stderr


def test_cli_rejects_non_https_server():
    env = {k: v for k, v in os.environ.items() if not k.startswith("JIRA_")}
    env.update({"JIRA_SERVER": "http://example.atlassian.net", "JIRA_USER": "u", "JIRA_TOKEN": "t"})
    result = subprocess.run(
        [sys.executable, SCRIPT, "RHAIRFE-1"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 1
    assert "must be an https://" in result.stderr
