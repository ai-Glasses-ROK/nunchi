"""드론(픽스호크) 실시간 상태를 RAG에 섞는 기능: 상태→검색(live_hint), 프롬프트의 [실시간 기체 상태](live_context)."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nunchi.pipeline import is_drone_question
from nunchi.rag.prompt import DRONE_VISION_HINT, LIVE_HINT, VISION_HINT, build_messages
from nunchi.server import create_app
from nunchi.stt import FixedTranscriber

DRONE_DOCS = Path(__file__).resolve().parents[2] / "examples" / "drone"
LIVE = "기체: ArduPilot 쿼드콥터 · 모드 STABILIZE · 시동 꺼짐\n최근 기체 메시지(새것부터):\n- 12:00:01 [경고] PreArm: Compass not calibrated"
HINT = "PreArm: Compass not calibrated"


def sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


@pytest.fixture()
def drone_hub(fake_hub):
    fake_hub.ingest_paths([DRONE_DOCS], log_fn=lambda *_: None)
    # 순위 검증은 폰과 같은 2-gram BM25로 (테스트용 가짜 임베딩은 의미가 없어서 RRF 순위를 흔듦)
    fake_hub.retriever.mode = "bm25"
    return fake_hub


@pytest.fixture()
def client(drone_hub):
    return TestClient(create_app(drone_hub))


def test_live_context_goes_to_prompt_and_hint_to_query(client):
    r = client.post("/v1/ask", json={"question": "왜 시동이 안 걸려?", "stream": True, "live_context": LIVE, "live_hint": HINT})
    events = sse(r.text)
    retrieval = next(d for t, d in events if t == "retrieval")
    assert HINT in retrieval["query"]
    # 상태 키워드 덕분에 '이게 뭐야'급 질문도 나침반 보정 절이 1위
    assert "Compass not calibrated" in retrieval["sources"][0]["label"]
    assert events[-1][0] == "done" and events[-1][1]["ok"]


def test_hint_finds_the_fix_that_the_bare_question_misses(drone_hub):
    bare, _ = drone_hub.retriever.search("왜 시동이 안 걸려?", k=1)
    fused, _ = drone_hub.retriever.search(f"왜 시동이 안 걸려? {HINT}", k=1)
    assert "Compass not calibrated" in fused[0].chunk.label
    assert "Compass not calibrated" not in bare[0].chunk.label


def test_build_messages_puts_live_block_first():
    msgs = build_messages("배터리 몇 프로야?", [], live=LIVE)
    assert LIVE_HINT in msgs[0]["content"]
    assert msgs[-1]["content"].startswith("[실시간 기체 상태]\n" + LIVE)
    assert msgs[-1]["content"].rstrip().endswith("배터리 몇 프로야?")
    # 상태가 없으면 예전과 똑같은 프롬프트
    plain = build_messages("배터리 몇 프로야?", [])
    assert LIVE_HINT not in plain[0]["content"] and plain[-1]["content"].startswith("[참고 문서]")


def test_drone_camera_photo_uses_drone_hint():
    drone = build_messages("뭐가 보여?", [], vision=True, image_source="drone")[0]["content"]
    glasses = build_messages("뭐가 보여?", [], vision=True)[0]["content"]
    assert DRONE_VISION_HINT in drone and VISION_HINT not in drone
    assert VISION_HINT in glasses


def test_voice_adds_hint_only_for_drone_questions(client, drone_hub, tmp_path):
    from tests.test_server import speech_like_wav

    def voice_query(transcript: str) -> str:
        drone_hub.stt = FixedTranscriber(transcript)
        r = client.post(
            "/v1/voice",
            files={"file": ("q.wav", speech_like_wav(), "audio/wav")},
            data={"stream": "true", "live_context": LIVE, "live_hint": HINT},
        )
        return next(d for t, d in sse(r.text) if t == "retrieval")["query"]

    assert HINT not in voice_query("캠핑장 체크인은 몇 시부터야?")
    assert HINT in voice_query("시동이 왜 안 걸려?")


def test_vision_endpoint_accepts_drone_source_and_live(client):
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    r = client.post(
        "/v1/vision",
        files={"image": ("p.jpg", jpeg, "image/jpeg")},
        data={"question": "뭐가 보여?", "image_source": "drone", "live_context": LIVE, "live_hint": HINT, "stream": "true"},
    )
    events = sse(r.text)
    assert [t for t, _ in events][:2] == ["caption", "retrieval"]
    assert HINT in events[1][1]["query"]


def test_is_drone_question():
    assert is_drone_question("배터리 몇 프로 남았어?")
    assert is_drone_question("PreArm 메시지가 떠")
    assert is_drone_question("이거 뭐야?")  # 짧고 모호한 질문
    assert not is_drone_question("캠핑장 체크인은 몇 시부터야?")
