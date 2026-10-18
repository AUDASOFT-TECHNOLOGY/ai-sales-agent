"""The SQL store of the product resources, on a real (SQLite) database created by the migrations.

REAL here: the schema (migration 0014), the store, the service on top of it, the search, and the
isolation between clients. SIMULATED here: the web, and PostgreSQL itself (SQLite stands in; the
statements are plain SQLAlchemy Core).
"""

import pytest
from resource_support import MP4, NOW, FakeWeb, docx, html, page
from sqlalchemy import inspect, text

from sales_app.persistence import create_database_engine, upgrade_database
from sales_app.persistence.resource_rows import SqlResourceStore
from sales_app.persistence.schema import head_revision
from sales_app.resources.knowledge import KnowledgeIndex
from sales_app.resources.models import ResourceStatus
from sales_app.resources.service import ResourceError, ResourceService


@pytest.fixture
def database(tmp_path):
    engine = create_database_engine(f"sqlite:///{tmp_path / 'resources.db'}")
    upgrade_database(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def service(database):
    ids = iter(range(1, 10_000))
    web = FakeWeb(
        {
            "https://shop.example/c": page(
                html("<p>" + "The course page has many details. " * 8 + "</p>")
            )
        }
    )
    return ResourceService(
        SqlResourceStore(database), clock=lambda: NOW, fetch=web, new_id=lambda: f"r{next(ids)}"
    )


def add(service, name, content, client="alpha", product="p1"):
    return service.add_file(client, product, filename=name, title=name, content=content, by="u")


def test_the_migration_creates_the_tables_and_is_the_head(database):
    names = set(inspect(database).get_table_names())
    assert {"product_resources", "product_resource_chunks"} <= names
    assert head_revision() == "0014"


def test_a_file_round_trips_with_its_bytes_and_its_reading(service):
    content = docx("Six live sessions on Tuesdays")
    added = add(service, "brochure.docx", content)
    (listed,) = service.for_product("alpha", "p1")
    assert (
        listed == added
        and listed.status is ResourceStatus.READY
        and listed.size_bytes == len(content)
    )
    assert service.download("alpha", "p1", added.id)[1] == content


def test_a_listing_never_reads_the_bytes_of_the_files(service, database):
    add(service, "a.txt", b"x" * 5000)
    store = SqlResourceStore(database)
    with database.connect() as connection:
        before = connection.execute(text("select count(*) from product_resources")).scalar_one()
    assert before == 1 and store.for_product("alpha", "p1")[0].size_bytes == 5000
    assert not hasattr(store.for_product("alpha", "p1")[0], "content")


def test_the_text_of_a_ready_file_is_searchable_and_a_video_is_not(service, database):
    add(service, "guide.txt", b"Les sessions ont lieu le mardi soir en direct.")
    add(service, "intro.mp4", MP4)
    index = KnowledgeIndex(SqlResourceStore(database))
    found = index.search("alpha", ["p1"], "sessions mardi", 3)
    assert [f.source for f in found] == ["guide.txt"]


def test_a_link_is_read_and_can_be_read_again(service):
    added = service.add_link(
        "alpha", "p1", title="Page", url="https://shop.example/c", category="web", by="u"
    )
    assert added.status is ResourceStatus.READY
    again = service.retry("alpha", "p1", added.id)
    assert again.status is ResourceStatus.READY and again.text_chars == added.text_chars


def test_deleting_removes_the_row_the_bytes_and_the_text(service, database):
    added = add(service, "a.txt", b"hello there my friend")
    service.delete("alpha", "p1", added.id)
    with database.connect() as connection:
        for table in ("product_resources", "product_resource_chunks"):
            assert connection.execute(text(f"select count(*) from {table}")).scalar_one() == 0


def test_two_clients_with_the_same_product_id_never_meet(service, database):
    mine = add(service, "price-list.txt", b"private price list", client="alpha")
    add(
        service, "price-list.txt", b"private price list", client="beta"
    )  # same bytes: not a duplicate
    assert [r.id for r in service.for_product("beta", "p1")] != [mine.id]
    with pytest.raises(ResourceError):
        service.download("beta", "p1", mine.id)
    with pytest.raises(ResourceError):
        service.delete("beta", "p1", mine.id)
    store = SqlResourceStore(database)
    assert store.content("beta", mine.id) is None and store.get("beta", mine.id) is None
    assert store.count("alpha") == 1 and store.count("beta") == 1
    assert KnowledgeIndex(store).search("alpha", ["p1"], "private price", 3)
    assert service.download("alpha", "p1", mine.id)[1] == b"private price list"


def test_the_stamp_changes_when_the_readable_text_does(service, database):
    store = SqlResourceStore(database)
    empty = store.stamp("alpha", "p1")
    added = add(service, "a.txt", b"hello there my friend")
    assert store.stamp("alpha", "p1") != empty
    service.delete("alpha", "p1", added.id)
    assert store.count("alpha", "p1") == 0
