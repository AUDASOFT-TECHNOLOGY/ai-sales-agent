"""A client's WhatsApp Business account, connected with Meta's Embedded Signup.

The client signs in to Meta in Meta's own window, picks its business, its WhatsApp Business account
and the phone number; Meta hands the browser a one-time code. Nothing technical is typed by the
client and no token ever reaches the browser: the code is exchanged here, on the server, with the
platform's Meta app, and the resulting business token is kept in the encrypted secret store under
that client alone.

What this module does, and every step is a real call to Meta:

- `start`: a signed, single-use, short-lived `state` bound to one client, with the public
  identifiers the browser needs (app id, configuration id);
- `complete`: exchange the code, ask Meta which account the token really grants (what the browser
  says about the account is only a hint that Meta confirms), check the number is the one the client
  declared, subscribe the app to the account's webhooks, register the number, then store;
- `disconnect`: unsubscribe the webhooks, withdraw the token's permissions, forget everything;
- templates: list / create / delete the account's message templates and choose the one that opens
  a conversation outside the 24-hour window;
- `handle`: what Meta tells us by webhook (delivery statuses, template approvals, account changes);
- `channel_for`: the sending channel of a client, bound to its windows and templates.

What is NOT proven here: that Meta accepts the app (App Review, Business Verification, Live mode),
that the account has a payment method, that the number can send. Those are Meta's, checked only by
a real account (see docs/whatsapp-channel.md).
"""

import hashlib
import hmac
import json
import logging
import re
import secrets as token_secrets
import threading
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sales_app.config.secrets import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_VERSION,
    WHATSAPP_APP_ID,
    WHATSAPP_APP_SECRET,
    WHATSAPP_CONFIG_ID,
    WHATSAPP_PHONE_NUMBER_ID,
    WHATSAPP_REAUTH_REQUIRED,
    WHATSAPP_VERIFY_TOKEN,
    WHATSAPP_WABA_ID,
)
from sales_app.secret_store import GLOBAL_SCOPE, SecretStore, SecretStoreError, client_scope
from sales_app.secret_store.errors import DuplicateSecretError
from sales_app.whatsapp_state import (
    APPROVED,
    DISABLED,
    FAILED,
    PAUSED,
    PENDING,
    DeliveryRecord,
    InMemoryWhatsAppState,
    TemplateRecord,
    WhatsAppState,
)
from sales_core.contracts import InboundMessage
from sales_core.ports import Clock
from sales_integrations.whatsapp import (
    ACCOUNT,
    DELIVERY,
    PHONE,
    TEMPLATE,
    GraphError,
    OpeningTemplate,
    SendPolicy,
    WhatsAppChannel,
    WhatsAppGraph,
    WhatsAppNotification,
)
from sales_integrations.whatsapp.channel import DEFAULT_API_VERSION

log = logging.getLogger(__name__)

NOT_CONNECTED = "not_connected"
CONNECTED = "connected"
REAUTH_REQUIRED = "reauth_required"
UNAVAILABLE = "unavailable"

STATE_TTL_SECONDS = 600
_ACTOR = "system:whatsapp-signup"
# Not catalogued (so the Admin credentials API cannot write or list them): written by this module.
WHATSAPP_REGISTRATION_PIN = "whatsapp.registration_pin"
WHATSAPP_FB_USER_ID = "whatsapp.fb_user_id"
WHATSAPP_CONNECTION = "whatsapp.connection"  # JSON: the account as Meta described it

TEMPLATE_NAME = re.compile(r"^[a-z0-9_]{1,512}$")
LANGUAGE = re.compile(r"^[a-z]{2,3}(_[A-Z]{2})?$")
CATEGORIES = ("MARKETING", "UTILITY")
_VARIABLE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
MAX_BODY = 1024
_STATUS_OF_EVENT = {
    "APPROVED": APPROVED,
    "REJECTED": "REJECTED",
    "PENDING": PENDING,
    "IN_APPEAL": PENDING,
    "PAUSED": PAUSED,
    "DISABLED": DISABLED,
    "FLAGGED": PAUSED,
    "PENDING_DELETION": DISABLED,
    "DELETED": DISABLED,
}
# What a refused template means for the next send (Meta's error code -> the status to hold).
_STATUS_OF_ERROR = {"132015": PAUSED, "132016": DISABLED, "132001": "MISSING"}
# Meta's `account_update` events that mean Revansa's access to the account is gone (not another
# partner's: `PARTNER_REMOVED` is about a partner of the account, not necessarily Revansa's app).
_ACCOUNT_REMOVED = frozenset({"PARTNER_APP_UNINSTALLED", "ACCOUNT_DELETED"})


class WhatsAppConnectionError(Exception):
    """A refusal with a fixed, client-safe `reason`; `cause` is one of the codes below."""

    def __init__(self, cause: str) -> None:
        super().__init__(cause)
        self.cause = cause


# The causes a completion can fail with (the browser is sent back with one of them).
INVALID = "invalid"  # the state is forged, expired, used already or another client's
UNAVAILABLE_CAUSE = "unavailable"  # the platform is not set up, or Meta cannot be reached now
DENIED = "denied"  # Meta did not grant what is needed
NUMBER_MISMATCH = "number_mismatch"  # the account's number is not the one the client declared
NUMBER_UNCLEAR = "number_unclear"  # several numbers, none of them chosen
IN_USE = "in_use"  # this number already belongs to another client
ERROR = "error"


@dataclass(frozen=True, slots=True)
class StartInfo:
    app_id: str
    config_id: str
    api_version: str
    state: str


@dataclass(frozen=True, slots=True)
class WhatsAppStatus:
    state: str  # not_connected | connected | reauth_required | unavailable
    display_number: str | None = None
    verified_name: str | None = None
    quality: str | None = None
    waba_id: str | None = None
    phone_number_id: str | None = None
    connected_at: str | None = None


@dataclass(frozen=True, slots=True)
class DisconnectReport:
    """What happened at Meta when a client disconnected. Locally everything is forgotten whatever
    the answer. For the Admin only."""

    steps: dict[str, str] = field(default_factory=dict)  # step -> "done" | "skipped" | "failed"


def _digits(value: str) -> str:
    return "".join(ch for ch in value if ch.isdigit())


def template_parameters(body: str) -> tuple[str, ...]:
    """The variables of a template body, in order of first appearance."""
    return tuple(dict.fromkeys(_VARIABLE.findall(body)))


def template_problem(
    name: str, language: str, category: str, body: str, examples: Sequence[str]
) -> str | None:
    """Why a template cannot be submitted, or None. Meta's own review comes after this."""
    if not TEMPLATE_NAME.match(name):
        return "the name uses only lower-case letters, digits and underscores"
    if not LANGUAGE.match(language):
        return "the language looks like fr, en or en_US"
    if category not in CATEGORIES:
        return "the category is MARKETING or UTILITY"
    if not body.strip() or len(body) > MAX_BODY:
        return f"the text is between 1 and {MAX_BODY} characters"
    names = template_parameters(body)
    if names and not all(n.isdigit() for n in names):
        return "variables are numbered {{1}}, {{2}}..."
    if names and list(names) != [str(i) for i in range(1, len(names) + 1)]:
        return "variables are numbered in order, starting at {{1}}"
    if len(examples) != len(names) or any(not e.strip() for e in examples):
        return "give one example value for each variable"
    if names and (body.strip().startswith("{{") or body.strip().endswith("}}")):
        return "the text cannot start or end with a variable"
    if "\n\n\n" in body or "\t" in body:
        return "the text has no tabs and no more than one empty line in a row"
    return None


class WhatsAppService:
    """Everything the application does with WhatsApp beyond the plain sending of a message."""

    def __init__(
        self,
        *,
        secrets: SecretStore,
        clock: Clock,
        state: WhatsAppState | None = None,
        graph_factory: Callable[[str], WhatsAppGraph] = lambda version: WhatsAppGraph(
            api_version=version
        ),
        client_ids: Callable[[], Iterable[str]] = lambda: (),
        public_base_url: Callable[[], str | None] = lambda: None,
        changed: Callable[[str], None] = lambda client_id: None,
        pending: Any = None,
    ) -> None:
        self._secrets = secrets
        self._clock = clock
        self.state = state if state is not None else InMemoryWhatsAppState()
        self._graph_factory = graph_factory
        self._client_ids = client_ids
        self._base = public_base_url
        self._changed = changed
        self._pending: dict[str, tuple[str, datetime]] = {}  # nonce -> (client, expires)
        # The shared (database) register of pending sign-ups, when there is one: a callback may then
        # land on another process than the one that started the sign-up. None: this process's memory.
        self._shared = pending
        self._lock = threading.Lock()

    def __repr__(self) -> str:
        return "WhatsAppService()"

    # ---- wiring done after construction (the registry does not exist yet) ----------------------

    def bind(
        self,
        *,
        client_ids: Callable[[], Iterable[str]],
        changed: Callable[[str], None],
    ) -> None:
        self._client_ids = client_ids
        self._changed = changed

    # ---- the platform's Meta app -------------------------------------------------------------------

    def _global(self, key: str) -> str | None:
        try:
            return self._secrets.get(GLOBAL_SCOPE, key)
        except SecretStoreError:
            return None

    def _client_value(self, client_id: str, key: str) -> str | None:
        try:
            return self._secrets.get(client_scope(client_id), key)
        except SecretStoreError:
            return None

    def _version(self) -> str:
        return self._global(WHATSAPP_API_VERSION) or DEFAULT_API_VERSION

    def graph(self) -> WhatsAppGraph:
        return self._graph_factory(self._version())

    def missing(self) -> list[str]:
        """What the Admin still has to set for the clients to connect. Names only."""
        labels = (
            (WHATSAPP_APP_ID, "Meta app id"),
            (WHATSAPP_CONFIG_ID, "Embedded Signup configuration id"),
            (WHATSAPP_APP_SECRET, "Meta app secret"),
            (WHATSAPP_VERIFY_TOKEN, "WhatsApp verify token"),
        )
        return [label for key, label in labels if not self._global(key)]

    def configured(self) -> bool:
        return not self.missing()

    def webhook_url(self) -> str | None:
        base = (self._base() or "").strip().rstrip("/")
        return f"{base}/webhooks/whatsapp" if base.startswith("https://") else None

    def _signing_key(self) -> bytes | None:
        secret = self._global(WHATSAPP_APP_SECRET)
        if not secret:
            return None
        return hmac.new(secret.encode(), b"revansa:whatsapp-signup-state", hashlib.sha256).digest()

    # ---- connection: start / complete --------------------------------------------------------------

    def start(self, client_id: str) -> StartInfo:
        key = self._signing_key()
        app_id, config_id = self._global(WHATSAPP_APP_ID), self._global(WHATSAPP_CONFIG_ID)
        if key is None or not app_id or not config_id or not self._global(WHATSAPP_VERIFY_TOKEN):
            raise WhatsAppConnectionError(UNAVAILABLE_CAUSE)
        now = self._clock.now()
        nonce = token_secrets.token_urlsafe(18)
        expires = now + timedelta(seconds=STATE_TTL_SECONDS)
        if self._shared is not None:
            self._shared.put("whatsapp", nonce, client_id, "", expires)
        else:
            with self._lock:
                self._pending = {n: v for n, v in self._pending.items() if v[1] > now}
                self._pending[nonce] = (client_id, expires)
        payload = f"{client_id}.{nonce}.{int(expires.timestamp())}"
        mac = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        return StartInfo(app_id, config_id, self._version(), f"{payload}.{mac}")

    def _take_state(self, client_id: str, state: str) -> None:
        key = self._signing_key()
        if key is None:
            raise WhatsAppConnectionError(UNAVAILABLE_CAUSE)
        payload, _, mac = state.rpartition(".")
        expected = hmac.new(key, payload.encode(), hashlib.sha256).hexdigest()
        parts = payload.split(".")
        nonce = parts[1] if len(parts) == 3 else ""
        if self._shared is not None:  # single use, atomically, whichever process asks first
            try:
                row = self._shared.take("whatsapp", nonce)
            except Exception:  # noqa: BLE001 - a register that cannot answer proves nothing
                row = None
            pending = (row.client_id, row.expires_at) if row is not None else None
        else:
            with self._lock:  # single use: the first attempt consumes it, valid or not
                pending = self._pending.pop(nonce, None)
        if (
            not hmac.compare_digest(mac.encode(), expected.encode())
            or len(parts) != 3
            or pending is None
            or pending[0] != client_id
            or parts[0] != client_id
            or pending[1] <= self._clock.now()
        ):
            raise WhatsAppConnectionError(INVALID)

    def complete(
        self,
        client_id: str,
        *,
        state: str,
        code: str,
        expected_number: str | None,
        waba_hint: str | None = None,
        phone_hint: str | None = None,
    ) -> WhatsAppStatus:
        """Finish the connection the client started. Nothing is stored unless every step worked."""
        self._take_state(client_id, state)
        app_id, app_secret = self._global(WHATSAPP_APP_ID), self._global(WHATSAPP_APP_SECRET)
        if not app_id or not app_secret:
            raise WhatsAppConnectionError(UNAVAILABLE_CAUSE)
        graph = self.graph()
        token: str | None = None
        subscribed: str | None = None
        try:
            token = graph.exchange_code(app_id=app_id, app_secret=app_secret, code=code)
            waba_id, phone = self._account_of(
                graph, token, app_id, app_secret, waba_hint, phone_hint
            )
            display = _digits(str(phone.get("display_phone_number", "")))
            if expected_number and display != _digits(expected_number):
                raise WhatsAppConnectionError(NUMBER_MISMATCH)
            phone_id = str(phone["id"])
            self._require_free_number(client_id, phone_id)
            graph.subscribe(token, waba_id)
            subscribed = waba_id
            pin = self._register(graph, token, client_id, phone_id, phone)
            user_id = self._user_of(graph, app_id, app_secret, token)
            earlier = self._token_and_waba(client_id)
            self._store(client_id, token, waba_id, phone_id, phone, pin, user_id)
            self._release_earlier(graph, earlier, waba_id)
        except WhatsAppConnectionError:
            self._withdraw(graph, token, subscribed)
            raise
        except GraphError as error:
            self._withdraw(graph, token, subscribed)
            log.warning("%s: WhatsApp connection refused by Meta (code %s)", client_id, error.code)
            raise WhatsAppConnectionError(
                UNAVAILABLE_CAUSE if error.transient else (DENIED if error.auth else ERROR)
            ) from None
        self._changed(client_id)
        return self.status(client_id)

    def _account_of(
        self,
        graph: WhatsAppGraph,
        token: str,
        app_id: str,
        app_secret: str,
        waba_hint: str | None,
        phone_hint: str | None,
    ) -> tuple[str, dict[str, Any]]:
        """The one account and number this token grants. The browser's ids are hints Meta confirms:
        an account the token cannot read is never used."""
        candidates = graph.granted_accounts(app_id=app_id, app_secret=app_secret, token=token)
        if waba_hint and waba_hint.isdigit() and waba_hint not in candidates:
            candidates.append(waba_hint)
        if not candidates:
            raise WhatsAppConnectionError(DENIED)
        found: list[tuple[str, dict[str, Any]]] = []
        for waba in candidates:
            try:
                numbers = graph.phone_numbers(token, waba)
            except GraphError as error:
                if error.transient:
                    raise
                continue  # an account this token cannot read
            found += [(waba, n) for n in numbers if str(n.get("id", "")).isdigit()]
        if phone_hint:
            chosen = [f for f in found if str(f[1]["id"]) == phone_hint]
            found = chosen or found
        if not found:
            raise WhatsAppConnectionError(DENIED)
        if len(found) > 1:
            raise WhatsAppConnectionError(NUMBER_UNCLEAR)
        return found[0]

    def _require_free_number(self, client_id: str, phone_id: str) -> None:
        """Before anything is done at Meta: a number belongs to one client. (The store's unique
        constraint is the atomic guard at the moment of saving.)"""
        for other in self._client_ids():
            if (
                other != client_id
                and self._client_value(other, WHATSAPP_PHONE_NUMBER_ID) == phone_id
            ):
                raise WhatsAppConnectionError(IN_USE)

    def _register(
        self, graph: WhatsAppGraph, token: str, client_id: str, phone_id: str, phone: dict[str, Any]
    ) -> str | None:
        """Register the number on the Cloud API unless Meta already shows it connected. The
        two-step PIN is generated here and kept, so a later reconnection can register again."""
        if str(phone.get("status", "")).upper() == "CONNECTED":
            return self._client_value(client_id, WHATSAPP_REGISTRATION_PIN)
        pin = f"{token_secrets.randbelow(1_000_000):06d}"
        graph.register(token, phone_id, pin)
        return pin

    def _user_of(
        self, graph: WhatsAppGraph, app_id: str, app_secret: str, token: str
    ) -> str | None:
        """The Meta user who authorised, so that Meta's deauthorisation and data-deletion calls
        can be tied to this client."""
        try:
            info = graph.token_info(app_id=app_id, app_secret=app_secret, token=token)
        except GraphError:
            return None
        user = info.get("user_id")
        return str(user) if user else None

    def _store(
        self,
        client_id: str,
        token: str,
        waba_id: str,
        phone_id: str,
        phone: dict[str, Any],
        pin: str | None,
        user_id: str | None,
    ) -> None:
        scope = client_scope(client_id)
        previous = self._client_value(client_id, WHATSAPP_PHONE_NUMBER_ID)
        try:
            # The number first, and unique: another client holding it refuses the whole save.
            self._secrets.set(scope, WHATSAPP_PHONE_NUMBER_ID, phone_id, by=_ACTOR, unique=True)
        except DuplicateSecretError:
            raise WhatsAppConnectionError(IN_USE) from None
        except SecretStoreError:
            raise WhatsAppConnectionError(ERROR) from None
        try:
            self._secrets.set(scope, WHATSAPP_ACCESS_TOKEN, token, by=_ACTOR)
            self._secrets.set(scope, WHATSAPP_WABA_ID, waba_id, by=_ACTOR)
            if pin:
                self._secrets.set(scope, WHATSAPP_REGISTRATION_PIN, pin, by=_ACTOR)
            if user_id:
                self._secrets.set(scope, WHATSAPP_FB_USER_ID, user_id, by=_ACTOR)
            snapshot = {
                "display_number": str(phone.get("display_phone_number", "")),
                "verified_name": str(phone.get("verified_name", "")),
                "quality": str(phone.get("quality_rating", "")),
                "connected_at": self._clock.now().isoformat(),
            }
            self._secrets.set(scope, WHATSAPP_CONNECTION, json.dumps(snapshot), by=_ACTOR)
            self._secrets.delete(scope, WHATSAPP_REAUTH_REQUIRED)
        except SecretStoreError:
            if previous:
                self._secrets.set(scope, WHATSAPP_PHONE_NUMBER_ID, previous, by=_ACTOR, unique=True)
            else:
                self._secrets.delete(scope, WHATSAPP_PHONE_NUMBER_ID)
            raise WhatsAppConnectionError(ERROR) from None

    @staticmethod
    def _release_earlier(
        graph: WhatsAppGraph, earlier: tuple[str | None, str | None], waba_id: str
    ) -> None:
        """A client that connects another account (its number changed) stops the webhooks of the
        account it leaves. Its permissions are not withdrawn: they may be the very ones in use."""
        token, waba = earlier
        if token and waba and waba != waba_id:
            try:
                graph.unsubscribe(token, waba)
            except GraphError:
                pass

    @staticmethod
    def _withdraw(graph: WhatsAppGraph, token: str | None, subscribed: str | None = None) -> None:
        """A token obtained for a connection that did not finish is withdrawn at once, and so is
        the webhook subscription made for it."""
        if not token:
            return
        for call in (
            (lambda: graph.unsubscribe(token, subscribed)) if subscribed else None,
            lambda: graph.revoke(token),
        ):
            if call is None:
                continue
            try:
                call()
            except GraphError:
                pass

    # ---- status ------------------------------------------------------------------------------------

    def status(self, client_id: str) -> WhatsAppStatus:
        token = self._client_value(client_id, WHATSAPP_ACCESS_TOKEN)
        phone_id = self._client_value(client_id, WHATSAPP_PHONE_NUMBER_ID)
        if not token or not phone_id:
            return WhatsAppStatus(NOT_CONNECTED if self.configured() else UNAVAILABLE)
        snapshot = self._snapshot(client_id)
        reauth = self._client_value(client_id, WHATSAPP_REAUTH_REQUIRED) == "1"
        return WhatsAppStatus(
            REAUTH_REQUIRED if reauth else CONNECTED,
            display_number=snapshot.get("display_number") or None,
            verified_name=snapshot.get("verified_name") or None,
            quality=snapshot.get("quality") or None,
            waba_id=self._client_value(client_id, WHATSAPP_WABA_ID),
            phone_number_id=phone_id,
            connected_at=snapshot.get("connected_at") or None,
        )

    def _snapshot(self, client_id: str) -> dict[str, str]:
        raw = self._client_value(client_id, WHATSAPP_CONNECTION)
        try:
            data = json.loads(raw) if raw else {}
        except ValueError:
            return {}
        return {k: str(v) for k, v in data.items()} if isinstance(data, dict) else {}

    def refresh_account(self, client_id: str) -> WhatsAppStatus:
        """Ask Meta now about the client's number (quality, registration) and whether the webhook
        subscription is still there; a refused token marks the client as to reconnect."""
        token, waba = self._token_and_waba(client_id)
        phone_id = self._client_value(client_id, WHATSAPP_PHONE_NUMBER_ID)
        if token is None or phone_id is None:
            return self.status(client_id)
        graph = self.graph()
        try:
            phone = graph.phone_number(token, phone_id)
            if waba and not graph.subscribed(token, waba):
                graph.subscribe(token, waba)
        except GraphError as error:
            if error.auth:
                self.mark_reauth_required(client_id)
            raise
        scope = client_scope(client_id)
        snapshot = {
            **self._snapshot(client_id),
            "display_number": str(phone.get("display_phone_number", "")),
            "verified_name": str(phone.get("verified_name", "")),
            "quality": str(phone.get("quality_rating", "")),
        }
        self._secrets.set(scope, WHATSAPP_CONNECTION, json.dumps(snapshot), by=_ACTOR)
        return self.status(client_id)

    def _token_and_waba(self, client_id: str) -> tuple[str | None, str | None]:
        return (
            self._client_value(client_id, WHATSAPP_ACCESS_TOKEN),
            self._client_value(client_id, WHATSAPP_WABA_ID),
        )

    def mark_reauth_required(self, client_id: str) -> None:
        try:
            self._secrets.set(client_scope(client_id), WHATSAPP_REAUTH_REQUIRED, "1", by=_ACTOR)
        except SecretStoreError:
            log.warning("could not record that a WhatsApp authorisation must be renewed")

    # ---- disconnection -----------------------------------------------------------------------------

    def disconnect(self, client_id: str, *, release_number: bool = False) -> DisconnectReport:
        """Ask Meta to stop sending webhooks, to withdraw the token's permissions and (only if
        asked, since it frees the number from the Cloud API) to deregister the number; then forget
        everything locally, whatever Meta answered. A token Meta no longer accepts needs none of
        the remote steps."""
        token, waba = self._token_and_waba(client_id)
        phone_id = self._client_value(client_id, WHATSAPP_PHONE_NUMBER_ID)
        graph = self.graph()
        steps: dict[str, str] = {}
        dead = self._client_value(client_id, WHATSAPP_REAUTH_REQUIRED) == "1"

        def attempt(name: str, call: Callable[[], None], possible: bool) -> None:
            if not possible or token is None or dead:
                steps[name] = "skipped"
                return
            try:
                call()
                steps[name] = "done"
            except GraphError:
                steps[name] = "failed"

        attempt(
            "unsubscribe_webhooks", lambda: graph.unsubscribe(token or "", waba or ""), bool(waba)
        )
        attempt(
            "deregister_number",
            lambda: graph.deregister(token or "", phone_id or ""),
            release_number and bool(phone_id),
        )
        attempt("revoke_token", lambda: graph.revoke(token or ""), True)
        self.forget_locally(client_id)
        return DisconnectReport(steps)

    def forget_locally(self, client_id: str) -> None:
        """Nothing of the account stays: token, ids, PIN, snapshot, templates, windows."""
        scope = client_scope(client_id)
        for key in (
            WHATSAPP_ACCESS_TOKEN,
            WHATSAPP_PHONE_NUMBER_ID,
            WHATSAPP_WABA_ID,
            WHATSAPP_REGISTRATION_PIN,
            WHATSAPP_FB_USER_ID,
            WHATSAPP_CONNECTION,
            WHATSAPP_REAUTH_REQUIRED,
        ):
            try:
                self._secrets.delete(scope, key)
            except SecretStoreError:
                log.error("%s: could not remove a stored WhatsApp value", client_id)
        try:
            self.state.forget_client(client_id)
        except Exception:  # noqa: BLE001 - the secrets are what matters; stale rows are harmless
            log.warning("%s: could not clear the WhatsApp registers", client_id)
        self._changed(client_id)

    # ---- Meta's deauthorisation and data-deletion calls -------------------------------------------

    def client_of_signed_request(self, signed_request: str) -> tuple[str | None, bool]:
        """(the client whose Meta user this is, whether the signature is valid). Meta signs these
        calls with the app secret; anything else is refused."""
        secret = self._global(WHATSAPP_APP_SECRET)
        encoded_sig, _, encoded_payload = signed_request.partition(".")
        if not secret or not encoded_payload:
            return None, False
        import base64

        def decode(value: str) -> bytes:
            return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

        try:
            signature, payload = decode(encoded_sig), decode(encoded_payload)
            expected = hmac.new(secret.encode(), encoded_payload.encode(), hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                return None, False
            user = str(json.loads(payload).get("user_id", ""))
        except (ValueError, TypeError, AttributeError):
            return None, False
        if not user:
            return None, True
        for client_id in self._client_ids():
            if self._client_value(client_id, WHATSAPP_FB_USER_ID) == user:
                return client_id, True
        return None, True

    # ---- the client of a notification --------------------------------------------------------------

    def client_for_account(self, waba_id: str | None, phone_number_id: str | None) -> str | None:
        for client_id in self._client_ids():
            if (
                phone_number_id
                and self._client_value(client_id, WHATSAPP_PHONE_NUMBER_ID) == phone_number_id
            ):
                return client_id
            if waba_id and self._client_value(client_id, WHATSAPP_WABA_ID) == waba_id:
                return client_id
        return None

    # ---- what comes in -----------------------------------------------------------------------------

    def note_inbound(self, client_id: str, message: InboundMessage) -> None:
        """A person wrote: their 24-hour window opens (or is extended). Never raises."""
        try:
            self.state.record_inbound(client_id, _digits(message.sender), message.timestamp)
        except Exception:  # noqa: BLE001 - a lost window only means a template is used instead
            log.warning("%s: could not record a WhatsApp window", client_id)

    def handle(self, notification: WhatsAppNotification) -> None:
        """Apply one thing Meta told us. Unknown accounts are ignored (logged without content)."""
        client_id = self.client_for_account(notification.waba_id, notification.phone_number_id)
        if client_id is None:
            log.info("WhatsApp %s notification for an unknown account", notification.kind)
            return
        data = notification.data
        if notification.kind == DELIVERY:
            self._delivery(client_id, data)
        elif notification.kind == TEMPLATE:
            self._template_event(client_id, data)
        elif notification.kind == PHONE:
            self._phone_event(client_id, data)
        elif notification.kind == ACCOUNT and data.get("event") in _ACCOUNT_REMOVED:
            log.warning(
                "%s: the WhatsApp account was withdrawn from Revansa by its owner", client_id
            )
            self.forget_locally(client_id)
        elif notification.kind == ACCOUNT:
            log.info("%s: WhatsApp account event %s", client_id, data.get("event", ""))

    def _delivery(self, client_id: str, data: dict[str, str] | Any) -> None:
        record = DeliveryRecord(
            client_id=client_id,
            message_id=data["message_id"],
            status=data["status"],
            at=datetime.fromisoformat(data["at"]),
            recipient=data.get("recipient", ""),
            error_code=data.get("error_code", ""),
            error_title=data.get("error_title", ""),
        )
        self.state.record_delivery(record)
        if record.status == FAILED:
            log.warning(
                "%s: WhatsApp reported a message as failed (code %s)", client_id, record.error_code
            )
            if record.error_code in ("0", "190", "10", "3", "200"):
                self.mark_reauth_required(client_id)

    def _template_event(self, client_id: str, data: dict[str, str] | Any) -> None:
        name, language = data.get("name", ""), data.get("language", "")
        existing = next(
            (
                t
                for t in self.state.templates(client_id)
                if t.name == name and t.language == language
            ),
            None,
        )
        event = data.get("event", "")
        if data.get("field") != "message_template_status_update" or not event:
            if existing is not None and data.get("category"):
                self.state.save_template(
                    TemplateRecord(**{**_fields(existing), "category": data["category"]})
                )
            return
        status = _STATUS_OF_EVENT.get(event.upper(), existing.status if existing else PENDING)
        if existing is None:
            existing = TemplateRecord(
                client_id=client_id,
                name=name,
                language=language,
                meta_id=data.get("template_id", ""),
            )
        self.state.save_template(
            TemplateRecord(
                **{
                    **_fields(existing),
                    "status": status,
                    "reason": "" if status == APPROVED else data.get("reason", ""),
                    "meta_id": data.get("template_id") or existing.meta_id,
                    "updated_at": self._clock.now(),
                    "is_opening": existing.is_opening and status == APPROVED,
                }
            )
        )

    def _phone_event(self, client_id: str, data: dict[str, str] | Any) -> None:
        snapshot = self._snapshot(client_id)
        if data.get("event"):
            snapshot["quality_event"] = data["event"]
        if data.get("limit"):
            snapshot["limit"] = data["limit"]
        try:
            self._secrets.set(
                client_scope(client_id), WHATSAPP_CONNECTION, json.dumps(snapshot), by=_ACTOR
            )
        except SecretStoreError:
            pass

    # ---- templates ---------------------------------------------------------------------------------

    def templates(self, client_id: str) -> Sequence[TemplateRecord]:
        return self.state.templates(client_id)

    def sync_templates(self, client_id: str) -> Sequence[TemplateRecord]:
        """The account's templates exactly as Meta lists them now."""
        token, waba = self._token_and_waba(client_id)
        if token is None or waba is None:
            raise WhatsAppConnectionError(NOT_CONNECTED)
        try:
            rows = self.graph().list_templates(token, waba)
        except GraphError as error:
            if error.auth:
                self.mark_reauth_required(client_id)
            raise
        now = self._clock.now()
        records = [self._record_of(client_id, row, now) for row in rows if row.get("name")]
        self.state.replace_templates(client_id, records)
        return self.state.templates(client_id)

    @staticmethod
    def _record_of(client_id: str, row: dict[str, Any], now: datetime) -> TemplateRecord:
        body = next(
            (
                str(c.get("text", ""))
                for c in row.get("components") or []
                if isinstance(c, dict) and c.get("type") == "BODY"
            ),
            "",
        )
        return TemplateRecord(
            client_id=client_id,
            name=str(row["name"]),
            language=str(row.get("language", "")),
            category=str(row.get("category", "")),
            status=str(row.get("status", PENDING)).upper(),
            body=body,
            parameter_names=template_parameters(body),
            reason=str(row.get("rejected_reason") or "").replace("NONE", ""),
            meta_id=str(row.get("id", "")),
            updated_at=now,
        )

    def create_template(
        self,
        client_id: str,
        *,
        name: str,
        language: str,
        category: str,
        body: str,
        examples: Sequence[str],
    ) -> TemplateRecord:
        """Submit a template to Meta for review. It is pending until Meta approves it: the webhook
        (or a synchronisation) brings the answer."""
        problem = template_problem(name, language, category, body, examples)
        if problem is not None:
            raise ValueError(problem)
        token, waba = self._token_and_waba(client_id)
        if token is None or waba is None:
            raise WhatsAppConnectionError(NOT_CONNECTED)
        names = template_parameters(body)
        component: dict[str, Any] = {"type": "BODY", "text": body}
        if names:
            component["example"] = {"body_text": [list(examples)]}
        payload = {
            "name": name,
            "language": language,
            "category": category,
            "components": [component],
        }
        try:
            answer = self.graph().create_template(token, waba, payload)
        except GraphError as error:
            if error.auth:
                self.mark_reauth_required(client_id)
            raise
        record = TemplateRecord(
            client_id=client_id,
            name=name,
            language=language,
            category=str(answer.get("category") or category),
            status=str(answer.get("status") or PENDING).upper(),
            body=body,
            parameter_names=names,
            meta_id=str(answer.get("id", "")),
            updated_at=self._clock.now(),
        )
        self.state.save_template(record)
        return record

    def delete_template(self, client_id: str, name: str) -> None:
        token, waba = self._token_and_waba(client_id)
        if token is None or waba is None:
            raise WhatsAppConnectionError(NOT_CONNECTED)
        try:
            self.graph().delete_template(token, waba, name)
        except GraphError as error:
            if error.auth:
                self.mark_reauth_required(client_id)
            raise
        self.state.delete_template(client_id, name)

    def choose_opening(self, client_id: str, name: str, language: str) -> bool:
        """Choose the approved template that carries the agent's message outside the window. It
        must have exactly one variable, which receives the message."""
        template = next(
            (
                t
                for t in self.state.templates(client_id)
                if t.name == name and t.language == language
            ),
            None,
        )
        if template is None or not template.sendable or len(template.parameter_names) != 1:
            return False
        return self.state.set_opening(client_id, name, language)

    # ---- the channel -------------------------------------------------------------------------------

    def policy_for(self, client_id: str) -> SendPolicy:
        state = self.state

        def last_inbound(digits: str) -> datetime | None:
            try:
                return state.last_inbound(client_id, digits)
            except Exception:  # noqa: BLE001 - an unknown window is a closed one
                return None

        def opening() -> OpeningTemplate | None:
            try:
                found = state.opening_template(client_id)
            except Exception:  # noqa: BLE001
                return None
            return (
                OpeningTemplate(found.name, found.language, found.parameter_names)
                if found is not None
                else None
            )

        def template_problem_seen(name: str, language: str, code: str) -> None:
            status = _STATUS_OF_ERROR.get(code)
            if status is None or not name:
                return
            for t in state.templates(client_id):
                if t.name == name and t.language == language:
                    state.save_template(
                        TemplateRecord(**{**_fields(t), "status": status, "is_opening": False})
                    )

        def window_closed(digits: str) -> None:
            try:
                state.close_window(client_id, digits)
            except Exception:  # noqa: BLE001 - the next send asks again
                log.warning("%s: could not forget a WhatsApp window", client_id)

        return SendPolicy(
            on_window_closed=window_closed,
            last_inbound=last_inbound,
            opening_template=opening,
            on_auth_failure=lambda: self.mark_reauth_required(client_id),
            on_template_problem=template_problem_seen,
        )

    def channel_for(self, config: Any) -> WhatsAppChannel:
        from sales_app.config.secrets import WHATSAPP_ACCESS_TOKEN as TOKEN
        from sales_app.config.secrets import WHATSAPP_PHONE_NUMBER_ID as PHONE_ID

        return WhatsAppChannel(
            token=config.secrets.get(TOKEN),
            phone_number_id=config.secrets.get(PHONE_ID),
            api_version=config.secrets.get(WHATSAPP_API_VERSION),
            clock=self._clock,
            policy=self.policy_for(config.client_id),
        )


def _fields(record: TemplateRecord) -> dict[str, Any]:
    return {
        "client_id": record.client_id,
        "name": record.name,
        "language": record.language,
        "category": record.category,
        "status": record.status,
        "body": record.body,
        "parameter_names": record.parameter_names,
        "reason": record.reason,
        "meta_id": record.meta_id,
        "is_opening": record.is_opening,
        "updated_at": record.updated_at,
    }


__all__ = [
    "CONNECTED",
    "NOT_CONNECTED",
    "REAUTH_REQUIRED",
    "UNAVAILABLE",
    "DisconnectReport",
    "StartInfo",
    "WhatsAppConnectionError",
    "WhatsAppService",
    "WhatsAppStatus",
    "template_parameters",
    "template_problem",
]
