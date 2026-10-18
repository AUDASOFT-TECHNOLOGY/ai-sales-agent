"""The Google OAuth client, against a fake HTTP transport (no network, no real Google).

What is proved: the consent URL parameters (PKCE S256, offline, forced consent, the mail scope),
the code exchange, the refresh, the revocation, the account address, the typed errors, and that no
secret reaches a `repr`, an error message or a log. What is not: that Google accepts any of it.
"""

import base64
import hashlib
import logging
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from sales_integrations.google_oauth import (
    DEFAULT_SCOPES,
    MAIL_SCOPE,
    GoogleAccessDeniedError,
    GoogleOAuthClient,
    GoogleProtocolError,
    GoogleReauthRequiredError,
    GoogleTransientError,
    code_challenge,
    new_code_verifier,
)

CLIENT_ID = "app-id.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-SECRET-client-secret"
REDIRECT = "https://sales.example.org/client/v1/channel/google/callback"
CODE = "4/0AQ-SECRET-code"
ACCESS = "ya29.SECRET-access"
REFRESH = "1//SECRET-refresh"
VERIFIER = "v" * 64
ALL_SECRETS = (CLIENT_SECRET, CODE, ACCESS, REFRESH, VERIFIER)


class Recorder:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def form(self, index=0):
        return {k: v[0] for k, v in parse_qs(self.requests[index].content.decode()).items()}


def make(*responses):
    recorder = Recorder(*responses)
    http = httpx.Client(transport=httpx.MockTransport(recorder))
    client = GoogleOAuthClient(
        client_id=CLIENT_ID, client_secret=CLIENT_SECRET, redirect_uri=REDIRECT, http_client=http
    )
    return client, recorder


def json_response(status, body):
    return httpx.Response(status, json=body)


def assert_clean(*things):
    for thing in things:
        for secret in ALL_SECRETS:
            assert secret not in str(thing)
            assert secret not in repr(thing)


class TestAuthorizationUrl:
    def test_it_carries_pkce_offline_and_forced_consent(self):
        client, _ = make()
        url = client.authorization_url(
            state="STATE", code_challenge=code_challenge(VERIFIER), login_hint="a@gmail.com"
        )
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == (
            "https://accounts.google.com/o/oauth2/v2/auth"
        )
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert q["client_id"] == CLIENT_ID
        assert q["redirect_uri"] == REDIRECT
        assert q["response_type"] == "code"
        assert q["state"] == "STATE"
        assert q["access_type"] == "offline"
        assert q["prompt"] == "consent"
        assert q["code_challenge_method"] == "S256"
        assert q["login_hint"] == "a@gmail.com"
        assert q["scope"].split() == list(DEFAULT_SCOPES)
        assert MAIL_SCOPE in q["scope"].split()
        assert CLIENT_SECRET not in url

    def test_the_challenge_is_the_s256_of_the_verifier(self):
        digest = hashlib.sha256(VERIFIER.encode()).digest()
        assert code_challenge(VERIFIER) == base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
        assert 43 <= len(new_code_verifier()) <= 128
        assert new_code_verifier() != new_code_verifier()

    def test_the_scope_can_be_replaced(self):
        http = httpx.Client(transport=httpx.MockTransport(Recorder()))
        client = GoogleOAuthClient(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            redirect_uri=REDIRECT,
            scopes=("openid", "email"),
            http_client=http,
        )
        q = parse_qs(urlsplit(client.authorization_url(state="s", code_challenge="c")).query)
        assert q["scope"] == ["openid email"]

    def test_missing_configuration_is_refused(self):
        with pytest.raises(ValueError):
            GoogleOAuthClient(client_id="", client_secret="x", redirect_uri="y")


class TestExchange:
    def test_a_code_becomes_tokens(self):
        client, rec = make(
            json_response(
                200,
                {
                    "access_token": ACCESS,
                    "refresh_token": REFRESH,
                    "expires_in": 3599,
                    "scope": "s",
                },
            )
        )
        grant = client.exchange_code(code=CODE, code_verifier=VERIFIER)
        assert (grant.access_token, grant.refresh_token, grant.expires_in) == (
            ACCESS,
            REFRESH,
            3599,
        )
        form = rec.form()
        assert form["grant_type"] == "authorization_code"
        assert form["code"] == CODE
        assert form["code_verifier"] == VERIFIER
        assert form["redirect_uri"] == REDIRECT
        assert form["client_secret"] == CLIENT_SECRET
        assert str(rec.requests[0].url) == "https://oauth2.googleapis.com/token"
        assert rec.requests[0].url.query == b""  # nothing secret in the URL
        assert_clean(grant)

    def test_no_refresh_token_is_none_not_an_error(self):
        client, _ = make(json_response(200, {"access_token": ACCESS, "expires_in": 10}))
        assert client.exchange_code(code=CODE, code_verifier=VERIFIER).refresh_token is None

    @pytest.mark.parametrize(
        "body",
        [
            {},
            {"access_token": ""},
            {"access_token": ACCESS},
            {"access_token": ACCESS, "expires_in": "x"},
        ],
    )
    def test_a_malformed_answer_fails_closed(self, body):
        client, _ = make(json_response(200, body))
        with pytest.raises(GoogleProtocolError) as caught:
            client.exchange_code(code=CODE, code_verifier=VERIFIER)
        assert_clean(caught.value)

    def test_a_non_json_answer_fails_closed(self):
        client, _ = make(httpx.Response(200, text="<html>"))
        with pytest.raises(GoogleProtocolError):
            client.exchange_code(code=CODE, code_verifier=VERIFIER)


class TestRefresh:
    def test_it_returns_a_new_access_token(self):
        client, rec = make(json_response(200, {"access_token": ACCESS, "expires_in": 3599}))
        grant = client.refresh(REFRESH)
        assert grant.access_token == ACCESS and grant.expires_in == 3599
        form = rec.form()
        assert form["grant_type"] == "refresh_token" and form["refresh_token"] == REFRESH
        assert_clean(grant)

    def test_invalid_grant_means_reauthorisation(self):
        said = {"error": "invalid_grant", "error_description": f"Token {REFRESH} revoked"}
        client, _ = make(json_response(400, said))
        with pytest.raises(GoogleReauthRequiredError) as caught:
            client.refresh(REFRESH)
        assert_clean(caught.value)
        assert "revoked" not in str(caught.value)

    def test_an_empty_refresh_token_means_reauthorisation_without_a_request(self):
        client, rec = make()
        with pytest.raises(GoogleReauthRequiredError):
            client.refresh("")
        assert rec.requests == []

    @pytest.mark.parametrize("status", [500, 502, 503, 429])
    def test_a_server_error_or_rate_limit_is_transient(self, status):
        client, _ = make(json_response(status, {"error": "backend_error"}))
        with pytest.raises(GoogleTransientError):
            client.refresh(REFRESH)

    @pytest.mark.parametrize("failure", [httpx.ConnectError(REFRESH), httpx.ReadTimeout(ACCESS)])
    def test_a_network_failure_is_transient_and_says_nothing(self, failure):
        client, _ = make(failure)
        with pytest.raises(GoogleTransientError) as caught:
            client.refresh(REFRESH)
        assert_clean(caught.value)
        assert caught.value.__cause__ is None and caught.value.__suppress_context__

    def test_access_denied_is_typed(self):
        client, _ = make(json_response(400, {"error": "access_denied"}))
        with pytest.raises(GoogleAccessDeniedError):
            client.exchange_code(code=CODE, code_verifier=VERIFIER)

    def test_another_refusal_is_a_protocol_error(self):
        client, _ = make(json_response(400, {"error": "invalid_client"}))
        with pytest.raises(GoogleProtocolError):
            client.refresh(REFRESH)


class TestRevokeAndAddress:
    def test_revoke_posts_the_token_in_the_body(self):
        client, rec = make(httpx.Response(200, json={}))
        client.revoke(REFRESH)
        assert rec.form() == {"token": REFRESH}
        assert str(rec.requests[0].url) == "https://oauth2.googleapis.com/revoke"

    def test_an_unknown_token_counts_as_revoked(self):
        client, _ = make(json_response(400, {"error": "invalid_token"}))
        client.revoke(REFRESH)

    def test_a_revoke_failure_is_typed(self):
        client, _ = make(json_response(503, {}))
        with pytest.raises(GoogleTransientError):
            client.revoke(REFRESH)

    def test_the_address_is_read_with_a_bearer_header(self):
        client, rec = make(json_response(200, {"email": "Sales@Gmail.com", "email_verified": True}))
        assert client.account_address(ACCESS) == "sales@gmail.com"
        assert rec.requests[0].headers["authorization"] == f"Bearer {ACCESS}"
        assert ACCESS not in str(rec.requests[0].url)

    @pytest.mark.parametrize(
        "body", [{}, {"email": "nope"}, {"email": "a@b.c", "email_verified": False}]
    )
    def test_a_bad_account_answer_fails_closed(self, body):
        client, _ = make(json_response(200, body))
        with pytest.raises(GoogleProtocolError):
            client.account_address(ACCESS)

    def test_a_rejected_access_token_is_access_denied(self):
        client, _ = make(json_response(401, {"error": "invalid_token"}))
        with pytest.raises(GoogleAccessDeniedError):
            client.account_address(ACCESS)


class TestNoLeak:
    def test_the_client_repr_hides_its_secret(self):
        client, _ = make()
        assert_clean(client)
        assert CLIENT_ID not in repr(client)

    def test_nothing_is_logged(self, caplog):
        caplog.set_level(logging.DEBUG)
        client, _ = make(
            json_response(200, {"access_token": ACCESS, "refresh_token": REFRESH, "expires_in": 5}),
            json_response(400, {"error": "invalid_grant"}),
        )
        client.exchange_code(code=CODE, code_verifier=VERIFIER)
        with pytest.raises(GoogleReauthRequiredError):
            client.refresh(REFRESH)
        for record in caplog.records:
            assert_clean(record.getMessage())
