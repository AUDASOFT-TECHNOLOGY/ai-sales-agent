"""The SQL side of the product resources (the port of `sales_app.resources.store`).

Every statement filters on the client, so a resource is reachable only through the client that owns
it. The listing never reads a file's bytes (`content` is fetched by `content()` alone). A database that
cannot be reached is `StoreUnavailableError`, a fixed sentence.
"""

from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import Engine, delete, func, insert, select, update
from sqlalchemy.exc import SQLAlchemyError

from sales_app.persistence.clients import ensure_client
from sales_app.persistence.tables import product_resource_chunks as chunk_table
from sales_app.persistence.tables import product_resources as table
from sales_app.resources.models import Chunk, Resource, ResourceKind, ResourceStatus
from sales_app.sourcing.errors import StoreUnavailableError

_UNAVAILABLE = "the product documents are unavailable"
_COLUMNS = [c for c in table.c if c.name != "content"]


def _resource(row) -> Resource:
    return Resource(
        id=row.id,
        client_id=row.client_id,
        product_id=row.product_id,
        kind=ResourceKind(row.kind),
        category=row.category,
        title=row.title,
        url=row.url,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        status=ResourceStatus(row.status),
        status_detail=row.status_detail,
        text_chars=row.text_chars,
        created_at=row.created_at,
        created_by=row.created_by,
    )


def _chunk_values(resource: Resource, chunks: Sequence[str]) -> list[dict[str, object]]:
    return [
        {
            "resource_id": resource.id,
            "position": position,
            "client_id": resource.client_id,
            "product_id": resource.product_id,
            "body": body,
        }
        for position, body in enumerate(chunks)
    ]


class SqlResourceStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def __repr__(self) -> str:
        return "SqlResourceStore()"

    def add(self, resource: Resource, *, content: bytes | None, chunks: Sequence[str]) -> None:
        try:
            with self._engine.begin() as connection:
                ensure_client(
                    connection, resource.client_id, created_at=resource.created_at, created_by=None
                )
                connection.execute(
                    insert(table).values(
                        id=resource.id,
                        client_id=resource.client_id,
                        product_id=resource.product_id,
                        kind=resource.kind.value,
                        category=resource.category,
                        title=resource.title,
                        url=resource.url,
                        filename=resource.filename,
                        mime_type=resource.mime_type,
                        size_bytes=resource.size_bytes,
                        sha256=resource.sha256,
                        status=resource.status.value,
                        status_detail=resource.status_detail,
                        text_chars=resource.text_chars,
                        content=content,
                        created_at=resource.created_at,
                        created_by=resource.created_by,
                        updated_at=resource.created_at,
                    )
                )
                if chunks:
                    connection.execute(insert(chunk_table), _chunk_values(resource, chunks))
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def update_reading(
        self,
        client_id: str,
        resource_id: str,
        *,
        status: ResourceStatus,
        status_detail: str | None,
        chunks: Sequence[str],
    ) -> Resource | None:
        now = datetime.now(UTC)
        try:
            with self._engine.begin() as connection:
                result = connection.execute(
                    update(table)
                    .where(table.c.id == resource_id, table.c.client_id == client_id)
                    .values(
                        status=status.value,
                        status_detail=status_detail,
                        text_chars=sum(len(c) for c in chunks),
                        updated_at=now,
                    )
                )
                if result.rowcount != 1:
                    return None
                connection.execute(
                    delete(chunk_table).where(chunk_table.c.resource_id == resource_id)
                )
                row = connection.execute(
                    select(*_COLUMNS).where(
                        table.c.id == resource_id, table.c.client_id == client_id
                    )
                ).first()
                assert row is not None
                resource = _resource(row)
                if chunks:
                    connection.execute(insert(chunk_table), _chunk_values(resource, chunks))
                return resource
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def for_product(self, client_id: str, product_id: str) -> list[Resource]:
        query = (
            select(*_COLUMNS)
            .where(table.c.client_id == client_id, table.c.product_id == product_id)
            .order_by(table.c.created_at, table.c.id)
        )
        try:
            with self._engine.connect() as connection:
                return [_resource(row) for row in connection.execute(query)]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def get(self, client_id: str, resource_id: str) -> Resource | None:
        query = select(*_COLUMNS).where(table.c.id == resource_id, table.c.client_id == client_id)
        try:
            with self._engine.connect() as connection:
                row = connection.execute(query).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return _resource(row) if row is not None else None

    def content(self, client_id: str, resource_id: str) -> bytes | None:
        query = select(table.c.content).where(
            table.c.id == resource_id, table.c.client_id == client_id
        )
        try:
            with self._engine.connect() as connection:
                row = connection.execute(query).first()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return bytes(row[0]) if row is not None and row[0] is not None else None

    def delete(self, client_id: str, resource_id: str) -> bool:
        try:
            with self._engine.begin() as connection:
                owned = connection.execute(
                    select(table.c.id).where(
                        table.c.id == resource_id, table.c.client_id == client_id
                    )
                ).first()
                if owned is None:
                    return False
                connection.execute(
                    delete(chunk_table).where(chunk_table.c.resource_id == resource_id)
                )
                connection.execute(delete(table).where(table.c.id == resource_id))
                return True
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def count(self, client_id: str, product_id: str | None = None) -> int:
        query = select(func.count()).select_from(table).where(table.c.client_id == client_id)
        if product_id is not None:
            query = query.where(table.c.product_id == product_id)
        try:
            with self._engine.connect() as connection:
                return int(connection.execute(query).scalar_one())
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def chunks(self, client_id: str, product_id: str) -> list[Chunk]:
        query = (
            select(table.c.id, table.c.title, chunk_table.c.body)
            .select_from(chunk_table.join(table, table.c.id == chunk_table.c.resource_id))
            .where(
                table.c.client_id == client_id,
                table.c.product_id == product_id,
                table.c.status == ResourceStatus.READY.value,
            )
            .order_by(table.c.created_at, table.c.id, chunk_table.c.position)
        )
        try:
            with self._engine.connect() as connection:
                return [
                    Chunk(product_id=product_id, resource_id=row.id, title=row.title, text=row.body)
                    for row in connection.execute(query)
                ]
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None

    def stamp(self, client_id: str, product_id: str) -> str:
        query = select(func.count(), func.max(table.c.updated_at)).where(
            table.c.client_id == client_id, table.c.product_id == product_id
        )
        try:
            with self._engine.connect() as connection:
                count, latest = connection.execute(query).one()
        except SQLAlchemyError:
            raise StoreUnavailableError(_UNAVAILABLE) from None
        return f"{count}:{latest}"
