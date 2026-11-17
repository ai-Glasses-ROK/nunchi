"""수능 문항으로 로컬 모델 평가하기 (Nunchi 연구용).

조건(condition)
  oracle          문항 텍스트(지문+발문+선택지)를 그대로 줌 → 모델의 순수 실력 (상한선)
  rag-naive       문제지 텍스트를 그대로 지식베이스에 넣고 "국어 5번 문제 풀어 줘"로 질문 → 검색이 문항을 찾아야 함
  rag-structured  '## 국어 5번 문항' 머리말을 붙인 구조화 문서를 넣고 같은 질문 → 문서 구조의 효과
  rag-lookup      rag-structured + 번호 조회·절 확장 ("5번"이 제목에 있는 절을 통째로 가져옴, 허브 기본값)
  vision          수학: 문항 이미지를 비전 모델에 줌 (안경으로 시험지를 보는 상황)
  ocr             수학: 비전 모델(--ocr-model)이 이미지를 글로 옮기고, 평가 모델이 그 글만 보고 풂
                  (이미지를 못 읽는 큰 텍스트 모델, 예: gpt-oss:20b 를 수학에 쓰기 위한 2단계 방식)

사용 예
  python research/csat/run_eval.py --data data/csat2026 --models gemma4:e2b,gemma4:e4b --conditions oracle,rag-naive,rag-structured --subjects kor,eng
  python research/csat/run_eval.py --data data/csat2026 --models gemma4:e4b --conditions vision --subjects math --think on

⚠️ 연구·학습용입니다. 실제 시험장에서 스마트 안경·AI 기기를 쓰는 것은 부정행위입니다.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import statistics
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

import httpx

LABEL = {"kor": "국어", "eng": "영어", "math": "수학"}
CIRCLED = {"①": 1, "②": 2, "③": 3, "④": 4, "⑤": 5}

SYSTEM = (
    "너는 대학수학능력시험 문제를 푸는 수험생이다. 문제를 꼼꼼히 읽고 풀어라. "
    "5지선다형이면 정답 번호(1~5)를, 단답형이면 정수를 고른다. 풀이는 짧게 쓰고, 마지막 줄에는 반드시 '정답: X' 형식으로만 쓴다."
)


def parse_answer(text: str) -> int | None:
    """모델 출력에서 최종 답 추출: '정답: ③' / '정답: 3' / 마지막 원문자."""
    t = text.replace("**", "")
    m = list(re.finditer(r"정답\s*[:：은는]?\s*([①②③④⑤]|\d{1,4})", t))
    if m:
        v = m[-1].group(1)
        if v in CIRCLED:  # 주의: '⑤'.isdigit()은 True지만 int('⑤')는 실패하므로 원문자를 먼저 처리
            return CIRCLED[v]
        return int(v) if v.isascii() and v.isdigit() else None
    circles = re.findall(r"[①②③④⑤]", t)
    if circles:
        return CIRCLED[circles[-1]]
    nums = re.findall(r"\b[0-9]{1,4}\b", t[-80:])
    return int(nums[-1]) if nums else None


def squash(text: str) -> str:
    """검색 적중 판정용: NFKC 정규화 + 공백 제거."""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


class Ollama:
    def __init__(self, url: str, timeout: float = 900) -> None:
        self.url = url.rstrip("/")
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0))

    def chat(self, model: str, messages: list[dict], think: str, images: list[bytes] | None = None, max_tokens: int = 1024) -> tuple[str, dict]:
        msgs = [dict(m) for m in messages]
        if images:
            msgs[-1]["images"] = [base64.b64encode(i).decode() for i in images]
        payload: dict = {
            "model": model,
            "messages": msgs,
            "stream": False,
            "keep_alive": "30m",
            "options": {"temperature": 0, "num_ctx": 16384, "num_predict": max_tokens},
        }
        if think in ("on", "off"):
            payload["think"] = think == "on"
        elif think in ("low", "medium", "high"):
            payload["think"] = think
        r = self.client.post(f"{self.url}/api/chat", json=payload)
        if r.status_code >= 400 and "think" in r.text.lower():
            payload.pop("think", None)
            r = self.client.post(f"{self.url}/api/chat", json=payload)
        r.raise_for_status()
        d = r.json()
        stats = {k: d.get(k) for k in ("total_duration", "eval_count", "prompt_eval_count")}
        return (d.get("message") or {}).get("content") or "", stats


OCR_PROMPT = (
    "이미지에 있는 수학 문제를 빠짐없이 그대로 텍스트로 옮겨 적어라. 수식은 LaTeX로, 선택지는 ①~⑤ 표기를 그대로 쓴다. "
    "그래프나 그림이 있으면 보이는 정보(좌표, 길이, 라벨)를 괄호 안에 설명한다. 문제를 풀지 말고 옮겨 적기만 해라."
)


def ocr_cached(ollama: "Ollama", model: str, data: Path, unit: dict) -> str:
    """수학 문항 이미지를 글로 옮김 (모델별로 캐시해 두고 재사용)."""
    cache_path = data / f"ocr_{re.sub(r'[^A-Za-z0-9]+', '_', model)}.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    key = str(unit["number"])
    if key not in cache:
        text, _ = ollama.chat(model, [{"role": "user", "content": OCR_PROMPT}], "off", [(data / unit["image"]).read_bytes()], 1500)
        cache[key] = text.strip()
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return cache[key]


def build_rag_hub(docs: list[Path], data_dir: Path, ollama_url: str, number_lookup: bool = False):
    """Nunchi 허브의 색인·검색을 그대로 사용 (임베딩 bge-m3 + BM25 하이브리드)."""
    from nunchi.config import Settings
    from nunchi.pipeline import Hub

    s = Settings.from_env(
        dotenv=None, data_dir=str(data_dir), ollama_url=ollama_url, tts_backend="none", stt_backend="none",
        number_lookup=number_lookup,
    )
    hub = Hub(s)
    hub.ingest_paths(docs, log_fn=lambda *_: None)
    return hub


def main() -> int:
    ap = argparse.ArgumentParser(description="수능 문항으로 로컬 모델 평가")
    ap.add_argument("--data", type=Path, required=True, help="prepare.py 출력 폴더")
    ap.add_argument("--answers", type=Path, default=Path(__file__).with_name("answers_2026.json"))
    ap.add_argument("--models", required=True, help="쉼표로 구분 (예: gemma4:e2b,gemma4:e4b)")
    ap.add_argument("--conditions", default="oracle")
    ap.add_argument("--subjects", default="kor,eng")
    ap.add_argument("--think", default="off", help="off | on | low | medium | high")
    ap.add_argument("--top-k", type=int, default=4)
    ap.add_argument("--ocr-model", default="gemma4:e4b", help="ocr 조건에서 이미지를 글로 옮길 비전 모델")
    ap.add_argument("--max-tokens", type=int, default=0, help="0이면 think에 따라 자동")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--numbers", default="", help="이 문항만 (예: 36,37,40) — 데이터를 고친 뒤 일부만 다시 돌릴 때")
    ap.add_argument("--out", type=Path, default=Path("csat_results.jsonl"))
    args = ap.parse_args()

    answers = json.loads(args.answers.read_text(encoding="utf-8"))
    units = [json.loads(l) for l in (args.data / "units.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    subjects = args.subjects.split(",")
    conditions = args.conditions.split(",")
    models = args.models.split(",")
    ollama = Ollama(args.ollama)
    max_tokens = args.max_tokens or (1024 if args.think == "off" else 12000)

    hubs = {}
    tmp = Path(tempfile.mkdtemp(prefix="nunchi-csat-"))
    for cond in conditions:
        if cond == "rag-naive":
            hubs[cond] = build_rag_hub([args.data / f"{s}_raw.txt" for s in subjects if s != "math"], tmp / "naive", args.ollama)
        elif cond == "rag-structured":
            hubs[cond] = build_rag_hub([args.data / f"{s}_structured.md" for s in subjects if s != "math"], tmp / "structured", args.ollama)
        elif cond == "rag-lookup":
            hubs[cond] = build_rag_hub(
                [args.data / f"{s}_structured.md" for s in subjects if s != "math"], tmp / "lookup", args.ollama, number_lookup=True
            )

    out = args.out.open("a", encoding="utf-8")
    rows = []
    for model in models:
        for cond in conditions:
            todo = [u for u in units if u["subject"] in subjects and ((cond in ("vision", "ocr")) == (u["subject"] == "math"))]
            if args.numbers:
                keep = {int(x) for x in args.numbers.split(",")}
                todo = [u for u in todo if u["number"] in keep]
            if args.limit:
                todo = todo[: args.limit]
            for u in todo:
                subj, n = u["subject"], u["number"]
                key = answers[subj][str(n)]
                hit = None
                images = None
                if cond == "oracle":
                    if u.get("needs_image"):
                        continue  # 지문이 그림뿐인 문항은 텍스트 조건에서 제외
                    user = f"[{LABEL[subj]} {n}번]\n{u['text']}"
                elif cond.startswith("rag"):
                    if u.get("needs_image"):
                        continue
                    query = f"{LABEL[subj]} {n}번 문제"
                    hits, _ = hubs[cond].retriever.search(query, k=args.top_k)
                    # 색인된 글은 NFKC 정규화됨(ⓐ～ⓔ → a~e) → 비교 전에 양쪽을 똑같이 정규화
                    snippet = squash(u["stem"])[:14]
                    hit = any(snippet and snippet in squash(h.chunk.text) for h in hits)
                    context = "\n\n".join(f"[{i}] {h.chunk.label}\n{h.chunk.text}" for i, h in enumerate(hits, 1))
                    user = f"[참고 문서]\n{context}\n\n[질문]\n{LABEL[subj]} {n}번 문제를 풀어 줘."
                elif cond == "ocr":
                    user = f"[수학 {n}번 (이미지를 글로 옮긴 것)]\n{ocr_cached(ollama, args.ocr_model, args.data, u)}"
                else:  # vision
                    images = [(args.data / u["image"]).read_bytes()]
                    user = f"이 이미지는 2026학년도 수능 수학 {n}번 문항이다. 문제를 풀어라."
                t = time.perf_counter()
                try:
                    text, stats = ollama.chat(model, [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}], args.think, images, max_tokens)
                except httpx.HTTPError as e:
                    text, stats = f"(오류: {e})", {}
                sec = time.perf_counter() - t
                pred = parse_answer(text)
                row = {
                    "model": model, "condition": cond, "think": args.think, "subject": subj, "number": n,
                    "pred": pred, "answer": key["answer"], "correct": pred == key["answer"], "points": key["points"],
                    "retrieval_hit": hit, "sec": round(sec, 2), "eval_tokens": stats.get("eval_count"), "output": text[-600:],
                }
                rows.append(row)
                out.write(json.dumps(row, ensure_ascii=False) + "\n")
                out.flush()
                mark = "O" if row["correct"] else "X"
                extra = "" if hit is None else (" 검색O" if hit else " 검색X")
                print(f"{model:16s} {cond:15s} {LABEL[subj]} {n:2d}번 {mark} 예측={pred} 정답={key['answer']}{extra} {sec:5.1f}s", flush=True)

    # 요약
    print("\n=== 요약 ===")
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        groups.setdefault((r["model"], r["condition"], r["think"], r["subject"]), []).append(r)
    for (model, cond, think, subj), rs in sorted(groups.items()):
        acc = sum(r["correct"] for r in rs) / len(rs)
        score = sum(r["points"] for r in rs if r["correct"])
        total = sum(r["points"] for r in rs)
        hits = [r["retrieval_hit"] for r in rs if r["retrieval_hit"] is not None]
        hit_s = f" · 검색 적중 {sum(hits) / len(hits):.0%}" if hits else ""
        print(
            f"{model:16s} {cond:15s} think={think:5s} {LABEL[subj]}: 정답률 {acc:.0%} ({sum(r['correct'] for r in rs)}/{len(rs)}) · "
            f"점수 {score}/{total} · 중앙값 {statistics.median(r['sec'] for r in rs):.1f}s{hit_s}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
