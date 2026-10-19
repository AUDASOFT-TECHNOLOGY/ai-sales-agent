"""The real MTProto client behind `AccountClient`: Telethon, loaded only when it is used.

One operation = one short connection: connect with the stored session, do the work, disconnect. No
connection is kept between operations, so nothing survives a restart that has to be rebuilt, and
there is no long-lived socket to leak. Each call runs its own event loop (`asyncio.run`), so it
must be called from a thread that has none running (a scheduler thread or the API's thread pool,
never inside a coroutine).

**Not validated against the real Telegram.** It was written from Telethon's documented API and is
exercised only through the port, with fakes, in this repository. The operator proves it once with a
throw-away account before any client uses it (see `docs/telegram-user-account.md`).

An exception that leaves while a message may already have left (a timeout during a send) is NOT
translated: it propagates as it is, so the Engine leaves the action for a human instead of retrying
a message that may have arrived.
"""

import asyncio
import importlib.util
import random
from collections.abc import Awaitable, Callable
from contextlib import suppress
from datetime import UTC
from typing import Any

from sales_integrations.telegram.account import (
    AUTH_LOST,
    BAD_CODE,
    BAD_PASSWORD,
    BAD_PHONE,
    BLOCKED,
    CODE_EXPIRED,
    FLOOD_WAIT,
    LOGIN_FLOOD,
    NOT_FOUND,
    PEER_FLOOD,
    REJECTED,
    UNREACHABLE,
    Accept,
    AccountFailure,
    Batch,
    Identity,
    Incoming,
    LoginTicket,
    PasswordNeeded,
    Sent,
    SignedIn,
)

CONNECT_TIMEOUT = 20.0
_DIALOGS = 200  # the most recent private conversations looked at in one read

# Telethon's error class names, by what they mean here. Matched by name so this module imports
# nothing of Telethon until a call is made.
_KINDS = {
    "PhoneNumberInvalidError": BAD_PHONE,
    "PhoneNumberBannedError": BAD_PHONE,
    "PhoneCodeInvalidError": BAD_CODE,
    "PhoneCodeEmptyError": BAD_CODE,
    "PhoneCodeExpiredError": CODE_EXPIRED,
    "PasswordHashInvalidError": BAD_PASSWORD,
    "PhoneNumberFloodError": LOGIN_FLOOD,
    "PhonePasswordFloodError": LOGIN_FLOOD,
    "FloodWaitError": FLOOD_WAIT,
    "SlowModeWaitError": FLOOD_WAIT,
    "FloodError": FLOOD_WAIT,
    "PeerFloodError": PEER_FLOOD,
    "UserPrivacyRestrictedError": BLOCKED,
    "UserIsBlockedError": BLOCKED,
    "InputUserDeactivatedError": BLOCKED,
    "ChatWriteForbiddenError": BLOCKED,
    "UserIsBotError": BLOCKED,
    "UsernameNotOccupiedError": NOT_FOUND,
    "PeerIdInvalidError": NOT_FOUND,
    "AuthKeyUnregisteredError": AUTH_LOST,
    "AuthKeyInvalidError": AUTH_LOST,
    "AuthKeyDuplicatedError": AUTH_LOST,
    "SessionRevokedError": AUTH_LOST,
    "SessionExpiredError": AUTH_LOST,
    "UserDeactivatedError": AUTH_LOST,
    "UserDeactivatedBanError": AUTH_LOST,
    "ApiIdInvalidError": REJECTED,
    "ApiIdPublishedFloodError": REJECTED,
}


def telethon_available() -> bool:
    """Whether the library is installed in this deployment (`pip install telethon`)."""
    return importlib.util.find_spec("telethon") is not None


def _translate(error: BaseException) -> BaseException:
    if isinstance(error, AccountFailure):
        return error
    kind = _KINDS.get(type(error).__name__)
    if kind is not None:
        seconds = getattr(error, "seconds", None)
        return AccountFailure(kind, retry_after=seconds if isinstance(seconds, int) else None)
    if isinstance(error, (ConnectionError, TimeoutError, asyncio.TimeoutError, OSError)):
        return AccountFailure(UNREACHABLE)
    return AccountFailure(REJECTED)  # an error we do not know: refused, never its words


def _phone_of(entity: Any) -> str | None:
    phone = (getattr(entity, "phone", None) or "").strip()
    if not phone:
        return None
    return phone if phone.startswith("+") else f"+{phone}"


def _is_person(dialog: Any) -> bool:
    entity = dialog.entity
    return bool(
        dialog.is_user
        and not getattr(entity, "bot", False)
        and not getattr(entity, "is_self", False)
        and not getattr(entity, "deleted", False)
    )


class TelethonAccount:
    """`AccountClient` over Telethon, for one session (None: not signed in yet)."""

    def __init__(
        self,
        api_id: int,
        api_hash: str,
        session: str | None = None,
        *,
        timeout: float = CONNECT_TIMEOUT,
    ) -> None:
        self._api_id = api_id
        self._api_hash = api_hash
        self._session = session
        self._timeout = timeout

    def __repr__(self) -> str:
        return "TelethonAccount()"

    # ---- plumbing -----------------------------------------------------------------------------

    def _run(
        self,
        work: Callable[[Any], Awaitable[Any]],
        *,
        session: str | None = None,
        signed_in: bool = True,
        sending: bool = False,
    ) -> Any:
        """Connect, run `work(client)`, disconnect. `signed_in`: refuse a session that is not
        authorised. `sending`: an error after the connection is not translated (see the module)."""
        held = self._session if session is None else session

        async def main() -> Any:
            from telethon import TelegramClient  # imported late on purpose
            from telethon.sessions import StringSession

            client = TelegramClient(
                StringSession(held or ""),
                self._api_id,
                self._api_hash,
                device_model="Revansa",
                connection_retries=1,
                request_retries=1,
                timeout=int(self._timeout),
            )
            try:
                try:
                    await asyncio.wait_for(client.connect(), self._timeout)
                except Exception as error:  # noqa: BLE001
                    raise _translate(error) from None
                try:
                    if signed_in and not await client.is_user_authorized():
                        raise AccountFailure(AUTH_LOST)
                    return await work(client)
                except AccountFailure:
                    raise
                except Exception as error:
                    if sending and _KINDS.get(type(error).__name__) is None:
                        raise  # the outcome is unknown: let it leave as it is
                    raise _translate(error) from None
            finally:
                with suppress(Exception):
                    await client.disconnect()

        return asyncio.run(main())

    # ---- login --------------------------------------------------------------------------------

    def request_code(self, phone: str) -> LoginTicket:
        async def work(client: Any) -> LoginTicket:
            sent = await client.send_code_request(phone)
            return LoginTicket(phone_code_hash=sent.phone_code_hash, session=client.session.save())

        return self._run(work, session="", signed_in=False)

    def sign_in_code(self, ticket: LoginTicket, phone: str, code: str) -> SignedIn:
        async def work(client: Any) -> SignedIn:
            from telethon.errors import SessionPasswordNeededError

            try:
                await client.sign_in(phone=phone, code=code, phone_code_hash=ticket.phone_code_hash)
            except SessionPasswordNeededError:
                raise PasswordNeeded(
                    LoginTicket(ticket.phone_code_hash, client.session.save())
                ) from None
            return await self._signed_in(client)

        return self._run(work, session=ticket.session, signed_in=False)

    def sign_in_password(self, ticket: LoginTicket, password: str) -> SignedIn:
        async def work(client: Any) -> SignedIn:
            await client.sign_in(password=password)
            return await self._signed_in(client)

        return self._run(work, session=ticket.session, signed_in=False)

    @staticmethod
    async def _signed_in(client: Any) -> SignedIn:
        me = await client.get_me()
        name = " ".join(p for p in (me.first_name, me.last_name) if p) or "Telegram"
        return SignedIn(session=client.session.save(), identity=Identity(str(me.id), name))

    # ---- the account --------------------------------------------------------------------------

    def me(self) -> Identity:
        async def work(client: Any) -> Identity:
            me = await client.get_me()
            return Identity(str(me.id), " ".join(p for p in (me.first_name, me.last_name) if p))

        return self._run(work)

    def log_out(self) -> None:
        async def work(client: Any) -> None:
            await client.log_out()

        self._run(work)

    # ---- people -------------------------------------------------------------------------------

    @staticmethod
    async def _entity(client: Any, user_id: int) -> Any:
        """The account's input entity. A session string keeps no address book, so the contacts and
        the recent conversations are loaded on first need."""
        try:
            return await client.get_input_entity(user_id)
        except ValueError:
            pass
        from telethon.tl.functions.contacts import GetContactsRequest

        await client(GetContactsRequest(hash=0))
        try:
            return await client.get_input_entity(user_id)
        except ValueError:
            pass
        async for _ in client.iter_dialogs(limit=_DIALOGS):
            pass
        try:
            return await client.get_input_entity(user_id)
        except ValueError:
            raise AccountFailure(NOT_FOUND) from None

    def resolve_phone(self, phone: str, name: str) -> str | None:
        async def work(client: Any) -> str | None:
            from telethon.tl.functions.contacts import ImportContactsRequest
            from telethon.tl.types import InputPhoneContact

            contact = InputPhoneContact(
                client_id=random.randrange(1 << 62),
                phone=phone,
                first_name=(name or phone)[:64],
                last_name="",
            )
            result = await client(ImportContactsRequest([contact]))
            return str(result.users[0].id) if result.users else None

        return self._run(work)

    def send_text(self, peer: str, text: str) -> Sent:
        async def work(client: Any) -> Sent:
            entity = await self._entity(client, int(peer))
            message = await client.send_message(entity, text, link_preview=False)
            return Sent(message_id=int(message.id))

        return self._run(work, sending=True)

    # ---- reading ------------------------------------------------------------------------------

    def newest_message_id(self) -> int:
        async def work(client: Any) -> int:
            newest = 0
            async for dialog in client.iter_dialogs(limit=_DIALOGS):
                if _is_person(dialog) and dialog.message is not None:
                    newest = max(newest, int(dialog.message.id))
            return newest

        return self._run(work)

    def fetch_incoming(self, after_id: int, limit: int, accept: Accept) -> Batch:
        async def work(client: Any) -> Batch:
            found: list[Incoming] = []
            scanned = after_id
            truncated = False
            async for dialog in client.iter_dialogs(limit=_DIALOGS):
                if not _is_person(dialog) or dialog.message is None:
                    continue
                top = int(dialog.message.id)
                scanned = max(scanned, top)
                if top <= after_id:
                    continue
                entity = dialog.entity
                sender, phone = str(entity.id), _phone_of(entity)
                if not accept(sender, phone):  # not a prospect: nothing of it is read
                    continue
                name = " ".join(p for p in (entity.first_name, entity.last_name) if p)
                count = 0
                async for message in client.iter_messages(entity, min_id=after_id, limit=limit):
                    count += 1
                    if message.out or getattr(message, "action", None) is not None:
                        continue
                    when = message.date
                    found.append(
                        Incoming(
                            sender_id=sender,
                            message_id=int(message.id),
                            text=message.message or "",
                            at=when if when.tzinfo else when.replace(tzinfo=UTC),
                            name=name,
                            phone=phone,
                            attachment=type(message.media).__name__ if message.media else None,
                        )
                    )
                truncated = truncated or count >= limit
            found.sort(key=lambda m: m.message_id)
            if len(found) > limit:
                found, truncated = found[:limit], True
            return Batch(messages=found, scanned_to=scanned, truncated=truncated)

        return self._run(work)
