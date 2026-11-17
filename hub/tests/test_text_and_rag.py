import numpy as np

from nunchi.rag.bm25 import BM25, tokenize, tokenize_query
from nunchi.rag.chunker import chunk_text
from nunchi.rag.loaders import decode_text, load_bytes
from nunchi.rag.retriever import Retriever
from nunchi.textutil import SentenceStreamer, chunk_for_tts, split_sentences, strip_markdown, to_speech_text


# --- 텍스트 -----------------------------------------------------------------------
def test_strip_markdown_for_speech():
    md = "## 답\n**체크인**은 *오후 2시*부터예요 [1]. 😀\n- 주말: 3시\n[링크](http://x.y)"
    out = strip_markdown(md)
    assert "*" not in out and "#" not in out and "[1]" not in out and "😀" not in out
    assert "체크인은 오후 2시부터예요" in out
    assert "링크" in out and "http" not in out


def test_to_speech_text_adds_periods():
    assert to_speech_text("첫 줄\n둘째 줄") == "첫 줄. 둘째 줄."


def test_split_sentences_keeps_decimals():
    sents = split_sentences("버전은 3.5입니다. 다음은? 끝!")
    assert sents == ["버전은 3.5입니다.", "다음은?", "끝!"]


def test_sentence_streamer_emits_as_soon_as_complete():
    s = SentenceStreamer()
    out = []
    for piece in ["체크인은 오후 ", "2시부터", "예요. 주말", "은 3시예요."]:
        out += s.feed(piece)
    assert out == ["체크인은 오후 2시부터예요."]
    assert s.flush() == ["주말은 3시예요."]


def test_chunk_for_tts_splits_long_sentence():
    long = "가나다라마바사, " * 20
    pieces = chunk_for_tts(long, max_chars=40)
    assert all(len(p) <= 40 for p in pieces)
    assert "".join(pieces).replace(" ", "") == long.replace(" ", "").strip()


# --- 문서 읽기 / 조각내기 ------------------------------------------------------------
def test_decode_cp949():
    assert decode_text("한국어 문서".encode("cp949")) == "한국어 문서"


def test_load_markdown_title():
    doc = load_bytes("# 제목입니다\n\n본문".encode(), name="a.md")
    assert doc.title == "제목입니다" and doc.kind == "md"


def test_load_csv_as_sentences():
    doc = load_bytes("이름,층\n회의실A,3\n".encode(), name="rooms.csv")
    assert "이름: 회의실A / 층: 3" in doc.text


def test_chunker_respects_headings_and_size():
    text = "# 문서\n\n## 가\n" + ("가나다라 마바사. " * 60) + "\n\n## 나\n짧은 문단."
    chunks = chunk_text(text, chunk_chars=200, overlap=40, skip_title="문서")
    assert all(len(c.text) <= 260 for c in chunks)  # overlap 포함 여유
    assert {c.heading for c in chunks} == {"가", "나"}
    assert chunks[-1].text == "짧은 문단."
    assert chunks[0].embed_text("문서").startswith("문서 > 가\n")


# --- BM25 ------------------------------------------------------------------------
def test_tokenize_korean_bigrams():
    assert tokenize("회의실은 2시간") == ["회의", "의실", "실은", "2", "시간"]


def test_query_stopwords_removed():
    assert "알려" not in tokenize_query("체크인 시간 알려줘")


def test_bm25_ranks_relevant_first_despite_particles():
    docs = ["회의실은 최대 두 시간 예약할 수 있습니다", "주차장은 지하 2층입니다", "식당은 12시에 엽니다"]
    bm = BM25(docs)
    top = bm.search("회의실이 몇 시간까지 돼?", k=3)
    assert top[0][0] == 0


# --- 검색 -----------------------------------------------------------------------
def test_hybrid_search_finds_campsite_rule(fake_hub):
    hits, info = fake_hub.retriever.search("매너 타임은 언제야", k=3)
    assert hits and "매너 타임" in hits[0].chunk.heading
    assert info.mode == "hybrid"


def test_search_falls_back_to_bm25_when_embedder_breaks(fake_hub):
    from nunchi.rag.embedder import EmbeddingError

    class Broken:
        model = "broken"

        def embed(self, texts):
            raise EmbeddingError("서버 꺼짐")

    r = Retriever(fake_hub.store, Broken())
    hits, info = r.search("발전기 E2 코드", k=2)
    assert hits and info.mode == "bm25" and info.warnings
    assert "고장 코드" in hits[0].chunk.heading


def test_dimension_mismatch_is_reported(fake_hub):
    from nunchi.rag.embedder import HashEmbedder

    r = Retriever(fake_hub.store, HashEmbedder(dim=64))  # 색인은 256차원
    hits, info = r.search("체크인", k=2)
    assert hits and info.mode == "bm25"
    assert any("차원" in w for w in info.warnings)


def test_reingest_skips_unchanged(fake_hub):
    from tests.conftest import KNOWLEDGE

    stats = fake_hub.ingest_paths([KNOWLEDGE], log_fn=lambda *_: None)
    assert stats["skipped"] == stats["files"] and stats["added"] == 0


def test_store_embeddings_are_normalized(fake_hub):
    ids, mat = fake_hub.store.load_embeddings()
    assert len(ids) == mat.shape[0] > 0
    assert np.allclose(np.linalg.norm(mat, axis=1), 1.0, atol=1e-4)


def test_dotenv_inline_comments_and_empty_values(tmp_path, monkeypatch):
    from nunchi.config import Settings, load_dotenv

    env = tmp_path / ".env"
    env.write_text(
        "NUNCHI_TOKEN=                # 비어 있으면 토큰 없음\n"
        "NUNCHI_LLM_MODEL=gemma4:e2b  # 작은 모델\n"
        "NUNCHI_TTS_VOICE='male korean'\n"
        "NUNCHI_TOP_K=6\t# 탭 뒤 주석\n"
        "# NUNCHI_PORT=1\n",
        encoding="utf-8",
    )
    for k in ("NUNCHI_TOKEN", "NUNCHI_LLM_MODEL", "NUNCHI_TTS_VOICE", "NUNCHI_TOP_K", "NUNCHI_PORT"):
        monkeypatch.delenv(k, raising=False)
    loaded = load_dotenv(env)
    assert loaded["NUNCHI_TOKEN"] == ""
    assert loaded["NUNCHI_LLM_MODEL"] == "gemma4:e2b"
    assert loaded["NUNCHI_TTS_VOICE"] == "male korean"
    assert loaded["NUNCHI_TOP_K"] == "6"
    assert "NUNCHI_PORT" not in loaded
    s = Settings.from_env(dotenv=None, data_dir=str(tmp_path / "d"))
    assert s.token == "" and s.llm_model == "gemma4:e2b" and s.top_k == 6


def test_env_example_matches_settings(tmp_path, monkeypatch):
    """hub/.env.example 의 모든 키가 실제 설정 항목이고, 주석이 값으로 새지 않는지."""
    from dataclasses import fields
    from pathlib import Path

    from nunchi.config import ENV_PREFIX, Settings, load_dotenv

    example = Path(__file__).resolve().parents[1] / ".env.example"
    keys = [
        line.split("=", 1)[0].strip()
        for line in example.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    for k in keys:
        monkeypatch.delenv(k, raising=False)
    loaded = load_dotenv(example)
    valid = {ENV_PREFIX + f.name.upper() for f in fields(Settings)}
    assert set(keys) <= valid, set(keys) - valid
    assert not [k for k, v in loaded.items() if "#" in v]
    s = Settings.from_env(dotenv=None, data_dir=str(tmp_path / "d"))
    assert s.token == "" and s.vision_model == "" and s.stt_model == ""


def _exam_store(tmp_path):
    """문항 번호 제목이 있는 시험지형 문서 (긴 지문 → 여러 조각으로 나뉨)."""
    from nunchi.rag.chunker import chunk_text
    from nunchi.rag.embedder import HashEmbedder
    from nunchi.rag.loaders import Document
    from nunchi.rag.store import Store

    store = Store(tmp_path / "exam.sqlite3")
    emb = HashEmbedder()

    def add(source, title, sections):
        md = f"# {title}\n\n" + "\n\n".join(f"## {h}\n\n{body}" for h, body in sections)
        chunks = chunk_text(md, chunk_chars=200, overlap=60, skip_title=title)
        vecs = emb.embed([c.embed_text(title) for c in chunks])
        store.replace_document(Document(source=source, title=title, text=md, sha256=source, kind="md"), chunks, vecs)

    passage = " ".join(f"지문 문장 {i}번째 내용은 독해와 관련이 있다." for i in range(12))
    add("kor.md", "국어", [
        ("국어 5번 문항", f"{passage}\n\n5. 윗글의 내용과 일치하는 것은? ① 가 ② 나 ③ 다 ④ 라 ⑤ 마"),
        ("국어 15번 문항", "15. 다음 중 맞는 것은? ① 하나 ② 둘 ③ 셋 ④ 넷 ⑤ 다섯"),
        ("국어 25번 문항", "25. 다음 중 틀린 것은? ① 하나 ② 둘 ③ 셋 ④ 넷 ⑤ 다섯"),
        ("국어 20번 문항", "20. 국어 이십 번 발문입니다. ① 하나 ② 둘"),
    ])
    add("eng.md", "영어", [("영어 20번 문항", "20. What is the main idea? ① one ② two")])
    return store, emb


def test_number_lookup_returns_whole_section(tmp_path):
    store, emb = _exam_store(tmp_path)
    r = Retriever(store, emb)
    hits, info = r.search("국어 5번 문제 풀어 줘", k=3)
    top = hits[0].chunk
    assert top.heading == "국어 5번 문항" and info.lookup
    # 여러 조각으로 나뉜 절이 한 덩어리로: 지문 처음 + 발문·선택지 끝까지, 겹침은 한 번만
    assert "지문 문장 0번째" in top.text and "윗글의 내용과 일치하는 것은" in top.text
    assert top.text.count("지문 문장 11번째") == 1
    assert all("15번" not in h.chunk.heading and "25번" not in h.chunk.heading for h in hits[:1])


def test_number_lookup_disambiguates_by_other_words(tmp_path):
    store, emb = _exam_store(tmp_path)
    r = Retriever(store, emb)
    assert r.search("영어 20번", k=2)[0][0].chunk.heading == "영어 20번 문항"
    assert r.search("국어 20번 문제", k=2)[0][0].chunk.heading == "국어 20번 문항"


def test_number_lookup_can_be_disabled_and_ignores_plain_numbers(tmp_path, fake_hub):
    store, emb = _exam_store(tmp_path)
    hits, info = Retriever(store, emb, number_lookup=False).search("국어 5번 문제", k=3)
    assert not info.lookup
    hits, info = fake_hub.retriever.search("체크인은 2시부터야?", k=2)  # '2시'는 번호 조회 대상이 아님
    assert not info.lookup and hits


def test_join_overlap():
    from nunchi.rag.retriever import join_overlap

    a = "앞 조각의 본문입니다. 겹치는 마지막 문장은 이것입니다."
    b = "겹치는 마지막 문장은 이것입니다. 뒤 조각의 새 내용."
    assert join_overlap(a, b) == "앞 조각의 본문입니다. 겹치는 마지막 문장은 이것입니다. 뒤 조각의 새 내용."
    # 너무 짧은 우연한 겹침(12자 미만)은 겹침으로 보지 않음
    assert join_overlap("끝났다.", "다. 시작") == "끝났다.\n\n다. 시작"


def test_number_lookup_prefers_latest_number_in_follow_up(tmp_path):
    store, emb = _exam_store(tmp_path)
    hits, info = Retriever(store, emb).search("국어 5번 문제 풀어 줘 그럼 15번은?", k=3)
    assert hits[0].chunk.heading == "국어 15번 문항"
    assert hits[1].chunk.heading == "국어 5번 문항"  # 직전 문항도 비교용으로 함께


def test_number_lookup_is_exclusive_by_default(tmp_path):
    store, emb = _exam_store(tmp_path)
    hits, _ = Retriever(store, emb).search("국어 5번 문제", k=4)
    assert [h.chunk.heading for h in hits] == ["국어 5번 문항"]  # 번호로 물으면 그 절만
    hits, _ = Retriever(store, emb, lookup_exclusive=False).search("국어 5번 문제", k=4)
    assert hits[0].chunk.heading == "국어 5번 문항" and len(hits) > 1


def test_stored_text_keeps_symbols_but_search_folds_them():
    from nunchi.rag.bm25 import tokenize
    from nunchi.textutil import normalize

    raw = "㉠의 의미로 적절한 것은? ① 가 ② 나 · ＡＢＣ-123"
    kept = normalize(raw)
    assert "①" in kept and "㉠" in kept and "ＡＢＣ" in kept  # LLM에게는 원문 그대로
    assert "abc" in tokenize(kept) and "123" in tokenize(kept)  # 검색은 NFKC로 맞춤
    decomposed = "한글"  # 풀어 쓴 '한글'
    assert normalize(decomposed) == "한글"
