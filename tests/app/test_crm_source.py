"""The rules every CRM source follows, with a scripted reader in place of any vendor.

What is proved: a record is kept only if its id and its status are understood; a do-not-contact
signal is never lost; nothing is ever written; a failing read raises instead of answering with
less; and nothing personal is in a problem, a `repr` or an error.
"""

import pytest

from sales_app.sourcing import (
    ProblemCode,
    ProspectChange,
    ProspectField,
    SourceAuthError,
    SourceReadOnlyError,
    SourceUnavailableError,
    StatusMapping,
)
from sales_app.sourcing.crm_source import CrmProspectSource, CrmRecord
from sales_core.contracts import ProspectStatus

CLIENT = "client-a"
PHONE = "+216 99 888 777"
EMAIL = "leak.me@example.com"
MAPPING = StatusMapping.from_config(
    {"new": "new", "later": "paused", "won": "closed", "refused": "opted_out"}
)


def record(identifier="1", **kwargs) -> CrmRecord:
    values = {"name": "Ada Lovelace", "phone": PHONE, "email": None, "status": "new"} | kwargs
    return CrmRecord(identifier, **values)


class ScriptedReader:
    def __init__(self, *records: CrmRecord) -> None:
        self.records = list(records)
        self.calls: list[str] = []
        self.failure: Exception | None = None

    def _go(self, name: str) -> None:
        self.calls.append(name)
        if self.failure is not None:
            raise self.failure

    def list_records(self):
        self._go("list")
        return list(self.records)

    def get_record(self, native_id):
        self._go("get")
        return next((r for r in self.records if r.native_id == native_id), None)

    def find_records(self, *, phone=None, email=None):
        self._go("find")
        return list(self.records)  # looser than the source: it must check for itself

    def verify(self):
        self._go("verify")
        return ["warning: something soft"]


def source(*records: CrmRecord) -> tuple[CrmProspectSource, ScriptedReader]:
    reader = ScriptedReader(*records)
    return (
        CrmProspectSource(reader, client_id=CLIENT, mapping=MAPPING, namespace="crm"),
        reader,
    )


class TestWhatItOwns:
    def test_it_owns_the_person_and_the_crms_status_and_can_write_nothing(self):
        capabilities = source()[0].capabilities
        assert capabilities.owned == {
            ProspectField.DISPLAY_NAME,
            ProspectField.PHONE,
            ProspectField.EMAIL,
            ProspectField.PREFERRED_CHANNEL,
            ProspectField.LOCALE,
            ProspectField.STATUS,
        }
        assert capabilities.writable == frozenset() and not capabilities.can_create
        assert capabilities.id_namespace == "crm" and capabilities.consent_signal

    def test_it_is_bound_to_one_client(self):
        found, _ = source(record())
        assert found.client_id == CLIENT
        assert {p.client_id for p in found.list_prospects()} == {CLIENT}


class TestReading:
    def test_a_record_with_an_understood_status_is_a_prospect(self):
        found, _ = source(record("7", status="Later", locale="fr", email=EMAIL))
        (prospect,) = found.list_prospects()
        assert prospect.prospect_id == "7" and prospect.display_name == "Ada Lovelace"
        assert prospect.status is ProspectStatus.PAUSED and prospect.locale == "fr"
        assert (prospect.contact.phone, prospect.contact.email) == (PHONE, EMAIL)
        assert found.problems() == () and found.refused_contacts() == ()

    def test_the_crms_statuses_follow_the_operators_mapping_and_nothing_else(self):
        found, _ = source(
            record("1", status="new"),
            record("2", status="won"),
            record("3", status="refused"),
        )
        assert [p.status for p in found.list_prospects()] == [
            ProspectStatus.NEW,
            ProspectStatus.CLOSED,
            ProspectStatus.OPTED_OUT,
        ]

    def test_a_blank_or_unmapped_status_leaves_the_record_out_and_says_why(self):
        found, _ = source(
            record("1", status=None),
            record("2", status="  "),
            record("3", status="customer"),
            record("4", status="new"),
        )
        assert [p.prospect_id for p in found.list_prospects()] == ["4"]
        assert [(p.code, p.native_id) for p in found.problems()] == [
            (ProblemCode.BLANK_STATUS, "1"),
            (ProblemCode.BLANK_STATUS, "2"),
            (ProblemCode.UNMAPPED_STATUS, "3"),
        ]

    @pytest.mark.parametrize("bad", ["", "a:b", "a b", "é", "x" * 129])
    def test_an_id_outside_the_alphabet_is_left_out_and_never_shown(self, bad):
        found, _ = source(record(bad), record("2"))
        assert [p.prospect_id for p in found.list_prospects()] == ["2"]
        (problem,) = found.problems()
        assert problem.code is ProblemCode.INVALID_NATIVE_ID and problem.native_id is None

    def test_without_a_name_the_contact_stands_in_for_it(self):
        found, _ = source(record("1", name=None, phone=None, email=EMAIL), record("2", name=None))
        names = [p.display_name for p in found.list_prospects()]
        assert names == [EMAIL, PHONE]

    def test_a_new_read_describes_what_is_left_out_now(self):
        found, reader = source(record("1", status=None))
        found.list_prospects()
        assert len(found.problems()) == 1
        reader.records = [record("1")]
        found.list_prospects()
        assert found.problems() == ()

    def test_get_and_find_return_only_what_is_understood(self):
        found, _ = source(record("1", status="customer"), record("2", email=EMAIL, phone=None))
        assert found.get_prospect("1") is None and found.get_prospect("3") is None
        assert found.get_prospect("2").prospect_id == "2"
        assert found.get_prospect("a:b") is None

    def test_an_invalid_id_does_not_reach_the_reader(self):
        found, reader = source(record())
        assert found.get_prospect("../x") is None
        assert reader.calls == []

    def test_a_looser_search_than_ours_does_not_make_a_match(self):
        found, _ = source(record("1", phone="+216 11 111 111"), record("2", phone="0021699888777"))
        assert found.find_prospect_by_contact(phone="(216) 99-888-777") is None
        assert found.find_prospect_by_contact(phone="+21699888777") is None  # "00..." is not "+"

    def test_find_matches_by_digits_or_address_case_insensitively(self):
        found, _ = source(
            record("1", phone="+21611111111", email=None),
            record("2", phone=None, email="Leak.Me@Example.com"),
        )
        assert found.find_prospect_by_contact(phone="216 11 111 111").prospect_id == "1"
        assert found.find_prospect_by_contact(email=EMAIL).prospect_id == "2"
        assert found.find_prospect_by_contact() is None


class TestConsent:
    def test_a_do_not_contact_signal_on_a_kept_record_makes_it_opted_out(self):
        found, _ = source(record("1", status="new", do_not_contact=True))
        (prospect,) = found.list_prospects()
        assert prospect.status is ProspectStatus.OPTED_OUT
        assert found.refused_contacts() == ()  # a kept record is listed, not repeated

    def test_the_signal_on_a_left_out_record_is_handed_on_with_the_bare_contact(self):
        found, _ = source(
            record("1", status="customer", do_not_contact=True, email=EMAIL),
            record("2", status=None, do_not_contact=True, phone=None, email=None),
            record("a:b", do_not_contact=True, phone="+21611111111"),
        )
        assert found.list_prospects() == []
        contacts = {(c.phone, c.email, c.preferred_channel) for c in found.refused_contacts()}
        assert contacts == {(PHONE, EMAIL, None), ("+21611111111", None, None)}

    def test_a_refusal_is_collapsed_not_repeated(self):
        found, _ = source(
            record("1", status=None, do_not_contact=True),
            record("2", status="customer", do_not_contact=True),
        )
        found.list_prospects()
        assert len(found.refused_contacts()) == 1

    def test_no_refusal_comes_from_a_record_that_did_not_ask(self):
        found, _ = source(record("1", status=None))
        found.list_prospects()
        assert found.refused_contacts() == ()

    def test_a_get_of_a_refusing_record_keeps_the_refusal_too(self):
        found, _ = source(record("1", status="customer", do_not_contact=True))
        assert found.get_prospect("1") is None
        assert len(found.refused_contacts()) == 1


class TestReadOnly:
    def test_it_never_writes_and_never_calls_the_reader_to_find_out(self):
        found, reader = source(record())
        with pytest.raises(SourceReadOnlyError):
            found.create_prospects(found.list_prospects())
        change = ProspectChange(
            prospect_id="1",
            new={ProspectField.STATUS: ProspectStatus.CLOSED},
            expected={ProspectField.STATUS: ProspectStatus.NEW},
        )
        reader.calls.clear()
        with pytest.raises(SourceReadOnlyError):
            found.update_prospects([change])
        with pytest.raises(SourceReadOnlyError):
            found.create_prospects([])
        assert reader.calls == []


class TestFailures:
    @pytest.mark.parametrize("error", [SourceUnavailableError("down"), SourceAuthError("no")])
    @pytest.mark.parametrize("call", ["list_prospects", "get_prospect", "find_prospect_by_contact"])
    def test_a_failing_read_raises_it_never_answers_with_less(self, error, call):
        found, reader = source(record())
        reader.failure = error
        arguments = {
            "list_prospects": (),
            "get_prospect": ("1",),
            "find_prospect_by_contact": (),
        }[call]
        kwargs = {"phone": PHONE} if call == "find_prospect_by_contact" else {}
        with pytest.raises(type(error)):
            getattr(found, call)(*arguments, **kwargs)

    def test_a_failed_list_keeps_the_last_description_it_did_not_replace(self):
        found, reader = source(record("1", status=None))
        found.list_prospects()
        reader.failure = SourceUnavailableError("down")
        with pytest.raises(SourceUnavailableError):
            found.list_prospects()
        assert len(found.problems()) == 1

    def test_verify_is_the_readers(self):
        found, _ = source()
        assert found.verify() == ["warning: something soft"]


class TestNothingPersonalLeaks:
    def test_repr_problems_and_details_hold_no_name_number_or_address(self):
        found, reader = source(
            record("1", status="customer", email=EMAIL, do_not_contact=True),
            record("a:b", email=EMAIL),
            record("3", email=EMAIL),
        )
        prospects = found.list_prospects()
        shown = " ".join(
            [repr(found), repr(reader.records[0]), *(repr(p) for p in found.problems())]
            + [p.detail for p in found.problems()]
        )
        for secret in ("Ada", "Lovelace", PHONE, "99 888", EMAIL, "leak.me"):
            assert secret not in shown, secret
        assert len(prospects) == 1
