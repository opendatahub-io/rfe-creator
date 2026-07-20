#!/usr/bin/env python3
"""Read-only Jira capability probe for /rfe.merge design.

Output is identity-redacted by default -- there is no flag to disable
this. Reporter and assignee names, email addresses, account IDs,
avatars, user-valued edit metadata, and free-text summary content
(which can contain customer names) must never be written to a
committed or shared artifact. Admin-configurable free text (custom
field names, custom picklist option values, the other side of an
issue link) is minimized on a best-effort basis -- reduced to a count
or dropped rather than a name, since this script has no way to vet
whether a given site administrator's naming choice is safe to repeat.

What's deliberately still retained: transition names, status names,
resolution names, and link-type inward/outward labels. These are
Jira/site-administered *taxonomy* the capability profile exists to
answer questions about (e.g. "is Duplicate an allowed resolution"),
not customer or personal data -- but they are still administrator-
chosen strings, so a final human scan of the output for anything
org-specific is worth doing before sharing it more broadly than the
design review it was generated for.

Issue summaries are built field-by-field from an explicit allowlist
(summarize_issue), never by copying Jira's raw response and blanking
out a few known keys -- a redacted *copy* still carries whatever
nested objects we didn't anticipate (a linked issue's own assignee, a
parent's summary text, a label containing a customer name). Building
fresh has no such blind spot. The same principle applies everywhere
else a raw Jira response might otherwise pass through: issueLinkType
and resolution/search responses are rebuilt into purpose-built
summaries (dropping "self" URLs, which embed the instance hostname),
error bodies keep only status/reason (never Jira's errorMessages,
which can echo request content, and never a URLError's raw OS-level
message, which can embed a hostname), and non-identity field option
values are either a small Jira-defined enum (priority, resolution --
safe to list in full) or reported as a count only, never by name --
free-text picklist options can carry customer or org-specific
vocabulary this script has no way to vet.

No repository imports: this is meant to survive independently of any
scripts/ layout change (see design-proposals/rfe-merge-design.md,
section 17).
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

IDENTITY_FIELDS = {"reporter", "assignee"}
TRACKED_EDITABILITY_FIELDS = ["reporter", "assignee", "priority", "labels", "components", "parent"]


def is_identity_field(field_id: str, schema: dict[str, Any] | None) -> bool:
    """True for known identity fields or any field whose Jira schema is user-valued.

    Field-ID matching alone misses custom fields -- e.g. a transition
    screen can require a "duplicate owner" picker on some
    customfield_NNNNN, which carries real user objects under a schema
    type of "user" (or an array with items "user"). Checking the schema
    closes that path generically instead of relying on a name allowlist.
    """
    if field_id in IDENTITY_FIELDS:
        return True
    schema = schema or {}
    return schema.get("type") == "user" or schema.get("items") == "user"


def request_json(server: str, user: str, token: str, path: str) -> Any:
    credentials = base64.b64encode(f"{user}:{token}".encode()).decode()
    request = urllib.request.Request(
        f"{server.rstrip('/')}{path}",
        headers={
            "Authorization": f"Basic {credentials}",
            "Accept": "application/json",
        },
        method="GET",
    )

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw_body = response.read() or b"null"
            try:
                parsed = json.loads(raw_body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                # A 200 with a non-JSON (or non-UTF-8) body happens -- e.g.
                # an SSO proxy returning an HTML login page instead of an
                # API response. Fail this one call, not the whole probe.
                return {
                    "_error": {
                        "status": response.status,
                        "reason": "invalid_json_response",
                    }
                }
            if not isinstance(parsed, dict):
                # Valid JSON that isn't an object (null, a bare list or
                # string) would otherwise reach a summarizer's .get() call
                # and crash it. Every endpoint this script calls returns an
                # object; anything else is itself the interesting fact.
                return {
                    "_error": {
                        "status": response.status,
                        "reason": "unexpected_json_shape",
                    }
                }
            return parsed
    except urllib.error.HTTPError as exc:
        exc.read()  # drain the body; its content is never surfaced (see below)
        return {
            "_error": {
                "status": exc.code,
                "reason": str(exc.reason),
                # Deliberately not Jira's errorMessages/errors: those can
                # echo request content (a submitted field value, a partial
                # identifier) back into what's meant to be a shareable
                # report. Status + reason is all any merge-design question
                # needs; anything more detailed belongs in local stderr
                # under an explicit debug flag, never in the JSON output.
            }
        }
    except urllib.error.URLError as exc:
        # Not str(exc.reason): the underlying OSError/SSLError's message can
        # include a hostname (e.g. an SSL hostname-mismatch error literally
        # names it). A static classification plus the exception class is
        # enough to diagnose a network problem without repeating it.
        return {
            "_error": {
                "reason": "network_error",
                "exception_type": type(exc.reason).__name__,
            }
        }


def summarize_link(link: dict[str, Any]) -> dict[str, Any]:
    """Reduce an issuelinks entry to type/direction only -- no other-issue key.

    Jira's linked-issue view nests a "fields" object on the other side
    (summary, status, priority, and whatever else that project exposes)
    -- summary text can contain a customer name, and there is no
    guarantee it never nests a user object, so none of "fields" is
    carried over. The other issue's key is dropped too: a project
    prefix alone (e.g. a partner-named project) can reveal a business
    relationship this report has no business repeating, and a
    capability-profile question ("which link types appear, how often")
    only needs type and direction, never which specific ticket.
    """
    if "outwardIssue" in link:
        direction = "outward"
    elif "inwardIssue" in link:
        direction = "inward"
    else:
        direction = "unknown"
    link_type = link.get("type") or {}
    return {
        "type_id": link_type.get("id"),
        "type_name": link_type.get("name"),
        "direction": direction,
    }


def summarize_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """Build an issue summary field-by-field from an explicit allowlist.

    Deliberately not a redacted copy of Jira's response: a copy still
    carries every nested object we didn't think to blank out (a linked
    issue's own assignee, a parent's summary text, a label naming a
    customer). Constructing the output from scratch means there is
    nothing to leak that wasn't explicitly put there.
    """
    if "_error" in issue:
        return issue

    fields = issue.get("fields") or {}
    status = fields.get("status") or {}
    resolution = fields.get("resolution") or {}

    return {
        "key": issue.get("key"),
        "status": status.get("name"),
        "status_category": (status.get("statusCategory") or {}).get("name"),
        "resolution": resolution.get("name"),
        "reporter_present": fields.get("reporter") is not None,
        "assignee_present": fields.get("assignee") is not None,
        "parent_present": fields.get("parent") is not None,
        "component_count": len(fields.get("components") or []),
        "label_count": len(fields.get("labels") or []),
        "links": [summarize_link(link) for link in fields.get("issuelinks") or []],
    }


SAFE_ENUM_FIELDS = {"priority", "resolution"}


def summarize_link_types(payload: dict[str, Any]) -> Any:
    """Purpose-built summary of GET /issueLinkType -- id/inward/outward/name only.

    The raw response's "self" URL embeds the Jira instance's hostname,
    which is exactly what dropping "server" from the top-level output was
    meant to avoid -- passing this response through unfiltered would leak
    it right back in. Nothing else in the raw response (self URLs aside)
    is needed to answer a merge-design question either.
    """
    if "_error" in payload:
        return payload
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "inward": item.get("inward"),
            "outward": item.get("outward"),
        }
        for item in payload.get("issueLinkTypes") or []
    ]


def summarize_resolutions(payload: dict[str, Any]) -> Any:
    """Purpose-built summary of GET /resolution/search -- id/name/isDefault only.

    Drops "self" (instance hostname) and "description" (free text not
    needed for capability-profile questions).
    """
    if "_error" in payload:
        return payload
    return [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "is_default": item.get("isDefault"),
        }
        for item in payload.get("values") or []
    ]


def summarize_enum_values(values: list) -> list[dict[str, Any]]:
    """Full id/name list for a small, meaningful, non-sensitive enum.

    Reserved for fields on SAFE_ENUM_FIELDS (priority, resolution) --
    Jira-defined vocabularies with a handful of values, not free-text or
    org-specific taxonomy. Unlike opaque fields, enumerating these in full
    is exactly what a merge-design question needs (e.g. "is Duplicate an
    allowed resolution").
    """
    return [{"id": value.get("id"), "name": value.get("name")} for value in values]


def summarize_opaque_field_values(values: list) -> dict[str, Any]:
    """Count only -- never names -- for any field not on SAFE_ENUM_FIELDS.

    Components, labels, parent, and any unrecognized custom field can
    carry organization- or customer-specific vocabulary (a component named
    after a customer, a custom picklist option that names one). Unlike the
    small Jira-defined enums, there's no way to know in advance that a
    given option value is safe to reproduce, so none of them are.
    """
    return {"count": len(values)}


def summarize_field_values(field_id: str, values: list) -> Any:
    if field_id in SAFE_ENUM_FIELDS:
        return summarize_enum_values(values)
    return summarize_opaque_field_values(values)


def summarize_field_editability(editmeta: dict[str, Any], field_id: str) -> dict[str, Any]:
    field = (editmeta.get("fields") or {}).get(field_id)
    if not field:
        return {"present_in_editmeta": False, "required": None}

    summary: dict[str, Any] = {
        "present_in_editmeta": True,
        "required": field.get("required"),
        "schema_type": (field.get("schema") or {}).get("type"),
    }

    # Identity fields: never surface allowedValues, which for a user picker
    # is a list of real user objects (name, email, accountId). A boolean is
    # all any merge-design question needs. Everything else gets the same
    # enum-vs-opaque split as summarize_transitions below.
    if is_identity_field(field_id, field.get("schema")):
        summary["allowed_values_available"] = bool(field.get("allowedValues"))
    else:
        allowed = field.get("allowedValues") or []
        summary["allowed_values"] = summarize_field_values(field_id, allowed)

    return summary


def summarize_editability(editmeta: dict[str, Any]) -> dict[str, Any]:
    if "_error" in editmeta:
        return editmeta

    return {
        field_id: summarize_field_editability(editmeta, field_id)
        for field_id in TRACKED_EDITABILITY_FIELDS
    }


def summarize_transitions(payload: dict[str, Any]) -> Any:
    if "_error" in payload:
        return payload

    results = []
    for transition in payload.get("transitions") or []:
        fields = transition.get("fields") or {}
        transition_fields = {}
        for field_id, metadata in fields.items():
            # No "name": a transition screen can require an admin-defined
            # custom field (e.g. one literally called "Customer X
            # Approver"), and the field ID plus schema type already answer
            # every capability question this probe exists for. Same reason
            # the full "schema" object isn't kept -- schema.custom is a
            # fully-qualified plugin class name that can itself name a
            # third-party vendor or internal tool.
            schema = metadata.get("schema") or {}
            entry: dict[str, Any] = {
                "required": metadata.get("required"),
                "schema_type": schema.get("type"),
                "schema_items": schema.get("items"),
                "schema_system": schema.get("system"),
            }
            if is_identity_field(field_id, schema):
                entry["allowed_values_available"] = bool(metadata.get("allowedValues"))
            else:
                allowed = metadata.get("allowedValues") or []
                entry["allowed_values"] = summarize_field_values(field_id, allowed)
            transition_fields[field_id] = entry

        results.append(
            {
                "id": transition.get("id"),
                "name": transition.get("name"),
                "target_status": (transition.get("to") or {}).get("name"),
                "has_screen": transition.get("hasScreen"),
                "is_available": transition.get("isAvailable"),
                "is_conditional": transition.get("isConditional"),
                "fields": transition_fields,
            }
        )

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "issue_keys",
        nargs="+",
        help="Representative Jira issues, preferably covering multiple statuses",
    )
    parser.add_argument(
        "--output",
        default="merge-capability-probe.json",
        help="Output JSON path",
    )
    args = parser.parse_args()

    server = os.environ.get("JIRA_SERVER")
    user = os.environ.get("JIRA_USER")
    token = os.environ.get("JIRA_TOKEN")

    if not all((server, user, token)):
        print(
            "Set JIRA_SERVER, JIRA_USER, and JIRA_TOKEN before running.",
            file=sys.stderr,
        )
        return 1

    output: dict[str, Any] = {
        "probe_version": 6,
        "read_only": True,
        "identity_redacted": True,
        # Deliberately no "server" key: a Jira URL is low-risk for a public
        # Cloud site, but this script isn't specific to one, and a shareable
        # report doesn't need to name the instance it came from.
        "global": {},
        "issues": {},
    }

    output["global"]["issue_link_types"] = summarize_link_types(
        request_json(server, user, token, "/rest/api/3/issueLinkType")
    )
    output["global"]["resolutions"] = summarize_resolutions(
        request_json(server, user, token, "/rest/api/3/resolution/search?maxResults=100")
    )

    for key in args.issue_keys:
        encoded_key = urllib.parse.quote(key, safe="")

        issue = request_json(
            server,
            user,
            token,
            (
                f"/rest/api/3/issue/{encoded_key}"
                "?fields=status,resolution,reporter,assignee,issuelinks,parent,labels,components"
            ),
        )

        transitions_raw = request_json(
            server,
            user,
            token,
            f"/rest/api/3/issue/{encoded_key}/transitions?expand=transitions.fields",
        )

        editmeta_raw = request_json(
            server, user, token, f"/rest/api/3/issue/{encoded_key}/editmeta"
        )

        output["issues"][key] = {
            "issue": summarize_issue(issue),
            "transitions": summarize_transitions(transitions_raw),
            "editability": summarize_editability(editmeta_raw),
        }

    with open(args.output, "w", encoding="utf-8") as stream:
        json.dump(output, stream, indent=2, sort_keys=True)

    print(f"Wrote identity-redacted, read-only capability report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
