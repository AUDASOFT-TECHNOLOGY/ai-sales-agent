"""A client's settings as they stand once the version published for it is laid over its client.yaml.

The one place that reads a published version this way: the loader merges the same two things when it
resolves a client, and the Admin reads a client that has no runtime through it. Nothing is
validated here, and nothing raises: a version that cannot be read leaves the `client.yaml` as it is.
"""

from typing import Any

from sales_app.config.resolution import (
    check_document,
    merge_document,
    strip_settings,
    upgrade_document,
)
from sales_app.config_store import ConfigStore, ConfigStoreError


def published_document(store: ConfigStore, client_id: str) -> dict[str, Any] | None:
    """The settings document in force for the client, in the current schema: the newest version
    published for it. None if none was published, or if it cannot be read."""
    try:
        version = store.current(client_id)
    except ConfigStoreError:
        return None
    if version is None or check_document(version.document):
        return None
    return upgrade_document(version.document)


def published_name(store: ConfigStore, client_id: str) -> str | None:
    """The business name in the newest published version, if there is one. The name a client gives
    itself is published without rebuilding its agent, so this is the newest name there is; the Client
    API reads it the same way."""
    document = published_document(store, client_id)
    name = document.get("display_name") if document is not None else None
    return name if isinstance(name, str) and name else None


def published_view(
    store: ConfigStore, client_id: str, raw: dict[str, Any] | None
) -> dict[str, Any]:
    """`raw` (a `client.yaml`) with the client's published settings laid over it, merged exactly as
    the loader merges them. Without a readable published version, `raw` as it is."""
    raw = raw or {}
    document = published_document(store, client_id)
    if document is None:
        return raw
    return merge_document(strip_settings(raw), document)
