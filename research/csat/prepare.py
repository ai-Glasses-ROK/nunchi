"""수능 문제지 PDF → 실험용 문항 데이터 만들기.

평가원(suneung.re.kr)에서 직접 받은 '문제지' PDF를 넣으면:
  - 국어·영어: 문항 단위 텍스트(units.jsonl), 구조화 문서(<과목>_structured.md), 원문 텍스트(<과목>_raw.txt)
  - 수학: 문항이 있는 쪽의 단(column)을 잘라낸 이미지(math_q01.png …)
를 out 폴더에 만듭니다. (문제 원문은 저작권 때문에 레포에 넣지 않습니다 — 이 스크립트로 각자 만드세요)

사용 예 (macOS):
  python research/csat/prepare.py --kor 국어영역_문제지.pdf --math 수학영역_문제지.pdf --eng 영어영역_문제지.pdf --out data/csat2026

필요: pypdf (허브 의존성에 포함), 수학 이미지는 macOS의 swift(PDFKit) + sips.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

HERE = Path(__file__).resolve().parent
TILDE = r"[～~∼〜\-–]"
NOISE = [
    r"이 문제지에 관한 저작권은 한국교육과정평가원에 있습니다\.?",
    r"2026학년도 대학수학능력시험 문제지",
    r"\d{4}학년도 대학수학능력시험 문제지",
    r"^\s*(홀수형|짝수형)\s*$",
    r"^\s*제\s*\d\s*교시\s*$",
    r"^\s*\d{1,2}\s+\d{1,2}\s*$",  # 쪽 번호 '2 20'
    r"^\s*\d{1,2}\s*$",
    r"^\s*(국어|영어|수학)\s*영역\s*$",
    r"^\s*\*\s*확인 사항.*$",
]


def odd_pages(reader: PdfReader) -> list[tuple[int, str]]:
    """홀수형 쪽만 (PDF 하나에 홀수형·짝수형이 함께 들어 있음)."""
    out = []
    for i, page in enumerate(reader.pages):
        t = page.extract_text() or ""
        if "짝수형" in t[:400]:
            break
        out.append((i, t))
    return out


def clean(text: str) -> str:
    for pat in NOISE:
        text = re.sub(pat, "", text, flags=re.M)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def split_units(text: str, first: int, last: int) -> tuple[dict[int, str], list[int]]:
    """문항 번호(줄 맨 앞 'N. ')와 묶음 머리말('[1～3] 다음 글을 읽고…')로 문항 단위를 만듭니다.

    문항 단위 = (묶음 지문) + 문항 발문 + 선택지. 단독 문항은 발문 다음에 지문이 이어집니다(영어).
    """
    starts: dict[int, int] = {}
    cursor = 0
    missing = []
    for n in range(first, last + 1):
        m = re.compile(rf"(?m)^\s*{n}\.\s").search(text, cursor)
        if not m:
            missing.append(n)
            continue
        starts[n] = m.start()
        cursor = m.end()
    groups = [(m.start(), int(m.group(1)), int(m.group(2))) for m in re.finditer(rf"\[(\d+)\s*{TILDE}\s*(\d+)\]", text)]
    order = sorted(starts.items(), key=lambda kv: kv[1])
    units: dict[int, str] = {}
    for idx, (n, s) in enumerate(order):
        nxt = order[idx + 1][1] if idx + 1 < len(order) else len(text)
        # 다음 묶음 머리말이 먼저 나오면 거기서 끊음
        for gpos, _, _ in groups:
            if s < gpos < nxt:
                nxt = gpos
                break
        body = text[s:nxt].strip()
        passage = ""
        for gpos, a, b in reversed(groups):
            if gpos < s and a <= n <= b:
                first_q = min((p for q, p in starts.items() if a <= q <= b), default=s)
                passage = text[gpos:first_q].strip()
                break
        units[n] = (passage + "\n\n" + body).strip() if passage else body
    return units, missing


def fix_two_column_choices(body: str) -> str:
    """2단 표 선택지 복원. 왼쪽 단(①③⑤)·오른쪽 단(②④) 표를 pypdf가 '행 단위'로 읽어
    "① ③ ⑤ (A) (B) (C) － － － (C) (C) (B) … ② ④ (B) (C) …"처럼 뒤섞습니다(영어 순서 배열·요약문 빈칸).
    열 단위로 다시 읽어 "① (A) － (C) － (B)"로 되돌립니다. 행·열 개수가 맞지 않으면 손대지 않습니다."""
    m = re.search(r"①\s*③\s*⑤\s+(.+?)\s+②\s*④\s+(.+)$", body, re.S)
    if not m:
        return body
    odd, even = m.group(1).split(), m.group(2).split()
    if len(odd) % 3 and odd[-2:] == ["(A)", "(B)"]:
        odd = odd[:-2]  # 오른쪽 단의 머리글 '(A) (B)'
    rows = len(odd) // 3
    if not rows or len(odd) != rows * 3 or len(even) != rows * 2:
        return body
    cols = {1: odd[0::3], 3: odd[1::3], 5: odd[2::3], 2: even[0::2], 4: even[1::2]}
    choices = "\n".join(f"{'①②③④⑤'[k - 1]} {' '.join(cols[k])}" for k in range(1, 6))
    return body[: m.start()].rstrip() + "\n" + choices


def mark_given_sentence(body: str, n: int) -> str:
    """문장 삽입 문항: 상자 안의 '주어진 문장'이 본문 첫 문장과 구별 없이 이어져 나오므로 표시를 붙입니다."""
    m = re.match(rf"\s*{n}\.\s*(.+?[.?!])(?=\s)", body, re.S)
    if not m:
        return body
    return f"{n}. [주어진 문장] {' '.join(m.group(1).split())}\n\n[글]\n{body[m.end():].lstrip()}"


def fix_layout(text: str, first: int, last: int) -> tuple[str, list[int]]:
    """시험지 레이아웃 때문에 텍스트에서 사라진 구조를 되살립니다 (원문 텍스트 자체를 고쳐서 모든 조건에 똑같이 적용)."""
    units, _ = split_units(text, first, last)
    fixed_numbers = []
    for n, unit in units.items():
        m = re.search(rf"(?m)^\s*{n}\.\s", unit)
        if not m:
            continue
        body = unit[m.start():].strip()
        new = fix_two_column_choices(body)
        if "주어진 문장이 들어가기에" in unit:
            new = mark_given_sentence(new, n)
        if new != body and body in text:
            text = text.replace(body, new, 1)
            fixed_numbers.append(n)
    return text, fixed_numbers


def stem_of(unit: str, n: int) -> str:
    m = re.search(rf"(?m)^\s*{n}\.\s*(.+)$", unit)
    return m.group(1).strip() if m else ""


def question_boxes(reader: PdfReader, pages: list[int], first: int, last: int) -> dict[int, dict]:
    """수학: 문항 N의 위치(쪽, 왼쪽/오른쪽 단, 세로 범위). pypdf가 주는 글자 좌표로 계산합니다.

    반환: {N: {"page", "col", "top", "bottom"}}  top/bottom은 PDF 좌표(아래가 0)에서 '위에서부터의 거리' 비율(0~1)
    """
    boxes: dict[int, dict] = {}
    for pi in pages:
        page = reader.pages[pi]
        width, height = float(page.mediabox.width), float(page.mediabox.height)
        labels: list[tuple[int, float, float]] = []

        def visitor(text, cm, tm, font, size):  # noqa: ANN001
            t = text.strip()
            if re.fullmatch(r"\d{1,2}\.", t):
                x0, y0 = tm[4], tm[5]
                if cm:
                    x = cm[0] * x0 + cm[2] * y0 + cm[4]
                    y = cm[1] * x0 + cm[3] * y0 + cm[5]
                else:
                    x, y = x0, y0
                labels.append((int(t[:-1]), x, y))

        page.extract_text(visitor_text=visitor)
        for col in ("L", "R"):
            in_col = [(n, y) for n, x, y in labels if first <= n <= last and (x < width / 2) == (col == "L")]
            in_col.sort(key=lambda t: -t[1])  # 위 → 아래
            for i, (n, y) in enumerate(in_col):
                if n in boxes:
                    continue
                top = max(0.0, (height - y) / height - 0.012)
                bottom = (height - in_col[i + 1][1]) / height - 0.012 if i + 1 < len(in_col) else 0.93
                boxes[n] = {"page": pi, "col": col, "top": round(top, 4), "bottom": round(bottom, 4)}
    return boxes


def render_math(pdf: Path, boxes: dict[int, dict], out: Path, scale: float = 1.5) -> None:
    """문항 하나만 보이게 잘라 PNG로 저장. (주의: macOS sips는 --cropOffset의 X가 0이면 자르기를 무시하므로 1부터)"""
    if not shutil.which("swift") or not shutil.which("sips"):
        print("  ! 수학 이미지는 macOS(swift, sips)에서 만들 수 있어요. 리눅스는 pdftoppm + ImageMagick으로 대신하세요.")
        return
    pages = sorted({b["page"] for b in boxes.values()})
    tmp = out / "_pages"
    tmp.mkdir(parents=True, exist_ok=True)
    subprocess.run(["swift", str(HERE / "render_pages.swift"), str(pdf), str(tmp), str(scale), *[str(p) for p in pages]], check=True)
    for n, b in sorted(boxes.items()):
        src = tmp / f"page_{b['page']:03d}.png"
        info = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(src)], capture_output=True, text=True).stdout
        w = int(re.search(r"pixelWidth: (\d+)", info).group(1))
        h = int(re.search(r"pixelHeight: (\d+)", info).group(1))
        half = w // 2
        left = 1 if b["col"] == "L" else half
        top = max(1, int(h * b["top"]))
        height = max(200, int(h * (b["bottom"] - b["top"])))
        dst = out / f"math_q{n:02d}.png"
        subprocess.run(
            ["sips", "--cropToHeightWidth", str(height), str(half - 1), "--cropOffset", str(top), str(left), str(src), "--out", str(dst)],
            check=True, capture_output=True,
        )
    shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="수능 문제지 PDF → 실험 데이터")
    ap.add_argument("--kor", type=Path)
    ap.add_argument("--eng", type=Path)
    ap.add_argument("--math", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    units_out = []

    for subject, pdf, first, last, label in [("kor", args.kor, 1, 34, "국어"), ("eng", args.eng, 18, 45, "영어")]:
        if not pdf:
            continue
        pages = odd_pages(PdfReader(str(pdf)))
        text = clean("\n\n".join(t for _, t in pages))
        text, fixed = fix_layout(text, first, last)
        if fixed:
            print(f"{label}: 레이아웃 복원 {fixed} (2단 선택지 표·주어진 문장)")
        (args.out / f"{subject}_raw.txt").write_text(text, encoding="utf-8")
        units, missing = split_units(text, first, last)
        md = [f"# 2026학년도 수능 {label} (홀수형)\n"]
        for n in range(first, last + 1):
            if n not in units:
                continue
            u = units[n]
            body_len = len(re.sub(r"\s", "", u))
            needs_image = body_len < 220  # 지문이 그림으로만 있는 문항(텍스트 추출 불가)
            units_out.append({"subject": subject, "number": n, "stem": stem_of(u, n), "text": u, "needs_image": needs_image})
            md.append(f"## {label} {n}번 문항\n\n{u}\n")
        (args.out / f"{subject}_structured.md").write_text("\n".join(md), encoding="utf-8")
        img = sum(1 for x in units_out if x["subject"] == subject and x["needs_image"])
        print(f"{label}: 쪽 {len(pages)} · 문항 {len(units)}/{last - first + 1} (누락 {missing}) · 그림 필요 {img}")

    if args.math:
        reader = PdfReader(str(args.math))
        pages = [i for i, _ in odd_pages(reader)]
        boxes = question_boxes(reader, pages, 1, 22)
        print(f"수학: 공통 22문항 중 위치 확인 {len(boxes)}")
        render_math(args.math, boxes, args.out)
        for n in range(1, 23):
            if (args.out / f"math_q{n:02d}.png").exists():
                units_out.append({"subject": "math", "number": n, "image": f"math_q{n:02d}.png"})

    with (args.out / "units.jsonl").open("w", encoding="utf-8") as f:
        for u in units_out:
            f.write(json.dumps(u, ensure_ascii=False) + "\n")
    print(f"→ {args.out}/units.jsonl ({len(units_out)}개)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
