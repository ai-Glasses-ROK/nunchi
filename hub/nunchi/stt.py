"""음성 인식(STT, Speech-to-Text).

기본값은 Ollama의 OpenAI 호환 엔드포인트 /v1/audio/transcriptions 입니다.
Gemma 4(e2b/e4b)처럼 '오디오 입력'을 지원하는 모델이면 Whisper를 따로 설치하지 않아도 됩니다.
whisper.cpp의 server(/inference)도 연결할 수 있습니다.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from nunchi import audio


class STTError(RuntimeError):
    pass


class Transcriber(Protocol):
    name: str

    def transcribe(self, wav: bytes) -> str: ...


class OpenAITranscriber:
    """OpenAI 호환 /audio/transcriptions (Ollama, speaches, LocalAI 등 로컬 서버)."""

    def __init__(self, base_url: str, model: str, language: str = "ko", prompt: str = "", api_key: str = "local", timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.language = language
        self.prompt = prompt
        self.name = f"openai:{model}"
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), headers={"Authorization": f"Bearer {api_key}"})

    def transcribe(self, wav: bytes) -> str:
        data = {"model": self.model, "response_format": "json", "temperature": "0"}
        if self.language:
            data["language"] = self.language
        if self.prompt:
            data["prompt"] = self.prompt
        try:
            r = self.client.post(
                f"{self.base_url}/audio/transcriptions", files={"file": ("audio.wav", wav, "audio/wav")}, data=data
            )
        except httpx.HTTPError as e:
            raise STTError(f"음성 인식 서버에 연결하지 못했어요({self.base_url}): {e}") from e
        if r.status_code >= 400:
            body = r.text[:300]
            if "not found" in body.lower() and "model" in body.lower():
                raise STTError(f"음성 인식 모델 '{self.model}'이 없어요. 먼저 실행: ollama pull {self.model}")
            if r.status_code == 404:
                raise STTError("이 서버는 /v1/audio/transcriptions 를 지원하지 않아요. Ollama를 최신 버전으로 올려 주세요.")
            raise STTError(f"음성 인식 실패({r.status_code}): {body}")
        return (r.json().get("text") or "").strip()


class WhisperCppTranscriber:
    """whisper.cpp `whisper-server` (기본 포트 8080)의 /inference. 16kHz WAV가 필요합니다."""

    def __init__(self, base_url: str, language: str = "ko", prompt: str = "", timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        if self.base_url.endswith("/v1"):
            self.base_url = self.base_url[:-3]
        self.language = language
        self.prompt = prompt
        self.name = "whispercpp"
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0))

    def transcribe(self, wav: bytes) -> str:
        x, sr = audio.read_wav(wav)
        if sr != 16000:
            wav = audio.write_wav(audio.resample(x, sr, 16000), 16000)
        data = {"response_format": "json", "temperature": "0.0"}
        if self.language:
            data["language"] = self.language
        if self.prompt:
            data["prompt"] = self.prompt
        try:
            r = self.client.post(f"{self.base_url}/inference", files={"file": ("audio.wav", wav, "audio/wav")}, data=data)
        except httpx.HTTPError as e:
            raise STTError(f"whisper.cpp 서버에 연결하지 못했어요({self.base_url}): {e}") from e
        if r.status_code >= 400:
            raise STTError(f"음성 인식 실패({r.status_code}): {r.text[:300]}")
        return (r.json().get("text") or "").strip()


class FixedTranscriber:
    """테스트용: 항상 정해진 문장을 돌려줍니다."""

    def __init__(self, text: str = "테스트 질문입니다") -> None:
        self.text = text
        self.name = "fixed"

    def transcribe(self, wav: bytes) -> str:
        audio.read_wav(wav)  # 형식 검증만
        return self.text
