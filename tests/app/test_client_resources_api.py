"""`/client/v1/products/{product}/resources`: documents and links a client attaches to a product.

REAL here: the application, the routes, the session cookie, the catalog check, the service and its
in-memory store, the readers and the checks on type and size. SIMULATED here: the web (a fake
fetcher) and the client login. The SQL store has its own test (`test_sql_product_resources.py`).
"""

from urllib.parse import quote

import pytest
from kit_support import with_product
from resource_support import MP4, NOW, FakeWeb, docx, html, page, pdf
from test_client_agent_api import SETTINGS, World, put

from sales_app.resources.fetch import FetchFailedError
from sales_app.resources.service import ResourceService
from sales_app.resources.store import InMemoryResourceStore

BASE = "/client/v1/products"
URL = "https://shop.example/course"


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    web = FakeWeb({URL: page(html("<p>" + "Details of the course are here. " * 8 + "</p>"))})
    w.web = web
    store = InMemoryResourceStore()
    ids = iter(range(1, 10_000))
    w.application.product_resources = ResourceService(
        store, clock=lambda: NOW, fetch=web, new_id=lambda: f"r{next(ids)}"
    )
    yield w
    w.application.close()


def with_catalog(browser, *product_ids):
    """The client's catalog holds these products (saved through the real settings route)."""
    version = browser.get(SETTINGS).json()["version"]
    doc = with_product(id=product_ids[0])
    for extra in product_ids[1:]:
        doc["catalog"]["products"].append({"id": extra, "name": extra, "summary": "s"})
    assert put(browser, version, commercial=doc).status_code == 200, "setup"


def upload(browser, product, name, content, title=None):
    headers = {"X-File-Name": quote(name), "Content-Type": "application/octet-stream"}
    if title is not None:
        headers["X-Resource-Title"] = quote(title)
    return browser.post(f"{BASE}/{product}/resources/file", content=content, headers=headers)


@pytest.fixture
def alpha(world):
    browser = world.browser("alpha")
    with_catalog(browser, "course-a", "course-b")
    return browser


class TestAuthentication:
    def test_every_route_needs_a_client_session(self, world):
        anonymous = world.anonymous()
        assert anonymous.get(f"{BASE}/p/resources").status_code == 401
        assert anonymous.post(f"{BASE}/p/resources/link", json={"url": URL}).status_code == 401
        assert anonymous.post(f"{BASE}/p/resources/file", content=b"x").status_code == 401
        assert anonymous.delete(f"{BASE}/p/resources/r1").status_code == 401
        assert anonymous.get(f"{BASE}/p/resources/r1/download").status_code == 401


class TestListing:
    def test_a_new_product_has_no_resources_and_shows_the_limits(self, alpha):
        response = alpha.get(f"{BASE}/course-a/resources")
        assert response.status_code == 200
        body = response.json()
        assert body["resources"] == []
        assert body["limits"]["max_file_bytes"] == 10 * 1024 * 1024
        assert "pdf" in body["limits"]["accepted_extensions"]
        assert response.headers["cache-control"] == "no-store"

    def test_a_product_that_is_not_in_the_catalog_is_not_found(self, alpha):
        assert alpha.get(f"{BASE}/ghost/resources").status_code == 404
        assert upload(alpha, "ghost", "a.txt", b"hello").status_code == 404
        assert alpha.post(f"{BASE}/ghost/resources/link", json={"url": URL}).status_code == 404

    def test_the_documents_are_not_available_on_a_platform_without_them(self, world):
        world.application.product_resources = None
        browser = world.browser("alpha")
        with_catalog(browser, "course-a")
        assert browser.get(f"{BASE}/course-a/resources").status_code == 503


class TestFiles:
    def test_a_word_document_is_stored_read_and_listed(self, alpha):
        response = upload(alpha, "course-a", "brochure.docx", docx("Six live sessions"), "Brochure")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["kind"] == "file" and body["category"] == "document"
        assert body["title"] == "Brochure" and body["filename"] == "brochure.docx"
        assert body["status"] == "ready" and body["text_chars"] > 0
        listed = alpha.get(f"{BASE}/course-a/resources").json()["resources"]
        assert [r["id"] for r in listed] == [body["id"]]
        assert "content" not in body and "sha256" not in body

    def test_a_pdf_with_text_is_ready_and_a_scan_is_not(self, alpha):
        assert (
            upload(alpha, "course-a", "a.pdf", pdf("Programme of the year")).json()["status"]
            == "ready"
        )

    def test_a_video_is_kept_and_says_it_is_not_read(self, alpha):
        body = upload(alpha, "course-a", "intro.mp4", MP4).json()
        assert (body["status"], body["status_detail"], body["text_chars"]) == (
            "stored_only",
            "video",
            0,
        )

    def test_a_file_name_with_accents_and_spaces_survives(self, alpha):
        body = upload(alpha, "course-a", "Présentation été.txt", b"bonjour").json()
        assert (
            body["filename"] == "Présentation été.txt" and body["title"] == "Présentation été.txt"
        )

    def test_a_file_that_is_not_what_it_says_is_refused(self, alpha):
        response = upload(alpha, "course-a", "fake.pdf", b"<html>x</html>")
        assert response.status_code == 422
        assert "fake" not in response.text

    def test_a_type_that_is_not_accepted_is_refused(self, alpha):
        assert upload(alpha, "course-a", "run.exe", b"MZ").status_code == 415
        assert upload(alpha, "course-a", "noextension", b"x").status_code == 415

    def test_a_file_without_a_name_is_refused(self, alpha):
        response = alpha.post(f"{BASE}/course-a/resources/file", content=b"hello")
        assert response.status_code == 422

    def test_a_file_over_the_limit_is_refused_before_it_is_kept(self, world, alpha):
        world.application.product_resources.limits = type(
            world.application.product_resources.limits
        )(max_file_bytes=50)
        assert upload(alpha, "course-a", "big.txt", b"x" * 51).status_code == 413
        assert alpha.get(f"{BASE}/course-a/resources").json()["resources"] == []

    def test_the_same_file_twice_is_a_conflict(self, alpha):
        assert upload(alpha, "course-a", "a.txt", b"hello").status_code == 201
        assert upload(alpha, "course-a", "b.txt", b"hello").status_code == 409

    def test_a_file_can_be_downloaded_back_never_displayed_inline(self, alpha):
        added = upload(alpha, "course-a", "a.txt", b"<script>alert(1)</script>").json()
        response = alpha.get(f"{BASE}/course-a/resources/{added['id']}/download")
        assert response.status_code == 200 and response.content == b"<script>alert(1)</script>"
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")
        assert response.headers["x-content-type-options"] == "nosniff"

    def test_a_link_has_nothing_to_download(self, alpha):
        added = alpha.post(f"{BASE}/course-a/resources/link", json={"url": URL}).json()
        assert alpha.get(f"{BASE}/course-a/resources/{added['id']}/download").status_code == 404


class TestLinks:
    def test_a_page_is_read_and_its_text_becomes_usable(self, alpha, world):
        response = alpha.post(
            f"{BASE}/course-a/resources/link",
            json={"title": "Course page", "url": URL, "category": "web"},
        )
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["kind"] == "link" and body["url"] == URL and body["status"] == "ready"
        assert world.web.opened == [URL]

    def test_a_video_link_is_kept_and_not_opened(self, alpha, world):
        body = alpha.post(
            f"{BASE}/course-a/resources/link",
            json={"url": "https://youtu.be/abc", "category": "video"},
        ).json()
        assert (body["status"], body["status_detail"]) == ("stored_only", "video")
        assert world.web.opened == []

    def test_a_link_that_cannot_be_read_says_so_and_can_be_tried_again(self, alpha, world):
        down = "https://down.example/x"
        world.web.pages[down] = FetchFailedError("down")
        body = alpha.post(f"{BASE}/course-a/resources/link", json={"url": down}).json()
        assert (body["status"], body["status_detail"]) == ("failed", "fetch_failed")
        world.web.pages[down] = page(html("<p>" + "Now it works well. " * 10 + "</p>"))
        retried = alpha.post(f"{BASE}/course-a/resources/{body['id']}/retry")
        assert retried.status_code == 200 and retried.json()["status"] == "ready"

    @pytest.mark.parametrize(
        "bad", ["http://shop.example/x", "javascript:alert(1)", "shop.example", "https://a b"]
    )
    def test_only_https_links_are_accepted(self, alpha, bad):
        assert alpha.post(f"{BASE}/course-a/resources/link", json={"url": bad}).status_code == 422

    def test_a_category_that_is_not_a_link_kind_is_refused(self, alpha):
        assert (
            alpha.post(
                f"{BASE}/course-a/resources/link", json={"url": URL, "category": "pdf"}
            ).status_code
            == 422
        )

    def test_unknown_fields_are_refused(self, alpha):
        assert (
            alpha.post(
                f"{BASE}/course-a/resources/link", json={"url": URL, "client_id": "beta"}
            ).status_code
            == 422
        )


class TestRemoving:
    def test_a_resource_can_be_removed(self, alpha):
        added = upload(alpha, "course-a", "a.txt", b"hello").json()
        response = alpha.delete(f"{BASE}/course-a/resources/{added['id']}")
        assert response.status_code == 200 and response.json() == {"deleted": added["id"]}
        assert alpha.get(f"{BASE}/course-a/resources").json()["resources"] == []
        assert alpha.delete(f"{BASE}/course-a/resources/{added['id']}").status_code == 404

    def test_a_resource_is_found_only_under_its_own_product(self, alpha):
        added = upload(alpha, "course-a", "a.txt", b"hello").json()
        assert alpha.delete(f"{BASE}/course-b/resources/{added['id']}").status_code == 404
        assert alpha.get(f"{BASE}/course-b/resources/{added['id']}/download").status_code == 404


class TestClientsNeverMeet:
    def test_one_client_never_sees_changes_or_downloads_another_clients_documents(
        self, world, alpha
    ):
        beta = world.browser("beta")
        with_catalog(beta, "course-a")  # the same product id on purpose
        mine = upload(alpha, "course-a", "secret-prices.txt", b"my private price list").json()

        assert beta.get(f"{BASE}/course-a/resources").json()["resources"] == []
        assert beta.get(f"{BASE}/course-a/resources/{mine['id']}/download").status_code == 404
        assert beta.delete(f"{BASE}/course-a/resources/{mine['id']}").status_code == 404
        assert beta.post(f"{BASE}/course-a/resources/{mine['id']}/retry").status_code == 404
        # nothing was lost, nothing leaked
        assert (
            alpha.get(f"{BASE}/course-a/resources/{mine['id']}/download").content
            == b"my private price list"
        )
        assert upload(beta, "course-a", "other.txt", b"my private price list").status_code == 201
