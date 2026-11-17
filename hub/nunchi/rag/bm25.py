"""한국어용 BM25 키워드 검색 (형태소 분석기 없이 '글자 2-gram' 사용).

왜 2-gram인가?
  '회의실은', '회의실이', '회의실을'처럼 한국어는 조사가 붙어 단어 모양이 계속 바뀝니다.
  '회의 / 의실 / 실은'처럼 두 글자씩 잘라 비교하면 조사가 달라도 대부분의 조각이 겹쳐서
  형태소 분석기 없이도 꽤 정확하게 찾을 수 있습니다. 폰(iOS/Android)에서도 똑같이 구현할 수 있어
  '폰 단독 오프라인 모드'의 검색기로도 씁니다 (clients/ 의 Swift·Kotlin 구현과 같은 규칙).
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter, defaultdict

import numpy as np

# 한글 음절/자모, 한자, 히라가나/가타카나, 영문·숫자
_RUN = re.compile(r"[가-힣ㄱ-ㆎ]+|[一-鿿]+|[぀-ヿ]+|[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    """'회의실 예약은 2시간' → ['회의', '의실', '예약', '약은', '2', '시간']"""
    text = unicodedata.normalize("NFKC", text).lower()
    tokens: list[str] = []
    for run in _RUN.findall(text):
        if _is_cjk(run[0]):
            if len(run) == 1:
                tokens.append(run)
            else:
                tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
        else:
            tokens.append(run)
    return tokens


# 질문에만 자주 나오고 내용과는 상관없는 2-gram (질문 꼬리말). 질의 쪽에서만 뺍니다.
QUERY_STOP = {
    "알려", "려줘", "려주", "주세", "세요", "해줘", "해주", "줄래", "주라", "뭐야", "뭐지", "뭔가",
    "무엇", "엇인", "인가", "인지", "어떻", "떻게", "어떤", "하나", "나요", "까요", "할까", "는지",
    "있어", "있나", "없어", "니다", "습니", "합니", "입니", "에요", "예요", "어요", "아요", "는데",
    "건가", "거야", "말해", "해봐", "궁금", "금해", "좀", "그럼", "그거", "이거", "저거",
}


def tokenize_query(text: str) -> list[str]:
    toks = [t for t in tokenize(text) if t not in QUERY_STOP]
    return toks or tokenize(text)


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return 0xAC00 <= o <= 0xD7A3 or 0x3131 <= o <= 0x318E or 0x4E00 <= o <= 0x9FFF or 0x3040 <= o <= 0x30FF


class BM25:
    """Okapi BM25. k1=문서 내 반복 단어의 가중치 포화 속도, b=문서 길이 보정 강도."""

    def __init__(self, docs: list[str], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.doc_len = np.zeros(self.n, dtype=np.float32)
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, doc in enumerate(docs):
            counts = Counter(tokenize(doc))
            self.doc_len[i] = sum(counts.values())
            for tok, tf in counts.items():
                self.postings[tok].append((i, tf))
        self.avgdl = float(self.doc_len.mean()) if self.n else 0.0
        self.idf = {
            tok: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for tok, p in self.postings.items()
        }

    def scores(self, query: str) -> np.ndarray:
        scores = np.zeros(self.n, dtype=np.float32)
        if not self.n:
            return scores
        norm = self.k1 * (1 - self.b + self.b * self.doc_len / max(self.avgdl, 1e-6))
        for tok in set(tokenize_query(query)):
            postings = self.postings.get(tok)
            if not postings:
                continue
            idf = self.idf[tok]
            idx = np.fromiter((i for i, _ in postings), dtype=np.int64, count=len(postings))
            tf = np.fromiter((t for _, t in postings), dtype=np.float32, count=len(postings))
            scores[idx] += idf * tf * (self.k1 + 1) / (tf + norm[idx])
        return scores

    def search(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        s = self.scores(query)
        if not self.n:
            return []
        k = min(k, self.n)
        top = np.argpartition(-s, k - 1)[:k]
        top = top[np.argsort(-s[top])]
        return [(int(i), float(s[i])) for i in top if s[i] > 0]
