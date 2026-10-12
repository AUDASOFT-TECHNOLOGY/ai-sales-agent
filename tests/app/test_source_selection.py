"""Choosing a prospect source: the schema, the stored document, the descriptor and the runtime guard.

What is proved: a client that never chose a source is exactly what it was (the same dumps and the
same documents); a chosen source is validated, versioned and written only when it differs from the
store's provider; HubSpot is declared but cannot be built; and a runtime is never built on a source
it cannot run, so a client can never silently keep running on the store's provider instead.
"""

import pytest
from app_support import client_yaml, env_for, fake_builders, make_settings, write_client
from baseline_support import resolve
from factories import NOW
from pydantic import ValidationError

from sales_app.config import extract_document, import_document, validate_settings_document
from sales_app.config.normalize import NormalizationError, normalize
from sales_app.config.resolution import SETTINGS_SECTION_KEYS, check_document
from sales_app.config.schema import ClientFile, SourceSection
from sales_app.config.secrets import HUBSPOT_ACCESS_TOKEN
from sales_app.config_store import InMemoryConfigStore
from sales_app.errors import RuntimeBuildError
from sales_app.secret_store import InMemorySecretStore, client_scope
from sales_app.wiring import build_runtime, default_builders
from sales_app.wiring.providers import (
    EmptySettings,
    ProviderKind,
    Requirement,
    default_registry,
    hubspot,
)
from sales_core.approval import ApprovalMode
from sales_integrations.fakes import FakeClock

TOKEN = "pat-eu1-12345678-1234-1234-1234-123456789abc"


def legacy(**overrides):
    return client_yaml("alpha", **overrides)


def document_with(source):
    check = import_document("alpha", legacy())
    assert check.document is not None
    return {**check.document, **({"source": source} if source is not None else {})}


# ---- the schema --------------------------------------------------------------------------------


class TestTheSchema:
    def test_a_client_that_never_chose_dumps_exactly_as_before(self):
        spec = ClientFile.model_validate(legacy())
        assert spec.source is None and "source" not in spec.model_dump(mode="json")
        assert spec.source_provider == spec.sheets.provider == "google_sheets"
        assert spec.source_apart_from_store is False

    def test_a_chosen_source_is_part_of_the_spec(self):
        spec = ClientFile.model_validate(legacy(source={"provider": "hubspot"}))
        assert spec.model_dump(mode="json")["source"] == {"provider": "hubspot"}
        assert (spec.source_provider, spec.source_apart_from_store) == ("hubspot", True)

    def test_the_stores_own_provider_chosen_as_the_source_is_not_apart(self):
        spec = ClientFile.model_validate(legacy(source={"provider": "google_sheets"}))
        assert spec.source_apart_from_store is False

    def test_a_source_named_like_the_store_of_another_provider_follows_the_store(self):
        spec = ClientFile.model_validate(legacy(sheets={"provider": "other_store"}))
        assert spec.source_provider == "other_store" and spec.source_apart_from_store is False

    @pytest.mark.parametrize("bad", ["", "H", "Hub Spot", "hub-spot", "1hubspot", "x" * 33])
    def test_a_provider_key_must_be_well_formed(self, bad):
        with pytest.raises(ValidationError):
            ClientFile.model_validate(legacy(source={"provider": bad}))

    def test_a_source_holds_nothing_but_its_provider(self):
        with pytest.raises(ValidationError):
            SourceSection.model_validate({"provider": "hubspot", "token": "x"})
        with pytest.raises(ValidationError):
            ClientFile.model_validate(legacy(source={"provider": "hubspot", "store": "sheets"}))


# ---- the stored document -----------------------------------------------------------------------


class TestTheStoredDocument:
    def test_the_source_is_a_setting_a_document_may_own_in_the_current_schema_only(self):
        assert SETTINGS_SECTION_KEYS["source"] == ("provider",)
        v1 = {**document_with({"provider": "hubspot"}), "schema_version": 1}
        assert any(e.startswith("source:") for e in check_document(v1))

    def test_nothing_else_may_hide_in_the_source_section(self):
        errors = check_document(document_with({"provider": "hubspot", "token": "x"}))
        assert any("source.token" in e for e in errors)

    def test_a_chosen_source_validates_and_is_written_back_normalised(self):
        check = validate_settings_document(
            "alpha", legacy(), document_with({"provider": "hubspot"})
        )
        assert check.errors == () and check.document["source"] == {"provider": "hubspot"}

    def test_the_stores_provider_is_written_as_nothing_so_documents_keep_their_shape(self):
        for source in (None, {"provider": "google_sheets"}):
            check = validate_settings_document("alpha", legacy(), document_with(source))
            assert check.errors == () and "source" not in check.document

    def test_a_malformed_source_is_refused_with_where_it_is(self):
        check = validate_settings_document("alpha", legacy(), document_with({"provider": "Bad!"}))
        assert check.document is None and any(e.startswith("source.provider") for e in check.errors)

    def test_extracting_a_spec_round_trips_the_choice(self):
        spec = ClientFile.model_validate(legacy(source={"provider": "hubspot"}))
        assert extract_document(spec)["source"] == {"provider": "hubspot"}
        plain = ClientFile.model_validate(legacy())
        assert "source" not in extract_document(plain)

    def test_the_operators_wiring_never_receives_the_clients_choice(self):
        # the choice lives in the versioned document: stripping a client.yaml leaves its own
        from sales_app.config.resolution import strip_settings

        assert strip_settings(legacy(source={"provider": "hubspot"}))["source"] == {}


# ---- the canonical form ------------------------------------------------------------------------


class TestTheCanonicalForm:
    def test_a_source_apart_from_the_store_cannot_be_converted_yet(self):
        with pytest.raises(NormalizationError) as caught:
            normalize(legacy(), document_with({"provider": "hubspot"}))
        assert any("apart from the store" in p for p in caught.value.problems)

    def test_the_stores_own_provider_converts_as_always(self):
        chosen = normalize(legacy(), document_with({"provider": "google_sheets"})).spec
        assert chosen == normalize(legacy(), document_with(None)).spec


# ---- the descriptor -----------------------------------------------------------------------------


class TestTheHubSpotDescriptor:
    descriptor = hubspot.DESCRIPTOR

    def test_it_is_a_source_and_only_a_source(self):
        assert self.descriptor.kind is ProviderKind.SOURCE
        assert self.descriptor.roles == {ProviderKind.SOURCE}
        assert not self.descriptor.can_host_store
        assert [d.key for d in default_registry().list(ProviderKind.STORE)] == ["google_sheets"]

    def test_it_is_declared_but_cannot_be_run(self):
        assert self.descriptor.build is None and not self.descriptor.runnable
        builders = default_builders(make_settings("."))
        assert set(builders.repositories) == {"google_sheets"}

    def test_it_declares_no_setting_yet(self):
        assert self.descriptor.settings_models == {ProviderKind.SOURCE: EmptySettings}

    def test_its_one_credential_is_the_clients_own_secret_and_unique(self):
        (credential,) = self.descriptor.credentials
        assert credential.key == HUBSPOT_ACCESS_TOKEN == "hubspot.access_token"
        assert (credential.scope.value, credential.sensitive, credential.unique) == (
            "client",
            True,
            True,
        )
        assert credential.required_when is Requirement.ALWAYS
        assert self.descriptor.connection_keys == (HUBSPOT_ACCESS_TOKEN,)

    @pytest.mark.parametrize(
        "token",
        [
            "pat-eu1-12345678-1234-1234-1234-123456789abc",
            "pat-na1-ABCDEF01-aaaa-BBBB-cccc-1234567890ab",
            "pat-ap1-12345678-1234-1234-1234-123456789abc",
        ],
    )
    def test_the_shape_hubspot_issues_is_accepted(self, token):
        assert self.descriptor.credentials[0].pattern.match(token)

    @pytest.mark.parametrize(
        "token", ["", "pat-", "pat-eu1-xyz", "eu1-12345678-1234-1234-1234-123456789abc", "sk-abc"]
    )
    def test_anything_else_is_refused(self, token):
        assert not self.descriptor.credentials[0].pattern.match(token)


# ---- the runtime guard --------------------------------------------------------------------------


class TestTheRuntimeNeverRunsAnUnrunnableSource:
    def build(self, tmp_path, source, *, secret_store=None):
        write_client(tmp_path, "alpha")
        store = InMemoryConfigStore()
        store.publish("alpha", document_with(source), author="test", expected_parent=None)
        config = resolve(
            tmp_path,
            "alpha",
            ceiling=ApprovalMode.APPROVAL,
            env=env_for("alpha"),
            store=store,
            secret_store=secret_store,
        ).config
        assert config is not None
        builders, _ = fake_builders(FakeClock(NOW))
        return config, lambda: build_runtime(config, builders, FakeClock(NOW))

    def test_a_client_without_a_choice_builds_as_always(self, tmp_path):
        _, build = self.build(tmp_path, None)
        assert build().client_id == "alpha"

    def test_the_stores_provider_as_the_source_builds_as_always(self, tmp_path):
        _, build = self.build(tmp_path, {"provider": "google_sheets"})
        assert build().client_id == "alpha"

    def test_a_source_apart_from_the_store_is_refused_not_replaced_by_the_store(self, tmp_path):
        config, build = self.build(tmp_path, {"provider": "hubspot"})
        assert config.spec.source_provider == "hubspot"
        with pytest.raises(RuntimeBuildError, match="cannot be run yet") as caught:
            build()
        assert "alpha" in str(caught.value) and "hubspot" in str(caught.value)

    def test_the_token_is_not_part_of_what_the_runtime_receives(self, tmp_path):
        secrets = InMemorySecretStore()
        secrets.set(client_scope("alpha"), HUBSPOT_ACCESS_TOKEN, TOKEN, by="client:alpha")
        config, _ = self.build(tmp_path, {"provider": "hubspot"}, secret_store=secrets)
        assert config.secrets.get_optional(HUBSPOT_ACCESS_TOKEN) is None
        assert TOKEN not in repr(config.secrets) and TOKEN not in repr(config.missing_credentials)
