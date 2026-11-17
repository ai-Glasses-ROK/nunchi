"""오디오 유틸리티 (numpy + 표준 라이브러리 wave 만 사용).

안경 마이크(블루투스 HFP)는 8kHz 모노라 소리가 작고 대역이 좁습니다. 인식률을 올리기 위해
1) 직류 성분 제거 2) 저주파 웅웅거림 제거(고역 통과) 3) 앞뒤 무음 자르기 4) 볼륨 정규화를 합니다.
"""

from __future__ import annotations

import io
import shutil
import subprocess
import wave

import numpy as np


class AudioError(ValueError):
    pass


def is_wav(data: bytes) -> bool:
    return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """WAV → (float32 모노 샘플 -1..1, 샘플레이트)."""
    try:
        with wave.open(io.BytesIO(data), "rb") as w:
            sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
            raw = w.readframes(n)
    except (wave.Error, EOFError) as e:
        # 스트리밍으로 만든 WAV는 길이 필드가 0xFFFFFFFF인 경우가 있어 직접 파싱
        parsed = _parse_wav_loose(data)
        if parsed is None:
            raise AudioError(f"WAV를 읽지 못했어요: {e}") from e
        sr, ch, width, raw = parsed
    if width == 2:
        x = np.frombuffer(raw[: len(raw) // 2 * 2], dtype="<i2").astype(np.float32) / 32768.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 3:
        b = np.frombuffer(raw[: len(raw) // 3 * 3], dtype=np.uint8).reshape(-1, 3)
        i = (b[:, 0].astype(np.int32) | (b[:, 1].astype(np.int32) << 8) | (b[:, 2].astype(np.int32) << 16))
        i = np.where(i & 0x800000, i - 0x1000000, i)
        x = i.astype(np.float32) / 8388608.0
    elif width == 4:
        x = np.frombuffer(raw[: len(raw) // 4 * 4], dtype="<i4").astype(np.float32) / 2147483648.0
    else:
        raise AudioError(f"지원하지 않는 샘플 크기: {width * 8}bit")
    if ch > 1:
        x = x[: len(x) // ch * ch].reshape(-1, ch).mean(axis=1)
    return x.astype(np.float32), sr


def _parse_wav_loose(data: bytes) -> tuple[int, int, int, bytes] | None:
    if not is_wav(data):
        return None
    pos, fmt = 12, None
    while pos + 8 <= len(data):
        cid, size = data[pos : pos + 4], int.from_bytes(data[pos + 4 : pos + 8], "little")
        body = pos + 8
        if cid == b"fmt ":
            audio_format = int.from_bytes(data[body : body + 2], "little")
            ch = int.from_bytes(data[body + 2 : body + 4], "little")
            sr = int.from_bytes(data[body + 4 : body + 8], "little")
            bits = int.from_bytes(data[body + 14 : body + 16], "little")
            if audio_format not in (1, 0xFFFE):
                return None
            fmt = (sr, ch, bits // 8)
        elif cid == b"data" and fmt:
            end = len(data) if size in (0, 0xFFFFFFFF) or body + size > len(data) else body + size
            return fmt[0], fmt[1], fmt[2], data[body:end]
        pos = body + size + (size & 1)
        if size == 0xFFFFFFFF:
            break
    return None


def write_wav(samples: np.ndarray, sr: int) -> bytes:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767.0).astype("<i2").tobytes()
    return pcm16_to_wav(pcm, sr)


def pcm16_to_wav(pcm: bytes, sr: int, channels: int = 1) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm)
    return buf.getvalue()


def duration(samples: np.ndarray, sr: int) -> float:
    return len(samples) / float(sr) if sr else 0.0


def _lowpass(x: np.ndarray, cutoff: float, taps: int = 101) -> np.ndarray:
    """윈도우 sinc FIR 저역 통과. cutoff는 '샘플레이트 대비' 정규화 주파수(0~0.5)."""
    n = np.arange(taps) - (taps - 1) / 2
    h = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(taps)
    h /= h.sum()
    return np.convolve(x, h, mode="same")


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """간단하지만 안전한 리샘플러 (선형 보간 + 앞/뒤 저역 통과 필터).

    - 다운샘플: 보간 '전'에 새 나이퀴스트(sr_to/2) 아래로 걸러 에일리어싱 방지
    - 업샘플: 보간 '후'에 원래 나이퀴스트(sr_from/2) 아래로 걸러 이미징 잡음 제거
      (필터 없이 8k→16k로 늘리면 고음역에 가짜 성분이 생겨 음성 인식이 오히려 나빠집니다. 실험으로 확인)
    """
    if sr_from == sr_to or len(x) == 0:
        return x.astype(np.float32)
    if sr_to < sr_from:
        x = _lowpass(x, 0.45 * sr_to / sr_from)
    n_out = int(round(len(x) * sr_to / sr_from))
    t_in = np.arange(len(x)) / sr_from
    t_out = np.arange(n_out) / sr_to
    y = np.interp(t_out, t_in, x)
    if sr_to > sr_from:
        y = _lowpass(y, 0.45 * sr_from / sr_to)
    return y.astype(np.float32)


def highpass(x: np.ndarray, sr: int, cutoff: float = 80.0) -> np.ndarray:
    """간이 고역 통과 필터: 원신호 - 이동평균(저역). 바람 소리·진동 같은 저주파 웅웅거림 제거.

    이동평균 창 길이를 sr/cutoff 로 잡으면 cutoff 이하 성분이 주로 빠집니다. numpy만으로 벡터 연산.
    """
    w = int(sr / cutoff)
    if len(x) < w * 2 or w < 3:
        return x
    low = np.convolve(x, np.ones(w, dtype=np.float32) / w, mode="same")
    return (x - low).astype(np.float32)


def _frame_rms(x: np.ndarray, sr: int, frame_ms: int) -> tuple[np.ndarray, int]:
    frame = max(1, int(sr * frame_ms / 1000))
    n = len(x) // frame
    if n == 0:
        return np.zeros(0, dtype=np.float32), frame
    return np.sqrt(np.mean(x[: n * frame].reshape(n, frame) ** 2, axis=1) + 1e-12), frame


def _voice_threshold(rms: np.ndarray) -> float:
    """말소리 판정 기준. 최댓값 대비 -30dB(3%) 또는 바닥 소음의 2배 중 큰 값.

    주의: '하위 20% 에너지 = 소음'으로 잡으면, 말이 빽빽한 녹음(무음이 거의 없음)에서는 그 값이 이미
    말소리 크기라서 문장 뒷부분을 무음으로 잘라 버립니다. (실험에서 실제로 발생 → 5% 지점 + 최댓값 기준으로 수정)
    """
    return float(max(rms.max() * 0.03, np.percentile(rms, 5) * 2.0, 1e-4))


def trim_silence(x: np.ndarray, sr: int, frame_ms: int = 30, pad_ms: int = 300) -> np.ndarray:
    """에너지 기반 VAD로 '앞뒤' 무음만 잘라냅니다(가운데는 건드리지 않음). 말소리가 없으면 원본 반환."""
    rms, frame = _frame_rms(x, sr, frame_ms)
    if len(rms) < 3 or rms.max() < 1e-4:
        return x
    voiced = np.where(rms > _voice_threshold(rms))[0]
    if len(voiced) == 0:
        return x
    pad = int(pad_ms / frame_ms)
    start = max(0, voiced[0] - pad) * frame
    end = min(len(rms), voiced[-1] + 1 + pad) * frame
    return x[start:end]


def normalize_peak(x: np.ndarray, target_db: float = -3.0, max_gain_db: float = 24.0) -> np.ndarray:
    peak = float(np.max(np.abs(x))) if len(x) else 0.0
    if peak < 1e-5:
        return x
    target = 10 ** (target_db / 20)
    gain = min(target / peak, 10 ** (max_gain_db / 20))
    return (x * gain).astype(np.float32)


def speech_ratio(x: np.ndarray, sr: int, frame_ms: int = 30) -> float:
    """대략적인 '말소리 비율' (0~1). 너무 낮으면 아무 말도 안 한 녹음일 가능성이 큽니다."""
    rms, _ = _frame_rms(x, sr, frame_ms)
    if len(rms) == 0 or rms.max() < 1e-4:
        return 0.0
    return float(np.mean(rms > _voice_threshold(rms)))


def preprocess(data: bytes, target_sr: int | None = None) -> tuple[bytes, dict]:
    """음성 인식 전에 거치는 정리 단계. (WAV 바이트, 정보 dict) 반환."""
    x, sr = read_wav(data)
    info = {"input_sr": sr, "input_sec": round(duration(x, sr), 2)}
    x = x - float(np.mean(x)) if len(x) else x
    x = highpass(x, sr)
    x = trim_silence(x, sr)
    x = normalize_peak(x)
    if target_sr and target_sr != sr:
        x = resample(x, sr, target_sr)
        sr = target_sr
    info.update({"output_sr": sr, "output_sec": round(duration(x, sr), 2), "speech_ratio": round(speech_ratio(x, sr), 2)})
    return write_wav(x, sr), info


def to_wav(data: bytes, sr: int = 16000) -> bytes:
    """WAV가 아니면(m4a, webm, mp3 …) ffmpeg로 16kHz 모노 WAV로 변환합니다."""
    if is_wav(data):
        return data
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise AudioError("WAV가 아닌 오디오는 ffmpeg가 있어야 변환할 수 있어요 (brew install ffmpeg). 또는 WAV로 보내 주세요.")
    proc = subprocess.run(
        [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-ac", "1", "-ar", str(sr), "-f", "wav", "pipe:1"],
        input=data,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0 or not is_wav(proc.stdout):
        raise AudioError(f"오디오 변환 실패: {proc.stderr.decode('utf-8', 'replace')[:200]}")
    return proc.stdout


def concat_wavs(wavs: list[bytes], gap_ms: int = 120) -> bytes:
    """여러 WAV(같은 형식 가정)를 이어 붙입니다. 샘플레이트가 다르면 첫 번째에 맞춥니다."""
    if not wavs:
        return b""
    parts: list[np.ndarray] = []
    sr0 = None
    for w in wavs:
        x, sr = read_wav(w)
        if sr0 is None:
            sr0 = sr
        elif sr != sr0:
            x = resample(x, sr, sr0)
        parts.append(x)
        parts.append(np.zeros(int(sr0 * gap_ms / 1000), dtype=np.float32))
    return write_wav(np.concatenate(parts[:-1]), sr0 or 16000)
