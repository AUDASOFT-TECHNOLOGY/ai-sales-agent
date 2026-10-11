"""The write-back policy and the pure plan of status writes (11.2.5a). No source, no store."""

import pytest
from factories import CLIENT, make_prospect
from sourcing_fakes import default_capabilities

from sales_app.sourcing import SOURCE_ONLY_FIELDS, ProspectChange, ProspectField, ProspectState
from sales_app.sourcing.writeback import (
    WRITEBACK_FIELDS,
    SkipReason,
    WriteBackPolicy,
    WriteBackPolicyError,
    plan_status_writes,
    wanted_status,
)
from sales_core.contracts import ContactInfo, ProspectId, ProspectStatus

S = ProspectStatus
STATUS = ProspectField.STATUS
PHONE = "+216 99 888 777"
ON = WriteBackPolicy.of(STATUS)
ON_WITH_OPT_OUT = WriteBackPolicy.of(STATUS, propagate_opt_out=True)


def record(native, status=S.NEW, phone=None):
    return make_prospect(
        prospect_id=ProspectId(native), status=status, contact=ContactInfo(phone=phone)
    )


def held(public, status):
    return {
        ProspectId(public): ProspectState(
            client_id=CLIENT, prospect_id=ProspectId(public), status=status
        )
    }


def never(contact):
    return False


def plan(records, states=None, *, policy=ON, caps=None, blocked=never, healthy=True):
    return plan_status_writes(
        records,
        states or {},
        capabilities=caps or default_capabilities(),
        policy=policy,
        is_blocked=blocked,
        registry_healthy=healthy,
    )


# ---- the policy -----------------------------------------------------------------------------


def test_the_default_policy_writes_nothing():
    policy = WriteBackPolicy()
    assert policy.fields == frozenset() and not policy.enabled and not policy.propagate_opt_out
    assert not policy.allows(STATUS)


def test_only_the_status_can_ever_be_written_back():
    assert WRITEBACK_FIELDS == {STATUS}
    assert not WRITEBACK_FIELDS & SOURCE_ONLY_FIELDS
    for field in ProspectField:
        if field is not STATUS:
            with pytest.raises(WriteBackPolicyError):
                WriteBackPolicy.of(field)
    with pytest.raises(WriteBackPolicyError):
        WriteBackPolicy.of(STATUS, ProspectField.PHONE)


def test_propagating_an_opt_out_needs_the_status():
    with pytest.raises(WriteBackPolicyError):
        WriteBackPolicy(propagate_opt_out=True)
    assert ON_WITH_OPT_OUT.enabled


def test_the_policy_is_checked_against_what_the_source_can_do():
    ON.validate_for(default_capabilities())
    WriteBackPolicy().validate_for(default_capabilities(writable=frozenset()))
    with pytest.raises(WriteBackPolicyError):
        ON.validate_for(default_capabilities(writable=frozenset()))
    with pytest.raises(WriteBackPolicyError):
        ON_WITH_OPT_OUT.validate_for(default_capabilities(consent_signal=False))
    ON.validate_for(default_capabilities(consent_signal=False))  # not asked to propagate


def test_a_policy_that_does_not_fit_the_source_fails_the_plan():
    with pytest.raises(WriteBackPolicyError):
        plan([record("1")], caps=default_capabilities(writable=frozenset()))


# ---- what Revansa wants ---------------------------------------------------------------------


@pytest.mark.parametrize("status", [None, S.NEW, S.ACTIVE])
def test_a_contactable_status_is_never_passed_on(status):
    result = wanted_status(status, registry_blocks=False, registry_healthy=True, policy=ON)
    assert result == (None, SkipReason.NOTHING_TO_WRITE)


@pytest.mark.parametrize("status", [S.PAUSED, S.CLOSED])
def test_a_restriction_revansa_holds_is_passed_on(status):
    result = wanted_status(status, registry_blocks=False, registry_healthy=True, policy=ON)
    assert result == (status, None)


def test_an_opt_out_is_passed_on_only_if_asked_and_while_the_registry_is_healthy():
    def ask(policy, healthy):
        return wanted_status(
            S.OPTED_OUT, registry_blocks=False, registry_healthy=healthy, policy=policy
        )

    assert ask(ON, True) == (None, SkipReason.OPT_OUT_NOT_PROPAGATED)
    assert ask(ON_WITH_OPT_OUT, True) == (S.OPTED_OUT, None)
    assert ask(ON_WITH_OPT_OUT, False) == (None, SkipReason.OPT_OUT_NOT_PROPAGATED)


def test_an_opt_out_is_never_replaced_by_a_lesser_status():
    # Revansa holds CLOSED but the registry blocks: the opt-out is the strictest, and if it is not
    # to be propagated nothing is written (CLOSED is not "the same thing, only gentler").
    result = wanted_status(S.CLOSED, registry_blocks=True, registry_healthy=True, policy=ON)
    assert result == (None, SkipReason.OPT_OUT_NOT_PROPAGATED)


# ---- the plan -------------------------------------------------------------------------------


def test_an_empty_policy_plans_nothing_and_asks_nobody():
    asked = []

    def blocked(contact):
        asked.append(contact)
        return True

    result = plan_status_writes(
        [record("1")],
        held("fake_crm:1", S.CLOSED),
        capabilities=default_capabilities(),
        policy=WriteBackPolicy(),
        is_blocked=blocked,
        registry_healthy=True,
    )
    assert result.writes == () and dict(result.skipped) == {} and asked == []


def test_a_restriction_is_written_with_the_status_the_source_held():
    result = plan([record("1", S.NEW), record("2", S.PAUSED)], held("fake_crm:1", S.CLOSED))
    assert [(w.prospect_id, w.public_id, w.new, w.expected) for w in result.writes] == [
        ("1", "fake_crm:1", S.CLOSED, S.NEW)
    ]
    (change,) = result.changes
    assert change == ProspectChange(
        prospect_id="1", new={STATUS: S.CLOSED}, expected={STATUS: S.NEW}
    )
    assert dict(result.skipped) == {SkipReason.NOTHING_TO_WRITE: 1}


def test_a_restriction_is_never_loosened_or_written_over_a_stricter_one():
    states = {**held("fake_crm:1", S.PAUSED), **held("fake_crm:2", S.PAUSED)}
    result = plan([record("1", S.CLOSED), record("2", S.OPTED_OUT)], states)
    assert result.writes == ()
    assert dict(result.skipped) == {SkipReason.NOT_TIGHTER: 2}


def test_a_status_the_source_already_holds_is_not_written_again():
    result = plan([record("1", S.CLOSED)], held("fake_crm:1", S.CLOSED))
    assert result.writes == () and dict(result.skipped) == {SkipReason.ALREADY_APPLIED: 1}


def test_a_stricter_status_replaces_a_lesser_restriction():
    result = plan([record("1", S.PAUSED)], held("fake_crm:1", S.CLOSED))
    assert [(w.new, w.expected) for w in result.writes] == [(S.CLOSED, S.PAUSED)]


def test_the_registry_opt_out_is_written_only_when_asked_and_healthy():
    def blocked(contact):
        return contact.phone == PHONE

    records = [record("1", phone=PHONE), record("2", phone="+216 99 000 000")]
    assert plan(records, blocked=blocked).writes == ()
    on = plan(records, policy=ON_WITH_OPT_OUT, blocked=blocked)
    assert [(w.prospect_id, w.new, w.expected) for w in on.writes] == [("1", S.OPTED_OUT, S.NEW)]


def test_an_unhealthy_registry_marks_nobody_and_is_not_consulted():
    asked = []

    def blocked(contact):
        asked.append(contact)
        return True  # what an unhealthy registry answers for everyone

    result = plan(
        [record(str(n), phone=f"+216 99 000 00{n}") for n in range(5)],
        policy=ON_WITH_OPT_OUT,
        blocked=blocked,
        healthy=False,
    )
    assert result.writes == () and asked == []


def test_the_revansa_opt_out_is_passed_on_with_its_policy():
    states = held("fake_crm:1", S.OPTED_OUT)
    assert plan([record("1")], states).writes == ()
    (write,) = plan([record("1")], states, policy=ON_WITH_OPT_OUT).writes
    assert (write.new, write.expected) == (S.OPTED_OUT, S.NEW)
    assert plan([record("1")], states, policy=ON_WITH_OPT_OUT, healthy=False).writes == ()


def test_ids_follow_the_source_namespace():
    plain = default_capabilities(id_namespace=None)
    (write,) = plan([record("abc")], held("abc", S.CLOSED), caps=plain).writes
    assert (write.prospect_id, write.public_id) == ("abc", "abc")
    other = plan([record("abc")], held("fake_crm:abc", S.CLOSED), caps=plain)
    assert other.writes == ()  # the state of another id is not this record's


def test_a_record_with_an_unusable_id_is_counted_and_never_written():
    result = plan([record("bad id!"), record("1")], held("fake_crm:1", S.CLOSED))
    assert [w.prospect_id for w in result.writes] == ["1"]
    assert result.skipped[SkipReason.INVALID_ID] == 1


def test_the_plan_is_deterministic_and_keeps_the_order_of_the_records():
    states = {**held("fake_crm:b", S.CLOSED), **held("fake_crm:a", S.PAUSED)}
    records = [record("b"), record("a"), record("c")]
    first, second = plan(records, states), plan(records, states)
    assert first == second
    assert [w.prospect_id for w in first.writes] == ["b", "a"]


def test_nothing_personal_is_in_a_plan_or_its_representation():
    result = plan(
        [record("1", phone=PHONE)],
        held("fake_crm:1", S.CLOSED),
        policy=ON_WITH_OPT_OUT,
        blocked=lambda c: True,
    )
    text = repr(result) + repr(result.changes) + repr(ON_WITH_OPT_OUT)
    for leak in (PHONE, "21699888777", "Alex", "Example"):
        assert leak not in text


def test_a_plan_is_frozen():
    result = plan([record("1")], held("fake_crm:1", S.CLOSED))
    with pytest.raises(TypeError):
        result.skipped[SkipReason.INVALID_ID] = 1  # type: ignore[index]
    with pytest.raises(AttributeError):
        result.writes = ()  # type: ignore[misc]
