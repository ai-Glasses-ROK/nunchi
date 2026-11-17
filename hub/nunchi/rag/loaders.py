"""문서 불러오기: .md .txt .pdf .docx .html .csv

외부 라이브러리는 PDF(pypdf)만 씁니다. DOCX·HTML은 표준 라이브러리로 직접 읽습니다.
"""

from __future__ import annotations

import csv
import hashlib
import html
import io
import re
import zipfile
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from xml.etree import ElementTree

from nunchi.textutil import normalize

SUPPORTED_EXTS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".html", ".htm", ".csv"}


@dataclass
class Document:
    source: str  # 파일 경로(또는 업로드 이름)
    title: str
    text: str
    sha256: str
    kind: str  # md | text | pdf | docx | html | csv


def iter_files(paths: list[str | Path]) -> list[Path]:
    """경로 목록(파일/폴더)을 받아 지원하는 파일만 골라 정렬된 목록으로 돌려줍니다."""
    found: list[Path] = []
    for raw in paths:
        p = Path(raw).expanduser()
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTS and not _hidden(f, p):
                    found.append(f)
        elif p.is_file() and p.suffix.lower() in SUPPORTED_EXTS:
            found.append(p)
    # 중복 제거(순서 유지)
    seen: set[Path] = set()
    unique = []
    for f in found:
        r = f.resolve()
        if r not in seen:
            seen.add(r)
            unique.append(f)
    return unique


def _hidden(f: Path, root: Path) -> bool:
    try:
        rel = f.relative_to(root)
    except ValueError:
        return False
    return any(part.startswith(".") for part in rel.parts)


def load_path(path: str | Path) -> Document:
    p = Path(path)
    return load_bytes(p.read_bytes(), name=str(p))


def load_bytes(data: bytes, name: str) -> Document:
    ext = Path(name).suffix.lower()
    sha = hashlib.sha256(data).hexdigest()
    if ext in {".md", ".markdown"}:
        text, kind = decode_text(data), "md"
    elif ext == ".txt":
        text, kind = decode_text(data), "text"
    elif ext == ".pdf":
        text, kind = _read_pdf(data), "pdf"
    elif ext == ".docx":
        text, kind = _read_docx(data), "docx"
    elif ext in {".html", ".htm"}:
        text, kind = _read_html(decode_text(data)), "html"
    elif ext == ".csv":
        text, kind = _read_csv(decode_text(data)), "csv"
    else:
        raise ValueError(f"지원하지 않는 형식이에요: {ext} (지원: {', '.join(sorted(SUPPORTED_EXTS))})")
    text = normalize(text)
    return Document(source=name, title=_guess_title(text, name, kind), text=text, sha256=sha, kind=kind)


def decode_text(data: bytes) -> str:
    """UTF-8 → (실패 시) CP949 순서로 시도. 한국 윈도우에서 만든 .txt는 CP949인 경우가 많아요."""
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    for enc in ("utf-8", "cp949", "euc-kr"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _guess_title(text: str, name: str, kind: str) -> str:
    if kind == "md":
        m = re.search(r"^#\s+(.+)$", text, re.M)
        if m:
            return m.group(1).strip()
    stem = Path(name).stem
    return stem.replace("_", " ").replace("-", " ").strip() or stem


def _read_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:  # pragma: no cover
        raise RuntimeError("PDF를 읽으려면 pypdf가 필요해요: pip install pypdf") from e
    reader = PdfReader(io.BytesIO(data))
    pages = []
    for i, page in enumerate(reader.pages, start=1):
        t = page.extract_text() or ""
        if t.strip():
            pages.append(f"## {i}쪽\n\n{t.strip()}")
    if not pages:
        raise ValueError("PDF에서 글자를 찾지 못했어요. 스캔 이미지 PDF라면 OCR이 먼저 필요해요.")
    return "\n\n".join(pages)


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _read_docx(data: bytes) -> str:
    """DOCX = ZIP 안의 word/document.xml. 문단(w:p)마다 글자(w:t)를 모읍니다."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        xml = zf.read("word/document.xml")
    root = ElementTree.fromstring(xml)
    lines: list[str] = []
    for para in root.iter(f"{_W}p"):
        style = para.find(f"{_W}pPr/{_W}pStyle")
        style_val = style.get(f"{_W}val", "") if style is not None else ""
        text = "".join(t.text or "" for t in para.iter(f"{_W}t"))
        if not text.strip():
            lines.append("")
            continue
        m = re.match(r"(?i)heading\s*(\d)|제목\s*(\d)", style_val)
        if m:
            level = int(m.group(1) or m.group(2) or 1)
            lines.append("#" * min(level + 1, 6) + " " + text.strip())
        else:
            lines.append(text)
    return "\n".join(lines)


class _HTMLText(HTMLParser):
    BLOCK = {"p", "div", "br", "li", "tr", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6"}
    SKIP = {"script", "style", "noscript", "head"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):  # noqa: ANN001
        if tag in self.SKIP:
            self.skip += 1
        elif tag in {"h1", "h2", "h3", "h4"}:
            self.parts.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):  # noqa: ANN001
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):  # noqa: ANN001
        if not self.skip:
            self.parts.append(data)


def _read_html(text: str) -> str:
    parser = _HTMLText()
    parser.feed(text)
    return html.unescape("".join(parser.parts))


def _read_csv(text: str) -> str:
    """CSV는 '열이름: 값' 형태의 문장으로 바꿔야 검색이 잘 됩니다."""
    rows = list(csv.reader(io.StringIO(text)))
    if not rows:
        return ""
    header = [h.strip() for h in rows[0]]
    out = []
    for row in rows[1:]:
        pairs = [f"{h}: {v.strip()}" for h, v in zip(header, row) if v.strip()]
        if pairs:
            out.append(" / ".join(pairs))
    return "\n\n".join(out)
