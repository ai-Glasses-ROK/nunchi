"""문서를 검색하기 좋은 크기의 '조각(chunk)'으로 나눕니다.

원칙
1. 마크다운 제목(#)을 따라가며 '제목 경로'(예: 캠핑장 안내 > 화재 안전)를 기억합니다.
   조각마다 제목 경로를 붙여 두면, 본문에 주제어가 없어도 검색이 잘 됩니다.
2. 문단을 최대한 깨지 않고 chunk_chars(기본 600자) 안으로 모읍니다.
   한국어는 영어보다 글자당 정보량이 많아서 600자 ≈ 영어 1,500자 정도의 내용입니다.
3. 앞 조각의 끝부분(overlap)을 다음 조각 앞에 겹쳐 붙여 문맥이 끊기지 않게 합니다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from nunchi.textutil import split_sentences

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")


@dataclass
class Chunk:
    ord: int
    heading: str  # "상위 제목 > 하위 제목"
    text: str

    def embed_text(self, title: str) -> str:
        """임베딩·BM25에 넣는 텍스트 = 제목 경로 + 본문 (맥락 헤더 기법)."""
        head = " > ".join(x for x in [title, self.heading] if x)
        return f"{head}\n{self.text}" if head else self.text


def _blocks(text: str) -> list[tuple[str, str]]:
    """(제목경로, 문단) 목록. 제목 줄 자체는 경로로만 쓰고 본문에서는 뺍니다."""
    path: list[tuple[int, str]] = []
    out: list[tuple[str, str]] = []
    para: list[str] = []
    in_code = False

    def flush() -> None:
        if para:
            body = "\n".join(para).strip()
            if body:
                out.append((" > ".join(t for _, t in path), body))
            para.clear()

    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            para.append(line)
            continue
        m = None if in_code else _HEADING.match(line)
        if m:
            flush()
            level, title = len(m.group(1)), m.group(2).strip()
            while path and path[-1][0] >= level:
                path.pop()
            path.append((level, title))
            continue
        if not line.strip() and not in_code:
            flush()
            continue
        para.append(line)
    flush()
    return out


def chunk_text(text: str, chunk_chars: int = 600, overlap: int = 120, skip_title: str = "") -> list[Chunk]:
    """본문을 조각냅니다. skip_title과 같은 최상위 제목은 경로에서 생략(중복 방지)."""
    chunk_chars = max(120, chunk_chars)
    overlap = max(0, min(overlap, chunk_chars // 2))

    units: list[tuple[str, str]] = []  # (heading, 작은 단위 텍스트)
    for heading, block in _blocks(text):
        if skip_title and (heading == skip_title or heading.startswith(skip_title + " > ")):
            heading = heading[len(skip_title) :].lstrip(" >")
        if len(block) <= chunk_chars:
            units.append((heading, block))
        else:
            units.extend((heading, piece) for piece in _split_block(block, chunk_chars))

    chunks: list[Chunk] = []
    cur_head = None
    cur: list[str] = []
    cur_len = 0

    def emit() -> None:
        nonlocal cur, cur_len
        if cur:
            body = "\n\n".join(cur).strip()
            if body:
                chunks.append(Chunk(ord=len(chunks), heading=cur_head or "", text=body))
        cur, cur_len = [], 0

    for heading, unit in units:
        # 제목이 바뀌면 새 조각 시작(주제 섞임 방지)
        if cur and (heading != cur_head or cur_len + len(unit) + 2 > chunk_chars):
            prev_text = "\n\n".join(cur)
            same_section = heading == cur_head
            emit()
            if same_section and overlap:
                tail = _tail(prev_text, overlap)
                if tail:
                    cur, cur_len = [tail], len(tail)
        cur_head = heading
        cur.append(unit)
        cur_len += len(unit) + 2
    emit()
    return chunks


def _split_block(block: str, limit: int) -> list[str]:
    """긴 문단은 문장 단위로, 긴 문장은 글자 수로 자릅니다."""
    pieces: list[str] = []
    buf = ""
    for sent in split_sentences(block) or [block]:
        while len(sent) > limit:
            pieces.append(sent[:limit])
            sent = sent[limit:]
        if buf and len(buf) + len(sent) + 1 > limit:
            pieces.append(buf)
            buf = sent
        else:
            buf = f"{buf} {sent}".strip()
    if buf:
        pieces.append(buf)
    return pieces


def _tail(text: str, overlap: int) -> str:
    """끝에서부터 문장 단위로 overlap 글자 이내만큼 가져옵니다."""
    sents = split_sentences(text)
    tail: list[str] = []
    total = 0
    for s in reversed(sents):
        if total + len(s) > overlap:
            break
        tail.insert(0, s)
        total += len(s) + 1
    if not tail and sents:
        return sents[-1][-overlap:]
    return " ".join(tail)
