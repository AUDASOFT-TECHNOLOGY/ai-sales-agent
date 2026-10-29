"""`/client/v1/training` and the notes of `/client/v1/products/{product}/resources/note`: what the
agent can draw on to sell, and draft lessons found in it.

REAL here: the routes, the session cookie, the catalog check, the service, the readers and the
suggestions. SIMULATED here: the web (a fake fetcher) and the client login."""

from urllib.parse import quote

import pytest
from resource_support import MP4, NOW, FakeWeb, docx
from test_client_agent_api import World
from test_client_resources_api import BASE, with_catalog

from sales_app.resources.service import ResourceService
from sales_app.resources.store import InMemoryResourceStore

TRAINING = "/client/v1/training"
FAQ = docx(
    "Frequently asked questions",
    "Is there a refund? Yes, you can cancel within fourteen days and we refund the full course.",
    "Do I need experience? No, the first week starts from zero and each step is explained.",
    "How much does it cost? It costs 49 EUR a month, see https://example.org/pricing.",
)
QUOTES = docx(
    "What our customers say",
    "“I doubled my leads in the first month and finally stopped chasing people.” — Leila, bakery owner",
)


@pytest.fixture
def world(tmp_path):
    w = World(tmp_path)
    ids = iter(range(1, 10_000))
    w.application.product_resources = ResourceService(
        InMemoryResourceStore(),
        clock=lambda: NOW,
        fetch=FakeWeb({}),
        new_id=lambda: f"r{next(ids)}",
    )
    yield w
    w.application.close()


@pytest.fixture
def alpha(world):
    browser = world.browser("alpha")
    with_catalog(browser, "course-a", "course-b")
    return browser


def upload(browser, product, name, content):
    headers = {"X-File-Name": quote(name), "Content-Type": "application/octet-stream"}
    return browser.post(f"{BASE}/{product}/resources/file", content=content, headers=headers)


def note(browser, product, **body):
    body = {"title": "Leila", "text": "It changed my shop.", "category": "testimonial", **body}
    return browser.post(f"{BASE}/{product}/resources/note", json=body)


class TestAuthentication:
    def test_every_route_needs_a_client_session(self, world):
        anonymous = world.anonymous()
        assert anonymous.get(f"{TRAINING}/material").status_code == 401
        assert anonymous.get(f"{TRAINING}/suggestions/p").status_code == 401
        assert (
            anonymous.post(f"{BASE}/p/resources/note", json={"title": "t", "text": "x"}).status_code
            == 401
        )


class TestNotes:
    def test_a_testimonial_typed_by_the_client_is_kept_and_read(self, alpha):
        response = note(alpha, "course-a")
        assert response.status_code == 201, response.text
        body = response.json()
        assert body["category"] == "testimonial" and body["status"] == "ready"
        assert body["title"] == "Leila" and body["text_chars"] > 0

    def test_an_empty_note_a_repeated_note_and_an_unknown_kind_are_refused(self, alpha):
        assert note(alpha, "course-a", text="   ").status_code == 422
        assert note(alpha, "course-a").status_code == 201
        assert note(alpha, "course-a").status_code == 409
        assert note(alpha, "course-a", text="Other words", category="image").status_code == 422

    def test_a_note_is_for_a_product_of_the_catalog(self, alpha):
        assert note(alpha, "ghost").status_code == 404

    def test_a_note_can_be_removed_like_any_resource(self, alpha):
        rid = note(alpha, "course-a").json()["id"]
        assert alpha.delete(f"{BASE}/course-a/resources/{rid}").status_code == 200
        assert alpha.get(f"{BASE}/course-a/resources").json()["resources"] == []


class TestMaterial:
    def test_a_product_without_material_says_so(self, alpha):
        products = {
            p["product_id"]: p for p in alpha.get(f"{TRAINING}/material").json()["products"]
        }
        assert set(products) == {"course-a", "course-b"}
        assert products["course-a"]["total"] == 0 and products["course-a"]["readable"] == 0

    def test_what_is_read_and_what_is_not_are_told_apart(self, alpha):
        upload(alpha, "course-a", "faq.docx", FAQ)
        upload(alpha, "course-a", "intro.mp4", MP4)
        note(alpha, "course-a")
        response = alpha.get(f"{TRAINING}/material")
        assert response.headers["cache-control"] == "no-store"
        a = next(p for p in response.json()["products"] if p["product_id"] == "course-a")
        assert (a["total"], a["readable"], a["testimonials"]) == (3, 2, 1)
        assert a["readable_chars"] > 0
        assert [(u["category"], u["reason"]) for u in a["unread"]] == [("video", "video")]

    def test_a_client_only_sees_its_own_material(self, world):
        mine = world.browser("alpha")
        with_catalog(mine, "course-a")
        note(mine, "course-a")
        other = world.browser("beta")
        with_catalog(other, "course-a")
        theirs = other.get(f"{TRAINING}/material").json()["products"]
        assert theirs[0]["total"] == 0

    def test_without_the_documents_service_it_is_unavailable(self, world):
        world.application.product_resources = None
        browser = world.browser("alpha")
        with_catalog(browser, "course-a")
        assert browser.get(f"{TRAINING}/material").status_code == 503


class TestSuggestions:
    def test_frequent_questions_in_a_document_become_drafts_with_their_answer(self, alpha):
        upload(alpha, "course-a", "faq.docx", FAQ)
        body = alpha.get(f"{TRAINING}/suggestions/course-a").json()
        faqs = {s["when"]: s["text"] for s in body["suggestions"] if s["kind"] == "faq"}
        assert (
            "Is there a refund?" in faqs and "refund the full course" in faqs["Is there a refund?"]
        )
        assert "Do I need experience?" in faqs

    def test_a_passage_with_a_price_or_a_link_is_left_out_and_counted(self, alpha):
        upload(alpha, "course-a", "faq.docx", FAQ)
        body = alpha.get(f"{TRAINING}/suggestions/course-a").json()
        assert all(
            "49" not in s["text"] and "example.org" not in s["text"] for s in body["suggestions"]
        )
        assert body["left_out"] >= 1

    def test_a_quoted_testimonial_becomes_a_proof_with_its_author(self, alpha):
        upload(alpha, "course-a", "reviews.docx", QUOTES)
        proofs = [
            s
            for s in alpha.get(f"{TRAINING}/suggestions/course-a").json()["suggestions"]
            if s["kind"] == "proof"
        ]
        assert proofs and "doubled my leads" in proofs[0]["text"]
        assert proofs[0]["attribution"] == "Leila, bakery owner"

    def test_a_testimonial_note_is_offered_as_a_proof(self, alpha):
        note(
            alpha, "course-a", title="Sami", text="After three weeks my shop had twice the orders."
        )
        proofs = [
            s
            for s in alpha.get(f"{TRAINING}/suggestions/course-a").json()["suggestions"]
            if s["kind"] == "proof"
        ]
        assert [p["source"] for p in proofs] == ["Sami"]

    def test_a_product_without_readable_material_has_nothing_to_suggest(self, alpha):
        upload(alpha, "course-a", "intro.mp4", MP4)
        assert alpha.get(f"{TRAINING}/suggestions/course-a").json() == {
            "suggestions": [],
            "left_out": 0,
        }

    def test_the_product_must_be_in_the_catalog(self, alpha):
        assert alpha.get(f"{TRAINING}/suggestions/ghost").status_code == 404
