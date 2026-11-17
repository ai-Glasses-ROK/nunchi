import numpy as np

from nunchi import audio


def tone(sr: int, sec: float, freq: float = 440.0, amp: float = 0.2) -> np.ndarray:
    t = np.arange(int(sr * sec)) / sr
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def test_wav_roundtrip():
    x = tone(8000, 0.5)
    y, sr = audio.read_wav(audio.write_wav(x, 8000))
    assert sr == 8000 and len(y) == len(x)
    assert np.max(np.abs(y - x)) < 1e-3


def test_loose_wav_with_streaming_header():
    wav = bytearray(audio.write_wav(tone(24000, 0.2), 24000))
    wav[4:8] = (0xFFFFFFFF).to_bytes(4, "little")  # RIFF 크기
    idx = wav.find(b"data")
    wav[idx + 4 : idx + 8] = (0xFFFFFFFF).to_bytes(4, "little")  # data 크기
    y, sr = audio.read_wav(bytes(wav))
    assert sr == 24000 and abs(len(y) - 4800) <= 1


def test_resample_lengths_and_tone_preserved():
    x = tone(8000, 1.0, freq=300)
    up = audio.resample(x, 8000, 16000)
    down = audio.resample(up, 16000, 8000)
    assert len(up) == 16000 and len(down) == 8000
    assert np.corrcoef(x[200:-200], down[200:-200])[0, 1] > 0.98


def test_upsample_does_not_create_imaging_noise():
    # 8k → 16k 업샘플 시 4kHz 위쪽(원래 없던 대역)에 에너지가 거의 없어야 함
    x = tone(8000, 1.0, freq=3000, amp=0.5)
    up = audio.resample(x, 8000, 16000)
    power = np.abs(np.fft.rfft(up)) ** 2
    freqs = np.fft.rfftfreq(len(up), 1 / 16000)
    assert power[freqs > 4500].sum() / power.sum() < 1e-3  # 필터 없는 선형 보간은 약 0.17 (실측)


def test_trim_silence_and_normalize():
    sr = 16000
    x = np.concatenate([np.zeros(sr), tone(sr, 0.5, amp=0.05), np.zeros(sr)])
    wav, info = audio.preprocess(audio.write_wav(x, sr))
    y, _ = audio.read_wav(wav)
    assert info["output_sec"] < 1.2  # 앞뒤 무음 1초씩 잘림
    assert 0.6 < np.max(np.abs(y)) <= 0.72  # -3 dBFS 근처로 정규화


def test_concat_wavs():
    a = audio.write_wav(tone(16000, 0.2), 16000)
    b = audio.write_wav(tone(16000, 0.3), 16000)
    y, sr = audio.read_wav(audio.concat_wavs([a, b], gap_ms=100))
    assert abs(len(y) / sr - 0.6) < 0.01
