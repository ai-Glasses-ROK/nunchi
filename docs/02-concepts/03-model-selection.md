# 모델 고르기

## 목차

- [1. 역할별로 필요한 모델](#1-역할별로-필요한-모델)
- [2. 메모리별 추천 조합](#2-메모리별-추천-조합)
- [3. 바꾸는 방법](#3-바꾸는-방법)
- [4. 한국어 관점 메모](#4-한국어-관점-메모)
- [5. 어려운 추론이 필요할 때 — 수능 실험에서 배운 것](#5-어려운-추론이-필요할-때--수능-실험에서-배운-것)
- [6. 외부 엔진 연결 (OpenAI 호환)](#6-외부-엔진-연결-openai-호환)

## 1. 역할별로 필요한 모델

| 역할 | 기본값 | 대안 | 필요한 기능(`ollama show`) |
|---|---|---|---|
| 답변 LLM | `gemma4:e4b` | `gemma4:e2b`(가벼움), `gemma4:12b`(정확) | completion |
| 사진 질문 | (LLM과 같음) | 비전 지원 모델 | vision |
| 음성 인식 | (LLM과 같음) | `gemma4:e2b`, whisper.cpp | audio |
| 임베딩 | `bge-m3` | `qwen3-embedding:0.6b`, `embeddinggemma` | embedding |
| 허브 TTS(선택) | `lfm3-audio:2b` | macOS `say`, 폰 TTS | speech |

> 맥(애플 실리콘)에서는 설정한 이름(`gemma4:e4b`)이 없고 `gemma4:e4b-mlx`가 있으면 **자동으로 MLX 버전을 씁니다.** `nunchi doctor`에서 `(→ gemma4:e4b-mlx)`로 표시돼요.

## 2. 메모리별 추천 조합

Ollama는 GPU(통합 메모리)에 올릴 수 있는 양만큼만 모델을 동시에 띄웁니다. 메모리를 넘으면 모델을 **내렸다 올렸다** 하며 매번 수 초씩 기다려요(실측: 24GB 맥에서 e2b·e4b를 번갈아 쓰면 STT가 0.2초 → 2~3초).

| 허브 메모리 | 추천 | 대략 사용량 |
|---|---|---|
| 8GB | `gemma4:e2b`(LLM+STT) + `bge-m3`, TTS는 폰 | ~6GB |
| 16GB | `gemma4:e4b`(LLM+STT) + `bge-m3`, TTS는 폰 | ~10GB |
| 24GB (실험 환경) | 위 + `lfm3-audio:2b`(허브 TTS) | ~13GB |
| 32GB+ | `gemma4:12b` 또는 `gemma4:26b` + `bge-m3` | 16~20GB |
| 48GB+ | 추가로 `gpt-oss:20b`(수학·추론 전용 백엔드) | +13GB |

공기계(폰 단독 모드)는 [폰 단독 모드](../04-practice/07-standalone-phone.md)를 보세요. 램 8GB 이상이면 2~4B 모델, 4GB 이하면 1B급만 현실적이에요.

## 3. 바꾸는 방법

```bash
ollama pull gemma4:e2b
```

```bash
NUNCHI_LLM_MODEL=gemma4:e2b nunchi doctor
```

임베딩 모델을 바꾸면 기존 색인과 차원이 달라지니 다시 계산해야 해요. (안 하면 자동으로 키워드 검색만 쓰고 경고를 보여 줍니다)

```bash
NUNCHI_EMBED_MODEL=qwen3-embedding:0.6b nunchi ingest ../examples/knowledge --reembed
```

## 4. 한국어 관점 메모

- **Gemma 4**: 한국어 이해·생성이 자연스럽고, 한 모델로 글·사진·소리를 처리. 이 레포의 실험은 전부 Gemma 4 기준.
- **bge-m3**: 한국어 검색에서 널리 검증된 다국어 임베딩(1024차원, 긴 문서 8k 토큰).
- **qwen3-embedding**: 질의 앞에 지시문을 붙이면 좋아짐 → `NUNCHI_EMBED_QUERY_PREFIX` 설정.
- **gpt-oss:20b**: 텍스트 전용. 추론(사고) 수준을 low/medium/high로 조절. 수능 수학 같은 계산형 문제에 강함 → [평가 결과](../05-applications/03-evaluation.md)

## 5. 어려운 추론이 필요할 때 — 수능 실험에서 배운 것

같은 노트북(M5 Pro 24GB)에서 2026 수능 공통과목으로 잰 결과입니다. 표 전체는 [평가](../05-applications/03-evaluation.md#1-수능-국어수학영어).

| 상황 | 해 볼 것 | 실측 |
|---|---|---|
| 작은 모델로 사진 속 수학 문제 | **눈과 머리를 나누기**: 먼저 이미지를 글(LaTeX)로 옮기게 하고(OCR), 그 글만 보고 사고 모드로 풀게 함 — 같은 `gemma4:e4b`를 두 번 호출 | 이미지 직접 50% → 사고 모드 68% → **OCR→사고 77%** (17/22) |
| 더 큰 모델로 | OCR은 `gemma4:e4b`, 풀이는 텍스트 전용 `gpt-oss:20b`(사고 medium) | 73% (16/22) — 6문항 중 4문항은 사고가 길어져 출력 한도 초과, 2문항은 OCR 오류. 한도를 정하면 작은 모델이 더 실용적 |
| 한국어 긴 글 독해 | 다국어에 강한 모델이 유리. 파라미터가 크다고 꼭 이기지는 않음 | 국어: `gemma4:e4b` 59% vs `gpt-oss:20b` 53% (사고 low) |
| 음성 답 | 사고 모드는 끄기(첫 문장이 늦어짐). 추론이 필요한 질문만 따로 | 사고 켜면 중앙값 5.9초 → 9.4초 (수학 이미지) |

- `gpt-oss:20b`(약 13GB)는 24GB 맥에서도 혼자서는 돕니다. 다만 Gemma와 번갈아 쓰면 모델 교체 대기가 생겨요.
- 사고 수준을 올리면 한 문항에 수 분씩 생각하다 출력 한도에 걸리기도 합니다(수학 10번: 200초 후 답 없음). 시간 제한·출력 한도를 함께 정하세요.

## 6. 외부 엔진 연결 (OpenAI 호환)

llama.cpp `llama-server`, LM Studio, `mlx_lm.server`, vLLM처럼 **내 컴퓨터에서 도는** OpenAI 호환 서버도 연결할 수 있어요.

```bash
NUNCHI_LLM_BACKEND=openai NUNCHI_LLM_URL=http://127.0.0.1:8080/v1 NUNCHI_LLM_MODEL=local nunchi serve
```

같은 방식으로 클라우드 API를 연결하면 '클라우드 기준치' 비교 실험도 할 수 있습니다(이 경우 질문이 외부로 나간다는 점에 유의).
