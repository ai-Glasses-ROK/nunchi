"""run_eval.py 결과(jsonl)를 표로 요약합니다.

  python research/csat/summarize.py csat_results.jsonl            # 마크다운 표
  python research/csat/summarize.py csat_results.jsonl --json     # 숫자만 (차트·문서용)

같은 (모델, 조건, think, 과목)이 여러 번 돌았으면 마지막 실행 결과만 씁니다.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

LABEL = {"kor": "국어", "eng": "영어", "math": "수학"}
COND = {
    "oracle": "문항 직접 제공",
    "rag-naive": "RAG (문제지 원문)",
    "rag-structured": "RAG (문항 번호 구조화)",
    "rag-lookup": "RAG (구조화 + 번호 조회)",
    "vision": "이미지 직접",
    "ocr": "이미지→글→풀이",
}


def load(path: Path) -> list[dict]:
    rows: dict[tuple, dict] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        rows[(r["model"], r["condition"], r["think"], r["subject"], r["number"])] = r
    return list(rows.values())


def summarize(rows: list[dict]) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["model"], r["condition"], r["think"], r["subject"]), []).append(r)
    out = []
    for (model, cond, think, subj), rs in groups.items():
        hits = [r["retrieval_hit"] for r in rs if r.get("retrieval_hit") is not None]
        unparsed = sum(r["pred"] is None for r in rs)
        out.append(
            {
                "model": model.removesuffix("-mlx"),
                "condition": cond,
                "think": think,
                "subject": subj,
                "n": len(rs),
                "correct": sum(r["correct"] for r in rs),
                "accuracy": sum(r["correct"] for r in rs) / len(rs),
                "score": sum(r["points"] for r in rs if r["correct"]),
                "total": sum(r["points"] for r in rs),
                "retrieval_hit": (sum(hits) / len(hits)) if hits else None,
                "median_sec": statistics.median(r["sec"] for r in rs),
                "p90_sec": sorted(r["sec"] for r in rs)[max(0, int(len(rs) * 0.9) - 1)],
                "unparsed": unparsed,
            }
        )
    order = {"kor": 0, "eng": 1, "math": 2}
    cond_order = list(COND)
    out.sort(key=lambda g: (order.get(g["subject"], 9), g["model"], cond_order.index(g["condition"]) if g["condition"] in cond_order else 9, g["think"]))
    return out


def to_markdown(groups: list[dict]) -> str:
    lines = [
        "| 과목 | 모델 | 조건 | 사고 | 정답 | 정답률 | 원점수 | 검색 적중 | 중앙값(초) |",
        "|---|---|---|---|---:|---:|---:|---:|---:|",
    ]
    for g in groups:
        hit = "—" if g["retrieval_hit"] is None else f"{g['retrieval_hit']:.0%}"
        lines.append(
            f"| {LABEL.get(g['subject'], g['subject'])} | {g['model']} | {COND.get(g['condition'], g['condition'])} | {g['think']} "
            f"| {g['correct']}/{g['n']} | {g['accuracy']:.0%} | {g['score']}/{g['total']} | {hit} | {g['median_sec']:.1f} |"
        )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("results", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    groups = summarize(load(args.results))
    if args.json:
        print(json.dumps(groups, ensure_ascii=False, indent=1))
    else:
        print(to_markdown(groups))
    return 0


if __name__ == "__main__":
    sys.exit(main())
