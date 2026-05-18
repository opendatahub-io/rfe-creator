"""Tests for label operations in jira_utils — add, remove, swap."""
import json
from unittest.mock import patch, MagicMock

from scripts.jira_utils import add_labels, remove_labels, swap_labels

SERVER = "https://jira.example.com"
USER = "u"
TOKEN = "t"
KEY = "PROJ-1"


def _capture_body(func, *args, **kwargs):
    """Call a label function and return the JSON body sent to api_call_with_retry."""
    with patch("scripts.jira_utils.api_call_with_retry") as mock:
        func(SERVER, USER, TOKEN, KEY, *args, **kwargs)
        mock.assert_called_once()
        return mock.call_args


class TestAddLabels:
    def test_body_structure(self):
        call = _capture_body(add_labels, ["alpha", "beta"])
        body = call.kwargs.get("body") or call[0][4]
        assert body == {
            "update": {
                "labels": [{"add": "alpha"}, {"add": "beta"}]
            }
        }

    def test_uses_put(self):
        call = _capture_body(add_labels, ["x"])
        method = call.kwargs.get("method") or call[0][5]
        assert method == "PUT"


class TestRemoveLabels:
    def test_body_structure(self):
        call = _capture_body(remove_labels, ["old"])
        body = call.kwargs.get("body") or call[0][4]
        assert body == {
            "update": {
                "labels": [{"remove": "old"}]
            }
        }


class TestSwapLabels:
    def test_body_combines_add_and_remove(self):
        call = _capture_body(swap_labels, ["new-a", "new-b"], ["old-x"])
        body = call.kwargs.get("body") or call[0][4]
        assert body == {
            "update": {
                "labels": [
                    {"add": "new-a"},
                    {"add": "new-b"},
                    {"remove": "old-x"},
                ]
            }
        }

    def test_add_only(self):
        call = _capture_body(swap_labels, ["only-add"], [])
        ops = (call.kwargs.get("body") or call[0][4])["update"]["labels"]
        assert ops == [{"add": "only-add"}]

    def test_remove_only(self):
        call = _capture_body(swap_labels, [], ["only-rm"])
        ops = (call.kwargs.get("body") or call[0][4])["update"]["labels"]
        assert ops == [{"remove": "only-rm"}]

    def test_uses_put_on_issue_path(self):
        call = _capture_body(swap_labels, ["a"], ["b"])
        path = call[0][1]
        method = call.kwargs.get("method") or call[0][5]
        assert path == f"/issue/{KEY}"
        assert method == "PUT"
