"""
JTBD schema extension for artifact_utils.py.

This module defines the schema additions for JTBD integration.
Import and merge these into the main SCHEMAS dict in artifact_utils.py.

Usage:
    from jtbd_schema import JTBD_TASK_FIELDS, JTBD_REVIEW_FIELDS, compute_jtbd_composite
"""

# -------------------------------------------------------------------
# Addition to SCHEMAS['rfe-task']['fields']
# Merge alongside existing fields: priority, status, labels, etc.
# -------------------------------------------------------------------

JTBD_TASK_FIELDS = {
    "jtbd_mapping": {
        "type": "object",
        "required": False,
        "fields": {
            "jobs": {
                "type": "list",
                "required": False,
                "items": {
                    "type": "object",
                    "fields": {
                        "id": {
                            "type": "string",
                            "required": True,
                            "pattern": r"^[a-z]+\.[a-z_]+$",
                        },
                        "name": {
                            "type": "string",
                            "required": True,
                        },
                        "opportunity_score": {
                            "type": "float",
                            "required": False,
                        },
                        "lifecycle_phase": {
                            "type": "string",
                            "required": False,
                            "enum": ["build", "deploy", "production"],
                        },
                    },
                },
            },
            "personas": {
                "type": "list",
                "required": False,
                "items": {
                    "type": "string",
                    "enum": ["dana", "alex", "maldi"],
                },
            },
            "confidence": {
                "type": "string",
                "required": True,
                "enum": ["high", "medium", "low", "none"],
            },
        },
    },
}


# -------------------------------------------------------------------
# Addition to SCHEMAS['rfe-review']['fields']['scores']
# Merge alongside existing: what, why, open_to_how, not_a_task, right_sized
# -------------------------------------------------------------------

JTBD_REVIEW_FIELDS = {
    "jtbd_alignment": {
        "type": "integer",
        "required": False,
        "nullable": True,
        "min": 0,
        "max": 2,
    },
}


# -------------------------------------------------------------------
# Dimension definitions for documentation and validation
# -------------------------------------------------------------------

JTBD_DIMENSIONS = {
    "job_mapping": {
        "label": "Job Mapping Validity",
        "scores": {
            0: "No mapping exists or mapping is clearly wrong",
            1: "Plausible job but loose fit; tangential scope or unacknowledged overlap",
            2: "Correct job(s); capability directly addresses job statement",
        },
    },
    "evidence_utilization": {
        "label": "Evidence Utilization",
        "scores": {
            0: "No research evidence cited; WHY relies on assertion/anecdote",
            1: "Some evidence but incomplete; generic use or disconnected from argument",
            2: "Evidence well-integrated; pain points, scores, quotes in direct support",
        },
    },
    "persona_task_coherence": {
        "label": "Persona-Task Coherence",
        "scores": {
            0: "No persona or persona contradicts JTBD mapping",
            1: "Persona plausible but underdeveloped; no workflow context",
            2: "Persona validated, maps to workflow, capability fits naturally",
        },
    },
    "opportunity_justification": {
        "label": "Opportunity Justification",
        "scores": {
            0: "No opportunity data or data contradicts priority",
            1: "Opportunity referenced but connection to priority implicit",
            2: "Priority earned by data; investment proportional to research signal",
        },
    },
}


def compute_jtbd_composite(dimensions: dict) -> int | None:
    """
    Compute composite JTBD alignment score from per-dimension scores.

    Args:
        dimensions: dict with keys matching JTBD_DIMENSIONS,
                    each mapping to int (0, 1, or 2) or None

    Returns:
        0 if any dimension is 0
        2 if all dimensions are 2
        1 otherwise
        None if any dimension is None (not applicable)
    """
    scores = [
        dimensions.get("job_mapping"),
        dimensions.get("evidence_utilization"),
        dimensions.get("persona_task_coherence"),
        dimensions.get("opportunity_justification"),
    ]

    if any(s is None for s in scores):
        return None
    if any(s == 0 for s in scores):
        return 0
    if all(s == 2 for s in scores):
        return 2
    return 1
