# 예제

## 목차

- [폴더](#폴더)
- [실행](#실행)
- [질문 형식](#질문-형식)

## 폴더

| 폴더 | 내용 |
|---|---|
| `knowledge/` | 실습용 **가상의** 문서 3개 — 솔바람 캠핑장 안내, HG-2000 발전기 점검 절차, 허브 사용 설명서 (실제 장소·제품이 아님) |
| `eval/questions.jsonl` | 위 문서로 만든 평가 질문 16개 (`nunchi bench`) |
| `drone/` | 드론 실험용 픽스호크 점검·문제 해결 안내 — 드론 화면의 답이 이 문서를 근거로 해요 → [드론 실험](../docs/07-drone/01-overview.md) |

`knowledge/`·`drone/`의 문서는 앱과 허브가 **검색하는 자료**라서 목차를 넣지 않았어요(목차 조각이 검색 결과를 흐려요).

## 실행

```bash
nunchi ingest examples/knowledge
```

```bash
nunchi bench --file examples/eval/questions.jsonl
```

## 질문 형식

질문 형식: `{"q": 질문, "expect": [키워드…], "source": 정답 문서 파일명}` — `expect` 의 `"2시|14시"` 처럼 `|` 로 대안 표기.
