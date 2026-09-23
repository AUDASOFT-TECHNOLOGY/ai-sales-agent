"""Settings documents: what may be stored, and everything that must hold before it becomes a version."""

import pytest
import yaml
from app_support import client_yaml

from sales_app.config import extract_document, import_document, validate_settings_document
from sales_app.config.resolution import (
    SETTINGS_SCHEMA_VERSION,
    check_document,
    merge_document,
    strip_settings,
)
from sales_core.approval import ApprovalMode


def wiring(cid="alpha", **overrides):
    return client_yaml(cid, **overrides)


def document(**overrides):
    base = {
        "schema_version": SETTINGS_SCHEMA_VERSION,
        "display_name": "Alpha",
        "timezone": "Africa/Tunis",
        "sheets": {"spreadsheet_id": "sheet-alpha"},
        "llm": {"model": "test-model"},
        "brain": {"settings": {"tone": "warm"}},
    }
    base.update(overrides)
    return base


def check(doc, cid="alpha", **wiring_overrides):
    return validate_settings_document(cid, wiring(cid, **wiring_overrides), doc)


class TestValidDocuments:
    def test_a_minimal_document_is_normalized_with_every_default_written_out(self):
        result = check(document())
        assert result.errors == ()
        assert result.document == {
            "schema_version": 1,
            "display_name": "Alpha",
            "timezone": "Africa/Tunis",
            "mode": "dry_run",  # the safe default, now explicit in the history
            "daily_action_limit": 20,
            "opt_out_keywords": ["stop"],
            "sheets": {
                "spreadsheet_id": "sheet-alpha",
                "prospects_tab": "Prospects",
                "actions_tab": "Actions",
                "messages_tab": "Messages",
                "log_tab": "Log",
                "column_mapping": {},
            },
            "llm": {"model": "test-model"},
            "schedule": {"daily_at": "09:00", "days": ["mon", "tue", "wed", "thu", "fri", "sat"]},
            "brain": {"settings": {"tone": "warm"}},
        }

    def test_normalizing_twice_changes_nothing(self):
        once = check(document()).document
        assert check(once).document == once

    def test_the_normalized_document_is_plain_json(self):
        import json

        assert json.loads(json.dumps(check(document()).document)) == check(document()).document

    def test_client_settings_can_be_set(self):
        result = check(
            document(
                mode="approval",
                daily_action_limit=35,
                opt_out_keywords=["stop", "arret"],
                schedule={"daily_at": "07:30", "days": ["mon", "sun"]},
                sheets={"spreadsheet_id": "s", "prospects_tab": "Leads", "column_mapping": {}},
            )
        )
        assert result.errors == ()
        assert result.spec.mode is ApprovalMode.APPROVAL and result.spec.daily_action_limit == 35
        assert result.document["schedule"] == {"daily_at": "07:30", "days": ["mon", "sun"]}
        assert result.document["sheets"]["prospects_tab"] == "Leads"

    def test_brain_settings_are_opaque_and_kept_as_they_are(self):
        nested = {"offers": [{"name": "x", "price": 10}], "max_tokens": 300, "tone": "warm"}
        result = check(document(brain={"settings": nested}))
        assert result.errors == () and result.document["brain"]["settings"] == nested

    def test_operator_wiring_is_not_needed_in_the_document_but_is_still_validated(self):
        result = check(document(), whatsapp={"token_env": "not valid!"})
        assert any(e.startswith("whatsapp.token_env") for e in result.errors)


class TestWhatIsNotASetting:
    @pytest.mark.parametrize(
        "extra",
        [
            {"client_id": "alpha"},
            {"enabled": True},
            {"max_attempts": 5},
            {"whatsapp": {"token_env": "X_TOKEN"}},
            {"unknown": 1},
        ],
    )
    def test_operator_owned_or_unknown_top_level_keys_are_refused(self, extra):
        result = check(document(**extra))
        assert result.document is None
        assert any("is not a client setting" in e for e in result.errors)

    @pytest.mark.parametrize(
        "section, key",
        [
            ("brain", "factory"),
            ("sheets", "provider"),
            ("sheets", "credentials_env"),
            ("llm", "provider"),
            ("llm", "api_key_env"),
            ("schedule", "dispatch_every_minutes"),
            ("schedule", "prepare_every_minutes"),
        ],
    )
    def test_operator_owned_keys_inside_sections_are_refused(self, section, key):
        doc = document()
        doc[section] = {**doc.get(section, {}), key: "x"}
        result = check(doc)
        assert result.document is None
        assert [e for e in result.errors if e.startswith(f"{section}.{key}:")]

    def test_a_document_cannot_point_the_agent_at_other_credentials(self):
        doc = document(sheets={"spreadsheet_id": "s", "credentials_env": "OTHER_CLIENT_KEY"})
        assert check(doc).document is None

    @pytest.mark.parametrize("bad", [None, [], "text", 3])
    def test_a_document_must_be_an_object(self, bad):
        assert check(bad).errors == ("document must be a JSON object",)

    @pytest.mark.parametrize("version", [None, 0, 2, "1", True])
    def test_the_schema_version_must_be_the_supported_one(self, version):
        doc = document()
        if version is None:
            del doc["schema_version"]
        else:
            doc["schema_version"] = version
        assert any(e.startswith("schema_version:") for e in check(doc).errors)

    def test_a_section_must_be_an_object(self):
        assert any(e.startswith("sheets:") for e in check(document(sheets="x")).errors)


class TestSecretsStayOutOfDocuments:
    @pytest.mark.parametrize(
        "key",
        ["api_key", "apiKey", "openai-api-key", "password", "Token", "access_token", "secret",
         "client_secret", "private_key", "credentials", "whatsapp_token"],
    )  # fmt: skip
    def test_keys_that_look_like_secrets_are_refused_anywhere(self, key):
        result = check(document(brain={"settings": {"nested": [{key: "abc"}]}}))
        assert result.document is None
        assert any("looks like a secret" in e and key in e for e in result.errors)

    @pytest.mark.parametrize(
        "key", ["max_tokens", "tokens_per_day", "keyword", "tone", "monkey", "secretary_name"]
    )
    def test_ordinary_keys_that_merely_contain_such_letters_are_fine(self, key):
        assert check(document(brain={"settings": {key: 1}})).errors == ()

    def test_the_path_of_the_offending_key_is_reported(self):
        result = check(document(brain={"settings": {"crm": {"api_key": "x"}}}))
        assert any(e.startswith("brain.settings.crm.api_key:") for e in result.errors)


class TestValuesAreValidatedByTheClientSchema:
    def test_every_problem_is_reported_at_once(self):
        result = check(
            document(
                display_name="  ",
                timezone="Mars/Olympus",
                daily_action_limit=0,
                mode="yolo",
                schedule={"daily_at": "9am"},
                sheets={"spreadsheet_id": "TODO"},
                llm={"model": "CHANGE_ME"},
            )
        )
        located = {e.split(":")[0] for e in result.errors}
        assert located >= {
            "display_name",
            "timezone",
            "daily_action_limit",
            "mode",
            "schedule.daily_at",
            "sheets.spreadsheet_id",
            "llm.model",
        }
        assert result.spec is None and result.document is None

    def test_required_settings_cannot_be_left_out(self):
        doc = document()
        del doc["display_name"], doc["timezone"]
        del doc["sheets"], doc["llm"]
        located = {e.split(":")[0] for e in check(doc).errors}
        assert located == {"display_name", "timezone", "sheets.spreadsheet_id", "llm.model"}

    def test_json_that_is_not_a_document_is_refused(self):
        result = check(document(brain={"settings": {"when": {1, 2}}}))
        assert result.document is None and any(e.startswith("document:") for e in result.errors)

    def test_the_client_id_of_the_wiring_must_match(self):
        result = validate_settings_document("beta", wiring("alpha"), document())
        assert result.errors == ("client_id 'alpha' must equal the directory name",)


class TestMergingWithWiring:
    def test_settings_in_client_yaml_never_leak_into_a_stored_document(self):
        legacy = wiring(daily_action_limit=7, opt_out_keywords=["quit"])
        result = validate_settings_document("alpha", legacy, document())  # omits both keys
        assert result.document["daily_action_limit"] == 20  # the default, not the yaml's 7
        assert result.document["opt_out_keywords"] == ["stop"]

    def test_strip_settings_keeps_only_the_operator_wiring(self):
        stripped = strip_settings(wiring(daily_action_limit=7))
        assert set(stripped) == {"client_id", "enabled", "brain", "sheets", "llm", "whatsapp"}
        assert stripped["brain"] == {"factory": "app_test_brains:good_brain"}
        assert stripped["sheets"] == {} and stripped["llm"] == {}

    def test_merge_puts_document_values_over_the_wiring_and_leaves_the_input_alone(self):
        base = strip_settings(wiring())
        before = repr(base)
        merged = merge_document(base, document(mode="approval"))
        assert merged["mode"] == "approval" and merged["sheets"]["spreadsheet_id"] == "sheet-alpha"
        assert merged["brain"]["factory"] == "app_test_brains:good_brain"
        assert repr(base) == before

    def test_check_document_alone_needs_no_wiring(self):
        assert check_document(document()) == []


class TestImportFromClientYaml:
    def test_a_full_client_yaml_yields_its_settings_document(self):
        result = import_document("alpha", wiring(daily_action_limit=12))
        assert result.errors == ()
        assert result.document["daily_action_limit"] == 12
        assert result.document == extract_document(result.spec)
        assert "whatsapp" not in result.document and "factory" not in str(result.document)

    def test_an_invalid_client_yaml_is_reported(self):
        assert import_document("alpha", wiring(timezone="Mars/Olympus")).errors
        assert import_document("beta", wiring("alpha")).errors

    def test_a_wiring_only_file_cannot_be_imported(self):
        raw = yaml.safe_load(yaml.safe_dump(strip_settings(wiring())))
        assert any("display_name" in e for e in import_document("alpha", raw).errors)
