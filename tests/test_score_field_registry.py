#!/usr/bin/env python3
"""Pin the per-type score/criterion field names across every registry that restates them.

The rubric emits one set of score keys per pipeline type. Four places in this
repo independently hard-code that same set:

  1. artifact_utils.SCHEMAS["<type>-review"]["scores"]["fields"]
  2. artifact_utils.SCHEMAS["<type>-review"]["before_scores"]["fields"]
  3. generate_run_report.TYPE_CONFIG["<type>"]["score_fields"]
  4. generate_review_pdf.REPORT_CONFIG["<type>"]["criterion_keys"]
  5. types/<type>/type.yaml schema.review.score_fields (the descriptor — inert
     today, the single source once the scripts read the registry; PR1-08)

Nothing enforces agreement. The keys are read straight out of review
frontmatter with .get(), so a mismatch does not raise — the report renders
every criterion as 0 and the run looks like a total rubric failure. That is
exactly how the initiative PDF shipped with the RFE key set.

Order is deliberately not pinned (it is display order, and the two consumers
may legitimately differ); membership is.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import type_registry  # noqa: E402
from artifact_utils import SCHEMAS  # noqa: E402
from generate_review_pdf import REPORT_CONFIG  # noqa: E402
from generate_run_report import TYPE_CONFIG  # noqa: E402
from pipeline_state import PIPELINE_TYPES  # noqa: E402

# Hermetic: the developer's RFE_CREATOR_EXTRA_TYPES / RFE_CREATOR_BINDING_* never leak in.
REGISTRY = type_registry.load(extra_roots=[], env={})

# The contract with the rubric in assess-rfe. Sources 1-4 above can be renamed
# in lockstep and still be wrong if the rubric was not renamed with them, so
# the agreed names are also pinned literally here.
EXPECTED_FIELDS = {
    "rfe": ["not_a_task", "open_to_how", "right_sized", "what", "why"],
    "initiative": ["open_to_how", "right_sized", "scope", "what", "why"],
}

TYPES = sorted(EXPECTED_FIELDS)


def _schema_scores(pipeline_type, key="scores"):
    return sorted(SCHEMAS[f"{pipeline_type}-review"][key]["fields"])


@pytest.mark.parametrize("pipeline_type", TYPES)
class TestFieldsAgree:
    def test_schema_matches_rubric_contract(self, pipeline_type):
        assert _schema_scores(pipeline_type) == EXPECTED_FIELDS[pipeline_type]

    def test_before_scores_match_scores(self, pipeline_type):
        """A revised item's before/after must be comparable field-for-field."""
        assert _schema_scores(pipeline_type, "before_scores") == _schema_scores(pipeline_type)

    def test_run_report_score_fields_match_schema(self, pipeline_type):
        actual = sorted(TYPE_CONFIG[pipeline_type]["score_fields"])
        assert actual == _schema_scores(pipeline_type)

    def test_pdf_criterion_keys_match_schema(self, pipeline_type):
        actual = sorted(REPORT_CONFIG[pipeline_type]["criterion_keys"])
        assert actual == _schema_scores(pipeline_type)

    def test_descriptor_score_fields_match_schema(self, pipeline_type):
        """The descriptor restates the set too; it must agree before it becomes the source."""
        assert sorted(REGISTRY.get(pipeline_type).score_fields) == _schema_scores(pipeline_type)


@pytest.mark.parametrize("pipeline_type", TYPES)
class TestPDFLabelsCoverCriteria:
    """An unlabelled criterion renders as a blank column header, not an error."""

    @pytest.mark.parametrize("label_key", ["criterion_labels", "criterion_short_labels"])
    def test_every_criterion_is_labelled(self, pipeline_type, label_key):
        config = REPORT_CONFIG[pipeline_type]
        assert sorted(config[label_key]) == sorted(config["criterion_keys"])

    def test_before_score_name_map_targets_real_keys(self, pipeline_type):
        """Revision history parses names back to keys; a stale target is dropped silently."""
        config = REPORT_CONFIG[pipeline_type]
        keys = set(config["criterion_keys"])
        name_map = config["before_score_name_map"].items()
        stray = {name: key for name, key in name_map if key not in keys}
        assert not stray, f"{pipeline_type} name map points at non-criteria: {stray}"


class TestRegistryCoverage:
    def test_every_pipeline_type_has_every_registry(self):
        """Adding a third type must not silently skip a registry."""
        expected = set(PIPELINE_TYPES)
        assert set(TYPE_CONFIG) == expected
        assert set(REPORT_CONFIG) == expected
        assert set(EXPECTED_FIELDS) == expected
        for pipeline_type in expected:
            assert f"{pipeline_type}-review" in SCHEMAS

    def test_registry_enumerates_exactly_the_pipeline_types(self):
        """PR1-08: types/<t>/type.yaml exists for every pipeline type and for nothing else."""
        assert set(REGISTRY.names()) == set(PIPELINE_TYPES)
        assert REGISTRY.names() == list(PIPELINE_TYPES)  # same order as today's choices

    def test_types_do_not_share_a_field_set(self):
        """Guards the parametrized tests from passing on a copy-pasted registry."""
        assert EXPECTED_FIELDS["rfe"] != EXPECTED_FIELDS["initiative"]
