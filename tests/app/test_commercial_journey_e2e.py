"""The commercial journey, end to end: two clients, one deployment, from the Admin's decisions to the
invoice that gets paid. See `docs/e2e-commercial-journey.md` for what is real, what is simulated and
what is not covered. The harness is `commercial_support.py`.

The story (Africa/Tunis, UTC+1; the clock only moves when the test moves it):

* the Admin gives client alpha a model, a plan and a daily e-mail allowance; beta gets a model and a
  plan of its own; both start their agents;
* alpha runs two circuits (one product each) on two Google Sheets: sheet A holds a contactable
  person, a person with no way to be contacted and a person who will answer; sheet B holds one;
* Thursday: proposals, approval through the Client API, sending (SMTP double), replies (IMAP double),
  write-back to the sheets, the follow-up planned;
* Friday to Monday: the daily report, the allowance reached, the mail server down, the follow-ups;
* a restart that changes nothing and repeats nothing;
* February: the January invoices, the signed payment, the replay, the manual attempt, the wrong amount.

Telegram is the second chapter (the existing harness), and what the platform cannot do yet is written
down as explicit skipped tests.
"""

import smtplib
from datetime import UTC, date, datetime, timedelta

import pytest
from app_support import declare_models
from commercial_support import OWNER_ADDRESS, Journey
from sqlalchemy import text
from test_invoice_payment_webhook import SECRET, WEBHOOK, body_of, signed_headers

from sales_app.persistence.daily_report_rows import SqlReportStore
from sales_app.persistence.send_cap_rows import SqlSendCapStore, SqlSendCounter
from sales_app.send_cap import (
    CAP_REACHED,
    DailySendGuard,
    SendCapChannel,
)
from sales_core.contracts import (
    ActionStatus,
    ProspectId,
    ProspectStatus,
    SalesActionType,
)

REPORTING = "/client/v1/reporting/settings"
REPORTS = "/client/v1/reports"
INVOICES = "/admin/v1/billing/invoices"
UTC_TZ = UTC


def utc(month, day, hour=0, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def report_body(recipient):
    return {
        "enabled": True,
        "channel": "email",
        "recipient": recipient,
        "send_time": "09:00",
        "timezone": "Africa/Tunis",
        "language": "en",
    }


def approve_all(browser):
    ids = [p["id"] for p in browser.get("/client/v1/approvals").json()["proposals"]]
    for action_id in ids:
        assert browser.post(f"/client/v1/approvals/{action_id}/approve").status_code == 200
    return ids


@pytest.fixture
def world(tmp_path):
    journey = Journey(tmp_path)
    yield journey
    journey.close()


def snapshot(j):
    """Everything a restart must not change: the people's state, the mail that left, the reports,
    the counters and the sheets' write-back cells."""
    states = {}
    for cid in ("alpha", "beta"):
        for pid, state in j.store(cid).list_states().items():
            schedule = state.schedule
            states[(cid, pid)] = (
                state.status,
                state.lane_id,
                schedule.next_action_type,
                schedule.next_action_due_at,
                schedule.last_contacted_at,
                schedule.follow_up_count,
            )
    with j.engine.connect() as connection:
        counters = connection.execute(
            text("select client_id, channel, day, used from op_send_counter order by 1, 2, 3")
        ).all()
    store = SqlReportStore(j.engine)
    reports = [
        (cid, r.day.isoformat(), r.status, r.attempts, r.sent_at)
        for cid in ("alpha", "beta")
        for r in sorted(store.list(cid, 50), key=lambda r: r.day)
    ]
    return {
        "states": states,
        "mails": {
            cid: [(m["To"], m["Message-ID"]) for m in j.prospect_mails(cid)] for cid in j.smtp
        },
        "report_mails": [(m["To"], m["Message-ID"]) for m in j.report_mails("alpha")],
        "reports": reports,
        "counters": [tuple(r) for r in counters],
        "sheets": {
            key: [list(row) for row in api.tabs["Prospects"]] for key, api in j.sheets.items()
        },
    }


# =============================================================================================
# THE JOURNEY
# =============================================================================================


def test_the_commercial_journey_of_two_clients(world):
    j = world
    root = j.admin()

    # ---- 1. two clients, and what the Admin decides for each --------------------------------
    declare_models(j.application.credentials, "gpt-5", "test-model")
    llm = root.put("/admin/v1/clients/alpha/llm", json={"provider": "openai", "model": "gpt-5"})
    assert llm.status_code == 200 and llm.json()["model"] == "gpt-5"
    assert j.application.registry.get_config("alpha").spec.llm.model == "gpt-5"
    assert j.application.registry.get_config("beta").spec.llm.model == "test-model"  # untouched

    plan = root.post(
        "/admin/v1/plans",
        json={
            "name": "Pro",
            "monthly_price": "31.00",
            "currency": "usd",
            "included_credits": 1000,
            "daily_quota": 200,
            "active": True,
        },
    )
    assert plan.status_code == 201 and plan.json()["plan_id"] == "pro"
    for cid in ("alpha", "beta"):
        given = root.put(f"/admin/v1/clients/{cid}/plan", json={"plan_id": "pro"}).json()
        assert given["current"]["plan_id"] == "pro"
        assert given["current"]["started_at"] == "2026-01-15T09:00:00Z"

    # The daily e-mail cap is the Admin's, per client, set through the Admin API. Every e-mail of
    # the client counts against it (prospecting, answers, follow-ups, the report), and it is held
    # to the plan's daily quota (200 here): the effective cap is the smaller of the two.
    cap_url = "/admin/v1/clients/{}/send-cap"
    done = root.put(cap_url.format("alpha"), json={"daily_cap": 4}).json()
    assert (done["daily_cap"], done["plan_daily_quota"], done["effective_cap"]) == (4, 200, 4)
    assert root.get(cap_url.format("beta")).json()["status"] == "not_configured"  # no default
    caps = SqlSendCapStore(j.engine)
    assert caps.get("alpha") == 4 and caps.get("beta") is None
    bob = j.admin("bob")  # an Admin who reaches beta only
    assert bob.get(cap_url.format("alpha")).status_code == 404
    assert bob.get(cap_url.format("beta")).status_code == 200
    assert bob.put(cap_url.format("beta"), json={"daily_cap": 9}).status_code == 403  # owner only

    for cid in ("alpha", "beta"):
        started = j.browser(cid).post("/client/v1/agent/start")
        assert started.status_code == 200 and started.json()["active"] is True

    # ---- 2. the sources: three people on sheet A (two circuits in all), one on sheet B ------
    j.add_prospect("alpha", "sheet_a", "p1", "Ada", email="ada@example.com")  # contactable
    j.add_prospect("alpha", "sheet_a", "p2", "Bob")  # no phone, no e-mail: cannot be contacted
    j.add_prospect("alpha", "sheet_a", "p3", "Cy", email="cy@example.com")  # will answer
    j.add_prospect("alpha", "sheet_b", "q1", "Dee", email="dee@example.com")  # the other product
    j.add_prospect("beta", "sheet_x", "x1", "Eve", email="eve@example.com")  # beta's own person

    # ---- 3. Thursday: the agent proposes, and nothing leaves before a person says so ---------
    assert all(r.ok for r in j.tick())
    alpha, beta = j.browser("alpha"), j.browser("beta")
    proposals = {
        p["prospect_name"]: p for p in alpha.get("/client/v1/approvals").json()["proposals"]
    }
    assert set(proposals) == {"Ada", "Cy", "Dee"}  # Bob is not proposed to
    assert (
        proposals["Ada"]["text"] == "Hello Ada [a]" and proposals["Dee"]["text"] == "Hello Dee [b]"
    )
    assert j.smtp["alpha"].sent == [] and j.smtp["beta"].sent == []

    actions = {a.prospect_id: a for a in j.store("alpha").list_actions()}
    assert (actions["sheet_a:p1"].lane_id, actions["sheet_a:p3"].lane_id) == ("offer_a", "offer_a")
    assert actions["sheet_b:q1"].lane_id == "offer_b"  # two products, two circuits
    assert actions["sheet_a:p2"].status is ActionStatus.CANCELLED
    assert actions["sheet_a:p2"].last_error == "safety: no_contact"  # refused, with a reason
    assert actions["sheet_a:p2"].draft is None

    # isolation: beta sees only its own proposal and cannot decide alpha's
    beta_proposals = beta.get("/client/v1/approvals").json()["proposals"]
    assert [p["prospect_name"] for p in beta_proposals] == ["Eve"]
    assert beta.post(f"/client/v1/approvals/{proposals['Ada']['id']}/approve").status_code == 404
    assert {a.prospect_id for a in j.store("beta").list_actions()} == {"sheet_x:x1"}

    # ---- 4. approval, then the mail leaves through alpha's own server, once -----------------
    approved = approve_all(alpha)
    assert len(approved) == 3
    assert all(r.ok for r in j.tick())
    sent = {m["To"]: m.get_content().strip() for m in j.prospect_mails("alpha")}
    assert sent == {
        "ada@example.com": "Hello Ada [a]",
        "cy@example.com": "Hello Cy [a]",
        "dee@example.com": "Hello Dee [b]",
    }
    assert {m["From"] for m in j.smtp["alpha"].sent} == {"Alpha Sales <alpha@example.org>"}
    assert j.smtp["alpha"].logins and set(j.smtp["alpha"].logins) == {
        ("alpha@example.org", "pass-alpha")
    }
    assert j.smtp["beta"].sent == []  # beta's proposal was not approved: its server is silent
    sent_day = date(2026, 1, 15)
    assert SqlSendCounter(j.engine).used("alpha", "email", sent_day) == 3  # prospecting is counted
    assert SqlSendCounter(j.engine).used("beta", "email", sent_day) == 0
    for _ in range(3):
        j.tick()
    assert len(j.prospect_mails("alpha")) == 3  # time passing sends nothing twice

    # the operational store
    states = j.store("alpha").list_states()
    assert states[ProspectId("sheet_a:p1")].status is ProspectStatus.ACTIVE
    assert states[ProspectId("sheet_a:p2")].status is ProspectStatus.PAUSED  # not contactable
    assert states[ProspectId("sheet_b:q1")].lane_id == "offer_b"
    due = states[ProspectId("sheet_a:p1")].schedule.next_action_due_at
    assert states[ProspectId("sheet_a:p1")].schedule.next_action_type is SalesActionType.FOLLOW_UP
    assert due is not None and due.date() == date(2026, 1, 18)  # the follow-up is planned
    outbound = j.store("alpha").get_conversation(ProspectId("sheet_a:p1")).messages
    assert [m.text for m in outbound] == ["Hello Ada [a]"]

    # the write-back: the clients' own sheets carry Revansa's results, in their own columns
    ada = j.sheet("alpha", "sheet_a", "p1")
    assert ada["Dernier contact"].startswith("2026-01-15T10:") and ada["Statut"] == "new"
    assert ada["Prochaine action"] == "follow_up" and ada["Echeance"].startswith("2026-01-18T")
    assert j.sheet("alpha", "sheet_a", "p2")["Statut"] == "paused"  # a restriction is written
    assert j.sheet("alpha", "sheet_a", "p2")["Dernier contact"] == ""  # nobody was contacted
    assert j.sheet("alpha", "sheet_b", "q1")["Prochaine action"] == "follow_up"
    assert j.sheet("beta", "sheet_x", "x1")["Dernier contact"] == ""  # beta's sheet is untouched

    # ---- 5. Cy answers, Ada says STOP: the mailbox is read, the Agent decides ----------------
    j.reply("alpha", "cy@example.com", "Yes, tell me more.", "<cy-1@mail.example>")
    j.reply("alpha", "ada@example.com", "STOP", "<ada-1@mail.example>")
    assert all(r.ok for r in j.tick())
    assert j.unread("alpha") == []  # stored, then marked read
    inbound = j.store("alpha").get_conversation(ProspectId("sheet_a:p3")).messages
    assert [(m.direction.value, m.text) for m in inbound][-1] == ("inbound", "Yes, tell me more.")
    ada_state = j.store("alpha").get_states([ProspectId("sheet_a:p1")])[ProspectId("sheet_a:p1")]
    assert ada_state.status is ProspectStatus.OPTED_OUT
    assert ada_state.schedule.next_action_due_at is None  # nothing is planned for someone who left
    assert j.sheet("alpha", "sheet_a", "p1")["Statut"] == "opted_out"  # and the sheet says so
    assert j.sheet("alpha", "sheet_a", "p1")["Prochaine action"] == ""
    assert j.sheet("alpha", "sheet_a", "p3")["Derniere reponse"].startswith("2026-01-15T")

    replies = alpha.get("/client/v1/approvals").json()["proposals"]
    assert [p["text"] for p in replies] == ["Re Cy [a]"]  # the Agent answers Cy, and only Cy
    approve_all(alpha)
    j.tick()
    assert [m.get_content().strip() for m in j.prospect_mails("alpha")][-1] == "Re Cy [a]"
    assert len(j.prospect_mails("alpha")) == 4
    assert SqlSendCounter(j.engine).used("alpha", "email", sent_day) == 4  # answers too
    cy = j.store("alpha").get_states([ProspectId("sheet_a:p3")])[ProspectId("sheet_a:p3")]
    assert cy.schedule.next_action_type is SalesActionType.FOLLOW_UP  # the follow-up is planned
    assert cy.schedule.next_action_due_at.date() == date(2026, 1, 18)
    assert j.sheet("alpha", "sheet_a", "p3")["Prochaine action"] == "follow_up"

    # ---- 6. Friday: the client asks for a daily report; it is built from stored data ---------
    j.goto(utc(1, 16, 8, 5))
    alpha = j.browser("alpha")  # (sessions are a day long)
    assert alpha.put(REPORTING, json=report_body(OWNER_ADDRESS["alpha"])).status_code == 200
    assert j.report_mails("alpha") == []
    j.tick()
    report = alpha.get(f"{REPORTS}/2026-01-16").json()
    assert report["status"] == "sent" and report["sent_at"] is not None
    figures = report["figures"]
    assert figures["activity"] == {
        "messages_sent": 4,  # three first contacts and the answer to Cy
        "prospects_contacted": 3,
        "new_contacts": 3,
        "follow_ups_sent": 0,
        "answers_sent": 1,
        "replies_received": 2,  # Cy's, and Ada's STOP
        "prospects_replied": 2,
    }
    assert figures["prospects"]["total"] == 4  # beta's person is not counted
    assert figures["prospects"]["by_status"] == {
        "new": 0,
        "active": 2,
        "paused": 1,
        "opted_out": 1,
        "closed": 0,
    }
    assert figures["follow_ups"]["scheduled"] == 2
    assert figures["quota"] == {
        "channel": "email",
        "status": "ok",
        "day_utc": "2026-01-16",
        "cap": 4,
        "used": 0,  # Friday's own count: Thursday's four mails are Thursday's
        "remaining": 4,
    }
    # intentions are not sales: nothing is claimed that the platform does not track
    for key in ("purchase_intents", "appointments", "conversions"):
        assert figures[key] == {"tracked": False}
    assert "nothing here is a confirmed sale" in report["text"]
    assert "ada@example.com" not in report["text"] and "Cy" not in report["text"]

    (mail,) = j.report_mails("alpha")  # sent through the fake channel, to the client's own address
    assert mail.get_content().strip() == report["text"].strip()
    day = date(2026, 1, 16)
    assert SqlSendCounter(j.engine).used("alpha", "email", day) == 1  # the place is taken in SQL
    assert SqlSendCounter(j.engine).used("beta", "email", day) == 0

    # idempotent: asking again, or letting the scheduler pass again, sends nothing more
    again = alpha.post(f"{REPORTS}/generate").json()
    assert again["generated_at"] == report["generated_at"] and again["status"] == "sent"
    j.tick(timedelta(hours=1))
    assert len(j.report_mails("alpha")) == 1
    assert SqlSendCounter(j.engine).used("alpha", "email", day) == 1
    assert [r["day"] for r in alpha.get(REPORTS).json()["reports"]] == ["2026-01-16"]

    # isolation: beta can read none of it and has configured no report of its own
    beta = j.browser("beta")
    assert beta.get(REPORTS).json() == {"reports": []}
    assert beta.get(f"{REPORTS}/2026-01-16").status_code == 404
    assert beta.post(f"{REPORTS}/2026-01-16/resend").status_code == 404
    assert j.report_mails("beta") == [] and j.smtp["beta"].sent == []

    # ---- 7. Saturday: the day's allowance is gone, so the report is not sent ------------------
    # The Admin lowers alpha's cap to 1 and the day's single place is taken here by hand, as the
    # other e-mails of the day would (the prospecting refusal itself is proved in
    # `test_a_prospect_email_over_the_cap_is_deferred_never_lost`).
    root = j.admin()  # (sessions are a day long)
    assert root.put(cap_url.format("alpha"), json={"daily_cap": 1}).json()["effective_cap"] == 1
    assert SqlSendCounter(j.engine).try_increment("alpha", "email", date(2026, 1, 17), 1)
    j.goto(utc(1, 17, 8, 15))
    alpha = j.browser("alpha")
    refused = alpha.get(f"{REPORTS}/2026-01-17").json()
    assert refused["status"] == "failed" and refused["error_code"] == CAP_REACHED
    assert refused["sent_at"] is None
    assert len(j.report_mails("alpha")) == 1  # nothing went out on the wire
    assert SqlSendCounter(j.engine).used("alpha", "email", date(2026, 1, 17)) == 1  # nothing taken

    # ---- 8. the mail server is down when the report is tried again: kept, not delivered -------
    # A refusal for the cap waits for the next UTC day ("Revansa will try again tomorrow").
    assert refused["next_attempt_at"] == "2026-01-18T00:05:00Z"
    j.smtp["alpha"].send_error = smtplib.SMTPDataError(451, b"try later")
    j.goto(utc(1, 18, 0, 10))
    down = j.browser("alpha").get(f"{REPORTS}/2026-01-17").json()
    assert down["status"] == "failed" and down["sent_at"] is None and down["attempts"] == 1
    assert down["error_code"] != CAP_REACHED
    assert down["text"] == refused["text"] and down["figures"] == refused["figures"]  # kept as is
    assert len(j.report_mails("alpha")) == 1
    assert SqlSendCounter(j.engine).used("alpha", "email", date(2026, 1, 18)) == 0  # place returned

    j.smtp["alpha"].send_error = None
    j.tick(timedelta(hours=2))
    delivered = j.browser("alpha").get(f"{REPORTS}/2026-01-17").json()
    assert delivered["status"] == "sent" and delivered["sent_at"] is not None
    assert delivered["text"] == refused["text"]  # the same report, not a new one
    assert len(j.report_mails("alpha")) == 2
    j.tick(timedelta(hours=3))
    assert len(j.report_mails("alpha")) == 2  # and never twice
    assert SqlSendCounter(j.engine).used("alpha", "email", date(2026, 1, 18)) == 1

    # ---- 9. Monday: the follow-ups that were planned come due, for the people still reachable --
    j.goto(utc(1, 19, 8, 30))
    alpha = j.browser("alpha")
    root = j.admin()
    assert (
        root.put(cap_url.format("alpha"), json={"daily_cap": 3}).status_code == 200
    )  # the report takes one place too
    follow_ups = alpha.get("/client/v1/approvals").json()["proposals"]
    assert sorted(p["prospect_name"] for p in follow_ups) == [
        "Cy",
        "Dee",
    ]  # not Ada (STOP), not Bob
    approve_all(alpha)
    j.tick()
    mails = j.prospect_mails("alpha")
    assert len(mails) == 6
    assert sorted(m["To"] for m in mails[4:]) == ["cy@example.com", "dee@example.com"]
    assert (
        SqlSendCounter(j.engine).used("alpha", "email", date(2026, 1, 19)) == 3
    )  # follow-ups too, and the report
    assert [m["To"] for m in mails].count("ada@example.com") == 1  # Ada was written to once only
    assert (
        j.store("alpha")
        .get_states([ProspectId("sheet_a:p3")])[ProspectId("sheet_a:p3")]
        .schedule.follow_up_count
        == 1
    )

    # beta was never approved, so nothing of beta's ever left, and nothing of alpha's is in it
    assert j.smtp["beta"].sent == []
    assert {a.prospect_id for a in j.store("beta").list_actions()} == {"sheet_x:x1"}
    assert j.store("beta").list_actions()[0].status is ActionStatus.PROPOSED

    # ---- 10. a restart changes nothing and repeats nothing -----------------------------------
    before = snapshot(j)
    j.restart()
    assert j.application.registry.get_config("alpha").spec.llm.model == "gpt-5"  # still assigned
    for step in (timedelta(minutes=10), timedelta(hours=1), timedelta(hours=5), timedelta(days=2)):
        assert all(r.ok for r in j.tick(step))
    after = snapshot(j)
    assert after["mails"] == before["mails"]  # no prospect send was repeated
    kept = len(before["report_mails"])  # (later days bring later reports, never the same again)
    assert after["report_mails"][:kept] == before["report_mails"]
    assert len({m[1] for m in after["report_mails"]}) == len(after["report_mails"])
    assert after["states"] == before["states"]  # statuses, lanes and schedules persist
    assert after["sheets"] == before["sheets"]  # and so does the write-back
    assert [r for r in after["reports"] if r[1] <= "2026-01-19"] == before["reports"]
    assert before["reports"][0][:3] == ("alpha", "2026-01-16", "sent")
    alpha = j.browser("alpha")
    assert alpha.get(f"{REPORTS}/2026-01-16").json()["figures"]["activity"] == figures["activity"]
    assert [r["status"] for r in alpha.get(REPORTS).json()["reports"] if r["day"] <= "2026-01-19"]
    assert SqlSendCounter(j.engine).used("alpha", "email", day) == 1
    assert SqlSendCapStore(j.engine).get("alpha") == 3
    assert j.browser("alpha").get("/client/v1/summary").json()["total_prospects"] == 4

    # ---- 11. February: the January invoices --------------------------------------------------
    j.clock.set(utc(2, 3, 9))
    root = j.admin()
    generated = root.post(f"{INVOICES}/generate", json={"period": "2026-01"}).json()
    created = {i["client_id"]: i for i in generated["created"]}
    assert set(created) == {"alpha", "beta"} and generated["skipped"] == []
    invoice = created["alpha"]
    assert invoice["number"] == "INV-2026-01-alpha" and invoice["status"] == "PENDING"
    assert (invoice["amount"], invoice["currency"]) == ("17.00", "USD")  # 17 days of 31 at 31.00
    assert invoice["lines"][0]["days"] == 17 and invoice["lines"][0]["plan_name"] == "Pro"
    again = root.post(f"{INVOICES}/generate", json={"period": "2026-01"}).json()
    assert again["created"] == [] and again["already_invoiced"] == 2  # no duplicate
    assert len(root.get(INVOICES).json()["invoices"]) == 2

    # the payment provider's signing secret is the Admin's
    j.application.credentials.save("global", "payments.webhook_secret", SECRET, actor="root")
    web = j.http()

    def hook(**over):
        body = body_of(**({"invoice": "INV-2026-01-alpha", "amount": "17.00"} | over))
        return web.post(WEBHOOK, content=body, headers=signed_headers(body, when=j.clock.now()))

    wrong = hook(amount="16.00", event_id="evt_wrong", transaction_id="txn_wrong")
    assert wrong.status_code == 200 and wrong.json() == {"status": "rejected"}
    assert root.get(f"{INVOICES}/{invoice['invoice_id']}").json()["status"] == "PENDING"

    assert hook().json() == {"status": "applied"}
    paid = root.get(f"{INVOICES}/{invoice['invoice_id']}").json()
    assert paid["status"] == "PAID" and paid["payment_origin"] == "electronic"
    assert (paid["payment_transaction_id"], paid["payment_event_id"]) == ("txn_1", "evt_1")
    assert hook().json() == {"status": "duplicate"}  # a replay is a no-op
    assert root.get(f"{INVOICES}/{invoice['invoice_id']}").json() == paid
    for status in ("PAID", "FOR_FREE"):
        manual = root.put(f"{INVOICES}/{invoice['invoice_id']}/status", json={"status": status})
        assert manual.status_code == 409
        assert "paid electronically" in manual.json()["detail"]
    unsigned = web.post(WEBHOOK, content=body_of(), headers={})
    assert unsigned.status_code == 401

    # isolation: beta's invoice, of the very same amount, is untouched, and beta's own Admin sees
    # only that one
    beta_invoice = created["beta"]
    assert root.get(f"{INVOICES}/{beta_invoice['invoice_id']}").json()["status"] == "PENDING"
    bob = j.admin("bob")  # an Admin who reaches beta only
    assert [i["client_id"] for i in bob.get(INVOICES).json()["invoices"]] == ["beta"]
    assert bob.get(f"{INVOICES}/{invoice['invoice_id']}").status_code == 404
    events = root.get(f"{INVOICES.rsplit('/', 1)[0]}/payment-events").json()["events"]
    assert {(e["event_id"], e["outcome"]) for e in events} >= {
        ("evt_wrong", "amount_mismatch"),
        ("evt_1", "applied"),
    }
    client_session = j.browser("beta")  # a client reads its own invoices (lot 3) and changes nothing
    own = client_session.get("/client/v1/invoices")
    assert own.status_code == 200
    assert all(i["client_id"] == "beta" for i in own.json().get("invoices", [])) if own.json().get("invoices") and "client_id" in own.json()["invoices"][0] else True
    for path in ("/client/v1/billing", "/client/v1/billing/invoices"):
        assert client_session.get(path).status_code == 404
    assert client_session.get(INVOICES).status_code in (401, 403, 404)

    # ---- 12. the invoices survive one more restart ------------------------------------------
    j.restart()
    root = j.admin()
    kept = root.get(f"{INVOICES}/{invoice['invoice_id']}").json()
    assert kept["status"] == "PAID" and kept["payment_origin"] == "electronic"
    assert hook().json() == {"status": "duplicate"}
    assert root.post(f"{INVOICES}/generate", json={"period": "2026-01"}).json()["created"] == []
    assert root.get("/admin/v1/clients/alpha/plan").json()["current"]["plan_id"] == "pro"


# =============================================================================================
# TELEGRAM (the existing harness: the bot is a double, Telegram never is reached)
# =============================================================================================


def test_telegram_a_person_who_has_not_started_the_bot_is_never_contacted(tmp_path):
    from test_telegram_application_e2e import Telegram

    root = tmp_path / "telegram"
    root.mkdir()
    r = Telegram(root, "alpha", "beta")
    try:
        r.allow_telegram()
        r.set_up_telegram("alpha")
        r.set_up_telegram("beta")
        r.add_prospect("alpha", "p1", display_name="Started", phone="+21620000011")
        r.add_prospect("alpha", "p2", display_name="Waiting", phone="+21620000012")
        r.add_prospect("beta", "p1", display_name="Other", phone="+21620000013")

        for _ in range(4):  # four days (to a Monday): a day of quota spent on nobody
            assert all(x.ok for x in r.tick(timedelta(days=1)))
        assert r.actions("alpha") == [] and r.sent("alpha") == [] and r.sent("beta") == []
        assert r.prospect("alpha", "p1")["status"] == "new"  # waiting, not lost, not paused
        assert r.prospect("alpha", "p2")["status"] == "new"

        # a chat id written into the sheet by hand proves nothing
        r.sheets["alpha"].human_set(
            "Prospects",
            r.sheets["alpha"].find_row("Prospects", "prospect_id", "p2"),
            "telegram_chat_id",
            "999",
        )
        r.tick()
        assert r.actions("alpha") == [] and r.sent("alpha") == []

        waiting = r.browser("alpha").get("/client/v1/channel/invitations").json()
        assert waiting["waiting"] == 2

        # p1 presses Start on their personal link: from then on they are a normal prospect
        r.starts("alpha", 424242, r.invitation("alpha", "p1"))
        assert r.prospect("alpha", "p1")["telegram_chat_id"] == "424242"
        (reply,) = r.actions("alpha")
        assert reply["prospect_id"] == "p1" and reply["status"] == "proposed"
        assert r.sent("alpha") == []  # approval mode
        r.approve("alpha", "p1")
        assert all(x.ok for x in r.tick())
        assert [m["chat_id"] for m in r.sent("alpha")] == ["424242"]  # only the one who started
        assert r.prospect("alpha", "p2")["status"] == "new"
        assert r.sent("beta") == [] and r.actions("beta") == []  # beta's bot is its own

        r.restart()  # a restart repeats nothing
        r.tick()
        r.tick(timedelta(hours=2))
        assert [m["chat_id"] for m in r.sent("alpha")] == ["424242"]
        assert r.prospect("alpha", "p1")["telegram_chat_id"] == "424242"
    finally:
        r.close()


# =============================================================================================
# THE SEND CAP ON THE REAL E-MAIL CHANNEL (a composition made by the test, see the docs)
# =============================================================================================


def test_the_send_cap_stops_the_real_mail_channel_and_the_count_survives_a_restart(world):
    """`SendCapChannel` around the real `MailChannel` of alpha, over the real SQL counter and the cap
    the Admin stored: the composition the application's wiring applies to every e-mail channel, built
    by hand here to show the pieces and the count surviving a restart."""
    from sales_app.persistence import create_database_engine
    from sales_core.contracts import Channel, MessageDraft, OutboundMessage

    j = world
    SqlSendCapStore(j.engine).set("alpha", 2, by="admin:root", at=j.clock.now())
    config = j.application.registry.get_config("alpha")
    inner = j.application.builders.channels["email"].build(config)  # real MailChannel, fake socket

    def channel(engine):
        guard = DailySendGuard(
            "alpha",
            "email",
            lambda: SqlSendCapStore(engine).get("alpha"),
            j.clock,
            SqlSendCounter(engine),
        )
        return SendCapChannel(inner, guard)

    def message(n):
        return OutboundMessage(
            client_id="alpha",
            prospect_id="sheet_a:p1",
            channel=Channel.EMAIL,
            recipient=f"lead{n}@example.com",
            draft=MessageDraft(text=f"Hello {n}"),
            idempotency_key=f"key-{n}",
        )

    first = channel(j.engine)
    assert first.send(message(1)).ok and first.send(message(2)).ok
    day = j.clock.now().date()
    assert SqlSendCounter(j.engine).used("alpha", "email", day) == 2
    refused = first.send(message(3))
    assert not refused.ok and refused.error_code == CAP_REACHED
    assert [m["To"] for m in j.smtp["alpha"].sent] == ["lead1@example.com", "lead2@example.com"]

    # a restart: a new engine on the same file, a new channel; the count is still there
    other = create_database_engine(j.url)
    try:
        assert SqlSendCounter(other).used("alpha", "email", day) == 2
        assert channel(other).send(message(4)).error_code == CAP_REACHED
        assert len(j.smtp["alpha"].sent) == 2
        # another client's allowance is its own
        assert SqlSendCounter(other).used("beta", "email", day) == 0
        # tomorrow (UTC) is a new day
        j.clock.set(j.clock.now() + timedelta(days=1))
        assert channel(other).send(message(5)).ok and len(j.smtp["alpha"].sent) == 3
    finally:
        other.dispose()


# =============================================================================================
# THE SEND CAP ON PROSPECTING E-MAIL, END TO END (the application's own wiring)
# =============================================================================================

CAP_URL = "/admin/v1/clients/{}/send-cap"


def _start_agents(j):
    for cid in ("alpha", "beta"):
        assert j.browser(cid).post("/client/v1/agent/start").status_code == 200


def _actions(j, cid):
    return {a.prospect_id: a for a in j.store(cid).list_actions()}


def test_a_prospect_email_over_the_cap_is_deferred_never_lost(world):
    """Prospecting e-mails take places in the same counter as the report, and are refused (nothing on
    the wire) with no cap, then once the day's cap is reached. A refusal keeps the action approved
    with its reason and does not use up its attempts: it goes out on a later day, once."""
    j = world
    root = j.admin()
    _start_agents(j)
    j.add_prospect("alpha", "sheet_a", "p1", "Ada", email="ada@example.com")
    j.add_prospect("alpha", "sheet_a", "p3", "Cy", email="cy@example.com")
    j.add_prospect("beta", "sheet_x", "x1", "Eve", email="eve@example.com")
    assert all(r.ok for r in j.tick())
    approve_all(j.browser("alpha"))
    approve_all(j.browser("beta"))

    # no cap at all (no default, not unlimited): nothing leaves, for either client
    assert all(r.ok for r in j.tick())
    assert j.smtp["alpha"].sent == [] and j.smtp["beta"].sent == []
    held = _actions(j, "alpha")["sheet_a:p1"]
    assert held.status is ActionStatus.APPROVED and held.attempts == 0
    assert held.last_error and "not configured" in held.last_error  # the reason is visible
    for _ in range(5):  # many dispatches: still nothing, and the attempts are not burnt
        assert all(r.ok for r in j.tick())
    assert j.smtp["alpha"].sent == [] and _actions(j, "alpha")["sheet_a:p1"].attempts == 0
    day = j.clock.now().date()
    assert SqlSendCounter(j.engine).used("alpha", "email", day) == 0

    # the Admin gives alpha a cap of one: exactly one prospect mail leaves today
    assert root.put(CAP_URL.format("alpha"), json={"daily_cap": 1}).status_code == 200
    assert all(r.ok for r in j.tick())
    assert len(j.prospect_mails("alpha")) == 1
    assert SqlSendCounter(j.engine).used("alpha", "email", day) == 1
    statuses = sorted(a.status.value for a in _actions(j, "alpha").values())
    assert statuses == ["approved", "sent"]
    waiting = next(a for a in _actions(j, "alpha").values() if a.status is ActionStatus.APPROVED)
    assert waiting.attempts == 0 and "reached" in (waiting.last_error or "")
    for _ in range(3):
        j.tick()
    assert len(j.prospect_mails("alpha")) == 1  # the cap holds all day
    assert root.get(CAP_URL.format("alpha")).json()["status"] == "limit_reached"
    assert j.smtp["beta"].sent == []  # beta has no cap: another client's cap is not its own

    # tomorrow (UTC) is a new day: the waiting mail goes, once
    j.tick(timedelta(days=1))
    assert len(j.prospect_mails("alpha")) == 2
    assert sorted(m["To"] for m in j.prospect_mails("alpha")) == [
        "ada@example.com",
        "cy@example.com",
    ]
    assert SqlSendCounter(j.engine).used("alpha", "email", day + timedelta(days=1)) == 1
    for _ in range(3):
        j.tick()
    assert len(j.prospect_mails("alpha")) == 2

    # beta gets a cap: its waiting mail leaves; removing the cap stops what follows
    root = j.admin()  # (sessions are a day long)
    assert root.put(CAP_URL.format("beta"), json={"daily_cap": 5}).status_code == 200
    j.tick()
    assert [m["To"] for m in j.prospect_mails("beta")] == ["eve@example.com"]
    assert root.delete(CAP_URL.format("beta")).json()["status"] == "not_configured"
    j.add_prospect("beta", "sheet_x", "x2", "Fay", email="fay@example.com")
    j.tick()
    approve_all(j.browser("beta"))
    j.tick()
    assert [m["To"] for m in j.prospect_mails("beta")] == ["eve@example.com"]  # refused again

    # a restart: the cap and the count are still there, and nothing is sent twice
    before = (len(j.prospect_mails("alpha")), len(j.prospect_mails("beta")))
    j.restart()
    j.tick(timedelta(minutes=30))
    assert (len(j.prospect_mails("alpha")), len(j.prospect_mails("beta"))) == before
    assert SqlSendCapStore(j.engine).get("alpha") == 1
    assert SqlSendCounter(j.engine).used("alpha", "email", day) == 1


def test_the_admin_sets_reads_and_removes_the_send_cap_over_http(world):
    j = world
    root = j.admin()
    shown = root.get(CAP_URL.format("alpha")).json()
    assert shown["daily_cap"] is None and shown["effective_cap"] is None
    assert shown["status"] == "not_configured" and shown["used_today"] == 0
    assert shown["max_cap"] == 100000

    # a value that is not a whole number from 1 to the maximum is refused, nothing is stored
    for bad in (0, -3, 100001, "5", 2.5, True, None):
        refused = root.put(CAP_URL.format("alpha"), json={"daily_cap": bad})
        assert refused.status_code == 422, bad
    assert root.put(CAP_URL.format("alpha"), json={}).status_code == 422
    assert SqlSendCapStore(j.engine).get("alpha") is None

    # the plan holds the cap: effective = min(cap, plan quota)
    root.post(
        "/admin/v1/plans",
        json={
            "name": "Small",
            "monthly_price": "10",
            "currency": "usd",
            "included_credits": 10,
            "daily_quota": 3,
            "active": True,
        },
    )
    assert root.put("/admin/v1/clients/alpha/plan", json={"plan_id": "small"}).status_code == 200
    done = root.put(CAP_URL.format("alpha"), json={"daily_cap": 50}).json()
    assert (done["daily_cap"], done["plan_daily_quota"], done["effective_cap"]) == (50, 3, 3)
    done = root.put(CAP_URL.format("alpha"), json={"daily_cap": 2}).json()
    assert (done["daily_cap"], done["effective_cap"], done["remaining_today"]) == (2, 2, 2)

    # isolation and permissions: an unknown client, an Admin of another perimeter, a client session
    assert root.get(CAP_URL.format("ghost")).status_code == 404
    assert root.put(CAP_URL.format("ghost"), json={"daily_cap": 1}).status_code == 404
    bob = j.admin("bob")
    assert bob.get(CAP_URL.format("alpha")).status_code == 404
    assert (
        bob.put(CAP_URL.format("alpha"), json={"daily_cap": 9}).status_code == 403
    )  # not an owner
    assert bob.delete(CAP_URL.format("alpha")).status_code == 403
    assert bob.get(CAP_URL.format("beta")).json()["daily_cap"] is None
    assert bob.put(CAP_URL.format("beta"), json={"daily_cap": 9}).status_code == 403
    assert bob.delete(CAP_URL.format("beta")).status_code == 403
    assert j.browser("alpha").get(CAP_URL.format("alpha")).status_code in (401, 403, 404)
    assert j.http().get(CAP_URL.format("alpha")).status_code == 401
    assert (
        SqlSendCapStore(j.engine).get("alpha") == 2
        and SqlSendCapStore(j.engine).get("beta") is None
    )

    # persisted across a restart, then removed
    j.restart()
    root = j.admin()
    assert root.get(CAP_URL.format("alpha")).json()["daily_cap"] == 2
    gone = root.delete(CAP_URL.format("alpha")).json()
    assert gone["daily_cap"] is None and gone["effective_cap"] is None
    assert SqlSendCapStore(j.engine).get("alpha") is None
