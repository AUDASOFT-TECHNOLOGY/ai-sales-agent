"""Google Sheets implementation of the `SalesRepository`, `FactsReader` and `FactsWriter` ports.
See docs/sheet-schema.md.

Sheets has no transactions and humans edit it while the agent runs, so this adapter is defensive:

- **Only changed cells are written**, never whole rows, so a human's edit to any other cell of the
  same row survives.
- **Compare-and-set:** before changing a cell it re-reads the row and refuses (raising
  `SheetConflictError`, writing nothing) if that cell no longer holds what the adapter last read.
  This is what stops a human's rejection or opt-out from being overwritten by a stale write.
- **Fail closed on bad data:** a row that cannot be interpreted is excluded from reads (so it is
  never contacted) and reported in `problems`; ambiguous ids are excluded, never guessed.
- **All-or-nothing per call:** every check runs before the first write request.

The spreadsheet is live and the only source of truth. Every read goes to the sheet; nothing is
answered from memory. The in-memory `_Snapshot`s exist only to notice that a cell changed between
the moment a record was read and the moment it is written back, never to answer a read.
"""

import logging
import threading
import uuid
from collections import Counter
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sales_core.contracts import (
    ActionStatus,
    AuditEvent,
    ClientId,
    Conversation,
    ConversationId,
    Fact,
    Message,
    Prospect,
    ProspectId,
    SalesAction,
    fact_identity,
)
from sales_integrations.google_sheets import codecs
from sales_integrations.google_sheets.api import SheetsApi
from sales_integrations.google_sheets.errors import (
    SheetConflictError,
    SheetDataError,
    SheetFieldConflictError,
    SheetLayoutError,
    SheetsApiError,
)
from sales_integrations.google_sheets.layout import (
    TAB_SPECS,
    Row,
    SheetTabs,
    Table,
    cell_ref,
    header_for,
    normalize_header,
    parse_table,
    quote_tab,
    validate_mapping,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RowProblem:
    tab: str
    row: int
    message: str


@dataclass(frozen=True, slots=True)
class FieldWrite:
    """Cells of one prospect's row to change, each only if it still holds what it was read as.

    `new` and `expected` hold the same column keys (`codecs.MIRRORED_KEYS`) with the canonical text
    of the value to put and of the value it replaces; an empty text is an empty cell.
    """

    prospect_id: str
    new: Mapping[str, str]
    expected: Mapping[str, str]


@dataclass(slots=True)
class _Snapshot[T]:
    """What the adapter last read for one record: the parsed object and its raw cells."""

    entity: T
    raw: dict[str, str]


class GoogleSheetsRepository:
    """One instance serves one client, bound to that client's spreadsheet."""

    def __init__(
        self,
        api: SheetsApi,
        *,
        client_id: ClientId,
        spreadsheet_id: str,
        timezone: ZoneInfo,
        tabs: SheetTabs | None = None,
        column_mapping: Mapping[str, str] | None = None,
        assign_missing_ids: bool = True,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._api = api
        self._client_id = client_id
        self._sid = spreadsheet_id
        self._zone = timezone
        self._tabs = tabs or SheetTabs()
        self._mapping = validate_mapping(column_mapping or {})
        self._assign_ids = assign_missing_ids
        self._new_id = id_factory or (lambda: "p-" + uuid.uuid4().hex[:10])
        self._lock = threading.RLock()
        self._prospect_snaps: dict[str, _Snapshot[Prospect]] = {}
        self._action_snaps: dict[str, _Snapshot[SalesAction]] = {}
        self._problems: dict[str, list[RowProblem]] = {}
        self._logged: set[RowProblem] = set()

    # ==== SalesRepository ================================================================

    @property
    def client_id(self) -> ClientId:
        return self._client_id

    @property
    def prospects_title(self) -> str:
        """The title of the prospects tab, which is what a `RowProblem.tab` names."""
        return self._tabs.title("prospects")

    @property
    def problems(self) -> tuple[RowProblem, ...]:
        """Rows found unusable at the last read of each tab (they are excluded from results)."""
        with self._lock:
            return tuple(p for problems in self._problems.values() for p in problems)

    # ---- prospects ---------------------------------------------------------------------

    def list_prospects(self) -> Sequence[Prospect]:
        with self._lock:
            table, accepted = self._prospects()
            self._prospect_snaps = {
                p.prospect_id: _Snapshot(p, codecs.row_raw(table, row)) for row, p in accepted
            }
            return [p for _, p in accepted]

    def get_prospect(self, prospect_id: ProspectId) -> Prospect | None:
        with self._lock:
            table, accepted = self._prospects()
            for row, prospect in accepted:
                if prospect.prospect_id == prospect_id:
                    snapshot = _Snapshot(prospect, codecs.row_raw(table, row))
                    self._prospect_snaps[prospect_id] = snapshot
                    return prospect
            return None

    def find_prospect_by_contact(
        self,
        *,
        phone: str | None = None,
        email: str | None = None,
        telegram_chat_id: str | None = None,
    ) -> Prospect | None:
        wanted_phone = codecs.digits(phone) if phone else ""
        wanted_email = email.strip().casefold() if email else ""
        wanted_chat = telegram_chat_id.strip() if telegram_chat_id else ""
        with self._lock:
            table, accepted = self._prospects()
            for row, prospect in accepted:
                contact = prospect.contact
                by_phone = (
                    wanted_phone and contact.phone and codecs.digits(contact.phone) == wanted_phone
                )
                by_email = (
                    wanted_email
                    and contact.email
                    and contact.email.strip().casefold() == wanted_email
                )
                by_chat = (
                    wanted_chat
                    and contact.telegram_chat_id
                    and contact.telegram_chat_id.strip() == wanted_chat
                )
                if by_phone or by_email or by_chat:
                    snapshot = _Snapshot(prospect, codecs.row_raw(table, row))
                    self._prospect_snaps[prospect.prospect_id] = snapshot
                    return prospect
            return None

    def create_prospects(self, prospects: Sequence[Prospect]) -> None:
        if not prospects:
            return
        with self._lock:
            for prospect in prospects:
                self._require_client(prospect.client_id, f"prospect {prospect.prospect_id!r}")
            table = self._read_table("prospects")
            if any(p.contact.telegram_chat_id for p in prospects):
                table = self._with_telegram_column(table)
            # Checked against every row, including ones the adapter cannot read (never contacted).
            ids = {codecs.text_of(table.cell(r, "prospect_id")) for r in table.rows}
            phones = {codecs.digits(codecs.text_of(table.cell(r, "phone"))) for r in table.rows}
            chats = {codecs.text_of(table.cell(r, "telegram_chat_id")) for r in table.rows}
            for prospect in prospects:
                chat = (prospect.contact.telegram_chat_id or "").strip()
                if chat and chat in chats:
                    raise ValueError(
                        f"prospect {prospect.prospect_id!r}: the Telegram chat is already in use"
                    )
                chats.add(chat)
                phone = codecs.digits(prospect.contact.phone or "")
                if prospect.prospect_id in ids:
                    raise ValueError(f"prospect {prospect.prospect_id!r} already exists")
                if phone and phone in phones:
                    raise ValueError(
                        f"prospect {prospect.prospect_id!r}: the phone number is already in use"
                    )
                ids.add(prospect.prospect_id)
                phones.add(phone)
            rows = [
                codecs.row_values(
                    table,
                    {"prospect_id": p.prospect_id, **codecs.encode_prospect(p, self._zone)},
                )
                for p in prospects
            ]
            # The append API inserts after the last row even if a person added one a moment ago.
            self._append("prospects", table.title, rows)

    def update_prospects(self, prospects: Sequence[Prospect]) -> None:
        with self._lock:
            incoming = {p.prospect_id: p for p in prospects}
            for prospect in incoming.values():
                self._require_client(prospect.client_id, f"prospect {prospect.prospect_id!r}")
            table = self._read_table("prospects")
            if any(p.contact.telegram_chat_id for p in incoming.values()):
                table = self._with_telegram_column(table)
            rows = self._rows_by_id(table, "prospect_id")
            plan = _WritePlan(table)
            for prospect_id, prospect in incoming.items():
                row = self._single_row(rows, prospect_id, "prospect")
                fresh = codecs.row_raw(table, row)
                snapshot = self._prospect_snaps.get(prospect_id) or self._snapshot_from(
                    lambda r=row: codecs.decode_prospect(table, r, self._client_id, self._zone),
                    fresh,
                    f"prospect {prospect_id!r}",
                )
                plan.update(
                    f"prospect {prospect_id!r}",
                    row,
                    fresh,
                    snapshot.raw,
                    codecs.encode_prospect(prospect, self._zone),
                    codecs.encode_prospect(snapshot.entity, self._zone),
                )
            plan.raise_on_conflict()
            self._write(plan)
            for prospect_id, prospect in incoming.items():
                self._prospect_snaps[prospect_id] = _Snapshot(
                    prospect, plan.raw_after(prospect_id, self)
                )

    def read_mirrored(self) -> dict[str, dict[str, Any]]:
        """The columns Revansa mirrors, as the sheet holds them now, by prospect id.

        Only rows the adapter accepts as prospects (the others are never contacted, so nothing is
        written for them). Dates and counts are typed; an empty cell is `None`.
        """
        with self._lock:
            _, accepted = self._prospects()
            return {p.prospect_id: codecs.mirrored_values(p) for _, p in accepted}

    def write_fields(self, writes: Sequence[FieldWrite]) -> None:
        """Change only the named cells, each only if it still holds what the caller read.

        Everything is checked before the first request: the columns exist, each id is on exactly one
        row, and every cell to change still holds its expected value (else `SheetFieldConflictError`
        and nothing is written). The changes then go out in one request, and a cell that already
        holds its new value is not sent. No other cell, and no row, is touched: a person's own
        columns and edits survive.

        **This is not an atomic compare-and-set.** Google Sheets offers none: the check reads the
        row, the write follows in a separate request, and a person who edits one of these cells in
        between is overwritten. The window is the time of one API round trip; nothing here can
        close it.
        """
        if not writes:
            return
        with self._lock:
            seen: set[str] = set()
            for write in writes:
                if write.prospect_id in seen:
                    raise ValueError("a prospect appears twice in one call")
                seen.add(write.prospect_id)
                if not write.new or set(write.new) != set(write.expected):
                    raise ValueError("a write needs the expected value of each cell it changes")
                if not set(write.new) <= codecs.MIRRORED_KEYS:
                    raise ValueError("this column is never written")
            table = self._read_table("prospects")
            wanted = {key for write in writes for key in write.new}
            if any(table.position(key) is None for key in wanted):
                raise SheetLayoutError(["a column to write to is missing from the prospects tab"])
            rows = self._rows_by_id(table, "prospect_id")
            updates: list[tuple[str, list[list[str]]]] = []
            for write in writes:
                row = self._single_row(rows, write.prospect_id, "prospect")
                stale = sorted(
                    key
                    for key, expected in write.expected.items()
                    if not codecs.same_cell(key, table.cell(row, key), expected, self._zone)
                )
                if stale:
                    raise SheetFieldConflictError(write.prospect_id, stale)
                for key, value in write.new.items():
                    if not codecs.same_cell(key, table.cell(row, key), value, self._zone):
                        position = table.position(key)
                        assert position is not None
                        updates.append((cell_ref(table.title, position, row.number), [[value]]))
            if updates:
                self._api.write(self._sid, updates)

    # ---- actions -----------------------------------------------------------------------

    def list_actions(
        self, statuses: Collection[ActionStatus] | None = None
    ) -> Sequence[SalesAction]:
        with self._lock:
            table, accepted = self._actions()
            present = {codecs.text_of(table.cell(r, "action_id")) for r in table.rows}
            self._action_snaps = {k: v for k, v in self._action_snaps.items() if k in present}
            returned = [(r, a) for r, a in accepted if statuses is None or a.status in statuses]
            for row, action in returned:
                self._action_snaps[action.action_id] = _Snapshot(action, codecs.row_raw(table, row))
            return [a for _, a in returned]

    def find_action_by_idempotency_key(self, key: str) -> SalesAction | None:
        with self._lock:
            table = self._read_table("actions")
            matches = [
                r for r in table.rows if codecs.text_of(table.cell(r, "idempotency_key")) == key
            ]
            if not matches:
                return None
            row = matches[0]
            try:
                action = codecs.decode_action(table, row, self._client_id, self._zone)
            except ValueError as error:
                # Never answer "not found" for a key that exists: that would allow a duplicate.
                raise SheetDataError(
                    f"Actions row {row.number} has key {key!r} but: {error}"
                ) from None
            self._action_snaps[action.action_id] = _Snapshot(action, codecs.row_raw(table, row))
            return action

    def save_actions(self, actions: Sequence[SalesAction]) -> None:
        with self._lock:
            incoming = {a.action_id: a for a in actions}
            for action in incoming.values():
                self._require_client(action.client_id, f"action {action.action_id!r}")
            table = self._read_table("actions")
            rows = self._rows_by_id(table, "action_id")
            self._require_unique_keys(table, incoming)

            plan = _WritePlan(table)
            for action_id, action in incoming.items():
                encoded = codecs.encode_action(action, self._zone)
                existing = rows.get(action_id)
                if not existing:
                    plan.insert(action_id, encoded)
                    continue
                row = self._single_row(rows, action_id, "action")
                fresh = codecs.row_raw(table, row)
                snapshot = self._action_snaps.get(action_id) or self._snapshot_from(
                    lambda r=row: codecs.decode_action(table, r, self._client_id, self._zone),
                    fresh,
                    f"action {action_id!r}",
                )
                plan.update(
                    f"action {action_id!r}",
                    row,
                    fresh,
                    snapshot.raw,
                    encoded,
                    codecs.encode_action(snapshot.entity, self._zone),
                )
            plan.raise_on_conflict()
            # New rows go through the append API, which inserts after the last row even if a
            # person added a row a moment ago; explicit row numbers would overwrite it.
            self._append("actions", table.title, plan.insert_rows())
            self._write(plan)
            for action_id, action in incoming.items():
                self._action_snaps[action_id] = _Snapshot(action, plan.raw_after(action_id, self))

    # ---- history -----------------------------------------------------------------------

    def get_conversation(self, prospect_id: ProspectId) -> Conversation:
        with self._lock:
            history = [m for m in self._read_messages() if m.prospect_id == prospect_id]
            conversation_id = (
                history[0].conversation_id
                if history
                else self._default_conversation_id(prospect_id)
            )
            try:
                return Conversation(
                    conversation_id=conversation_id,
                    client_id=self._client_id,
                    prospect_id=prospect_id,
                    messages=tuple(history),
                )
            except ValueError as error:
                raise SheetDataError(
                    f"Messages for {prospect_id!r} are inconsistent: {error}"
                ) from None

    def append_messages(self, messages: Sequence[Message]) -> None:
        if not messages:
            return
        with self._lock:
            existing = self._read_messages()  # fresh: also catches rows added by other means
            seen = {m.message_id for m in existing}
            assigned: dict[str, str] = {}
            for message in existing:
                assigned.setdefault(message.prospect_id, message.conversation_id)
            prospect_conversations: dict[str, str | None] | None = None
            for message in messages:
                self._require_client(message.client_id, f"message {message.message_id!r}")
                if message.message_id in seen:
                    raise ValueError(f"message {message.message_id!r} already exists")
                seen.add(message.message_id)
                if message.prospect_id not in assigned:
                    if prospect_conversations is None:
                        prospect_conversations = self._prospect_conversations()
                    own = prospect_conversations.get(message.prospect_id)
                    assigned[message.prospect_id] = own or message.conversation_id
                expected = assigned[message.prospect_id]
                if message.conversation_id != expected:
                    raise ValueError(
                        f"message {message.message_id!r} is in conversation "
                        f"{message.conversation_id!r}, but prospect {message.prospect_id!r} "
                        f"uses {expected!r}"
                    )
            header = self._read_header("messages")
            rows = [
                codecs.row_values(header, codecs.encode_message(m, self._zone)) for m in messages
            ]
            self._append("messages", header.title, rows)

    def append_audit_events(self, events: Sequence[AuditEvent]) -> None:
        if not events:
            return
        with self._lock:
            for event in events:
                self._require_client(event.client_id, f"audit event {event.event_type!r}")
            header = self._read_header("log")
            rows = [codecs.row_values(header, codecs.encode_audit(e, self._zone)) for e in events]
            self._api.append(self._sid, quote_tab(header.title), rows)

    # ---- facts (FactsReader, FactsWriter) ----------------------------------------------

    def get_facts(self, prospect_id: ProspectId) -> Sequence[Fact]:
        with self._lock:
            table = self._facts_table_if_any()
            if table is None:
                return []  # a spreadsheet set up before facts existed has none yet
            found = [f for f in self._read_facts(table) if f.stored.prospect_id == prospect_id]
            found.sort(key=lambda f: (f.stored.fact.recorded_at, f.row))
            return [f.stored.fact for f in found]

    def append_facts(self, prospect_id: ProspectId, facts: Sequence[Fact]) -> None:
        if not facts:
            return
        with self._lock:
            # Everything that can be refused is checked before the first write request.
            identified = [
                (fact_identity(self._client_id, prospect_id, fact), fact) for fact in facts
            ]
            encoded = {
                fact_id: codecs.encode_fact(fact_id, self._client_id, prospect_id, fact, self._zone)
                for fact_id, fact in identified
            }
            _, accepted = self._prospects()
            if not any(p.prospect_id == prospect_id for _, p in accepted):
                raise KeyError(f"unknown prospect {prospect_id!r}")
            table = self._facts_table_for_append()
            # Compared with every row, including ones the adapter cannot read or that belong to
            # another client: an identity that is on the sheet is never written a second time.
            known = {codecs.text_of(table.cell(r, "fact_id")) for r in table.rows}
            rows = []
            for fact_id, values in encoded.items():
                if fact_id not in known:
                    known.add(fact_id)
                    rows.append(codecs.row_values(table, values))
            # Append-only: existing rows are never touched, and the append API inserts after the
            # last row even if a person added one a moment ago.
            self._append("facts", table.title, rows)

    # ==== onboarding helpers (not part of the port) ======================================

    def verify(self, tabs: Sequence[str] | None = None) -> list[str]:
        """Check the spreadsheet against the schema. Empty list means ready; 'warning:' is soft.

        `tabs` limits the check to some tabs (by key, e.g. `("prospects",)`), for a spreadsheet that
        is only read as a source: the tabs of the store are then none of its business."""
        try:
            meta = self._api.metadata(self._sid)
        except SheetsApiError as error:
            return [str(error)]
        issues: list[str] = []
        for key in tabs if tabs is not None else TAB_SPECS:
            title = self._tabs.title(key)
            if title not in meta.titles:
                if TAB_SPECS[key].on_demand:
                    issues.append(
                        f"warning: tab {title!r} does not exist; it is created when first needed "
                        "(or by initialize_layout)"
                    )
                else:
                    issues.append(f"tab {title!r} does not exist")
                continue
            try:
                self._read_header(key)
            except SheetLayoutError as error:
                issues.extend(error.problems)
        if meta.timezone and meta.timezone != self._zone.key:
            issues.append(
                f"warning: the spreadsheet's time zone is {meta.timezone!r} but the client's is "
                f"{self._zone.key!r}; dates that Sheets itself stores use the spreadsheet's"
            )
        return issues

    def initialize_layout(self) -> list[str]:
        """Create missing tabs and add missing columns to the right. Never edits existing data."""
        return self._initialize(tuple(TAB_SPECS))

    def _initialize(self, keys: Sequence[str]) -> list[str]:
        done: list[str] = []
        meta = self._api.metadata(self._sid)
        missing = [self._tabs.title(k) for k in keys if self._tabs.title(k) not in meta.titles]
        if missing:
            self._api.add_tabs(self._sid, missing)
            done.extend(f"created tab {title!r}" for title in missing)
        for key in keys:
            spec = TAB_SPECS[key]
            title = self._tabs.title(key)
            (values,) = self._api.read(self._sid, [f"{quote_tab(title)}!1:1"])
            existing = list(values[0]) if values else []
            present = {normalize_header(h) for h in existing}
            wanted = [header_for(spec, c.field, self._mapping) for c in spec.columns]
            to_add = [h for h in wanted if normalize_header(h) not in present]
            if not to_add:
                continue
            start = len(existing)
            end = start + len(to_add) - 1
            span = f"{quote_tab(title)}!{_col(start)}1:{_col(end)}1"
            self._api.write(self._sid, [(span, [to_add])])
            done.append(f"added columns to {title!r}: {', '.join(to_add)}")
        return done

    # ==== internals ======================================================================

    def _require_client(self, client_id: ClientId, what: str) -> None:
        if client_id != self._client_id:
            raise ValueError(f"{what} belongs to client {client_id!r}, not {self._client_id!r}")

    def _read_table(self, key: str) -> Table:
        title = self._tabs.title(key)
        (values,) = self._api.read(self._sid, [quote_tab(title)])
        return parse_table(TAB_SPECS[key], title, values, self._mapping)

    def _with_telegram_column(self, table: Table) -> Table:
        """The prospects table, having the `telegram_chat_id` column added to the right of the
        client's own if the sheet has none. A chat id that could not be written would be lost
        without a word, and the person would never be recognised again: the column is the only
        place the link lives, so it is created rather than skipped."""
        if "telegram_chat_id" in table.index:
            return table
        header = header_for(TAB_SPECS["prospects"], "telegram_chat_id", self._mapping)
        span = f"{quote_tab(table.title)}!{_col(len(table.headers))}1"
        self._api.write(self._sid, [(span, [[header]])])
        return self._read_table("prospects")

    def _read_header(self, key: str) -> Table:
        title = self._tabs.title(key)
        (values,) = self._api.read(self._sid, [f"{quote_tab(title)}!1:1"])
        return parse_table(TAB_SPECS[key], title, values, self._mapping, with_rows=False)

    def _append(self, key: str, title: str, rows: list[list[str]]) -> None:
        if rows:
            self._api.append(self._sid, quote_tab(title), rows)

    def _set_problems(self, key: str, problems: list[RowProblem]) -> None:
        self._problems[key] = problems
        for problem in problems:
            if problem not in self._logged:
                self._logged.add(problem)
                log.warning("%s row %d excluded: %s", problem.tab, problem.row, problem.message)

    # ---- prospects tab ---------------------------------------------------------------

    def _prospects(self) -> tuple[Table, list[tuple[Row, Prospect]]]:
        table = self._read_table("prospects")
        self._backfill_ids(table)
        ids = Counter(codecs.text_of(table.cell(r, "prospect_id")) for r in table.rows)
        accepted: list[tuple[Row, Prospect]] = []
        problems: list[RowProblem] = []
        first_by_phone: dict[str, tuple[int, str]] = {}
        for row in table.rows:
            prospect_id = codecs.text_of(table.cell(row, "prospect_id"))
            if not prospect_id:
                continue  # a row with data only in the client's own columns
            if ids[prospect_id] > 1:
                problems.append(
                    RowProblem(table.title, row.number, f"duplicate prospect_id {prospect_id!r}")
                )
                continue
            try:
                prospect = codecs.decode_prospect(table, row, self._client_id, self._zone)
            except ValueError as error:
                problems.append(RowProblem(table.title, row.number, str(error)))
                continue
            phone = codecs.digits(prospect.contact.phone or "")
            if phone and phone in first_by_phone:
                first_row, first_id = first_by_phone[phone]
                problems.append(
                    RowProblem(
                        table.title,
                        row.number,
                        f"same phone number as row {first_row} ({first_id!r}); "
                        "only the first row is contacted",
                    )
                )
                continue
            if phone:
                first_by_phone[phone] = (row.number, prospect_id)
            accepted.append((row, prospect))
        self._set_problems("prospects", problems)
        return table, accepted

    def _backfill_ids(self, table: Table) -> None:
        """Give a `prospect_id` to rows a human added without one (a stable, generated id)."""
        if not self._assign_ids:
            return
        position = table.index["prospect_id"]
        updates: list[tuple[str, list[list[str]]]] = []
        for row in table.rows:
            if codecs.text_of(table.cell(row, "prospect_id")):
                continue
            identity = any(
                codecs.text_of(table.cell(row, f)) for f in ("display_name", "phone", "email")
            )
            if not identity:
                continue
            new_id = self._new_id()
            while len(row.cells) <= position:
                row.cells.append("")
            row.cells[position] = new_id
            updates.append((cell_ref(table.title, position, row.number), [[new_id]]))
        if updates:
            self._api.write(self._sid, updates)

    def _prospect_conversations(self) -> dict[str, str | None]:
        """Each prospect's own conversation_id, without touching the change-detection snapshots."""
        _, accepted = self._prospects()
        return {p.prospect_id: p.conversation_id for _, p in accepted}

    def _default_conversation_id(self, prospect_id: ProspectId) -> ConversationId:
        own = self._prospect_conversations().get(prospect_id)
        return own or ConversationId(f"conv-{prospect_id}")

    # ---- actions tab -----------------------------------------------------------------

    def _actions(self) -> tuple[Table, list[tuple[Row, SalesAction]]]:
        table = self._read_table("actions")
        ids = Counter(codecs.text_of(table.cell(r, "action_id")) for r in table.rows)
        accepted: list[tuple[Row, SalesAction]] = []
        problems: list[RowProblem] = []
        for row in table.rows:
            action_id = codecs.text_of(table.cell(row, "action_id"))
            if not action_id or ids[action_id] > 1:
                reason = (
                    "has no action_id" if not action_id else f"duplicate action_id {action_id!r}"
                )
                problems.append(RowProblem(table.title, row.number, reason))
                continue
            try:
                action = codecs.decode_action(table, row, self._client_id, self._zone)
            except ValueError as error:
                problems.append(RowProblem(table.title, row.number, str(error)))
                continue
            accepted.append((row, action))
        self._set_problems("actions", problems)
        return table, accepted

    def _require_unique_keys(self, table: Table, incoming: Mapping[str, SalesAction]) -> None:
        """Idempotency keys are unique across ALL rows, including rows the adapter cannot read."""
        owners: dict[str, str] = {}
        for row in table.rows:
            action_id = codecs.text_of(table.cell(row, "action_id"))
            key = codecs.text_of(table.cell(row, "idempotency_key"))
            if key and action_id not in incoming:
                owners.setdefault(key, action_id)
        for action_id, action in incoming.items():
            if owners.setdefault(action.idempotency_key, action_id) != action_id:
                raise ValueError(f"idempotency key {action.idempotency_key!r} is already in use")

    # ---- messages tab ----------------------------------------------------------------

    def _read_messages(self) -> list[Message]:
        """The whole history, read fresh. Unreadable and repeated rows are excluded and reported."""
        table = self._read_table("messages")
        loaded: list[Message] = []
        seen: set[str] = set()
        problems: list[RowProblem] = []
        for row in table.rows:
            try:
                message = codecs.decode_message(table, row, self._client_id, self._zone)
            except ValueError as error:
                problems.append(RowProblem(table.title, row.number, str(error)))
                continue
            if message.message_id in seen:
                reason = f"duplicate message_id {message.message_id!r}"
                problems.append(RowProblem(table.title, row.number, reason))
                continue
            seen.add(message.message_id)
            loaded.append(message)
        self._set_problems("messages", problems)
        return loaded

    # ---- facts tab -------------------------------------------------------------------

    def _facts_table_if_any(self) -> Table | None:
        """The Facts tab, or None when the spreadsheet has none (a read creates nothing)."""
        try:
            return self._read_table("facts")
        except SheetsApiError as error:
            if error.status != 400:
                raise
            try:
                absent = self._tabs.title("facts") not in self._api.metadata(self._sid).titles
            except SheetsApiError:
                raise error from None
            if not absent:
                raise
            self._set_problems("facts", [])
            return None

    def _facts_table_for_append(self) -> Table:
        """The Facts tab; created, or completed with its columns, if it is not ready yet."""
        try:
            return self._read_table("facts")
        except (SheetsApiError, SheetLayoutError) as error:
            if isinstance(error, SheetsApiError) and error.status != 400:
                raise
        self._initialize(("facts",))
        return self._read_table("facts")

    def _read_facts(self, table: Table) -> list["_FactRow"]:
        """This client's readable observations. Rows of other clients are not ours: skipped
        without a word. Unreadable and repeated rows are excluded and reported."""
        loaded: list[_FactRow] = []
        seen: set[str] = set()
        problems: list[RowProblem] = []
        for row in table.rows:
            owner = codecs.text_of(table.cell(row, "client_id"))
            if owner and owner != self._client_id:
                continue
            try:
                if not owner:
                    raise ValueError("client_id: is empty")
                stored = codecs.decode_fact(table, row, self._zone)
            except ValueError as error:
                problems.append(RowProblem(table.title, row.number, str(error)))
                continue
            if stored.fact_id in seen:
                reason = f"duplicate fact_id {stored.fact_id!r}"
                problems.append(RowProblem(table.title, row.number, reason))
                continue
            seen.add(stored.fact_id)
            loaded.append(_FactRow(row.number, stored))
        self._set_problems("facts", problems)
        return loaded

    # ---- shared write machinery ------------------------------------------------------

    @staticmethod
    def _rows_by_id(table: Table, id_field: str) -> dict[str, list[Row]]:
        rows: dict[str, list[Row]] = {}
        for row in table.rows:
            value = codecs.text_of(table.cell(row, id_field))
            if value:
                rows.setdefault(value, []).append(row)
        return rows

    @staticmethod
    def _single_row(rows: Mapping[str, list[Row]], record_id: str, what: str) -> Row:
        found = rows.get(record_id, [])
        if not found:
            raise KeyError(f"unknown {what} {record_id!r}")
        if len(found) > 1:
            raise SheetDataError(f"{what} {record_id!r} appears in several rows; fix the sheet")
        return found[0]

    @staticmethod
    def _snapshot_from(
        decode: Callable[[], Any], fresh: dict[str, str], what: str
    ) -> _Snapshot[Any]:
        try:
            return _Snapshot(decode(), dict(fresh))
        except ValueError as error:
            raise SheetDataError(f"{what} cannot be read: {error}") from None

    def _write(self, plan: "_WritePlan") -> None:
        updates = plan.updates(self)
        if updates:
            self._api.write(self._sid, updates)


@dataclass(frozen=True, slots=True)
class _FactRow:
    row: int
    stored: codecs.StoredFact


class _WritePlan:
    """The cells to change and rows to add for one repository call, checked before any write."""

    def __init__(self, table: Table) -> None:
        self._table = table
        self._cells: list[tuple[str, Row, str, str]] = []  # (record, row, key, new value)
        self._inserts: list[tuple[str, dict[str, str]]] = []
        self._fresh: dict[str, dict[str, str]] = {}
        self._conflicts: list[str] = []

    def update(
        self,
        record: str,
        row: Row,
        fresh: dict[str, str],
        base_raw: dict[str, str],
        new: dict[str, str],
        old: dict[str, str],
    ) -> None:
        record_id = record.split("'")[1]
        self._fresh[record_id] = dict(fresh)
        for key, value in new.items():
            if old.get(key) == value or self._table.position(key) is None:
                continue  # unchanged, or the sheet has no such column
            current = fresh.get(key, "")
            if current != base_raw.get(key, "") and current != value:
                self._conflicts.append(
                    f"{record}: {key!r} changed to {current!r} since it was read "
                    f"(expected {base_raw.get(key, '')!r})"
                )
                continue
            if current != value:
                self._cells.append((record_id, row, key, value))
                self._fresh[record_id][key] = value

    def insert(self, record_id: str, encoded: dict[str, str]) -> None:
        self._inserts.append((record_id, encoded))
        self._fresh[record_id] = dict(encoded)

    def raise_on_conflict(self) -> None:
        if self._conflicts:
            raise SheetConflictError("; ".join(self._conflicts))

    def updates(self, repo: GoogleSheetsRepository) -> list[tuple[str, list[list[str]]]]:
        table = self._table
        writes: list[tuple[str, list[list[str]]]] = []
        for _, row, key, value in self._cells:
            position = table.position(key)
            assert position is not None
            writes.append((cell_ref(table.title, position, row.number), [[value]]))
        return writes

    def insert_rows(self) -> list[list[str]]:
        return [codecs.row_values(self._table, encoded) for _, encoded in self._inserts]

    def raw_after(self, record_id: str, repo: GoogleSheetsRepository) -> dict[str, str]:
        return dict(self._fresh.get(record_id, {}))


def _col(position: int) -> str:
    from sales_integrations.google_sheets.layout import column_letter

    return column_letter(position)
