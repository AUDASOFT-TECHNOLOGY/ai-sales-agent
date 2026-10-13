"""A client with several prospect sources behind one `CompositeRepository`.

Deterministic, no network. What is real: the composite, the pure Sheets source, the Sheets adapter
(over `FakeSheetsApi`, an in-memory grid that stands for a real spreadsheet: nothing here proves a
real Google call), the consent registry, and, in half of the runs, the SQL operational store over a
real database. HubSpot is `FakeProspectSource`, a CRM-shaped source: nothing here proves a real
HubSpot call either.

Every test runs on the in-memory store and on the SQL store: the guarantees are the port's.
"""

import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from consent_support import contact as consent_contact
from consent_support import make_world
from factories import NOW, make_action, make_message, make_prospect
from sheets_support import SID, TUNIS, FakeSheetsApi
from sourcing_fakes import FakeOperationalStore, FakeProspectSource, default_capabilities
from sql_store_support import SqlStoreUnderTest

from sales_app.sourcing import (
    ProblemCode,
    SourceReadOnlyError,
    SourceUnavailableError,
    StatusMapping,
)
from sales_app.sourcing.composite import KEYWORD_OPT_OUT_EVENT, CompositeRepository
from sales_app.wiring.providers.google_sheets import build_prospect_source
from sales_core.contracts import (
    AuditEvent,
    ClientId,
    ContactInfo,
    Fact,
    FactStatus,
    ProspectId,
    ProspectStatus,
)
from sales_core.ports import SalesRepository

CLIENT = ClientId("alpha")
S = ProspectStatus
TAB = "Prospects"
PHONE_ADA = "+216 99 888 777"
PHONE_BOB = "+216 22 111 000"
PHONE_CLEO = "+216 55 444 333"
PHONE_DINA = "+216 77 666 555"
MAPPING = StatusMapping.from_config({"lead": "new", "hold": "paused", "dnc": "opted_out"})


def sheet(*rows, **options) -> FakeSheetsApi:
    """A spreadsheet of people: (id, name, phone[, status]) per row."""
    api = FakeSheetsApi()
    api.create_layout()
    for pid, name, phone, *status in rows:
        cells = {"prospect_id": pid, "display_name": name, "phone": phone}
        if status:
            cells["status"] = status[0]
        api.human_add_row(TAB, **cells)
    return api


def sheets_source(api, source_id):
    return build_prospect_source(
        api,
        client_id=CLIENT,
        source_id=source_id,
        settings={"spreadsheet_id": SID},
        timezone=TUNIS,
    )


def hubspot_like(**capabilities):
    return FakeProspectSource(
        CLIENT,
        capabilities=default_capabilities(id_namespace="hubspot", **capabilities),
        status_mapping=MAPPING,
    )


def crm_person(pid, phone=None, email=None, **overrides):
    contact = ContactInfo(phone=phone, email=email)
    return make_prospect(
        client_id=CLIENT, prospect_id=ProspectId(pid), contact=contact, **overrides
    )


class Setup:
    def __init__(self, sources, *, store):
        self.sources = sources
        self.store = store
        self.world = make_world(client_id=str(CLIENT))
        self.repo = CompositeRepository(sources, store, self.world.registry)


@pytest.fixture(params=["memory", "sql"])
def make_store(request, sql_engine):
    def make():
        if request.param == "memory":
            return FakeOperationalStore(CLIENT)
        return SqlStoreUnderTest(sql_engine, CLIENT)

    return make


@pytest.fixture
def two_sheets(make_store):
    """Two spreadsheets that use the same ids for different people."""
    a = sheet(("p1", "Ada", PHONE_ADA), ("p2", "Bob", PHONE_BOB))
    b = sheet(("p1", "Cleo", PHONE_CLEO), ("p2", "Dina", PHONE_DINA))
    return (
        a,
        b,
        Setup(
            {"sheet_a": sheets_source(a, "sheet_a"), "sheet_b": sheets_source(b, "sheet_b")},
            store=make_store(),
        ),
    )


def by_id(repo):
    return {p.prospect_id: p for p in repo.list_prospects()}


def names(repo):
    return {pid: p.display_name for pid, p in by_id(repo).items()}


# ---- two sheets with the same ids ------------------------------------------------------------


def test_it_is_a_sales_repository(two_sheets):
    assert isinstance(two_sheets[2].repo, SalesRepository)


def test_identical_native_ids_of_two_sheets_never_meet(two_sheets):
    *_, env = two_sheets
    assert names(env.repo) == {
        "sheet_a:p1": "Ada",
        "sheet_a:p2": "Bob",
        "sheet_b:p1": "Cleo",
        "sheet_b:p2": "Dina",
    }
    assert env.repo.get_prospect(ProspectId("sheet_a:p1")).display_name == "Ada"
    assert env.repo.get_prospect(ProspectId("sheet_b:p1")).display_name == "Cleo"
    # a bare native id belongs to nobody once every source has a namespace
    assert env.repo.get_prospect(ProspectId("p1")) is None
    assert env.repo.get_prospect(ProspectId("sheet_c:p1")) is None


def test_a_person_is_found_by_contact_in_the_sheet_that_holds_them(two_sheets):
    *_, env = two_sheets
    assert env.repo.find_prospect_by_contact(phone=PHONE_ADA).prospect_id == "sheet_a:p1"
    assert env.repo.find_prospect_by_contact(phone=PHONE_DINA).prospect_id == "sheet_b:p2"
    assert env.repo.find_prospect_by_contact(phone="+216 00 000 000") is None


def test_revansas_state_of_one_sheets_prospect_never_touches_the_other(two_sheets):
    *_, env = two_sheets
    ada = env.repo.get_prospect(ProspectId("sheet_a:p1"))
    env.repo.update_prospects([replace(ada, status=S.PAUSED, stage="warm")])
    listed = by_id(env.repo)
    assert listed["sheet_a:p1"].status is S.PAUSED and listed["sheet_a:p1"].stage == "warm"
    assert listed["sheet_b:p1"].status is S.NEW and listed["sheet_b:p1"].stage is None
    assert set(env.store.list_states()) == {"sheet_a:p1"}


def test_actions_messages_and_facts_are_kept_apart_by_public_id(two_sheets):
    *_, env = two_sheets
    for pid, key in (("sheet_a:p1", "a"), ("sheet_b:p1", "b")):
        env.repo.save_actions(
            [
                make_action(
                    action_id=f"act-{key}",
                    client_id=CLIENT,
                    prospect_id=ProspectId(pid),
                    idempotency_key=f"alpha:{pid}:first_contact:2026-01-15",
                )
            ]
        )
        env.repo.append_messages(
            [
                make_message(
                    message_id=f"msg-{key}",
                    client_id=CLIENT,
                    prospect_id=ProspectId(pid),
                    conversation_id=f"conv-{key}",
                    text=f"hello {key}",
                )
            ]
        )
        env.repo.append_facts(
            ProspectId(pid),
            [Fact(key="interest", value=key, status=FactStatus.CONFIRMED, recorded_at=NOW)],
        )
    assert {a.prospect_id for a in env.repo.list_actions()} == {"sheet_a:p1", "sheet_b:p1"}
    assert [m.text for m in env.repo.get_conversation(ProspectId("sheet_a:p1")).messages] == [
        "hello a"
    ]
    assert [m.text for m in env.repo.get_conversation(ProspectId("sheet_b:p1")).messages] == [
        "hello b"
    ]
    assert [f.value for f in env.repo.get_facts(ProspectId("sheet_a:p1"))] == ["a"]
    assert [f.value for f in env.repo.get_facts(ProspectId("sheet_b:p1"))] == ["b"]


def test_facts_are_checked_in_the_source_the_id_belongs_to(two_sheets):
    *_, env = two_sheets
    fact = Fact(key="interest", value="x", status=FactStatus.CONFIRMED, recorded_at=NOW)
    for ghost in ("sheet_a:nobody", "sheet_c:p1", "p1"):
        with pytest.raises(KeyError):
            env.repo.append_facts(ProspectId(ghost), [fact])
    assert env.store.get_facts(ProspectId("sheet_c:p1")) == []


def test_nothing_is_ever_written_to_a_sheet(two_sheets):
    a, b, env = two_sheets
    ada = env.repo.get_prospect(ProspectId("sheet_a:p1"))
    env.repo.update_prospects([replace(ada, status=S.CLOSED)])
    env.repo.create_prospects([crm_person("inbound-2026-01-15-1", "+216 11 000 111")])
    env.repo.list_prospects()
    for api in (a, b):
        assert api.count("write") == 0 and api.count("append") == 0


def test_a_field_of_the_person_cannot_be_changed_through_the_repository(two_sheets):
    *_, env = two_sheets
    ada = env.repo.get_prospect(ProspectId("sheet_a:p1"))
    with pytest.raises(SourceReadOnlyError):
        env.repo.update_prospects([replace(ada, display_name="Someone Else")])
    assert env.store.list_states() == {}


def test_an_unknown_sender_becomes_a_local_prospect_of_the_client_not_of_a_sheet(two_sheets):
    *_, env = two_sheets
    local = crm_person("inbound-2026-01-15-216111", "+216 11 000 111")
    env.repo.create_prospects([local])
    assert env.repo.get_prospect(local.prospect_id).display_name == local.display_name
    assert (
        env.repo.find_prospect_by_contact(phone="+216 11 000 111").prospect_id == local.prospect_id
    )
    assert set(by_id(env.repo)) >= {"sheet_a:p1", "sheet_b:p1", local.prospect_id}
    # a contact one of the sheets holds cannot be created again, whichever sheet it is
    for phone in (PHONE_ADA, PHONE_DINA):
        with pytest.raises(ValueError):
            env.repo.create_prospects([crm_person("inbound-2026-01-15-2", phone)])


def test_a_human_who_opts_a_person_out_in_one_sheet_binds_only_that_person(two_sheets):
    a, _, env = two_sheets
    a.human_set(TAB, 2, "status", "opted_out")
    listed = by_id(env.repo)
    assert listed["sheet_a:p1"].status is S.OPTED_OUT
    assert listed["sheet_a:p2"].status is S.NEW and listed["sheet_b:p1"].status is S.NEW


def test_a_keyword_opt_out_in_one_sheet_blocks_that_contact_everywhere(two_sheets):
    *_, env = two_sheets
    event = AuditEvent(
        timestamp=NOW,
        client_id=CLIENT,
        event_type=KEYWORD_OPT_OUT_EVENT,
        actor="core",
        prospect_id=ProspectId("sheet_b:p1"),
    )
    env.repo.append_audit_events([event])
    assert env.world.registry.snapshot().blocks(consent_contact(PHONE_CLEO))
    listed = by_id(env.repo)
    assert listed["sheet_b:p1"].status is S.OPTED_OUT and listed["sheet_a:p1"].status is S.NEW


def test_a_failing_sheet_fails_the_whole_read(make_store):
    ok = sheets_source(sheet(("p1", "Ada", PHONE_ADA)), "sheet_a")
    broken_api = sheet(("p1", "Cleo", PHONE_CLEO))
    env = Setup(
        {"sheet_a": ok, "sheet_b": sheets_source(broken_api, "sheet_b")}, store=make_store()
    )
    from sales_integrations.google_sheets import SheetsApiError

    broken_api.fail_next("read", SheetsApiError("down", status=503))
    with pytest.raises(SourceUnavailableError):  # never a list of one sheet only
        env.repo.list_prospects()


def test_the_problems_of_each_sheet_say_which_sheet_they_come_from(make_store):
    a = sheet(("p1", "Ada", PHONE_ADA), ("bad:id", "Bob", PHONE_BOB))
    b = sheet(("p1", "Cleo", PHONE_CLEO), ("p1", "Dina", PHONE_DINA))
    env = Setup(
        {"sheet_a": sheets_source(a, "sheet_a"), "sheet_b": sheets_source(b, "sheet_b")},
        store=make_store(),
    )
    env.repo.list_prospects()
    found = {(p.source_id, p.code) for p in env.repo.problems()}
    assert found == {
        ("sheet_a", ProblemCode.INVALID_NATIVE_ID),
        ("sheet_b", ProblemCode.DUPLICATE_ID),
    }


def test_verification_names_the_sheet_and_keeps_soft_warnings_soft(make_store):
    a = sheet(("p1", "Ada", PHONE_ADA))
    b = sheet(("p1", "Cleo", PHONE_CLEO))
    b.tabs[TAB][0].remove("phone")
    sources = {"sheet_a": sheets_source(a, "sheet_a"), "sheet_b": sheets_source(b, "sheet_b")}
    issues = Setup(sources, store=make_store()).repo.verify()
    assert issues and all(issue.startswith("[sheet_b] ") for issue in issues)
    fake = FakeProspectSource(CLIENT, capabilities=default_capabilities(id_namespace="x_crm"))
    fake.verify_issues = ["warning: slow"]
    soft = Setup({"x_crm": fake, "sheet_a": sources["sheet_a"]}, store=make_store()).repo.verify()
    assert soft == ["warning: [x_crm] slow"]


# ---- clients with a single source are unchanged ----------------------------------------------


def test_a_client_with_one_historical_sheet_keeps_its_ids_and_its_state(make_store):
    api = sheet(("p1", "Ada", PHONE_ADA), ("legacy id 7", "Bob", PHONE_BOB))
    store = make_store()
    # The state Revansa already holds under the sheet's own id, from before.
    single = CompositeRepository(
        sheets_source(api, None), store, make_world(client_id="alpha").registry
    )
    listed = by_id(single)
    assert set(listed) == {"p1", "legacy id 7"}
    single.update_prospects([replace(listed["p1"], status=S.PAUSED, stage="warm")])
    # The same client, now declared as a mapping with one source, reads the same ids and state.
    world = make_world(client_id="alpha")
    named = CompositeRepository({"main": sheets_source(api, None)}, store, world.registry)
    again = by_id(named)
    assert set(again) == {"p1", "legacy id 7"}
    assert again["p1"].status is S.PAUSED and again["p1"].stage == "warm"
    assert named.get_prospect(ProspectId("p1")).display_name == "Ada"
    assert named.problems() == single.problems() == ()  # one source: nothing is tagged


def test_a_single_source_given_directly_still_works_as_before(make_store):
    api = sheet(("p1", "Ada", PHONE_ADA))
    env = Setup(sheets_source(api, "sheet_a"), store=make_store())
    assert set(by_id(env.repo)) == {"sheet_a:p1"}


def test_a_historical_sheet_can_live_beside_new_sources(make_store):
    legacy = sheet(("p1", "Ada", PHONE_ADA), ("sheet_b:x", "Eve", "+216 10 101 010"))
    other = sheet(("p1", "Cleo", PHONE_CLEO))
    env = Setup(
        {"main": sheets_source(legacy, None), "sheet_b": sheets_source(other, "sheet_b")},
        store=make_store(),
    )
    assert names(env.repo) == {"p1": "Ada", "sheet_b:p1": "Cleo"}
    assert env.repo.get_prospect(ProspectId("p1")).display_name == "Ada"
    assert env.repo.get_prospect(ProspectId("sheet_b:p1")).display_name == "Cleo"
    # a historical id that looks like another source's cannot be told apart: it is left out
    assert env.repo.get_prospect(ProspectId("sheet_b:x")) is None
    assert env.repo.find_prospect_by_contact(phone="+216 10 101 010") is None
    assert ProblemCode.ID_COLLISION in {p.code for p in env.repo.problems()}
    assert {p.source_id for p in env.repo.problems()} == {"main"}


# ---- each source keeps what it can do --------------------------------------------------------


@pytest.fixture
def mixed(make_store):
    """A Google Sheet and a HubSpot-like CRM, whose people have the same native id."""
    crm = hubspot_like()
    crm.add_record(crm_person("p1", PHONE_BOB), raw_status="hold")
    crm.add_record(crm_person("p2", PHONE_DINA), raw_status="lead")
    crm.add_record(crm_person("p3", "+216 00 999 888"), raw_status="dnc")
    api = sheet(("p1", "Ada", PHONE_ADA), ("p2", "Cleo", PHONE_CLEO))
    env = Setup({"sheet": sheets_source(api, "sheet"), "hubspot": crm}, store=make_store())
    env.api, env.crm = api, crm
    return env


def test_the_status_of_the_crm_is_kept_and_the_sheets_does_not_replace_it(mixed):
    listed = by_id(mixed.repo)
    assert set(listed) == {"sheet:p1", "sheet:p2", "hubspot:p1", "hubspot:p2", "hubspot:p3"}
    assert listed["hubspot:p1"].status is S.PAUSED  # the CRM's own word
    assert listed["hubspot:p3"].status is S.OPTED_OUT  # its do-not-contact signal
    assert listed["sheet:p1"].status is S.NEW


def test_a_refusal_the_crm_reports_for_a_record_it_left_out_binds_a_sheet_person(mixed):
    mixed.crm.add_record(
        crm_person("p4", PHONE_CLEO), raw_status="not in the mapping", do_not_contact=True
    )
    listed = by_id(mixed.repo)  # the sheet's Cleo (sheet:p2) shares the refused number
    assert "hubspot:p4" not in listed
    assert listed["sheet:p2"].status is S.OPTED_OUT
    assert mixed.world.registry.snapshot().blocks(consent_contact(PHONE_CLEO))
    assert listed["sheet:p1"].status is S.NEW


def test_revansas_own_status_beats_a_contactable_sheet_but_not_the_other_way_round(mixed):
    ada = mixed.repo.get_prospect(ProspectId("sheet:p1"))
    mixed.repo.update_prospects([replace(ada, status=S.ACTIVE, stage="warm")])
    assert mixed.repo.get_prospect(ProspectId("sheet:p1")).status is S.ACTIVE
    mixed.api.human_set(TAB, 2, "status", "opted_out")  # the sheet's owner opts her out
    assert mixed.repo.get_prospect(ProspectId("sheet:p1")).status is S.OPTED_OUT
    mixed.api.human_set(TAB, 2, "status", "new")
    mixed.repo.update_prospects([replace(ada, status=S.PAUSED)])
    mixed.api.human_set(TAB, 2, "status", "active")
    assert mixed.repo.get_prospect(ProspectId("sheet:p1")).status is S.PAUSED


def test_a_status_change_to_the_crms_person_goes_to_the_store_never_to_the_crm(mixed):
    person = mixed.repo.get_prospect(ProspectId("hubspot:p2"))
    mixed.repo.update_prospects([replace(person, status=S.CLOSED)])
    assert mixed.repo.get_prospect(ProspectId("hubspot:p2")).status is S.CLOSED
    assert not [r for r in mixed.crm.requests if r.startswith("write")]
    assert mixed.crm.records["p2"].status is S.NEW  # the CRM's record is untouched
    assert mixed.repo.get_prospect(ProspectId("sheet:p2")).status is S.NEW


def test_the_same_person_in_two_sources_is_listed_once_and_reported(make_store):
    crm = hubspot_like()
    crm.add_record(crm_person("p1", PHONE_ADA), raw_status="lead")
    api = sheet(("p1", "Ada", PHONE_ADA), ("p2", "Bob", PHONE_BOB))
    env = Setup({"sheet": sheets_source(api, "sheet"), "hubspot": crm}, store=make_store())
    listed = by_id(env.repo)
    assert set(listed) == {"sheet:p1", "sheet:p2"}  # the first source in order is the one used
    (conflict,) = [p for p in env.repo.problems() if p.code is ProblemCode.DUPLICATE_CONTACT]
    assert conflict.source_id == "hubspot"


def test_a_refusal_in_the_source_set_aside_follows_the_person(make_store):
    crm = hubspot_like()
    crm.add_record(crm_person("p1", PHONE_ADA), raw_status="dnc")
    api = sheet(("p1", "Ada", PHONE_ADA, "active"))
    env = Setup({"sheet": sheets_source(api, "sheet"), "hubspot": crm}, store=make_store())
    assert by_id(env.repo)["sheet:p1"].status is S.OPTED_OUT


# ---- the assembly is checked -----------------------------------------------------------------


def test_the_assembly_is_refused_when_it_cannot_be_unambiguous(make_store):
    store, registry = make_store(), make_world(client_id="alpha").registry
    a = sheets_source(sheet(), "sheet_a")
    with pytest.raises(ValueError):
        CompositeRepository({}, store, registry)
    with pytest.raises(ValueError):  # two sources would name their prospects the same way
        CompositeRepository({"one": a, "two": sheets_source(sheet(), "sheet_a")}, store, registry)
    with pytest.raises(ValueError):  # two sources would both keep native ids
        CompositeRepository(
            {"one": sheets_source(sheet(), None), "two": sheets_source(sheet(), None)},
            store,
            registry,
        )
    with pytest.raises(ValueError):
        CompositeRepository({"": a}, store, registry)
    other = build_prospect_source(
        sheet(),
        client_id="beta",
        source_id="sheet_b",
        settings={"spreadsheet_id": SID},
        timezone=TUNIS,
    )
    with pytest.raises(ValueError):  # a source of another client
        CompositeRepository({"one": a, "two": other}, store, registry)


def test_nothing_personal_is_in_a_repr_or_a_problem(two_sheets):
    a, _, env = two_sheets
    a.human_add_row(TAB, prospect_id="bad:id", display_name="Zed Zed", phone="+216 12 345 678")
    env.repo.list_prospects()
    assert env.repo.problems()
    shown = repr(env.repo) + repr(env.repo.problems()) + repr(env.sources["sheet_a"])
    for private in ("Ada", "Zed", "216", "345", "bad:id"):
        assert private not in shown


# ---- a refusal follows the person, in every way of reading -----------------------------------


@pytest.fixture
def twins(make_store):
    """Ada is in two sheets under the same number; in the second one she opted out."""
    a = sheet(("p1", "Ada", PHONE_ADA, "active"))
    b = sheet(("p9", "Ada", PHONE_ADA, "opted_out"))
    env = Setup(
        {"sheet_a": sheets_source(a, "sheet_a"), "sheet_b": sheets_source(b, "sheet_b")},
        store=make_store(),
    )
    env.a, env.b = a, b
    return env


def test_the_refusal_of_a_set_aside_twin_holds_in_the_list_the_id_lookup_and_the_contact_lookup(
    twins,
):
    repo = twins.repo
    assert by_id(repo)["sheet_a:p1"].status is S.OPTED_OUT
    assert repo.get_prospect(ProspectId("sheet_a:p1")).status is S.OPTED_OUT
    assert repo.find_prospect_by_contact(phone=PHONE_ADA).status is S.OPTED_OUT
    assert repo.get_prospect(ProspectId("sheet_b:p9")).status is S.OPTED_OUT


def test_a_refusal_in_the_first_source_holds_when_the_second_is_read_directly(make_store):
    a = sheet(("p1", "Ada", PHONE_ADA, "opted_out"))
    b = sheet(("p9", "Ada", PHONE_ADA, "active"))
    env = Setup(
        {"sheet_a": sheets_source(a, "sheet_a"), "sheet_b": sheets_source(b, "sheet_b")},
        store=make_store(),
    )
    assert env.repo.get_prospect(ProspectId("sheet_b:p9")).status is S.OPTED_OUT
    assert env.repo.find_prospect_by_contact(phone=PHONE_ADA).status is S.OPTED_OUT


def test_a_refusal_held_by_revansa_for_the_twin_holds_too(make_store):
    crm = hubspot_like()
    crm.add_record(crm_person("p1", PHONE_ADA), raw_status="lead")
    a = sheet(("p1", "Ada", PHONE_ADA, "active"))
    env = Setup({"sheet": sheets_source(a, "sheet"), "hubspot": crm}, store=make_store())
    twin = env.repo.get_prospect(ProspectId("hubspot:p1"))
    env.repo.update_prospects([replace(twin, status=S.OPTED_OUT)])
    assert env.repo.get_prospect(ProspectId("sheet:p1")).status is S.OPTED_OUT
    assert by_id(env.repo)["sheet:p1"].status is S.OPTED_OUT


def test_the_refusal_of_one_of_three_sources_reaches_all_the_others(make_store):
    apis = [
        sheet((f"p{i}", "Ada", PHONE_ADA, "opted_out" if i == 3 else "active")) for i in (1, 2, 3)
    ]
    env = Setup(
        {f"s{i}": sheets_source(api, f"s{i}") for i, api in enumerate(apis, 1)}, store=make_store()
    )
    assert env.repo.get_prospect(ProspectId("s1:p1")).status is S.OPTED_OUT
    assert env.repo.get_prospect(ProspectId("s2:p2")).status is S.OPTED_OUT
    assert by_id(env.repo)["s1:p1"].status is S.OPTED_OUT


def test_other_restrictions_do_not_follow_the_person_and_other_people_are_untouched(make_store):
    a = sheet(("p1", "Ada", PHONE_ADA, "active"), ("p2", "Bob", PHONE_BOB, "active"))
    b = sheet(("p9", "Ada", PHONE_ADA, "closed"), ("p8", "Cleo", PHONE_CLEO, "opted_out"))
    env = Setup(
        {"sheet_a": sheets_source(a, "sheet_a"), "sheet_b": sheets_source(b, "sheet_b")},
        store=make_store(),
    )
    assert env.repo.get_prospect(ProspectId("sheet_a:p1")).status is S.ACTIVE
    assert env.repo.get_prospect(ProspectId("sheet_a:p2")).status is S.ACTIVE


def test_a_single_source_never_asks_another_source(make_store):
    api = sheet(("p1", "Ada", PHONE_ADA, "active"))
    env = Setup({"only": sheets_source(api, None)}, store=make_store())
    api.calls.clear()
    env.repo.get_prospect(ProspectId("p1"))
    assert api.count("read") == 1
