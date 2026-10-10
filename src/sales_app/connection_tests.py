"""Real connection checks for the integrations the Admin configures.

Each check makes one read-only call to the real service with the stored credentials; nothing is
simulated. The outcome never carries a credential: messages are fixed sentences, plus the error
code the remote service returned.

`check_openai` only lists what the account can see and costs nothing. `check_openai_inference` is
different: it asks one model for a few tokens, which is a real, billed request. Nothing here calls
it by itself; the Admin API asks for an explicit confirmation first.

Not every credential can be checked on its own. The WhatsApp app secret and verify token only prove
themselves when Meta calls Revansa's webhook, so no check exists for them and none is pretended.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

import httpx

DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_GRAPH_URL = "https://graph.facebook.com"
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
