"""허브의 두뇌: 음성/글자/사진 질문 → (STT) → 검색 → LLM → (TTS) 를 이벤트 스트림으로 처리합니다.

이벤트 종류 (서버의 SSE와 CLI가 그대로 사용)
  transcript  음성 인식 결과            {"text", "ms", "audio"}
  caption     사진 한 줄 묘사(사진 질문)  {"text", "ms"}
  retrieval   검색된 참고 문서          {"query", "sources", "ms", "mode", "warnings"}
  delta       LLM이 만든 글자 조각       {"text"}
  sentence    TTS로 읽을 완성 문장      {"index", "text"}
  audio       (speak=true일 때) 문장 음성 {"index", "wav_b64"}
  done        최종 결과                {"ok", "answer", "speech", "sources", "timings", ...}
  error       오류                     {"stage", "message"}
"""

from __future__ import annotations

import base64
import logging
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

from nunchi import audio, ollama_util
from nunchi.config import Settings
from nunchi.llm import ChatBackend, LLMError, OllamaChat, OpenAIChat
from nunchi.rag.chunker import chunk_text
from nunchi.rag.embedder import Embedder, EmbeddingError, OllamaEmbedder, OpenAIEmbedder
from nunchi.rag.loaders import Document, iter_files, load_path
from nunchi.rag.prompt import CAPTION_PROMPT, build_messages
from nunchi.rag.retriever import Retriever
from nunchi.rag.store import Store
from nunchi.stt import OpenAITranscriber, STTError, Transcriber, WhisperCppTranscriber
from nunchi.textutil import SentenceStreamer, to_speech_text
from nunchi.tts import Speaker, TTSError, build_backend

log = logging.getLogger("nunchi")

NOT_HEARD = "잘 못 들었어요. 다시 한 번 말씀해 주세요."
FOLLOW_UP_HINTS = ("그럼", "그러면", "그거", "그건", "그게", "거기", "그곳", "그때", "그리고", "또", "저거", "이거", "그 ", "왜", "언제", "몇")


@dataclass
class Event:
    type: str
    data: dict = field(default_factory=dict)


class SessionMemory:
    """짧은 대화 기억 (세션별 최근 N턴). '그럼 그건 몇 시까지야?' 같은 후속 질문을 위해."""

    def __init__(self, max_turns: int = 3, ttl_sec: float = 1800) -> None:
        self.max_turns = max_turns
        self.ttl = ttl_sec
        self._data: dict[str, tuple[float, deque]] = {}
        self._lock = threading.Lock()

    def get(self, sid: str | None) -> list[tuple[str, str]]:
        if not sid or self.max_turns <= 0:
            return []
        with self._lock:
            self._evict()
            item = self._data.get(sid)
            return list(item[1]) if item else []

    def add(self, sid: str | None, question: str, answer: str) -> None:
        if not sid or self.max_turns <= 0:
            return
        with self._lock:
            _, dq = self._data.get(sid, (0.0, deque(maxlen=self.max_turns)))
            dq.append((question, answer))
            self._data[sid] = (time.time(), dq)

    def clear(self, sid: str) -> None:
        with self._lock:
            self._data.pop(sid, None)

    def _evict(self) -> None:
        now = time.time()
        for k in [k for k, (t, _) in self._data.items() if now - t > self.ttl]:
            del self._data[k]


# --- 구성 요소 만들기 ---------------------------------------------------------------


def build_chat(s: Settings, model: str) -> ChatBackend:
    if s.llm_backend == "openai":
        return OpenAIChat(s.llm_base, model, s.llm_api_key, s.temperature, s.max_tokens)
    return OllamaChat(s.ollama_url, model, s.temperature, s.num_ctx, s.max_tokens, s.keep_alive, s.think)


def build_embedder(s: Settings, model: str) -> Embedder | None:
    if s.embed_backend == "none" or s.retrieval_mode == "bm25":
        return None
    if s.embed_backend == "openai":
        return OpenAIEmbedder(s.embed_base, model, s.llm_api_key)
    return OllamaEmbedder(s.ollama_url, model, s.keep_alive)


def build_stt(s: Settings, model: str) -> Transcriber | None:
    if s.stt_backend == "none":
        return None
    if s.stt_backend == "whispercpp":
        return WhisperCppTranscriber(s.stt_url or "http://127.0.0.1:8080", s.stt_language, s.stt_prompt)
    return OpenAITranscriber(s.stt_base, model, s.stt_language, s.stt_prompt)


class Hub:
    def __init__(
        self,
        settings: Settings,
        *,
        store: Store | None = None,
        chat: ChatBackend | None = None,
        vision_chat: ChatBackend | None = None,
        embedder: Embedder | None | str = "auto",
        stt: Transcriber | None | str = "auto",
        speaker: Speaker | None = None,
        probe_ollama: bool = True,
    ) -> None:
        self.settings = s = settings
        self.installed = ollama_util.list_models(s.ollama_url) if probe_ollama else None
        r = lambda name: ollama_util.resolve_model(name, self.installed)  # noqa: E731
        self.models = {
            "llm": r(s.llm_model),
            "vision": r(s.effective_vision_model),
            "embed": r(s.embed_model),
            "stt": r(s.effective_stt_model),
            "tts": r(s.tts_model),
        }
        self.store = store or Store(s.index_path)
        self.embedder = build_embedder(s, self.models["embed"]) if embedder == "auto" else embedder
        self.retriever = Retriever(
            self.store, self.embedder, s.retrieval_mode, s.embed_query_prefix, s.number_lookup, s.max_section_chars,
            s.lookup_exclusive,
        )
        self.chat = chat or build_chat(s, self.models["llm"])
        if vision_chat is not None:
            self.vision_chat = vision_chat
        elif chat is not None or self.models["vision"] == self.models["llm"]:
            self.vision_chat = self.chat
        else:
            self.vision_chat = build_chat(s, self.models["vision"])
        self.stt = build_stt(s, self.models["stt"]) if stt == "auto" else stt
        if speaker is None:
            has_speech = bool(self.installed) and "speech" in ollama_util.capabilities(s.ollama_url, self.models["tts"])
            try:
                backend = build_backend(
                    s.tts_backend,
                    tts_base=s.tts_base,
                    tts_model=self.models["tts"],
                    tts_voice=s.tts_voice,
                    say_voice=s.say_voice,
                    installed_models=self.installed,
                    model_has_speech=has_speech,
                )
            except TTSError as e:
                log.warning("TTS를 쓸 수 없어요: %s", e)
                backend = None
            speaker = Speaker(backend)
        self.speaker = speaker
        self.sessions = SessionMemory(s.history_turns)
        self._tts_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tts")

    # ------------------------------------------------------------------ 색인
    def ingest_paths(self, paths: list[str | Path], reembed: bool = False, prune: bool = False, log_fn=print) -> dict:
        files = iter_files(paths)
        stats = {"files": len(files), "added": 0, "updated": 0, "skipped": 0, "failed": 0, "chunks": 0, "removed": 0}
        if reembed:
            self.store.clear_embeddings()
        for f in files:
            source = str(f.resolve())
            try:
                doc = load_path(f)
                doc.source = source
            except Exception as e:  # noqa: BLE001 - 한 파일 실패가 전체를 멈추지 않게
                stats["failed"] += 1
                log_fn(f"  ✗ {f.name}: {e}")
                continue
            prev = self.store.get_document(source)
            if prev is not None and prev["sha256"] == doc.sha256 and not reembed:
                stats["skipped"] += 1
                continue
            n = self.ingest_document(doc, log_fn=log_fn)
            stats["chunks"] += n
            stats["updated" if prev is not None else "added"] += 1
            log_fn(f"  ✓ {f.name} → 조각 {n}개")
        if reembed or self.embedder is not None:
            stats["embedded_later"] = self.embed_missing(log_fn=log_fn)
        if prune:
            keep = {str(f.resolve()) for f in files}
            roots = [str(Path(p).expanduser().resolve()) for p in paths]
            for d in self.store.list_documents():
                if d["source"] not in keep and any(d["source"].startswith(r) for r in roots):
                    self.store.delete_document(d["source"])
                    stats["removed"] += 1
        self.retriever.refresh(force=True)
        return stats

    def ingest_document(self, doc: Document, log_fn=print) -> int:
        chunks = chunk_text(doc.text, self.settings.chunk_chars, self.settings.chunk_overlap, skip_title=doc.title)
        embeddings = None
        if self.embedder is not None and chunks:
            try:
                embeddings = self.embedder.embed([c.embed_text(doc.title) for c in chunks])
                self.store.set_meta("embed_model", self.embedder.model)
            except EmbeddingError as e:
                log_fn(f"  ! 임베딩 생략(키워드 검색은 가능): {e}")
        self.store.replace_document(doc, chunks, embeddings)
        return len(chunks)

    def embed_missing(self, log_fn=print, batch: int = 64) -> int:
        if self.embedder is None:
            return 0
        todo = self.store.chunks_without_embeddings()
        done = 0
        for i in range(0, len(todo), batch):
            part = todo[i : i + batch]
            try:
                emb = self.embedder.embed([c.search_text() for c in part])
            except EmbeddingError as e:
                log_fn(f"  ! 임베딩 실패: {e}")
                break
            self.store.set_embeddings([c.id for c in part], emb)
            self.store.set_meta("embed_model", self.embedder.model)
            done += len(part)
        return done

    # ------------------------------------------------------------------ 질문
    def retrieval_query(self, question: str, history: list[tuple[str, str]]) -> str:
        """후속 질문이면 직전 질문을 붙여 검색합니다. ('그럼 주말엔?' → '체크인 몇 시야? 그럼 주말엔?')"""
        q = question.strip()
        if history and (len(q) <= 12 or q.startswith(FOLLOW_UP_HINTS)):
            return f"{history[-1][0]} {q}"
        return q

    def transcribe(self, data: bytes) -> tuple[str, dict]:
        if self.stt is None:
            raise STTError("허브의 음성 인식이 꺼져 있어요 (NUNCHI_STT_BACKEND=none). 폰에서 글자로 보내 주세요.")
        wav = audio.to_wav(data)
        info: dict = {}
        if self.settings.audio_preprocess:
            wav, info = audio.preprocess(wav)
            if info.get("output_sec", 1) < 0.25:
                return "", info
        return self.stt.transcribe(wav), info

    def voice_events(self, data: bytes, **kw) -> Iterator[Event]:
        t0 = time.perf_counter()
        try:
            text, info = self.transcribe(data)
        except (STTError, audio.AudioError) as e:
            yield Event("error", {"stage": "stt", "message": str(e)})
            yield Event("done", {"ok": False, "answer": NOT_HEARD, "speech": NOT_HEARD, "sources": [], "timings": {}})
            return
        stt_ms = (time.perf_counter() - t0) * 1000
        yield Event("transcript", {"text": text, "ms": round(stt_ms), "audio": info})
        if len(text.strip()) < 2:
            yield Event("done", {"ok": False, "answer": NOT_HEARD, "speech": NOT_HEARD, "sources": [], "timings": {"stt_ms": round(stt_ms)}})
            return
        yield from self.ask_events(text, _t0=t0, _extra_timings={"stt_ms": round(stt_ms)}, _from_voice=True, **kw)

    def ask_events(
        self,
        question: str,
        *,
        session_id: str | None = None,
        images: list[bytes] | None = None,
        use_rag: bool = True,
        style: str | None = None,
        top_k: int | None = None,
        speak: bool = False,
        _t0: float | None = None,
        _extra_timings: dict | None = None,
        _from_voice: bool = False,
    ) -> Iterator[Event]:
        s = self.settings
        t0 = _t0 or time.perf_counter()
        timings = dict(_extra_timings or {})
        history = self.sessions.get(session_id)
        style = style or s.answer_style

        # 0) 사진 질문이면 먼저 사진을 한 줄로 묘사(캡션)해서 검색어에 보탭니다.
        #    "이게 뭐야?"처럼 질문에 검색어가 없어도 사진 속 글자·코드('E2')로 문서를 찾을 수 있어요.
        caption = ""
        if images and use_rag and s.vision_caption:
            t = time.perf_counter()
            try:
                raw = "".join(self.vision_chat.stream([{"role": "user", "content": CAPTION_PROMPT}], images=images, max_tokens=60))
                caption = " ".join(raw.split())[:160]
            except LLMError as e:
                yield Event("error", {"stage": "caption", "message": str(e)})
            timings["caption_ms"] = round((time.perf_counter() - t) * 1000)
            if caption:
                yield Event("caption", {"text": caption, "ms": timings["caption_ms"]})

        # 1) 검색
        hits, sources = [], []
        if use_rag:
            t = time.perf_counter()
            query = self.retrieval_query(question, history)
            if caption:
                query = f"{query} {caption}"
            hits, info = self.retriever.search(query, k=top_k or s.top_k)
            sources = [h.to_dict(i) for i, h in enumerate(hits, start=1)]
            timings["retrieval_ms"] = round((time.perf_counter() - t) * 1000, 1)
            yield Event(
                "retrieval",
                {
                    "query": query,
                    "sources": sources,
                    "ms": timings["retrieval_ms"],
                    "mode": info.mode,
                    "warnings": info.warnings,
                    "lookup": info.lookup,
                },
            )

        # 2) 생성 (+ 문장 단위 TTS를 별도 스레드에서 병렬로)
        messages = build_messages(
            question, hits, history, style=style, strict=s.strict_rag, vision=bool(images), from_voice=_from_voice
        )
        backend = self.vision_chat if images else self.chat
        streamer = SentenceStreamer()
        pending: deque[tuple[int, Future]] = deque()
        answer_parts: list[str] = []
        n_sent = 0
        t_llm = time.perf_counter()

        def sentence_events(sentences: list[str]) -> Iterator[Event]:
            nonlocal n_sent
            for sent in sentences:
                speech = to_speech_text(sent)
                if not speech:
                    continue
                yield Event("sentence", {"index": n_sent, "text": speech})
                if speak and self.speaker.available:
                    pending.append((n_sent, self._tts_pool.submit(self.speaker.synthesize, speech)))
                n_sent += 1

        def drain(block: bool) -> Iterator[Event]:
            while pending and (block or pending[0][1].done()):
                idx, fut = pending.popleft()
                try:
                    wav = fut.result()
                except Exception as e:  # noqa: BLE001
                    yield Event("error", {"stage": "tts", "message": str(e)})
                    continue
                if wav:
                    yield Event("audio", {"index": idx, "wav_b64": base64.b64encode(wav).decode("ascii")})

        try:
            for delta in backend.stream(messages, images=images):
                if "first_token_ms" not in timings:
                    timings["first_token_ms"] = round((time.perf_counter() - t0) * 1000)
                answer_parts.append(delta)
                yield Event("delta", {"text": delta})
                yield from sentence_events(streamer.feed(delta))
                yield from drain(block=False)
        except LLMError as e:
            yield Event("error", {"stage": "llm", "message": str(e)})
            yield Event("done", {"ok": False, "answer": f"답을 만들지 못했어요. {e}", "speech": "답을 만들지 못했어요.", "sources": sources, "timings": timings})
            return
        yield from sentence_events(streamer.flush())
        timings["llm_ms"] = round((time.perf_counter() - t_llm) * 1000)
        yield from drain(block=True)

        answer = "".join(answer_parts).strip()
        speech = to_speech_text(answer)
        timings["total_ms"] = round((time.perf_counter() - t0) * 1000)
        self.sessions.add(session_id, question, answer)
        done = {
            "ok": True,
            "question": question,
            "answer": answer,
            "speech": speech,
            "sources": sources,
            "timings": timings,
            "model": backend.model,
            "session_id": session_id,
        }
        # 전체 음성은 SSE에서는 보내지 않습니다(문장별 audio 이벤트로 이미 전달). JSON 응답은 collect()가 합칩니다.
        yield Event("done", done)

    def ask(self, question: str, **kw) -> dict:
        """스트리밍 없이 결과만 받기 (CLI, 테스트, 단순한 클라이언트용)."""
        return collect(self.ask_events(question, **kw))

    def voice(self, data: bytes, **kw) -> dict:
        return collect(self.voice_events(data, **kw))

    # ------------------------------------------------------------------ 상태
    def health(self) -> dict:
        s = self.settings
        installed = ollama_util.list_models(s.ollama_url)
        missing = []
        if installed is not None:
            needed = {self.models["llm"]}
            if self.embedder is not None:
                needed.add(self.models["embed"])
            if isinstance(self.stt, OpenAITranscriber) and s.stt_url == "":
                needed.add(self.models["stt"])
            missing = sorted(m for m in needed if m not in installed)
        return {
            "ok": installed is not None and not missing,
            "ollama": {"url": s.ollama_url, "reachable": installed is not None, "version": ollama_util.version(s.ollama_url)},
            "models": self.models,
            "missing_models": missing,
            "stt": getattr(self.stt, "name", "none") if self.stt else "none",
            "tts": self.speaker.name,
            "retrieval_mode": s.retrieval_mode,
            "index": {k: v for k, v in self.store.stats().items() if k != "path"},  # 허브 컴퓨터 경로는 숨김
            "answer_style": s.answer_style,
            "auth": bool(s.token),
        }


def collect(events: Iterator[Event]) -> dict:
    """이벤트 스트림을 JSON 하나로 모읍니다. 문장별 음성은 하나의 WAV(audio_wav_b64)로 합칩니다."""
    result: dict = {"transcript": None, "errors": []}
    wavs: list[bytes] = []
    for ev in events:
        if ev.type == "transcript":
            result["transcript"] = ev.data["text"]
        elif ev.type == "error":
            result["errors"].append(ev.data)
        elif ev.type == "audio":
            wavs.append(base64.b64decode(ev.data["wav_b64"]))
        elif ev.type == "done":
            result.update(ev.data)
    if wavs:
        result["audio_wav_b64"] = base64.b64encode(audio.concat_wavs(wavs)).decode("ascii")
    return result


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]
