"""임베딩(문장 → 숫자 벡터) 만들기. 기본은 Ollama의 /api/embed."""

from __future__ import annotations

from typing import Protocol

import httpx
import numpy as np


class EmbeddingError(RuntimeError):
    pass


class Embedder(Protocol):
    model: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


def l2_normalize(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        mat = mat[None, :]
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms


class OllamaEmbedder:
    def __init__(self, base_url: str, model: str, keep_alive: str = "30m", batch: int = 16, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self.batch = batch
        self.client = httpx.Client(timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            part = texts[i : i + self.batch]
            try:
                r = self.client.post(
                    f"{self.base_url}/api/embed",
                    json={"model": self.model, "input": part, "keep_alive": self.keep_alive, "truncate": True},
                )
            except httpx.HTTPError as e:
                raise EmbeddingError(f"Ollama에 연결하지 못했어요({self.base_url}): {e}") from e
            if r.status_code >= 400:
                if r.status_code == 404 or "not found" in r.text.lower():
                    raise EmbeddingError(f"임베딩 모델 '{self.model}'이 없어요. 먼저 실행: ollama pull {self.model}")
                raise EmbeddingError(f"임베딩 실패({r.status_code}): {r.text[:300]}")
            out.extend(r.json()["embeddings"])
        return l2_normalize(np.array(out, dtype=np.float32))


class OpenAIEmbedder:
    """OpenAI 호환 /v1/embeddings (llama.cpp server --embeddings, LM Studio, vLLM 등)."""

    def __init__(self, base_url: str, model: str, api_key: str = "local", batch: int = 16, timeout: float = 120):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.batch = batch
        self.client = httpx.Client(timeout=timeout, headers={"Authorization": f"Bearer {api_key}"})

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        out: list[list[float]] = []
        for i in range(0, len(texts), self.batch):
            part = texts[i : i + self.batch]
            try:
                r = self.client.post(f"{self.base_url}/embeddings", json={"model": self.model, "input": part})
            except httpx.HTTPError as e:
                raise EmbeddingError(f"임베딩 서버에 연결하지 못했어요({self.base_url}): {e}") from e
            if r.status_code >= 400:
                raise EmbeddingError(f"임베딩 실패({r.status_code}): {r.text[:300]}")
            data = sorted(r.json()["data"], key=lambda d: d.get("index", 0))
            out.extend(d["embedding"] for d in data)
        return l2_normalize(np.array(out, dtype=np.float32))


class HashEmbedder:
    """테스트용 가짜 임베딩(외부 서버 없이 동작). 글자 2-gram을 해시해 고정 길이 벡터로 만듭니다."""

    def __init__(self, dim: int = 256) -> None:
        self.dim = dim
        self.model = f"hash-{dim}"

    def embed(self, texts: list[str]) -> np.ndarray:
        import zlib

        from nunchi.rag.bm25 import tokenize

        mat = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for tok in tokenize(t):
                mat[i, zlib.crc32(tok.encode()) % self.dim] += 1.0
        return l2_normalize(mat)
