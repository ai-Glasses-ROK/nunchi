"""LLM 채팅 백엔드: Ollama(/api/chat) 와 OpenAI 호환(/v1/chat/completions).

OpenAI 호환 백엔드로는 llama.cpp의 llama-server, LM Studio, mlx_lm.server, vLLM 등
'내 컴퓨터에서 도는' 서버를 연결합니다. (클라우드 API가 아닙니다)
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

import httpx


class LLMError(RuntimeError):
    pass


class ChatBackend(Protocol):
    model: str
    last_stats: dict

    def stream(self, messages: list[dict], images: list[bytes] | None = None, max_tokens: int | None = None) -> Iterator[str]: ...


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def image_mime(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc"):
        return "image/heic"
    return "application/octet-stream"


def ensure_supported_image(data: bytes) -> bytes:
    """HEIC(아이폰 기본 사진 형식)는 많은 비전 모델이 못 읽습니다. macOS면 내장 `sips`로 JPEG 변환."""
    mime = image_mime(data)
    if mime in ("image/jpeg", "image/png", "image/webp"):
        return data
    if mime == "image/heic" and shutil.which("sips"):
        with tempfile.TemporaryDirectory() as td:
            src, dst = Path(td) / "in.heic", Path(td) / "out.jpg"
            src.write_bytes(data)
            subprocess.run(
                ["sips", "-s", "format", "jpeg", str(src), "--out", str(dst)], check=True, capture_output=True
            )
            return dst.read_bytes()
    if mime == "image/heic":
        raise LLMError("HEIC 사진은 지원하지 않아요. JPEG로 보내 주세요 (앱에서는 자동으로 JPEG를 씁니다).")
    raise LLMError("알 수 없는 이미지 형식이에요. JPEG 또는 PNG를 보내 주세요.")


class OllamaChat:
    def __init__(
        self,
        base_url: str,
        model: str,
        temperature: float = 0.3,
        num_ctx: int = 8192,
        max_tokens: int = 384,
        keep_alive: str = "30m",
        think: bool = False,
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.max_tokens = max_tokens
        self.keep_alive = keep_alive
        self.think = think
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=5.0))
        self.last_stats: dict = {}
        self._send_think = True  # 추론을 지원하지 않는 모델이면 think 필드를 빼고 다시 보냄

    def _payload(self, messages: list[dict], images: list[bytes] | None, max_tokens: int | None = None) -> dict:
        msgs = [dict(m) for m in messages]
        if images:
            for m in reversed(msgs):
                if m["role"] == "user":
                    m["images"] = [_b64(ensure_supported_image(img)) for img in images]
                    break
        payload: dict = {
            "model": self.model,
            "messages": msgs,
            "stream": True,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx, "num_predict": max_tokens or self.max_tokens},
        }
        if self._send_think:
            payload["think"] = self.think
        return payload

    def stream(self, messages: list[dict], images: list[bytes] | None = None, max_tokens: int | None = None) -> Iterator[str]:
        for attempt in range(2):
            payload = self._payload(messages, images, max_tokens)
            try:
                with self.client.stream("POST", f"{self.base_url}/api/chat", json=payload) as r:
                    if r.status_code >= 400:
                        body = r.read().decode("utf-8", "replace")
                        if "think" in body.lower() and self._send_think and attempt == 0:
                            self._send_think = False
                            continue
                        if r.status_code == 404 or "not found" in body.lower():
                            raise LLMError(f"모델 '{self.model}'이 없어요. 먼저 실행: ollama pull {self.model}")
                        raise LLMError(f"LLM 오류({r.status_code}): {body[:300]}")
                    for line in r.iter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise LLMError(str(data["error"]))
                        content = (data.get("message") or {}).get("content")
                        if content:
                            yield content
                        if data.get("done"):
                            self.last_stats = {
                                k: data.get(k)
                                for k in ("total_duration", "load_duration", "prompt_eval_count", "eval_count", "eval_duration")
                            }
                            break
                return
            except httpx.HTTPError as e:
                raise LLMError(f"Ollama에 연결하지 못했어요({self.base_url}). Ollama가 켜져 있나요? ({e})") from e

    def warmup(self) -> bool:
        """모델을 미리 메모리에 올려 첫 질문의 대기 시간을 줄입니다."""
        try:
            r = self.client.post(
                f"{self.base_url}/api/generate", json={"model": self.model, "keep_alive": self.keep_alive}, timeout=180
            )
            return r.status_code < 400
        except httpx.HTTPError:
            return False


class OpenAIChat:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "local",
        temperature: float = 0.3,
        max_tokens: int = 384,
        timeout: float = 300.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.client = httpx.Client(
            timeout=httpx.Timeout(timeout, connect=5.0), headers={"Authorization": f"Bearer {api_key}"}
        )
        self.last_stats: dict = {}

    def stream(self, messages: list[dict], images: list[bytes] | None = None, max_tokens: int | None = None) -> Iterator[str]:
        msgs = [dict(m) for m in messages]
        if images:
            for m in reversed(msgs):
                if m["role"] == "user":
                    parts: list[dict] = [{"type": "text", "text": m["content"]}]
                    for img in images:
                        img = ensure_supported_image(img)
                        parts.append(
                            {"type": "image_url", "image_url": {"url": f"data:{image_mime(img)};base64,{_b64(img)}"}}
                        )
                    m["content"] = parts
                    break
        payload = {
            "model": self.model,
            "messages": msgs,
            "stream": True,
            "temperature": self.temperature,
            "max_tokens": max_tokens or self.max_tokens,
        }
        try:
            with self.client.stream("POST", f"{self.base_url}/chat/completions", json=payload) as r:
                if r.status_code >= 400:
                    body = r.read().decode("utf-8", "replace")
                    raise LLMError(f"LLM 오류({r.status_code}): {body[:300]}")
                for line in r.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    obj = json.loads(data)
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = (choices[0].get("delta") or {}).get("content")
                    if delta:
                        yield delta
                    if obj.get("usage"):
                        self.last_stats = obj["usage"]
        except httpx.HTTPError as e:
            raise LLMError(f"LLM 서버에 연결하지 못했어요({self.base_url}): {e}") from e

    def warmup(self) -> bool:
        return True


class EchoChat:
    """테스트용: 받은 질문과 참고 문서 개수를 그대로 말해 주는 가짜 LLM."""

    def __init__(self) -> None:
        self.model = "echo"
        self.last_stats: dict = {}

    def stream(self, messages: list[dict], images: list[bytes] | None = None, max_tokens: int | None = None) -> Iterator[str]:
        last = messages[-1]["content"]
        q = last.split("[질문]")[-1].strip() if "[질문]" in last else last
        n_docs = len(re.findall(r"^\[\d+\] ", last, re.M))
        answer = f"질문은 {q} 입니다. 참고 문서 {n_docs}개를 봤어요."
        if images:
            answer += f" 사진 {len(images)}장을 받았어요."
        for word in answer.split(" "):
            yield word + " "

    def warmup(self) -> bool:
        return True
