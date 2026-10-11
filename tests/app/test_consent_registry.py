"""The consent registry: keys, events, setting up, recording, reading. Behaviour, not attacks (the
attacks are in `test_consent_security.py`)."""

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from consent_support import (
    CLIENT,
    EMAIL,
    OTHER,
    PHONE,
    PHONE_DIGITS,
    PHONE_SPELLED,
    SECOND_PHONE,
    START,
    contact,
    make_world,
)
from factories import make_prospect
from sourcing_fakes import FakeProspectSource, default_capabilities

from sales_app.secret_store import (
    CredentialService,
    InMemorySecretStore,
    SecretStoreCorruptError,
    UnavailableSecretStore,
    client_scope,
)
from sales_app.secret_store.catalog import spec_for
from sales_app.secret_store.errors import UnknownCredentialError
from sales_app.sourcing import StatusMapping
from sales_app.sourcing.consent import (
    ConsentFormatError,
    ConsentKeyError,
    ConsentKeys,
    ConsentRefusedError,
    ConsentRegistry,
    ConsentSecrets,
    ConsentStoreUnavailableError,
    ConsentUnavailableError,
    EventType,
    Health,
    InitResult,
    Origin,
    ReasonCode,
    new_root_key,
    record_refusals,
    verify_chain,
)
from sales_app.sourcing.consent.events import from_line, sign_event, to_line
from sales_app.sourcing.consent.keys import parse_root_key
from sales_core.contracts import Channel, ContactInfo, ProspectId

ROOT = new_root_key()


def keys(client=CLIENT, root=ROOT):
    return ConsentKeys.from_stored(root, client)


def event(seq=1, *, prev=None, keys_=None, **kw):
    keys_ = keys_ or keys()
    values = {
        "seq": seq,
        "key": keys_.digest("phone:21699888777"),
        "type": EventType.OPT_OUT,
        "origin": Origin.OPERATOR,
        "at": START,
        "actor": "cli:tester",
        "reason": ReasonCode.OPERATOR_REQUEST,
        "prev_mac": prev or keys_.genesis_mac(),
        "event_id": f"{seq:032x}",
    }
    return sign_event(keys_, **(values | kw))


# ---- keys ------------------------------------------------------------------------------------


class TestKeys:
    def test_a_root_key_is_random_and_strictly_shaped(self):
        first, second = new_root_key(), new_root_key()
        assert first != second and len(first) == 43
        assert len(parse_root_key(first)) == 32

    @pytest.mark.parametrize("bad", ["", "short", ROOT + "=", ROOT + "\n", "\n" + ROOT, "é" * 43])
    def test_a_malformed_root_key_is_refused_without_repeating_it(self, bad):
        with pytest.raises(ConsentKeyError) as raised:
            parse_root_key(bad)
        assert bad.strip() not in str(raised.value) or not bad.strip()

    def test_a_digest_is_stable_and_the_same_for_every_spelling_of_a_contact(self):
        k = keys()
        a = k.digests(contact(PHONE))
        assert a == k.digests(contact(PHONE_SPELLED)) == k.digests(contact(PHONE_DIGITS))
        assert len(a) == 1 and len(a[0]) == 43

    def test_a_contact_with_a_phone_and_an_address_has_two_identities(self):
        both = keys().digests(contact(PHONE, EMAIL))
        assert len(both) == 2 and both == tuple(sorted(both))

    def test_an_address_is_compared_ignoring_case_and_blanks(self):
        k = keys()
        assert k.digests(contact(None, " Leak.Me@Example.COM ")) == k.digests(contact(None, EMAIL))

    def test_a_contact_with_nothing_usable_has_no_digest(self):
        assert keys().digests(ContactInfo()) == ()

    def test_two_clients_never_share_a_digest(self):
        assert keys(CLIENT).digests(contact()) != keys(OTHER).digests(contact())

    def test_two_keys_never_share_a_digest(self):
        assert keys(root=new_root_key()).digests(contact()) != keys().digests(contact())

    def test_a_digest_is_not_an_unkeyed_hash_that_could_be_tested_by_guessing(self):
        (digest,) = keys().digests(contact())
        for plain in ("phone:" + PHONE_DIGITS, PHONE_DIGITS, PHONE):
            assert digest != hashlib.sha256(plain.encode()).hexdigest()
            assert digest not in {
                __import__("base64")
                .urlsafe_b64encode(hashlib.sha256(plain.encode()).digest())
                .rstrip(b"=")
                .decode()
            }

    def test_a_phone_and_an_address_of_the_same_text_do_not_collide(self):
        k = keys()
        assert k.digest("phone:123") != k.digest("email:123")

    def test_keys_show_nothing_when_printed(self):
        k = keys()
        assert ROOT not in repr(k) and "redacted" in repr(k)
        with pytest.raises(TypeError):
            hash(k)


# ---- events ----------------------------------------------------------------------------------


class TestEvents:
    def test_events_chain_and_verify(self):
        first = event(1)
        second = event(2, prev=first.mac, type=EventType.RELEASE, reason=ReasonCode.OTHER)
        third = event(3, prev=second.mac)
        result = verify_chain(keys(), [first, second, third])
        assert result.ok and result.last_seq == 3 and result.last_mac == third.mac

    def test_an_empty_chain_is_valid_and_ends_at_the_genesis(self):
        result = verify_chain(keys(), [])
        assert result.ok and result.last_seq == 0 and result.last_mac == keys().genesis_mac()

    def test_an_event_survives_a_round_trip_through_a_line(self):
        first = event(1, evidence_ref="ticket #12")
        assert from_line(to_line(first)) == first

    def test_a_line_has_no_contact_in_it(self):
        text = to_line(event(1))
        for leaky in ("21699888777", "phone", "+216"):
            assert leaky not in text

    def test_every_field_is_covered_by_the_signature(self):
        original = event(1)
        changes = {
            "event_id": f"{9:032x}",
            "key": keys().digest("phone:1"),
            "type": EventType.RELEASE,
            "origin": Origin.SOURCE,
            "at": START.replace(minute=1),
            "actor": "cli:other",
            "reason": ReasonCode.OTHER,
            "evidence_ref": "changed",
        }
        for name, value in changes.items():
            extra = {"reason": ReasonCode.OTHER} if name == "type" else {}  # fits both types
            altered = replace(original, **{name: value, **extra})
            assert not verify_chain(keys(), [altered]).ok, name

    def test_the_signature_of_one_client_is_worthless_for_another(self):
        assert not verify_chain(keys(OTHER), [event(1)]).ok

    @pytest.mark.parametrize(
        "mutation",
        [
            lambda d: d.pop("actor"),
            lambda d: d.update(extra="x"),
            lambda d: d.update(seq="1"),
            lambda d: d.update(seq=True),
            lambda d: d.update(seq=0),
            lambda d: d.update(type="other"),
            lambda d: d.update(origin="somewhere"),
            lambda d: d.update(reason="whatever"),
            lambda d: d.update(key="short"),
            lambda d: d.update(mac="x" * 43 + "\n"),
            lambda d: d.update(actor="has space"),
            lambda d: d.update(evidence_ref="<script>"),
            lambda d: d.update(at="2026-03-01T09:00:00"),  # no time zone
            lambda d: d.update(at="2026-03-01T09:00:00+00:00"),  # not the one accepted spelling
            lambda d: d.update(reason="written_consent"),  # not a reason for an opt-out
        ],
    )
    def test_a_line_that_is_not_exactly_what_is_written_is_refused(self, mutation):
        data = json.loads(to_line(event(1)))
        mutation(data)
        with pytest.raises(ConsentFormatError):
            from_line(json.dumps(data))

    @pytest.mark.parametrize("line", ["", "not json", "[]", "null", '{"seq":1}'])
    def test_a_line_that_is_not_an_event_is_refused(self, line):
        with pytest.raises(ConsentFormatError):
            from_line(line)

    def test_a_release_reason_cannot_describe_an_opt_out_and_the_other_way_round(self):
        with pytest.raises(ConsentFormatError):
            event(1, reason=ReasonCode.WRITTEN_CONSENT)
        with pytest.raises(ConsentFormatError):
            event(1, type=EventType.RELEASE, reason=ReasonCode.KEYWORD)

    def test_a_repeated_event_id_is_a_broken_chain(self):
        first = event(1)
        second = event(2, prev=first.mac, event_id=first.event_id)
        assert verify_chain(keys(), [first, second]).reason == "duplicate_event"


# ---- setting up ------------------------------------------------------------------------------


class TestSettingUp:
    def test_a_registry_nobody_set_up_blocks_everyone(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        snapshot = world.registry.snapshot()
        assert snapshot.health is Health.NOT_INITIALISED and not snapshot.healthy
        assert snapshot.blocks(contact()) and snapshot.blocks(ContactInfo())

    def test_setting_up_makes_a_healthy_registry_that_blocks_nobody(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        assert world.registry.initialise(actor="cli:tester") is InitResult.CREATED
        snapshot = world.registry.snapshot()
        assert snapshot.health is Health.OK
        assert not snapshot.blocks(contact()) and not snapshot.blocks(ContactInfo())

    def test_setting_up_again_changes_nothing_and_keeps_the_key(self, tmp_path):
        world = make_world(tmp_path)
        key = world.store.get(world.scope, "hmac_key")
        anchor = world.store.get(world.scope, "anchor")
        assert world.registry.initialise(actor="cli:tester") is InitResult.ALREADY_INITIALISED
        assert world.store.get(world.scope, "hmac_key") == key
        assert world.store.get(world.scope, "anchor") == anchor

    def test_setting_up_again_after_events_keeps_everything(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        key = world.store.get(world.scope, "hmac_key")
        assert world.registry.initialise(actor="cli:tester") is InitResult.ALREADY_INITIALISED
        assert world.store.get(world.scope, "hmac_key") == key
        assert world.registry.snapshot().blocks(contact())

    def test_an_interrupted_set_up_is_completed_without_a_new_key(self, tmp_path):
        world = make_world(tmp_path)
        key = world.store.get(world.scope, "hmac_key")
        world.store.delete(world.scope, "anchor")  # it stopped after the key
        assert world.registry.snapshot().health is Health.NOT_INITIALISED
        assert world.registry.initialise(actor="cli:tester") is InitResult.COMPLETED
        assert world.store.get(world.scope, "hmac_key") == key
        assert world.registry.snapshot().health is Health.OK

    def test_it_never_makes_a_new_key_over_an_existing_registry(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        key = world.store.get(world.scope, "hmac_key")
        world.store.delete(world.scope, "hmac_key")  # events exist, the key is gone
        with pytest.raises(ConsentRefusedError) as raised:
            world.registry.initialise(actor="cli:tester")
        assert raised.value.reason == "key_missing"
        assert world.store.get(world.scope, "hmac_key") is None  # nothing was invented
        assert key not in str(raised.value)

    def test_it_refuses_a_corrupt_registry_and_touches_nothing(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        world.block(contact(SECOND_PHONE))
        before = world.log.path.read_bytes()
        key = world.store.get(world.scope, "hmac_key")
        world.rewrite(world.line_texts()[:1])  # a record deleted
        tampered = world.log.path.read_bytes()
        with pytest.raises(ConsentRefusedError):
            world.registry.initialise(actor="cli:tester")
        assert world.log.path.read_bytes() == tampered != before
        assert world.store.get(world.scope, "hmac_key") == key

    def test_it_refuses_when_only_the_anchor_is_left(self, tmp_path):
        world = make_world(tmp_path)
        world.store.delete(world.scope, "hmac_key")
        with pytest.raises(ConsentRefusedError):
            world.registry.initialise(actor="cli:tester")

    def test_it_cannot_set_up_where_secrets_cannot_be_stored(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        unavailable = ConsentSecrets(UnavailableSecretStore(), world.audit, CLIENT)
        registry = ConsentRegistry(CLIENT, world.log, unavailable)
        assert registry.snapshot().health is Health.NOT_INITIALISED
        with pytest.raises(ConsentStoreUnavailableError):
            registry.initialise(actor="cli:tester")
        assert [e.outcome for e in world.audit.recent()] == ["rejected"]
        assert not world.log.path.exists()

    def test_the_key_and_the_anchor_are_audited_and_the_audit_never_holds_them(self, tmp_path):
        world = make_world(tmp_path)
        key = world.store.get(world.scope, "hmac_key")
        events = list(reversed(world.audit.recent()))
        assert [(e.scope, e.key, e.outcome) for e in events] == [
            (world.scope, "hmac_key", "stored"),
            (world.scope, "anchor", "stored"),
        ]
        assert all(e.actor == "cli:tester" for e in events)
        assert key not in repr(world.audit.recent())

    def test_the_registry_keeps_clear_of_the_admins_credential_catalogue(self, tmp_path):
        world = make_world(tmp_path)
        for key in ("hmac_key", "anchor", "consent.hmac_key", "consent.anchor"):
            with pytest.raises(UnknownCredentialError):
                spec_for(client_scope(CLIENT), key)
        listed = {
            s.key
            for s in CredentialService(world.store, world.audit).describe(client_scope(CLIENT))
        }
        assert not {k for k in listed if "consent" in k or k in ("hmac_key", "anchor")}
        assert world.scope == "consent:client-a" != client_scope(CLIENT)

    def test_the_scope_of_a_registry_needs_a_well_formed_client_id(self):
        from sales_app.sourcing.consent import consent_scope

        for bad in ("", "Client A", "a:b", "x\n"):
            with pytest.raises(ValueError):
                consent_scope(bad)


# ---- recording ---------------------------------------------------------------------------------


class TestRecording:
    def test_a_recorded_refusal_blocks_the_contact_and_only_that_contact(self, tmp_path):
        world = make_world(tmp_path)
        assert world.block(contact(PHONE)) == 1
        snapshot = world.registry.snapshot()
        assert snapshot.blocks(contact(PHONE))
        assert not snapshot.blocks(contact(SECOND_PHONE))
        assert not snapshot.blocks(contact(None, EMAIL))

    def test_every_spelling_of_the_contact_is_blocked(self, tmp_path):
        world = make_world(tmp_path)
        world.block(contact(PHONE))
        snapshot = world.registry.snapshot()
        assert snapshot.blocks(contact(PHONE_SPELLED)) and snapshot.blocks(contact(PHONE_DIGITS))

    def test_recording_twice_adds_nothing(self, tmp_path):
        world = make_world(tmp_path)
        assert world.block() == 1
        assert world.block() == 0
        assert world.block(contact(PHONE_SPELLED)) == 0
        assert len(world.line_texts()) == 1

    def test_a_phone_and_an_address_are_two_identities_blocked_separately(self, tmp_path):
        world = make_world(tmp_path)
        assert world.block(contact(PHONE, EMAIL)) == 2
        assert world.block(contact(PHONE)) == 0
        assert world.block(contact(None, EMAIL)) == 0
        snapshot = world.registry.snapshot()
        assert snapshot.blocks(contact(PHONE)) and snapshot.blocks(contact(None, EMAIL))
        # a record carrying only one of them is caught too, and one carrying both
        assert snapshot.blocks(contact(PHONE, "someone.else@example.com"))

    def test_a_contact_with_nothing_usable_records_nothing(self, tmp_path):
        world = make_world(tmp_path)
        assert world.block(ContactInfo()) == 0
        assert world.line_texts() == []

    def test_events_are_numbered_without_a_gap_and_the_anchor_follows(self, tmp_path):
        world = make_world(tmp_path)
        for number in ("+216 99 000 001", "+216 99 000 002", "+216 99 000 003"):
            world.block(contact(number))
        events = world.registry.events()
        assert [e.seq for e in events] == [1, 2, 3]
        assert world.store.get(world.scope, "anchor") == f"3.{events[-1].mac}"
        assert [e.outcome for e in world.audit.recent()][:3] == ["stored"] * 3

    def test_what_is_recorded_is_the_origin_the_reason_the_actor_and_the_time(self, tmp_path):
        world = make_world(tmp_path)
        world.clock.advance(hours=2)
        world.block(
            origin=Origin.INBOUND_KEYWORD,
            reason=ReasonCode.KEYWORD,
            actor="system:engine",
            evidence_ref="msg #44",
        )
        (only,) = world.registry.events()
        assert (only.type, only.origin, only.reason) == (
            EventType.OPT_OUT,
            Origin.INBOUND_KEYWORD,
            ReasonCode.KEYWORD,
        )
        assert only.actor == "system:engine" and only.evidence_ref == "msg #44"
        assert only.at == datetime(2026, 3, 1, 11, 0, tzinfo=UTC)

    def test_a_registry_that_is_not_healthy_records_nothing_and_says_so(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        with pytest.raises(ConsentUnavailableError) as raised:
            world.block()
        assert raised.value.health == "not_initialised"
        assert not world.log.path.exists()

    def test_a_reason_that_does_not_fit_is_refused_and_nothing_is_written(self, tmp_path):
        world = make_world(tmp_path)
        with pytest.raises(ConsentFormatError):
            world.block(reason=ReasonCode.WRITTEN_CONSENT)
        assert world.line_texts() == []

    def test_an_evidence_reference_with_odd_characters_is_refused(self, tmp_path):
        world = make_world(tmp_path)
        for bad in ("a\nb", "x" * 81, "<b>", "name=Ana"):
            with pytest.raises(ConsentFormatError):
                world.block(evidence_ref=bad)
        assert world.line_texts() == []

    def test_the_same_registry_over_the_same_files_is_read_again_after_a_restart(self, tmp_path):
        first = make_world(tmp_path)
        first.block()
        again = make_world(tmp_path, store=first.store, audit=first.audit, initialise=False)
        assert again.registry.snapshot().blocks(contact())

    def test_one_clients_log_is_not_read_as_anothers(self, tmp_path):
        a = make_world(tmp_path, client_id=CLIENT)
        a.block()
        b = make_world(tmp_path, client_id=OTHER)
        assert not b.registry.snapshot().blocks(contact())  # its own, empty registry
        assert a.registry.snapshot().blocks(contact())


# ---- reading ------------------------------------------------------------------------------------


class TestReading:
    def test_the_report_counts_without_showing_anything(self, tmp_path):
        world = make_world(tmp_path)
        world.block(contact(PHONE, EMAIL))
        report = world.registry.report()
        assert (report.health, report.events, report.blocked, report.releases) == (
            Health.OK,
            2,
            2,
            0,
        )
        assert report.reason is None and not report.torn_tail and not report.anchor_behind
        text = repr(report)
        assert PHONE_DIGITS not in text and EMAIL not in text

    def test_the_history_of_a_contact_is_its_own_events_in_order(self, tmp_path):
        world = make_world(tmp_path)
        world.block(contact(PHONE))
        world.block(contact(SECOND_PHONE))
        assert [e.seq for e in world.registry.history(contact(PHONE))] == [1]
        assert [e.seq for e in world.registry.history(contact(SECOND_PHONE))] == [2]
        assert world.registry.history(contact("+216 99 555 666")) == ()

    def test_a_snapshot_shows_nothing_when_printed(self, tmp_path):
        world = make_world(tmp_path)
        world.block()
        text = repr(world.registry.snapshot()) + repr(world.registry)
        assert PHONE_DIGITS not in text and "hmac" not in text.lower()


# ---- the refusals a source reports ----------------------------------------------------------------


class TestRecordingASourcesRefusals:
    MAPPING = StatusMapping.from_config({"lead": "new"})

    def source(self):
        made = FakeProspectSource(
            CLIENT, capabilities=default_capabilities(), status_mapping=self.MAPPING
        )

        def add(pid, phone, status, refuses):
            made.add_record(
                make_prospect(
                    prospect_id=ProspectId(pid),
                    display_name="Ana Test",
                    contact=ContactInfo(phone=phone, email=EMAIL if pid == "1" else None),
                ),
                raw_status=status,
                do_not_contact=refuses,
            )

        add("1", PHONE, None, True)  # left out, refuses: must be recorded
        add("2", SECOND_PHONE, "lead", False)  # kept, no refusal: nothing to record
        add("3", "+216 99 555 666", "mystery", False)  # left out, no refusal: nothing to record
        made.list_prospects()
        return made

    def test_a_refusal_on_a_record_that_is_left_out_is_recorded(self, tmp_path):
        world = make_world(tmp_path)
        source = self.source()
        assert record_refusals(world.registry, source.refused_contacts()) == 2  # phone and address
        snapshot = world.registry.snapshot()
        assert snapshot.blocks(contact(PHONE)) and snapshot.blocks(contact(None, EMAIL))
        assert not snapshot.blocks(contact(SECOND_PHONE))
        assert not snapshot.blocks(contact("+216 99 555 666"))
        assert {e.origin for e in world.registry.events()} == {Origin.SOURCE}
        assert {e.reason for e in world.registry.events()} == {ReasonCode.SOURCE_SIGNAL}

    def test_the_refusal_outlives_the_sources_own_flag(self, tmp_path):
        world = make_world(tmp_path)
        source = self.source()
        record_refusals(world.registry, source.refused_contacts())
        source.clear_refusals()  # the CRM user clears the flag: nothing is lifted here
        source.list_prospects()
        assert source.refused_contacts() == ()
        assert world.registry.snapshot().blocks(contact(PHONE))

    def test_recording_the_same_refusals_again_adds_nothing(self, tmp_path):
        world = make_world(tmp_path)
        source = self.source()
        assert record_refusals(world.registry, source.refused_contacts()) == 2
        assert record_refusals(world.registry, source.refused_contacts()) == 0
        assert len(world.registry.events()) == 2

    def test_only_a_phone_and_an_address_reach_the_registry(self):
        seen = []

        class Spy:
            def record_opt_out(self, who, **kwargs):
                seen.append((who, kwargs))
                return 0

            def snapshot(self):
                raise AssertionError

        rich = ContactInfo(phone=PHONE, email=EMAIL, preferred_channel=Channel.WHATSAPP)
        record_refusals(Spy(), [rich])  # type: ignore[arg-type]
        ((who, kwargs),) = seen
        assert who == ContactInfo(phone=PHONE, email=EMAIL) and who.preferred_channel is None
        assert kwargs["origin"] is Origin.SOURCE

    def test_an_unhealthy_registry_is_reported_to_the_caller(self, tmp_path):
        world = make_world(tmp_path, initialise=False)
        with pytest.raises(ConsentUnavailableError):
            record_refusals(world.registry, self.source().refused_contacts())

    def test_nothing_to_record_is_zero(self, tmp_path):
        assert record_refusals(make_world(tmp_path).registry, []) == 0


class TestWhereSecretsLive:
    def test_a_store_that_cannot_be_read_blocks_everyone(self, tmp_path):
        class Broken(InMemorySecretStore):
            def get(self, scope, key):
                raise SecretStoreCorruptError("unreadable")

        world = make_world(tmp_path, store=Broken(), initialise=False)
        snapshot = world.registry.snapshot()
        assert snapshot.health is Health.UNAVAILABLE and snapshot.blocks(contact())
        assert snapshot.reason == "store_unavailable"
