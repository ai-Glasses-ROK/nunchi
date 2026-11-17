# 허브 API 레퍼런스

기본 주소 `http://<허브>:8765`. 토큰을 정했다면 모든 `/v1/*` 요청에 헤더 `Authorization: Bearer <토큰>` (또는 `X-Hub-Token: <토큰>`).
자동 생성 문서: `http://<허브>:8765/docs` (Swagger UI)

## 응답 방식 두 가지

| 방식 | 요청 | 응답 |
|---|---|---|
| 한 번에 | `stream=false`(기본) | JSON 하나 |
| 조금씩 | `stream=true` | `text/event-stream` (SSE) |

### SSE 이벤트

| event | data | 언제 |
|---|---|---|
| `transcript` | `{"text","ms","audio":{input_sr,output_sec,…}}` | 음성 인식 끝 |
| `caption` | `{"text","ms"}` | 사진 한 줄 묘사(사진 질문) |
| `retrieval` | `{"query","sources":[…],"ms","mode","warnings","lookup"}` | 검색 끝 (`lookup`: 번호 조회로 통째로 가져온 절 이름들, 없으면 빈 목록) |
| `delta` | `{"text"}` | LLM 글자 조각 |
| `sentence` | `{"index","text"}` | 완성된 문장(마크다운 제거됨) → **폰이 바로 읽기** |
| `audio` | `{"index","wav_b64"}` | (speak=true) 문장별 허브 음성 |
| `error` | `{"stage","message"}` | 단계별 오류 (stt/caption/llm/tts) |
| `done` | `{"ok","answer","speech","sources","timings","model"}` | 끝 |

`timings`: `stt_ms`, `caption_ms`, `retrieval_ms`, `first_token_ms`, `llm_ms`, `total_ms`

## 엔드포인트

### `GET /v1/health`
상태·모델·색인 통계.

```bash
curl -s http://127.0.0.1:8765/v1/health
```

### `POST /v1/ask` — 글자 질문 (JSON)

| 필드 | 기본 | 설명 |
|---|---|---|
| `question` | (필수) | 질문 |
| `session_id` | null | 같은 값이면 대화 기억 |
| `stream` | false | SSE |
| `speak` | false | 허브 음성(문장별 audio 이벤트 / JSON엔 `audio_wav_b64`) |
| `use_rag` | true | 문서 검색 |
| `style` | voice | `voice` 또는 `text` |
| `top_k` | 4 | 1~12 |

```bash
curl -s http://127.0.0.1:8765/v1/ask -H 'Content-Type: application/json' -d '{"question":"매너 타임이 언제야?"}'
```

```bash
curl -N http://127.0.0.1:8765/v1/ask -H 'Content-Type: application/json' -d '{"question":"매너 타임이 언제야?","stream":true}'
```

### `POST /v1/voice` — 음성 질문 (multipart)
`file`(WAV 권장, 8k/16k 모노 16bit. 다른 형식은 허브에 ffmpeg 필요) + `session_id`, `stream`, `speak`, `style`, `top_k`

```bash
curl -s -X POST http://127.0.0.1:8765/v1/voice -F file=@q.wav -F stream=false
```

말소리가 없거나 너무 짧으면 `ok=false`, `answer="잘 못 들었어요. 다시 한 번 말씀해 주세요."`

### `POST /v1/vision` — 사진 질문 (multipart)
`image`(JPEG/PNG) + `question`(기본 "지금 보이는 것을 짧게 설명해 줘.") + `use_rag`(기본 true) + 위와 같은 옵션

### `POST /v1/stt` · `POST /v1/tts`
음성 인식만 / 음성 합성만 (`{"text": "…"}` → `audio/wav`)

### 지식베이스
| 요청 | 설명 |
|---|---|
| `GET /v1/kb` | 문서 목록·통계 (허브 컴퓨터의 전체 경로는 숨김) |
| `POST /v1/kb/upload` | `files` 여러 개 업로드·색인 (`NUNCHI_ALLOW_UPLOAD=false`로 끄기) |
| `GET /v1/kb/export` | 폰 단독 모드용 JSON (`format: nunchi-kb`, `tokenizer: char-bigram-v1`) |
| `POST /v1/session/reset` | `{"session_id": "…"}` 대화 기억 지우기 |

## 다른 언어에서 SSE 읽기

### 파이썬

```python
import httpx, json
with httpx.stream("POST", "http://127.0.0.1:8765/v1/ask", json={"question": "체크인 시간?", "stream": True}) as r:
    event = None
    for line in r.iter_lines():
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            data = json.loads(line[6:])
            if event == "sentence":
                print("읽기:", data["text"])
```

### Swift / Kotlin
`clients/ios/Nunchi/Hub/HubClient.swift`, `clients/android/.../HubClient.kt` 에 그대로 쓸 수 있는 구현이 있어요. 핵심: **`data:` 줄마다 직전 `event:` 줄의 종류로 처리**.
