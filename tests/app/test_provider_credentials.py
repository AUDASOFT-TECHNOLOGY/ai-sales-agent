"""Credentials as the provider descriptors declare them, and the generic checks built on them.

Three things are tested. That the declarations are complete and equal to the credential catalog the
Admin API still uses (the catalog is kept as a compatibility layer, and these tests are what keep the
two from drifting apart). That the generic validator applies a declaration whatever the provider.
And that the uniqueness rules can say what the runtime says today about shared accounts.
"""

import inspect
import re
from pathlib import Path

import pytest
from app_support import env_for, write_client
from baseline_support import fixture_text, resolve

from sales_app.config import load_client_configs
from sales_app.config.spec_resolution import resolve_client_spec
from sales_app.connection_tests import check_openai, check_whatsapp_client
from sales_app.secret_store import CATALOG, GLOBAL_SCOPE, ScopeKind, client_scope
from sales_app.secret_store.catalog import normalize_value, spec_for
from sales_app.secret_store.errors import InvalidCredentialError
from sales_app.wiring.providers import (
    CredentialDeclaration,
    DuplicateCredentialError,
    ProviderDescriptor,
    ProviderKind,
    ProviderRegistry,
    Requirement,
    default_registry,
    fake_descriptor,
    google_sheets,
    meta,
    openai,
    whatsapp,
)
from sales_app.wiring.providers import credentials as credentials_module
from sales_app.wiring.providers.credentials import (
    Claim,
    ProblemCode,
    ProvidedCredential,
    is_required,
    legacy_specs,
    setting_claims,
    shared_values,
    validate_credentials,
    validate_provider_credentials,
)
from sales_core.approval import ApprovalMode

REGISTRY = default_registry()
CATALOG_BY_KEY = {spec.key: spec for spec in CATALOG}
# Model lists live in the credential store but are model-catalogue data, not a provider credential.
MODEL_LIST_KEYS = {"openai.model_sol", "openai.model_lune", "openai.models"}
# The platform mail server profile (Admin → Settings → Email): global, non-secret settings of
# Revansa's own mail servers. Stored like credentials, but no provider declares them: the email
# provider's credentials are the client's own (`email.password`).
# The payment webhook's signing secret: a platform credential no provider declares.
PLATFORM_PAYMENT_KEYS = {"payments.webhook_secret"}
# The platform's Google OAuth application (Gmail mailbox mode): global, Admin only, no provider declares it.
PLATFORM_GMAIL_KEYS = {
    "gmail.oauth_client_id",
    "gmail.oauth_client_secret",
    "gmail.oauth_state_key",
}
PLATFORM_MAIL_KEYS = {
    "email.platform_domain",
    "email.platform_smtp_host",
    "email.platform_smtp_port",
    "email.platform_smtp_security",
    "email.platform_imap_host",
    "email.platform_imap_port",
    "email.platform_imap_security",
    "email.platform_smtp_username",
    "email.platform_smtp_password",
    "email.platform_imap_username",
    "email.platform_imap_password",
}
# Declared by a descriptor, not yet storable through the Admin API (the catalog does not list them).
NOT_YET_IN_CATALOG = {"meta.api_key", "meta.base_url"}
SRC = Path(__file__).resolve().parents[2] / "src"

# key -> (provider, scope, sensitive, required_when, unique)
EXPECTED = {
    "sheets.credentials_file": ("google_sheets", None, True, Requirement.ALWAYS, False),
    "whatsapp.verify_token": ("whatsapp", ScopeKind.GLOBAL, True, Requirement.OPTIONAL, False),
    "whatsapp.app_secret": ("whatsapp", ScopeKind.GLOBAL, True, Requirement.LIVE, False),
    "whatsapp.api_version": ("whatsapp", ScopeKind.GLOBAL, False, Requirement.OPTIONAL, False),
    "whatsapp.access_token": ("whatsapp", ScopeKind.CLIENT, True, Requirement.LIVE, False),
    "whatsapp.phone_number_id": ("whatsapp", ScopeKind.CLIENT, False, Requirement.LIVE, True),
    "email.password": ("email", ScopeKind.CLIENT, True, Requirement.LIVE, False),
    "email.platform_mailbox": ("email", ScopeKind.CLIENT, False, Requirement.OPTIONAL, True),
    "telegram.bot_token": ("telegram", ScopeKind.CLIENT, True, Requirement.LIVE, False),
    "telegram.webhook_secret": ("telegram", ScopeKind.CLIENT, True, Requirement.LIVE, False),
    "openai.api_key": ("openai", ScopeKind.GLOBAL, True, Requirement.ALWAYS, False),
    "openai.organization": ("openai", ScopeKind.GLOBAL, False, Requirement.OPTIONAL, False),
    "openai.project": ("openai", ScopeKind.GLOBAL, False, Requirement.OPTIONAL, False),
    "openai.base_url": ("openai", ScopeKind.GLOBAL, False, Requirement.OPTIONAL, False),
    "hubspot.access_token": ("hubspot", ScopeKind.CLIENT, True, Requirement.ALWAYS, True),
    "meta.api_key": ("meta", ScopeKind.GLOBAL, True, Requirement.ALWAYS, False),
    "meta.base_url": ("meta", ScopeKind.GLOBAL, False, Requirement.ALWAYS, False),
}
# key -> (a valid value, values that must be refused)
VALUES = {
    "whatsapp.verify_token": ("a-token", [""]),
    "whatsapp.app_secret": ("a-secret", [""]),
    "whatsapp.api_version": ("v21.0", ["21", "v21", "v1.2.3", "version"]),
    "whatsapp.access_token": ("EAAB-token", [""]),
    "whatsapp.phone_number_id": ("123456789012345", ["abc", "1234", "12 345 678", "1" * 33]),
    "email.password": ("a-mailbox-password", [""]),
    "email.platform_mailbox": (
        "alpha@mail.example.org",
        ["", "no-at-sign", "a b@example.org", "a@b", "<a@example.org>"],
    ),
    "telegram.bot_token": (
        "123456789:AAH-secret-token-value-of-the-bot_xx",
        ["", "not-a-token", "123:short", "abc:AAH-secret-token-value-of-the-bot_xx", "1:2"],
    ),
    "telegram.webhook_secret": ("a" * 43, ["", "short", "a b" * 20, "a" * 257]),
    "openai.api_key": ("sk-abc", [""]),
    "openai.organization": ("org-123", ["bad org!", "-leading", "x" * 129]),
    "openai.project": ("proj_abc", ["bad project", "/x"]),
    "openai.base_url": (
        "https://api.openai.com/v1",
        ["http://api.openai.com/v1", "https://user:pw@host.com", "ftp://x", "https://a b.com"],
    ),
    "hubspot.access_token": (
        "pat-eu1-12345678-1234-1234-1234-123456789abc",
        [
            "",
            "pat-eu1-short",
            "EAAB-token",
            "pat-EU1-12345678-1234-1234-1234-123456789abc",
            "x" * 40,
        ],
    ),
    "meta.api_key": ("meta-key", [""]),
    "meta.base_url": (
        "https://api.example.com/v1",
        ["http://api.example.com/v1", "https://user:pw@api.example.com", "api.example.com", ""],
    ),
    "sheets.credentials_file": ("creds.json", [""]),
}


def declaration(key: str) -> tuple[ProviderDescriptor, CredentialDeclaration]:
    owner = REGISTRY.credential_owner(key)
    assert owner is not None, key
    return owner, owner.credential(key)


def problems(key: str, value: str | None, scope=None, **kwargs):
    descriptor, credential = declaration(key)
    found = {key: ProvidedCredential(value, scope if scope is not None else credential.scope)}
    result = validate_credentials(descriptor, found, **kwargs)
    # only what is said about this credential (the provider's other required ones are absent here)
    return tuple(p for p in result if p.key == key)


def codes(result) -> list[ProblemCode]:
    return [p.code for p in result]


class TestTheDeclarationsAreComplete:
    def test_every_credential_the_runtime_uses_is_declared_once(self):
        declared = {c.key for d in REGISTRY for c in d.credentials}
        assert declared == set(EXPECTED)
        all_keys = [c.key for d in REGISTRY for c in d.credentials]
        assert len(all_keys) == len(set(all_keys))

    @pytest.mark.parametrize("key", sorted(EXPECTED))
    def test_each_declaration(self, key):
        provider, scope, sensitive, required, unique = EXPECTED[key]
        descriptor, credential = declaration(key)
        assert descriptor.key == provider
        assert credential.scope is scope
        assert credential.sensitive is sensitive
        assert credential.required_when is required
        assert credential.unique is unique
        assert credential.label.strip() and credential.key == key

    @pytest.mark.parametrize("key", sorted(EXPECTED))
    def test_a_credential_with_a_pattern_has_a_rule_in_words(self, key):
        _, credential = declaration(key)
        assert bool(credential.pattern) == bool(credential.rule)

    def test_only_a_non_secret_identifier_or_setting_is_shown_to_an_admin(self):
        shown = {k for k, (_, _, sensitive, _, _) in EXPECTED.items() if not sensitive}
        assert shown == {
            "whatsapp.api_version",
            "whatsapp.phone_number_id",
            "openai.organization",
            "openai.project",
            "openai.base_url",
            "meta.base_url",
            "email.platform_mailbox",
        }

    def test_only_an_account_identifier_or_token_is_unique_among_stored_credentials(self):
        unique = {c.key for d in REGISTRY for c in d.credentials if c.unique}
        assert unique == {
            "whatsapp.phone_number_id",
            "hubspot.access_token",
            "email.platform_mailbox",
        }

    def test_the_environment_only_credential_cannot_be_stored_or_unique(self):
        _, credential = declaration("sheets.credentials_file")
        assert credential.scope is None and not credential.unique
        with pytest.raises(ValueError, match="can be unique"):
            CredentialDeclaration("x.file", "File", None, unique=True)

    def test_a_credential_key_belongs_to_one_provider(self):
        registry = ProviderRegistry([openai.DESCRIPTOR])
        clash = fake_descriptor(
            "other_llm",
            ProviderKind.LLM,
            credentials=(CredentialDeclaration("openai.api_key", "Key", ScopeKind.GLOBAL),),
        )
        with pytest.raises(DuplicateCredentialError, match="openai.api_key.*openai"):
            registry.register(clash)
        assert "other_llm" not in registry
        assert registry.credential_owner("openai.api_key") is openai.DESCRIPTOR
        assert registry.credential_owner("nope.key") is None


class TestEquivalenceWithTheCredentialCatalog:
    """The catalog stays, as the layer the Admin API and the store use. It must say what the
    descriptors say."""

    def test_the_catalog_lists_exactly_the_stored_declarations_plus_model_lists(self):
        derived = {spec.key for spec in legacy_specs(REGISTRY)}
        catalog_only = (
            MODEL_LIST_KEYS | PLATFORM_MAIL_KEYS | PLATFORM_PAYMENT_KEYS | PLATFORM_GMAIL_KEYS
        )
        assert derived - NOT_YET_IN_CATALOG == set(CATALOG_BY_KEY) - catalog_only
        assert set(CATALOG_BY_KEY) - derived == catalog_only
        assert derived & NOT_YET_IN_CATALOG == NOT_YET_IN_CATALOG

    def test_every_field_is_equal(self):
        for spec in legacy_specs(REGISTRY):
            if spec.key in NOT_YET_IN_CATALOG:
                continue
            catalog = CATALOG_BY_KEY[spec.key]
            assert spec.scope_kind is catalog.scope_kind, spec.key
            assert spec.label == catalog.label, spec.key
            assert spec.sensitive == catalog.sensitive, spec.key
            assert spec.unique == catalog.unique, spec.key
            assert (spec.pattern.pattern if spec.pattern else None) == (
                catalog.pattern.pattern if catalog.pattern else None
            ), spec.key
            assert spec.rule == catalog.rule, spec.key

    def test_the_environment_only_credential_has_no_catalog_entry(self):
        assert "sheets.credentials_file" not in CATALOG_BY_KEY
        assert "sheets.credentials_file" not in {s.key for s in legacy_specs(REGISTRY)}

    def test_meta_is_declared_but_not_yet_storable(self):
        assert not NOT_YET_IN_CATALOG & set(CATALOG_BY_KEY)
        assert {c.key for c in meta.DESCRIPTOR.credentials} == NOT_YET_IN_CATALOG

    @pytest.mark.parametrize(
        "key", sorted(set(EXPECTED) - NOT_YET_IN_CATALOG - {"sheets.credentials_file"})
    )
    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "ok-value",
            "a" * 5000,
            "bad\nvalue",
            "tab\tvalue",
            " padded ",
            "v21.0",
            "21",
            "12345678",
            "123456789012345",
            "abc",
            "https://api.example.com/v1",
            "http://api.example.com",
            "https://user:pw@host.com",
        ],
    )
    def test_a_value_is_judged_as_the_catalog_judges_it(self, key, value):
        """The same verdict and the same words as saving it through the Admin API's service."""
        kind = CATALOG_BY_KEY[key].scope_kind
        catalog = spec_for(client_scope("alpha") if kind is ScopeKind.CLIENT else GLOBAL_SCOPE, key)
        try:
            expected = ("ok", normalize_value(catalog, value))
        except InvalidCredentialError as error:
            expected = ("refused", str(error))
        result = problems(key, value)
        if expected[0] == "ok":
            assert result == ()
        else:
            assert [(p.code, p.message) for p in result] == [(ProblemCode.INVALID, expected[1])]

    def test_the_required_credentials_are_what_the_loader_requires(self):
        """`required_when` is judged by the real resolution: same credentials, same words."""
        for live, ceiling in ((True, ApprovalMode.APPROVAL), (False, ApprovalMode.DRY_RUN)):
            for removed in (
                None,
                "GOOGLE_APPLICATION_CREDENTIALS",
                "OPENAI_API_KEY",
                "ALPHA_TOKEN",
                "ALPHA_PHONE_ID",
                "ALPHA_APP_SECRET",
            ):
                self.check_against_the_loader(live, ceiling, removed)

    def check_against_the_loader(self, live, ceiling, removed):
        import tempfile

        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write_client(root, "alpha")
            env = env_for("alpha")
            env.pop(removed, None)
            config = resolve(root, ceiling=ceiling, env=env).config
        found = {}
        for descriptor in (google_sheets.DESCRIPTOR, whatsapp.DESCRIPTOR, openai.DESCRIPTOR):
            for credential in descriptor.credentials:
                found[credential.key] = ProvidedCredential(
                    config.secrets.get_optional(credential.key)
                )
        messages = set()
        for descriptor in (google_sheets.DESCRIPTOR, whatsapp.DESCRIPTOR, openai.DESCRIPTOR):
            own = {c.key: found[c.key] for c in descriptor.credentials}
            result = validate_credentials(descriptor, own, live=live)
            # a value from the environment is not pattern-checked by the loader: only absence is
            messages |= {p.message for p in result if p.code is ProblemCode.MISSING}
        assert messages == set(config.missing_credentials), (live, removed)


class TestValidatingCredentials:
    @pytest.mark.parametrize("key", sorted(VALUES))
    def test_a_valid_value_is_accepted(self, key):
        assert problems(key, VALUES[key][0]) == ()

    @pytest.mark.parametrize(
        ("key", "bad"),
        [(key, bad) for key, (_, refused) in VALUES.items() for bad in refused],
    )
    def test_an_invalid_value_is_refused_with_the_rule(self, key, bad):
        result = problems(key, bad)
        assert codes(result) == [ProblemCode.INVALID]
        assert result[0].key == key
        _, credential = declaration(key)
        if credential.rule and bad.strip():
            assert result[0].message == f"{credential.label} {credential.rule}"

    def test_a_refusal_never_carries_the_value(self):
        sentinel = "SECRET VALUE !!"  # fails every pattern a credential declares
        patterned = [key for key in EXPECTED if declaration(key)[1].pattern]
        assert patterned
        for key in patterned:
            (problem,) = problems(key, sentinel)
            assert problem.code is ProblemCode.INVALID and "SECRET" not in problem.message

    def test_an_absent_required_credential(self):
        descriptor = openai.DESCRIPTOR
        result = validate_credentials(descriptor, {})
        assert [(p.code, p.key, p.message) for p in result] == [
            (ProblemCode.MISSING, "openai.api_key", "OpenAI API key is not configured")
        ]

    def test_a_value_of_none_counts_as_absent(self):
        result = validate_credentials(
            openai.DESCRIPTOR, {"openai.api_key": ProvidedCredential(None)}
        )
        assert codes(result) == [ProblemCode.MISSING]

    def test_an_optional_credential_may_be_absent(self):
        result = validate_credentials(
            openai.DESCRIPTOR, {"openai.api_key": ProvidedCredential("k")}
        )
        assert result == ()

    def test_required_when_live_depends_on_the_mode(self):
        live = validate_credentials(whatsapp.DESCRIPTOR, {}, live=True)
        dry = validate_credentials(whatsapp.DESCRIPTOR, {}, live=False)
        assert {p.key for p in live} == {
            "whatsapp.app_secret",
            "whatsapp.access_token",
            "whatsapp.phone_number_id",
        }
        assert dry == ()

    def test_required_when_always_holds_in_a_dry_run_too(self):
        assert codes(validate_credentials(openai.DESCRIPTOR, {}, live=False)) == [
            ProblemCode.MISSING
        ]
        assert codes(validate_credentials(meta.DESCRIPTOR, {}, live=False)) == [
            ProblemCode.MISSING,
            ProblemCode.MISSING,
        ]

    def test_is_required(self):
        always = CredentialDeclaration(
            "x.a", "A", ScopeKind.GLOBAL, required_when=Requirement.ALWAYS
        )
        live = CredentialDeclaration("x.b", "B", ScopeKind.GLOBAL, required_when=Requirement.LIVE)
        optional = CredentialDeclaration("x.c", "C", ScopeKind.GLOBAL)
        assert [is_required(c, live=True) for c in (always, live, optional)] == [True, True, False]
        assert [is_required(c, live=False) for c in (always, live, optional)] == [
            True,
            False,
            False,
        ]

    def test_a_credential_the_provider_does_not_declare(self):
        result = validate_credentials(
            openai.DESCRIPTOR,
            {
                "openai.api_key": ProvidedCredential("k"),
                "whatsapp.access_token": ProvidedCredential("t"),
                "zzz.other": ProvidedCredential("x"),
            },
        )
        assert [(p.code, p.key) for p in result] == [
            (ProblemCode.UNKNOWN_CREDENTIAL, "whatsapp.access_token"),
            (ProblemCode.UNKNOWN_CREDENTIAL, "zzz.other"),
        ]

    def test_an_unknown_provider(self):
        result = validate_provider_credentials(REGISTRY, "nope", {})
        assert [(p.code, p.key) for p in result] == [(ProblemCode.UNKNOWN_PROVIDER, None)]
        assert "nope" in result[0].message

    def test_a_known_provider_through_the_registry(self):
        assert codes(validate_provider_credentials(REGISTRY, "meta", {})) == [
            ProblemCode.MISSING,
            ProblemCode.MISSING,
        ]

    @pytest.mark.parametrize(
        ("key", "given"),
        [
            ("openai.api_key", ScopeKind.CLIENT),
            ("whatsapp.access_token", ScopeKind.GLOBAL),
            ("whatsapp.phone_number_id", ScopeKind.GLOBAL),
            ("whatsapp.app_secret", ScopeKind.CLIENT),
            ("meta.api_key", ScopeKind.CLIENT),
            ("sheets.credentials_file", ScopeKind.GLOBAL),
            ("sheets.credentials_file", ScopeKind.CLIENT),
        ],
    )
    def test_a_credential_found_in_the_wrong_scope(self, key, given):
        result = problems(key, VALUES[key][0], scope=given)
        assert codes(result) == [ProblemCode.WRONG_SCOPE]
        _, credential = declaration(key)
        assert credential.label in result[0].message

    @pytest.mark.parametrize("key", sorted(set(EXPECTED) - {"sheets.credentials_file"}))
    def test_a_stored_credential_may_come_from_the_environment(self, key):
        descriptor, _ = declaration(key)
        found = {key: ProvidedCredential(VALUES[key][0], scope=None)}
        result = validate_credentials(descriptor, found)
        assert [p for p in result if p.key == key] == []

    def test_the_environment_only_credential_may_come_from_the_environment(self):
        descriptor, _ = declaration("sheets.credentials_file")
        found = {"sheets.credentials_file": ProvidedCredential("creds.json", None)}
        assert validate_credentials(descriptor, found) == ()

    def test_a_unique_value_held_elsewhere(self):
        def holder_of(key, value):
            return (
                "client:beta"
                if (key, value) == ("whatsapp.phone_number_id", "12345678901")
                else None
            )

        taken = problems("whatsapp.phone_number_id", " 12345678901 ", holder_of=holder_of)
        assert [(p.code, p.message) for p in taken] == [
            (ProblemCode.DUPLICATE, "WhatsApp phone number id is already used by client:beta")
        ]
        assert problems("whatsapp.phone_number_id", "99999999999", holder_of=holder_of) == ()

    def test_uniqueness_is_asked_only_of_unique_credentials_and_only_with_a_holder_lookup(self):
        asked = []

        def holder_of(key, value):
            asked.append(key)

        problems("whatsapp.phone_number_id", "12345678901", holder_of=holder_of)
        problems("openai.api_key", "k", holder_of=holder_of)
        assert asked == ["whatsapp.phone_number_id"]
        assert problems("whatsapp.phone_number_id", "12345678901") == ()

    def test_an_invalid_value_is_not_looked_up(self):
        asked = []
        result = problems(
            "whatsapp.phone_number_id", "abc", holder_of=lambda k, v: asked.append(k) or "x"
        )
        assert codes(result) == [ProblemCode.INVALID] and asked == []

    def test_problems_come_in_a_fixed_order(self):
        provided = {
            "zzz.b": ProvidedCredential("x"),
            "aaa.a": ProvidedCredential("x"),
            "openai.base_url": ProvidedCredential("http://x"),
        }
        result = validate_credentials(openai.DESCRIPTOR, provided)
        assert [(p.code, p.key) for p in result] == [
            (ProblemCode.UNKNOWN_CREDENTIAL, "aaa.a"),
            (ProblemCode.UNKNOWN_CREDENTIAL, "zzz.b"),
            (ProblemCode.MISSING, "openai.api_key"),
            (ProblemCode.INVALID, "openai.base_url"),
        ]


class TestMeta:
    descriptor = meta.DESCRIPTOR

    def test_the_api_key_is_a_required_secret(self):
        _, credential = declaration("meta.api_key")
        assert credential.sensitive and credential.required_when is Requirement.ALWAYS
        assert credential.scope is ScopeKind.GLOBAL

    def test_the_base_url_is_a_required_non_secret(self):
        _, credential = declaration("meta.base_url")
        assert not credential.sensitive and credential.required_when is Requirement.ALWAYS

    def test_both_are_missing_when_nothing_is_given(self):
        result = validate_credentials(self.descriptor, {})
        assert [p.message for p in result] == [
            "Meta model API key is not configured",
            "Meta model API base URL is not configured",
        ]

    @pytest.mark.parametrize(
        "url",
        [
            "http://api.example.com/v1",
            "ftp://api.example.com",
            "https://user:secret@api.example.com",
            "https://api.example.com/v1?key=1",
            "https://api example.com",
            "//api.example.com",
            "api.example.com",
        ],
    )
    def test_the_base_url_must_be_https_without_credentials(self, url):
        result = problems("meta.base_url", url)
        assert [(p.code, p.message) for p in result] == [
            (
                ProblemCode.INVALID,
                "Meta model API base URL must be an https URL without credentials",
            )
        ]

    @pytest.mark.parametrize(
        "url",
        ["https://api.example.com", "https://api.example.com/v1", "https://a.b.example.com/x/y"],
    )
    def test_an_https_base_url_is_accepted(self, url):
        assert problems("meta.base_url", url) == ()

    def test_a_complete_connection_is_valid(self):
        provided = {
            "meta.api_key": ProvidedCredential("key", ScopeKind.GLOBAL),
            "meta.base_url": ProvidedCredential("https://api.example.com/v1", ScopeKind.GLOBAL),
        }
        assert validate_credentials(self.descriptor, provided) == ()

    def test_meta_and_openai_do_not_share_credentials(self):
        meta_keys = {c.key for c in meta.DESCRIPTOR.credentials}
        assert not meta_keys & {c.key for c in openai.DESCRIPTOR.credentials}
        found = {"openai.api_key": ProvidedCredential("k")}
        assert codes(validate_credentials(meta.DESCRIPTOR, found)) == [
            ProblemCode.UNKNOWN_CREDENTIAL,
            ProblemCode.MISSING,
            ProblemCode.MISSING,
        ]

    def test_the_openai_base_url_stays_optional_while_metas_is_required(self):
        assert declaration("openai.base_url")[1].required_when is Requirement.OPTIONAL
        assert declaration("meta.base_url")[1].required_when is Requirement.ALWAYS


class TestConnectionKeys:
    def test_each_provider_names_the_credentials_that_make_a_connection(self):
        assert google_sheets.DESCRIPTOR.connection_keys == ("sheets.credentials_file",)
        assert whatsapp.DESCRIPTOR.connection_keys == (
            "whatsapp.access_token",
            "whatsapp.phone_number_id",
            "whatsapp.api_version",
        )
        assert openai.DESCRIPTOR.connection_keys == (
            "openai.api_key",
            "openai.organization",
            "openai.project",
            "openai.base_url",
        )
        assert meta.DESCRIPTOR.connection_keys == ("meta.api_key", "meta.base_url")

    def test_they_are_what_the_real_connection_checks_take(self):
        def names(descriptor):
            return {key.split(".", 1)[1] for key in descriptor.connection_keys}

        openai_parameters = set(inspect.signature(check_openai).parameters) - {"models", "http"}
        whatsapp_parameters = set(inspect.signature(check_whatsapp_client).parameters) - {
            "http",
            "base_url",
        }
        assert names(openai.DESCRIPTOR) == openai_parameters
        assert names(whatsapp.DESCRIPTOR) == whatsapp_parameters

    def test_credentials_that_only_prove_themselves_through_the_webhook_are_not_connection_keys(
        self,
    ):
        assert "whatsapp.app_secret" not in whatsapp.DESCRIPTOR.connection_keys
        assert "whatsapp.verify_token" not in whatsapp.DESCRIPTOR.connection_keys

    def test_a_connection_key_must_be_declared(self):
        with pytest.raises(ValueError, match="connection keys that are not declared"):
            fake_descriptor("fake_llm", ProviderKind.LLM, connection_keys=("fake.key",))


class TestWhatClientsMustNotShare:
    def test_the_descriptors_declare_the_rules_that_exist_today(self):
        rules = {
            (d.key, rule.kind, rule.name, rule.label)
            for d in REGISTRY
            for rule in d.unique_across_clients
        }
        assert rules == {
            ("google_sheets", "setting", "spreadsheet_id", "spreadsheet_id"),
            ("whatsapp", "credential", "whatsapp.phone_number_id", "WhatsApp phone_number_id"),
            ("hubspot", "credential", "hubspot.access_token", "HubSpot token"),
        }

    def test_a_credential_rule_names_a_unique_declared_credential(self):
        for descriptor in REGISTRY:
            for rule in descriptor.unique_across_clients:
                if rule.kind == "credential":
                    assert descriptor.credential(rule.name).unique

    def test_a_value_shared_by_two_clients(self):
        claims = [
            Claim("alpha", "google_sheets", "setting", "spreadsheet_id", "sheet-1"),
            Claim("beta", "google_sheets", "setting", "spreadsheet_id", "sheet-1"),
            Claim("gamma", "google_sheets", "setting", "spreadsheet_id", "sheet-2"),
        ]
        (shared,) = shared_values(REGISTRY, claims)
        assert shared.client_ids == ("alpha", "beta") and shared.value == "sheet-1"
        assert shared.message == "spreadsheet_id 'sheet-1' is used by several clients: alpha, beta"

    def test_the_order_of_the_first_claim_is_kept_and_a_client_counts_once(self):
        claims = [
            Claim(client, "whatsapp", "credential", "whatsapp.phone_number_id", "123456")
            for client in ("gamma", "alpha", "gamma", "beta")
        ]
        (shared,) = shared_values(REGISTRY, claims)
        assert shared.client_ids == ("gamma", "alpha", "beta")
        assert shared.message.startswith("WhatsApp phone_number_id '123456' is used by several")

    def test_one_client_repeating_a_value_shares_nothing(self):
        claims = [Claim("alpha", "google_sheets", "setting", "spreadsheet_id", "s")] * 2
        assert shared_values(REGISTRY, claims) == ()

    def test_a_claim_no_descriptor_declares_unique_is_ignored(self):
        claims = [
            Claim("alpha", "google_sheets", "setting", "prospects_tab", "Leads"),
            Claim("beta", "google_sheets", "setting", "prospects_tab", "Leads"),
            Claim("alpha", "openai", "credential", "openai.api_key", "k"),
            Claim("beta", "openai", "credential", "openai.api_key", "k"),
            Claim("alpha", "nope", "setting", "x", "v"),
            Claim("beta", "nope", "setting", "x", "v"),
        ]
        assert shared_values(REGISTRY, claims) == ()

    def test_an_empty_value_is_never_shared(self):
        claims = [Claim(c, "whatsapp", "credential", "whatsapp.phone_number_id", "") for c in "ab"]
        assert shared_values(REGISTRY, claims) == ()

    def test_the_same_value_under_two_providers_is_not_shared(self):
        registry = ProviderRegistry()
        for key in ("fake_one", "fake_two"):
            registry.register_fake(key, unique_across_clients=(_rule(),))
        claims = [
            Claim("alpha", "fake_one", "setting", "account", "same"),
            Claim("beta", "fake_two", "setting", "account", "same"),
        ]
        assert shared_values(registry, claims) == ()

    def test_a_new_provider_can_declare_its_own_rule_without_any_other_change(self):
        registry = ProviderRegistry()
        registry.register_fake("fake_crm", unique_across_clients=(_rule(),))
        claims = [
            Claim("alpha", "fake_crm", "setting", "account", "acct-1"),
            Claim("beta", "fake_crm", "setting", "account", "acct-1"),
        ]
        (shared,) = shared_values(registry, claims)
        assert shared.message == "CRM account 'acct-1' is used by several clients: alpha, beta"

    def test_the_claims_of_a_canonical_spec_come_from_its_providers_settings(self, tmp_path):
        write_client(tmp_path, "alpha", raw=fixture_text("legacy_complete.yaml"))
        spec = resolve_client_spec(tmp_path / "alpha").spec
        assert setting_claims(REGISTRY, "alpha", spec) == (
            Claim("alpha", "google_sheets", "setting", "spreadsheet_id", "sheet-alpha"),
        )
        assert setting_claims(ProviderRegistry(), "alpha", spec) == ()

    def test_the_same_answer_as_the_loader_for_shared_accounts(self, tmp_path):
        for client_id in ("alpha", "beta"):
            text = fixture_text("legacy_complete.yaml").replace(
                "client_id: alpha", f"client_id: {client_id}"
            )
            write_client(tmp_path, client_id, raw=text)
        env = {**env_for("alpha", "beta"), "BETA_PHONE_ID": "phone-alpha"}
        loaded = load_client_configs(tmp_path, env=env, mode_ceiling=ApprovalMode.AUTONOMOUS)
        claims = []
        for config in loaded.clients:
            spec = resolve_client_spec(tmp_path / config.client_id).spec
            claims.extend(setting_claims(REGISTRY, config.client_id, spec))
            claims.append(
                Claim(
                    config.client_id,
                    "whatsapp",
                    "credential",
                    "whatsapp.phone_number_id",
                    config.phone_number_id,
                )
            )
        messages = {s.message for s in shared_values(REGISTRY, claims)}
        assert messages == {
            "spreadsheet_id 'sheet-alpha' is used by several clients: alpha, beta",
            "WhatsApp phone_number_id 'phone-alpha' is used by several clients: alpha, beta",
        }
        assert messages <= set(loaded.errors)
        # what the loader reports beyond that is about environment variable names, not providers
        assert all("_env" in e for e in set(loaded.errors) - messages)


def _rule():
    from sales_app.wiring.providers import UniquenessRule

    return UniquenessRule("setting", "account", "CRM account")


class TestTheValidatorIsGeneric:
    def test_it_names_no_provider(self):
        text = Path(credentials_module.__file__).read_text(encoding="utf-8")
        for name in ("google_sheets", "whatsapp", "openai", "meta", "airtable", "sheets", "gpt"):
            assert not re.search(rf"\b{name}\b", text, flags=re.IGNORECASE), name
        assert not re.search(r"\.key\s*[=!]=\s*['\"]|provider\s*[=!]=\s*['\"]", text)

    def test_a_provider_that_did_not_exist_is_validated_by_its_declarations_alone(self):
        crm = ProviderDescriptor(
            key="fake_crm",
            kind=ProviderKind.SOURCE,
            label="Fake CRM",
            credentials=(
                CredentialDeclaration(
                    "fake_crm.token",
                    "Fake CRM token",
                    ScopeKind.CLIENT,
                    required_when=Requirement.ALWAYS,
                    pattern=re.compile(r"^crm-[0-9]{4}$"),
                    rule="must look like crm-1234",
                ),
                CredentialDeclaration(
                    "fake_crm.account",
                    "Fake CRM account",
                    ScopeKind.CLIENT,
                    sensitive=False,
                    required_when=Requirement.LIVE,
                    unique=True,
                ),
            ),
        )
        registry = ProviderRegistry([crm])
        assert [p.message for p in validate_provider_credentials(registry, "fake_crm", {})] == [
            "Fake CRM token is not configured",
            "Fake CRM account is not configured",
        ]
        assert (
            validate_provider_credentials(registry, "fake_crm", {}, live=False)[0].key
            == "fake_crm.token"
        )
        bad = {"fake_crm.token": ProvidedCredential("nope", ScopeKind.CLIENT)}
        result = validate_provider_credentials(registry, "fake_crm", bad, live=False)
        assert [(p.code, p.message) for p in result] == [
            (ProblemCode.INVALID, "Fake CRM token must look like crm-1234")
        ]
        good = {
            "fake_crm.token": ProvidedCredential("crm-1234", ScopeKind.CLIENT),
            "fake_crm.account": ProvidedCredential("acct", ScopeKind.CLIENT),
        }
        assert validate_provider_credentials(registry, "fake_crm", good) == ()
        taken = validate_provider_credentials(
            registry, "fake_crm", good, holder_of=lambda k, v: "client:x"
        )
        assert [p.code for p in taken] == [ProblemCode.DUPLICATE]

    def test_it_is_not_imported_by_the_runtime_yet(self):
        pattern = re.compile(
            r"providers\.credentials|from sales_app\.wiring\.providers import .*credentials"
        )
        importers = [
            p.relative_to(SRC).as_posix()
            for p in (SRC / "sales_app").rglob("*.py")
            if p.name != "credentials.py" and pattern.search(p.read_text(encoding="utf-8"))
        ]
        assert importers == []
