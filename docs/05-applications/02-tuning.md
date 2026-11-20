# 튜닝: 빠르게, 정확하게, 오래

## 목차

- [1. 빠르게 (지연시간)](#1-빠르게-지연시간)
- [2. 정확하게](#2-정확하게)
- [3. 오래 (배터리·발열)](#3-오래-배터리발열)
- [4. 설정 예시 모음](#4-설정-예시-모음)
  - [8GB 노트북](#8gb-노트북)
  - [정확도 우선 (32GB+)](#정확도-우선-32gb)
  - [화면용 자세한 답 + 근거 번호](#화면용-자세한-답--근거-번호)

## 1. 빠르게 (지연시간)

| 증상 | 원인 | 해결 |
|---|---|---|
| 첫 질문만 몇 초 걸림 | 모델 로딩 | `nunchi serve` 는 시작할 때 예열함. `NUNCHI_KEEP_ALIVE=2h` 로 오래 붙잡기 |
| 음성 질문마다 2~3초 더 걸림 | STT와 LLM 모델이 달라 번갈아 로딩 | `NUNCHI_STT_MODEL` 을 비워 LLM과 같은 모델 쓰기(기본) |
| 검색이 1초 넘음 | 임베딩 모델이 메모리에서 밀려남 | 모델 수 줄이기, 허브 TTS 끄기(`NUNCHI_TTS_BACKEND=none`) |
| 답이 길어서 느림 | | `NUNCHI_MAX_TOKENS=256`, `answer_style=voice` |
| 사고 모드가 켜져 있음 | | `NUNCHI_THINK=false` (기본) |
| 맥인데 느림 | GGUF 버전 사용 중 | `-mlx` 태그 모델 받기 (자동 선택됨) |

Ollama가 지금 무엇을 메모리에 올렸는지:

```bash
ollama ps
```

## 2. 정확하게

| 문제 | 해결 |
|---|---|
| 엉뚱한 문서를 찾음 | `nunchi ask` 출력의 검색 결과를 보고 문서 제목·구조 손보기, `NUNCHI_TOP_K=6` |
| 숫자·코드를 못 찾음 | `NUNCHI_RETRIEVAL_MODE=hybrid` 유지(키워드 검색이 숫자에 강함) |
| "5번 문제", "3단계", "12쪽"처럼 번호로 물으면 엉뚱한 곳 | 문서에 번호를 제목으로 달기(`## 3단계: 연료 확인`) → 번호 조회가 그 절을 통째로 가져옴(`NUNCHI_NUMBER_LOOKUP=true`, 기본값). 절이 길면 `NUNCHI_MAX_SECTION_CHARS` |
| 다른 말로 물으면 못 찾음 | 임베딩 모델 확인(`bge-m3`), `nunchi doctor` 로 임베딩 수 확인 |
| 문서에 없는 걸 지어냄 | `NUNCHI_STRICT_RAG=true`, 온도 낮추기 `NUNCHI_TEMPERATURE=0.1` |
| 음성 인식 오류 | 16kHz 경로, 조용한 곳, `NUNCHI_STT_PROMPT`, 큰 모델(e4b) |
| 사진 판독 실패 | 사진을 가까이·정면에서, 긴 변 1280px 근처 |
| 추론이 필요한 질문(계산·수학) | 해당 질문만 사고 모드, 또는 수학 전용 백엔드(gpt-oss) → [평가](03-evaluation.md) |

## 3. 오래 (배터리·발열)

| 대상 | 방법 |
|---|---|
| 안경 | 카메라는 찍을 때만(앱 기본), 해상도 medium/low, 연속 스트리밍 피하기 |
| 공기계 | 허브 모드(폰은 중계만), 화면 밝기 최저, 케이스 벗기기 |
| 허브 노트북 | 전원 연결, 모델 하나만 상주(e4b로 STT+LLM) |

## 4. 설정 예시 모음

### 8GB 노트북

```bash
NUNCHI_LLM_MODEL=gemma4:e2b NUNCHI_TTS_BACKEND=none nunchi serve --host 0.0.0.0 --token …
```

### 정확도 우선 (32GB+)

```bash
NUNCHI_LLM_MODEL=gemma4:12b NUNCHI_STT_MODEL=gemma4:e4b NUNCHI_TOP_K=6 nunchi serve
```

### 화면용 자세한 답 + 근거 번호

```bash
NUNCHI_ANSWER_STYLE=text nunchi chat --sources
```
