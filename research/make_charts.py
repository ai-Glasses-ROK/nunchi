"""실험 결과 → 문서용 SVG 차트 (외부 라이브러리 없음, 라이트/다크 모드 대응).

  python research/csat/summarize.py results.jsonl --json > summary.json
  python research/make_charts.py summary.json --out docs/images

만드는 것
  csat-rag.svg   국어·영어: 검색 적중률과 정답률 (조건별, gemma4:e4b)
  csat-math.svg  수학: 로컬 모델(이 연구) vs 클라우드(공개 결과)

색: 범주 1·2번(파랑·주황)은 색각 이상 시뮬레이션 검증을 통과한 조합(라이트 ΔE 24.7, 다크도 통과).
정적 이미지라 마우스를 올려 값을 볼 수 없으므로, 문서에 같은 값의 표를 함께 둡니다.
"""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path

FONT = "-apple-system, BlinkMacSystemFont, 'Apple SD Gothic Neo', 'Malgun Gothic', 'Noto Sans KR', 'Segoe UI', sans-serif"

STYLE = """
  .surface { fill: #fcfcfb; }
  .ink { fill: #0b0b0b; } .ink2 { fill: #52514e; } .muted { fill: #898781; }
  .grid { stroke: #e1e0d9; stroke-width: 1; } .base { stroke: #c3c2b7; stroke-width: 1; }
  .s1 { fill: #2a78d6; } .s2 { fill: #eb6834; } .ref { fill: #b9b8b0; }
  text { font-family: %s; }
  @media (prefers-color-scheme: dark) {
    .surface { fill: #1a1a19; }
    .ink { fill: #f0efec; } .ink2 { fill: #c3c2b7; } .muted { fill: #898781; }
    .grid { stroke: #2c2c2a; } .base { stroke: #383835; }
    .s1 { fill: #3987e5; } .s2 { fill: #d95926; } .ref { fill: #5a5955; }
  }
""" % FONT

# 공개 결과: Pyeon et al., "Evaluating Large Language Models on the 2026 Korean CSAT Mathematics Exam" (arXiv:2511.18649, Table 6)
# 46문항(공통 22 + 선택 3과목 전부), 한국어 지시문, 100점 환산
PAPER = [
    ("GPT-5 Codex · 이미지", 100.0),
    ("GPT-5 · 이미지", 97.8),
    ("gpt-oss-20B · 텍스트(사람이 옮김)", 95.7),
    ("Gemini 2.5 Pro · 이미지", 93.5),
    ("GPT-5-nano · 이미지", 67.4),
]


def bar(x: float, y: float, w: float, h: float, cls: str, r: float = 4) -> str:
    """막대: 기준선 쪽은 직각, 값 끝만 4px 둥글게."""
    if w <= 0:
        return ""
    r = min(r, w, h / 2)
    return (
        f'<path class="{cls}" d="M{x:.1f},{y:.1f} h{w - r:.1f} a{r},{r} 0 0 1 {r},{r} v{h - 2 * r:.1f} '
        f'a{r},{r} 0 0 1 -{r},{r} h-{w - r:.1f} z"/>'
    )


def svg(width: int, height: int, title: str, desc: str, body: list[str]) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" '
        f'role="img" aria-labelledby="t d">\n<title id="t">{escape(title)}</title>\n<desc id="d">{escape(desc)}</desc>\n'
        f"<style>{STYLE}</style>\n"
        f'<rect class="surface" x="0" y="0" width="{width}" height="{height}" rx="12"/>\n' + "\n".join(body) + "\n</svg>\n"
    )


def axis(x0: float, y0: float, y1: float, width: float, ticks=(0, 25, 50, 75, 100)) -> list[str]:
    out = []
    for t in ticks:
        x = x0 + width * t / 100
        out.append(f'<line class="{"base" if t == 0 else "grid"}" x1="{x:.1f}" y1="{y0}" x2="{x:.1f}" y2="{y1}"/>')
        out.append(f'<text class="muted" x="{x:.1f}" y="{y1 + 16}" font-size="11" text-anchor="middle">{t}%</text>')
    return out


def pick(groups: list[dict], model: str, cond: str, subj: str, think: str | None = None) -> dict | None:
    for g in groups:
        if g["model"] == model and g["condition"] == cond and g["subject"] == subj and (think is None or g["think"] == think):
            return g
    return None


def chart_rag(groups: list[dict], model: str = "gemma4:e4b") -> str:
    rows = [
        ("원문 RAG", "rag-naive"),
        ("문항 번호를 제목으로", "rag-structured"),
        ("+ 번호 조회 (Nunchi)", "rag-lookup"),
        ("문항 직접 제공 (상한)", "oracle"),
    ]
    subjects = [("kor", "국어", "s1"), ("eng", "영어", "s2")]
    W, H = 960, 338
    label_w, gap, panel_w = 178, 56, 330
    top, row_h, bh = 112, 44, 12
    px = [24 + label_w, 24 + label_w + panel_w + gap]
    body = [
        '<text class="ink" x="24" y="38" font-size="18" font-weight="700">수능 국어·영어: 번호로 묻는 RAG의 검색 적중과 정답률</text>',
        f'<text class="ink2" x="24" y="60" font-size="12.5">{escape(model)} · 질문 "국어 5번 문제 풀어 줘" · 2026학년도 홀수형 국어 34문항, 영어 27문항 · temperature 0</text>',
    ]
    # 범례
    lx = W - 170
    for i, (_, name, cls) in enumerate(subjects):
        body.append(f'<rect class="{cls}" x="{lx + i * 74}" y="29" width="12" height="12" rx="3"/>')
        body.append(f'<text class="ink2" x="{lx + i * 74 + 18}" y="39" font-size="12.5">{name}</text>')
    for p, (head, key) in enumerate([("검색 적중률 (정답 문항이 검색 결과에 있었나)", "retrieval_hit"), ("정답률", "accuracy")]):
        body.append(f'<text class="ink" x="{px[p]}" y="{top - 22}" font-size="13.5" font-weight="600">{head}</text>')
        body += axis(px[p], top - 8, top + row_h * len(rows) - 6, panel_w)
    for r, (label, cond) in enumerate(rows):
        y = top + r * row_h
        body.append(f'<text class="ink" x="24" y="{y + 18}" font-size="13">{escape(label)}</text>')
        for p, key in enumerate(["retrieval_hit", "accuracy"]):
            for s, (subj, _, cls) in enumerate(subjects):
                g = pick(groups, model, cond, subj)
                by = y + 2 + s * (bh + 2)
                if not g or g.get(key) is None:
                    if s == 0:
                        body.append(f'<text class="muted" x="{px[p] + 6}" y="{y + 18}" font-size="12">해당 없음 (검색 안 함)</text>')
                    continue
                v = g[key] * 100
                body.append(bar(px[p], by, panel_w * v / 100, bh, cls))
                if cond == "rag-lookup" or (cond == "oracle" and key == "accuracy"):  # 이야기의 끝점만 숫자로
                    body.append(f'<text class="ink2" x="{px[p] + panel_w * v / 100 + 6:.1f}" y="{by + 10}" font-size="11.5">{v:.0f}%</text>')
    body.append(
        f'<text class="muted" x="24" y="{H - 16}" font-size="11.5">검색 적중: 검색 결과 상위 4개 안에 그 문항의 발문이 있었는지. '
        f"번호 조회 = 질문의 “N번”과 같은 번호의 제목을 가진 절을 통째로 가져옴 (hub/nunchi/rag/retriever.py)</text>"
    )
    return svg(W, H, "수능 국어·영어 RAG 조건별 검색 적중률과 정답률", "조건별 검색 적중률과 정답률 막대그래프. 값은 문서의 표에 있습니다.", body)


def chart_math(groups: list[dict]) -> str:
    ours = []
    for label, model, cond, think in [
        ("gpt-oss:20b · 이미지→OCR→풀이", "gpt-oss:20b", "ocr", None),
        ("gemma4:e4b · 이미지→OCR→풀이", "gemma4:e4b", "ocr", None),
        ("gemma4:e4b · 이미지 · 사고 켬", "gemma4:e4b", "vision", "on"),
        ("gemma4:e4b · 이미지", "gemma4:e4b", "vision", "off"),
        ("gemma4:e2b · 이미지", "gemma4:e2b", "vision", "off"),
    ]:
        g = pick(groups, model, cond, "math", think)
        if g:
            ours.append((label, g["accuracy"] * 100, f'{g["correct"]}/{g["n"]}'))
    W, row_h, bh = 960, 30, 14
    label_w, plot_w = 260, 560
    x0 = 24 + label_w
    sections = [("공개 결과 · 클라우드·대형 (Pyeon et al., 2025 · 46문항)", [(l, v, "") for l, v in PAPER], "ref"),
                ("이 연구 · 로컬 노트북 하나 (공통 22문항)", ours, "s1")]
    n_rows = sum(len(rows) for _, rows, _ in sections)
    top = 96
    H = top + n_rows * row_h + len(sections) * 34 + 58
    body = [
        '<text class="ink" x="24" y="38" font-size="18" font-weight="700">수능 수학: 이미지로 풀 때 로컬 소형 모델과 클라우드의 거리</text>',
        '<text class="ink2" x="24" y="60" font-size="12.5">2026학년도 수학 · 정답률(%) · 문항 범위와 입력 방식이 달라 참고용 비교입니다 (아래 주석)</text>',
    ]
    y = top
    marks: list[str] = []
    for head, rows, cls in sections:
        marks.append(f'<text class="ink" x="24" y="{y + 14}" font-size="13" font-weight="600">{escape(head)}</text>')
        y += 26
        for label, v, extra in rows:
            marks.append(f'<text class="ink" x="24" y="{y + 12}" font-size="12.5">{escape(label)}</text>')
            marks.append(bar(x0, y + 1, plot_w * v / 100, bh, cls))
            tip = f"{v:.1f}" if not extra else f"{v:.0f}% ({extra})"
            marks.append(f'<text class="ink2" x="{x0 + plot_w * v / 100 + 6:.1f}" y="{y + 12}" font-size="11.5">{tip}</text>')
            y += row_h
        y += 8
    body += axis(x0, top + 18, y - 4, plot_w) + marks  # 눈금선을 먼저 그려 막대 아래에 깔리게
    body.append(
        f'<text class="muted" x="24" y="{H - 30}" font-size="11.5">공개 결과: arXiv:2511.18649 표 6, 한국어 지시문, 공통+선택 3과목 46문항, 100점 환산. '
        f"이 연구: 공통 1~22번, 정답 문항 수 비율.</text>"
    )
    body.append(
        f'<text class="muted" x="24" y="{H - 14}" font-size="11.5">OCR: gemma4:e4b가 문항 이미지를 글(LaTeX)로 옮기고 풀이 모델이 그 글만 봄. '
        f"모든 로컬 실행은 같은 노트북(M5 Pro 24GB)의 Ollama.</text>"
    )
    return svg(W, H, "수능 수학 로컬 vs 클라우드 정답률", "로컬 모델과 공개된 클라우드 결과의 수학 정답률 막대그래프. 값은 막대 끝과 문서의 표에 있습니다.", body)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("summary", type=Path, help="summarize.py --json 출력")
    ap.add_argument("--out", type=Path, default=Path("docs/images"))
    args = ap.parse_args()
    groups = json.loads(args.summary.read_text(encoding="utf-8"))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "csat-rag.svg").write_text(chart_rag(groups), encoding="utf-8")
    (args.out / "csat-math.svg").write_text(chart_math(groups), encoding="utf-8")
    print(f"저장: {args.out}/csat-rag.svg, {args.out}/csat-math.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
