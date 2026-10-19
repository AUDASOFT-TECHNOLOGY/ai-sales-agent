"""Provider descriptors and their registry: what each provider declares, and that the builders the
runtime uses are the same as before now that they are built from the registry."""

import dataclasses
from types import MappingProxyType

import pytest
import yaml
from app_support import VERIFY_TOKEN, client_yaml, make_settings, phone_id, write_client
from baseline_support import fixture_text, install_recording_vendors, resolve
from factories import NOW
from pydantic import ValidationError

from sales_app.config.normalize import normalize
from sales_app.config.secrets import (
    OPENAI_API_KEY,
    OPENAI_BASE_URL,
    OPENAI_ORGANIZATION,
    OPENAI_PROJECT,
    SHEETS_CREDENTIALS_FILE,
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_ID,
    WHATSAPP_APP_SECRET,
    WHATSAPP_CONFIG_ID,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_VERIFY_TOKEN,
    WHATSAPP_WABA_ID,
)
from sales_app.errors import RuntimeBuildError
from sales_app.secret_store import CATALOG, ScopeKind
from sales_app.wiring import ChannelProvider, IntegrationBuilders, build_runtime, default_builders
from sales_app.wiring.providers import (
    ChannelSupport,
    CredentialDeclaration,
    DuplicateProviderError,
    ProviderKind,
    ProviderRegistry,
    Requirement,
    UnknownProviderError,
    default_registry,
    fake_descriptor,
    google_sheets,
    meta,
    openai,
    whatsapp,
)
from sales_app.wiring.providers.google_sheets import SheetsSourceSettings, SheetsStoreSettings
from sales_app.wiring.providers.whatsapp import WhatsAppChannelSettings
from sales_core.approval import ApprovalMode
from sales_core.contracts import Channel
from sales_integrations.fakes import FakeClock
from sales_integrations.google_sheets.layout import TAB_SPECS

SOURCE, STORE, CHANNEL, LLM = (
    ProviderKind.SOURCE,
    ProviderKind.STORE,
    ProviderKind.CHANNEL,
    ProviderKind.LLM,
)
REGISTRY = default_registry()
CATALOG_BY_KEY = {spec.key: spec for spec in CATALOG}
# Model lists are managed in Admin Settings, not declared by a provider (see the OpenAI module).
MODEL_LIST_KEYS = {"openai.model_sol", "openai.model_lune", "openai.models"}


class TestTheRegistryHoldsTheProviders:
    def test_every_governed_channel_is_a_channel_provider_and_back(self):
        from sales_app.config.channel_access import GOVERNED

        assert {d.key for d in REGISTRY.list(CHANNEL)} == GOVERNED

    def test_the_descriptors(self):
        assert [d.key for d in REGISTRY] == [
            "email",
            "google_sheets",
            "hubspot",
            "meta",
            "openai",
            "telegram",
            "whatsapp",
        ]
        assert len(REGISTRY) == 7
        for key in ("email", "google_sheets", "hubspot", "whatsapp", "openai", "meta", "telegram"):
            assert key in REGISTRY and REGISTRY.require(key).key == key

    def test_keys_are_unique(self):
        keys = [d.key for d in REGISTRY]
        assert len(keys) == len(set(keys))

    def test_a_fresh_registry_each_time(self):
        first, second = default_registry(), default_registry()
        first.register_fake("only_here")
        assert "only_here" in first and "only_here" not in second

    def test_providers_by_the_role_they_play(self):
        assert [d.key for d in REGISTRY.list(SOURCE)] == ["google_sheets", "hubspot"]
        assert [d.key for d in REGISTRY.list(STORE)] == ["google_sheets"]
        assert [d.key for d in REGISTRY.list(CHANNEL)] == ["email", "telegram", "whatsapp"]
        assert [d.key for d in REGISTRY.list(LLM)] == ["meta", "openai"]

    def test_the_descriptors_are_immutable(self):
        descriptor = REGISTRY.require("openai")
        with pytest.raises(dataclasses.FrozenInstanceError):
            descriptor.key = "other"  # type: ignore[misc]
        assert isinstance(descriptor.capabilities, MappingProxyType)
        assert isinstance(descriptor.settings_models, MappingProxyType)
        with pytest.raises(TypeError):
            descriptor.capabilities["x"] = 1  # type: ignore[index]


class TestGoogleSheets:
    descriptor = google_sheets.DESCRIPTOR

    def test_it_plays_two_roles_and_can_host_a_store(self):
        assert self.descriptor.kind is SOURCE
        assert self.descriptor.roles == {SOURCE, STORE}
        assert self.descriptor.can_host_store is True
        assert self.descriptor.capabilities["can_host_store"] is True
        assert self.descriptor.label == "Google Sheets"

    def test_its_credential_comes_from_the_environment_only(self):
        (credential,) = self.descriptor.credentials
        assert credential.key == SHEETS_CREDENTIALS_FILE == "sheets.credentials_file"
        assert credential.scope is None and credential.required_when is Requirement.ALWAYS
        assert credential.label == "Google Sheets credentials file"
        assert credential.key not in CATALOG_BY_KEY  # nothing is stored for it today

    def test_two_clients_cannot_share_a_spreadsheet(self):
        (rule,) = self.descriptor.unique_across_clients
        assert (rule.kind, rule.name) == ("setting", "spreadsheet_id")

    def test_it_is_runnable_through_its_own_factory(self):
        assert self.descriptor.runnable and self.descriptor.build is google_sheets.build_repository

    def test_it_declares_the_settings_of_each_role(self):
        models = self.descriptor.settings_models
        assert models[SOURCE] is SheetsSourceSettings and models[STORE] is SheetsStoreSettings

    def test_its_tab_lists_are_the_layouts_tabs(self):
        assert set(google_sheets.SOURCE_TABS) | set(google_sheets.STORE_TABS) == set(TAB_SPECS)

    def test_the_settings_a_legacy_configuration_normalises_to_are_valid(self):
        for raw in (
            yaml.safe_load(fixture_text("legacy_complete.yaml")),
            yaml.safe_load(yaml.safe_dump(client_yaml("alpha"))),
        ):
            spec = normalize(raw).spec
            source = SheetsSourceSettings.model_validate(spec["source"]["settings"])
            store = SheetsStoreSettings.model_validate(spec["store"]["settings"])
            assert source.spreadsheet_id == raw["sheets"]["spreadsheet_id"]
            assert store.actions_tab == spec["store"]["settings"]["actions_tab"]

    def test_the_settings_models_refuse_what_belongs_to_the_other_role_or_nowhere(self):
        with pytest.raises(ValidationError, match="column_mapping"):
            SheetsSourceSettings(spreadsheet_id="s", column_mapping={"actions.status": "S"})
        with pytest.raises(ValidationError, match="column_mapping"):
            SheetsStoreSettings(column_mapping={"prospects.phone": "P"})
        with pytest.raises(ValidationError, match="column_mapping"):
            SheetsStoreSettings(column_mapping={"leads.x": "P"})
        with pytest.raises(ValidationError, match="surprise"):
            SheetsSourceSettings(spreadsheet_id="s", surprise=1)
        with pytest.raises(ValidationError, match="spreadsheet_id"):
            SheetsSourceSettings()
        assert SheetsStoreSettings().log_tab == "Log"


class TestWhatsApp:
    descriptor = whatsapp.DESCRIPTOR

    def test_it_is_the_channel_provider(self):
        assert self.descriptor.kind is CHANNEL and self.descriptor.roles == {CHANNEL}
        assert self.descriptor.label == "WhatsApp" and self.descriptor.runnable
        assert self.descriptor.channel.kind == "whatsapp" == Channel.WHATSAPP.value

    def test_it_declares_exactly_the_credentials_the_store_already_knows(self):
        assert {c.key for c in self.descriptor.credentials} == {
            WHATSAPP_VERIFY_TOKEN,
            WHATSAPP_APP_SECRET,
            WHATSAPP_API_VERSION,
            WHATSAPP_ACCESS_TOKEN,
            WHATSAPP_PHONE_NUMBER_ID,
            WHATSAPP_APP_ID,
            WHATSAPP_CONFIG_ID,
            WHATSAPP_WABA_ID,
        }

    @pytest.mark.parametrize("credential", whatsapp.DESCRIPTOR.credentials, ids=lambda c: c.key)
    def test_each_one_matches_the_credential_catalog(self, credential):
        assert_matches_catalog(credential)

    def test_which_are_global_and_which_belong_to_a_client(self):
        scope = {c.key: c.scope for c in self.descriptor.credentials}
        assert scope[WHATSAPP_ACCESS_TOKEN] is ScopeKind.CLIENT
        assert scope[WHATSAPP_PHONE_NUMBER_ID] is ScopeKind.CLIENT
        assert scope[WHATSAPP_APP_SECRET] is ScopeKind.GLOBAL
        assert scope[WHATSAPP_VERIFY_TOKEN] is ScopeKind.GLOBAL
        assert scope[WHATSAPP_API_VERSION] is ScopeKind.GLOBAL

    def test_the_phone_number_id_is_the_unique_non_secret_account_identifier(self):
        phone = self.descriptor.credential(WHATSAPP_PHONE_NUMBER_ID)
        assert phone.unique and not phone.sensitive
        (rule,) = self.descriptor.unique_across_clients
        assert (rule.kind, rule.name) == ("credential", WHATSAPP_PHONE_NUMBER_ID)

    def test_a_live_client_needs_its_account_and_a_dry_run_does_not(self):
        live = {c.key for c in self.descriptor.credentials if c.required_when is Requirement.LIVE}
        assert live == {WHATSAPP_ACCESS_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_APP_SECRET}

    def test_its_channel_settings_hold_no_account_and_no_credential(self):
        assert set(WhatsAppChannelSettings.model_fields) == {"api_version"}
        assert WhatsAppChannelSettings().api_version == "v21.0"
        with pytest.raises(ValidationError):
            WhatsAppChannelSettings(token="x")  # type: ignore[call-arg]

    def test_the_settings_a_legacy_configuration_normalises_to_are_valid(self):
        channel = normalize(yaml.safe_load(fixture_text("legacy_complete.yaml"))).spec["channels"][
            0
        ]
        assert WhatsAppChannelSettings.model_validate(channel["settings"]).api_version == "v22.0"

    def test_what_it_says_about_the_platform(self):
        assert self.descriptor.capabilities["reply_window_hours"] == 24
        assert self.descriptor.capabilities["can_initiate"] is True


class TestOpenAI:
    descriptor = openai.DESCRIPTOR

    def test_it_is_an_llm_provider_of_the_openai_family(self):
        assert self.descriptor.kind is LLM and self.descriptor.roles == {LLM}
        assert self.descriptor.family == "openai_compatible" and self.descriptor.label == "OpenAI"
        assert self.descriptor.runnable and self.descriptor.build is openai.build_provider

    def test_it_declares_the_credentials_the_store_already_knows(self):
        declared = {c.key for c in self.descriptor.credentials}
        assert declared == {OPENAI_API_KEY, OPENAI_ORGANIZATION, OPENAI_PROJECT, OPENAI_BASE_URL}
        assert declared == {k for k in CATALOG_BY_KEY if k.startswith("openai.")} - MODEL_LIST_KEYS

    @pytest.mark.parametrize("credential", openai.DESCRIPTOR.credentials, ids=lambda c: c.key)
    def test_each_one_matches_the_credential_catalog(self, credential):
        assert_matches_catalog(credential)

    def test_only_the_api_key_is_required_and_it_is_global(self):
        required = [
            c.key
            for c in self.descriptor.credentials
            if c.required_when is not Requirement.OPTIONAL
        ]
        assert required == [OPENAI_API_KEY]
        assert self.descriptor.credential(OPENAI_API_KEY).scope is ScopeKind.GLOBAL

    def test_it_declares_no_model(self):
        assert not any("model" in key for key in {c.key for c in self.descriptor.credentials})
        assert set(self.descriptor.settings_models) == {LLM}


class TestMeta:
    descriptor = meta.DESCRIPTOR

    def test_it_is_a_provider_of_its_own(self):
        assert self.descriptor.key == "meta" != openai.DESCRIPTOR.key
        assert self.descriptor.kind is LLM and self.descriptor.label == "Meta"
        assert meta.DESCRIPTOR is not openai.DESCRIPTOR
        assert REGISTRY.require("meta") is not REGISTRY.require("openai")

    def test_it_shares_an_adapter_family_with_openai_and_nothing_else(self):
        assert self.descriptor.family == "openai_compatible" == openai.DESCRIPTOR.family
        meta_keys = {c.key for c in self.descriptor.credentials}
        assert meta_keys.isdisjoint({c.key for c in openai.DESCRIPTOR.credentials})
        assert self.descriptor.capabilities != openai.DESCRIPTOR.capabilities

    def test_its_api_key_is_a_required_global_secret(self):
        key = self.descriptor.credential("meta.api_key")
        assert key is not None and key.key == meta.META_API_KEY
        assert key.scope is ScopeKind.GLOBAL and key.sensitive
        assert key.required_when is Requirement.ALWAYS

    def test_its_base_url_is_required_and_is_not_a_secret(self):
        url = self.descriptor.credential("meta.base_url")
        assert url is not None and url.required_when is Requirement.ALWAYS
        assert url.scope is ScopeKind.GLOBAL and not url.sensitive
        assert url.pattern.match("https://api.example.com/v1")
        assert not url.pattern.match("http://api.example.com/v1")
        assert not url.pattern.match("https://user:pw@api.example.com")

    def test_openai_does_not_require_a_base_url(self):
        assert openai.DESCRIPTOR.credential(OPENAI_BASE_URL).required_when is Requirement.OPTIONAL

    def test_it_cannot_be_run_yet_and_is_not_offered_to_the_runtime(self):
        assert self.descriptor.build is None and not self.descriptor.runnable
        builders = default_builders(make_settings("."))
        assert set(builders.llms) == {"openai"}

    def test_its_structured_output_is_declared_unverified_not_assumed(self):
        assert self.descriptor.capabilities["structured_output"] == "unverified"

    def test_its_credentials_are_not_in_the_credential_catalog_yet(self):
        assert not {"meta.api_key", "meta.base_url"} & set(CATALOG_BY_KEY)


def assert_matches_catalog(credential: CredentialDeclaration) -> None:
    """A declaration repeats what the credential catalog says; they must not drift apart."""
    spec = CATALOG_BY_KEY[credential.key]
    assert credential.scope is spec.scope_kind
    assert credential.label == spec.label
    assert credential.sensitive == spec.sensitive
    assert credential.unique == spec.unique
    assert (credential.pattern.pattern if credential.pattern else None) == (
        spec.pattern.pattern if spec.pattern else None
    )
    assert credential.rule == spec.rule


class TestRequirementsAgreeWithWhatTheRuntimeRequiresToday:
    """`required_when` restates what `_resolve_secrets` decides; the real resolution is the judge."""

    def required_labels(self, live: bool) -> set[str]:
        wanted = {Requirement.ALWAYS, Requirement.LIVE} if live else {Requirement.ALWAYS}
        return {
            f"{c.label} is not configured"
            for key in ("google_sheets", "whatsapp", "openai")
            for c in REGISTRY.require(key).credentials
            if c.required_when in wanted
        }

    def test_a_live_client_with_nothing_configured(self, tmp_path):
        write_client(tmp_path, "alpha")
        config = resolve(tmp_path, ceiling=ApprovalMode.APPROVAL, env={}).config
        assert set(config.missing_credentials) == self.required_labels(live=True)

    def test_a_dry_run_client_with_nothing_configured(self, tmp_path):
        write_client(tmp_path, "alpha")
        config = resolve(tmp_path, ceiling=ApprovalMode.DRY_RUN, env={}).config
        assert set(config.missing_credentials) == self.required_labels(live=False)


class TestTheRegistry:
    def test_a_duplicate_key_is_refused(self):
        registry = ProviderRegistry([google_sheets.DESCRIPTOR])
        with pytest.raises(DuplicateProviderError, match="google_sheets"):
            registry.register(google_sheets.DESCRIPTOR)
        assert len(registry) == 1

    def test_a_key_is_unique_across_kinds(self):
        registry = ProviderRegistry([openai.DESCRIPTOR])
        with pytest.raises(DuplicateProviderError):
            registry.register(fake_descriptor("openai", SOURCE))

    def test_the_constructor_refuses_duplicates_too(self):
        with pytest.raises(DuplicateProviderError):
            ProviderRegistry([meta.DESCRIPTOR, meta.DESCRIPTOR])

    def test_get_require_and_membership(self):
        registry = ProviderRegistry([meta.DESCRIPTOR])
        assert registry.get("meta") is meta.DESCRIPTOR and registry.get("nope") is None
        assert "meta" in registry and "nope" not in registry
        with pytest.raises(UnknownProviderError, match="nope"):
            registry.require("nope")

    def test_listing_is_ordered_by_key_and_can_filter(self):
        registry = ProviderRegistry()
        for key in ("zeta", "alpha", "mid"):
            registry.register_fake(key, LLM)
        registry.register_fake("a_source", SOURCE)
        assert [d.key for d in registry.list()] == ["a_source", "alpha", "mid", "zeta"]
        assert [d.key for d in registry.list(LLM)] == ["alpha", "mid", "zeta"]
        assert registry.list(CHANNEL) == ()

    def test_a_fake_provider_registers_in_one_line(self):
        registry = ProviderRegistry()
        built = []
        descriptor = registry.register_fake(
            "fake_source", build=lambda config: built.append(config)
        )
        assert registry.require("fake_source") is descriptor
        assert (
            descriptor.kind is SOURCE and descriptor.label == "fake_source" and descriptor.runnable
        )
        descriptor.build("config")
        assert built == ["config"]

    def test_a_fake_can_carry_a_label_credentials_and_roles(self):
        credential = CredentialDeclaration("fake.token", "Fake token", ScopeKind.CLIENT)
        descriptor = fake_descriptor(
            "fake_both",
            SOURCE,
            label="Fake both",
            roles=frozenset({SOURCE, STORE}),
            can_host_store=True,
            credentials=(credential,),
        )
        assert descriptor.label == "Fake both" and descriptor.can_host_store
        assert descriptor.credential("fake.token") is credential and descriptor.plays(STORE)

    def test_a_fake_channel_needs_its_channel_support(self):
        support = ChannelSupport(
            kind="sms",
            routing_key=lambda config: None,
            webhook_secret=lambda config: None,
            build_parser=lambda secrets, token: None,
        )
        assert fake_descriptor("fake_sms", CHANNEL, channel=support).channel is support
        with pytest.raises(ValueError, match="channel providers carry"):
            fake_descriptor("fake_sms", CHANNEL)
        with pytest.raises(ValueError, match="channel providers carry"):
            fake_descriptor("fake_source", SOURCE, channel=support)


class TestDescriptorValidation:
    @pytest.mark.parametrize("key", ["", "a", "Upper", "1abc", "has-hyphen", "has.dot", "x" * 33])
    def test_a_provider_key_must_be_well_formed(self, key):
        with pytest.raises(ValueError, match="provider key"):
            fake_descriptor(key)

    def test_hosting_a_store_needs_both_roles(self):
        with pytest.raises(ValueError, match="can_host_store"):
            fake_descriptor("fake_source", SOURCE, can_host_store=True)

    def test_settings_only_for_roles_it_plays(self):
        with pytest.raises(ValueError, match="role it does not play"):
            fake_descriptor("fake_source", SOURCE, settings_models={LLM: object})

    def test_a_credential_is_declared_once(self):
        credential = CredentialDeclaration("fake.token", "Token", ScopeKind.GLOBAL)
        with pytest.raises(ValueError, match="declared twice"):
            fake_descriptor("fake_source", credentials=(credential, credential))

    @pytest.mark.parametrize("key", ["token", "Fake.token", "fake.", ".token", "a.b.c"])
    def test_a_credential_key_looks_like_provider_dot_name(self, key):
        with pytest.raises(ValueError, match="credential key"):
            CredentialDeclaration(key, "Token", ScopeKind.GLOBAL)

    def test_only_a_stored_credential_can_be_unique(self):
        with pytest.raises(ValueError, match="can be unique"):
            CredentialDeclaration("fake.file", "File", None, unique=True)


class TestTheBuildersTheRuntimeUsesAreUnchanged:
    def builders(self, tmp_path=".") -> IntegrationBuilders:
        return default_builders(make_settings(tmp_path))

    def test_the_integration_table_keeps_its_fields_and_adds_the_composed_source(self):
        assert (
            [f.name for f in dataclasses.fields(IntegrationBuilders)]
            == [
                "repositories",
                "llms",
                "channels",
                "sources",
                "operational_stores",
                "consent",
                "source_instances",  # E5: added last, with a default, so older callers are unchanged
                "writeback",  # E9: the same, and None (off) unless the deployment builds it
                "llm_usage",  # the LLM usage register of a client, None without a database
                "knowledge",  # the product documents of a client, None without a database
                "send_guards",  # the daily send cap of a client's metered channel, None without a database
            ]
        )

    def test_the_channel_provider_keeps_its_fields_and_gains_the_inbox(self):
        assert [f.name for f in dataclasses.fields(ChannelProvider)] == [
            "kind",
            "build",
            "routing_key",
            "webhook_secret",
            "build_parser",
            "build_inbox",  # added with e-mail, the one channel that is polled, not pushed
            "answer_unknown",  # added with Telegram, where a stranger is asked who they are
            "polls",  # Telegram from the client's own account: polled in that mode only
            "metered",  # e-mail: every message takes a place in the client's daily send cap
        ]

    def test_the_table_has_the_same_providers_as_before(self):
        builders = self.builders()
        assert set(builders.repositories) == {"google_sheets"}
        assert set(builders.llms) == {"openai"}
        assert set(builders.channels) == {"whatsapp", "email", "telegram"}

    def test_each_entry_is_the_providers_own_factory(self):
        builders = self.builders()
        assert builders.repositories["google_sheets"] is google_sheets.build_repository
        assert builders.llms["openai"] is openai.build_provider
        assert builders.channels["whatsapp"].build is whatsapp.build_channel

    def test_the_channel_provider_is_what_it_was(self):
        provider = self.builders().channels["whatsapp"]
        assert isinstance(provider, ChannelProvider) and provider.kind is Channel.WHATSAPP
        assert provider.routing_key is whatsapp.routing_key
        assert provider.webhook_secret is whatsapp.webhook_secret

    def test_the_old_factory_names_still_import(self):
        from sales_app.wiring.integrations import _google_sheets, _openai, _whatsapp_channel

        assert _openai is openai.build_provider
        assert _google_sheets is google_sheets.build_repository
        assert _whatsapp_channel is whatsapp.build_channel

    def test_routing_and_signing_come_from_the_resolved_configuration(self, tmp_path):
        write_client(tmp_path, "alpha")
        config = resolve(tmp_path, ceiling=ApprovalMode.APPROVAL).config
        provider = self.builders(tmp_path).channels["whatsapp"]
        assert provider.routing_key(config) == phone_id("alpha")
        assert provider.webhook_secret(config) == "secret-alpha"

    def test_the_parser_prefers_the_token_saved_in_admin_then_the_environment(self, tmp_path):
        query = {"hub.mode": "subscribe", "hub.challenge": "7"}
        saved = default_builders(make_settings(tmp_path), verify_token=lambda: "from-admin")
        parser = saved.channels["whatsapp"].build_parser({})
        assert parser.handshake({**query, "hub.verify_token": "from-admin"}) == "7"
        environment = default_builders(make_settings(tmp_path), verify_token=lambda: None)
        parser = environment.channels["whatsapp"].build_parser({})
        assert parser.handshake({**query, "hub.verify_token": "verify-me"}) == "7"

    def test_a_registry_of_fakes_gives_a_table_of_fakes(self):
        registry = ProviderRegistry()
        registry.register_fake("fake_source", SOURCE, build=lambda config: "repository")
        registry.register_fake("fake_llm", LLM, build=lambda config: "llm")
        registry.register_fake("declared_only", LLM)  # no factory: known, not runnable
        registry.register_fake(
            "fake_sms",
            CHANNEL,
            build=lambda config: "channel",
            channel=ChannelSupport(
                kind="sms",
                routing_key=lambda config: "route",
                webhook_secret=lambda config: "secret",
                build_parser=lambda secrets, token: ("parser", dict(secrets), token),
            ),
        )
        builders = default_builders(make_settings("."), registry=registry)
        assert set(builders.repositories) == {"fake_source"}
        assert set(builders.llms) == {"fake_llm"}
        assert set(builders.channels) == {"fake_sms"}
        sms = builders.channels["fake_sms"]
        assert sms.kind is Channel.SMS and sms.routing_key(None) == "route"
        # no token saved in Admin: the deployment's own is handed to the parser
        assert sms.build_parser({"r": "s"}) == ("parser", {"r": "s"}, VERIFY_TOKEN)

    def test_only_the_primary_role_feeds_the_table(self):
        registry = ProviderRegistry()
        registry.register_fake(
            "multi", SOURCE, build=lambda config: "x", roles=frozenset({SOURCE, STORE})
        )
        builders = default_builders(make_settings("."), registry=registry)
        assert (
            set(builders.repositories) == {"multi"} and not builders.llms and not builders.channels
        )


class TestAProviderWithoutADescriptorStaysUsable:
    """Builders written by hand (the tests' fakes) name providers no descriptor knows."""

    def test_such_a_table_still_builds_a_runtime(self, tmp_path):
        from app_support import fake_builders

        base, made = fake_builders()
        builders = IntegrationBuilders(
            repositories={"fake_source": base.repositories["google_sheets"]},
            llms={"fake_llm": base.llms["openai"]},
            channels={"fake_channel": base.channels["whatsapp"]},
        )
        write_client(
            tmp_path,
            "alpha",
            sheets={"provider": "fake_source"},
            llm={"provider": "fake_llm"},
            whatsapp={"provider": "fake_channel"},
        )
        config = resolve(tmp_path, ceiling=ApprovalMode.APPROVAL).config
        runtime = build_runtime(config, builders, FakeClock(NOW))
        assert runtime.client_id == "alpha" and "alpha" in made.repositories
        assert not any(name in REGISTRY for name in ("fake_source", "fake_llm", "fake_channel"))

    def test_a_provider_known_only_to_the_registry_is_refused_like_any_unknown_one(
        self, tmp_path, monkeypatch
    ):
        install_recording_vendors(monkeypatch)  # the repository is built before the model client
        write_client(tmp_path, "alpha", llm={"provider": "meta"})
        config = resolve(tmp_path, ceiling=ApprovalMode.APPROVAL).config
        with pytest.raises(RuntimeBuildError, match="unknown llm provider 'meta'"):
            build_runtime(config, default_builders(make_settings(tmp_path)), FakeClock(NOW))
