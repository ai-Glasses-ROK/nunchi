# 실험 원자료

[평가와 실험 결과](../../docs/05-applications/03-evaluation.md)의 표와 차트를 만든 숫자입니다. 2026-11-18, Apple M5 Pro 24GB · macOS 26.6 · Ollama 0.40.16.

| 파일 | 내용 |
|---|---|
| `csat_2026.jsonl` | 수능 문항별 결과 한 줄씩: 모델·조건·사고 수준·과목·번호·예측·정답·정오·배점·검색 적중·시간·출력 토큰 수. **모델 출력 원문은 뺐습니다**(문제 지문을 인용할 수 있어서) |
| `latency_m5pro.json` | 지연시간 p50/p90 (모드별 16회, 서로 다른 질문·사진, 모드마다 캐시 비움) |

표 다시 만들기:

```bash
hub/.venv/bin/python research/csat/summarize.py research/results/csat_2026.jsonl
```

```bash
hub/.venv/bin/python research/csat/summarize.py research/results/csat_2026.jsonl --json > summary.json && hub/.venv/bin/python research/make_charts.py summary.json --out docs/images
```

같은 (모델, 조건, 사고 수준, 과목, 번호)는 마지막 실행만 남겼습니다. 영어 36~40·43번은 레이아웃 복원 후 다시 잰 값, RAG 세 조건은 저장 텍스트를 원문(NFC)으로 바꾸고 번호 조회를 '그 절만'으로 바꾼 뒤 다시 잰 값입니다.
