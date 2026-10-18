"""Scaffolding for the product-resource tests: small real files built in memory (nothing from disk)."""

import io
import zipfile
from datetime import UTC, datetime
from xml.sax.saxutils import escape

from sales_app.resources.fetch import Page
from sales_app.resources.service import ResourceService
from sales_app.resources.store import InMemoryResourceStore

NOW = datetime(2026, 1, 15, 9, 0, tzinfo=UTC)


def pdf(text: str) -> bytes:
    """A one-page PDF whose text layer is `text` (standard Helvetica, no scan)."""
    content = f"BT /F1 12 Tf 72 700 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1,
        xref,
    )
    return bytes(out)


def blank_pdf() -> bytes:
    """A PDF page with no text at all, like a scan."""
    return pdf("").replace(b"() Tj", b"() Tj")


def docx(*paragraphs: str) -> bytes:
    body = "".join(f"<w:p><w:r><w:t>{escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    xml = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
        f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def xlsx(rows: list[list[str | int]], sheet: str = "Prices") -> bytes:
    strings: list[str] = []
    xml_rows = []
    for number, row in enumerate(rows, start=1):
        cells = []
        for column, value in enumerate(row):
            ref = f"{'ABCDEFGH'[column]}{number}"
            if isinstance(value, int):
                cells.append(f'<c r="{ref}"><v>{value}</v></c>')
            else:
                strings.append(value)
                cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
        xml_rows.append(f'<row r="{number}">{"".join(cells)}</row>')
    shared = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "xl/workbook.xml",
            f'<workbook><sheets><sheet name="{sheet}" sheetId="1"/></sheets></workbook>',
        )
        archive.writestr("xl/sharedStrings.xml", f"<sst>{shared}</sst>")
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"<worksheet><sheetData>{''.join(xml_rows)}</sheetData></worksheet>",
        )
    return buffer.getvalue()


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
MP4 = b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 32


def html(body: str, title: str = "Page") -> bytes:
    return f"<html><head><title>{title}</title><script>var x=1;</script></head><body>{body}</body></html>".encode()


class FakeWeb:
    """Stands in for the safe fetcher: serves pages by address, and counts what was opened."""

    def __init__(self, pages: dict[str, Page | Exception] | None = None) -> None:
        self.pages = pages or {}
        self.opened: list[str] = []

    def __call__(self, url: str) -> Page:
        self.opened.append(url)
        found = self.pages[url]
        if isinstance(found, Exception):
            raise found
        return found


def page(body: bytes, kind: str = "text/html") -> Page:
    return Page(url="https://x.example", content_type=kind, full_content_type=kind, body=body)


def make_service(
    web: FakeWeb | None = None, **kwargs
) -> tuple[ResourceService, InMemoryResourceStore]:
    store = InMemoryResourceStore()
    counter = iter(range(1, 10_000))
    service = ResourceService(
        store,
        clock=lambda: NOW,
        fetch=web or FakeWeb(),
        new_id=lambda: f"r{next(counter)}",
        **kwargs,
    )
    return service, store
