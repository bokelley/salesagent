"""Unit tests for the source-first signal authoring form validation.

The validator translates picker-emitted form payloads into the existing
``adapter_config`` shapes (passthrough for one entity, composed AND for
many). Coverage targets each source kind + the advanced JSON escape
hatch + the failure modes operators are most likely to hit.
"""

from __future__ import annotations

import json

from werkzeug.datastructures import MultiDict

from src.admin.blueprints.tenant_signals import _validate_form


def _form(**fields) -> MultiDict:
    return MultiDict(fields)


class TestStructuredAudienceSegment:
    def test_single_segment_emits_passthrough(self):
        _, errors, parsed = _validate_form(
            _form(
                name="Sports fans",
                source_kind="gam_audience_segment",
                entities=json.dumps([{"segment_id": "98765"}]),
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["adapter_config"] == {
            "type": "passthrough",
            "kind": "audience_segment",
            "segment_id": "98765",
            "mode": "include",
        }
        assert parsed["value_type"] == "binary"
        assert parsed["name"] == "Sports fans"

    def test_multiple_segments_compose_to_and(self):
        _, errors, parsed = _validate_form(
            _form(
                name="Sports AND finance",
                source_kind="gam_audience_segment",
                entities=json.dumps([{"segment_id": "111"}, {"segment_id": "222"}]),
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["adapter_config"]["type"] == "composed"
        assert len(parsed["adapter_config"]["criteria"]) == 2
        assert parsed["adapter_config"]["criteria"][0]["segment_id"] == "111"
        assert parsed["adapter_config"]["criteria"][1]["segment_id"] == "222"

    def test_missing_segment_id_rejected(self):
        _, errors, _ = _validate_form(
            _form(
                name="Bad",
                source_kind="gam_audience_segment",
                entities=json.dumps([{"_display": "missing id"}]),
            ),
            mode="add",
        )
        assert "entities" in errors
        assert "segment_id" in errors["entities"]


class TestStructuredCustomKeyValue:
    def test_single_kv_pair(self):
        _, errors, parsed = _validate_form(
            _form(
                name="Sports section",
                source_kind="gam_custom_key_value",
                entities=json.dumps([{"key_id": "11111", "value_id": "22222"}]),
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["adapter_config"] == {
            "type": "passthrough",
            "kind": "custom_key_value",
            "key_id": "11111",
            "value_id": "22222",
            "mode": "include",
        }

    def test_freeform_values_populate_categories(self):
        _, errors, parsed = _validate_form(
            _form(
                name="Custom section",
                source_kind="gam_custom_key_value",
                entities=json.dumps([{"key_id": "33333", "value_id": "freeform_val"}]),
                freeform_values="alpha, beta, gamma",
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["categories"] == ["alpha", "beta", "gamma"]


class TestStructuredFreewheel:
    def test_fw_viewership_profile(self):
        _, errors, parsed = _validate_form(
            _form(
                name="FW male 18-34",
                source_kind="fw_viewership_profile",
                entities=json.dumps([{"profile_id": 4711}]),
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["adapter_config"]["kind"] == "freewheel_viewership_profile"
        assert parsed["adapter_config"]["profile_id"] == 4711

    def test_fw_audience_item(self):
        _, errors, parsed = _validate_form(
            _form(
                name="FW audience X",
                source_kind="fw_audience_item",
                entities=json.dumps([{"item_id": 9876}]),
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["adapter_config"]["kind"] == "freewheel_audience_item"
        assert parsed["adapter_config"]["item_id"] == 9876

    def test_fw_custom_kv(self):
        _, errors, parsed = _validate_form(
            _form(
                name="FW genre sports",
                source_kind="fw_custom_kv",
                entities=json.dumps([{"key": "genre", "value_id": "sports"}]),
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["adapter_config"]["kind"] == "freewheel_custom_kv"
        assert parsed["adapter_config"]["key"] == "genre"


class TestGamComplexTargeting:
    """The TargetingWidget hands a groups payload to the form; the
    validator passes it through as ``adapter_config`` with kind
    ``gam_targeting_groups`` (consumed by the GAM materializer)."""

    def test_single_group_single_criterion(self):
        payload = {
            "key_value_pairs": {
                "groups": [
                    {"criteria": [{"keyId": "11111", "values": ["22222"]}]},
                ]
            }
        }
        _, errors, parsed = _validate_form(
            _form(
                name="Complex KV",
                source_kind="gam_complex_targeting",
                entities=json.dumps(payload),
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["adapter_config"] == {
            "type": "passthrough",
            "kind": "gam_targeting_groups",
            "groups": payload["key_value_pairs"]["groups"],
        }
        assert parsed["value_type"] == "binary"

    def test_multi_group_with_multi_value_and_exclude(self):
        payload = {
            "key_value_pairs": {
                "groups": [
                    {
                        "criteria": [
                            {"keyId": "11111", "values": ["22222", "33333"]},
                            {"keyId": "44444", "values": ["55555"], "exclude": True},
                        ]
                    },
                    {"criteria": [{"keyId": "66666", "values": ["77777"]}]},
                ]
            }
        }
        _, errors, parsed = _validate_form(
            _form(
                name="Complex multi-group",
                source_kind="gam_complex_targeting",
                entities=json.dumps(payload),
            ),
            mode="add",
        )
        assert errors == {}
        assert parsed["adapter_config"]["kind"] == "gam_targeting_groups"
        assert len(parsed["adapter_config"]["groups"]) == 2

    def test_empty_groups_rejected(self):
        _, errors, _ = _validate_form(
            _form(
                name="Empty",
                source_kind="gam_complex_targeting",
                entities=json.dumps({"key_value_pairs": {"groups": []}}),
            ),
            mode="add",
        )
        assert "entities" in errors

    def test_criterion_missing_values_rejected(self):
        _, errors, _ = _validate_form(
            _form(
                name="No values",
                source_kind="gam_complex_targeting",
                entities=json.dumps(
                    {"key_value_pairs": {"groups": [{"criteria": [{"keyId": "11111", "values": []}]}]}}
                ),
            ),
            mode="add",
        )
        assert "entities" in errors

    def test_criterion_missing_key_rejected(self):
        _, errors, _ = _validate_form(
            _form(
                name="No key",
                source_kind="gam_complex_targeting",
                entities=json.dumps({"key_value_pairs": {"groups": [{"criteria": [{"values": ["X"]}]}]}}),
            ),
            mode="add",
        )
        assert "entities" in errors


class TestValidationErrors:
    def test_missing_source_kind(self):
        _, errors, _ = _validate_form(
            _form(name="No source"),
            mode="add",
        )
        assert "source_kind" in errors

    def test_unknown_source_kind(self):
        _, errors, _ = _validate_form(
            _form(name="N", source_kind="not_a_real_kind", entities="[]"),
            mode="add",
        )
        assert "source_kind" in errors
        assert "not_a_real_kind" in errors["source_kind"]

    def test_empty_entities_list(self):
        _, errors, _ = _validate_form(
            _form(name="N", source_kind="gam_audience_segment", entities="[]"),
            mode="add",
        )
        assert "entities" in errors

    def test_missing_name(self):
        _, errors, _ = _validate_form(
            _form(
                source_kind="gam_audience_segment",
                entities=json.dumps([{"segment_id": "1"}]),
            ),
            mode="add",
        )
        assert "name" in errors


class TestAdvancedJsonPath:
    """Edit mode preserves the JSON textarea for round-tripping legacy rows."""

    def test_advanced_passthrough_accepted(self):
        adapter_config = {"type": "passthrough", "kind": "audience_segment", "segment_id": "X"}
        _, errors, parsed = _validate_form(
            _form(
                name="Legacy row",
                authoring_mode="advanced",
                value_type="binary",
                adapter_config=json.dumps(adapter_config),
            ),
            mode="edit",
        )
        assert errors == {}
        assert parsed["adapter_config"] == adapter_config

    def test_advanced_rejects_non_object_adapter_config(self):
        _, errors, _ = _validate_form(
            _form(
                name="Bad",
                authoring_mode="advanced",
                value_type="binary",
                adapter_config='["array", "not", "object"]',
            ),
            mode="edit",
        )
        assert "adapter_config" in errors

    def test_advanced_rejects_bad_value_type(self):
        _, errors, _ = _validate_form(
            _form(
                name="Bad",
                authoring_mode="advanced",
                value_type="not_a_value_type",
                adapter_config="{}",
            ),
            mode="edit",
        )
        assert "value_type" in errors
