"""A client's `sources` and `lanes`: the model, the legacy client that becomes one `default` lane, and
every refusal.

Nothing here runs a client but one check that the runtime needs the operational store for lanes (the
running of lanes is `test_lane_runtime.py`). Providers are judged by their descriptors only, so no
HubSpot account, spreadsheet or mailbox is involved.
"""

from copy import deepcopy

import pytest
import yaml
from app_support import client_yaml, env_for, fake_builders
from baseline_support import resolve
from kit_support import minimal
from pydantic import ValidationError

from sales_app.config import ClientFile, import_document, validate_settings_document
from sales_app.config.lanes import (
    DEFAULT_LANE,
    catalog_product_ids,
    effective_lanes,
    lane_problems,
)
from sales_app.config.normalize import NormalizationError, normalize
from sales_app.config.resolution import check_document
from sales_app.config_store import InMemoryConfigStore
from sales_app.errors import RuntimeBuildError
from sales_app.secret_store import GLOBAL_SCOPE, InMemorySecretStore, client_scope
from sales_app.wiring import build_runtime
from sales_core.approval import ApprovalMode
from sales_integrations.fakes import FakeClock

SPEC_FACTORY = "app_spec_brains:create_brain"
HUBSPOT = {"status_mapping": {"NEW": "new", "BAD_TIMING": "paused", "UNQUALIFIED": "closed"}}

SOURCES = [
    {"id": "sheet_a", "provider": "google_sheets", "settings": {"spreadsheet_id": "sheet-a"}},
    {"id": "hubspot", "provider": "hubspot", "settings": HUBSPOT},
    {"id": "sheet_b", "provider": "google_sheets", "settings": {"spreadsheet_id": "sheet-b"}},
]
EMAIL_SETTINGS = {
    "from_address": "sales@example.org",
    "username": "sales@example.org",
    "smtp_host": "smtp.example.org",
    "imap_host": "imap.example.org",
}
LANES = [
    {"id": "a", "source": "sheet_a", "channel": "whatsapp", "products": ["a", "b"]},
    {"id": "crm", "source": "hubspot", "channel": "email", "products": ["c"]},
    {"id": "b", "source": "sheet_b", "channel": "telegram", "products": ["a"]},
]


def catalog(*ids):
    settings = minimal()
    settings["catalog"] = {"products": [{"id": i, "name": i, "summary": "s"} for i in ids]}
    return settings


def raw_client(**overrides):
    """A `client.yaml` whose Brain has three products, a, b and c."""
    data = client_yaml("alpha")
    data["brain"] = {"factory": SPEC_FACTORY, "settings": catalog("a", "b", "c")}
    return {**data, **overrides}


def with_lanes(sources=SOURCES, lanes=LANES, **overrides):
    return raw_client(sources=deepcopy(sources), lanes=deepcopy(lanes), **overrides)


def problems_of(raw):
    check = import_document("alpha", raw)
    return list(check.errors)


# ---- a client with no lanes is exactly what it was -----------------------------------------------


def test_a_client_that_configured_nothing_is_unchanged():
    check = import_document("alpha", raw_client())
    assert check.errors == () and check.spec is not None and check.document is not None
    assert "sources" not in check.document and "lanes" not in check.document
    dumped = check.spec.model_dump(mode="json")
    assert "sources" not in dumped and "lanes" not in dumped
    assert check.spec.sources is None and check.spec.lanes is None


def test_it_becomes_one_implicit_default_lane_made_of_what_it_already_had():
    spec = import_document("alpha", raw_client()).spec
    lanes = effective_lanes(spec)
    assert lanes.explicit is False and lanes.default_lane == DEFAULT_LANE
    (lane,) = lanes.lanes
    assert (lane.id, lane.channel, lane.products) == (DEFAULT_LANE, "whatsapp", ())  # all products
    (source,) = lanes.sources
    assert source.provider == "google_sheets" and lane.source == source.id
    assert lanes.keeps_native_ids == {source.id}  # its prospects keep the ids they have always had


def test_a_legacy_client_on_hubspot_keeps_its_prefix_and_its_channel_choice():
    raw = raw_client(source={"provider": "hubspot", "settings": HUBSPOT})
    raw["channel"] = {"provider": "email"}
    lanes = effective_lanes(import_document("alpha", raw).spec)
    (source,) = lanes.sources
    assert (source.id, source.provider, dict(source.settings)) == ("hubspot", "hubspot", HUBSPOT)
    assert lanes.lanes[0].channel == "email"
    assert lanes.keeps_native_ids == frozenset()  # `hubspot:<id>`, as it has always been


def test_no_lane_problem_is_ever_raised_for_a_client_without_lanes():
    assert lane_problems(import_document("alpha", raw_client()).spec) == []


def test_an_old_document_without_lanes_still_validates_and_changes_nothing():
    wiring = raw_client()
    document = import_document("alpha", wiring).document
    again = validate_settings_document("alpha", wiring, document)
    assert again.errors == () and again.document == document


# ---- lanes that are right -------------------------------------------------------------------------


def test_several_sources_channels_and_products_coexist():
    check = import_document("alpha", with_lanes())
    assert check.errors == (), check.errors
    lanes = effective_lanes(check.spec)
    assert lanes.explicit and lanes.default_lane is None
    assert [(lane.id, lane.source, lane.channel, lane.products) for lane in lanes.lanes] == [
        ("a", "sheet_a", "whatsapp", ("a", "b")),
        ("crm", "hubspot", "email", ("c",)),
        ("b", "sheet_b", "telegram", ("a",)),
    ]
    assert lanes.source("hubspot").provider == "hubspot"


def test_two_sources_of_one_provider_are_allowed_when_it_needs_no_secret_of_the_client():
    sheets = [s["id"] for s in SOURCES if s["provider"] == "google_sheets"]
    assert sheets == ["sheet_a", "sheet_b"]
    assert import_document("alpha", with_lanes()).errors == ()


def test_lanes_survive_the_stored_document_round_trip():
    wiring = with_lanes()
    document = import_document("alpha", wiring).document
    assert [lane["id"] for lane in document["lanes"]] == ["a", "crm", "b"]
    again = validate_settings_document("alpha", wiring, document)
    assert again.errors == () and again.document == document
    assert effective_lanes(again.spec).explicit


def test_a_stored_document_alone_decides_the_lanes():
    wiring = raw_client()
    document = import_document("alpha", with_lanes()).document
    check = validate_settings_document("alpha", wiring, document)
    assert check.errors == () and effective_lanes(check.spec).explicit


def test_a_lane_document_holds_no_secret():
    document = import_document("alpha", with_lanes()).document
    assert check_document(document) == []
    leaky = deepcopy(document)
    leaky["sources"][0]["settings"]["access_token"] = "x"
    assert any("looks like a secret" in e for e in check_document(leaky))


# ---- lanes that are wrong -------------------------------------------------------------------------


def lane_error(**changes):
    lanes = deepcopy(LANES)
    lanes[0].update(changes)
    return problems_of(with_lanes(lanes=lanes))


@pytest.mark.parametrize(
    ("sources", "lanes", "needle"),
    [
        (SOURCES, None, "go together"),
        (None, LANES, "go together"),
        (SOURCES, [], "at least one lane"),
        (SOURCES + [SOURCES[0]], LANES, "used more than once"),
        (SOURCES, LANES + [LANES[0]], "used more than once"),
        (SOURCES, [{**LANES[0], "source": "ghost"}], "unknown source"),
        (SOURCES, [LANES[0], {**LANES[1], "source": "sheet_a"}], "share the source"),
    ],
)
def test_the_structure_is_refused_in_words(sources, lanes, needle):
    raw = raw_client()
    if sources is not None:
        raw["sources"] = deepcopy(sources)
    if lanes is not None:
        raw["lanes"] = deepcopy(lanes)
    errors = problems_of(raw)
    assert errors and any(needle in e for e in errors), errors


def test_a_lane_with_no_product_sells_nothing_and_is_refused():
    assert any("products" in e for e in lane_error(products=[]))


def test_a_lane_cannot_list_a_product_twice():
    assert any("twice" in e for e in lane_error(products=["a", "a"]))


def test_a_product_the_catalog_does_not_have_is_refused():
    errors = lane_error(products=["a", "ghost"])
    assert any("lanes[0].products: 'ghost' is not a product of the catalog" in e for e in errors)


def test_a_lane_needs_a_catalog():
    raw = with_lanes()
    raw["brain"]["settings"] = minimal()
    assert any("is not a product of the catalog" in e for e in problems_of(raw))


def test_lanes_replace_the_single_source_instead_of_contradicting_it():
    errors = problems_of(with_lanes(source={"provider": "hubspot", "settings": HUBSPOT}))
    assert any("lanes replace `source`" in e for e in errors)


def test_with_lanes_the_channel_section_only_holds_the_settings_of_a_channel_a_lane_uses():
    mailbox = {"provider": "email", "settings": EMAIL_SETTINGS}
    check = import_document("alpha", with_lanes(channel=mailbox))
    assert check.errors == (), check.errors
    assert check.spec.channel_settings_for("email") == EMAIL_SETTINGS
    assert check.spec.channel_settings_for("telegram") == {}  # the other channels run on defaults
    assert check.document["channel"] == mailbox  # and the document keeps it

    unused = {"provider": "telegram"}
    lanes = [lane for lane in LANES if lane["channel"] != "telegram"]
    errors = problems_of(with_lanes(lanes=lanes, sources=SOURCES[:2], channel=unused))
    assert any("channel: with lanes it only holds the settings" in e for e in errors), errors


def test_an_unknown_or_unsuitable_provider_is_refused():
    sources = deepcopy(SOURCES)
    sources[0]["provider"] = "salesforce"
    assert any(
        "unknown provider 'salesforce'" in e for e in problems_of(with_lanes(sources=sources))
    )
    sources[0]["provider"] = "openai"
    assert any("cannot be used as a source" in e for e in problems_of(with_lanes(sources=sources)))
    assert any("unknown channel" in e for e in lane_error(channel="carrier_pigeon"))
    assert any("is not a channel" in e for e in lane_error(channel="hubspot"))


def test_a_source_whose_settings_its_provider_refuses_is_refused():
    sources = deepcopy(SOURCES)
    sources[1]["settings"] = {"status_mapping": {"NEW": "active"}}  # not a status Revansa knows
    errors = problems_of(with_lanes(sources=sources))
    assert any(e.startswith("sources[1].settings.") for e in errors), errors


def test_two_accounts_of_a_provider_that_needs_the_clients_own_secret_are_refused():
    sources = [*SOURCES, {"id": "hubspot_two", "provider": "hubspot", "settings": HUBSPOT}]
    lanes = [*LANES, {"id": "crm2", "source": "hubspot_two", "channel": "email", "products": ["c"]}]
    errors = problems_of(with_lanes(sources=sources, lanes=lanes))
    assert any("one account per client" in e for e in errors), errors


def test_a_channel_the_admin_does_not_allow_is_refused_when_it_is_known():
    spec = import_document("alpha", with_lanes()).spec
    assert lane_problems(spec, allowed_channels={"whatsapp", "email", "telegram"}) == []
    errors = lane_problems(spec, allowed_channels={"whatsapp"})
    assert sorted(errors) == [
        "lanes[1].channel: the email channel is not authorized",
        "lanes[2].channel: the telegram channel is not authorized",
    ]


def test_the_ids_a_source_gives_its_prospects_cannot_be_ambiguous():
    for bad in ("Sheet-A", "a", "sheet a", "x:y"):
        sources = deepcopy(SOURCES)
        sources[0]["id"] = bad
        assert problems_of(with_lanes(sources=sources, lanes=[{**LANES[0], "source": bad}]))


def test_the_schema_models_refuse_unknown_keys():
    with pytest.raises(ValidationError):
        ClientFile.model_validate(
            {**raw_client(), "lanes": [{"id": "a", "oops": 1}], "sources": []}
        )


def test_the_catalog_ids_are_read_from_the_brain_settings():
    assert catalog_product_ids(catalog("a", "b")) == {"a", "b"}
    assert catalog_product_ids(minimal()) == frozenset()


# ---- what is not done yet is refused, never half-done ----------------------------------------------


def test_the_canonical_form_does_not_silently_drop_lanes():
    with pytest.raises(NormalizationError, match="lanes"):
        normalize(yaml.safe_load(yaml.safe_dump(with_lanes())))


def test_a_client_with_lanes_needs_the_operational_store_and_is_never_run_on_a_guess(tmp_path):
    from app_support import write_client

    wiring = with_lanes()
    write_client(tmp_path, "alpha", raw=yaml.safe_dump(raw_client()))
    store = InMemoryConfigStore()
    store.publish(
        "alpha", import_document("alpha", wiring).document, author="t", expected_parent=None
    )
    resolved = resolve(
        tmp_path, "alpha", ceiling=ApprovalMode.APPROVAL, env=env_for("alpha"), store=store
    )
    assert resolved.config is not None, resolved.errors
    builders, made = fake_builders(FakeClock())  # a deployment with no database
    with pytest.raises(RuntimeBuildError, match="operational store.*SALES_AGENT_DATABASE_URL"):
        build_runtime(resolved.config, builders, FakeClock())
    assert made.repositories == {} and made.channels == {}  # nothing was built


def test_a_lane_on_a_channel_the_admin_forbids_makes_the_client_not_ready(tmp_path):
    from app_support import write_client

    write_client(tmp_path, "alpha", raw=yaml.safe_dump(raw_client()))
    store = InMemoryConfigStore()
    document = import_document("alpha", with_lanes()).document
    store.publish("alpha", document, author="t", expected_parent=None)
    secrets = InMemorySecretStore()  # nothing allowed but WhatsApp, the default

    def missing(secret_store):
        resolved = resolve(
            tmp_path, "alpha", ceiling=ApprovalMode.APPROVAL, env=env_for("alpha"),
            store=store, secret_store=secret_store,
        )  # fmt: skip
        assert resolved.config is not None, resolved.errors
        return resolved.config.missing_credentials

    assert "The email channel is not authorized for this account" in missing(secrets)
    assert "The telegram channel is not authorized for this account" in missing(secrets)
    secrets.set(GLOBAL_SCOPE, "channels.allowed", "whatsapp,email,telegram", by="t")
    assert not [m for m in missing(secrets) if "not authorized" in m]
    secrets.set(GLOBAL_SCOPE, "channels.allowed", "whatsapp", by="t")
    secrets.set(client_scope("alpha"), "channels.access", "+email", by="t")
    assert "The email channel is not authorized for this account" not in missing(secrets)
    assert "The telegram channel is not authorized for this account" in missing(secrets)


# ---- ALL: the whole catalog (E6) ------------------------------------------------------------------


@pytest.mark.parametrize("written", ["ALL", ["ALL"]])
def test_a_lane_may_offer_all_products(written):
    raw = with_lanes(lanes=[{**LANES[0], "products": written}])
    raw["sources"] = raw["sources"][:1]
    check = import_document("alpha", raw)
    assert check.errors == (), check.errors
    lane = effective_lanes(check.spec).lanes[0]
    assert lane.products == ("ALL",) and lane.offers_all_products


def test_all_is_judged_without_a_catalog_lookup_and_follows_the_catalog():
    # `ALL` names no product, so a catalog that grows needs no change to the lane
    for ids in (("a",), ("a", "b", "c", "d")):
        raw = with_lanes(lanes=[{**LANES[0], "products": ["ALL"]}])
        raw["brain"]["settings"] = catalog(*ids)
        assert problems_of(raw) == []


def test_all_cannot_be_mixed_with_products():
    assert any("ALL stands for every product" in e for e in lane_error(products=["ALL", "a"]))


def test_no_product_can_be_called_all_so_it_never_collides_with_one():
    raw = with_lanes()  # product ids are lower case: "ALL" cannot be a catalog id
    raw["brain"]["settings"] = catalog("a", "b", "c", "ALL")
    assert problems_of(raw)


def test_a_list_of_products_does_not_offer_all():
    lane = effective_lanes(import_document("alpha", with_lanes()).spec).lanes[0]
    assert not lane.offers_all_products
