"""`/client/v1`: a client logs in, reads its own profile, changes its password, logs out.

Covers: login (right, wrong, unknown, inactive), the session cookie, protected routes without and
with a session, isolation between clients (the id comes from the session, never from the request),
the Admin/Client separation in both directions, logout, password change (and that the new one is the
one that works afterwards) and that no response ever carries a password, a hash or a Meta secret.
"""

from datetime import timedelta

import pytest
from app_support import InlineExecutor, env_for, fake_builders, make_settings, write_client
from fastapi.testclient import TestClient
from test_admin_clients_api import NOW, PASSWORD, owner

from sales_app.admin_auth import InMemoryAdminSessionStore, InMemoryAdminUserStore
from sales_app.admin_auth.passwords import hash_password, verify_password
from sales_app.api import create_app
from sales_app.bootstrap import build_application
from sales_app.client_profiles import ClientProfile, InMemoryClientProfileStore
from sales_app.config_store import InMemoryConfigStore
from sales_integrations.fakes import FakeClock

LOGIN = "/client/v1/auth/login"
LOGOUT = "/client/v1/auth/logout"
ME = "/client/v1/auth/me"
PROFILE = "/client/v1/profile"
PASSWORD_URL = "/client/v1/profile/password"

ALPHA_PASSWORD = "alpha-secret-123"
BETA_PASSWORD = "beta-secret-456"
NAGA_PASSWORD = "naga-secret-789"


def give_profile(fx, client_id, login, password, *, whatsapp="+216 20 000 001"):
    fx.application.client_profiles.create(
        ClientProfile(
            client_id=client_id,
            login=login,
            password_hash=hash_password(password),
            whatsapp_number=whatsapp,
            created_at=NOW,
        )
    )


class Fixture:
    """The running app, with the Admin and Client session stores kept in reach to inspect them."""

    def __init__(self, tmp_path):
        self.tmp_path = tmp_path
        self.clock = FakeClock(NOW)
        self.users = InMemoryAdminUserStore()
        self.sessions = InMemoryAdminSessionStore()
        self.client_sessions = InMemoryAdminSessionStore()
        builders, _made = fake_builders(self.clock)
        self.application = build_application(
            make_settings(tmp_path),
            env=env_for("alpha", "beta"),
            builders=builders,
            clock=self.clock,
            executor=InlineExecutor(),
            admin_user_store=self.users,
            admin_session_store=self.sessions,
            client_profile_store=InMemoryClientProfileStore(),
            client_session_store=self.client_sessions,
            config_store=InMemoryConfigStore(),  # never the real ./config directory
        )


@pytest.fixture
def fx(tmp_path):
    # "naga" is present on disk but disabled (Inactive); alpha and beta are active.
    write_client(tmp_path, "alpha")
    write_client(tmp_path, "beta")
    write_client(tmp_path, "naga", enabled=False, display_name="Naga")
    fixture = Fixture(tmp_path)
    fixture.users.create(owner())
    give_profile(fixture, "alpha", "alpha.login", ALPHA_PASSWORD)
    give_profile(fixture, "beta", "beta.login", BETA_PASSWORD, whatsapp="+216 20 000 002")
    give_profile(fixture, "naga", "naga.login", NAGA_PASSWORD, whatsapp="+216 20 000 003")
    return fixture


def browser(fx) -> TestClient:
    """A fresh browser (own cookie jar) on the same running app."""
    return TestClient(
        create_app(fx.application, run_scheduler=False), base_url="https://testserver"
    )


def login(client, name="alpha.login", password=ALPHA_PASSWORD):
    return client.post(LOGIN, json={"login": name, "password": password})


@pytest.fixture
def alpha(fx):
    client = browser(fx)
    assert login(client).status_code == 200
    return client


@pytest.fixture
def beta(fx):
    client = browser(fx)
    assert login(client, "beta.login", BETA_PASSWORD).status_code == 200
    return client


class TestLogin:
    def test_correct_credentials_open_a_session_and_return_the_profile(self, fx):
        client = browser(fx)
        response = login(client)
        assert response.status_code == 200
        assert response.json() == {
            "name": "Alpha",
            "login": "alpha.login",
            "whatsapp_number": "+216 20 000 001",
            "status": "active",
            "must_change_password": True,
            "whatsapp_reconnect_required": False,
        }
        cookie = response.headers["set-cookie"].lower()
        assert "revansa_client_session=" in cookie
        assert "httponly" in cookie and "secure" in cookie and "path=/client/v1" in cookie

    def test_the_login_is_case_insensitive_like_at_creation(self, fx):
        assert login(browser(fx), "  ALPHA.Login ").status_code == 200

    def test_a_session_is_stored_after_login_and_never_as_the_raw_token(self, fx):
        client = browser(fx)
        login(client)
        stored = fx.client_sessions.all()
        assert [s.admin_id for s in stored] == ["alpha"]
        raw_token = client.cookies.get("revansa_client_session")
        assert raw_token and raw_token != stored[0].token_hash

    def test_a_wrong_password_is_refused_without_a_session(self, fx):
        client = browser(fx)
        response = login(client, password="nope-nope-nope")
        assert response.status_code == 401
        assert "revansa_client_session" not in response.headers.get("set-cookie", "")
        assert client.get(ME).status_code == 401

    def test_an_unknown_login_answers_exactly_like_a_wrong_password(self, fx):
        wrong = login(browser(fx), password="nope-nope-nope")
        unknown = login(browser(fx), "ghost.login", "nope-nope-nope")
        assert unknown.status_code == wrong.status_code == 401
        assert unknown.json() == wrong.json()

    def test_an_inactive_client_cannot_log_in(self, fx):
        client = browser(fx)
        response = login(client, "naga.login", NAGA_PASSWORD)
        assert response.status_code == 403
        assert "revansa_client_session" not in response.headers.get("set-cookie", "")
        assert client.get(ME).status_code == 401
        assert fx.client_sessions.all() == ()

    def test_an_inactive_client_with_a_wrong_password_looks_like_any_wrong_password(self, fx):
        response = login(browser(fx), "naga.login", "nope-nope-nope")
        assert (
            response.status_code == 401
        )  # "inactive" is only said to someone who knows the password

    def test_repeated_failures_are_rate_limited(self, fx):
        client = browser(fx)
        for _ in range(5):
            assert login(client, password="nope-nope-nope").status_code == 401
        assert login(client).status_code == 429  # even the right password, until the window ends


class TestProtectedRoutes:
    @pytest.mark.parametrize("path", [ME, PROFILE])
    def test_a_route_without_a_session_is_refused(self, fx, path):
        assert browser(fx).get(path).status_code == 401

    def test_changing_the_password_without_a_session_is_refused(self, fx):
        body = {"current_password": "x", "new_password": "new-pass-123", "confirm_password": "x"}
        assert browser(fx).post(PASSWORD_URL, json=body).status_code == 401

    def test_logout_without_a_session_is_refused(self, fx):
        assert browser(fx).post(LOGOUT).status_code == 401

    def test_a_forged_or_unknown_token_is_refused(self, fx):
        client = browser(fx)
        client.cookies.set("revansa_client_session", "not-a-real-token", path="/client/v1")
        assert client.get(PROFILE).status_code == 401

    def test_an_expired_session_is_refused(self, fx, alpha):
        fx.clock.advance(timedelta(hours=25))
        assert alpha.get(PROFILE).status_code == 401

    def test_an_authenticated_client_reaches_its_own_space(self, alpha):
        assert alpha.get(ME).status_code == 200
        profile = alpha.get(PROFILE)
        assert profile.status_code == 200
        assert profile.json()["login"] == "alpha.login"

    def test_deactivating_a_client_ends_its_session_at_once(self, fx, alpha):
        path = fx.tmp_path / "alpha" / "client.yaml"
        path.write_text(path.read_text().replace("enabled: true", "enabled: false"))
        assert alpha.get(PROFILE).status_code == 401


class TestIsolation:
    def test_each_client_only_ever_sees_itself(self, alpha, beta):
        assert alpha.get(PROFILE).json()["login"] == "alpha.login"
        assert beta.get(PROFILE).json()["login"] == "beta.login"
        assert beta.get(PROFILE).json()["whatsapp_number"] == "+216 20 000 002"

    @pytest.mark.parametrize(
        "extra",
        [
            {"params": {"client_id": "beta"}},
            {"headers": {"X-Client-Id": "beta"}},
            {"params": {"client": "beta", "login": "beta.login"}},
        ],
    )
    def test_a_client_id_supplied_by_the_browser_is_ignored(self, alpha, extra):
        assert alpha.get(PROFILE, **extra).json()["login"] == "alpha.login"

    def test_there_is_no_route_to_reach_another_client_by_id(self, alpha):
        for path in ("/client/v1/clients/beta", "/client/v1/profile/beta", "/client/v1/beta"):
            assert alpha.get(path).status_code == 404

    def test_a_password_change_never_touches_another_client(self, fx, alpha, beta):
        before = fx.application.client_profiles.get("beta").password_hash
        body = {
            "current_password": ALPHA_PASSWORD,
            "new_password": "alpha-new-pass",
            "confirm_password": "alpha-new-pass",
        }
        assert alpha.post(PASSWORD_URL, json=body).status_code == 200
        assert fx.application.client_profiles.get("beta").password_hash == before
        assert beta.get(PROFILE).status_code == 200  # beta's session is untouched

    def test_a_password_change_uses_the_session_client_even_if_another_is_named(self, fx, alpha):
        body = {
            "client_id": "beta",
            "current_password": BETA_PASSWORD,  # beta's real password, sent by alpha
            "new_password": "hijacked-pass",
            "confirm_password": "hijacked-pass",
        }
        assert alpha.post(PASSWORD_URL, json=body).status_code == 400
        assert verify_password(
            BETA_PASSWORD, fx.application.client_profiles.get("beta").password_hash
        )


class TestAdminSeparation:
    ADMIN_PATHS = (
        "/admin/v1/auth/me",
        "/admin/v1/clients",
        "/admin/v1/clients/alpha",
        "/admin/v1/agents",
        "/admin/v1/settings/llm",
    )

    @pytest.mark.parametrize("path", ADMIN_PATHS)
    def test_a_client_session_never_opens_the_admin(self, alpha, path):
        assert alpha.get(path).status_code == 401

    def test_a_client_session_cannot_write_to_the_admin(self, alpha):
        assert alpha.patch("/admin/v1/clients/alpha", json={"status": "active"}).status_code == 401
        assert alpha.post("/admin/v1/clients", json={}).status_code == 401

    def test_an_admin_session_never_opens_the_client_space(self, fx):
        admin = browser(fx)
        assert admin.post(
            "/admin/v1/auth/login", json={"username": "root", "password": PASSWORD}
        ).is_success
        assert admin.get("/admin/v1/clients").status_code == 200
        assert admin.get(PROFILE).status_code == 401
        assert admin.get(ME).status_code == 401

    def test_the_two_sessions_do_not_share_a_store_or_a_cookie(self, fx, alpha):
        assert fx.sessions.all() == ()  # the admin store saw nothing of the client login
        assert len(fx.client_sessions.all()) == 1


class TestLogout:
    def test_logout_ends_the_session(self, fx, alpha):
        response = alpha.post(LOGOUT)
        assert response.status_code == 200
        assert fx.client_sessions.all() == ()
        assert alpha.get(PROFILE).status_code == 401
        assert alpha.get(ME).status_code == 401

    def test_the_old_token_stays_dead_even_if_replayed(self, fx, alpha):
        token = alpha.cookies.get("revansa_client_session")
        alpha.post(LOGOUT)
        replay = browser(fx)
        replay.cookies.set("revansa_client_session", token, path="/client/v1")
        assert replay.get(PROFILE).status_code == 401

    def test_logout_only_ends_this_clients_session(self, alpha, beta):
        alpha.post(LOGOUT)
        assert beta.get(PROFILE).status_code == 200


def change(client, current=ALPHA_PASSWORD, new="alpha-new-pass", confirm=None):
    return client.post(
        PASSWORD_URL,
        json={
            "current_password": current,
            "new_password": new,
            "confirm_password": new if confirm is None else confirm,
        },
    )


class TestChangePassword:
    def test_a_correct_change_succeeds_and_clears_the_temporary_flag(self, fx, alpha):
        response = change(alpha)
        assert response.status_code == 200
        assert response.json()["must_change_password"] is False
        stored = fx.application.client_profiles.get("alpha")
        assert stored.must_change_password is False
        assert stored.password_hash.startswith("$2")  # bcrypt, never the plain text
        assert verify_password("alpha-new-pass", stored.password_hash)

    def test_a_wrong_current_password_is_refused_and_nothing_changes(self, fx, alpha):
        before = fx.application.client_profiles.get("alpha").password_hash
        response = change(alpha, current="not-my-password")
        assert response.status_code == 400
        assert fx.application.client_profiles.get("alpha").password_hash == before
        assert alpha.get(PROFILE).status_code == 200  # a failed attempt does not log out

    def test_a_different_confirmation_is_refused_and_nothing_changes(self, fx, alpha):
        before = fx.application.client_profiles.get("alpha").password_hash
        assert change(alpha, confirm="something-else").status_code == 400
        assert fx.application.client_profiles.get("alpha").password_hash == before

    def test_a_too_short_new_password_is_refused(self, fx, alpha):
        before = fx.application.client_profiles.get("alpha").password_hash
        assert change(alpha, new="short").status_code == 422  # same 8-character rule as creation
        assert fx.application.client_profiles.get("alpha").password_hash == before

    def test_the_new_password_works_and_the_old_one_no_longer_does(self, fx, alpha):
        assert change(alpha).status_code == 200
        assert login(browser(fx), password="alpha-new-pass").status_code == 200
        assert login(browser(fx), password=ALPHA_PASSWORD).status_code == 401

    def test_the_other_sessions_end_but_the_current_browser_stays_logged_in(self, fx, alpha):
        elsewhere = browser(fx)
        assert login(elsewhere).status_code == 200
        assert change(alpha).status_code == 200
        assert alpha.get(PROFILE).status_code == 200
        assert elsewhere.get(PROFILE).status_code == 401
        assert len(fx.client_sessions.all()) == 1

    def test_repeated_wrong_current_passwords_are_rate_limited(self, alpha):
        for _ in range(5):
            assert change(alpha, current="not-my-password").status_code == 400
        assert change(alpha).status_code == 429


class TestNoSecretsLeak:
    FORBIDDEN = ("hash", "$2b$", "token", "secret", "access", "phone_number_id", "verify")

    def test_the_profile_has_only_the_allowed_fields(self, alpha):
        body = alpha.get(PROFILE).json()
        assert set(body) == {
            "name",
            "login",
            "whatsapp_number",
            "status",
            "must_change_password",
            "whatsapp_reconnect_required",
        }

    def test_no_response_carries_a_password_a_hash_or_a_meta_secret(self, fx, alpha):
        responses = [
            login(browser(fx)),
            alpha.get(ME),
            alpha.get(PROFILE),
            change(alpha),
            alpha.get(PROFILE),
            alpha.post(LOGOUT),
        ]
        for response in responses:
            text = response.text.lower()
            for word in self.FORBIDDEN:
                assert word not in text, (response.request.url, word)
            for secret in (ALPHA_PASSWORD, "alpha-new-pass"):
                assert secret not in response.text
            stored_hash = fx.application.client_profiles.get("alpha").password_hash
            assert stored_hash not in response.text


def patch(client, **fields):
    return client.patch(PROFILE, json=fields)


class TestEditProfile:
    def test_the_name_is_changed_and_seen_everywhere(self, fx, alpha):
        response = patch(alpha, name="  Alpha Industries ")
        assert response.status_code == 200
        assert response.json()["name"] == "Alpha Industries"
        assert alpha.get(PROFILE).json()["name"] == "Alpha Industries"
        # one source of truth: the same versioned settings the Admin writes (it reaches the
        # running config at the next runtime refresh, exactly like an Admin rename)
        published = fx.application.config_store.current("alpha")
        assert published.document["display_name"] == "Alpha Industries"
        assert published.author == "client:alpha"

    def test_an_empty_name_is_refused(self, alpha):
        assert patch(alpha, name="   ").status_code == 400
        assert patch(alpha, name="").status_code == 422
        assert alpha.get(PROFILE).json()["name"] == "Alpha"

    def test_the_login_is_changed_and_normalised_and_the_session_survives(self, fx, alpha):
        response = patch(alpha, login="  New.Login ")
        assert response.status_code == 200
        assert response.json()["login"] == "new.login"
        assert fx.application.client_profiles.get("alpha").login == "new.login"
        assert alpha.get(PROFILE).status_code == 200
        assert login(browser(fx), "new.login").status_code == 200
        assert login(browser(fx), "alpha.login").status_code == 401  # the old one is gone

    def test_a_login_used_by_another_client_is_refused(self, fx, alpha):
        assert patch(alpha, login="BETA.login").status_code == 409
        assert fx.application.client_profiles.get("alpha").login == "alpha.login"

    def test_keeping_the_same_login_is_not_a_conflict(self, alpha):
        assert patch(alpha, login="Alpha.Login").status_code == 200

    def test_an_invalid_login_is_refused(self, alpha):
        assert patch(alpha, login="no spaces!").status_code == 400

    def test_the_whatsapp_number_is_changed_and_flagged_as_not_connected(self, fx, alpha):
        response = patch(alpha, whatsapp_number="  +216   99 697 335 ")
        assert response.status_code == 200
        assert response.json()["whatsapp_number"] == "+216 99 697 335"
        assert response.json()["whatsapp_reconnect_required"] is True
        assert fx.application.client_profiles.get("alpha").whatsapp_reconnect_required is True
        assert alpha.get(PROFILE).json()["whatsapp_reconnect_required"] is True
        # entering a number connects nothing: no Meta credential appears for this client
        assert fx.application.credentials.reveal("client:alpha", "whatsapp.phone_number_id") is None

    def test_the_same_number_written_differently_does_not_ask_for_a_reconnection(self, alpha):
        assert (
            patch(alpha, whatsapp_number="+21620000001").json()["whatsapp_reconnect_required"]
            is False
        )

    def test_an_invalid_whatsapp_number_is_refused(self, fx, alpha):
        assert patch(alpha, whatsapp_number="not a number").status_code == 400
        assert fx.application.client_profiles.get("alpha").whatsapp_number == "+216 20 000 001"

    def test_a_whatsapp_number_of_another_client_is_refused(self, alpha):
        assert patch(alpha, whatsapp_number="+216 20 000 002").status_code == 409

    def test_several_fields_are_saved_together(self, alpha):
        body = patch(
            alpha, name="Alpha Two", login="alpha.two", whatsapp_number="+33 6 12 34 56 78"
        )
        assert body.status_code == 200
        assert body.json()["name"] == "Alpha Two" and body.json()["login"] == "alpha.two"

    def test_a_field_that_is_not_sent_is_left_alone(self, alpha):
        patch(alpha, name="Alpha Two")
        body = alpha.get(PROFILE).json()
        assert body["login"] == "alpha.login" and body["whatsapp_number"] == "+216 20 000 001"

    def test_the_status_cannot_be_changed_by_the_client(self, fx, alpha):
        for status in ("inactive", "active", "disabled"):
            assert patch(alpha, status=status).status_code == 422
        text = (fx.tmp_path / "alpha" / "client.yaml").read_text()
        assert "enabled: true" in text
        assert alpha.get(PROFILE).json()["status"] == "active"

    @pytest.mark.parametrize(
        "field",
        ["client_id", "must_change_password", "password_hash", "access_token", "phone_number_id"],
    )
    def test_nothing_but_name_login_and_number_is_accepted(self, alpha, field):
        assert patch(alpha, **{field: "x"}).status_code == 422

    def test_editing_without_a_session_is_refused(self, fx):
        assert patch(browser(fx), name="Hacked").status_code == 401

    def test_a_client_only_ever_edits_its_own_profile(self, fx, alpha, beta):
        patch(alpha, name="Alpha Two", login="alpha.two", whatsapp_number="+33 6 12 34 56 78")
        other = fx.application.client_profiles.get("beta")
        assert (other.login, other.whatsapp_number) == ("beta.login", "+216 20 000 002")
        assert beta.get(PROFILE).json()["name"] == "Beta"

    def test_naming_another_client_in_the_request_is_refused(self, fx, alpha):
        assert patch(alpha, client_id="beta", name="Hijacked").status_code == 422
        assert alpha.patch("/client/v1/profile/beta", json={"name": "x"}).status_code in (404, 405)
        assert fx.application.config_store.current("beta") is None

    def test_a_client_session_cannot_edit_through_the_admin_api(self, alpha):
        assert alpha.patch("/admin/v1/clients/alpha", json={"display_name": "x"}).status_code == 401

    def test_edits_return_no_secret(self, alpha):
        text = patch(alpha, name="Alpha Two", whatsapp_number="+33 6 12 34 56 78").text.lower()
        for word in ("hash", "$2b$", "token", "secret", "phone_number_id"):
            assert word not in text
