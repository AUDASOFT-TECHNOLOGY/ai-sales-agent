"""Write-back wired into a client's runtime, its scheduler and its health (E9, lot 4).

What is real: the `Application` (configuration loading and validation, registry, scheduler, health),
`build_runtime`, the real SQL operational store and durable ledger over a migrated database, the real
consent registry, `CompositeRepository`, and the Google Sheets source and adapter. What is simulated:
the Google Sheets API (`FakeSheetsApi`, an in-memory grid), the messaging channels and the clock.
Nothing here proves that a real Google Sheet, credential or quota behaves like the double, and
nothing was run on PostgreSQL.
"""

import sys
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))

import pytest
from sheets_support import TUNIS, FakeSheetsApi
from test_lane_runtime import Deployment, raw_for, secrets_for

from sales_app.api.client_agent import lane_sources_fingerprint
from sales_app.config import import_document
from sales_app.errors import ConfigurationError, RuntimeBuildError
from sales_app.scheduler import Job
from sales_app.sourcing import ProspectField, ProspectState
from sales_app.wiring import build_runtime
from sales_app.wiring.providers.google_sheets import build_prospect_source
from sales_app.wiring.writeback import WritebackSupport, default_policies, sql_ledgers
from sales_core.contracts import ProspectId, Scheduling

F = ProspectField
TAB = "Prospects"
HEADERS = {
    "status": "Statut",
    "last_contacted_at": "Dernier contact",
    "last_inbound_at": "Derniere reponse",
    "follow_up_count": "Relances",
    "next_action_type": "Prochaine action",
    "next_action_due_at": "Echeance",
}
WRITEBACK = {"columns": HEADERS}
PHONE = "+216 99 100 001"


def sheet(*people):
    api = FakeSheetsApi()
    api.create_layout({f"prospects.{key}": header for key, header in HEADERS.items()})
    for pid, name, phone in people:
        api.human_add_row(TAB, prospect_id=pid, display_name=name, phone=phone, Statut="new")
    return api


def raw_with_sheets(cid, sources, *, writeback=WRITEBACK, mode=None):
    """A client whose lanes run on Google Sheets sources (ids in `sources`), each with a write-back
    block unless it is `None`."""
    entries = []
    for sid in sources:
        settings = {"spreadsheet_id": f"{cid}-{sid}"}
        if writeback is not None:
            settings["writeback"] = writeback
        entries.append({"id": sid, "provider": "google_sheets", "settings": settings})
    lanes = [
        {"id": f"l{n}", "source": sid, "channel": "whatsapp", "products": ["a"]}
        for n, sid in enumerate(sources)
    ]
    raw = raw_for(cid, sources=entries, lanes=lanes, channel=None)
    return {**raw, "mode": mode} if mode else raw


class WritebackDeployment(Deployment):
    """Sheets sources over in-memory spreadsheets, and the deployment's write-back switch."""

    def __init__(self, root, engine, clients, secrets, *, apis, enabled=True, **kwargs):
        self.apis, self.enabled = apis, enabled  # (client, source id) -> FakeSheetsApi
        super().__init__(root, engine, clients, secrets, **kwargs)

    def builders(self):
        def build_source(config, instance):
            return build_prospect_source(
                self.apis[(config.client_id, instance.id)],
                client_id=config.client_id,
                source_id=instance.id,
                settings=instance.settings,
                timezone=TUNIS,
            )

        return replace(
            super().builders(),
            source_instances={"google_sheets": build_source},
            writeback=WritebackSupport(
                enabled=self.enabled,
                open_ledger=sql_ledgers(self.engine),
                policies=default_policies(),
            ),
        )

    def revansa(self, cid, public_id, **schedule):
        """What the Engine would have recorded for a prospect: its own state, in the store."""
        self.stores[cid].save_states(
            [
                ProspectState(
                    client_id=cid,
                    prospect_id=ProspectId(public_id),
                    schedule=Scheduling(**schedule),
                )
            ]
        )

    def sync(self, cid):
        return self.application.scheduler.run_job_now(self.runtime(cid), Job.SYNC, self.clock.now())

    def due(self, cid, now=None):
        return self.application.scheduler.due_jobs(self.runtime(cid), now or self.clock.now())


@pytest.fixture
def deploy_sheets(tmp_path, sql_engine):
    made = []

    def make(clients, apis, secrets=None, **kwargs):
        deployment = WritebackDeployment(
            tmp_path,
            sql_engine,
            clients,
            secrets or secrets_for(*clients, email=False, telegram=False, hubspot=False),
            apis=apis,
            **kwargs,
        )
        made.append(deployment)
        return deployment

    yield make
    for deployment in made:
        deployment.close()


def cell(api, row, header):
    return api.cell(TAB, row, header)


# ---- off unless asked, twice ---------------------------------------------------------------------------


def test_the_deployments_switch_keeps_every_source_untouched_even_with_a_block(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets(
        {"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api}, enabled=False
    )
    d.revansa("alpha", "sa:p1", follow_up_count=3)
    runtime = d.runtime("alpha")
    assert runtime.writeback is None
    assert Job.SYNC not in d.due("alpha")
    d.application.scheduler.tick()
    assert api.count("write") == 0 and "writeback" not in runtime.health.snapshot()


def test_a_source_without_a_block_is_never_switched_on_by_the_deployment(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets(
        {"alpha": raw_with_sheets("alpha", ["sa"], writeback=None)}, {("alpha", "sa"): api}
    )
    assert d.runtime("alpha").writeback is None
    assert Job.SYNC not in d.due("alpha")


def test_the_default_deployment_and_settings_have_it_off():
    from sales_app.settings import Settings

    assert Settings().writeback_enabled is False
    assert WritebackSupport().enabled is False
    from sales_app.wiring import default_builders

    assert default_builders(Settings()).writeback is None


def test_in_the_global_dry_run_mode_no_syncer_exists_so_nothing_is_read_or_written(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    clients = {"alpha": raw_with_sheets("alpha", ["sa"], mode="dry_run")}
    d = deploy_sheets(clients, {("alpha", "sa"): api}, mode_of={"alpha": "dry_run"})
    d.revansa("alpha", "sa:p1", follow_up_count=3)
    runtime = d.runtime("alpha")
    assert str(runtime.config.effective_mode) == "dry_run"
    assert runtime.writeback is None and Job.SYNC not in d.due("alpha")
    d.application.scheduler.tick()
    # The client's own cycle may read its source, as it always does in a dry run; the write-back
    # does not exist: nothing is written, no run is recorded and the ledger is empty.
    snapshot = runtime.health.snapshot()
    assert api.count("write") == 0 and "writeback" not in snapshot
    assert "sync" not in snapshot["last_runs"]
    assert sql_ledgers(d.engine)("alpha").entries() == ()
    assert cell(api, 2, "Relances") == ""


# ---- when it is on ---------------------------------------------------------------------------------------


def test_a_source_that_asked_gets_a_syncer_and_the_job_writes_its_results(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    d.revansa("alpha", "sa:p1", follow_up_count=3)
    runtime = d.runtime("alpha")
    assert runtime.writeback is not None and runtime.writeback.source_ids == ("sa",)
    result = d.sync("alpha")
    assert result.ok and result.errors == ()
    assert cell(api, 2, "Relances") == "3"
    assert api.count("write") == 1


def test_the_health_shows_counters_and_only_counters(deploy_sheets):
    api = sheet(("p1", "Ada Lovelace", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    d.revansa("alpha", "sa:p1", follow_up_count=3)
    d.sync("alpha")
    snapshot = d.runtime("alpha").health.snapshot()
    counters = snapshot["writeback"]
    assert set(counters) >= {"sources", "applied", "conflicts", "not_ready", "consecutive_failures"}
    assert all(type(value) is int for value in counters.values())
    assert counters["applied"] == 1 and counters["sources"] == 1
    assert snapshot["status"] == "ready" and snapshot["consecutive_failures"] == 0
    assert "sync" in snapshot["last_runs"]
    text = repr(d.application.registry.health())
    for leak in (PHONE, "Ada", "Lovelace", "alpha-sa", HEADERS["follow_up_count"]):
        assert leak not in text


def test_the_job_is_due_every_ten_minutes_whatever_the_outbound_window(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    # 03:00 in Tunis on a Sunday: no outbound work is open
    night = d.clock.now().replace(year=2026, month=1, day=18, hour=2, minute=0)
    assert d.due("alpha", night) == [Job.SYNC] or d.due("alpha", night)[-1] is Job.SYNC
    assert Job.DAILY not in d.due("alpha", night)
    d.application.scheduler.run_job_now(d.runtime("alpha"), Job.SYNC, night)
    assert Job.SYNC not in d.due("alpha", night + timedelta(minutes=9))
    assert Job.SYNC in d.due("alpha", night + timedelta(minutes=10))
    assert d.application.scheduler._sync_every == 10


def test_a_second_run_writes_nothing_and_the_ledger_is_the_clients_own(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    d.revansa("alpha", "sa:p1", follow_up_count=3)
    d.sync("alpha")
    calls = api.count("write")
    again = d.sync("alpha")
    assert again.ok and api.count("write") == calls
    ledger = sql_ledgers(d.engine)("alpha")
    assert [(e.prospect_id, e.client_id) for e in ledger.entries()] == [("sa:p1", "alpha")]


# ---- sources are independent --------------------------------------------------------------------------------


def test_a_source_that_is_not_ready_stops_only_itself(deploy_sheets):
    fine = sheet(("p1", "Ada", PHONE))
    broken = sheet(("p1", "Grace", "+216 22 111 000"))
    broken.tabs[TAB][0] = [h for h in broken.tabs[TAB][0] if h != "Relances"]  # the mapping is gone
    d = deploy_sheets(
        {"alpha": raw_with_sheets("alpha", ["sa", "sb"])},
        {("alpha", "sa"): fine, ("alpha", "sb"): broken},
    )
    d.revansa("alpha", "sa:p1", follow_up_count=2)
    d.revansa("alpha", "sb:p1", follow_up_count=5)
    result = d.sync("alpha")
    assert result.ok  # one source ran: the job did
    assert cell(fine, 2, "Relances") == "2" and broken.count("write") == 0
    counters = d.runtime("alpha").health.snapshot()["writeback"]
    assert (counters["applied"], counters["not_ready"], counters["sources"]) == (1, 1, 2)
    assert result.errors == ("write-back sb: the source is not ready",)
    assert d.runtime("alpha").health.snapshot()["consecutive_failures"] == 0


def test_a_source_that_fails_for_a_moment_does_not_stop_the_others_nor_the_client(deploy_sheets):
    first, second = sheet(("p1", "Ada", PHONE)), sheet(("p1", "Grace", "+216 22 111 000"))
    d = deploy_sheets(
        {"alpha": raw_with_sheets("alpha", ["sa", "sb"])},
        {("alpha", "sa"): first, ("alpha", "sb"): second},
    )
    for sid in ("sa", "sb"):
        d.revansa("alpha", f"{sid}:p1", follow_up_count=2)
    from sales_integrations.google_sheets import SheetsApiError

    first.fail_next("write", SheetsApiError("boom", status=500))
    result = d.sync("alpha")
    assert cell(second, 2, "Relances") == "2" and cell(first, 2, "Relances") == ""
    assert result.ok and result.errors == ("write-back sa: stopped (unavailable)",)
    runtime = d.runtime("alpha")
    assert runtime.health.snapshot()["writeback"]["stopped"] == 1
    assert runtime.state.blocked_until is None  # sending is not delayed


def test_when_every_source_fails_the_job_backs_off_on_its_own_and_sending_does_not(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    api.tabs[TAB][0] = [h for h in api.tabs[TAB][0] if h != "Relances"]
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    runtime, scheduler = d.runtime("alpha"), d.application.scheduler
    now = d.clock.now()
    first = scheduler.run_job_now(runtime, Job.SYNC, now)
    assert not first.ok and runtime.state.sync_failures == 1
    assert Job.SYNC not in d.due("alpha", now + timedelta(seconds=30))  # its own backoff
    assert runtime.state.blocked_until is None and runtime.health.snapshot()["status"] == "ready"
    scheduler.run_job_now(runtime, Job.SYNC, now + timedelta(minutes=11))
    assert runtime.state.sync_failures == 2
    assert runtime.state.sync_blocked_until == now + timedelta(minutes=11, seconds=120)
    api.create_layout({f"prospects.{k}": v for k, v in HEADERS.items()})  # the sheet is repaired
    ok = scheduler.run_job_now(runtime, Job.SYNC, now + timedelta(minutes=30))
    assert ok.ok and runtime.state.sync_failures == 0 and runtime.state.sync_blocked_until is None


def test_a_pass_that_raises_is_counted_without_its_message_and_blocks_nothing_else(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    runtime = d.runtime("alpha")

    def explode():
        raise RuntimeError(f"the sheet of {PHONE} is on fire")

    runtime.writeback.run = explode
    result = d.sync("alpha")
    assert not result.ok and result.errors == ("write-back failed",)
    snapshot = runtime.health.snapshot()
    assert snapshot["writeback"]["consecutive_failures"] == 1
    assert snapshot["consecutive_failures"] == 0 and snapshot["status"] == "ready"
    assert snapshot["last_error"] is None and PHONE not in repr(snapshot)
    assert runtime.state.sync_blocked_until is not None and runtime.state.blocked_until is None


def test_a_failing_sync_never_stops_the_other_jobs_of_a_tick(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    runtime = d.runtime("alpha")
    runtime.writeback.run = lambda: (_ for _ in ()).throw(RuntimeError("x"))
    results = (
        d.application.scheduler._run_jobs(runtime, [Job.SYNC, Job.RECOVER], d.clock.now())
        if runtime.state.try_begin()
        else []
    )
    assert [r.job for r in results] == [Job.SYNC, Job.RECOVER]
    assert [r.ok for r in results] == [False, True]


# ---- clients are kept apart ----------------------------------------------------------------------------------


def test_two_clients_with_the_same_ids_write_each_into_its_own_sheet_and_ledger(deploy_sheets):
    mine, theirs = sheet(("p1", "Ada", PHONE)), sheet(("p1", "Ada", PHONE))
    d = deploy_sheets(
        {"alpha": raw_with_sheets("alpha", ["sa"]), "beta": raw_with_sheets("beta", ["sa"])},
        {("alpha", "sa"): mine, ("beta", "sa"): theirs},
    )
    d.revansa("alpha", "sa:p1", follow_up_count=3)
    d.sync("alpha")
    d.sync("beta")
    assert cell(mine, 2, "Relances") == "3" and cell(theirs, 2, "Relances") == ""
    assert theirs.count("write") == 0
    d.revansa("beta", "sa:p1", follow_up_count=1)
    d.sync("beta")
    assert cell(theirs, 2, "Relances") == "1" and cell(mine, 2, "Relances") == "3"
    alpha, beta = sql_ledgers(d.engine)("alpha"), sql_ledgers(d.engine)("beta")
    assert {e.client_id for e in alpha.entries()} == {"alpha"}
    assert {e.client_id for e in beta.entries()} == {"beta"} and len(beta.entries()) == 1


# ---- the configuration -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "block",
    [
        {"columns": {"next_action_type": "Prochaine action"}},  # half a next action
        {"columns": {"phone": "Telephone"}},  # an identity column
        {"columns": {"status": "Statut"}, "propagate_opt_out": "perhaps"},
        {"columns": {}},
    ],
)
def test_an_invalid_block_is_refused_at_publication_and_stops_a_hand_written_start(
    tmp_path, sql_engine, block
):
    raw = raw_with_sheets("alpha", ["sa"], writeback=block)
    check = import_document("alpha", raw)
    assert check.errors and any("writeback" in e for e in check.errors), check.errors
    with pytest.raises(ConfigurationError):
        WritebackDeployment(
            tmp_path, sql_engine, {"alpha": raw}, secrets_for("alpha", email=False, telegram=False, hubspot=False),
            apis={("alpha", "sa"): sheet()}, publish=False,
        )  # fmt: skip


def test_a_source_that_asked_for_write_back_in_a_deployment_without_a_database_is_refused(
    deploy_sheets,
):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    config = d.application.registry.get_config("alpha")
    builders = d.builders()
    without = replace(builders, writeback=replace(builders.writeback, open_ledger=None))
    with pytest.raises(RuntimeBuildError, match="write-back needs the database"):
        build_runtime(config, without, d.clock)
    assert api.count("write") == 0


def test_changing_the_spreadsheet_or_the_columns_cancels_the_last_connection_test(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    base = d.config_store.current("alpha").document

    def fingerprint(document):
        return lane_sources_fingerprint(d.application, "alpha", document)

    def with_settings(**changes):
        document = {**base, "sources": [dict(s) for s in base["sources"]]}
        document["sources"][0]["settings"] = {**base["sources"][0]["settings"], **changes}
        return document

    same = fingerprint(base)
    assert fingerprint(with_settings()) == same
    assert fingerprint(with_settings(spreadsheet_id="another-sheet")) != same
    other = {"columns": {**HEADERS, "follow_up_count": "Nombre de relances"}}
    assert fingerprint(with_settings(writeback=other)) != same
    off = {k: v for k, v in base["sources"][0]["settings"].items() if k != "writeback"}
    plain = {**base, "sources": [{**base["sources"][0], "settings": off}]}
    assert fingerprint(plain) != same  # and a source with no block keeps the fingerprint it had


def test_a_source_without_a_block_keeps_exactly_the_fingerprint_it_always_had(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets(
        {"alpha": raw_with_sheets("alpha", ["sa"], writeback=None)}, {("alpha", "sa"): api}
    )
    document = d.config_store.current("alpha").document
    assert "writeback" not in document["sources"][0]["settings"]
    assert lane_sources_fingerprint(d.application, "alpha", document)  # computed, nothing new in it


# ---- a runtime that is replaced ------------------------------------------------------------------------------


def test_a_replacement_runtime_brings_its_own_syncers_and_loses_none_it_should_keep(deploy_sheets):
    api = sheet(("p1", "Ada", PHONE))
    d = deploy_sheets({"alpha": raw_with_sheets("alpha", ["sa"])}, {("alpha", "sa"): api})
    runtime = d.runtime("alpha")
    config = d.application.registry.get_config("alpha")
    built = build_runtime(config, d.builders(), d.clock)
    assert built.writeback is not None and built.writeback is not runtime.writeback
    assert runtime.stage(config, built.engine, None, built.writeback)
    assert runtime.writeback is built.writeback
    assert runtime.stage(config, built.engine, None)  # a replacement with none removes it
    assert runtime.writeback is None


# ---- a pass that raises: logged by its type alone, counted, and the others go on -----------------------


class _Source:
    def __init__(self, issues=()):
        self.issues = list(issues)

    def verify(self):
        return list(self.issues)


class _Syncer:
    def __init__(self, outcome):
        self.outcome = outcome
        self.runs = 0

    def run_once(self):
        self.runs += 1
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def test_a_pass_that_raises_is_logged_by_its_type_with_a_fixed_message_and_nothing_else(caplog):
    import logging

    from sales_app.sourcing.writeback import SyncReport
    from sales_app.wiring.writeback import ClientWriteback

    leak = f"{PHONE} Ada Lovelace cell=2026-03-01T10:00 {HEADERS['status']}"
    broken, fine = _Syncer(RuntimeError(leak)), _Syncer(SyncReport(planned=1, applied=1))
    door = ClientWriteback({"sa": (_Source(), broken), "sb": (_Source(), fine)})
    with caplog.at_level(logging.DEBUG, logger="sales_app.wiring.writeback"):
        summary = door.run()
    [record] = [r for r in caplog.records if r.name == "sales_app.wiring.writeback"]
    assert record.levelno == logging.ERROR
    assert record.getMessage() == "a write-back pass raised RuntimeError"
    assert record.exc_info is None and record.args == ("RuntimeError",)
    everything = caplog.text + repr(summary) + repr(record.__dict__)
    for leaked in (PHONE, "Ada", "Lovelace", "2026-03-01", HEADERS["status"], "cell="):
        assert leaked not in everything
    assert (summary.errored, summary.applied, summary.sources) == (1, 1, 2)
    assert summary.errors == ("write-back sa: the pass failed unexpectedly",)
    assert (broken.runs, fine.runs) == (1, 1)  # the second source ran after the first one raised


def test_a_source_that_is_not_ready_is_not_logged_here_and_a_good_pass_is_silent(caplog):
    import logging

    from sales_app.sourcing.writeback import SyncReport
    from sales_app.wiring.writeback import ClientWriteback

    door = ClientWriteback(
        {
            "sa": (_Source(["a column is missing"]), _Syncer(SyncReport())),
            "sb": (_Source(["warning: soft"]), _Syncer(SyncReport(planned=2, applied=2))),
        }
    )
    with caplog.at_level(logging.DEBUG, logger="sales_app.wiring.writeback"):
        summary = door.run()
    assert [r for r in caplog.records if r.name == "sales_app.wiring.writeback"] == []
    assert (summary.not_ready, summary.applied, summary.errored) == (1, 2, 0)
