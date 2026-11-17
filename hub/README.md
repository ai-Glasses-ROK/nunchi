# Nunchi 허브 (`hub/`)

인터넷 없이 동작하는 **RAG + 로컬 LLM + 음성(STT/TTS) + 사진 질문** 서버와 명령줄 도구입니다.
모든 모델은 [Ollama](https://ollama.com) 하나로 돌립니다.

## 설치

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
```

```bash
ollama pull gemma4:e4b && ollama pull bge-m3
```

```bash
.venv/bin/nunchi doctor
```

자세한 안내: [docs/03-setup/01-hub.md](../docs/03-setup/01-hub.md)

## 명령

| 명령 | 하는 일 |
|---|---|
| `nunchi doctor` | 설치·모델·네트워크 점검 |
| `nunchi config` | 지금 적용된 설정 보기 (토큰은 가림) |
| `nunchi ingest <경로…> [--reembed] [--prune]` | 문서 색인 (.md .txt .pdf .docx .html .csv) |
| `nunchi kb [--remove 이름]` | 문서 목록·삭제 |
| `nunchi ask "질문" [--image 사진] [--speak] [--style text]` | 한 번 묻기 |
| `nunchi chat [--speak] [--sources]` | 대화형 |
| `nunchi transcribe 파일.wav` / `nunchi say "문장"` | 음성 인식 / 합성만 |
| `nunchi voice 파일.wav [--speak] [-o 답.wav]` | 음성 질문 → 음성 답 |
| `nunchi listen` | 맥 마이크로 말하기 |
| `nunchi serve [--host 0.0.0.0] [--token …] [--https]` | 폰·웹용 서버 (웹 클라이언트 포함) |
| `nunchi bench [--voice --sr 8000]` | 정확도·지연·CER 측정 |
| `nunchi export-mobile -o kb.json` | 폰 단독 모드용 지식베이스 |

## 구조

```
nunchi/
├── config.py        설정 (NUNCHI_* 환경변수, .env)
├── pipeline.py      Hub: 음성/글자/사진 → STT → 캡션 → 검색 → LLM → (TTS) 이벤트 스트림
├── server.py        FastAPI (/v1/ask, /v1/voice, /v1/vision, /v1/kb …), SSE
├── cli.py           명령줄
├── llm.py           Ollama /api/chat · OpenAI 호환 /v1/chat/completions
├── stt.py           /v1/audio/transcriptions (Ollama·Gemma 4) · whisper.cpp
├── tts.py           /v1/audio/speech (Ollama·lfm3-audio) · macOS say · 없음(폰이 읽기)
├── audio.py         WAV 읽기/쓰기, 리샘플(안티에일리어싱·안티이미징), VAD, 정규화
├── textutil.py      한국어 정규화, 마크다운 제거, 문장 분리, 스트리밍 문장 조립
├── ollama_util.py   설치 모델 목록, 이름 해석(-mlx 자동), 기능 조회
├── rag/
│   ├── loaders.py   문서 읽기 (CP949 자동, DOCX·HTML 표준 라이브러리 파서, CSV→문장)
│   ├── chunker.py   제목 경로를 기억하는 청커 (맥락 헤더, 오버랩)
│   ├── bm25.py      한국어 2-gram BM25 + 질문 꼬리말 제거
│   ├── embedder.py  Ollama /api/embed · OpenAI 호환 · 테스트용 해시 임베딩
│   ├── store.py     SQLite 하나로 끝내는 저장소 (변경 감지 스탬프)
│   ├── retriever.py 하이브리드 검색 + RRF + 자동 BM25 대체 + 번호 조회("5번"→ 그 절 통째로)
│   └── prompt.py    음성/화면용 프롬프트, 음성 인식 오류 힌트, 사진 캡션 프롬프트
└── web/             브라우저 클라이언트 (외부 라이브러리 없음)
```

## 테스트

```bash
.venv/bin/python -m pytest -q
```

외부 서버 없이 도는 단위·API 테스트 45개(가짜 LLM/임베딩/STT/TTS 사용). 실제 Ollama 통합 테스트:

```bash
.venv/bin/python -m pytest -q -m integration
```

## 설정

[`.env.example`](.env.example) 을 `.env` 로 복사해 고치세요. 모든 값은 `NUNCHI_` 접두사 환경변수로도 바꿀 수 있어요.
