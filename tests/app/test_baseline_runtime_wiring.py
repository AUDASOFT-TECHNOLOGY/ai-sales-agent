"""Baseline (golden) tests: how a resolved configuration becomes a running client.

Pins, with the *real* default builders (`default_builders`) and recording stand-ins for the four
vendor classes they construct (Google Sheets API and repository, OpenAI provider, WhatsApp
channel):

- the exact arguments each factory passes to its vendor class;
- how `build_runtime` builds the Engine (mode, limits, inbound policy, channel, facts wiring);
- the messages of every way `build_runtime` can fail;
- the registry: inbound routing by (provider, routing key), readiness, health report;
- the webhook parsers: which accounts they authenticate and with which secret.

No network, no file, no clock: every value is deterministic.
"""

import json
from datetime import timedelta

import app_test_brains as brains
import pytest
from app_support import VERIFY_TOKEN, env_for, make_settings, phone_id, write_client
from baseline_support import (
    assert_golden,
    engine_snapshot,
    install_recording_vendors,
    place_yaml,
    resolve,
)
from factories import NOW

from sales_app.config import load_client_configs
from sales_app.errors import RuntimeBuildError
from sales_app.parsers import build_parsers
from sales_app.registry import ClientRegistry
from sales_app.secret_store import GLOBAL_SCOPE, InMemorySecretStore, client_scope
from sales_app.wiring import ChannelProvider, IntegrationBuilders, build_runtime, default_builders
from sales_core.approval import ApprovalMode
from sales_core.contracts import Channel
from sales_core.ports import InvalidSignatureError
from sales_integrations.fakes import FakeClock
from sales_integrations.whatsapp import compute_signature

APPROVAL, DRY, AUTO = ApprovalMode.APPROVAL, ApprovalMode.DRY_RUN, ApprovalMode.AUTONOMOUS


@pytest.fixture(autouse=True)
def _reset_created():
    brains.CREATED.clear()


def builders_for(tmp_path, verify_token=None) -> IntegrationBuilders:
    return default_builders(make_settings(tmp_path), verify_token=verify_token)


def build(tmp_path, *, ceiling=AUTO, env=None, secret_store=None, store=None, client="alpha"):
    config = resolve(
        tmp_path, client, ceiling=ceiling, env=env, secret_store=secret_store, store=store
    ).config
    assert config is not None
    return config, build_runtime(config, builders_for(tmp_path), FakeClock(NOW))


class TestFactoryArguments:
    """What each real factory hands to its vendor class."""

    def test_a_live_client_from_a_complete_legacy_file(self, tmp_path, monkeypatch):
        recording = install_recording_vendors(monkeypatch)
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        _, runtime = build(tmp_path, ceiling=APPROVAL)
        assert_golden(
            "runtime_legacy_complete_live",
            {
                "vendor_calls": recording.calls,
                "engine": engine_snapshot(runtime),
                "brain_factory_calls": [
                    {"settings": dict(settings), "llm_is_the_built_provider": llm in recording.llms}
                    for llm, settings in brains.CREATED
                ],
            },
        )

    def test_optional_account_settings_and_the_api_version_come_from_the_credential_store(
        self, tmp_path, monkeypatch
    ):
        recording = install_recording_vendors(monkeypatch)
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        secrets = InMemorySecretStore()
        secrets.set(GLOBAL_SCOPE, "openai.organization", "org-1", by="baseline")
        secrets.set(GLOBAL_SCOPE, "openai.project", "proj-1", by="baseline")
        secrets.set(GLOBAL_SCOPE, "openai.base_url", "https://llm.example.com/v1", by="baseline")
        secrets.set(GLOBAL_SCOPE, "whatsapp.api_version", "v23.0", by="baseline")
        secrets.set(client_scope("alpha"), "whatsapp.access_token", "tok-store", by="baseline")
        secrets.set(client_scope("alpha"), "whatsapp.phone_number_id", "99887766", by="baseline")
        build(tmp_path, ceiling=APPROVAL, secret_store=secrets)
        assert_golden("runtime_legacy_complete_store_credentials", recording.calls)

    def test_a_dry_run_never_builds_the_whatsapp_channel(self, tmp_path, monkeypatch):
        recording = install_recording_vendors(monkeypatch)
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        _, runtime = build(tmp_path, ceiling=DRY)
        assert recording.named("WhatsAppChannel") == []
        assert_golden(
            "runtime_legacy_complete_dry_run",
            {"vendor_calls": recording.calls, "engine": engine_snapshot(runtime)},
        )

    def test_minimal_legacy_client_defaults(self, tmp_path, monkeypatch):
        recording = install_recording_vendors(monkeypatch)
        write_client(tmp_path, "alpha")
        _, runtime = build(tmp_path, ceiling=APPROVAL)
        assert_golden(
            "runtime_minimal_legacy",
            {"vendor_calls": recording.calls, "engine": engine_snapshot(runtime)},
        )

    def test_a_stored_document_changes_what_the_factories_receive(self, tmp_path, monkeypatch):
        from baseline_support import fixture_json, store_with_document

        recording = install_recording_vendors(monkeypatch)
        place_yaml(tmp_path, "alpha", "wiring_only.yaml")
        store = store_with_document("alpha", fixture_json("doc_v2.json"))
        _, runtime = build(tmp_path, ceiling=APPROVAL, store=store)
        assert_golden(
            "runtime_stored_document_v2",
            {"vendor_calls": recording.calls, "engine": engine_snapshot(runtime)},
        )

    def test_clients_are_staggered_by_their_position(self, tmp_path, monkeypatch):
        install_recording_vendors(monkeypatch)
        place_yaml(tmp_path, "alpha", "legacy_complete.yaml")
        config = resolve(tmp_path, ceiling=APPROVAL).config
        runtime = build_runtime(
            config, builders_for(tmp_path), FakeClock(NOW), stagger=timedelta(seconds=15)
        )
        assert runtime.stagger.total_seconds() == 15


class TestBuildFailures:
    """Every way `build_runtime` refuses a client, with its exact message."""

    def failure(self, tmp_path, monkeypatch, *, raw_overrides, ceiling=APPROVAL, builders=None):
        install_recording_vendors(monkeypatch)
        write_client(tmp_path, "alpha", **raw_overrides)
        config = resolve(tmp_path, ceiling=ceiling).config
        with pytest.raises(RuntimeBuildError) as raised:
            build_runtime(config, builders or builders_for(tmp_path), FakeClock(NOW))
        return str(raised.value)

    def test_the_messages(self, tmp_path_factory, monkeypatch):
        def case(**kwargs):
            return self.failure(tmp_path_factory.mktemp("case"), monkeypatch, **kwargs)

        results = {
            "unknown_sheets_provider": case(raw_overrides={"sheets": {"provider": "airtable"}}),
            "unknown_llm_provider": case(raw_overrides={"llm": {"provider": "anthropic"}}),
            "unknown_channel_provider": case(raw_overrides={"whatsapp": {"provider": "signal"}}),
            "brain_cannot_be_imported": case(
                raw_overrides={"brain": {"factory": "no_such_module_xyz:good_brain"}}
            ),
            "brain_factory_missing": case(
                raw_overrides={"brain": {"factory": "app_test_brains:nope"}}
            ),
            "brain_raises": case(
                raw_overrides={"brain": {"factory": "app_test_brains:raising_brain"}}
            ),
            "brain_is_not_a_brain": case(
                raw_overrides={"brain": {"factory": "app_test_brains:not_a_brain"}}
            ),
            "unknown_channel_provider_in_dry_run": case(
                raw_overrides={"whatsapp": {"provider": "signal"}}, ceiling=DRY
            ),
        }
        assert_golden("runtime_build_failures", results)

    def test_a_repository_that_fails_to_build_is_reported_with_the_stage(self, tmp_path):
        write_client(tmp_path, "alpha")
        config = resolve(tmp_path, ceiling=APPROVAL).config

        def boom(_config):
            raise RuntimeError("cannot open the spreadsheet")

        base = builders_for(tmp_path)
        builders = IntegrationBuilders(
            repositories={"google_sheets": boom}, llms=base.llms, channels=base.channels
        )
        with pytest.raises(RuntimeBuildError) as raised:
            build_runtime(config, builders, FakeClock(NOW))
        assert_golden("runtime_build_failure_repository", {"message": str(raised.value)})


class TestRegistryAndParsers:
    """Three clients on the real builders: ready, ready, and one lacking its access token."""

    @pytest.fixture
    def world(self, tmp_path, monkeypatch):
        install_recording_vendors(monkeypatch)
        for client_id in ("alpha", "beta", "delta"):
            write_client(tmp_path, client_id)
        place_yaml(tmp_path, "pholder", "legacy_disabled_placeholders.yaml")
        env = env_for("alpha", "beta", "delta")
        del env["DELTA_TOKEN"]  # delta is valid but not ready
        loaded = load_client_configs(tmp_path, env=env, mode_ceiling=APPROVAL)
        assert loaded.errors == ()
        builders = default_builders(make_settings(tmp_path))
        registry = ClientRegistry.build(loaded, builders, FakeClock(NOW))
        return loaded, builders, registry

    def test_inbound_routing(self, world):
        _, _, registry = world

        def owner(provider, key):
            runtime = registry.by_routing_key(provider, key)
            return None if runtime is None else runtime.client_id

        assert_golden(
            "registry_routing",
            {
                "alpha": owner("whatsapp", phone_id("alpha")),
                "beta": owner("whatsapp", phone_id("beta")),
                "delta_not_ready": owner("whatsapp", phone_id("delta")),
                "delta_is_known_as_waiting": registry.not_ready_client_for(
                    "whatsapp", phone_id("delta")
                ),
                "unknown_key": owner("whatsapp", "unknown"),
                "unknown_provider": owner("sms", phone_id("beta")),
                "ready_client_is_not_waiting": registry.not_ready_client_for(
                    "whatsapp", phone_id("alpha")
                ),
                "runtime_client_ids": sorted(r.client_id for r in registry.runtimes()),
                "config_client_ids": sorted(c.client_id for c in registry.configs()),
                "missing_alpha": list(registry.missing("alpha")),
                "missing_delta": list(registry.missing("delta")),
                "missing_unknown": list(registry.missing("zzz")),
                "get_config_delta_is_known": registry.get_config("delta") is not None,
                "get_runtime_delta": registry.get("delta") is not None,
            },
        )

    def test_health_report(self, world):
        _, _, registry = world
        assert_golden("registry_health", registry.health())

    def test_dropped_inbound_messages_are_counted_per_client(self, world):
        _, _, registry = world
        registry.note_dropped("delta", NOW)
        registry.note_dropped("delta", NOW)
        report = {entry["client_id"]: entry for entry in registry.health()}
        assert_golden(
            "registry_health_dropped_inbound",
            {
                k: report["delta"][k]
                for k in ("status", "dropped_inbound", "last_dropped_inbound_at")
            },
        )

    def test_the_parsers_cover_every_client_with_an_account(self, world):
        loaded, builders, _ = world
        parsers = build_parsers(loaded.clients, builders)
        assert_golden("parsers_providers", {"providers": sorted(parsers)})

    def test_each_account_is_authenticated_with_its_own_secret(self, world):
        loaded, builders, _ = world
        parser = build_parsers(loaded.clients, builders)["whatsapp"]

        def body(phone):
            return json.dumps(
                {
                    "object": "whatsapp_business_account",
                    "entry": [
                        {
                            "changes": [
                                {
                                    "field": "messages",
                                    "value": {
                                        "metadata": {"phone_number_id": phone},
                                        "messages": [
                                            {
                                                "from": "21612345678",
                                                "id": f"wamid.{phone}",
                                                "timestamp": "1768467600",
                                                "type": "text",
                                                "text": {"body": "Hello"},
                                            }
                                        ],
                                    },
                                }
                            ]
                        }
                    ],
                }
            ).encode()

        def attempt(phone, secret):
            raw = body(phone)
            headers = {"X-Hub-Signature-256": compute_signature(secret, raw)} if secret else {}
            try:
                events = parser.parse(raw, headers)
            except InvalidSignatureError as error:
                return {"rejected": str(error)}
            return {"events": [(e.routing_key, e.message.external_id) for e in events]}

        results = {
            "alpha_with_its_secret": attempt(phone_id("alpha"), "secret-alpha"),
            "alpha_with_beta_secret": attempt(phone_id("alpha"), "secret-beta"),
            "alpha_unsigned": attempt(phone_id("alpha"), None),
            "beta_with_its_secret": attempt(phone_id("beta"), "secret-beta"),
            "not_ready_delta_is_still_authenticated": attempt(phone_id("delta"), "secret-delta"),
            "not_ready_delta_with_a_wrong_secret": attempt(phone_id("delta"), "secret-alpha"),
            "unknown_account_is_returned_unverified": attempt("phone-unknown", None),
        }
        assert_golden("parsers_authentication", results)

    def test_the_handshake_uses_the_deployments_verify_token(self, world):
        loaded, builders, _ = world
        parser = build_parsers(loaded.clients, builders)["whatsapp"]
        good = {"hub.mode": "subscribe", "hub.verify_token": VERIFY_TOKEN, "hub.challenge": "42"}
        bad = {**good, "hub.verify_token": "wrong"}
        assert_golden(
            "parsers_handshake",
            {"good": parser.handshake(good), "wrong_token": parser.handshake(bad)},
        )

    def test_a_token_saved_in_admin_applies_without_a_restart(self, world, tmp_path):
        loaded, _, _ = world
        builders = default_builders(make_settings(tmp_path), verify_token=lambda: "saved-in-admin")
        parser = build_parsers(loaded.clients, builders)["whatsapp"]
        query = {
            "hub.mode": "subscribe",
            "hub.verify_token": "saved-in-admin",
            "hub.challenge": "7",
        }
        old = {**query, "hub.verify_token": VERIFY_TOKEN}
        assert_golden(
            "parsers_handshake_admin_token",
            {"saved_token": parser.handshake(query), "environment_token": parser.handshake(old)},
        )

    def test_a_channel_provider_chooses_the_routing_key_and_secret(self, world):
        _, builders, _ = world
        provider = builders.channels["whatsapp"]
        assert isinstance(provider, ChannelProvider) and provider.kind is Channel.WHATSAPP
