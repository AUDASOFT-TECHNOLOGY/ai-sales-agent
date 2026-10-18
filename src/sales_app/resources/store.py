"""Where a client's product resources and their searchable text are kept.

Every call names the client: a resource is found only through the client that owns it, so no id a
browser sends can reach another client's file. The in-memory store is the reference implementation
(and what tests use); the SQL one lives in `sales_app.persistence.resource_rows`.
"""

import threading
from collections.abc import Sequence
from dataclasses import replace
from typing import Protocol

from sales_app.resources.models import Chunk, Resource, ResourceStatus


class ResourceStore(Protocol):
    def add(self, resource: Resource, *, content: bytes | None, chunks: Sequence[str]) -> None: ...

    def update_reading(
        self,
        client_id: str,
        resource_id: str,
        *,
        status: ResourceStatus,
        status_detail: str | None,
        chunks: Sequence[str],
    ) -> Resource | None: ...

    def for_product(self, client_id: str, product_id: str) -> list[Resource]: ...

    def get(self, client_id: str, resource_id: str) -> Resource | None: ...

    def content(self, client_id: str, resource_id: str) -> bytes | None: ...

    def delete(self, client_id: str, resource_id: str) -> bool: ...

    def count(self, client_id: str, product_id: str | None = None) -> int: ...

    def chunks(self, client_id: str, product_id: str) -> list[Chunk]: ...

    def stamp(self, client_id: str, product_id: str) -> str:
        """Changes whenever the readable text of this product changes (to know a cache is stale)."""
        ...


class InMemoryResourceStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resources: dict[tuple[str, str], Resource] = {}
        self._content: dict[tuple[str, str], bytes] = {}
        self._chunks: dict[tuple[str, str], list[str]] = {}
        self._version = 0

    def __repr__(self) -> str:
        return "InMemoryResourceStore()"

    def add(self, resource: Resource, *, content: bytes | None, chunks: Sequence[str]) -> None:
        key = (resource.client_id, resource.id)
        with self._lock:
            self._resources[key] = resource
            if content is not None:
                self._content[key] = content
            self._chunks[key] = list(chunks)
            self._version += 1

    def update_reading(
        self,
        client_id: str,
        resource_id: str,
        *,
        status: ResourceStatus,
        status_detail: str | None,
        chunks: Sequence[str],
    ) -> Resource | None:
        key = (client_id, resource_id)
        with self._lock:
            current = self._resources.get(key)
            if current is None:
                return None
            updated = replace(
                current,
                status=status,
                status_detail=status_detail,
                text_chars=sum(len(c) for c in chunks),
            )
            self._resources[key] = updated
            self._chunks[key] = list(chunks)
            self._version += 1
            return updated

    def for_product(self, client_id: str, product_id: str) -> list[Resource]:
        with self._lock:
            found = [
                r
                for (owner, _), r in self._resources.items()
                if owner == client_id and r.product_id == product_id
            ]
        return sorted(found, key=lambda r: (r.created_at, r.id))

    def get(self, client_id: str, resource_id: str) -> Resource | None:
        with self._lock:
            return self._resources.get((client_id, resource_id))

    def content(self, client_id: str, resource_id: str) -> bytes | None:
        with self._lock:
            return self._content.get((client_id, resource_id))

    def delete(self, client_id: str, resource_id: str) -> bool:
        key = (client_id, resource_id)
        with self._lock:
            existed = self._resources.pop(key, None) is not None
            self._content.pop(key, None)
            self._chunks.pop(key, None)
            if existed:
                self._version += 1
            return existed

    def count(self, client_id: str, product_id: str | None = None) -> int:
        with self._lock:
            return sum(
                1
                for (owner, _), r in self._resources.items()
                if owner == client_id and (product_id is None or r.product_id == product_id)
            )

    def chunks(self, client_id: str, product_id: str) -> list[Chunk]:
        with self._lock:
            found: list[Chunk] = []
            for key, resource in sorted(self._resources.items(), key=lambda kv: kv[1].created_at):
                if key[0] != client_id or resource.product_id != product_id:
                    continue
                if resource.status is not ResourceStatus.READY:
                    continue
                found.extend(
                    Chunk(
                        product_id=product_id, resource_id=resource.id, title=resource.title, text=t
                    )
                    for t in self._chunks.get(key, [])
                )
            return found

    def stamp(self, client_id: str, product_id: str) -> str:
        with self._lock:
            return f"{self._version}"
