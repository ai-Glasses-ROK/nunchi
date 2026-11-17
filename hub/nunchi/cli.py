"""명령줄 도구: nunchi <명령>

  doctor          설치·모델·네트워크 상태 점검 (가장 먼저 실행!)
  ingest 경로...    문서를 지식베이스에 넣기 (.md .txt .pdf .docx .html .csv)
  kb              들어 있는 문서 목록
  ask "질문"       한 번 묻기            chat   대화형으로 묻기
  transcribe 파일  음성 인식만 테스트    say "문장"  음성 합성만 테스트
  voice 파일.wav   음성 질문 → 음성 답   listen  맥 마이크로 바로 말해 보기
  serve           폰/웹이 접속할 서버 실행
  bench           지연시간·정확도 측정 (연구용)
  export-mobile   폰 단독 모드용 지식베이스(JSON) 내보내기
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from nunchi import __version__, audio, ollama_util
from nunchi.config import Settings

OK, WARN, FAIL = "✅", "⚠️ ", "❌"


def _hub(settings: Settings, **kw):
    from nunchi.pipeline import Hub

    return Hub(settings, **kw)


# ---------------------------------------------------------------------------- doctor
def cmd_doctor(s: Settings, args) -> int:
    print(f"nunchi {__version__} · Python {platform.python_version()} · {platform.system()} {platform.machine()}")
    problems = 0
    ver = ollama_util.version(s.ollama_url)
    installed = ollama_util.list_models(s.ollama_url)
    if installed is None:
        print(f"{FAIL} Ollama에 연결할 수 없어요: {s.ollama_url}  →  Ollama 앱을 켜거나 `ollama serve` 실행")
        problems += 1
    else:
        print(f"{OK} Ollama {ver} 연결됨 ({s.ollama_url}) · 설치된 모델 {len(installed)}개")

    def check_model(role: str, name: str, need_cap: str | None, required: bool = True) -> None:
        nonlocal problems
        resolved = ollama_util.resolve_model(name, installed)
        if installed is None:
            return
        if resolved not in installed:
            mark = FAIL if required else WARN
            print(f"{mark} {role}: '{name}' 없음  →  ollama pull {name}")
            problems += int(required)
            return
        caps = ollama_util.capabilities(s.ollama_url, resolved)
        alias = f" (→ {resolved})" if resolved != name else ""
        if need_cap and need_cap not in caps:
            print(f"{WARN} {role}: {name}{alias} 에 '{need_cap}' 기능이 없어요 (기능: {', '.join(caps)})")
        else:
            print(f"{OK} {role}: {name}{alias}  [{', '.join(caps)}]")

    check_model("LLM", s.llm_model, "completion")
    if s.effective_vision_model != s.llm_model:
        check_model("비전", s.effective_vision_model, "vision", required=False)
    elif installed is not None:
        caps = ollama_util.capabilities(s.ollama_url, ollama_util.resolve_model(s.llm_model, installed))
        if "vision" not in caps:
            print(f"{WARN} 비전: LLM에 vision 기능이 없어 사진 질문은 실패해요 (NUNCHI_VISION_MODEL 설정)")
    if s.embed_backend == "ollama" and s.retrieval_mode != "bm25":
        check_model("임베딩", s.embed_model, "embedding", required=False)
    if s.stt_backend == "openai" and not s.stt_url:
        check_model("음성인식", s.effective_stt_model, "audio", required=False)
    elif s.stt_backend == "whispercpp":
        print(f"ℹ️  음성인식: whisper.cpp 서버 사용 ({s.stt_url or 'http://127.0.0.1:8080'})")

    # TTS
    tts_line = None
    if s.tts_backend in ("auto", "openai") and installed is not None:
        resolved = ollama_util.resolve_model(s.tts_model, installed)
        if resolved in installed and "speech" in ollama_util.capabilities(s.ollama_url, resolved):
            tts_line = f"{OK} 음성합성: Ollama {resolved} (voice={s.tts_voice})"
    if tts_line is None and s.tts_backend in ("auto", "say") and shutil.which("say"):
        voices = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
        has = any(line.startswith(s.say_voice) for line in voices.splitlines())
        tts_line = f"{OK if has else WARN} 음성합성: macOS say ({s.say_voice}{'' if has else ' 목소리 없음 → 설정 > 손쉬운 사용 > 음성 콘텐츠에서 추가'})"
    print(tts_line or "ℹ️  음성합성: 허브 TTS 없음 → 폰이 직접 읽습니다 (권장 구성이기도 해요)")

    # 기타 도구
    print(f"{OK if shutil.which('ffmpeg') else WARN} ffmpeg: {'있음' if shutil.which('ffmpeg') else '없음 (WAV 이외 형식 변환·listen 명령에 필요: brew install ffmpeg)'}")

    # 색인
    from nunchi.rag.store import Store

    st = Store(s.index_path).stats()
    mark = OK if st["chunks"] else WARN
    print(f"{mark} 지식베이스: 문서 {st['documents']}개 · 조각 {st['chunks']}개 · 임베딩 {st['embedded_chunks']}개 ({st['path']})")
    if st["embed_model"] and st["embed_model"] != ollama_util.resolve_model(s.embed_model, installed):
        print(f"{WARN} 색인 임베딩 모델({st['embed_model']})과 설정({s.embed_model})이 달라요 → nunchi ingest <폴더> --reembed")

    # 네트워크
    ips = local_ips()
    print(f"ℹ️  이 컴퓨터 주소: {', '.join(ips) or '네트워크 없음'} · Bonjour 이름: {bonjour_name() or '-'}")
    if s.host in ("127.0.0.1", "localhost"):
        print("ℹ️  현재 서버는 이 컴퓨터에서만 접속 가능해요. 폰에서 접속하려면: nunchi serve --host 0.0.0.0")
    if s.host == "0.0.0.0" and not s.token:
        print(f"{WARN} 같은 네트워크의 누구나 접속할 수 있어요. NUNCHI_TOKEN 설정을 권장해요.")
    offline = not internet_reachable()
    print(f"ℹ️  인터넷: {'연결 안 됨 (오프라인 모드 OK)' if offline else '연결됨 (오프라인 준비가 끝났다면 끊고 다시 doctor로 확인해 보세요)'}")
    print()
    print("결과:", "모두 정상이에요 🎉" if problems == 0 else f"고칠 것 {problems}개")
    return 0 if problems == 0 else 1


def local_ips() -> list[str]:
    ips: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))  # 실제 패킷은 나가지 않음 (경로만 계산)
            ips.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return [ip for ip in ips if not ip.startswith("127.")]


def bonjour_name() -> str | None:
    if sys.platform == "darwin" and shutil.which("scutil"):
        out = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True).stdout.strip()
        return f"{out}.local" if out else None
    name = socket.gethostname()
    return name if name.endswith(".local") else None


def internet_reachable(timeout: float = 1.5) -> bool:
    for host in (("1.1.1.1", 53), ("8.8.8.8", 53)):
        try:
            with socket.create_connection(host, timeout=timeout):
                return True
        except OSError:
            continue
    return False


# ---------------------------------------------------------------------------- 색인
def cmd_ingest(s: Settings, args) -> int:
    hub = _hub(s)
    print(f"지식베이스: {s.index_path}")
    print(f"임베딩: {hub.models['embed'] if hub.embedder else '사용 안 함 (BM25만)'}")
    t = time.perf_counter()
    stats = hub.ingest_paths(args.paths, reembed=args.reembed, prune=args.prune)
    print(
        f"\n완료 ({time.perf_counter() - t:.1f}초): 파일 {stats['files']}개 중 추가 {stats['added']} · 갱신 {stats['updated']} · "
        f"변경없음 {stats['skipped']} · 실패 {stats['failed']} · 새 조각 {stats['chunks']}개"
        + (f" · 삭제 {stats['removed']}" if stats.get("removed") else "")
    )
    st = hub.store.stats()
    print(f"현재: 문서 {st['documents']}개 · 조각 {st['chunks']}개 · 임베딩 {st['embedded_chunks']}개")
    return 0 if stats["failed"] == 0 else 1


def cmd_kb(s: Settings, args) -> int:
    from nunchi.rag.store import Store

    store = Store(s.index_path)
    docs = store.list_documents()
    if not docs:
        print("비어 있어요. 먼저: nunchi ingest ../examples/knowledge")
        return 0
    for d in docs:
        print(f"- {d['title']}  ({Path(d['source']).name}, 조각 {d['n_chunks']}개, {d['chars']}자)")
    if args.remove:
        target = [d for d in docs if Path(d["source"]).name == args.remove or d["source"] == args.remove]
        for d in target:
            store.delete_document(d["source"])
            print(f"삭제: {d['source']}")
    return 0


# ---------------------------------------------------------------------------- 질문
def _print_sources(sources: list[dict]) -> None:
    if not sources:
        print("  (참고 문서 없음)")
    for src in sources:
        dense = f" 의미 {src['dense']:.2f}" if src.get("dense") is not None else ""
        kw = f" 키워드 {src['bm25']:.1f}" if src.get("bm25") is not None else ""
        print(f"  [{src['n']}] {src['label']}{dense}{kw}")


def _play(wav: bytes) -> None:
    player = shutil.which("afplay") or shutil.which("aplay") or shutil.which("paplay")
    if not player:
        print("(재생기를 찾지 못했어요. -o 로 저장해서 들어 보세요)")
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        f.write(wav)
        path = f.name
    try:
        subprocess.run([player, path], check=False)
    finally:
        os.unlink(path)


def _run_stream(events, show_sources: bool = True) -> dict:
    from nunchi.pipeline import collect

    result: dict = {}
    printed_header = False

    def gen():
        nonlocal printed_header
        for ev in events:
            if ev.type == "transcript":
                print(f"🎙️  인식: {ev.data['text']}  ({ev.data['ms']}ms)")
            elif ev.type == "retrieval" and show_sources:
                print(f"🔎 검색 ({ev.data['mode']}, {ev.data['ms']}ms)")
                _print_sources(ev.data["sources"])
                for w in ev.data.get("warnings", []):
                    print(f"  {WARN}{w}")
            elif ev.type == "delta":
                if not printed_header:
                    print("💬 ", end="", flush=True)
                    printed_header = True
                print(ev.data["text"], end="", flush=True)
            elif ev.type == "error":
                print(f"\n{FAIL} [{ev.data['stage']}] {ev.data['message']}")
            yield ev

    result = collect(gen())
    if printed_header:
        print()
    t = result.get("timings") or {}
    if t:
        print("⏱️  " + " · ".join(f"{k.replace('_ms', '')} {v}ms" for k, v in t.items()))
    return result


def cmd_ask(s: Settings, args) -> int:
    hub = _hub(s)
    images = [Path(args.image).read_bytes()] if args.image else None
    events = hub.ask_events(args.question, images=images, use_rag=not args.no_rag, style=args.style, speak=args.speak)
    if args.json:
        from nunchi.pipeline import collect

        r = collect(events)
        r.pop("audio_wav_b64", None)
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0 if r.get("ok") else 1
    r = _run_stream(events)
    if args.speak and r.get("audio_wav_b64"):
        _play(base64.b64decode(r["audio_wav_b64"]))
    return 0 if r.get("ok") else 1


def cmd_chat(s: Settings, args) -> int:
    from nunchi.pipeline import new_session_id

    hub = _hub(s)
    sid = new_session_id()
    print("대화를 시작해요. /reset 대화 초기화 · /quit 종료")
    while True:
        try:
            q = input("\n🙂 ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not q:
            continue
        if q in ("/quit", "/exit", "/q"):
            return 0
        if q == "/reset":
            hub.sessions.clear(sid)
            print("초기화했어요.")
            continue
        r = _run_stream(hub.ask_events(q, session_id=sid, speak=args.speak), show_sources=args.sources)
        if args.speak and r.get("audio_wav_b64"):
            _play(base64.b64decode(r["audio_wav_b64"]))


# ---------------------------------------------------------------------------- 음성
def cmd_transcribe(s: Settings, args) -> int:
    hub = _hub(s)
    data = Path(args.file).read_bytes()
    t = time.perf_counter()
    text, info = hub.transcribe(data)
    print(f"🎙️  {text}")
    print(f"⏱️  {(time.perf_counter() - t) * 1000:.0f}ms · {info}")
    return 0


def cmd_say(s: Settings, args) -> int:
    hub = _hub(s)
    if not hub.speaker.available:
        print(f"{FAIL} 허브 TTS가 없어요 (doctor로 확인)")
        return 1
    t = time.perf_counter()
    wav = hub.speaker.synthesize(args.text)
    print(f"🔊 {hub.speaker.name} · {(time.perf_counter() - t) * 1000:.0f}ms")
    if args.output:
        Path(args.output).write_bytes(wav)
        print(f"저장: {args.output}")
    else:
        _play(wav)
    return 0


def cmd_voice(s: Settings, args) -> int:
    hub = _hub(s)
    data = Path(args.file).read_bytes()
    r = _run_stream(hub.voice_events(data, speak=args.speak or bool(args.output)))
    if r.get("audio_wav_b64"):
        wav = base64.b64decode(r["audio_wav_b64"])
        if args.output:
            Path(args.output).write_bytes(wav)
            print(f"답변 음성 저장: {args.output}")
        elif args.speak:
            _play(wav)
    return 0 if r.get("ok") else 1


def record_mic(device: str, seconds: float | None, sr: int = 16000) -> bytes:
    """ffmpeg로 맥 마이크 녹음. seconds가 없으면 Enter를 누를 때까지."""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg가 필요해요: brew install ffmpeg")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "mic.wav"
        cmd = [ffmpeg, "-hide_banner", "-loglevel", "error", "-f", "avfoundation", "-i", device, "-ac", "1", "-ar", str(sr)]
        if seconds:
            cmd += ["-t", str(seconds)]
        cmd += [str(out)]
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
        if seconds:
            proc.wait()
        else:
            try:
                input("🎙️  말하세요… (끝나면 Enter) ")
            finally:
                if proc.stdin:
                    proc.stdin.write(b"q")
                    proc.stdin.flush()
                proc.wait(timeout=5)
        if not out.exists():
            raise SystemExit("녹음 실패: 시스템 설정 > 개인정보 보호 > 마이크에서 터미널 권한을 확인하세요")
        return out.read_bytes()


def cmd_listen(s: Settings, args) -> int:
    from nunchi.pipeline import new_session_id

    hub = _hub(s)
    sid = new_session_id()
    print("맥(또는 맥에 연결한 블루투스 헤드셋) 마이크로 질문해 보세요. Ctrl+C로 종료.")
    while True:
        try:
            wav = record_mic(args.device, args.seconds)
        except KeyboardInterrupt:
            print()
            return 0
        r = _run_stream(hub.voice_events(wav, session_id=sid, speak=hub.speaker.available))
        if r.get("audio_wav_b64"):
            _play(base64.b64decode(r["audio_wav_b64"]))
        if args.once:
            return 0


# ---------------------------------------------------------------------------- 서버
def cmd_serve(s: Settings, args) -> int:
    import uvicorn

    from nunchi.server import create_app

    host = args.host or s.host
    port = args.port or s.port
    s.host, s.port = host, port
    if args.token:
        s.token = args.token
    hub = _hub(s)
    if not args.no_warmup:
        print("모델 예열 중… (첫 질문을 빠르게)")
        hub.chat.warmup()
    app = create_app(hub)
    ssl_kw = {}
    scheme = "http"
    if args.https:
        cert, key = ensure_self_signed_cert(s.data_path / "tls")
        ssl_kw = {"ssl_certfile": str(cert), "ssl_keyfile": str(key)}
        scheme = "https"
    print("\n=== nunchi 서버 ===")
    print(f"LLM {hub.models['llm']} · STT {getattr(hub.stt, 'name', 'none')} · TTS {hub.speaker.name}")
    st = hub.store.stats()
    print(f"지식베이스: 문서 {st['documents']}개 · 조각 {st['chunks']}개")
    if host in ("0.0.0.0", "::"):
        for ip in local_ips():
            print(f"폰에서 접속: {scheme}://{ip}:{port}")
        if bonjour_name():
            print(f"또는:       {scheme}://{bonjour_name()}:{port}")
        if not s.token:
            print(f"{WARN}토큰 없이 네트워크에 열려 있어요. --token 또는 NUNCHI_TOKEN 권장")
    else:
        print(f"이 컴퓨터에서 접속: {scheme}://127.0.0.1:{port}  (폰 접속은 --host 0.0.0.0)")
    uvicorn.run(app, host=host, port=port, log_level="warning", **ssl_kw)
    return 0


def ensure_self_signed_cert(folder: Path) -> tuple[Path, Path]:
    """브라우저 마이크(getUserMedia)는 HTTPS에서만 동작해서, 폰 웹 테스트용 자체 서명 인증서를 만듭니다."""
    folder.mkdir(parents=True, exist_ok=True)
    cert, key = folder / "cert.pem", folder / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    if not shutil.which("openssl"):
        raise SystemExit("openssl이 필요해요")
    san = ",".join(["DNS:localhost", "IP:127.0.0.1"] + [f"IP:{ip}" for ip in local_ips()] + ([f"DNS:{bonjour_name()}"] if bonjour_name() else []))
    subprocess.run(
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "825", "-subj", "/CN=nunchi",
         "-addext", f"subjectAltName={san}", "-keyout", str(key), "-out", str(cert)],
        check=True, capture_output=True,
    )
    print(f"자체 서명 인증서를 만들었어요: {cert}")
    return cert, key


# ---------------------------------------------------------------------------- 연구용
def cer(ref: str, hyp: str) -> float:
    """문자 오류율(CER). 공백·문장부호는 빼고 비교합니다. 한국어 STT 평가의 표준 지표."""
    norm = lambda t: re.sub(r"[\s.,!?~·'\"“”‘’()\[\]-]", "", t)  # noqa: E731
    r, h = norm(ref), norm(hyp)
    if not r:
        return 0.0 if not h else 1.0
    prev = list(range(len(h) + 1))
    for i, rc in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hc in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rc != hc))
        prev = cur
    return prev[-1] / len(r)


def cmd_bench(s: Settings, args) -> int:
    hub = _hub(s)
    path = Path(args.file)
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.limit:
        items = items[: args.limit]
    rows = []
    for i, it in enumerate(items, 1):
        q = it["q"]
        rec = {"q": q}
        if args.voice:
            wav = synth_question(q, args.sr, s.say_voice)
            r = hub.voice(wav)
            rec["heard"] = r.get("transcript") or ""
            rec["cer"] = round(cer(q, rec["heard"]), 3)
        else:
            r = hub.ask(q)
        t = r.get("timings") or {}
        answer = r.get("answer") or ""
        expect = it.get("expect") or []
        # "오후 2시|14시" 처럼 | 로 구분하면 그중 하나만 있어도 정답으로 칩니다.
        rec["hit"] = all(any(alt in answer for alt in k.split("|")) for k in expect) if expect else None
        want_src = it.get("source")
        srcs = [Path(x["source"]).name for x in r.get("sources") or []]
        rec["src_hit"] = (want_src in srcs) if want_src else None
        rec.update({k: t.get(k) for k in ("stt_ms", "retrieval_ms", "first_token_ms", "total_ms")})
        rec["answer"] = answer
        rows.append(rec)
        flag = {True: "O", False: "X", None: "-"}
        extra = f" CER {rec['cer']:.2f} '{rec['heard']}'" if args.voice else ""
        print(f"[{i}/{len(items)}] 답{flag[rec['hit']]} 문서{flag[rec['src_hit']]} 첫토큰 {rec['first_token_ms']}ms 전체 {rec['total_ms']}ms{extra} | {q}")

    def avg(key):
        vals = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        return sum(vals) / len(vals) if vals else None

    def rate(key):
        vals = [r[key] for r in rows if r.get(key) is not None]
        return sum(vals) / len(vals) if vals else None

    print("\n=== 요약 ===")
    print(f"모델 {hub.models['llm']} · 검색 {s.retrieval_mode} · top_k {s.top_k}" + (f" · STT {hub.models['stt']} @ {args.sr}Hz" if args.voice else ""))
    for label, key, fn, unit in [
        ("답변 키워드 적중률", "hit", rate, "%"),
        ("정답 문서 검색률", "src_hit", rate, "%"),
        ("평균 CER", "cer", avg, ""),
        ("평균 STT", "stt_ms", avg, "ms"),
        ("평균 검색", "retrieval_ms", avg, "ms"),
        ("평균 첫 토큰", "first_token_ms", avg, "ms"),
        ("평균 전체", "total_ms", avg, "ms"),
    ]:
        v = fn(key)
        if v is None:
            continue
        print(f"{label}: {v * 100:.0f}%" if unit == "%" else f"{label}: {v:.0f}{unit}" if unit else f"{label}: {v:.3f}")
    if args.output:
        Path(args.output).write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
        print(f"결과 저장: {args.output}")
    return 0


def synth_question(text: str, sr: int, voice: str) -> bytes:
    """벤치마크용 질문 음성 만들기 (macOS say). sr=8000이면 안경 HFP 마이크 조건을 흉내 냅니다."""
    if not shutil.which("say"):
        raise SystemExit("--voice 벤치마크는 macOS의 say가 필요해요")
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "q.wav"
        src = Path(td) / "q.txt"
        src.write_text(text, encoding="utf-8")
        subprocess.run(["say", "-v", voice, "--file-format=WAVE", "--data-format=LEI16@22050", "-o", str(out), "-f", str(src)], check=True)
        x, in_sr = audio.read_wav(out.read_bytes())
        return audio.write_wav(audio.resample(x, in_sr, sr), sr)


def cmd_export(s: Settings, args) -> int:
    from nunchi.server import export_mobile

    hub = _hub(s, probe_ollama=False, embedder=None, stt=None)
    data = export_mobile(hub)
    Path(args.output).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    print(f"조각 {len(data['chunks'])}개 → {args.output} ({Path(args.output).stat().st_size / 1024:.0f}KB)")
    print("폰 앱의 '단독 모드'에서 이 파일을 불러오세요 (파일 앱/AirDrop/USB).")
    return 0


def cmd_config(s: Settings, args) -> int:
    print(json.dumps(s.public_dict(), ensure_ascii=False, indent=2))
    return 0


# ---------------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nunchi", description="오프라인 RAG + 로컬 LLM 허브 (Meta AI 안경용)")
    p.add_argument("--env", default=".env", help="설정 파일 (기본: 현재 폴더의 .env)")
    p.add_argument("--data-dir", help="지식베이스 저장 폴더 (기본: ~/nunchi)")
    p.add_argument("--model", help="LLM 모델 (NUNCHI_LLM_MODEL)")
    p.add_argument("-v", "--version", action="version", version=f"nunchi {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("doctor", help="설치·모델·네트워크 점검").set_defaults(fn=cmd_doctor)
    sub.add_parser("config", help="현재 설정 보기").set_defaults(fn=cmd_config)

    sp = sub.add_parser("ingest", help="문서 넣기")
    sp.add_argument("paths", nargs="+")
    sp.add_argument("--reembed", action="store_true", help="임베딩 모델을 바꿨을 때 전부 다시 계산")
    sp.add_argument("--prune", action="store_true", help="폴더에서 사라진 파일을 색인에서도 삭제")
    sp.set_defaults(fn=cmd_ingest)

    sp = sub.add_parser("kb", help="문서 목록")
    sp.add_argument("--remove", help="파일 이름으로 문서 삭제")
    sp.set_defaults(fn=cmd_kb)

    sp = sub.add_parser("ask", help="한 번 묻기")
    sp.add_argument("question")
    sp.add_argument("--image", help="사진과 함께 묻기")
    sp.add_argument("--speak", action="store_true", help="답을 소리로 듣기")
    sp.add_argument("--style", choices=["voice", "text"])
    sp.add_argument("--no-rag", action="store_true", help="문서 검색 없이 모델에게만 묻기")
    sp.add_argument("--json", action="store_true")
    sp.set_defaults(fn=cmd_ask)

    sp = sub.add_parser("chat", help="대화형으로 묻기")
    sp.add_argument("--speak", action="store_true")
    sp.add_argument("--sources", action="store_true", help="검색된 문서도 보여주기")
    sp.set_defaults(fn=cmd_chat)

    sp = sub.add_parser("transcribe", help="음성 인식만 테스트")
    sp.add_argument("file")
    sp.set_defaults(fn=cmd_transcribe)

    sp = sub.add_parser("say", help="음성 합성만 테스트")
    sp.add_argument("text")
    sp.add_argument("-o", "--output")
    sp.set_defaults(fn=cmd_say)

    sp = sub.add_parser("voice", help="음성 파일로 질문")
    sp.add_argument("file")
    sp.add_argument("--speak", action="store_true")
    sp.add_argument("-o", "--output", help="답변 음성을 WAV로 저장")
    sp.set_defaults(fn=cmd_voice)

    sp = sub.add_parser("listen", help="맥 마이크로 말해서 질문")
    sp.add_argument("--device", default=":0", help="ffmpeg avfoundation 오디오 장치 (기본 :0)")
    sp.add_argument("--seconds", type=float, help="고정 녹음 시간(초). 없으면 Enter로 끝냄")
    sp.add_argument("--once", action="store_true")
    sp.set_defaults(fn=cmd_listen)

    sp = sub.add_parser("serve", help="서버 실행")
    sp.add_argument("--host", help="0.0.0.0 이면 같은 네트워크의 폰에서 접속 가능")
    sp.add_argument("--port", type=int)
    sp.add_argument("--token", help="접속 토큰 (NUNCHI_TOKEN)")
    sp.add_argument("--https", action="store_true", help="자체 서명 인증서로 HTTPS (폰 브라우저 마이크용)")
    sp.add_argument("--no-warmup", action="store_true")
    sp.set_defaults(fn=cmd_serve)

    sp = sub.add_parser("bench", help="지연·정확도 측정")
    sp.add_argument("--file", default=str(Path(__file__).resolve().parents[2] / "examples" / "eval" / "questions.jsonl"))
    sp.add_argument("--voice", action="store_true", help="say로 만든 음성으로 STT까지 측정")
    sp.add_argument("--sr", type=int, default=8000, help="--voice 음성 샘플레이트 (8000=안경 HFP 조건)")
    sp.add_argument("--limit", type=int)
    sp.add_argument("-o", "--output", help="결과 JSONL 저장")
    sp.set_defaults(fn=cmd_bench)

    sp = sub.add_parser("export-mobile", help="폰 단독 모드용 지식베이스 내보내기")
    sp.add_argument("-o", "--output", default="kb.json")
    sp.set_defaults(fn=cmd_export)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {"data_dir": args.data_dir, "llm_model": args.model}
    settings = Settings.from_env(dotenv=args.env, **overrides)
    try:
        return int(args.fn(settings, args) or 0)
    except KeyboardInterrupt:
        return 130
