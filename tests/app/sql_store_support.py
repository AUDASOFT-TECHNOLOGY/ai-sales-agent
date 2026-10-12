"""The SQL operational store, dressed for the contract tests.

`SqlStoreUnderTest` is the real `SqlOperationalStore` over a real database, plus the same three
test helpers the in-memory fake has (`messages`, `audit_events`, `fail_next`), so the contract tests
written against the port run on both without change. The helpers read through the rows layer or wrap
a public method; they add nothing to what the store itself does.
"""

from sales_app.persistence.operational_rows import SqlOperationalRows
from sales_app.sourcing import store_codec as codec
from sales_app.sourcing.errors import StoreUnavailableError
from sales_app.sourcing.sql_store import SqlOperationalStore

PORT_CALLS = (
    "list_actions",
    "find_action_by_idempotency_key",
    "save_actions",
    "get_conversation",
    "append_messages",
    "append_audit_events",
    "get_facts",
    "append_facts",
    "get_states",
    "list_states",
    "save_states",
    "list_local_prospects",
    "get_local_prospect",
    "find_local_by_contact",
    "create_local_prospects",
    "verify",
)


class SqlStoreUnderTest(SqlOperationalStore):
    def __init__(self, engine, client_id: str) -> None:
        self.rows = SqlOperationalRows(engine, client_id)
        super().__init__(self.rows)
        self._failures: dict[str, list[Exception]] = {}
        for name in PORT_CALLS:
            self._guard(name)

    def _guard(self, name: str) -> None:
        real = getattr(self, name)

        def call(*args, **kwargs):
            queue = self._failures.get(name)
            if queue:
                raise queue.pop(0)
            return real(*args, **kwargs)

        setattr(self, name, call)

    def fail_next(self, method: str, error: Exception | None = None, *, times: int = 1) -> None:
        failure = error if error is not None else StoreUnavailableError("injected failure")
        self._failures.setdefault(method, []).extend([failure] * times)

    @property
    def messages(self):
        with self.rows._read() as connection:
            from sqlalchemy import select

            from sales_app.persistence.tables import op_messages

            query = (
                select(op_messages.c.document)
                .where(op_messages.c.client_id == self.client_id)
                .order_by(op_messages.c.seq)
            )
            return tuple(codec.message_from_doc(r.document) for r in connection.execute(query))

    @property
    def audit_events(self):
        return tuple(codec.audit_from_doc(d) for d in self.rows.list_audit())
