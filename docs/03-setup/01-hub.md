# 허브 설치 (맥 · 윈도우 · 리눅스)

> 목표: `nunchi doctor` 가 "모두 정상이에요 🎉" 를 출력하는 것. 15~30분 (모델 내려받기 시간 제외).

## 목차

- [0. 준비물](#0-준비물)
- [1. Ollama 설치 (모든 모델의 엔진)](#1-ollama-설치-모든-모델의-엔진)
- [2. 모델 내려받기 (인터넷 필요, 한 번만)](#2-모델-내려받기-인터넷-필요-한-번만)
- [3. Nunchi 허브 설치](#3-nunchi-허브-설치)
- [4. 점검](#4-점검)
- [5. 첫 질문](#5-첫-질문)
- [6. 서버로 띄우기 (폰·웹 접속용)](#6-서버로-띄우기-폰웹-접속용)
- [7. 설정 파일](#7-설정-파일)
- [윈도우 메모](#윈도우-메모)
- [리눅스 메모](#리눅스-메모)

## 0. 준비물

| 항목 | 최소 | 권장 |
|---|---|---|
| 컴퓨터 | 램 8GB | 애플 실리콘 맥 16GB+ / 엔비디아 GPU PC |
| 디스크 | 15GB | 30GB+ (모델 여러 개 비교 시) |
| 파이썬 | 3.10 | 3.12 |

## 1. Ollama 설치 (모든 모델의 엔진)

- 맥·윈도우: [ollama.com/download](https://ollama.com/download) 에서 앱 설치 → 실행하면 메뉴바/트레이에 상주
- 리눅스:

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

확인:

```bash
ollama --version
```

## 2. 모델 내려받기 (인터넷 필요, 한 번만)

```bash
ollama pull gemma4:e4b
```

```bash
ollama pull bge-m3
```

선택(허브가 직접 한국어 음성을 만들 때):

```bash
ollama pull lfm3-audio:2b
```

> 맥이라면 `gemma4:e4b-mlx` 를 받으면 더 빨라요. Nunchi가 자동으로 알아서 씁니다. 메모리가 8GB면 `gemma4:e2b` 를 받으세요. → [모델 고르기](../02-concepts/03-model-selection.md)

## 3. Nunchi 허브 설치

```bash
git clone https://github.com/ai-Glasses-ROK/nunchi.git
```

```bash
cd nunchi/hub
```

uv(권장, 빠른 파이썬 관리자):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

```bash
uv venv --python 3.12 && uv pip install -e ".[dev]"
```

uv 없이 기본 파이썬으로:

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

맥에서는 한 번에 끝내는 스크립트도 있어요:

```bash
../scripts/setup_mac.sh
```

## 4. 점검

```bash
.venv/bin/nunchi doctor
```

정상 예시 (실험 환경: M5 Pro 24GB, macOS 26):

```
nunchi 0.1.0 · Python 3.12.13 · Darwin arm64
✅ Ollama 0.40.16 연결됨 (http://127.0.0.1:11434) · 설치된 모델 9개
✅ LLM: gemma4:e4b (→ gemma4:e4b-mlx)  [completion, vision, audio, tools, thinking]
✅ 임베딩: bge-m3 (→ bge-m3:latest)  [embedding]
✅ 음성인식: gemma4:e4b (→ gemma4:e4b-mlx)  [completion, vision, audio, tools, thinking]
✅ 음성합성: Ollama lfm3-audio:2b (voice=female-korean)
✅ ffmpeg: 있음
✅ 지식베이스: 문서 3개 · 조각 22개 · 임베딩 22개
ℹ️  이 컴퓨터 주소: 192.168.0.10 · Bonjour 이름: my-mac.local
결과: 모두 정상이에요 🎉
```

## 5. 첫 질문

```bash
.venv/bin/nunchi ingest ../examples/knowledge
```

```bash
.venv/bin/nunchi ask "캠핑장 체크인은 몇 시부터야?"
```

## 6. 서버로 띄우기 (폰·웹 접속용)

같은 와이파이의 폰에서 접속하려면 `0.0.0.0` 으로, **토큰을 꼭 함께** 쓰세요.

```bash
.venv/bin/nunchi serve --host 0.0.0.0 --token 내가정한비밀번호
```

화면에 `폰에서 접속: http://192.168.0.10:8765` 처럼 주소가 나옵니다. 브라우저로 열면 웹 클라이언트가 떠요.

## 7. 설정 파일

자주 바꾸는 값은 `.env` 에:

```bash
cp .env.example .env
```

모든 항목은 [`hub/.env.example`](../../hub/.env.example) 에 한국어 설명과 함께 있어요.

## 윈도우 메모

- PowerShell에서 경로는 `.venv\Scripts\nunchi.exe doctor` 처럼 써요.
- 방화벽이 처음 서버를 띄울 때 '개인 네트워크 허용'을 물으면 허용하세요.
- macOS `say` 가 없으니 허브 TTS는 `lfm3-audio:2b` 를 쓰거나 폰 TTS(기본)를 쓰세요.

## 리눅스 메모

- 엔비디아 GPU면 Ollama가 자동으로 CUDA를 씁니다. `nvidia-smi` 로 확인.
- 소리 재생(`--speak`)은 `aplay`/`paplay` 중 있는 것을 사용해요.
