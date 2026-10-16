"""`SourceCapabilities.mirrored` changes nothing for any source that does not use it, and nothing
for how a prospect is put back together, even for one that does (E9 decision 1).

A mirrored field is a copy of Revansa's own bookkeeping that a source holds for its users. The
truth stays in the operational store: it is written under the operator's policy and never read back.
"""

import sys
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from consent_support import make_world
from factories import make_prospect
from sheets_support import SID, TUNIS, FakeSheetsApi
from sourcing_fakes import FakeOperationalStore, default_capabilities, spreadsheet_capabilities

from sales_app.sourcing import (
    SOURCE_ONLY_FIELDS,
    ProspectField,
    ProspectState,
    SourceCapabilities,
    merge_prospect,
)
from sales_app.sourcing.composite import CompositeRepository
from sales_app.sourcing.crm_source import CrmProspectSource
from sales_app.wiring.providers.google_sheets import build_prospect_source
from sales_core.contracts import ProspectId, Scheduling

F = ProspectField
TAB = "Prospects"
CLIENT = "client-a"
HEADERS = {
    "status": "Statut",
    "last_contacted_at": "Dernier contact",
    "last_inbound_at": "Derniere reponse",
    "follow_up_count": "Relances",
    "next_action_type": "Prochaine action",
    "next_action_due_at": "Echeance",
}
SENT = datetime(2026, 3, 1, 9, 0, tzinfo=UTC)


def source_over(api, *, writeback):
    # the same columns either way: named for write-back, or only for reading
    settings = {"spreadsheet_id": SID}
    if writeback:
        settings["writeback"] = {"columns": HEADERS}
    else:
        settings["column_mapping"] = {f"prospects.{k}": v for k, v in HEADERS.items()}
    return build_prospect_source(
        api, client_id=CLIENT, source_id="sheet_a", settings=settings, timezone=TUNIS
    )


def sheet_with_a_person(**cells):
    api = FakeSheetsApi()
    api.create_layout({f"prospects.{k}": v for k, v in HEADERS.items()})
    api.human_add_row(
        TAB, prospect_id="p1", display_name="Ada", phone="+216 99 100 001", Statut="new", **cells
    )
    return api


# ---- sources that do not use it ---------------------------------------------------------------------


def test_the_capabilities_of_every_source_that_does_not_mirror_are_what_they_always_were():
    assert SourceCapabilities(owned=SOURCE_ONLY_FIELDS).mirrored == frozenset()
    assert default_capabilities().mirrored == spreadsheet_capabilities().mirrored == frozenset()
    plain = source_over(FakeSheetsApi(), writeback=False).capabilities
    assert (plain.owned, plain.writable, plain.mirrored) == (
        SOURCE_ONLY_FIELDS | {F.STATUS},
        frozenset(),
        frozenset(),
    )
    assert not plain.can_create and not plain.consent_signal and not plain.atomic_compare_and_set
    crm = CrmProspectSource.__init__.__code__.co_names  # a CRM source declares no mirror either
    assert "mirrored" not in crm


def test_the_rule_for_what_a_source_may_write_is_unchanged_when_nothing_is_mirrored():
    for forbidden in (F.STAGE, F.NEXT_ACTION, F.FOLLOW_UP_COUNT, F.LANE_ID):
        with pytest.raises(ValueError):
            SourceCapabilities(owned=SOURCE_ONLY_FIELDS, writable=frozenset({forbidden}))
    with pytest.raises(ValueError):
        SourceCapabilities(owned=frozenset({F.STATUS}))  # a source always owns what a person is
    SourceCapabilities(owned=SOURCE_ONLY_FIELDS | {F.STATUS}, writable=frozenset({F.STATUS}))
    spreadsheet_capabilities()  # a spreadsheet that owns it all may write it all


def test_a_crm_source_is_read_only_and_mirrors_nothing():
    class Reader:
        def list_records(self):
            return []

    from sales_app.sourcing.statuses import StatusMapping

    crm = CrmProspectSource(
        Reader(),
        client_id=CLIENT,
        mapping=StatusMapping.from_config({"lead": "new"}),
        namespace="crm",
    )
    assert crm.capabilities.mirrored == crm.capabilities.writable == frozenset()


# ---- how a prospect is put back together ------------------------------------------------------------------


def state(**schedule):
    return ProspectState(
        client_id=CLIENT,
        prospect_id=ProspectId("sheet_a:p1"),
        schedule=Scheduling(**schedule),
    )


def test_merging_depends_on_what_a_source_owns_and_never_on_what_it_mirrors():
    record = make_prospect(prospect_id=ProspectId("p1"), client_id=CLIENT)
    held = state(last_contacted_at=SENT, follow_up_count=4)
    owned = source_over(FakeSheetsApi(), writeback=True).capabilities.owned
    twin = source_over(FakeSheetsApi(), writeback=False).capabilities.owned
    assert owned == twin
    with_block = merge_prospect(record, held, owned=owned, public_id="sheet_a:p1")
    without = merge_prospect(record, held, owned=twin, public_id="sheet_a:p1")
    assert with_block == without
    assert (
        with_block.schedule.follow_up_count == 4 and with_block.schedule.last_contacted_at == SENT
    )
    import inspect

    assert "mirrored" not in inspect.getsource(merge_prospect)


def read_through_the_composite(api, *, writeback, held=None):
    source = source_over(api, writeback=writeback)
    store = FakeOperationalStore(CLIENT)
    if held is not None:
        store.save_states([held])
    world = make_world(client_id=CLIENT)
    return CompositeRepository({"sheet_a": source}, store, world.registry), store


def test_what_the_sheet_holds_in_the_mirrored_columns_is_never_the_truth():
    cells = {
        "Dernier contact": "2026-02-01 09:00",
        "Derniere reponse": "2026-02-02 09:00",
        "Relances": 7,
        "Prochaine action": "callback",
        "Echeance": "2026-04-01 09:00",
    }
    for writeback in (False, True):
        repository, _ = read_through_the_composite(
            sheet_with_a_person(**cells), writeback=writeback
        )
        (prospect,) = repository.list_prospects()
        # exactly what a prospect Revansa never touched has: a person typing in those columns
        # changes nothing about what the Engine sees
        assert prospect.schedule == Scheduling()
        assert prospect.schedule.follow_up_count == 0


def test_a_prospect_read_through_a_source_that_writes_back_is_the_same_as_through_one_that_does_not():
    held = state(last_contacted_at=SENT, follow_up_count=2)
    seen = []
    for writeback in (False, True):
        repository, _ = read_through_the_composite(
            sheet_with_a_person(Relances=9), writeback=writeback, held=held
        )
        seen.append(
            (repository.list_prospects(), repository.get_prospect(ProspectId("sheet_a:p1")))
        )
    assert seen[0] == seen[1]
    (listed,), found = seen[1]
    assert listed == found and found.schedule.follow_up_count == 2


def test_the_engine_still_saves_its_results_to_the_store_and_the_composite_still_never_writes():
    api = sheet_with_a_person()
    repository, store = read_through_the_composite(api, writeback=True)
    current = repository.get_prospect(ProspectId("sheet_a:p1"))
    updated = replace(
        current, schedule=replace(current.schedule, last_contacted_at=SENT, follow_up_count=1)
    )
    repository.update_prospects([updated])
    assert store.list_states()[ProspectId("sheet_a:p1")].schedule.follow_up_count == 1
    assert api.count("write") == 0 and api.count("append") == 0
    assert repository.get_prospect(ProspectId("sheet_a:p1")).schedule.follow_up_count == 1
