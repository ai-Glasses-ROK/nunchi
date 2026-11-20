#!/usr/bin/env python3
"""카메라·5.8GHz USB 영상 수신기를 MJPEG(HTTP)로 중계해, Nunchi 앱(Android·iOS) 드론 화면의 '네트워크(MJPEG)' 카메라로 보냅니다.

표준 라이브러리 + ffmpeg만 씁니다. 여러 앱이 동시에 붙어도 되고(안드로이드·아이폰·브라우저), 끊겼다 다시 붙어도 됩니다.

  # 1) 장치 번호 확인 (5.8GHz 수신기를 꽂으면 새 카메라로 나타남, 예: USB2.0 PC CAMERA)
  python3 scripts/video_relay.py --list

  # 2) 중계 (기본: 이 컴퓨터 안에서만 → Android 에뮬레이터·iOS 시뮬레이터)
  python3 scripts/video_relay.py --device 0

  # 3) 같은 와이파이의 폰·아이폰에서 받기 (영상이 그 네트워크에 열리니 집·실험실 와이파이에서만)
  python3 scripts/video_relay.py --device 1 --host 0.0.0.0

  # 카메라 없이 시험 영상으로 / 다른 영상 주소(5.8GHz→와이파이 박스의 RTSP 등)를 받아 다시 내보내기
  python3 scripts/video_relay.py --file clients/android/app/src/main/res/raw/virtual_drone_feed.mp4
  python3 scripts/video_relay.py --url rtsp://192.168.1.1:554/live

맥은 이 명령을 실행한 앱(터미널 등)에 카메라 권한이 있어야 합니다: 시스템 설정 → 개인정보 보호 및 보안 → 카메라.
"""

from __future__ import annotations

import argparse
import glob
import http.server
import platform
import shutil
import socket
import subprocess
import sys
import threading
import time

BOUNDARY = "nunchiframe"


def jpeg_end(buf: bytes | bytearray, start: int) -> int:
    """buf[start]의 SOI부터 JPEG 끝(EOI 바로 뒤) 위치. 아직 덜 왔으면 -1, 깨졌으면 -2.

    머리 구간(표·주석)은 길이로 건너뛰어 그 안의 FF D9를 끝으로 착각하지 않습니다. 압축 데이터 안에서는
    FF가 늘 FF 00으로 바뀌어 있으므로 SOS 뒤 첫 FF D9가 곧 끝입니다.
    """
    i, n = start + 2, len(buf)
    while i + 4 <= n:
        if buf[i] != 0xFF:
            return -2
        marker = buf[i + 1]
        if marker == 0xFF:  # 채움 바이트
            i += 1
            continue
        if marker == 0xD9:
            return i + 2
        seg = (buf[i + 2] << 8) | buf[i + 3]
        if marker == 0xDA:  # 압축 데이터 시작(SOS)
            end = buf.find(b"\xff\xd9", i + 2 + seg)
            return -1 if end < 0 else end + 2
        i += 2 + seg
    return -1


def split_jpegs(buf: bytearray) -> list[bytes]:
    """이어 붙은 JPEG 바이트(ffmpeg image2pipe)에서 완성된 장을 떼어 돌려주고, buf에는 덜 온 조각만 남깁니다."""
    frames: list[bytes] = []
    while True:
        start = buf.find(b"\xff\xd8\xff")
        if start < 0:
            del buf[: max(0, len(buf) - 2)]
            return frames
        end = jpeg_end(buf, start)
        if end == -1:
            del buf[:start]
            return frames
        if end == -2:
            del buf[: start + 2]
            continue
        frames.append(bytes(buf[start:end]))
        del buf[:end]


def jpeg_size(jpeg: bytes) -> tuple[int, int] | None:
    """(가로, 세로). SOF 구간에서 읽음."""
    i = 2
    while i + 9 <= len(jpeg) and jpeg[i] == 0xFF:
        marker = jpeg[i + 1]
        if marker in (0xC0, 0xC1, 0xC2):
            return (jpeg[i + 7] << 8) | jpeg[i + 8], (jpeg[i + 5] << 8) | jpeg[i + 6]
        i += 2 + ((jpeg[i + 2] << 8) | jpeg[i + 3])
    return None


class Latest:
    """가장 최근 JPEG 한 장. 모든 연결이 같은 장을 나눠 씁니다(느린 연결은 중간 장을 건너뜀)."""

    def __init__(self) -> None:
        self.cond = threading.Condition()
        self.jpeg = b""
        self.count = 0
        self.done = False

    def put(self, jpeg: bytes) -> None:
        with self.cond:
            self.jpeg = jpeg
            self.count += 1
            self.cond.notify_all()

    def finish(self) -> None:
        with self.cond:
            self.done = True
            self.cond.notify_all()

    def wait_next(self, seen: int, timeout: float = 5.0) -> tuple[int, bytes]:
        with self.cond:
            self.cond.wait_for(lambda: self.count != seen or self.done, timeout)
            return self.count, self.jpeg


def log(text: str) -> None:
    print(time.strftime("%H:%M:%S"), text, flush=True)


def make_handler(latest: Latest):
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"

        def do_GET(self) -> None:  # noqa: N802 (http.server 규칙)
            path = self.path.split("?", 1)[0]
            if path in ("/stream.mjpg", "/stream", "/video"):
                self.stream()
            elif path in ("/snapshot.jpg", "/frame.jpg"):
                self.snapshot()
            elif path == "/":
                body = (
                    "<!doctype html><meta charset=utf-8><title>Nunchi 영상 중계</title>"
                    "<body style='margin:0;background:#111;color:#eee;font:14px sans-serif'>"
                    "<p style='padding:8px'>앱 주소: /stream.mjpg · 한 장: /snapshot.jpg</p>"
                    "<img src='/stream.mjpg' style='max-width:100%'></body>"
                ).encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                self.send_error(404)

        def snapshot(self) -> None:
            _, jpeg = latest.wait_next(0, timeout=5)
            if not jpeg:
                self.send_error(503, "아직 영상이 없어요")
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(jpeg)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(jpeg)

        def stream(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={BOUNDARY}")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Pragma", "no-cache")
            self.end_headers()
            who = self.client_address[0]
            log(f"연결: {who}")
            seen, sent = 0, 0
            try:
                while not latest.done:
                    count, jpeg = latest.wait_next(seen)
                    if count == seen or not jpeg:
                        continue
                    seen = count
                    head = f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
                    self.wfile.write(head.encode() + jpeg + b"\r\n")
                    self.wfile.flush()
                    sent += 1
            except (BrokenPipeError, ConnectionResetError, TimeoutError):
                pass
            log(f"끊김: {who} (보낸 장 {sent})")

        def log_message(self, format: str, *args) -> None:  # noqa: A002 - 요청마다 찍지 않음
            pass

    return Handler


def input_args(args: argparse.Namespace) -> list[str]:
    if args.file:
        return ["-re", "-stream_loop", "-1", "-i", args.file]
    if args.url:
        return (["-rtsp_transport", "tcp"] if args.url.startswith("rtsp") else []) + ["-i", args.url]
    system = platform.system()
    if system == "Darwin":
        return ["-f", "avfoundation", "-framerate", args.input_fps, "-pixel_format", "nv12", "-i", f"{args.device}:none"]
    if system == "Linux":
        dev = args.device if args.device.startswith("/dev/") else f"/dev/video{args.device}"
        return ["-f", "v4l2", "-framerate", args.input_fps, "-i", dev]
    if system == "Windows":
        return ["-f", "dshow", "-i", f"video={args.device}"]
    raise SystemExit(f"이 운영체제({system})의 카메라 입력은 모릅니다. --url 이나 --file 을 쓰세요.")


def list_devices() -> None:
    system = platform.system()
    if system == "Linux":
        found = sorted(glob.glob("/dev/video*"))
        print("\n".join(found) if found else "카메라 장치(/dev/video*)가 없어요.")
        return
    if system == "Darwin":
        cmd = ["ffmpeg", "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]
    elif system == "Windows":
        cmd = ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
    else:
        raise SystemExit(f"이 운영체제({system})의 장치 목록은 모릅니다.")
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=20).stderr
    video = system == "Windows"
    print("영상 장치 (--device 에 번호/이름):")
    for line in out.splitlines():
        if "video devices" in line:
            video = True
            continue
        if "audio devices" in line:
            video = False
            continue
        if system == "Darwin" and video and "] [" in line:
            print("  " + line.split("] [", 1)[1].replace("] ", "  ", 1))
        elif system == "Windows" and "(video)" in line:
            print("  " + line.split("]", 1)[-1].strip())


def lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.254.254.254", 1))  # 패킷은 보내지 않고, 나갈 경로의 내 주소만 알아냄
            return s.getsockname()[0]
    except OSError:
        return None


def pump(proc: subprocess.Popen, latest: Latest) -> None:
    buf = bytearray()
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read1(65536)
        if not chunk:
            break
        buf += chunk
        for jpeg in split_jpegs(buf):
            latest.put(jpeg)
            if latest.count == 1:
                size = jpeg_size(jpeg)
                log(f"첫 프레임: {size[0]}x{size[1]} · {len(jpeg) // 1024}KB" if size else "첫 프레임")
    latest.finish()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", action="store_true", help="카메라·영상 수신기 장치 목록")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--device", default="0", help="장치 번호/이름 (맥: --list의 번호, 리눅스: 0 또는 /dev/video0, 윈도: 장치 이름)")
    src.add_argument("--file", help="시험 영상 파일 (반복 재생)")
    src.add_argument("--url", help="ffmpeg가 여는 다른 영상 주소 (rtsp://… 등)")
    ap.add_argument("--host", default="127.0.0.1", help="열 주소. 같은 와이파이의 폰에서 받으려면 0.0.0.0 (기본: 이 컴퓨터 안에서만)")
    ap.add_argument("--port", type=int, default=8090)
    ap.add_argument("--fps", type=float, default=10, help="내보내는 초당 장수 (기본 10, Gemma 분석엔 충분)")
    ap.add_argument("--input-fps", default="30", help="장치에서 받는 초당 장수 (PAL 수신기는 25)")
    ap.add_argument("--width", type=int, default=960, help="내보내는 최대 가로 픽셀 (기본 960)")
    ap.add_argument("--quality", type=int, default=5, choices=range(2, 16), metavar="2~15", help="JPEG 품질, 작을수록 좋음 (기본 5)")
    args = ap.parse_args()

    if not shutil.which("ffmpeg"):
        raise SystemExit("ffmpeg가 필요해요: brew install ffmpeg (리눅스: apt install ffmpeg)")
    if args.list:
        list_devices()
        return 0

    latest = Latest()
    try:
        server = http.server.ThreadingHTTPServer((args.host, args.port), make_handler(latest))
    except OSError as e:
        raise SystemExit(f"{args.host}:{args.port} 를 열 수 없어요 ({e}). 다른 포트: --port 8091")
    server.daemon_threads = True

    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", *input_args(args), "-an",
           "-vf", f"fps={args.fps:g},scale='min({args.width},iw)':-2", "-q:v", str(args.quality),
           "-f", "image2pipe", "-c:v", "mjpeg", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE)
    threading.Thread(target=pump, args=(proc, latest), daemon=True).start()
    threading.Thread(target=server.serve_forever, daemon=True).start()

    log(f"중계 시작: http://{args.host}:{args.port}/stream.mjpg (한 장: /snapshot.jpg, 끝내기: Ctrl-C)")
    if args.host in ("127.0.0.1", "localhost"):
        print(f"  Android 에뮬레이터: http://10.0.2.2:{args.port}/stream.mjpg · iOS 시뮬레이터: http://127.0.0.1:{args.port}/stream.mjpg")
        print("  같은 와이파이의 폰에서 받으려면 --host 0.0.0.0")
    else:
        print(f"  폰 앱(드론 화면 → 네트워크(MJPEG)): http://{lan_ip() or '이컴퓨터IP'}:{args.port}/stream.mjpg")
        print("  영상이 이 네트워크에 열려 있어요. 다 쓰면 Ctrl-C로 끄세요.")

    started, warned, code = time.time(), False, 0
    try:
        while proc.poll() is None:
            time.sleep(0.5)
            if not warned and latest.count == 0 and time.time() - started > 10:
                warned = True
                log("10초 동안 영상이 안 와요. 맥이면 카메라 권한(시스템 설정 → 개인정보 보호 및 보안 → 카메라)에서 "
                    "이 명령을 실행한 앱을 켜 주세요. 장치 번호(--list)와 --input-fps 25(PAL 수신기)도 확인해 보세요.")
        code = proc.returncode or 0
        log(f"ffmpeg가 끝났어요 (코드 {code}). 위 오류를 보세요 — 장치 번호·초당 장수(--input-fps)·권한.")
    except KeyboardInterrupt:
        log("끝냅니다.")
        proc.terminate()
    finally:
        latest.finish()
        server.shutdown()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
    return code


if __name__ == "__main__":
    sys.exit(main())
