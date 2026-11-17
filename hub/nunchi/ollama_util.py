"""Ollama 보조 함수: 설치된 모델 목록, 이름 해석, 기능(capabilities) 조회."""

from __future__ import annotations

import httpx


def list_models(ollama_url: str, timeout: float = 3.0) -> list[str] | None:
    """설치된 모델 이름 목록. Ollama가 꺼져 있으면 None."""
    try:
        r = httpx.get(f"{ollama_url.rstrip('/')}/api/tags", timeout=timeout)
        r.raise_for_status()
    except httpx.HTTPError:
        return None
    return [m.get("name") or m.get("model") for m in r.json().get("models", [])]


def resolve_model(name: str, installed: list[str] | None) -> str:
    """설정한 이름이 없으면 비슷한 설치본을 찾아 줍니다.

    - 'gemma4:e4b'가 없고 'gemma4:e4b-mlx'(애플 실리콘용 MLX 빌드)가 있으면 그걸 사용
    - 'bge-m3'는 'bge-m3:latest'로도 찾음
    """
    if not installed or not name:
        return name
    if name in installed:
        return name
    if ":" not in name and f"{name}:latest" in installed:
        return f"{name}:latest"
    for suffix in ("-mlx", "-nvfp4", "-it-qat", "-it-q4_K_M"):
        if f"{name}{suffix}" in installed:
            return f"{name}{suffix}"
    base = name.split(":")[0]
    if name.endswith(":latest") and base in installed:
        return base
    return name


def show(ollama_url: str, name: str, timeout: float = 5.0) -> dict | None:
    try:
        r = httpx.post(f"{ollama_url.rstrip('/')}/api/show", json={"model": name}, timeout=timeout)
        if r.status_code >= 400:
            return None
        return r.json()
    except httpx.HTTPError:
        return None


def capabilities(ollama_url: str, name: str) -> list[str]:
    info = show(ollama_url, name)
    return list(info.get("capabilities") or []) if info else []


def version(ollama_url: str) -> str | None:
    try:
        r = httpx.get(f"{ollama_url.rstrip('/')}/api/version", timeout=3.0)
        return r.json().get("version")
    except (httpx.HTTPError, ValueError):
        return None
