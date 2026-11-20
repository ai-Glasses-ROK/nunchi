#!/usr/bin/env python3
"""문서마다 첫 `##` 제목 앞에 '## 목차'를 만들거나 새로 고칩니다. 링크는 GitHub의 제목 id 규칙과 같게 만듭니다.

  python3 scripts/make_toc.py          # 모든 문서의 목차를 새로 만듦 (문서를 고친 뒤 다시 실행)
  python3 scripts/make_toc.py --check  # 목차가 없거나 낡은 문서가 있으면 실패

대상: 레포의 모든 .md. 단, examples/knowledge·examples/drone은 앱과 허브가 검색하는 지식베이스라서 뺍니다
(목차 조각이 모든 절 제목을 담고 있어 검색 결과를 흐립니다). 목차에는 `##`·`###` 제목을 넣습니다.
표준 라이브러리만 씁니다.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOC_TITLE = "목차"
SKIP_PREFIXES = ("examples/knowledge/", "examples/drone/")
FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


def slug(text: str) -> str:
    """GitHub 제목 id: 소문자로 바꾸고 글자·결합 표시·숫자·밑줄·하이픈·공백만 남긴 뒤 공백을 하이픈으로."""
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    kept = [ch for ch in text.strip().lower()
            if ch in "- " or unicodedata.category(ch)[0] in "LM" or unicodedata.category(ch) in ("Nd", "Pc")]
    return "".join(kept).replace(" ", "-")


def headings(lines: list[str]) -> list[tuple[int, int, str, str]]:
    """(줄 번호, 단계, 제목 글, 링크 id). 코드 블록 안은 건너뛰고, 같은 id는 GitHub처럼 -1, -2를 붙임."""
    found, seen, fence = [], {}, False
    for i, line in enumerate(lines):
        if FENCE.match(line):
            fence = not fence
            continue
        if fence:
            continue
        m = HEADING.match(line)
        if not m:
            continue
        text = m.group(2)
        base = slug(text)
        n = seen.get(base, 0)
        seen[base] = n + 1
        found.append((i, len(m.group(1)), text, base if n == 0 else f"{base}-{n}"))
    return found


def link_text(text: str) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)  # 링크 안에 링크를 둘 수 없음
    return text.replace("[", "\\[").replace("]", "\\]")


def with_toc(text: str) -> str | None:
    """목차를 넣은(또는 새로 고친) 본문. 넣을 제목이 없으면 None."""
    lines = text.split("\n")
    hs = headings(lines)
    old = next((h for h in hs if h[1] == 2 and h[2].strip() == TOC_TITLE), None)
    if old:
        end = next((h[0] for h in hs if h[0] > old[0]), len(lines))
        del lines[old[0]:end]
        hs = headings(lines)
    entries = [(level, txt, hid) for _, level, txt, hid in hs if level in (2, 3)]
    if not entries:
        return None
    top = min(level for level, _, _ in entries)
    block = [f"## {TOC_TITLE}", ""]
    block += ["  " * (level - top) + f"- [{link_text(txt)}](#{hid})" for level, txt, hid in entries]
    block.append("")
    at = next(h[0] for h in hs if h[1] == top)
    if at > 0 and lines[at - 1].strip():
        block.insert(0, "")
    lines[at:at] = block
    return "\n".join(lines)


def targets() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "--", "*.md"],
                         cwd=ROOT, capture_output=True, text=True, check=True).stdout
    names = sorted({n for n in out.splitlines() if n and not n.startswith(SKIP_PREFIXES)})
    return [ROOT / n for n in names if (ROOT / n).is_file()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="고치지 않고, 목차가 없거나 낡은 문서만 알려 줌")
    args = ap.parse_args()
    changed, missing = [], []
    for path in targets():
        text = path.read_text(encoding="utf-8")
        new = with_toc(text)
        rel = path.relative_to(ROOT)
        if new is None:
            missing.append(rel)
            continue
        if new != text:
            changed.append(rel)
            if not args.check:
                path.write_text(new, encoding="utf-8")
    for rel in missing:
        print(f"제목(## 또는 ###)이 없어 목차를 못 만듦: {rel}")
    if args.check:
        for rel in changed:
            print(f"목차가 없거나 낡음: {rel}")
        print(f"검사: 문서 {len(targets())}개 · 고칠 것 {len(changed)}개 · 제목 없음 {len(missing)}개")
        return 1 if changed or missing else 0
    print(f"목차: 문서 {len(targets())}개 중 {len(changed)}개 새로 고침" + (f" · 제목 없음 {len(missing)}개" if missing else ""))
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
