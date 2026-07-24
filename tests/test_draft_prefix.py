#!/usr/bin/env python3
"""Tests for DRAFT-NNN local draft prefix.

Local draft IDs must use the DRAFT-NNN prefix to avoid collision with
real Jira project keys. The old RFE-NNN prefix is retired because RFE
is itself a valid Jira project key.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

from artifact_utils import validate


class TestDraftPrefixInSchemas:
    """Schema validation must accept DRAFT-NNN and reject bare RFE-NNN."""

    def test_draft_prefix_accepted_in_task_schema(self):
        data = {
            "rfe_id": "DRAFT-001",
            "title": "Test RFE",
            "priority": "Normal",
            "status": "Draft",
        }
        errors = validate(data, "rfe-task")
        assert errors == [], f"DRAFT-001 should be valid: {errors}"

    def test_draft_prefix_accepted_in_review_schema(self):
        data = {
            "rfe_id": "DRAFT-042",
            "score": 8,
            "pass": True,
            "recommendation": "submit",
            "feasibility": "feasible",
            "auto_revised": False,
            "needs_attention": False,
            "scores": {
                "what": 2,
                "why": 2,
                "open_to_how": 2,
                "not_a_task": 2,
                "right_sized": 0,
            },
        }
        errors = validate(data, "rfe-review")
        assert errors == [], f"DRAFT-042 should be valid: {errors}"

    def test_rfe_prefix_accepted_as_jira_key_in_task_schema(self):
        """RFE-001 is a valid Jira key format (RFE is a valid project key)."""
        data = {
            "rfe_id": "RFE-001",
            "title": "Test RFE",
            "priority": "Normal",
            "status": "Draft",
        }
        errors = validate(data, "rfe-task")
        assert errors == [], f"RFE-001 should be valid as a Jira key: {errors}"

    def test_rfe_prefix_accepted_as_jira_key_in_review_schema(self):
        """RFE-001 is a valid Jira key format (RFE is a valid project key)."""
        data = {
            "rfe_id": "RFE-001",
            "score": 8,
            "pass": True,
            "recommendation": "submit",
            "feasibility": "feasible",
            "auto_revised": False,
            "needs_attention": False,
            "scores": {
                "what": 2,
                "why": 2,
                "open_to_how": 2,
                "not_a_task": 2,
                "right_sized": 0,
            },
        }
        errors = validate(data, "rfe-review")
        assert errors == [], f"RFE-001 should be valid as a Jira key: {errors}"

    def test_jira_key_still_accepted_in_task_schema(self):
        data = {
            "rfe_id": "RHAIRFE-1595",
            "title": "Existing Jira issue",
            "priority": "Major",
            "status": "Ready",
        }
        errors = validate(data, "rfe-task")
        assert errors == [], f"RHAIRFE-1595 should be valid: {errors}"

    def test_draft_prefix_accepted_as_parent_key(self):
        data = {
            "rfe_id": "DRAFT-002",
            "title": "Child RFE",
            "priority": "Normal",
            "status": "Draft",
            "parent_key": "DRAFT-001",
        }
        errors = validate(data, "rfe-task")
        assert errors == [], f"DRAFT-001 as parent_key should be valid: {errors}"
