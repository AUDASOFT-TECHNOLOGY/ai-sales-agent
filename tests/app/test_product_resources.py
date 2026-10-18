"""Product resources: what is stored, what is really read, and what is refused.

REAL here: the readers (text, CSV, PDF, Word, Excel, HTML), the type and size checks, the limits,
the duplicate guard, the service and the in-memory store, the safe fetcher's address rules (its HTTP
exchange is a `MockTransport`), and the BM25 search. SIMULATED here: the web (a fake fetcher) and the
clock. Nothing here proves that a real site answers or that a real database holds the files.
"""

import httpx
import pytest
from resource_support import (
    MP4,
    NOW,
    PNG,
    FakeWeb,
    blank_pdf,
    docx,
    html,
    make_service,
    page,
    pdf,
    xlsx,
)

from sales_app.resources.extract import (
    ACCEPTED_EXTENSIONS,
    chunk_text,
    clean_text,
    extract_file,
    matches_type,
    read_html,
)
from sales_app.resources.fetch import (
    FetchFailedError,
    UnsafeAddressError,
    check_address,
    fetch_page,
    is_video_host,
)
from sales_app.resources.knowledge import KnowledgeIndex
from sales_app.resources.models import ResourceKind, ResourceStatus
from sales_app.resources.service import Limits, ResourceError

PUBLIC = "93.184.216.34"


def add_file(service, name, content, client="alpha", product="p1", title="T"):
    return service.add_file(client, product, filename=name, title=title, content=content, by="u")


class TestWhatIsRead:
    def test_plain_text_and_markdown(self):
        out = extract_file("txt", b"Hello\n\n\nWorld  ")
        assert out.status is ResourceStatus.READY and out.text == "Hello\n\nWorld"

    def test_text_in_the_old_windows_encoding_is_still_text(self):
        assert "café" in extract_file("txt", "café du matin".encode("cp1252")).text

    def test_a_csv_is_read_row_by_row(self):
        out = extract_file("csv", b"name;price\nCourse A;100\nCourse B;200\n")
        assert out.status is ResourceStatus.READY
        assert "Course A | 100" in out.text and "name | price" in out.text

    def test_a_pdf_with_a_text_layer(self):
        out = extract_file("pdf", pdf("Six live sessions every Tuesday"))
        assert out.status is ResourceStatus.READY and "Six live sessions" in out.text

    def test_a_pdf_without_text_is_said_to_be_a_scan_not_read(self):
        out = extract_file("pdf", blank_pdf())
        assert out.status is ResourceStatus.STORED_ONLY and out.detail == "scanned_pdf"

    def test_a_broken_pdf_is_a_failed_reading_not_a_crash(self):
        out = extract_file("pdf", b"%PDF-1.4 this is not really a pdf")
        assert out.status is ResourceStatus.FAILED and out.detail == "parse_failed"

    def test_a_word_document(self):
        out = extract_file("docx", docx("Intro & basics", "Second paragraph"))
        assert out.status is ResourceStatus.READY
        assert out.text == "Intro & basics\nSecond paragraph"

    def test_an_excel_workbook_with_its_sheet_name(self):
        out = extract_file("xlsx", xlsx([["Course", "Price"], ["Alpha", 100]]))
        assert out.status is ResourceStatus.READY
        assert "[Prices]" in out.text and "Course | Price" in out.text and "Alpha | 100" in out.text

    def test_a_damaged_office_file_is_a_failed_reading(self):
        assert extract_file("docx", b"PK\x03\x04junk").status is ResourceStatus.FAILED
        assert extract_file("xlsx", b"PK\x03\x04junk").status is ResourceStatus.FAILED

    def test_images_and_videos_are_kept_but_never_read(self):
        image, video = extract_file("png", PNG), extract_file("mp4", MP4)
        assert (image.status, image.detail, image.text) == (ResourceStatus.STORED_ONLY, "image", "")
        assert (video.status, video.detail, video.text) == (ResourceStatus.STORED_ONLY, "video", "")

    def test_a_web_page_gives_its_visible_text_without_scripts_or_menus(self):
        body = (
            "<nav>Menu Home</nav><h1>Sales course</h1><p>"
            + "Learn to sell. " * 10
            + "</p><script>evil()</script>"
        )
        out = read_html(html(body, "Alpha"), "text/html; charset=utf-8")
        assert out.status is ResourceStatus.READY
        assert "Sales course" in out.text and "Learn to sell." in out.text
        assert "Menu" not in out.text and "evil" not in out.text and out.text.startswith("Alpha")

    def test_a_page_that_is_only_a_shell_is_not_pretended_to_be_read(self):
        out = read_html(html("<div id=app></div>"))
        assert out.status is ResourceStatus.STORED_ONLY and out.detail == "page_not_text"


class TestWhatAFileIs:
    @pytest.mark.parametrize(
        ("extension", "content"),
        [
            ("pdf", pdf("x")),
            ("docx", docx("x")),
            ("xlsx", xlsx([["a"]])),
            ("png", PNG),
            ("mp4", MP4),
            ("txt", b"hello"),
        ],
    )
    def test_the_bytes_match_their_extension(self, extension, content):
        assert matches_type(extension, content)

    @pytest.mark.parametrize(
        ("extension", "content"),
        [
            ("pdf", b"MZ\x90\x00 an executable"),
            ("docx", pdf("x")),
            ("xlsx", docx("x")),
            ("png", b"<html><script>alert(1)</script>"),
            ("mp4", PNG),
            ("txt", b"binary\x00data"),
            ("exe", b"MZ"),
        ],
    )
    def test_a_renamed_file_does_not_pass(self, extension, content):
        assert not matches_type(extension, content)

    def test_text_is_tidied_and_capped(self):
        assert clean_text("a\r\n\r\n\r\n b  \x00c\n\nb\nb") == "a\n\nb c\n\nb"

    def test_chunks_are_bounded_and_overlap_so_an_answer_is_not_cut_in_two(self):
        text = " ".join(f"Sentence number {i} is here." for i in range(200))
        chunks = chunk_text(text, size=300)
        assert len(chunks) > 5 and all(len(c) <= 330 for c in chunks)
        assert chunks[0].split(".")[-2].strip() in chunks[1]


class TestAddingFiles:
    def test_a_readable_file_is_stored_and_marked_ready(self):
        service, store = make_service()
        resource = add_file(service, "brochure.docx", docx("Six live sessions"))
        assert resource.kind is ResourceKind.FILE and resource.category == "document"
        assert resource.status is ResourceStatus.READY and resource.text_chars > 0
        assert store.content("alpha", resource.id) == docx("Six live sessions")

    def test_a_video_is_stored_and_honestly_not_exploited(self):
        service, _ = make_service()
        resource = add_file(service, "intro.mp4", MP4)
        assert resource.status is ResourceStatus.STORED_ONLY and resource.status_detail == "video"
        assert resource.text_chars == 0

    def test_a_type_that_is_not_accepted_is_refused(self):
        service, _ = make_service()
        with pytest.raises(ResourceError) as refused:
            add_file(service, "run.exe", b"MZ")
        assert refused.value.status == 415
        assert "exe" not in refused.value.message

    def test_a_file_that_is_not_what_it_says_is_refused(self):
        service, _ = make_service()
        with pytest.raises(ResourceError) as refused:
            add_file(service, "fake.pdf", b"<html>not a pdf</html>")
        assert refused.value.status == 422

    def test_an_empty_or_too_large_file_is_refused(self):
        service, _ = make_service(limits=Limits(max_file_bytes=100))
        with pytest.raises(ResourceError) as empty:
            add_file(service, "a.txt", b"")
        with pytest.raises(ResourceError) as large:
            add_file(service, "a.txt", b"x" * 101)
        assert (empty.value.status, large.value.status) == (422, 413)

    def test_the_same_file_twice_on_a_product_is_refused(self):
        service, _ = make_service()
        add_file(service, "a.txt", b"hello world")
        with pytest.raises(ResourceError) as again:
            add_file(service, "copy.txt", b"hello world")
        assert again.value.status == 409
        add_file(service, "a.txt", b"hello world", product="p2")  # another product is fine

    def test_a_product_holds_a_limited_number(self):
        service, _ = make_service(limits=Limits(max_per_product=2))
        add_file(service, "a.txt", b"one")
        add_file(service, "b.txt", b"two")
        with pytest.raises(ResourceError) as full:
            add_file(service, "c.txt", b"three")
        assert full.value.status == 422

    def test_every_accepted_type_is_one_the_readers_know(self):
        assert set(ACCEPTED_EXTENSIONS) == {
            "pdf", "docx", "xlsx", "csv", "txt", "md", "jpg", "jpeg", "png", "webp", "gif", "mp4", "webm", "mov",
        }  # fmt: skip

    def test_titles_are_tidied_and_default_to_the_file_name(self):
        service, _ = make_service()
        assert add_file(service, "guide.txt", b"hello", title="  \n ").title == "guide.txt"
        assert add_file(service, "g2.txt", b"hello2", title="A\x00B  C").title == "AB C"


class TestAddingLinks:
    URL = "https://shop.example/course"

    def test_a_page_is_fetched_and_its_text_kept(self):
        web = FakeWeb({self.URL: page(html("<p>" + "Course details here. " * 8 + "</p>"))})
        service, store = make_service(web)
        resource = service.add_link(
            "alpha", "p1", title="Course page", url=self.URL, category="web", by="u"
        )
        assert resource.status is ResourceStatus.READY and resource.url == self.URL
        assert web.opened == [self.URL]
        assert store.chunks("alpha", "p1")

    def test_a_pdf_behind_a_link_is_read(self):
        web = FakeWeb({self.URL: page(pdf("Official programme of the course"), "application/pdf")})
        service, _ = make_service(web)
        resource = service.add_link("alpha", "p1", title="", url=self.URL, category="web", by="u")
        assert resource.status is ResourceStatus.READY

    def test_a_video_site_is_not_even_opened(self):
        web = FakeWeb()
        service, _ = make_service(web)
        resource = service.add_link(
            "alpha", "p1", title="Demo", url="https://youtu.be/abc", category="video", by="u"
        )
        assert resource.status is ResourceStatus.STORED_ONLY and resource.status_detail == "video"
        assert web.opened == []

    def test_an_address_that_leads_to_a_private_network_is_kept_as_failed_not_opened(self):
        web = FakeWeb({self.URL: UnsafeAddressError("private")})
        service, _ = make_service(web)
        resource = service.add_link("alpha", "p1", title="x", url=self.URL, category="web", by="u")
        assert (
            resource.status is ResourceStatus.FAILED and resource.status_detail == "unsafe_address"
        )

    def test_an_unreachable_page_is_failed_and_can_be_read_again(self):
        web = FakeWeb({self.URL: FetchFailedError("down")})
        service, _ = make_service(web)
        resource = service.add_link("alpha", "p1", title="x", url=self.URL, category="web", by="u")
        assert (resource.status, resource.status_detail) == (ResourceStatus.FAILED, "fetch_failed")
        web.pages[self.URL] = page(html("<p>" + "Back online with content. " * 8 + "</p>"))
        assert service.retry("alpha", "p1", resource.id).status is ResourceStatus.READY

    @pytest.mark.parametrize(
        "bad",
        [
            "http://shop.example/x",
            "ftp://x.example",
            "javascript:alert(1)",
            "shop.example",
            "https://",
            "https://a b",
        ],
    )
    def test_only_https_addresses_are_accepted(self, bad):
        service, _ = make_service()
        with pytest.raises(ResourceError) as refused:
            service.add_link("alpha", "p1", title="x", url=bad, category="web", by="u")
        assert refused.value.status == 422

    def test_a_link_cannot_be_added_twice(self):
        service, _ = make_service(FakeWeb({self.URL: FetchFailedError("x")}))
        service.add_link("alpha", "p1", title="x", url=self.URL, category="web", by="u")
        with pytest.raises(ResourceError) as again:
            service.add_link("alpha", "p1", title="y", url=self.URL + "/", category="web", by="u")
        assert again.value.status == 409

    def test_only_a_link_can_be_read_again(self):
        service, _ = make_service()
        file = add_file(service, "a.txt", b"hello")
        with pytest.raises(ResourceError):
            service.retry("alpha", "p1", file.id)


class TestClientsNeverMeet:
    def test_a_resource_is_reachable_only_through_its_own_client_and_product(self):
        service, store = make_service()
        mine = add_file(service, "a.txt", b"my secret price list", client="alpha")
        assert service.for_product("beta", "p1") == []
        with pytest.raises(ResourceError):
            service.download("beta", "p1", mine.id)
        with pytest.raises(ResourceError):
            service.delete("beta", "p1", mine.id)
        with pytest.raises(ResourceError):
            service.download("alpha", "other-product", mine.id)
        assert store.get("beta", mine.id) is None and store.content("beta", mine.id) is None
        assert service.download("alpha", "p1", mine.id)[1] == b"my secret price list"

    def test_deleting_removes_the_file_and_its_text(self):
        service, store = make_service()
        mine = add_file(service, "a.txt", b"some text here")
        service.delete("alpha", "p1", mine.id)
        assert store.get("alpha", mine.id) is None and store.chunks("alpha", "p1") == []


class TestTheSafeFetcher:
    def resolver(self, *addresses):
        return lambda host, port: list(addresses)

    @pytest.mark.parametrize(
        "address",
        [
            "127.0.0.1",
            "10.1.2.3",
            "192.168.0.9",
            "172.16.0.1",
            "169.254.169.254",
            "::1",
            "fe80::1",
            "0.0.0.0",
            "100.64.0.1",
        ],
    )
    def test_a_name_that_resolves_to_a_private_address_is_refused(self, address):
        with pytest.raises(UnsafeAddressError):
            check_address("https://shop.example/x", self.resolver(address))

    def test_one_private_address_among_public_ones_is_enough_to_refuse(self):
        with pytest.raises(UnsafeAddressError):
            check_address("https://shop.example/x", self.resolver(PUBLIC, "10.0.0.1"))

    @pytest.mark.parametrize(
        "url",
        [
            "http://shop.example/",
            "https://127.0.0.1/",
            "https://[::1]/",
            "https://user:pw@shop.example/",
            "https://shop.example:8443/",
            "file:///etc/passwd",
            "gopher://x",
        ],
    )
    def test_only_plain_https_to_a_name_on_the_standard_port(self, url):
        with pytest.raises(UnsafeAddressError):
            check_address(url, self.resolver(PUBLIC))

    def test_a_public_address_gives_the_ip_to_connect_to(self):
        assert check_address("https://shop.example/a?b=1", self.resolver(PUBLIC)) == (
            "shop.example",
            "/a?b=1",
            PUBLIC,
        )

    def test_video_sites_are_recognised(self):
        assert is_video_host("https://www.youtube.com/watch?v=1") and is_video_host(
            "https://vimeo.com/1"
        )
        assert not is_video_host("https://notyoutube.com/")

    def test_a_page_is_read_by_connecting_to_the_checked_ip_with_the_real_host_name(self):
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.url.host, request.headers["host"], request.url.path))
            return httpx.Response(
                200, headers={"content-type": "text/html; charset=utf-8"}, content=html("<p>Hi</p>")
            )

        result = fetch_page(
            "https://shop.example/course",
            resolver=self.resolver(PUBLIC),
            transport=httpx.MockTransport(handler),
        )
        assert seen == [(PUBLIC, "shop.example", "/course")]
        assert result.content_type == "text/html"

    def test_a_redirect_to_a_private_address_is_not_followed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://internal.example/admin"})

        def resolve(host, port):
            return ["10.0.0.7"] if host == "internal.example" else [PUBLIC]

        with pytest.raises(UnsafeAddressError):
            fetch_page(
                "https://shop.example/", resolver=resolve, transport=httpx.MockTransport(handler)
            )

    def test_redirects_are_limited(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "https://shop.example/again"})

        with pytest.raises(FetchFailedError):
            fetch_page(
                "https://shop.example/",
                resolver=self.resolver(PUBLIC),
                transport=httpx.MockTransport(handler),
            )

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(404, content=b"no"),
            httpx.Response(200, headers={"content-type": "image/png"}, content=b"x"),
            httpx.Response(
                200, headers={"content-type": "text/html"}, content=b"x" * (2 * 1024 * 1024 + 1)
            ),
        ],
    )
    def test_errors_wrong_types_and_huge_pages_fail(self, response):
        with pytest.raises(FetchFailedError):
            fetch_page(
                "https://shop.example/",
                resolver=self.resolver(PUBLIC),
                transport=httpx.MockTransport(lambda r: response),
            )

    def test_a_name_that_does_not_resolve_fails(self):
        with pytest.raises(FetchFailedError):
            fetch_page("https://nowhere.example/", resolver=self.resolver())


class TestFindingTheRightPassage:
    def library(self):
        service, store = make_service()
        add_file(
            service,
            "alpha.txt",
            b"Le cours Alpha dure six semaines avec des sessions en direct le mardi soir.\n\nLes inscriptions ferment le premier du mois.",
            product="alpha",
            title="Programme Alpha",
        )
        add_file(
            service,
            "beta.txt",
            b"Beta is a self-paced online course with recorded videos only.",
            product="beta",
            title="Beta guide",
        )
        return store, KnowledgeIndex(store)

    def test_the_best_passage_of_the_right_product_comes_first(self):
        _, index = self.library()
        found = index.search(
            "alpha_client" if False else "alpha",
            ["alpha", "beta"],
            "quand sont les sessions en direct ?",
            3,
        )
        assert found and found[0].product_id == "alpha" and "mardi" in found[0].text
        assert found[0].source == "Programme Alpha"

    def test_search_ignores_case_and_accents(self):
        _, index = self.library()
        found = index.search("alpha", ["alpha"], "SESSIONS EN DIRECT MARDI", 2)
        assert found and "mardi" in found[0].text
        assert index.search("alpha", ["alpha"], "inscriptions fermées", 2)

    def test_a_question_without_a_match_finds_nothing_when_several_products_are_in_play(self):
        _, index = self.library()
        assert index.search("alpha", ["alpha", "beta"], "zzzz qqqq", 3) == []

    def test_tell_me_more_gets_the_opening_of_the_one_product_in_play(self):
        _, index = self.library()
        found = index.search("alpha", ["alpha"], "dis-moi en plus", 3)
        assert found and found[0].product_id == "alpha"

    def test_another_clients_documents_are_never_searched(self):
        _, index = self.library()
        assert index.search("beta_client", ["alpha", "beta"], "sessions en direct", 3) == []

    def test_a_deleted_or_changed_document_disappears_from_the_next_search(self):
        store, index = self.library()
        assert index.search("alpha", ["beta"], "recorded videos", 3)
        (resource,) = store.for_product("alpha", "beta")
        store.delete("alpha", resource.id)
        assert index.search("alpha", ["beta"], "recorded videos", 3) == []

    def test_only_documents_the_agent_can_read_are_searched(self):
        service, store = make_service()
        add_file(service, "promo.mp4", MP4, product="alpha", title="Promo video about sessions")
        assert KnowledgeIndex(store).search("alpha", ["alpha"], "promo video sessions", 3) == []

    def test_the_bound_source_is_one_clients_view(self):
        _, index = self.library()
        found = index.for_client("alpha").search(["alpha"], "inscriptions", 2)
        assert found and found[0].text.startswith("Le cours") or "inscriptions" in found[0].text

    def test_a_long_document_cannot_crowd_out_the_others(self):
        service, store = make_service()
        add_file(
            service,
            "long.txt",
            ("session " * 40 + ".\n\n").encode() * 30,
            product="alpha",
            title="Long",
        )
        add_file(service, "short.txt", b"The session is on Monday.", product="alpha", title="Short")
        found = KnowledgeIndex(store).search("alpha", ["alpha"], "session", 4)
        assert sum(1 for f in found if f.source == "Long") <= 2
        assert any(f.source == "Short" for f in found)


def test_the_clock_is_the_one_given():
    service, _ = make_service()
    assert add_file(service, "a.txt", b"hello").created_at == NOW
