"""Reading the text of a file or a web page, honestly.

Each reader returns an `Extraction`: ``ready`` with the text it really found, ``stored_only`` for a
kind of content nothing here can read (videos, images, a page that is not text, a scanned PDF), or
``failed`` when reading was attempted and did not work. Nothing is ever guessed or described from a
file name: an image or a video is kept, not "understood".

What can be read, and how:

- text, Markdown, CSV: decoded;
- PDF: the text layer, with ``pypdf`` (a scan has none, and is said to have none);
- Word (.docx) and Excel (.xlsx): their XML, read directly (no macro, formula or external link is ever
  run or followed);
- web pages: the visible text of the HTML.

Every reader works on bytes already in memory, under size limits (a damaged or hostile archive cannot
exhaust memory), and returns at most `MAX_TEXT_CHARS` characters.
"""

import csv
import io
import logging
import re
import unicodedata
import zipfile
from collections.abc import Callable
from html import unescape
from html.parser import HTMLParser

from sales_app.resources.models import (
    DETAIL_EMPTY,
    DETAIL_IMAGE,
    DETAIL_PAGE_NOT_TEXT,
    DETAIL_PARSE_FAILED,
    DETAIL_SCANNED_PDF,
    DETAIL_VIDEO,
    Extraction,
)

log = logging.getLogger(__name__)

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_TEXT_CHARS = 120_000
MAX_PDF_PAGES = 300
MAX_ZIP_MEMBER_BYTES = 30 * 1024 * 1024
MAX_ZIP_ENTRIES = 3000
MAX_SHEETS = 10
MAX_ROWS_PER_SHEET = 3000
MIN_PAGE_CHARS = 80  # a page with less visible text is a shell filled in by scripts, or empty
CHUNK_CHARS = 900

# extension -> (category, what is read from it)
FILE_TYPES: dict[str, str] = {
    "pdf": "pdf",
    "docx": "document",
    "xlsx": "spreadsheet",
    "csv": "spreadsheet",
    "txt": "document",
    "md": "document",
    "jpg": "image",
    "jpeg": "image",
    "png": "image",
    "webp": "image",
    "gif": "image",
    "mp4": "video",
    "webm": "video",
    "mov": "video",
}
ACCEPTED_EXTENSIONS = tuple(FILE_TYPES)
TEXT_EXTENSIONS = ("txt", "md", "csv")


def extension_of(filename: str) -> str:
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


# ---- guarding what a file says it is ------------------------------------------------------------


def matches_type(extension: str, content: bytes) -> bool:
    """Whether the bytes are what the extension claims (so a renamed file is refused, not read)."""
    head = content[:16]
    if extension == "pdf":
        return content[:1024].lstrip().startswith(b"%PDF-")
    if extension in ("docx", "xlsx"):
        if not head.startswith(b"PK\x03\x04"):
            return False
        member = "word/document.xml" if extension == "docx" else "xl/workbook.xml"
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                return member in archive.namelist()
        except zipfile.BadZipFile:
            return False
    if extension in ("jpg", "jpeg"):
        return head.startswith(b"\xff\xd8\xff")
    if extension == "png":
        return head.startswith(b"\x89PNG\r\n\x1a\n")
    if extension == "gif":
        return head.startswith((b"GIF87a", b"GIF89a"))
    if extension == "webp":
        return head[:4] == b"RIFF" and head[8:12] == b"WEBP"
    if extension in ("mp4", "mov"):
        return head[4:8] == b"ftyp"
    if extension == "webm":
        return head.startswith(b"\x1a\x45\xdf\xa3")
    if extension in TEXT_EXTENSIONS:
        return b"\x00" not in content[:4096]
    return False


# ---- text helpers ---------------------------------------------------------------------------------


def clean_text(text: str) -> str:
    """Printable text with tidy spacing, capped at `MAX_TEXT_CHARS`."""
    kept = "".join(
        c
        for c in unicodedata.normalize("NFC", text)
        if c in "\n\t" or unicodedata.category(c) != "Cc"
    )
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in kept.replace("\r", "\n").split("\n")]
    out: list[str] = []
    blank = 0
    for line in lines:
        if line == "":
            blank += 1
            if blank == 1 and out:
                out.append("")
            continue
        blank = 0
        if out and out[-1] == line:
            continue  # a repeated line (a menu, a footer)
        out.append(line)
    return "\n".join(out).strip()[:MAX_TEXT_CHARS]


def chunk_text(text: str, size: int = CHUNK_CHARS) -> list[str]:
    """Passages of about `size` characters, split at paragraph and sentence ends (one per line), with
    the last piece of a passage repeated at the start of the next so that an answer is not cut in two."""
    units: list[str] = []
    for paragraph in re.split(r"\n\s*\n|\n", text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= size:
            units.append(paragraph)
            continue
        for sentence in re.split(r"(?<=[.!?؟])\s+", paragraph):
            while len(sentence) > size:
                units.append(sentence[:size])
                sentence = sentence[size:]
            if sentence:
                units.append(sentence)
    chunks: list[str] = []
    current: list[str] = []
    length = 0
    for unit in units:
        if current and length + len(unit) + 1 > size:
            chunks.append("\n".join(current))
            tail = current[-1]
            current, length = ([tail], len(tail)) if len(tail) <= size // 4 else ([], 0)
        current.append(unit)
        length += len(unit) + 1
    if current:
        chunks.append("\n".join(current))
    return [c for c in chunks if c.strip()]


# ---- readers --------------------------------------------------------------------------------------


def _decode(content: bytes) -> str:
    if content.startswith((b"\xff\xfe", b"\xfe\xff")):
        return content.decode("utf-16", errors="replace")
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def _read_text(content: bytes) -> Extraction:
    text = clean_text(_decode(content))
    return Extraction.ready(text) if text else Extraction.stored(DETAIL_EMPTY)


def _read_csv(content: bytes) -> Extraction:
    raw = _decode(content)
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    lines = []
    for row in csv.reader(io.StringIO(raw), dialect):
        values = [cell.strip() for cell in row if cell.strip()]
        if values:
            lines.append(" | ".join(values))
        if len(lines) >= MAX_ROWS_PER_SHEET:
            break
    text = clean_text("\n".join(lines))
    return Extraction.ready(text) if text else Extraction.stored(DETAIL_EMPTY)


def _read_pdf(content: bytes) -> Extraction:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(content), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return Extraction.failed(DETAIL_PARSE_FAILED)
        parts: list[str] = []
        total = 0
        for page in reader.pages[:MAX_PDF_PAGES]:
            try:
                piece = page.extract_text() or ""
            except Exception:  # noqa: BLE001 - one unreadable page does not lose the others
                log.debug("a PDF page could not be read")
                continue
            parts.append(piece)
            total += len(piece)
            if total >= MAX_TEXT_CHARS:
                break
    except Exception:  # noqa: BLE001 - a damaged or hostile file is a failed reading, not a crash
        log.warning("a PDF could not be read")
        return Extraction.failed(DETAIL_PARSE_FAILED)
    text = clean_text("\n".join(parts))
    return Extraction.ready(text) if len(text) >= 20 else Extraction.stored(DETAIL_SCANNED_PDF)


def _member(archive: zipfile.ZipFile, name: str) -> str | None:
    try:
        info = archive.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_ZIP_MEMBER_BYTES:
        raise ValueError("archive member too large")
    with archive.open(info) as handle:
        data = handle.read(MAX_ZIP_MEMBER_BYTES + 1)
    if len(data) > MAX_ZIP_MEMBER_BYTES:
        raise ValueError("archive member too large")
    return data.decode("utf-8", errors="replace")


def _open_archive(content: bytes) -> zipfile.ZipFile:
    archive = zipfile.ZipFile(io.BytesIO(content))
    if len(archive.namelist()) > MAX_ZIP_ENTRIES:
        raise ValueError("archive has too many entries")
    return archive


_RUN_TEXT = re.compile(r"<w:t(?:\s[^>]*)?>(.*?)</w:t>|<w:tab\s*/>|<w:br\s*/>", re.DOTALL)
_PARAGRAPH = re.compile(r"<w:p[\s>].*?</w:p>", re.DOTALL)


def _read_docx(content: bytes) -> Extraction:
    try:
        with _open_archive(content) as archive:
            xml = _member(archive, "word/document.xml") or ""
    except (zipfile.BadZipFile, ValueError, OSError):
        return Extraction.failed(DETAIL_PARSE_FAILED)
    lines = []
    for paragraph in _PARAGRAPH.findall(xml):
        pieces = [
            unescape(m.group(1)) if m.group(1) is not None else " "
            for m in _RUN_TEXT.finditer(paragraph)
        ]
        lines.append("".join(pieces))
    text = clean_text("\n".join(lines))
    return Extraction.ready(text) if text else Extraction.stored(DETAIL_EMPTY)


_SHARED = re.compile(r"<si\b.*?</si>", re.DOTALL)
_T = re.compile(r"<t(?:\s[^>]*)?>(.*?)</t>", re.DOTALL)
_ROW = re.compile(r"<row\b[^>]*>(.*?)</row>", re.DOTALL)
_CELL = re.compile(r"<c\b([^>]*?)(?:/>|>(.*?)</c>)", re.DOTALL)
_V = re.compile(r"<v>(.*?)</v>", re.DOTALL)
_ATTR_T = re.compile(r'\bt="([^"]*)"')
_SHEET_NAME = re.compile(r"<sheet\b[^>]*\bname=\"([^\"]*)\"", re.DOTALL)


def _read_xlsx(content: bytes) -> Extraction:
    try:
        with _open_archive(content) as archive:
            shared_xml = _member(archive, "xl/sharedStrings.xml") or ""
            shared = [unescape("".join(_T.findall(si))) for si in _SHARED.findall(shared_xml)]
            names = [
                unescape(n) for n in _SHEET_NAME.findall(_member(archive, "xl/workbook.xml") or "")
            ]
            sheets = sorted(
                (n for n in archive.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                key=lambda n: int(re.findall(r"\d+", n)[-1]),
            )[:MAX_SHEETS]
            lines: list[str] = []
            for index, sheet in enumerate(sheets):
                xml = _member(archive, sheet) or ""
                if index < len(names):
                    lines.append(f"[{names[index]}]")
                for row in _ROW.findall(xml)[:MAX_ROWS_PER_SHEET]:
                    values = []
                    for attrs, body in _CELL.findall(row):
                        kind = (_ATTR_T.search(attrs) or [None, ""])[1]
                        body = body or ""
                        if kind == "inlineStr":
                            value = unescape("".join(_T.findall(body)))
                        else:
                            found = _V.search(body)
                            raw = unescape(found.group(1)) if found else ""
                            if kind == "s":
                                value = (
                                    shared[int(raw)]
                                    if raw.isdigit() and int(raw) < len(shared)
                                    else ""
                                )
                            else:
                                value = raw
                        if value.strip():
                            values.append(value.strip())
                    if values:
                        lines.append(" | ".join(values))
    except (zipfile.BadZipFile, ValueError, OSError):
        return Extraction.failed(DETAIL_PARSE_FAILED)
    text = clean_text("\n".join(lines))
    return Extraction.ready(text) if text else Extraction.stored(DETAIL_EMPTY)


_SKIPPED = {
    "script",
    "style",
    "noscript",
    "svg",
    "template",
    "head",
    "nav",
    "footer",
    "aside",
    "form",
    "iframe",
}
_BLOCKS = {
    "p",
    "div",
    "br",
    "li",
    "ul",
    "ol",
    "tr",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "section",
    "article",
    "table",
    "blockquote",
    "main",
    "header",
    "dd",
    "dt",
}


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self._skip = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "title":
            self._in_title = True
        if tag in _SKIPPED:
            self._skip += 1
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in _SKIPPED and self._skip:
            self._skip -= 1
        elif tag in _BLOCKS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif not self._skip:
            self.parts.append(data)


def _charset(content_type: str, head: bytes) -> str | None:
    found = re.search(r"charset=([\w-]+)", content_type, re.IGNORECASE) or re.search(
        rb"charset=[\"']?([\w-]+)", head, re.IGNORECASE
    )
    if not found:
        return None
    value = found.group(1)
    return value.decode("ascii", "ignore") if isinstance(value, bytes) else value


def read_html(content: bytes, content_type: str = "") -> Extraction:
    """The visible text of a web page. A page that is only a shell for scripts says so."""
    charset = _charset(content_type, content[:2048])
    try:
        html = content.decode(charset or "utf-8", errors="replace")
    except LookupError:
        html = content.decode("utf-8", errors="replace")
    parser = _VisibleText()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup is a failed reading
        return Extraction.failed(DETAIL_PARSE_FAILED)
    body = clean_text("".join(parser.parts))
    title = clean_text(parser.title)
    text = f"{title}\n{body}".strip() if title and title not in body[:200] else body
    return (
        Extraction.ready(text)
        if len(text) >= MIN_PAGE_CHARS
        else Extraction.stored(DETAIL_PAGE_NOT_TEXT)
    )


_READERS: dict[str, Callable[[bytes], Extraction]] = {
    "txt": _read_text,
    "md": _read_text,
    "csv": _read_csv,
    "pdf": _read_pdf,
    "docx": _read_docx,
    "xlsx": _read_xlsx,
}


def extract_file(extension: str, content: bytes) -> Extraction:
    """What can really be read from an uploaded file of this type (already checked by `matches_type`)."""
    category = FILE_TYPES.get(extension)
    if category == "image":
        return Extraction.stored(DETAIL_IMAGE)
    if category == "video":
        return Extraction.stored(DETAIL_VIDEO)
    reader = _READERS.get(extension)
    if reader is None:
        return Extraction.failed(DETAIL_PARSE_FAILED)
    return reader(content)


def extract_pdf(content: bytes) -> Extraction:
    return _read_pdf(content)
