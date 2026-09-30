"""AdminAuthService: login, sessions, and the anti-brute-force guard."""

from datetime import UTC, datetime, timedelta

import pytest

from sales_app.admin_auth import (
    AdminRole,
    AdminUser,
    InMemoryAdminSessionStore,
    InMemoryAdminUserStore,
    InvalidCredentialsError,
    TooManyAttemptsError,
)
from sales_app.admin_auth.passwords import hash_password
from sales_app.admin_auth.service import AdminAuthService
from sales_integrations.fakes import FakeClock

NOW = datetime(2026, 5, 1, 9, 30, tzinfo=UTC)
PASSWORD = "correct-horse-battery-staple"


def make_service(*, ttl_hours=1, max_attempts=5, window_seconds=900.0, clock=None):
    users = InMemoryAdminUserStore()
    sessions = InMemoryAdminSessionStore()
    clock = clock or FakeClock(NOW)
    users.create(
        AdminUser(
            id="id-alice",
            username="alice",
            password_hash=hash_password(PASSWORD),
            role=AdminRole.EDITOR,
            client_ids=frozenset({"alpha"}),
            created_at=NOW,
        )
    )
    service = AdminAuthService(
        users,
        sessions,
        clock,
        session_ttl=timedelta(hours=ttl_hours),
        max_login_attempts=max_attempts,
        login_window_seconds=window_seconds,
    )
    return service, users, sessions, clock


class TestLogin:
    def test_correct_credentials_return_a_session_and_a_token(self):
        service, *_ = make_service()
        result = service.login("alice", PASSWORD)
        assert result.user.username == "alice"
        assert len(result.token) > 20
        assert result.session.admin_id == "id-alice"

    def test_username_is_matched_case_and_whitespace_insensitively(self):
        service, *_ = make_service()
        assert service.login("  Alice ", PASSWORD).user.username == "alice"

    def test_the_raw_token_is_never_stored_only_its_hash(self):
        service, _users, sessions, _clock = make_service()
        result = service.login("alice", PASSWORD)
        stored = sessions.get(result.session.token_hash)
        assert stored is not None
        assert result.token != result.session.token_hash
        assert result.token not in _dump(sessions)

    def test_wrong_password_is_rejected(self):
        service, *_ = make_service()
        with pytest.raises(InvalidCredentialsError):
            service.login("alice", "wrong password")

    def test_unknown_username_is_rejected_the_same_way_as_a_wrong_password(self):
        service, *_ = make_service()
        with pytest.raises(InvalidCredentialsError):
            service.login("ghost", PASSWORD)

    def test_a_deactivated_account_cannot_log_in(self):
        service, users, *_ = make_service()
        from dataclasses import replace

        users.update(replace(users.get("id-alice"), active=False))
        with pytest.raises(InvalidCredentialsError):
            service.login("alice", PASSWORD)

    def test_a_successful_login_resets_the_failure_counter(self):
        service, *_ = make_service(max_attempts=2)
        with pytest.raises(InvalidCredentialsError):
            service.login("alice", "wrong")
        service.login("alice", PASSWORD)  # succeeds, resets the counter
        with pytest.raises(InvalidCredentialsError):
            service.login("alice", "wrong")  # only the first of a fresh window


class TestRateLimiting:
    def test_too_many_failures_block_further_attempts_even_with_the_right_password(self):
        service, *_ = make_service(max_attempts=3)
        for _ in range(3):
            with pytest.raises(InvalidCredentialsError):
                service.login("alice", "wrong")
        with pytest.raises(TooManyAttemptsError):
            service.login("alice", PASSWORD)

    def test_the_lock_lifts_once_the_window_passes(self):
        clock = FakeClock(NOW)
        service, *_ = make_service(max_attempts=2, window_seconds=60, clock=clock)
        for _ in range(2):
            with pytest.raises(InvalidCredentialsError):
                service.login("alice", "wrong")
        with pytest.raises(TooManyAttemptsError):
            service.login("alice", PASSWORD)
        clock.advance(timedelta(seconds=61))
        assert service.login("alice", PASSWORD).user.username == "alice"

    def test_the_limit_is_per_username(self):
        service, users, _sessions, _clock = make_service(max_attempts=1)
        users.create(
            AdminUser(
                id="id-bob",
                username="bob",
                password_hash=hash_password(PASSWORD),
                role=AdminRole.VIEWER,
                client_ids=frozenset({"alpha"}),
                created_at=NOW,
            )
        )
        with pytest.raises(InvalidCredentialsError):
            service.login("alice", "wrong")
        assert service.login("bob", PASSWORD).user.username == "bob"


class TestAuthenticate:
    def test_a_fresh_token_resolves_to_its_user(self):
        service, *_ = make_service()
        token = service.login("alice", PASSWORD).token
        assert service.authenticate(token).username == "alice"

    def test_an_unknown_token_resolves_to_none(self):
        service, *_ = make_service()
        assert service.authenticate("not-a-real-token") is None

    def test_an_expired_session_resolves_to_none_and_is_removed(self):
        clock = FakeClock(NOW)
        service, _users, sessions, _clock = make_service(ttl_hours=1, clock=clock)
        token = service.login("alice", PASSWORD).token
        clock.advance(timedelta(hours=1, seconds=1))
        assert service.authenticate(token) is None
        assert len(sessions.all()) == 0  # lazily cleaned up

    def test_a_session_for_a_deactivated_account_stops_working(self):
        from dataclasses import replace

        service, users, *_ = make_service()
        token = service.login("alice", PASSWORD).token
        users.update(replace(users.get("id-alice"), active=False))
        assert service.authenticate(token) is None


class TestLogout:
    def test_logout_invalidates_the_token(self):
        service, *_ = make_service()
        token = service.login("alice", PASSWORD).token
        service.logout(token)
        assert service.authenticate(token) is None

    def test_logging_out_an_unknown_token_does_not_raise(self):
        service, *_ = make_service()
        service.logout("never-issued")


def _dump(sessions: InMemoryAdminSessionStore) -> str:
    return " ".join(f"{s.token_hash}:{s.admin_id}" for s in sessions.all())
