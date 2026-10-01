"""Real connection checks for the integrations the Admin configures.

Each check makes one read-only call to the real service with the stored credentials; nothing is
simulated. The outcome never carries a credential: messages are fixed sentences, plus the error
code the remote service returned.

Not every credential can be checked on its own. The WhatsApp app secret and verify token only prove
themselves when Meta calls Revansa's webhook, so no check exists for them and none is pretended.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_GRAPH_URL = "https://graph.facebook.com"
DEFAULT_TIMEOUT_SECONDS = 15.0

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
