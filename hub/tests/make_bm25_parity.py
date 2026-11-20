"""폰 검색기(Android rag/LocalRag.kt)가 허브 검색기와 같은지 보는 기대값을 허브 코드로 다시 만듭니다.

결과: clients/android/app/src/test/resources/bm25_parity.json (LocalRagParityTest가 읽음)
예제 문서(examples/knowledge·examples/drone)를 고치면 다시 실행하세요:

  cd hub && .venv/bin/python tests/make_bm25_parity.py
"""

from __future__ import annotations

import json
from pathlib import Path

from nunchi.rag.bm25 import BM25, tokenize
from nunchi.rag.chunker import chunk_text
from nunchi.rag.loaders import load_path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "clients/android/app/src/test/resources/bm25_parity.json"
TOKENS_TEXT = "회의실 예약은 2시간 ①번 ＡＢＣ PreArm:Compass"
QUERIES = [
    "캠핑장 체크인은 몇 시부터야?",
    "토요일에 가면 몇 시에 들어갈 수 있어?",
    "매너 타임이 언제야?",
    "텐트 안에서 숯불 피워도 돼?",
    "전기장판이랑 전기난로 같이 써도 돼?",
    "멧돼지를 만나면 어떻게 해야 해?",
    "온수 샤워 몇 시까지 돼?",
    "발전기 빨간 경고등이 켜졌어",
    "발전기에 E2 코드가 떴는데 뭐야?",
    "발전기 시동 걸 때 초크는 언제 열어?",
    "발전기 엔진 오일은 얼마마다 갈아?",
    "발전기는 창문에서 얼마나 떨어뜨려야 해?",
    "공기계에서 재난문자 안 오게 하려면?",
    "앱이 허브에 연결이 안 돼",
    "헤이 메타라고 부르면 왜 대답을 안 해?",
    "캠핑장 와이파이 비밀번호 알려줘",
    "왜 시동이 안 걸려? PreArm: Compass not calibrated",
    "PreArm: Need 3D Fix 떴어",
    "배터리 부족 경고가 뜨면 어떻게 해?",
    "LED가 노랑으로 두 번씩 깜빡여",
    "텔레메트리 라디오 보드레이트는?",
    "드론 카메라 영상을 폰으로 보려면?",
    "Battery 1 is low",
    "안전 스위치는 어떻게 풀어?",
    "왜 시동이 안 걸려? PreArm: 3D Accel calibration needed PreArm: Compass not calibrated "
    "PreArm: RC Roll not configured PreArm: Throttle below Failsafe",
    "카메라랑 송신기만 연결하면 폰으로 볼 수 있어?",
    "아이폰으로 드론 영상 볼 수 있어?",
    "드론 영상이 흑백으로 나와",
]


def build() -> dict:
    # 앱과 같은 순서(파일 이름순)·같은 설정(600자, 겹침 120자)으로 조각내고 '제목 경로 + 본문'을 BM25에 넣음
    files = sorted([*ROOT.glob("examples/knowledge/*.md"), *ROOT.glob("examples/drone/*.md")], key=lambda p: p.name)
    labels: list[str] = []
    texts: list[str] = []
    for path in files:
        doc = load_path(path)
        for chunk in chunk_text(doc.text, 600, 120, skip_title=doc.title):
            labels.append(" > ".join(x for x in [doc.title, chunk.heading] if x))
            texts.append(chunk.embed_text(doc.title))
    bm25 = BM25(texts)
    cases = [{"q": q, "top": [labels[i] for i, _ in bm25.search(q, 3)]} for q in QUERIES]
    return {"chunk_count": len(texts), "tokens": tokenize(TOKENS_TEXT), "cases": cases}


if __name__ == "__main__":
    data = build()
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"{OUT.relative_to(ROOT)}: 조각 {data['chunk_count']}개, 질문 {len(data['cases'])}개")
