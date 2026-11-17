# 예제

| 폴더 | 내용 |
|---|---|
| `knowledge/` | 실습용 **가상의** 문서 3개 — 솔바람 캠핑장 안내, HG-2000 발전기 점검 절차, 허브 사용 설명서 (실제 장소·제품이 아님) |
| `eval/questions.jsonl` | 위 문서로 만든 평가 질문 16개 (`nunchi bench`) |

```bash
nunchi ingest examples/knowledge
```

```bash
nunchi bench --file examples/eval/questions.jsonl
```

질문 형식: `{"q": 질문, "expect": [키워드…], "source": 정답 문서 파일명}` — `expect` 의 `"2시|14시"` 처럼 `|` 로 대안 표기.
