"""실제 Ollama가 켜져 있을 때만 도는 통합 테스트.

    NUNCHI_LLM_MODEL=gemma4:e4b pytest -m integration
"""

import os

import pytest

from nunchi import ollama_util
from nunchi.config import Settings
from nunchi.pipeline import Hub
from tests.conftest import KNOWLEDGE

URL = os.environ.get("NUNCHI_OLLAMA_URL", "http://127.0.0.1:11434")
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def real_hub(tmp_path_factory):
    models = ollama_util.list_models(URL)
    if models is None:
        pytest.skip("Ollama가 꺼져 있어요")
    s = Settings.from_env(dotenv=None, data_dir=str(tmp_path_factory.mktemp("data")), tts_backend="none")
    hub = Hub(s)
    if hub.models["llm"] not in models:
        pytest.skip(f"LLM 모델 없음: {hub.models['llm']}")
    hub.ingest_paths([KNOWLEDGE], log_fn=lambda *_: None)
    return hub


def test_real_rag_answer(real_hub):
    r = real_hub.ask("발전기에 E2 코드가 떴는데 뭐야?")
    assert r["ok"], r
    assert "과열" in r["answer"]
    assert "고장 코드" in r["sources"][0]["heading"]
