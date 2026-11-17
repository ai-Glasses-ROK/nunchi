"""한국어 텍스트 유틸리티: 정규화, 마크다운 제거(음성용), 문장 분리, 스트리밍 문장 조립."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Iterator

_WS = re.compile(r"[ \t 　]+")
_MULTI_NL = re.compile(r"\n{3,}")


def normalize(text: str) -> str:
    """유니코드 NFC 정규화 + 공백 정리 (문서를 저장할 때).

    NFC는 풀어 쓴 한글 자모(맥 파일명·일부 PDF)를 완성형으로 합칠 뿐, 기호는 바꾸지 않습니다.
    전각 문자(Ａ, １)·원문자까지 하나로 맞추는 NFKC는 **검색 토큰화(bm25.tokenize)에서만** 씁니다.
    저장할 때 NFKC를 쓰면 LLM이 받는 글도 바뀌어서, 시험지의 선택지 ①~⑤가 1~5로, ㉠·ⓐ 같은 표시가
    엉뚱한 글자로 바뀝니다 (수능 실험에서 정답률을 떨어뜨린 원인 중 하나).
    """
    text = unicodedata.normalize("NFC", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return _MULTI_NL.sub("\n\n", text).strip()


# --- 음성용 정리 ---------------------------------------------------------------

_MD_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"```.*?```", re.S), " "),  # 코드 블록
    (re.compile(r"`([^`]*)`"), r"\1"),  # 인라인 코드
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),  # 이미지
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),  # 링크 → 텍스트
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.M), ""),  # 제목 기호
    (re.compile(r"^\s*>\s?", re.M), ""),  # 인용
    (re.compile(r"^\s*[-*+•·]\s+", re.M), ""),  # 글머리표
    (re.compile(r"^\s*\d+[.)]\s+", re.M), ""),  # 번호 목록
    (re.compile(r"\*\*|__|~~"), ""),  # 굵게/취소선
    (re.compile(r"\*(?!\s)([^*\n]+?)(?<!\s)\*"), r"\1"),  # 기울임 *강조* (한국어는 뒤에 조사가 바로 붙음)
    (re.compile(r"(?<!\w)_(?!\s)([^_\n]+?)(?<!\s)_(?!\w)"), r"\1"),  # 기울임 _강조_ (snake_case는 보존)
    (re.compile(r"\|"), " "),  # 표 구분자
    (re.compile(r"^\s*:?-{3,}:?\s*$", re.M), ""),  # 표 구분선/수평선
    (re.compile(r"\[(?:\d+(?:\s*[,\-]\s*\d+)*)\]"), ""),  # 인용 번호 [1], [1,2]
    (re.compile(r"\((?:출처|source)[^)]*\)", re.I), ""),
]

# 이모지·기호 범위 (TTS가 이상하게 읽거나 멈추는 원인)
_EMOJI = re.compile(
    "["
    "\U0001f300-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f000-\U0001f2ff"
    "←-⇿"
    "⬀-⯿"
    "️"
    "]+"
)


def strip_markdown(text: str) -> str:
    """LLM 답변에서 마크다운·이모지·인용 번호를 걷어내 '읽기 좋은 문장'으로 만듭니다."""
    out = text
    for pattern, repl in _MD_RULES:
        out = pattern.sub(repl, out)
    out = _EMOJI.sub("", out)
    out = re.sub(r"[ \t]+([.,!?])", r"\1", out)  # 인용 번호를 지운 자리의 '공백+마침표' 정리
    out = re.sub(r"[ \t]+", " ", out)
    out = re.sub(r"\s*\n\s*", "\n", out)
    return out.strip()


def to_speech_text(text: str) -> str:
    """TTS에 넣을 최종 텍스트: 마크다운 제거 + 줄바꿈을 문장 경계로."""
    cleaned = strip_markdown(text)
    lines = [ln.strip() for ln in cleaned.split("\n") if ln.strip()]
    fixed: list[str] = []
    for ln in lines:
        # 줄 끝에 문장부호가 없으면 붙여서 TTS가 자연스럽게 쉬게 함
        if ln[-1] not in ".!?。！？…:;,~":
            ln += "."
        fixed.append(ln)
    return " ".join(fixed)


# --- 문장 분리 ------------------------------------------------------------------

# 문장 끝: . ! ? … 。 ！ ？ 뒤에 공백/줄바꿈/끝, 또는 줄바꿈 자체.
# '3.5' 같은 소수점은 뒤에 공백이 없어서 끊기지 않습니다.
_SENT_END = re.compile(r"(?<=[.!?…。！？])[\"'”’)\]]*\s+|\n+")


def split_sentences(text: str) -> list[str]:
    parts = [p.strip() for p in _SENT_END.split(text)]
    return [p for p in parts if p]


def chunk_for_tts(text: str, max_chars: int = 80) -> list[str]:
    """TTS 요청 단위로 자르기. 너무 긴 문장은 쉼표·공백에서 한 번 더 자릅니다.

    짧은 단위로 나누면 (1) 첫 소리가 빨리 나오고 (2) 일부 TTS 모델의
    긴 문장 잘림 현상을 줄일 수 있습니다.
    """
    pieces: list[str] = []
    for sent in split_sentences(text):
        pieces.extend(_split_long(sent, max_chars))
    return pieces


def _split_long(sentence: str, max_chars: int) -> list[str]:
    if len(sentence) <= max_chars:
        return [sentence]
    out: list[str] = []
    rest = sentence
    while len(rest) > max_chars:
        window = rest[:max_chars]
        cut = max(window.rfind(", "), window.rfind("，"), window.rfind("、"))
        if cut < max_chars // 3:
            cut = window.rfind(" ")
        if cut < max_chars // 3:
            cut = max_chars - 1
        out.append(rest[: cut + 1].strip())
        rest = rest[cut + 1 :].strip()
    if rest:
        out.append(rest)
    return out


class SentenceStreamer:
    """LLM 토큰 스트림을 받아 '완성된 문장'이 생길 때마다 내보냅니다.

    음성 비서에서 지연을 줄이는 핵심 기법: 답변 전체를 기다리지 않고
    첫 문장이 완성되는 즉시 TTS를 시작합니다.
    """

    def __init__(self, max_chars: int = 120) -> None:
        self.buf = ""
        self.max_chars = max_chars

    def feed(self, delta: str) -> list[str]:
        self.buf += delta
        out: list[str] = []
        while True:
            m = _SENT_END.search(self.buf)
            if not m:
                break
            sent = self.buf[: m.start()].strip()
            self.buf = self.buf[m.end() :]
            if sent:
                out.append(sent)
        # 문장부호 없이 계속 길어지면 쉼표/공백에서 강제로 자름
        if len(self.buf) > self.max_chars:
            window = self.buf[: self.max_chars]
            cut = max(window.rfind(", "), window.rfind(" "))
            if cut > self.max_chars // 3:
                out.append(self.buf[: cut + 1].strip())
                self.buf = self.buf[cut + 1 :]
        return out

    def flush(self) -> list[str]:
        rest = self.buf.strip()
        self.buf = ""
        return [rest] if rest else []


def iter_sentences(deltas: Iterable[str]) -> Iterator[str]:
    s = SentenceStreamer()
    for d in deltas:
        yield from s.feed(d)
    yield from s.flush()


def shorten(text: str, limit: int = 80) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"
