"""허브의 지연시간을 여러 번 재서 중앙값(p50)과 p90을 냅니다 (클라이언트 기준, HTTP+SSE 포함).

측정 항목
  text   글자 질문:  첫 글자(delta) · 첫 문장(sentence) · 완료(done)
  speak  글자 질문 + 허브가 음성 합성(TTS):  첫 음성 조각(audio) · 완료   (--speak)
  voice  음성 질문:  인식 결과(transcript) · 첫 문장 · 완료   (macOS say로 만든 16kHz 질문 음성)
  photo  사진 질문:  캡션 · 검색 · 첫 문장 · 완료

캐시 착시를 막는 규칙
  - 모든 요청은 서로 다른 질문(예제 평가 질문 16개)·서로 다른 사진(조금씩 회전한 변형)
  - 모드를 시작할 때마다 Ollama에서 모델을 내려(keep_alive=0) 프롬프트 캐시를 비우고,
    측정에 쓰지 않는 별도 질문으로 예열한 뒤 잽니다.
  같은 질문·같은 사진을 되풀이하면 프롬프트 캐시가 재사용돼 실제보다 훨씬 빠르게 나옵니다
  (실측: 같은 사진 반복 시 사진 질문 완료 p50 0.71초 → 매번 새 사진이면 2.6초).

사용 예 (허브가 켜져 있어야 함: nunchi serve)
  python research/latency/measure.py --image panel.jpg --speak -o latency.json
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
WARMUP_TEXT = "안녕, 오늘 날씨 어때?"  # 측정 질문과 겹치지 않는 예열용
PHOTO_QUESTIONS = [
    "이게 뭐야?",
    "이거 왜 이래? 어떻게 해야 해?",
    "지금 보이는 코드는 무슨 뜻이야?",
    "화면에 뜬 게 뭐야?",
    "이 경고는 무슨 의미야?",
    "이 장비 지금 괜찮은 거야?",
    "빨간 불이 왜 켜졌어?",
    "이거 고치려면 뭐부터 해?",
]


def load_questions(path: Path) -> list[str]:
    return [json.loads(line)["q"] for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def image_variants(src: Path, n: int, outdir: Path) -> list[bytes]:
    """사진을 조금씩 돌려 n장의 서로 다른 사진을 만듭니다 (macOS sips). 내용은 같고 픽셀은 달라 캐시가 재사용되지 않음."""
    if not shutil.which("sips"):
        print("  ! sips가 없어 같은 사진을 반복합니다 (사진 지연이 실제보다 빠르게 나올 수 있음)")
        return [src.read_bytes()] * n
    out = []
    for i in range(n):
        angle = (-6 + 12 * i / max(n - 1, 1)) % 360  # sips는 음수 각도를 옵션으로 읽으므로 354°처럼 바꿔 줌
        dst = outdir / f"variant_{i:02d}.jpg"
        subprocess.run(["sips", "-r", f"{angle:.2f}", str(src), "--out", str(dst)], check=True, capture_output=True)
        out.append(dst.read_bytes())
    return out


def sse(client: httpx.Client, url: str, **kw) -> dict:
    """요청을 보내고 이벤트별 '처음 도착 시각'(ms)과 done 데이터를 돌려줍니다."""
    t0 = time.perf_counter()
    first: dict[str, float] = {}
    done: dict = {}
    with client.stream("POST", url, **kw) as r:
        r.raise_for_status()
        event = None
        for line in r.iter_lines():
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: ") and event:
                first.setdefault(event, (time.perf_counter() - t0) * 1000)
                if event == "done":
                    done = json.loads(line[6:])
    return {"first": first, "done": done, "total_ms": (time.perf_counter() - t0) * 1000}


def say_wav(text: str, sr: int = 16000) -> bytes:
    with tempfile.TemporaryDirectory() as td:
        aiff, wav = Path(td) / "q.aiff", Path(td) / "q.wav"
        subprocess.run(["say", "-v", "Yuna", "-o", str(aiff), text], check=True)
        subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-i", str(aiff), "-ar", str(sr), "-ac", "1", str(wav)], check=True)
        return wav.read_bytes()


def machine() -> str:
    """측정한 컴퓨터 (결과를 비교할 때 꼭 함께 적기)."""
    try:
        if platform.system() == "Darwin":
            chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
            mem = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout) // 2**30
            return f"{chip} · {mem}GB · macOS {platform.mac_ver()[0]}"
    except Exception:  # noqa: BLE001
        pass
    return f"{platform.system()} {platform.machine()}"


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def summarize(name: str, runs: list[dict], keys: list[tuple[str, str]]) -> dict:
    out = {"n": len(runs)}
    print(f"\n[{name}] {len(runs)}회")
    for label, key in keys:
        vals = [r["first"][key] if key in r["first"] else r.get(key) for r in runs]
        vals = [v for v in vals if isinstance(v, (int, float))]
        if not vals:
            continue
        out[label] = {"p50": round(pct(vals, 0.5)), "p90": round(pct(vals, 0.9)), "min": round(min(vals)), "max": round(max(vals))}
        print(f"  {label:12s} p50 {out[label]['p50']:6d}ms · p90 {out[label]['p90']:6d}ms · 범위 {out[label]['min']}~{out[label]['max']}")
    return out


def save(result: dict, path: Path | None) -> None:
    if path:
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hub", default="http://127.0.0.1:8765")
    ap.add_argument("--token", default="")
    ap.add_argument("--runs", type=int, default=16, help="모드별 횟수 (질문이 겹치지 않도록 평가 질문 수 이하 권장)")
    ap.add_argument("--questions", type=Path, default=ROOT / "examples" / "eval" / "questions.jsonl")
    ap.add_argument("--image", type=Path, help="사진 질문용 이미지 (없으면 photo 생략)")
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--speak", action="store_true", help="허브 TTS의 첫 음성 조각까지도 측정")
    ap.add_argument("--modes", default="text,speak,voice,photo", help="측정할 것만 (예: photo). 결과는 -o 파일에 합쳐짐")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434", help="모드마다 모델을 내려 캐시를 비울 Ollama 주소")
    ap.add_argument("--keep-cache", action="store_true", help="캐시를 비우지 않음 (캐시 효과를 보고 싶을 때)")
    ap.add_argument("-o", "--output", type=Path)
    args = ap.parse_args()

    headers = {"Authorization": f"Bearer {args.token}"} if args.token else {}
    client = httpx.Client(timeout=300, headers=headers)
    health = client.get(f"{args.hub}/v1/health").json()
    modes = set(args.modes.split(","))
    previous = json.loads(args.output.read_text(encoding="utf-8")) if args.output and args.output.exists() else {}
    result: dict = {
        **previous,
        "machine": machine(),
        "models": health.get("models"),
        "runs": args.runs,
        "date": time.strftime("%Y-%m-%d %H:%M"),
    }
    print(f"{result['machine']} · {result['models']}")

    questions = load_questions(args.questions)
    if args.runs > len(questions):
        print(f"  ! 질문 {len(questions)}개보다 많이 돌려 질문이 반복됩니다 (캐시 때문에 빨라질 수 있음)")
    result["unique_prompts"] = args.runs <= len(questions)
    result["cache_cleared_per_mode"] = not args.keep_cache

    def q(i: int) -> str:
        return questions[i % len(questions)]

    def unload() -> None:
        """허브가 쓰는 모델을 Ollama에서 내려 프롬프트 캐시를 비웁니다 (다음 요청에서 다시 올라옴)."""
        if args.keep_cache:
            return
        names = {m for m in (health.get("models") or {}).values() if m}
        for name in names:
            try:
                httpx.post(f"{args.ollama}/api/generate", json={"model": name, "keep_alive": 0}, timeout=60)
            except httpx.HTTPError:
                pass

    def run_many(make, n, warm):
        unload()
        for _ in range(max(1, args.warmup)):
            warm()
        return [make(i) for i in range(n)]

    warm_text = lambda: sse(client, f"{args.hub}/v1/ask", json={"question": WARMUP_TEXT, "stream": True})  # noqa: E731
    if "text" in modes:
        text_runs = run_many(lambda i: sse(client, f"{args.hub}/v1/ask", json={"question": q(i), "stream": True}), args.runs, warm_text)
        result["text"] = summarize("글자 질문", text_runs, [("첫 글자", "delta"), ("첫 문장", "sentence"), ("완료", "total_ms")])
        save(result, args.output)

    if args.speak and "speak" in modes:
        speak_runs = run_many(
            lambda i: sse(client, f"{args.hub}/v1/ask", json={"question": q(i + 7), "stream": True, "speak": True}),
            args.runs,
            lambda: sse(client, f"{args.hub}/v1/ask", json={"question": WARMUP_TEXT, "stream": True, "speak": True}),
        )
        result["speak"] = summarize("글자 질문 + 허브 TTS", speak_runs, [("첫 문장", "sentence"), ("첫 음성", "audio"), ("완료", "total_ms")])
        save(result, args.output)

    if "voice" in modes and shutil.which("say") and shutil.which("ffmpeg"):
        wavs = [say_wav(q(i + 3)) for i in range(args.runs)]
        warm_wav = say_wav(WARMUP_TEXT)
        voice_runs = run_many(
            lambda i: sse(client, f"{args.hub}/v1/voice", files={"file": ("q.wav", wavs[i], "audio/wav")}, data={"stream": "true"}),
            args.runs,
            lambda: sse(client, f"{args.hub}/v1/voice", files={"file": ("q.wav", warm_wav, "audio/wav")}, data={"stream": "true"}),
        )
        result["voice"] = summarize("음성 질문(16kHz)", voice_runs, [("인식", "transcript"), ("첫 문장", "sentence"), ("완료", "total_ms")])
        save(result, args.output)

    if args.image and "photo" in modes:
        tmp = Path(tempfile.mkdtemp(prefix="nunchi-latency-"))
        imgs = image_variants(args.image, args.runs + 1, tmp)  # 마지막 한 장은 예열용
        photo_runs = run_many(
            lambda i: sse(
                client,
                f"{args.hub}/v1/vision",
                files={"image": ("photo.jpg", imgs[i], "image/jpeg")},
                data={"question": PHOTO_QUESTIONS[i % len(PHOTO_QUESTIONS)], "stream": "true"},
            ),
            args.runs,
            lambda: sse(
                client, f"{args.hub}/v1/vision", files={"image": ("photo.jpg", imgs[-1], "image/jpeg")}, data={"question": "안녕?", "stream": "true"}
            ),
        )
        shutil.rmtree(tmp, ignore_errors=True)
        result["photo"] = summarize("사진 질문", photo_runs, [("캡션", "caption"), ("검색", "retrieval"), ("첫 문장", "sentence"), ("완료", "total_ms")])

    if args.output:
        save(result, args.output)
        print(f"\n저장: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
