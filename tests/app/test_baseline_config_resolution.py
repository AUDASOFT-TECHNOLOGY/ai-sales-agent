"""Baseline (golden) tests: how a client's configuration is read and resolved today.

Pins, for every legacy shape the application can read now (client.yaml with and without
placeholders, stored settings documents v1 and v2, a v2 document whose `llm.model` is the upstream
id), the whole `resolve_client_config` outcome: the validated `spec`, the effective mode, the exact
`missing_credentials` messages, `config_source`, and the credentials resolved. These are the values
a later change to the configuration pipeline must not alter. Nothing here asserts what the
behaviour should be: a value that looks odd is recorded as it is.
"""

import pytest
import yaml
from app_support import SHARED_ENV, deep_merge, env_for, write_client
from baseline_support import (
    assert_golden,
    fixture_json,
    fixture_text,
    place_yaml,
    resolve,
    settings_secret_store,
    snapshot_resolution,
    store_with_document,
)

from sales_app.config import (
    build_llm_registry,
    extract_document,
    import_document,
    load_client_configs,
    validate_settings_document,
)
from sales_app.config.resolution import check_document
from sales_app.secret_store import InMemorySecretStore
from sales_core.approval import ApprovalMode

DRY, APPROVAL, AUTO = ApprovalMode.DRY_RUN, ApprovalMode.APPROVAL, ApprovalMode.AUTONOMOUS
UPSTREAM = "gpt-upstream-1"  # the `llm.model` of the legacy fixtures that carry an upstream id


class TestLegacyClientYaml:
    def test_placeholders_in_an_enabled_client_are_errors(self, tmp_path):
        place_yaml(tmp_path, "pholder", "legacy_with_placeholders.yaml")
        resolution = resolve(tmp_path, "pholder", env=env_for("pholder"))
        assert resolution.config is None and not resolution.disabled
        assert_golden("resolution_legacy_with_placeholders", snapshot_resolution(resolution))

    def test_placeholders_in_a_disabled_client_are_never_validated(self, tmp_path):
        place_yaml(tmp_path, "pholder", "legacy_disabled_placeholders.yaml")
        resolution = resolve(tmp_path, "pholder", env=env_for("pholder"))
        assert resolution.disabled and resolution.config is None and resolution.errors == ()
        assert_golden("resolution_legacy_disabled_placeholders", snapshot_resolution(resolution))

    def test_a_complete_client_with_every_credential(self, tmp_path):
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        resolution = resolve(tmp_path)
        assert resolution.config is not None and resolution.config.ready
        assert_golden("resolution_legacy_complete_full_env", snapshot_resolution(resolution))

    def test_the_deployment_ceiling_clamps_the_mode(self, tmp_path):
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        resolution = resolve(tmp_path, ceiling=DRY)
        assert resolution.config.mode_clamped
        assert_golden("resolution_legacy_complete_dry_run_ceiling", snapshot_resolution(resolution))

    def test_a_dry_run_does_not_need_whatsapp_credentials(self, tmp_path):
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        resolution = resolve(tmp_path, ceiling=DRY, env=SHARED_ENV)
        assert resolution.config.ready
        assert_golden(
            "resolution_legacy_complete_dry_run_no_whatsapp", snapshot_resolution(resolution)
        )

    def test_every_missing_credential_is_listed_with_its_exact_message(self, tmp_path):
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        resolution = resolve(tmp_path, ceiling=APPROVAL, env={})
        assert not resolution.config.ready
        assert_golden("resolution_legacy_complete_empty_env", snapshot_resolution(resolution))

    def test_a_credential_store_value_wins_over_the_environment(self, tmp_path):
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        from sales_app.secret_store import GLOBAL_SCOPE, client_scope

        secrets = InMemorySecretStore()
        secrets.set(GLOBAL_SCOPE, "openai.api_key", "sk-from-store", by="baseline")
        secrets.set(GLOBAL_SCOPE, "openai.organization", "org-1", by="baseline")
        secrets.set(GLOBAL_SCOPE, "openai.project", "proj-1", by="baseline")
        secrets.set(GLOBAL_SCOPE, "openai.base_url", "https://llm.example.com/v1", by="baseline")
        secrets.set(GLOBAL_SCOPE, "whatsapp.api_version", "v23.0", by="baseline")
        secrets.set(GLOBAL_SCOPE, "whatsapp.app_secret", "app-secret-from-store", by="baseline")
        secrets.set(client_scope("alpha"), "whatsapp.access_token", "tok-from-store", by="baseline")
        secrets.set(client_scope("alpha"), "whatsapp.phone_number_id", "99887766", by="baseline")
        resolution = resolve(tmp_path, secret_store=secrets)
        assert_golden(
            "resolution_legacy_complete_store_credentials", snapshot_resolution(resolution)
        )

    @pytest.mark.parametrize(
        ("name", "overrides"),
        [
            ("unknown_key", {"surprise": 1}),
            ("bad_timezone", {"timezone": "Mars/Base"}),
            ("bad_mode", {"mode": "yolo"}),
            ("bad_factory", {"brain": {"factory": "not-a-path"}}),
            ("bad_column_mapping_type", {"sheets": {"column_mapping": ["x"]}}),
            ("limit_out_of_range", {"daily_action_limit": 0}),
        ],
    )
    def test_structural_errors_are_reported_with_their_location(self, tmp_path, name, overrides):
        data = deep_merge(yaml.safe_load(fixture_text("legacy_complete.yaml")), overrides)
        write_client(tmp_path, "alpha", raw=yaml.safe_dump(data))
        resolution = resolve(tmp_path)
        assert resolution.config is None
        assert_golden(f"resolution_error_{name}", snapshot_resolution(resolution))

    def test_the_client_id_must_equal_the_directory_name(self, tmp_path):
        text = fixture_text("legacy_complete.yaml").replace("client_id: alpha", "client_id: other")
        write_client(tmp_path, "alpha", raw=text)
        assert_golden("resolution_error_client_id_mismatch", snapshot_resolution(resolve(tmp_path)))

    def test_a_missing_or_unreadable_client_yaml_is_an_error_not_a_crash(self, tmp_path):
        (tmp_path / "alpha").mkdir()
        missing = snapshot_resolution(resolve(tmp_path))
        write_client(tmp_path, "alpha", raw="[1, 2")
        broken = snapshot_resolution(resolve(tmp_path))
        write_client(tmp_path, "alpha", raw="- just\n- a list\n")
        not_a_mapping = snapshot_resolution(resolve(tmp_path))
        assert_golden(
            "resolution_error_unreadable_client_yaml",
            {"missing": missing, "broken": broken, "not_a_mapping": not_a_mapping},
        )


class TestStoredDocuments:
    """Wiring in client.yaml, settings in a published document (v1, v2, v2 with an upstream id)."""

    @pytest.mark.parametrize(
        "document",
        ["doc_v1.json", "doc_v2.json", "doc_v2_upstream_id.json"],
    )
    def test_each_document_version_resolves(self, tmp_path, document):
        place_yaml(tmp_path, "alpha", "wiring_only.yaml")
        store = store_with_document("alpha", fixture_json(document))
        resolution = resolve(tmp_path, store=store)
        assert resolution.config is not None and resolution.config.config_source == "store"
        assert_golden(
            f"resolution_{document.removesuffix('.json')}", snapshot_resolution(resolution)
        )

    def test_a_v1_document_keeps_todays_inbound_behaviour(self, tmp_path):
        place_yaml(tmp_path, "alpha", "wiring_only.yaml")
        store = store_with_document("alpha", fixture_json("doc_v1.json"))
        inbound = resolve(tmp_path, store=store).config.spec.inbound
        assert inbound.spontaneous_conversations.enabled is True

    def test_a_v2_document_that_says_nothing_gets_the_safe_inbound_defaults(self, tmp_path):
        place_yaml(tmp_path, "alpha", "wiring_only.yaml")
        store = store_with_document("alpha", fixture_json("doc_v2_upstream_id.json"))
        inbound = resolve(tmp_path, store=store).config.spec.inbound
        assert inbound.spontaneous_conversations.enabled is False

    def test_the_store_wins_over_settings_left_in_client_yaml(self, tmp_path):
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        store = store_with_document("alpha", fixture_json("doc_v2.json"))
        config = resolve(tmp_path, store=store).config
        assert config.spec.display_name == "Alpha V2" and config.config_version == 1

    @pytest.mark.parametrize(
        ("name", "document"),
        [
            ("unknown_schema_version", {"schema_version": 9}),
            (
                "operator_key",
                {"schema_version": 2, "mode_ceiling": "x", "brain": {"factory": "a:b"}},
            ),
            ("secret_looking_key", {"schema_version": 2, "llm": {"model": "m", "api_key": "k"}}),
            ("not_an_object", ["x"]),
        ],
    )
    def test_a_bad_stored_document_is_reported_not_applied(self, tmp_path, name, document):
        place_yaml(tmp_path, "alpha", "wiring_only.yaml")
        if isinstance(document, dict):
            resolution = resolve(tmp_path, store=store_with_document("alpha", document))
            assert resolution.config is None
            assert_golden(f"resolution_stored_document_{name}", snapshot_resolution(resolution))
        else:  # a store holds JSON objects only: a non-object is checked at the document level
            assert_golden(f"resolution_stored_document_{name}", {"check": check_document(document)})


class TestModelAvailability:
    """`llm.model` against Admin Settings' model list and the deployment allow-list."""

    def outcome(
        self, tmp_path, *, secret_store=None, llm_registry=None, document="doc_v2_upstream_id.json"
    ):
        place_yaml(tmp_path, "alpha", "wiring_only.yaml")
        store = store_with_document("alpha", fixture_json(document))
        resolution = resolve(
            tmp_path, store=store, secret_store=secret_store, llm_registry=llm_registry
        )
        config = resolution.config
        return {
            "errors": list(resolution.errors),
            "ready": None if config is None else config.ready,
            "missing_credentials": None if config is None else list(config.missing_credentials),
            "llm": None if config is None else config.spec.llm.model_dump(mode="json"),
        }

    def test_the_whole_matrix(self, tmp_path_factory):
        def case(**kwargs):
            return self.outcome(tmp_path_factory.mktemp("case"), **kwargs)

        results = {
            "no_secret_store": case(),
            "no_list_saved": case(secret_store=settings_secret_store()),
            "list_with_the_model_active": case(
                secret_store=settings_secret_store([("GPT Lune", UPSTREAM, True)])
            ),
            "list_with_the_model_inactive": case(
                secret_store=settings_secret_store([("GPT Lune", UPSTREAM, False)])
            ),
            "list_without_the_model": case(
                secret_store=settings_secret_store([("GPT Sol", "another-id", True)])
            ),
            "empty_list": case(secret_store=settings_secret_store([])),
            "legacy_id_matches": case(
                secret_store=settings_secret_store(openai__model_lune=UPSTREAM)
            ),
            "legacy_id_differs": case(
                secret_store=settings_secret_store(openai__model_sol="other")
            ),
            "list_wins_over_legacy_ids": case(
                secret_store=settings_secret_store(
                    [("GPT Lune", UPSTREAM, True)], openai__model_sol="other"
                )
            ),
            "provider_not_on_the_allow_list": case(
                llm_registry=build_llm_registry({"anthropic": [UPSTREAM]})
            ),
            "model_not_on_the_allow_list": case(
                llm_registry=build_llm_registry({"openai": ["something-else"]})
            ),
            "model_on_the_allow_list": case(
                llm_registry=build_llm_registry({"openai": [UPSTREAM]})
            ),
            "empty_allow_list": case(llm_registry=build_llm_registry({})),
            "v1_document_without_a_provider": case(
                document="doc_v1.json", secret_store=settings_secret_store([("M", UPSTREAM, True)])
            ),
        }
        assert_golden("resolution_model_availability", results)


class TestSettingsDocuments:
    """What `config import|publish` write today: the normalised, current-schema document."""

    def test_extract_document_of_a_complete_legacy_client(self, tmp_path):
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        spec = resolve(tmp_path).config.spec
        assert_golden("settings_extract_legacy_complete", extract_document(spec))

    def test_import_document_of_a_legacy_client_yaml(self):
        check = import_document("alpha", yaml.safe_load(fixture_text("legacy_complete.yaml")))
        assert not check.errors
        assert_golden("settings_import_legacy_complete", check.document)

    @pytest.mark.parametrize("name", ["doc_v1", "doc_v2", "doc_v2_upstream_id"])
    def test_validating_a_document_normalises_it_to_the_current_schema(self, name):
        wiring = yaml.safe_load(fixture_text("wiring_only.yaml"))
        check = validate_settings_document("alpha", wiring, fixture_json(f"{name}.json"))
        assert not check.errors, check.errors
        assert_golden(f"settings_validated_{name}", check.document)

    @pytest.mark.parametrize(
        ("name", "document"),
        [
            ("not_an_object", ["x"]),
            ("unknown_schema_version", {"schema_version": 7}),
            ("operator_owned_section_key", {"schema_version": 2, "whatsapp": {"token_env": "X"}}),
            ("operator_owned_brain_factory", {"schema_version": 2, "brain": {"factory": "a:b"}}),
            ("unknown_top_level_key", {"schema_version": 2, "color": "red"}),
            (
                "secret_in_a_nested_key",
                {"schema_version": 2, "brain": {"settings": {"api_key": "k"}}},
            ),
            ("section_not_an_object", {"schema_version": 2, "llm": "gpt"}),
            ("not_json_compatible", {"schema_version": 2, "display_name": object()}),
            ("v1_document_with_a_v2_only_key", {"schema_version": 1, "inbound": {}}),
        ],
    )
    def test_document_structure_errors(self, name, document):
        assert_golden(f"settings_check_{name}", {"errors": check_document(document)})

    def test_value_errors_of_a_document_are_collected(self):
        wiring = yaml.safe_load(fixture_text("wiring_only.yaml"))
        document = {
            "schema_version": 2,
            "display_name": "X",
            "timezone": "Nowhere/Land",
            "sheets": {"spreadsheet_id": "TODO"},
            "llm": {"model": "TODO"},
            "daily_action_limit": 0,
        }
        check = validate_settings_document("alpha", wiring, document)
        assert check.document is None
        assert_golden("settings_validate_value_errors", {"errors": list(check.errors)})


class TestAcrossClients:
    def test_two_clients_sharing_an_account_are_reported(self, tmp_path):
        for client_id in ("alpha", "beta"):
            text = fixture_text("legacy_complete.yaml").replace(
                "client_id: alpha", f"client_id: {client_id}"
            )
            write_client(tmp_path, client_id, raw=text)
        env = {**env_for("alpha", "beta"), "BETA_PHONE_ID": "phone-alpha"}
        loaded = load_client_configs(tmp_path, env=env, mode_ceiling=AUTO)
        assert_golden(
            "resolution_shared_accounts",
            {
                "clients": [c.client_id for c in loaded.clients],
                "disabled": list(loaded.disabled),
                "errors": sorted(loaded.errors),
            },
        )

    def test_discovery_skips_underscore_and_dot_directories_and_lists_disabled(self, tmp_path):
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        place_yaml(tmp_path, "pholder", "legacy_disabled_placeholders.yaml")
        write_client(tmp_path, "_template", enabled=False)
        write_client(tmp_path, ".hidden")
        loaded = load_client_configs(tmp_path, env=env_for("alpha"), mode_ceiling=AUTO)
        assert_golden(
            "resolution_discovery",
            {
                "clients": [c.client_id for c in loaded.clients],
                "disabled": list(loaded.disabled),
                "errors": list(loaded.errors),
            },
        )
