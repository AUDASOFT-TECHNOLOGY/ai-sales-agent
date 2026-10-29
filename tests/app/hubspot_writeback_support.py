"""A simulated HubSpot account for the write-back tests: nothing here reaches the network.

`FakeHubSpot` is a stateful `httpx.MockTransport` handler: it holds contacts and property
definitions, answers the reads the connector makes, applies the `PATCH`es it accepts (an enumeration
refuses a value it does not offer, as HubSpot does), keeps every request, and can be told to fail
the next calls in the ways HubSpot does (401, 403, 429 with Retry-After, 5xx, an answer lost after
the write was applied).
"""

import httpx

TOKEN_A = "pat-eu1-" + "aaaaaaaa-1111-1111-1111-111111111111"
TOKEN_B = "pat-eu1-" + "bbbbbbbb-2222-2222-2222-222222222222"

STATUS_MAPPING = {
    "NEW": "new",
    "OPEN": "new",
    "BAD_TIMING": "paused",
    "UNQUALIFIED": "closed",
    "DO_NOT_CONTACT": "opted_out",
}
PROPERTIES = {
    "last_contacted_at": "revansa_last_contact",
    "last_inbound_at": "revansa_last_reply",
    "follow_up_count": "revansa_followups",
    "next_action_type": "revansa_next_action",
    "next_action_due_at": "revansa_next_due",
}
DEFINITIONS = {
    "hs_lead_status": {
        "type": "enumeration",
        "options": [{"value": v} for v in ("NEW", "OPEN", "BAD_TIMING", "UNQUALIFIED")]
        + [{"value": "DO_NOT_CONTACT"}],
    },
    "revansa_last_contact": {"type": "datetime"},
    "revansa_last_reply": {"type": "datetime"},
    "revansa_followups": {"type": "number"},
    "revansa_next_action": {"type": "string"},
    "revansa_next_due": {"type": "datetime"},
}


def writeback_block(**overrides):
    block = {"status": True, "properties": dict(PROPERTIES)}
    return {**block, **overrides}


def settings(**overrides):
    return {"status_mapping": dict(STATUS_MAPPING), "writeback": writeback_block(), **overrides}


class FakeHubSpot:
    def __init__(self, token=TOKEN_A, *, definitions=None):
        self.token = token
        self.contacts: dict[str, dict[str, str | None]] = {}
        self.definitions = {k: dict(v) for k, v in (definitions or DEFINITIONS).items()}
        self.requests: list[httpx.Request] = []
        self.bodies: list[bytes] = []
        self.failures: list[tuple[str, httpx.Response]] = []  # (method, answer) for the next calls
        self.lose_answer_of_next_patch = False
        self.before_patch = None  # a hook: a person edits the contact right before the write

    # ---- helpers for tests ---------------------------------------------------------------------

    def add(self, identifier, status="NEW", **properties):
        self.contacts[identifier] = {
            "firstname": "Ada",
            "lastname": "Lovelace",
            "email": f"ada{identifier}@example.com",
            "mobilephone": "+216 99 888 777",
            "hs_lead_status": status,
            "hs_email_optout": None,
            **properties,
        }

    def fail(self, method, status, **headers):
        self.failures.append(
            (method, httpx.Response(status, json={"message": "secret-detail"}, headers=headers))
        )

    def methods(self):
        return [r.method for r in self.requests]

    @property
    def patches(self):
        return [r for r in self.requests if r.method == "PATCH"]

    def value(self, identifier, name):
        return self.contacts[identifier].get(name)

    def transport(self):
        return httpx.MockTransport(self)

    def client_factory(self):
        transport = self.transport()
        return lambda: httpx.Client(transport=transport)

    # ---- the API ---------------------------------------------------------------------------------

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        self.bodies.append(request.content)
        if request.headers.get("Authorization") != f"Bearer {self.token}":
            return httpx.Response(401, json={"message": "no"})
        for index, (method, answer) in enumerate(self.failures):
            if method == request.method:
                del self.failures[index]
                return answer
        path = request.url.path
        last = path.rsplit("/", 1)[-1]
        if path.startswith("/crm/v3/properties/contacts/"):
            found = self.definitions.get(last)
            if found is None:
                return httpx.Response(404, json={})
            return httpx.Response(200, json={"name": last, **found})
        if request.method == "PATCH":
            return self._patch(last, request)
        wanted = [p for p in request.url.params.get("properties", "").split(",") if p]
        if last.isdigit():
            if last not in self.contacts:
                return httpx.Response(404, json={})
            return httpx.Response(200, json=self._shape(last, wanted))
        results = [self._shape(i, wanted) for i in self.contacts]
        return httpx.Response(200, json={"results": results})

    def _shape(self, identifier, wanted):
        properties = {w: self.contacts[identifier].get(w) for w in wanted}
        return {"id": identifier, "properties": properties}

    def _patch(self, identifier, request):
        import json

        if self.before_patch is not None:
            hook, self.before_patch = self.before_patch, None
            hook(self)
        if identifier not in self.contacts:
            return httpx.Response(404, json={})
        values = json.loads(request.content)["properties"]
        for name, value in values.items():
            offered = [o["value"] for o in self.definitions.get(name, {}).get("options", [])]
            if offered and value not in offered:
                return httpx.Response(400, json={"message": "invalid option"})
        self.contacts[identifier].update({k: (v or None) for k, v in values.items()})
        if self.lose_answer_of_next_patch:
            self.lose_answer_of_next_patch = False
            return httpx.Response(502, json={})  # applied, but the answer is an error
        return httpx.Response(200, json={"id": identifier, "properties": values})
