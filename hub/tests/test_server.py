import base64
import json

import numpy as np
import pytest
from fastapi.testclient import TestClient

from nunchi import audio
from nunchi.server import create_app


@pytest.fixture()
def client(fake_hub):
    return TestClient(create_app(fake_hub))


def speech_like_wav(sr: int = 8000) -> bytes:
    t = np.arange(int(sr * 1.0)) / sr
    x = 0.3 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 3 * t) > 0)
    return audio.write_wav(np.concatenate([np.zeros(sr // 2), x, np.zeros(sr // 2)]).astype(np.float32), sr)


def parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        events.append((lines["event"], json.loads(lines["data"])))
    return events


def test_health(client):
    r = client.get("/v1/health")
    assert r.status_code == 200
    body = r.json()
    assert body["index"]["chunks"] > 0 and body["tts"] == "silent"


def test_web_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_ask_json(client):
    r = client.post("/v1/ask", json={"question": "매너 타임은 언제야?"})
    body = r.json()
    assert body["ok"] and "매너 타임은 언제야?" in body["answer"]
    assert body["sources"] and body["sources"][0]["n"] == 1
    assert "source" in body["sources"][0]
    assert {"retrieval_ms", "first_token_ms", "total_ms"} <= set(body["timings"])


def test_ask_sse_event_order(client):
    r = client.post("/v1/ask", json={"question": "발전기 E2 코드", "stream": True, "speak": True})
    assert r.headers["content-type"].startswith("text/event-stream")
    events = parse_sse(r.text)
    types = [t for t, _ in events]
    assert types[0] == "retrieval" and types[-1] == "done"
    assert "delta" in types and "sentence" in types and "audio" in types
    assert types.index("sentence") < types.index("done")
    first_audio = next(d for t, d in events if t == "audio")
    assert audio.is_wav(base64.b64decode(first_audio["wav_b64"]))
    assert "audio_wav_b64" not in events[-1][1]  # SSE에서는 전체 음성을 중복 전송하지 않음


def test_ask_json_with_speak_merges_audio(client):
    body = client.post("/v1/ask", json={"question": "발전기 E2 코드", "speak": True}).json()
    assert audio.is_wav(base64.b64decode(body["audio_wav_b64"]))


def test_voice_endpoint(client):
    r = client.post("/v1/voice", files={"file": ("q.wav", speech_like_wav(), "audio/wav")}, data={"session_id": "s1"})
    body = r.json()
    assert body["transcript"] == "캠핑장 체크인은 몇 시부터야?"
    assert body["ok"] and body["timings"]["stt_ms"] >= 0


def test_voice_silence_returns_not_heard(client, fake_hub):
    from nunchi.stt import FixedTranscriber

    fake_hub.stt = FixedTranscriber("")
    r = client.post("/v1/voice", files={"file": ("q.wav", speech_like_wav(), "audio/wav")})
    body = r.json()
    assert body["ok"] is False and "다시" in body["speech"]


def test_vision_endpoint(client):
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    r = client.post("/v1/vision", files={"image": ("p.jpg", jpeg, "image/jpeg")}, data={"question": "이게 뭐야?", "use_rag": "false"})
    body = r.json()
    assert body["ok"] and "사진 1장" in body["answer"] and body["sources"] == []


def test_vision_caption_feeds_retrieval(client):
    jpeg = b"\xff\xd8\xff\xe0" + b"0" * 100
    r = client.post("/v1/vision", files={"image": ("p.jpg", jpeg, "image/jpeg")}, data={"question": "이게 뭐야?", "stream": "true"})
    events = parse_sse(r.text)
    types = [t for t, _ in events]
    assert types[:2] == ["caption", "retrieval"]
    caption = events[0][1]["text"]
    assert caption and caption in events[1][1]["query"]


def test_tts_endpoint(client):
    r = client.post("/v1/tts", json={"text": "안녕하세요. 반가워요."})
    assert r.status_code == 200 and r.headers["content-type"] == "audio/wav"


def test_session_memory_follow_up(client, fake_hub):
    client.post("/v1/ask", json={"question": "체크인은 몇 시야?", "session_id": "abc"})
    q = fake_hub.retrieval_query("그럼 주말엔?", fake_hub.sessions.get("abc"))
    assert q.startswith("체크인은 몇 시야?")


def test_kb_upload_and_export(client):
    r = client.post("/v1/kb/upload", files=[("files", ("메모.md", "# 메모\n\n와이파이 이름은 솔바람게스트입니다.".encode(), "text/markdown"))])
    assert r.json()["results"][0]["ok"]
    r = client.post("/v1/ask", json={"question": "와이파이 이름이 뭐야?"})
    assert any("메모" in s["label"] for s in r.json()["sources"])
    exp = client.get("/v1/kb/export").json()
    assert exp["format"] == "nunchi-kb" and exp["tokenizer"] == "char-bigram-v1"
    assert all("/" not in c["source"] for c in exp["chunks"])  # 전체 경로 노출 안 함


def test_token_auth(fake_hub):
    fake_hub.settings.token = "s3cret"
    c = TestClient(create_app(fake_hub))
    assert c.get("/v1/health").status_code == 401
    assert c.get("/v1/health", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert c.get("/v1/health", headers={"X-Hub-Token": "s3cret"}).status_code == 200
    assert c.get("/").status_code == 200  # 웹 페이지 자체는 열림 (API만 보호)
