"""Real connection checks for the integrations the Admin configures.

Each check makes one read-only call to the real service with the stored credentials; nothing is
simulated. The outcome never carries a credential: messages are fixed sentences, plus the error
code the remote service returned.

`check_openai` only lists what the account can see and costs nothing. `check_openai_inference` is
different: it asks one model for a few tokens, which is a real, billed request. Nothing here calls
it by itself; the Admin API asks for an explicit confirmation first.

`check_hubspot` reads at most one contact id with the client's own token, and `check_google_sheets`
reads the spreadsheet's tab list with the platform's service account: both are read-only, bounded
in time, and never return what they read.

Not every credential can be checked on its own. The WhatsApp app secret and verify token only prove
themselves when Meta calls Revansa's webhook, so no check exists for them and none is pretended.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

import httpx

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_GRAPH_URL = "https://graph.facebook.com"
DEFAULT_HUBSPOT_URL = "https://api.hubapi.com"
DEFAULT_TIMEOUT_SECONDS = 15.0
# A real request is allowed to take longer than a list: a model may be slow to start.
INFERENCE_TIMEOUT_SECONDS = 30.0
# The most a test may generate. The parameter is the one the OpenAI adapter sends at runtime
# (`max_completion_tokens`), so a model that cannot take it is found out here and not by a client.
INFERENCE_MAX_TOKENS = 16
INFERENCE_PROMPT = "Reply with the word: ok"

HttpClientFactory = Callable[[], httpx.Client]


def default_http_client() -> httpx.Client:
    return httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    ok: bool
    message: str
    details: dict[str, str] = field(default_factory=dict)  # never a credential


def _error_code(response: httpx.Response) -> str | None:
    try:
        error = response.json().get("error")
    except Exception:  # noqa: BLE001 - an unreadable body just means no code
        return None
    if isinstance(error, dict):
        code = error.get("code") or error.get("type")
        if isinstance(code, (str, int)):
            return str(code)[:64]
    return None


def _failure(prefix: str, response: httpx.Response) -> ConnectionResult:
    code = _error_code(response)
    suffix = f" (code {code})" if code else ""
    return ConnectionResult(
        False, f"{prefix}: the service answered {response.status_code}{suffix}."
    )


def check_openai(
    *,
    api_key: str | None,
    organization: str | None,
    project: str | None,
    base_url: str | None,
    models: dict[str, str | None],
    http: HttpClientFactory = default_http_client,
) -> ConnectionResult:
    """Lists the account's models (needs a valid key) and reports whether each configured model
    is among them."""
    if not api_key:
        return ConnectionResult(False, "The OpenAI API key is not configured.")
    headers = {"Authorization": f"Bearer {api_key}"}
    if organization:
        headers["OpenAI-Organization"] = organization
    if project:
        headers["OpenAI-Project"] = project
    url = f"{(base_url or DEFAULT_OPENAI_BASE_URL).rstrip('/')}/models"
    try:
        with http() as client:
            response = client.get(url, headers=headers)
    except httpx.HTTPError as error:
        return ConnectionResult(False, f"Could not reach OpenAI ({type(error).__name__}).")
    if response.status_code in (401, 403):
        return _failure("OpenAI refused these credentials", response)
    if not response.is_success:
        return _failure("OpenAI request failed", response)
    try:
        available = {m["id"] for m in response.json()["data"] if isinstance(m.get("id"), str)}
    except Exception:  # noqa: BLE001 - not the shape the OpenAI API documents
        return ConnectionResult(False, "OpenAI answered, but not with a model list.")
    details = {
        label: ("available" if model in available else "not available to this account")
        for label, model in models.items()
        if model
    }
    if any(state != "available" for state in details.values()):
        return ConnectionResult(
            False, "Credentials accepted, but a configured model is not available.", details
        )
    return ConnectionResult(True, "OpenAI accepted the credentials.", details)


class InferenceOutcome(StrEnum):
    """How a real test of a model ended. The value is what the Admin API reports and keeps."""

    USABLE = "usable"
    NO_API_KEY = "no_api_key"  # nothing was sent
    INVALID_API_KEY = "invalid_api_key"  # the key, organization or project was refused
    MODEL_NOT_FOUND = "model_not_found"
    INVALID_PARAMETERS = "invalid_parameters"  # the model refused what the adapter sends
    QUOTA_EXCEEDED = "quota_exceeded"  # rate limit or exhausted quota
    TIMEOUT = "timeout"
    UNREACHABLE = "unreachable"
    SERVICE_ERROR = "service_error"
    UNEXPECTED_RESPONSE = "unexpected_response"


@dataclass(frozen=True, slots=True)
class InferenceResult:
    outcome: InferenceOutcome
    message: str  # a fixed sentence: never a credential, never what the service said
    details: dict[str, str] = field(default_factory=dict)  # the service's error code, tokens used

    @property
    def ok(self) -> bool:
        return self.outcome is InferenceOutcome.USABLE

    @property
    def sent_a_request(self) -> bool:
        """Whether a request went out: only then does the outcome say anything about the model."""
        return self.outcome is not InferenceOutcome.NO_API_KEY


def _inference_failure(
    outcome: InferenceOutcome, message: str, response: httpx.Response | None = None
) -> InferenceResult:
    code = _error_code(response) if response is not None else None
    return InferenceResult(outcome, message, {"code": code} if code else {})


def check_openai_inference(
    *,
    api_key: str | None,
    organization: str | None,
    project: str | None,
    base_url: str | None,
    model_id: str,
    http: HttpClientFactory = default_http_client,
) -> InferenceResult:
    """Asks the model `model_id` (exactly that id) for at most `INFERENCE_MAX_TOKENS` tokens, with the
    stored credentials. A real request that the account may be billed for: the caller must have been
    told so. Succeeds when the model answers; the answer itself is neither returned nor kept."""
    if not api_key:
        return InferenceResult(
            InferenceOutcome.NO_API_KEY, "The OpenAI API key is not configured: nothing was sent."
        )
    headers = {"Authorization": f"Bearer {api_key}"}
    if organization:
        headers["OpenAI-Organization"] = organization
    if project:
        headers["OpenAI-Project"] = project
    url = f"{(base_url or DEFAULT_OPENAI_BASE_URL).rstrip('/')}/chat/completions"
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": INFERENCE_PROMPT}],
        "max_completion_tokens": INFERENCE_MAX_TOKENS,
    }
    try:
        with http() as client:
            response = client.post(
                url, headers=headers, json=body, timeout=INFERENCE_TIMEOUT_SECONDS
            )
    except httpx.TimeoutException:
        return InferenceResult(
            InferenceOutcome.TIMEOUT,
            f"OpenAI did not answer within {INFERENCE_TIMEOUT_SECONDS:g} seconds.",
        )
    except httpx.HTTPError as error:
        return InferenceResult(
            InferenceOutcome.UNREACHABLE, f"Could not reach OpenAI ({type(error).__name__})."
        )

    status = response.status_code
    code = _error_code(response) if not response.is_success else None
    if status in (401, 403):
        return _inference_failure(
            InferenceOutcome.INVALID_API_KEY,
            "OpenAI refused these credentials: check the API key, organization and project.",
            response,
        )
    if status == 404 or code == "model_not_found":
        return _inference_failure(
            InferenceOutcome.MODEL_NOT_FOUND,
            "OpenAI does not know this model id, or the account has no access to it.",
            response,
        )
    if status == 429:
        quota = code == "insufficient_quota"
        return _inference_failure(
            InferenceOutcome.QUOTA_EXCEEDED,
            "The account has no quota left." if quota else "OpenAI's rate limit is reached.",
            response,
        )
    if status in (400, 422):
        return _inference_failure(
            InferenceOutcome.INVALID_PARAMETERS,
            "The model refused the request parameters Revansa sends.",
            response,
        )
    if status >= 500:
        return _inference_failure(
            InferenceOutcome.SERVICE_ERROR, "OpenAI answered with an error.", response
        )
    if not response.is_success:
        return _inference_failure(
            InferenceOutcome.SERVICE_ERROR,
            f"OpenAI refused the request (status {status}).",
            response,
        )
    try:
        completion = response.json()
        choices = completion["choices"]
        usable = isinstance(choices, list) and len(choices) > 0
    except Exception:  # noqa: BLE001 - not the shape the OpenAI API documents
        usable = False
        completion = {}
    if not usable:
        return InferenceResult(
            InferenceOutcome.UNEXPECTED_RESPONSE, "OpenAI answered, but not with a completion."
        )
    details: dict[str, str] = {}
    usage = completion.get("usage")
    total = usage.get("total_tokens") if isinstance(usage, dict) else None
    if isinstance(total, int) and not isinstance(total, bool):
        details["tokens_used"] = str(total)
    served = completion.get("model")
    if isinstance(served, str) and served:
        details["served_model"] = served[:128]
    return InferenceResult(InferenceOutcome.USABLE, "The model answered.", details)


def check_whatsapp_client(
    *,
    access_token: str | None,
    phone_number_id: str | None,
    api_version: str,
    http: HttpClientFactory = default_http_client,
    base_url: str = DEFAULT_GRAPH_URL,
) -> ConnectionResult:
    """Reads the phone number's record from the Graph API: it only succeeds when the access token
    is valid AND is allowed to see that phone number id."""
    if not access_token:
        return ConnectionResult(False, "The WhatsApp access token is not configured.")
    if not phone_number_id:
        return ConnectionResult(False, "The WhatsApp phone number id is not configured.")
    url = f"{base_url.rstrip('/')}/{api_version}/{phone_number_id}"
    try:
        with http() as client:
            response = client.get(
                url,
                params={"fields": "display_phone_number,verified_name"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.HTTPError as error:
        return ConnectionResult(False, f"Could not reach Meta ({type(error).__name__}).")
    if not response.is_success:
        return _failure("Meta refused this token or phone number id", response)
    details: dict[str, str] = {}
    try:
        body = response.json()
    except ValueError:  # the success status is what matters
        body = {}
    for key in ("display_phone_number", "verified_name"):
        if isinstance(body, dict) and isinstance(body.get(key), str):
            details[key] = body[key][:128]
    return ConnectionResult(
        True, "Meta accepted the access token for this phone number id.", details
    )


class SheetsProbe(Protocol):
    """The one thing a Sheets check needs of a spreadsheet service: its metadata (tab titles)."""

    def metadata(self, spreadsheet_id: str) -> Any: ...


_SHEETS_REFUSALS = {
    400: "Google did not accept this spreadsheet id.",
    401: "Google refused the platform's access: contact your administrator.",
    403: (
        "The spreadsheet is not shared with the platform's address, or the Google Sheets API "
        "is disabled for the platform."
    ),
    404: "Spreadsheet not found: check the link or the id.",
    429: "Google Sheets' rate limit is reached: try again in a moment.",
}


def check_google_sheets(
    *, api: SheetsProbe | None, spreadsheet_id: str | None, prospects_tab: str | None
) -> ConnectionResult:
    """Reads the spreadsheet's tab list with the platform's service account and checks that the
    prospects tab is there. Nothing is written and no cell is read. A failure never carries what
    Google or the library said, only a fixed sentence (and the HTTP status when there is one)."""
    if api is None:
        return ConnectionResult(
            False,
            "The platform's Google Sheets access is not configured: contact your administrator.",
        )
    if not spreadsheet_id:
        return ConnectionResult(False, "No spreadsheet is configured.")
    try:
        meta = api.metadata(spreadsheet_id)
    except Exception as error:  # noqa: BLE001 - whatever failed, the answer is a fixed sentence
        return _sheets_failure(error)
    titles = getattr(meta, "titles", None)
    if not isinstance(titles, (tuple, list)):
        return ConnectionResult(False, "Google Sheets answered, but not with a list of tabs.")
    if prospects_tab and prospects_tab not in titles:
        return ConnectionResult(
            False,
            "The spreadsheet is reachable, but the prospects tab was not found in it.",
            {"missing_tab": prospects_tab[:128]},
        )
    return ConnectionResult(
        True, "Google Sheets gave the platform access to this spreadsheet and its prospects tab."
    )


def _sheets_failure(error: Exception) -> ConnectionResult:
    status = getattr(error, "status", None)
    if isinstance(status, int) and not isinstance(status, bool):
        if status in _SHEETS_REFUSALS:
            return ConnectionResult(False, _SHEETS_REFUSALS[status], {"status": str(status)})
        if status >= 500:
            return ConnectionResult(
                False, "Google Sheets answered with an error.", {"status": str(status)}
            )
        return ConnectionResult(False, f"Google Sheets answered {status}.", {"status": str(status)})
    if isinstance(error, TimeoutError):
        return ConnectionResult(False, "Google Sheets did not answer in time.")
    return ConnectionResult(False, f"Could not reach Google Sheets ({type(error).__name__}).")


_HUBSPOT_CATEGORY = re.compile(r"^[A-Z][A-Z_]{0,63}$")


def check_hubspot(
    *,
    access_token: str | None,
    http: HttpClientFactory = default_http_client,
    base_url: str = DEFAULT_HUBSPOT_URL,
) -> ConnectionResult:
    """Asks HubSpot for one contact id with the client's Private App token. It only succeeds when
    the token is valid AND may read contacts, which the connector will need. The contact itself
    is neither returned nor kept."""
    if not access_token:
        return ConnectionResult(False, "The HubSpot access token is not configured.")
    url = f"{base_url.rstrip('/')}/crm/v3/objects/contacts"
    try:
        with http() as client:
            response = client.get(
                url,
                params={"limit": 1, "properties": "hs_object_id"},
                headers={"Authorization": f"Bearer {access_token}"},
            )
    except httpx.TimeoutException:
        return ConnectionResult(False, "HubSpot did not answer in time.")
    except httpx.HTTPError as error:
        return ConnectionResult(False, f"Could not reach HubSpot ({type(error).__name__}).")
    status = response.status_code
    details = {"status": str(status)}
    if not response.is_success:
        category = _hubspot_category(response)
        if category:
            details["code"] = category
        if status == 401:
            message = "HubSpot refused this access token."
        elif status == 403:
            message = (
                "HubSpot accepted the token but it may not read contacts: give the Private App "
                "the contacts read permission."
            )
        elif status == 429:
            message = "HubSpot's rate limit is reached: try again in a moment."
        elif status >= 500:
            message = "HubSpot answered with an error."
        else:
            message = f"HubSpot answered {status}."
        return ConnectionResult(False, message, details)
    try:
        usable = isinstance(response.json()["results"], list)
    except Exception:  # noqa: BLE001 - not the shape the HubSpot API documents
        usable = False
    if not usable:
        return ConnectionResult(False, "HubSpot answered, but not with a contact list.", details)
    return ConnectionResult(True, "HubSpot accepted the access token and may read contacts.")


def _hubspot_category(response: httpx.Response) -> str | None:
    try:
        category = response.json().get("category")
    except Exception:  # noqa: BLE001 - an unreadable body just means no code
        return None
    return category if isinstance(category, str) and _HUBSPOT_CATEGORY.match(category) else None


# ---- e-mail -----------------------------------------------------------------------------------


class MailProbeFailure(Exception):
    """What a mailbox check found, by kind: `auth`, `unreachable` or `refused`. Nothing else: not
    what the server said, which may repeat the login."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class MailProbe(Protocol):
    def check_smtp(self) -> None: ...

    def check_imap(self) -> None: ...


_MAIL_PROBLEMS = {
    "auth": "the server refused the login",
    "refused": "this server cannot be used (it is not a public mail server)",
    "unreachable": "the server could not be reached",
}


def check_email(*, probe: MailProbe | None) -> ConnectionResult:
    """Log in to the sending server and to the mailbox: no message is sent, none is read. `probe`
    is None when the mailbox is not set up (settings or password missing)."""
    if probe is None:
        return ConnectionResult(False, "Email: the settings or the password are not set yet.")
    details: dict[str, str] = {}
    problems: list[str] = []
    for name, check in (("smtp", probe.check_smtp), ("imap", probe.check_imap)):
        try:
            check()
        except MailProbeFailure as failure:
            details[name] = failure.kind
            problems.append(f"{name.upper()}: {_MAIL_PROBLEMS.get(failure.kind, 'it failed')}")
        else:
            details[name] = "ok"
    if problems:
        return ConnectionResult(False, "Email: " + "; ".join(problems) + ".", details)
    return ConnectionResult(True, "Email: sending and reading both work.", details)


# ---- Telegram ---------------------------------------------------------------------------------


class BotProbeFailure(Exception):
    """What a bot check found, by kind: `auth`, `unreachable`, `rate_limited` or `service_error`.
    Nothing else: not what Telegram said, and never the token."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


class BotProbe(Protocol):
    def check_bot(self) -> str:
        """The bot's username, or `BotProbeFailure`."""
        ...

    def register_webhook(self, url: str, secret: str) -> None:
        """Tell Telegram where to deliver, or `BotProbeFailure`."""
        ...

    def webhook_state(self, expected_url: str | None) -> str:
        """`ok`, `not_set`, `other_url` or `failing`: whether Telegram delivers to us."""
        ...


_BOT_PROBLEMS = {
    "auth": "Telegram refused this bot token",
    "unreachable": "Telegram could not be reached",
    "rate_limited": "Telegram asks to try again later",
    "rejected": "Telegram refused the platform's address for this bot",
    "service_error": "Telegram had a problem",
}
_WEBHOOK_PROBLEMS = {
    "not_set": "Telegram has no webhook for this bot yet: connect the bot",
    "other_url": "this bot sends its messages somewhere else: connect the bot again",
    "failing": "Telegram cannot deliver messages to the platform",
}


def check_telegram(*, probe: BotProbe | None, expected_url: str | None) -> ConnectionResult:
    """Ask Telegram who the bot is and where it delivers: nothing is sent to anyone. `probe` is None
    when the bot is not set up (no token). `expected_url` is where the platform receives updates,
    None when the deployment has no public address (then only the token can be checked)."""
    if probe is None:
        return ConnectionResult(False, "Telegram: the bot token is not set yet.")
    try:
        probe.check_bot()
    except BotProbeFailure as failure:
        message = _BOT_PROBLEMS.get(failure.kind, "it failed")
        return ConnectionResult(False, f"Telegram: {message}.", {"bot": failure.kind})
    details = {"bot": "ok"}
    if expected_url is None:
        details["webhook"] = "unchecked"
        return ConnectionResult(
            False,
            "Telegram: the bot token works, but the platform has no public address to receive "
            "messages on. Contact your administrator.",
            details,
        )
    state = probe.webhook_state(expected_url)
    details["webhook"] = state
    if state != "ok":
        return ConnectionResult(
            False, f"Telegram: {_WEBHOOK_PROBLEMS.get(state, 'the webhook is not ready')}.", details
        )
    return ConnectionResult(True, "Telegram: the bot works and delivers to the platform.", details)
