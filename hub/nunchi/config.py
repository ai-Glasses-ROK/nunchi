"""설정 관리.

모든 설정은 환경변수(접두사 NUNCHI_)로 바꿀 수 있고, 현재 폴더의 `.env` 파일도 읽습니다.
예) NUNCHI_LLM_MODEL=gemma4:e4b-mlx nunchi serve

설정 항목 설명은 hub/.env.example 에 한국어로 정리되어 있습니다.
"""

from __future__ import annotations

import dataclasses
import os
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any

ENV_PREFIX = "NUNCHI_"


def load_dotenv(path: str | os.PathLike[str] = ".env") -> dict[str, str]:
    """아주 단순한 .env 파서. 이미 설정된 환경변수는 덮어쓰지 않습니다."""
    loaded: dict[str, str] = {}
    p = Path(path)
    if not p.is_file():
        return loaded
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key.startswith("export "):
            key = key[len("export ") :].strip()
        value = value.strip()
        # 따옴표 제거, 따옴표 밖의 인라인 주석 제거
        # (빈 값 + 주석, 예: "NUNCHI_TOKEN=   # 설명" 은 빈 문자열이어야 함)
        if value.startswith("#"):
            value = ""
        elif len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif " #" in value or "\t#" in value:
            value = value.replace("\t#", " #").split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded


def _to_bool(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "y", "on", "예", "네"}


@dataclass
class Settings:
    # 저장 위치 -----------------------------------------------------------
    data_dir: str = "~/nunchi"

    # Ollama (LLM·임베딩·STT·TTS 공통 서버) --------------------------------
    ollama_url: str = "http://127.0.0.1:11434"
    keep_alive: str = "30m"  # 모델을 메모리에 붙잡아 두는 시간 (응답 지연 감소)

    # LLM ---------------------------------------------------------------
    llm_backend: str = "ollama"  # ollama | openai(OpenAI 호환: llama.cpp, LM Studio, mlx_lm 등)
    llm_url: str = ""  # openai 백엔드의 base URL (예: http://127.0.0.1:8080/v1)
    llm_api_key: str = "local"
    llm_model: str = "gemma4:e4b"
    vision_model: str = ""  # 비우면 llm_model 사용
    vision_caption: bool = True  # 사진 질문: 먼저 사진을 한 줄로 묘사해 검색어에 보탬 (예: "이게 뭐야?" 대응)
    temperature: float = 0.3
    num_ctx: int = 8192
    max_tokens: int = 384
    think: bool = False  # 추론(thinking) 모드. 음성 비서는 끄는 편이 훨씬 빠릅니다.

    # 임베딩 / 검색 --------------------------------------------------------
    embed_backend: str = "ollama"  # ollama | openai | none(BM25만 사용)
    embed_url: str = ""
    embed_model: str = "bge-m3"
    embed_query_prefix: str = ""  # 일부 모델(qwen3-embedding 등)은 질의 앞에 지시문을 붙이면 좋아집니다
    retrieval_mode: str = "hybrid"  # hybrid | bm25 | dense
    top_k: int = 4
    number_lookup: bool = True  # "5번 문제"·"3단계"처럼 번호로 물으면 같은 번호 제목의 절을 통째로
    max_section_chars: int = 6000
    lookup_exclusive: bool = True  # 번호 조회가 성공하면 그 절만 (다른 검색 결과는 빼기)
    chunk_chars: int = 600
    chunk_overlap: int = 120
    strict_rag: bool = False  # True면 문서에 없는 질문엔 답하지 않음

    # 음성 인식(STT) -------------------------------------------------------
    stt_backend: str = "openai"  # openai(/v1/audio/transcriptions) | whispercpp | none
    stt_url: str = ""  # 비우면 {ollama_url}/v1
    stt_model: str = ""  # 비우면 LLM 모델을 같이 사용 (Gemma 4는 오디오 입력 지원 → 모델 교체 대기 없음)
    stt_language: str = "ko"
    stt_prompt: str = ""  # 고유명사 힌트 (예: "레이밴 메타, 공기계, 올라마")
    audio_preprocess: bool = True  # 무음 자르기·볼륨 정규화 (8kHz 안경 마이크에 특히 유용)

    # 음성 합성(TTS) -------------------------------------------------------
    tts_backend: str = "auto"  # auto | openai | say(macOS) | none(폰이 직접 읽기)
    tts_url: str = ""  # 비우면 {ollama_url}/v1
    tts_model: str = "lfm3-audio:2b"
    tts_voice: str = "female-korean"
    say_voice: str = "Yuna"

    # 대화 ----------------------------------------------------------------
    answer_style: str = "voice"  # voice(짧게, 말하듯) | text(조금 더 자세히)
    history_turns: int = 3

    # 서버 ----------------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8765
    token: str = ""  # 비어 있지 않으면 모든 API에 토큰 필요
    allow_upload: bool = True  # 웹/앱에서 문서 업로드 허용

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls, dotenv: str | None = ".env", **overrides: Any) -> "Settings":
        if dotenv:
            load_dotenv(dotenv)
        values: dict[str, Any] = {}
        for f in fields(cls):
            env_key = ENV_PREFIX + f.name.upper()
            if env_key in os.environ:
                values[f.name] = _coerce(f.type, os.environ[env_key])
        values.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**values)

    # 파생 값 ------------------------------------------------------------
    @property
    def data_path(self) -> Path:
        p = Path(os.path.expanduser(self.data_dir))
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def index_path(self) -> Path:
        return self.data_path / "index.sqlite3"

    @property
    def ollama_v1(self) -> str:
        return self.ollama_url.rstrip("/") + "/v1"

    @property
    def llm_base(self) -> str:
        return (self.llm_url or self.ollama_v1).rstrip("/")

    @property
    def embed_base(self) -> str:
        return (self.embed_url or self.ollama_v1).rstrip("/")

    @property
    def stt_base(self) -> str:
        return (self.stt_url or self.ollama_v1).rstrip("/")

    @property
    def tts_base(self) -> str:
        return (self.tts_url or self.ollama_v1).rstrip("/")

    @property
    def effective_vision_model(self) -> str:
        return self.vision_model or self.llm_model

    @property
    def effective_stt_model(self) -> str:
        return self.stt_model or self.llm_model

    def public_dict(self) -> dict[str, Any]:
        """토큰처럼 민감한 값을 가린 설정 사본 (헬스체크·doctor 출력용)."""
        d = dataclasses.asdict(self)
        if d.get("token"):
            d["token"] = "***"
        if d.get("llm_api_key") and d["llm_api_key"] != "local":
            d["llm_api_key"] = "***"
        return d


def _coerce(type_hint: Any, raw: str) -> Any:
    t = type_hint if isinstance(type_hint, str) else getattr(type_hint, "__name__", str(type_hint))
    if t == "bool":
        return _to_bool(raw)
    if t == "int":
        return int(raw)
    if t == "float":
        return float(raw)
    return raw
