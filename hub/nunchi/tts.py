"""음성 합성(TTS, Text-to-Speech).

백엔드
- openai : OpenAI 호환 /v1/audio/speech. Ollama + lfm3-audio(한국어 voice: female-korean / male-korean)
- say    : macOS 내장 `say` 명령 (한국어 목소리 Yuna 등, 완전 오프라인, 설치 불필요)
- none   : 허브는 글자만 보내고 폰이 직접 읽음 (iOS AVSpeechSynthesizer / Android TextToSpeech)

실사용 팁: 폰에서 읽는 'none'이 지연이 가장 짧습니다. 허브 TTS는 웹 클라이언트나 녹음 저장용으로 좋아요.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Protocol

import httpx

from nunchi import audio
from nunchi.textutil import chunk_for_tts, to_speech_text


class TTSError(RuntimeError):
    pass


class SpeechBackend(Protocol):
    name: str

    def synthesize_chunk(self, text: str) -> bytes: ...


class OpenAISpeech:
    def __init__(self, base_url: str, model: str, voice: str = "", api_key: str = "local", timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.voice = voice
        self.name = f"openai:{model}" + (f"/{voice}" if voice else "")
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0), headers={"Authorization": f"Bearer {api_key}"})

    def synthesize_chunk(self, text: str) -> bytes:
        payload = {"model": self.model, "input": text, "response_format": "wav"}
        if self.voice:
            payload["voice"] = self.voice
        try:
            r = self.client.post(f"{self.base_url}/audio/speech", json=payload)
        except httpx.HTTPError as e:
            raise TTSError(f"TTS 서버에 연결하지 못했어요({self.base_url}): {e}") from e
        if r.status_code >= 400:
            raise TTSError(f"TTS 실패({r.status_code}): {r.text[:300]}")
        # 스트리밍 WAV는 헤더의 길이 값이 최대값일 수 있어, 읽어서 올바른 헤더로 다시 씁니다.
        x, sr = audio.read_wav(r.content)
        return audio.write_wav(x, sr)


class MacSay:
    def __init__(self, voice: str = "Yuna", sample_rate: int = 22050) -> None:
        if not shutil.which("say"):
            raise TTSError("macOS의 say 명령을 찾지 못했어요")
        self.voice = voice
        self.sample_rate = sample_rate
        self.name = f"say:{voice}"

    def synthesize_chunk(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory() as td:
            src, out = Path(td) / "in.txt", Path(td) / "out.wav"
            src.write_text(text, encoding="utf-8")  # 텍스트를 파일로 넘겨 '-'로 시작하는 문장도 안전하게
            cmd = ["say", "-v", self.voice, "--file-format=WAVE", f"--data-format=LEI16@{self.sample_rate}", "-o", str(out), "-f", str(src)]
            proc = subprocess.run(cmd, capture_output=True, check=False)
            if proc.returncode != 0 or not out.exists():
                raise TTSError(f"say 실패: {proc.stderr.decode('utf-8', 'replace')[:200]}")
            return out.read_bytes()


class SilentSpeech:
    """테스트용: 글자 수에 비례한 길이의 무음 WAV."""

    name = "silent"

    def synthesize_chunk(self, text: str) -> bytes:
        import numpy as np

        return audio.write_wav(np.zeros(int(16000 * 0.05 * max(1, len(text))), dtype=np.float32), 16000)


class Speaker:
    """문장 단위로 잘라 합성하고 이어 붙입니다."""

    def __init__(self, backend: SpeechBackend | None) -> None:
        self.backend = backend

    @property
    def name(self) -> str:
        return self.backend.name if self.backend else "none"

    @property
    def available(self) -> bool:
        return self.backend is not None

    def synthesize(self, text: str, max_chars: int = 80) -> bytes | None:
        if not self.backend:
            return None
        pieces = chunk_for_tts(to_speech_text(text), max_chars=max_chars)
        if not pieces:
            return None
        return audio.concat_wavs([self.backend.synthesize_chunk(p) for p in pieces])


def build_backend(
    kind: str,
    *,
    tts_base: str,
    tts_model: str,
    tts_voice: str,
    say_voice: str,
    installed_models: list[str] | None,
    model_has_speech: bool,
) -> SpeechBackend | None:
    kind = (kind or "auto").lower()
    if kind == "none":
        return None
    if kind == "openai":
        return OpenAISpeech(tts_base, tts_model, tts_voice)
    if kind == "say":
        return MacSay(say_voice)
    # auto: Ollama에 음성 모델이 있으면 그것, 아니면 macOS say, 둘 다 없으면 폰에 맡김
    if installed_models and tts_model in installed_models and model_has_speech:
        return OpenAISpeech(tts_base, tts_model, tts_voice)
    if sys.platform == "darwin" and shutil.which("say"):
        try:
            return MacSay(say_voice)
        except TTSError:
            return None
    return None
