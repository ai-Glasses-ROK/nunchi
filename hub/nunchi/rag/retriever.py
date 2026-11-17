"""하이브리드 검색 = 키워드(BM25) + 의미(임베딩) 결과를 RRF로 합치기.

- BM25는 '정확한 단어'(제품명, 번호, 고유명사)에 강하고
- 임베딩은 '뜻이 비슷한 말'(예: "와이파이 암호" ↔ "무선 비밀번호")에 강합니다.
- RRF(Reciprocal Rank Fusion)는 점수 대신 '순위'만 보고 합치므로 두 점수의 단위가 달라도 안전합니다.
  점수 = Σ 1 / (60 + 순위)

임베딩 서버가 꺼져 있거나 모델이 없으면 자동으로 BM25만으로 검색합니다(오프라인 현장에서 중요한 안전장치).

번호 조회 + 절 확장 (number lookup + section expansion)
  "국어 5번 문제", "3단계", "12쪽"처럼 번호로 물으면, 같은 번호가 붙은 제목(헤딩)의 절을 검색 대신
  목차로 바로 찾아가 그 절 전체를 한 덩어리로 돌려줍니다 (작게 찾고 크게 돌려주는 small-to-big).
  2-gram BM25에서 '5'는 선택지 ⑤(NFKC → 5)·번호 목록 때문에 거의 모든 조각에 들어 있어 변별력이 없고,
  임베딩도 숫자에 약해서, 수능 실험에서 "국어 5번 문제" 검색 적중률이 12~32%에 그쳤습니다.
"""

from __future__ import annotations

import re
import threading
import unicodedata
from dataclasses import dataclass, field

import numpy as np

from nunchi.rag.bm25 import BM25, tokenize, tokenize_query
from nunchi.rag.embedder import Embedder, EmbeddingError
from nunchi.rag.store import Store, StoredChunk

RRF_K = 60
# 키워드가 하나도 안 겹치고 의미 유사도도 이보다 낮으면 '관련 없음'으로 보고 버립니다.
MIN_DENSE_ONLY = 0.35
# 질문 속 '번호 + 단위'. 앞에 숫자·점이 붙은 경우(15번의 5, 1.5쪽)는 제외
NUM_REF = re.compile(r"(?<![\d.])0*(\d{1,4})\s*(번|쪽|페이지|장|조|항|절|단계|호)")
MAX_SECTION_CHARS = 6000


@dataclass
class Hit:
    chunk: StoredChunk
    score: float
    bm25: float | None = None
    dense: float | None = None
    rank_bm25: int | None = None
    rank_dense: int | None = None

    def to_dict(self, n: int) -> dict:
        return {
            "n": n,
            "chunk_id": self.chunk.id,
            "source": self.chunk.source,
            "title": self.chunk.title,
            "heading": self.chunk.heading,
            "label": self.chunk.label,
            "text": self.chunk.text,
            "score": round(self.score, 5),
            "bm25": None if self.bm25 is None else round(self.bm25, 3),
            "dense": None if self.dense is None else round(self.dense, 4),
        }


@dataclass
class SearchInfo:
    mode: str
    warnings: list[str] = field(default_factory=list)
    lookup: list[str] = field(default_factory=list)  # 번호 조회로 바로 찾아간 절의 이름


class Retriever:
    def __init__(
        self,
        store: Store,
        embedder: Embedder | None,
        mode: str = "hybrid",
        query_prefix: str = "",
        number_lookup: bool = True,
        max_section_chars: int = MAX_SECTION_CHARS,
        lookup_exclusive: bool = True,
    ):
        self.store = store
        self.embedder = embedder
        self.mode = mode
        self.query_prefix = query_prefix
        self.number_lookup = number_lookup
        self.max_section_chars = max_section_chars
        self.lookup_exclusive = lookup_exclusive
        self._lock = threading.Lock()
        self._stamp: str | None = None
        self._chunks: list[StoredChunk] = []
        self._bm25: BM25 | None = None
        self._emb_rows: np.ndarray = np.zeros(0, dtype=np.int64)  # 행렬의 각 행 → self._chunks 인덱스
        self._emb: np.ndarray = np.zeros((0, 0), dtype=np.float32)

    def refresh(self, force: bool = False) -> None:
        with self._lock:
            stamp = self.store.stamp()
            if not force and stamp == self._stamp:
                return
            chunks = self.store.load_chunks()
            id_to_idx = {c.id: i for i, c in enumerate(chunks)}
            ids, mat = self.store.load_embeddings()
            keep = [j for j, cid in enumerate(ids) if int(cid) in id_to_idx]
            self._chunks = chunks
            self._bm25 = BM25([c.search_text() for c in chunks])
            self._emb_rows = np.array([id_to_idx[int(ids[j])] for j in keep], dtype=np.int64)
            self._emb = mat[keep] if len(keep) else np.zeros((0, 0), dtype=np.float32)
            self._stamp = stamp

    @property
    def size(self) -> int:
        self.refresh()
        return len(self._chunks)

    def search(self, query: str, k: int = 4, candidates: int = 24) -> tuple[list[Hit], SearchInfo]:
        self.refresh()
        info = SearchInfo(mode=self.mode)
        if not self._chunks or not query.strip():
            return [], info

        bm25_ranked: list[tuple[int, float]] = []
        if self.mode in ("hybrid", "bm25") and self._bm25 is not None:
            bm25_ranked = self._bm25.search(query, candidates)

        dense_ranked: list[tuple[int, float]] = []
        if self.mode in ("hybrid", "dense"):
            if self.embedder is None or self._emb.size == 0:
                info.warnings.append("임베딩이 없어 키워드 검색(BM25)만 사용했어요")
                info.mode = "bm25"
                if not bm25_ranked and self._bm25 is not None:
                    bm25_ranked = self._bm25.search(query, candidates)
            else:
                try:
                    qv = self.embedder.embed([self.query_prefix + query])[0]
                    if qv.shape[0] != self._emb.shape[1]:
                        raise EmbeddingError(
                            f"질의 임베딩 차원({qv.shape[0]})과 색인 차원({self._emb.shape[1]})이 달라요. "
                            "임베딩 모델을 바꿨다면 `nunchi ingest --reembed` 를 실행하세요."
                        )
                    sims = self._emb @ qv
                    top = np.argsort(-sims)[:candidates]
                    dense_ranked = [(int(self._emb_rows[j]), float(sims[j])) for j in top]
                except EmbeddingError as e:
                    info.warnings.append(f"의미 검색 실패 → 키워드 검색만 사용: {e}")
                    info.mode = "bm25"
                    if not bm25_ranked and self._bm25 is not None:
                        bm25_ranked = self._bm25.search(query, candidates)

        fused: dict[int, Hit] = {}
        for rank, (idx, s) in enumerate(bm25_ranked, start=1):
            h = fused.setdefault(idx, Hit(chunk=self._chunks[idx], score=0.0))
            h.score += 1.0 / (RRF_K + rank)
            h.bm25, h.rank_bm25 = s, rank
        for rank, (idx, s) in enumerate(dense_ranked, start=1):
            h = fused.setdefault(idx, Hit(chunk=self._chunks[idx], score=0.0))
            h.score += 1.0 / (RRF_K + rank)
            h.dense, h.rank_dense = s, rank

        hits = sorted(fused.values(), key=lambda h: -h.score)
        hits = [h for h in hits if h.rank_bm25 is not None or (h.dense or 0) >= MIN_DENSE_ONLY]

        if self.number_lookup:
            sections = self._lookup_sections(query)
            if sections:
                taken = {self._chunks[i].id for sec in sections for i in sec}
                merged = [self._merge_section(sec, query) for sec in sections]
                info.lookup = [h.chunk.label for h in merged]
                # 번호로 물었으면 그 절만 줍니다(기본). 다른 조각을 섞으면 작은 모델이 헷갈림 (수능 실험)
                hits = merged if self.lookup_exclusive else merged + [h for h in hits if h.chunk.id not in taken]
        return hits[:k], info

    # 번호 조회 ------------------------------------------------------------
    def _lookup_sections(self, query: str) -> list[list[int]]:
        """질문의 '5번'·'3단계' 등과 같은 번호가 제목에 있는 절을 찾아, 번호마다 가장 잘 맞는 절 하나씩."""
        refs: list[tuple[str, str]] = []
        for m in NUM_REF.finditer(unicodedata.normalize("NFKC", query)):
            if (m.group(1), m.group(2)) not in refs:
                refs.append((m.group(1), m.group(2)))
        if not refs:
            return []
        # 나중에 나온 번호가 우선: 후속 질문은 '직전 질문 + 지금 질문'으로 검색하므로("5번 … 그럼 6번은?") 지금 묻는 번호가 뒤에 옴
        refs.reverse()
        scores = self._bm25.scores(query) if self._bm25 is not None else None
        qtok = set(tokenize_query(NUM_REF.sub(" ", query)))
        out: list[list[int]] = []
        for num, unit in refs[:2]:
            pat = re.compile(rf"(?<![\d.])0*{num}\s*{unit}")
            groups: dict[tuple[int, str], list[int]] = {}
            for i, c in enumerate(self._chunks):
                if c.heading and pat.search(unicodedata.normalize("NFKC", c.heading)):
                    groups.setdefault((c.doc_id, c.heading), []).append(i)
            if groups:
                # 같은 번호의 절이 여러 문서에 있으면(국어 20번 / 영어 20번) 질문의 나머지 단어가
                # 문서 제목·절 제목과 얼마나 겹치는지로 먼저 고르고, 같으면 본문 점수로
                def rank(idx: list[int]) -> tuple[int, float]:
                    in_label = len(qtok & set(tokenize(self._chunks[idx[0]].label)))
                    return in_label, float(scores[idx].max()) if scores is not None else 0.0

                best = max(groups.values(), key=rank)
                if best not in out:
                    out.append(best)
        return out

    def _merge_section(self, idx: list[int], query: str) -> Hit:
        """절의 조각들을 순서대로 이어 붙임(겹침 제거). 너무 길면 질문과 가까운 조각만 골라 원래 순서로."""
        if sum(len(self._chunks[i].text) for i in idx) > self.max_section_chars and self._bm25 is not None:
            scores = self._bm25.scores(query)
            keep, total = [], 0
            for i in sorted(idx, key=lambda j: -float(scores[j])):
                if total + len(self._chunks[i].text) > self.max_section_chars and keep:
                    continue
                keep.append(i)
                total += len(self._chunks[i].text)
            idx = sorted(keep)
        first = self._chunks[idx[0]]
        text = first.text
        for i in idx[1:]:
            text = join_overlap(text, self._chunks[i].text)
        chunk = StoredChunk(
            id=first.id, doc_id=first.doc_id, source=first.source, title=first.title, heading=first.heading, text=text
        )
        return Hit(chunk=chunk, score=1.0)


def join_overlap(a: str, b: str, max_overlap: int = 400, min_overlap: int = 12) -> str:
    """앞 조각의 끝과 뒤 조각의 시작이 겹치면(청커의 overlap) 한 번만 남기고 잇습니다."""
    for n in range(min(len(a), len(b), max_overlap), min_overlap - 1, -1):
        if a.endswith(b[:n]):
            return a + b[n:]
    return a + "\n\n" + b
