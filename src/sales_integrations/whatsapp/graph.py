"""Meta's Graph API for the WhatsApp Business Platform, other than sending a message.

Onboarding (Embedded Signup): exchanging the one-time code for the client's business token, finding
which WhatsApp Business Account and phone numbers that token really grants, subscribing the app to
the account's webhooks, registering the number. Then message templates (list, create, delete), the
phone number's health, and the way back out (unsubscribe, deregister, revoke).

Nothing here is simulated: every method is one call to `graph.facebook.com`. The token is passed to
each call and is never kept, logged or put in an error message.
"""

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://graph.facebook.com"
DEFAULT_API_VERSION = "v21.0"
DEFAULT_TIMEOUT_SECONDS = 20.0

# Meta codes that mean the token (or the permission behind it) is no longer accepted.
AUTH_CODES = frozenset({"0", "3", "10", "190", "200"})


class GraphError(Exception):
    """A call Meta refused or could not answer. `message` is Meta's own sentence, never a token."""

    def __init__(self, message: str, *, code: str = "", status: int = 0, transient: bool = False):
        super().__init__(message)
        self.code = code
        self.status = status
        self.transient = transient

    @property
    def auth(self) -> bool:
        """The token is expired, revoked or lacks the permission: the client must connect again."""
        return self.status in (401, 403) or self.code in AUTH_CODES


class WhatsAppGraph:
    def __init__(
        self,
        *,
        api_version: str = DEFAULT_API_VERSION,
        base_url: str = DEFAULT_BASE_URL,
        client: httpx.Client | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._root = f"{base_url.rstrip('/')}/{api_version}"
        self._client = client if client is not None else httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return "WhatsAppGraph()"

    # ---- onboarding --------------------------------------------------------------------------

    def exchange_code(self, *, app_id: str, app_secret: str, code: str) -> str:
        """The business token for the one-time code Embedded Signup gave the browser (the code
        lives 30 seconds: this is called as soon as it arrives)."""
        data = self._call(
            "GET",
            "/oauth/access_token",
            params={"client_id": app_id, "client_secret": app_secret, "code": code},
        )
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise GraphError("Meta did not return an access token")
        return token

    def token_info(self, *, app_id: str, app_secret: str, token: str) -> dict[str, Any]:
        """Meta's own description of a token (`debug_token`): whose it is, what it grants."""
        data = self._call(
            "GET",
            "/debug_token",
            params={"input_token": token, "access_token": f"{app_id}|{app_secret}"},
        )
        info = data.get("data")
        if not isinstance(info, dict) or info.get("is_valid") is False:
            raise GraphError("Meta does not accept this token", code="190", status=401)
        if info.get("app_id") not in (None, app_id):
            raise GraphError("this token belongs to another Meta app", code="190", status=401)
        return info

    def granted_accounts(self, *, app_id: str, app_secret: str, token: str) -> list[str]:
        """The WhatsApp Business Account ids the token really grants (Meta's own answer about the
        token, so a browser cannot claim an account the client did not authorise)."""
        info = self.token_info(app_id=app_id, app_secret=app_secret, token=token)
        found: list[str] = []
        for scope in info.get("granular_scopes") or []:
            if isinstance(scope, dict) and scope.get("scope") in (
                "whatsapp_business_management",
                "whatsapp_business_messaging",
            ):
                found += [str(t) for t in scope.get("target_ids") or []]
        return list(dict.fromkeys(found))

    def phone_numbers(self, token: str, waba_id: str) -> list[dict[str, Any]]:
        data = self._call(
            "GET",
            f"/{waba_id}/phone_numbers",
            token=token,
            params={
                "fields": "id,display_phone_number,verified_name,quality_rating,"
                "code_verification_status,platform_type,status"
            },
        )
        return [row for row in data.get("data") or [] if isinstance(row, dict)]

    def phone_number(self, token: str, phone_number_id: str) -> dict[str, Any]:
        return self._call(
            "GET",
            f"/{phone_number_id}",
            token=token,
            params={
                "fields": "id,display_phone_number,verified_name,quality_rating,"
                "code_verification_status,platform_type,status,messaging_limit_tier"
            },
        )

    def subscribe(self, token: str, waba_id: str) -> None:
        self._call("POST", f"/{waba_id}/subscribed_apps", token=token)

    def subscribed(self, token: str, waba_id: str) -> bool:
        data = self._call("GET", f"/{waba_id}/subscribed_apps", token=token)
        return bool(data.get("data"))

    def register(self, token: str, phone_number_id: str, pin: str) -> None:
        self._call(
            "POST",
            f"/{phone_number_id}/register",
            token=token,
            json={"messaging_product": "whatsapp", "pin": pin},
        )

    # ---- the way out -------------------------------------------------------------------------

    def unsubscribe(self, token: str, waba_id: str) -> None:
        self._call("DELETE", f"/{waba_id}/subscribed_apps", token=token)

    def deregister(self, token: str, phone_number_id: str) -> None:
        self._call("POST", f"/{phone_number_id}/deregister", token=token, json={})

    def revoke(self, token: str) -> None:
        """Withdraws the permissions the token holds for this app."""
        self._call("DELETE", "/me/permissions", token=token)

    # ---- message templates -------------------------------------------------------------------

    def list_templates(self, token: str, waba_id: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        params: dict[str, Any] | None = {
            "fields": "id,name,language,status,category,components,rejected_reason",
            "limit": 100,
        }
        path = f"/{waba_id}/message_templates"
        for _ in range(20):  # a bounded walk through the pages
            data = self._call("GET", path, token=token, params=params)
            rows += [row for row in data.get("data") or [] if isinstance(row, dict)]
            after = ((data.get("paging") or {}).get("cursors") or {}).get("after")
            if not after or not (data.get("paging") or {}).get("next"):
                break
            params = {**(params or {}), "after": after}
        return rows

    def create_template(self, token: str, waba_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._call("POST", f"/{waba_id}/message_templates", token=token, json=payload)

    def delete_template(self, token: str, waba_id: str, name: str) -> None:
        self._call("DELETE", f"/{waba_id}/message_templates", token=token, params={"name": name})

    # ---- transport ---------------------------------------------------------------------------

    def _call(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            response = self._client.request(
                method, self._root + path, params=params, json=json, headers=headers
            )
        except httpx.HTTPError:
            raise GraphError("Meta could not be reached", transient=True) from None
        try:
            body = response.json()
        except ValueError:
            body = {}
        if not response.is_success:
            error = body.get("error") if isinstance(body, dict) else None
            error = error if isinstance(error, dict) else {}
            message = str(error.get("error_user_msg") or error.get("message") or "")[:300]
            raise GraphError(
                message or f"Meta answered HTTP {response.status_code}",
                code=str(error.get("code", "")),
                status=response.status_code,
                transient=response.status_code == 429 or response.status_code >= 500,
            )
        if isinstance(body, dict):
            return body
        if isinstance(body, str) and body:  # an older token endpoint answers a bare string
            return {"access_token": body}
        return {}
