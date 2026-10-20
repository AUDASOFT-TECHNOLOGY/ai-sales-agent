"""Records every state-changing Admin request in the central audit log (spec 12.4).

A pure ASGI middleware, so it adds nothing to the routes and cannot be forgotten by a new one: any
`POST/PUT/PATCH/DELETE` under `/admin/v1` made with a valid Admin session is written with who (and
role), which client (from the path), what (a stable action name), and how it ended (from the status:
`success`, `denied` 403, `rejected` other 4xx, `error` 5xx). Requests without a valid session, the
login/logout and the read-only methods are not recorded.

The request body is kept as the `after` document **only** where it holds no credential by nature
(`_BODY_ALLOWED`); every other route keeps no body at all, and whatever is kept goes through
`audit_log.redact`. The body is observed while it streams to the route (never consumed or altered).
A failure to write the audit never changes the response.
"""

import json
import re
from http.cookies import CookieError, SimpleCookie
from typing import Any

from sales_app.audit_log import (
    OUTCOME_DENIED,
    OUTCOME_ERROR,
    OUTCOME_REJECTED,
    OUTCOME_SUCCESS,
)
from sales_app.bootstrap import Application

PREFIX = "/admin/v1"
COOKIE_NAME = "revansa_admin_session"
MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})
MAX_BODY = 16_384

# (method, route template without prefix) -> stable action name. Anything else is named
# "<method>:<template>" so a new route is audited without being listed here.
_ACTIONS: tuple[tuple[str, re.Pattern[str], str], ...] = tuple(
    (method, re.compile(pattern), name)
    for method, pattern, name in (
        ("POST", r"^/clients$", "client.create"),
        ("PATCH", r"^/clients/\{client_id\}$", "client.update"),
        ("PUT", r"^/clients/\{client_id\}/llm$", "client.llm_assign"),
        ("PUT", r"^/clients/\{client_id\}/source$", "client.source_update"),
        ("PUT", r"^/clients/\{client_id\}/plan$", "client.plan_change"),
        ("PUT", r"^/clients/\{client_id\}/send-cap$", "client.send_cap_set"),
        ("DELETE", r"^/clients/\{client_id\}/send-cap$", "client.send_cap_remove"),
        ("PUT", r"^/clients/\{client_id\}/channels$", "client.channels_set"),
        ("PUT", r"^/settings/channels$", "settings.channels_set"),
        ("POST", r"^/plans$", "plan.create"),
        ("PUT", r"^/plans/\{plan_id\}$", "plan.update"),
        ("POST", r"^/invoices/generate$", "invoice.generate"),
        ("PUT", r"^/invoices/\{invoice_id\}/status$", "invoice.status_change"),
        ("PUT", r"^/settings/writeback$", "settings.writeback_set"),
        ("PUT", r"^/settings/llm/pricing$", "settings.llm_pricing_set"),
        ("DELETE", r"^/settings/llm/pricing/.*$", "settings.llm_pricing_remove"),
        ("PUT", r"^/settings/costs/rates$", "settings.cost_rate_set"),
        ("DELETE", r"^/settings/costs/rates/.*$", "settings.cost_rate_remove"),
        ("POST", r"^/costs/entries$", "costs.entry_add"),
        ("DELETE", r"^/costs/entries/.*$", "costs.entry_remove"),
        ("POST", r"^/costs/materialize$", "costs.materialize"),
        ("POST", r"^/settings/openai/models$", "settings.llm_model_add"),
        ("PUT", r"^/settings/openai/models/\{model_id\}$", "settings.llm_model_update"),
        ("DELETE", r"^/settings/openai/models/\{model_id\}$", "settings.llm_model_remove"),
        ("PUT", r"^/settings/integrations/.*$", "integration.platform_set"),
        ("DELETE", r"^/settings/integrations/.*$", "integration.platform_remove"),
        ("PUT", r"^/clients/\{client_id\}/integrations/.*$", "integration.client_set"),
        ("DELETE", r"^/clients/\{client_id\}/integrations/.*$", "integration.client_remove"),
        ("POST", r"^/clients/\{client_id\}/email-activation/(\w+)$", "email_activation.move"),
        ("POST", r"^/clients/\{client_id\}/whatsapp/(\w+)$", "whatsapp.change"),
        ("PUT", r"^/clients/\{client_id\}/whatsapp/.*$", "whatsapp.change"),
        ("DELETE", r"^/clients/\{client_id\}/whatsapp/.*$", "whatsapp.change"),
    )
)
# Routes whose body holds no credential by nature and is useful as the "after" of the entry.
_BODY_ALLOWED = re.compile(
    r"^/(clients(/\{client_id\}(/(llm|source|plan|send-cap|channels))?)?|plans(/\{plan_id\})?"
    r"|invoices/.*|settings/(channels|writeback|llm/pricing|costs/rates)|costs/(entries|materialize))$"
)
_SKIPPED = re.compile(r"^/(auth/|notifications)")  # reading a bell is not an operation


def action_name(method: str, template: str) -> str:
    match = None
    for wanted, pattern, name in _ACTIONS:
        if wanted == method:
            match = pattern.match(template)
            if match:
                if match.groups():
                    return f"{name}.{match.group(1)}"
                return name
    return f"{method.lower()}:{template}"


def outcome_of(status: int) -> str:
    if status < 400:
        return OUTCOME_SUCCESS
    if status == 403:
        return OUTCOME_DENIED
    return OUTCOME_REJECTED if status < 500 else OUTCOME_ERROR


def _session_token(headers: list[tuple[bytes, bytes]]) -> str | None:
    for key, value in headers:
        if key == b"cookie":
            jar: SimpleCookie = SimpleCookie()
            try:
                jar.load(value.decode("latin-1"))
            except CookieError:
                return None
            morsel = jar.get(COOKIE_NAME)
            return morsel.value if morsel else None
    return None


class AuditMiddleware:
    def __init__(self, app: Any, application: Application) -> None:
        self.app = app
        self._application = application

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        path = scope.get("path", "")
        if (
            scope["type"] != "http"
            or scope.get("method") not in MUTATING
            or not path.startswith(PREFIX + "/")
            or _SKIPPED.match(path[len(PREFIX) :])
        ):
            await self.app(scope, receive, send)
            return

        chunks: list[bytes] = []
        size = 0

        async def watch_receive() -> Any:
            nonlocal size
            message = await receive()
            if message["type"] == "http.request" and size <= MAX_BODY:
                piece = message.get("body", b"")
                size += len(piece)
                if size <= MAX_BODY:
                    chunks.append(piece)
            return message

        status = 500

        async def watch_send(message: Any) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
            await send(message)

        try:
            await self.app(scope, watch_receive, watch_send)
        finally:
            self._record(scope, status, b"".join(chunks) if size <= MAX_BODY else None)

    def _record(self, scope: dict, status: int, body: bytes | None) -> None:
        try:
            token = _session_token(scope.get("headers", []))
            admin = self._application.admin_auth.authenticate(token) if token else None
            if admin is None:
                return
            route = scope.get("route")
            template = getattr(route, "path", None) or scope["path"]
            template = template[len(PREFIX) :] if template.startswith(PREFIX) else template
            params = scope.get("path_params") or {}
            client_id = params.get("client_id") if isinstance(params.get("client_id"), str) else None
            document = _document(body)
            if client_id is None and isinstance(document, dict):
                found = document.get("client_id")
                client_id = found if isinstance(found, str) else None
            after = document if _BODY_ALLOWED.match(template) else None
            self._application.audit.record(
                actor=admin.username,
                actor_role=admin.role.value,
                action=action_name(scope["method"], template),
                target=scope["path"][len(PREFIX) :],
                client_id=client_id,
                outcome=outcome_of(status),
                after=after,
            )
        except Exception:  # noqa: BLE001 - the audit never changes a response
            return


def _document(body: bytes | None) -> Any:
    if not body:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None
