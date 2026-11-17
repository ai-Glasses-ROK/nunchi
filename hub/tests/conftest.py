import os
from pathlib import Path

import pytest

from nunchi.config import Settings
from nunchi.llm import EchoChat
from nunchi.pipeline import Hub
from nunchi.rag.embedder import HashEmbedder
from nunchi.rag.store import Store
from nunchi.stt import FixedTranscriber
from nunchi.tts import SilentSpeech, Speaker

ROOT = Path(__file__).resolve().parents[2]
KNOWLEDGE = ROOT / "examples" / "knowledge"


@pytest.fixture()
def settings(tmp_path) -> Settings:
    # .env를 읽지 않도록 dotenv=None, 외부 서버 주소는 쓰이지 않음
    for k in list(os.environ):
        if k.startswith("NUNCHI_"):
            os.environ.pop(k)
    return Settings.from_env(dotenv=None, data_dir=str(tmp_path / "data"))


@pytest.fixture()
def fake_hub(settings) -> Hub:
    """외부 서버(Ollama) 없이 동작하는 허브: 가짜 LLM/임베딩/STT/TTS."""
    hub = Hub(
        settings,
        store=Store(settings.index_path),
        chat=EchoChat(),
        embedder=HashEmbedder(),
        stt=FixedTranscriber("캠핑장 체크인은 몇 시부터야?"),
        speaker=Speaker(SilentSpeech()),
        probe_ollama=False,
    )
    hub.ingest_paths([KNOWLEDGE], log_fn=lambda *_: None)
    return hub
