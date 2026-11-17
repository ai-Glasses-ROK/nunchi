"""HTTP API 서버 (FastAPI).

폰 앱·웹 클라이언트가 같은 와이파이(인터넷 연결 없어도 됨) 안에서 이 서버에 질문을 보냅니다.
- 응답 방식 두 가지: JSON 한 번에 받기 / SSE(text/event-stream)로 조금씩 받기(stream=true)
- 인증: NUNCHI_TOKEN을 정하면 모든 /v1 요청에 `Authorization: Bearer <토큰>` 헤더가 필요합니다.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nunchi import __version__
from nunchi.config import Settings
from nunchi.pipeline import Event, Hub, collect
from nunchi.rag.loaders import SUPPORTED_EXTS, load_bytes
from nunchi.tts import TTSError

log = logging.getLogger("nunchi.server")
WEB_DIR = Path(__file__).parent / "web"
MAX_AUDIO = 25 * 1024 * 1024
MAX_IMAGE = 15 * 1024 * 1024
MAX_DOC = 30 * 1024 * 1024


class AskBody(BaseModel):
    question: str = Field(..., min_length=1, max_length=4000)
    session_id: str | None = None
    stream: bool = False
    speak: bool = False
    use_rag: bool = True
    style: str | None = None  # voice | text
    top_k: int | None = Field(default=None, ge=1, le=12)


class TTSBody(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000)


class SessionBody(BaseModel):
    session_id: str


def _sse(events: Iterator[Event]) -> Iterator[bytes]:
    try:
        for ev in events:
            yield f"event: {ev.type}\ndata: {json.dumps(ev.data, ensure_ascii=False)}\n\n".encode("utf-8")
    except Exception as e:  # noqa: BLE001 - 스트림 도중 예외도 클라이언트에 알림
        log.exception("stream failed")
        payload = json.dumps({"stage": "server", "message": str(e)}, ensure_ascii=False)
        yield f"event: error\ndata: {payload}\n\n".encode("utf-8")


def _respond(events: Iterator[Event], stream: bool):
    if stream:
        return StreamingResponse(
            _sse(events),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )
    return JSONResponse(collect(events))


def _bool(v: str | bool | None, default: bool = False) -> bool:
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return v.strip().lower() in {"1", "true", "yes", "on"}


def create_app(hub: Hub | None = None, settings: Settings | None = None) -> FastAPI:
    settings = settings or (hub.settings if hub else Settings.from_env())
    hub = hub or Hub(settings)
    app = FastAPI(title="nunchi", version=__version__, description="오프라인 RAG + 로컬 LLM 허브")
    app.state.hub = hub

    def auth(
        authorization: str | None = Header(default=None),
        x_hub_token: str | None = Header(default=None),
    ) -> None:
        token = settings.token
        if not token:
            return
        given = x_hub_token or ""
        if authorization and authorization.lower().startswith("bearer "):
            given = authorization[7:].strip()
        if given != token:
            raise HTTPException(status_code=401, detail="토큰이 맞지 않아요 (Authorization: Bearer <NUNCHI_TOKEN>)")

    deps = [Depends(auth)]

    # ---------------------------------------------------------------- 웹
    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    @app.middleware("http")
    async def timing(request: Request, call_next):  # noqa: ANN001
        t = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Elapsed-Ms"] = f"{(time.perf_counter() - t) * 1000:.0f}"
        return response

    # ---------------------------------------------------------------- 상태
    @app.get("/v1/health", dependencies=deps)
    def health() -> dict:
        return {"version": __version__, **hub.health()}

    # ---------------------------------------------------------------- 질문
    @app.post("/v1/ask", dependencies=deps)
    def ask(body: AskBody):
        events = hub.ask_events(
            body.question,
            session_id=body.session_id,
            use_rag=body.use_rag,
            style=body.style,
            top_k=body.top_k,
            speak=body.speak,
        )
        return _respond(events, body.stream)

    @app.post("/v1/voice", dependencies=deps)
    def voice(
        file: UploadFile = File(..., description="WAV 권장 (8k/16k 모노 16bit). 그 외 형식은 ffmpeg 필요"),
        session_id: str | None = Form(default=None),
        stream: str | None = Form(default=None),
        speak: str | None = Form(default=None),
        style: str | None = Form(default=None),
        top_k: int | None = Form(default=None),
    ):
        data = file.file.read(MAX_AUDIO + 1)
        if len(data) > MAX_AUDIO:
            raise HTTPException(413, "오디오가 너무 커요 (최대 25MB)")
        if not data:
            raise HTTPException(400, "오디오가 비어 있어요")
        events = hub.voice_events(data, session_id=session_id, style=style, top_k=top_k, speak=_bool(speak))
        return _respond(events, _bool(stream))

    @app.post("/v1/vision", dependencies=deps)
    def vision(
        image: UploadFile = File(..., description="JPEG/PNG (macOS 허브는 HEIC도 자동 변환)"),
        question: str = Form(default="지금 보이는 것을 짧게 설명해 줘."),
        session_id: str | None = Form(default=None),
        stream: str | None = Form(default=None),
        speak: str | None = Form(default=None),
        use_rag: str | None = Form(default=None),
        style: str | None = Form(default=None),
    ):
        data = image.file.read(MAX_IMAGE + 1)
        if len(data) > MAX_IMAGE:
            raise HTTPException(413, "사진이 너무 커요 (최대 15MB)")
        if not data:
            raise HTTPException(400, "사진이 비어 있어요")
        events = hub.ask_events(
            question,
            session_id=session_id,
            images=[data],
            use_rag=_bool(use_rag, default=True),
            style=style,
            speak=_bool(speak),
        )
        return _respond(events, _bool(stream))

    @app.post("/v1/stt", dependencies=deps)
    def stt(file: UploadFile = File(...)) -> dict:
        data = file.file.read(MAX_AUDIO + 1)
        if len(data) > MAX_AUDIO:
            raise HTTPException(413, "오디오가 너무 커요 (최대 25MB)")
        t = time.perf_counter()
        try:
            text, info = hub.transcribe(data)
        except Exception as e:  # noqa: BLE001
            raise HTTPException(400, str(e)) from e
        return {"text": text, "audio": info, "ms": round((time.perf_counter() - t) * 1000)}

    @app.post("/v1/tts", dependencies=deps)
    def tts(body: TTSBody) -> Response:
        if not hub.speaker.available:
            raise HTTPException(503, "허브 TTS가 꺼져 있어요. 폰/브라우저의 음성 합성을 쓰세요.")
        try:
            wav = hub.speaker.synthesize(body.text)
        except TTSError as e:
            raise HTTPException(502, str(e)) from e
        if not wav:
            raise HTTPException(400, "읽을 내용이 없어요")
        return Response(content=wav, media_type="audio/wav")

    @app.post("/v1/session/reset", dependencies=deps)
    def reset(body: SessionBody) -> dict:
        hub.sessions.clear(body.session_id)
        return {"ok": True}

    # ---------------------------------------------------------------- 지식베이스
    @app.get("/v1/kb", dependencies=deps)
    def kb() -> dict:
        docs = hub.store.list_documents()
        for d in docs:
            d["name"] = Path(d["source"]).name
            d.pop("source", None)  # 허브 컴퓨터의 전체 경로는 노출하지 않음
        return {"stats": {k: v for k, v in hub.store.stats().items() if k != "path"}, "documents": docs}

    @app.post("/v1/kb/upload", dependencies=deps)
    def kb_upload(files: list[UploadFile] = File(...)) -> dict:
        if not settings.allow_upload:
            raise HTTPException(403, "문서 업로드가 꺼져 있어요 (NUNCHI_ALLOW_UPLOAD=false)")
        results = []
        for f in files:
            name = Path(f.filename or "upload.txt").name
            if Path(name).suffix.lower() not in SUPPORTED_EXTS:
                results.append({"name": name, "ok": False, "error": "지원하지 않는 형식"})
                continue
            data = f.file.read(MAX_DOC + 1)
            if len(data) > MAX_DOC:
                results.append({"name": name, "ok": False, "error": "파일이 너무 커요 (최대 30MB)"})
                continue
            try:
                doc = load_bytes(data, name=f"upload://{name}")
                n = hub.ingest_document(doc, log_fn=log.info)
                results.append({"name": name, "ok": True, "chunks": n})
            except Exception as e:  # noqa: BLE001
                results.append({"name": name, "ok": False, "error": str(e)})
        hub.retriever.refresh(force=True)
        return {"results": results, "stats": {k: v for k, v in hub.store.stats().items() if k != "path"}}

    @app.get("/v1/kb/export", dependencies=deps)
    def kb_export() -> dict:
        """폰 단독 모드용 지식베이스 내보내기 (조각 텍스트만; 폰에서 같은 2-gram BM25로 검색)."""
        return export_mobile(hub)

    return app


def export_mobile(hub: Hub) -> dict:
    chunks = hub.store.load_chunks()
    return {
        "format": "nunchi-kb",
        "version": 1,
        "tokenizer": "char-bigram-v1",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "chunks": [
            {
                "id": c.id,
                "title": c.title,
                "heading": c.heading,
                "text": c.text,
                "source": Path(c.source.replace("upload://", "")).name,
            }
            for c in chunks
        ],
    }
