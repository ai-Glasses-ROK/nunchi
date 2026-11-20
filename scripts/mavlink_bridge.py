#!/usr/bin/env python3
"""픽스호크(MAVLink)를 컴퓨터 USB로 읽어 상태를 보여 주고, 원하면 UDP로 폰·에뮬레이터·아이폰에 중계합니다.

표준 라이브러리만 씁니다(pyserial 불필요, 맥·리눅스). Nunchi 앱(Android drone/Mavlink.kt)과 같은 파서 규칙.

  # 1) 꽂힌 비행컨트롤러 확인 (15초 동안 읽고 요약)
  python3 scripts/mavlink_bridge.py --probe 15

  # 2) 중계: 컴퓨터 USB의 픽스호크 ↔ UDP (아이폰은 USB 시리얼을 못 써서 이 방법으로)
  python3 scripts/mavlink_bridge.py --udp 192.168.0.23:14550      # 같은 와이파이의 폰/아이폰 IP
  python3 scripts/mavlink_bridge.py --udp 127.0.0.1:14550         # 에뮬레이터(adb emu redir add udp:14550:14550)·iOS 시뮬레이터

시리얼 포트는 자동으로 찾습니다(/dev/cu.usbmodem* = 픽스호크 본체 USB, /dev/cu.usbserial*·wchusbserial* = 텔레메트리 라디오).
픽스호크 USB는 보드레이트와 상관없고, 텔레메트리 라디오는 --baud 57600(기본)입니다.
"""

from __future__ import annotations

import argparse
import glob
import os
import select
import socket
import struct
import sys
import termios
import time
import tty

SPECS = {  # 메시지 번호: (CRC_EXTRA, 기본 길이)
    0: (50, 9), 1: (124, 31), 2: (137, 12), 24: (24, 30), 29: (115, 14), 30: (39, 28), 33: (104, 28), 42: (28, 2),
    65: (118, 42), 66: (148, 6), 74: (20, 20), 76: (152, 33), 77: (143, 3), 125: (203, 6), 147: (154, 36),
    193: (71, 22), 242: (104, 52), 253: (83, 51),
}
NAMES = {0: "HEARTBEAT", 1: "SYS_STATUS", 2: "SYSTEM_TIME", 24: "GPS_RAW_INT", 29: "SCALED_PRESSURE", 30: "ATTITUDE",
         33: "GLOBAL_POSITION_INT", 42: "MISSION_CURRENT", 65: "RC_CHANNELS", 74: "VFR_HUD", 77: "COMMAND_ACK",
         125: "POWER_STATUS", 147: "BATTERY_STATUS", 193: "EKF_STATUS_REPORT", 242: "HOME_POSITION", 253: "STATUSTEXT"}
COPTER = {0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE", 9: "LAND",
          13: "SPORT", 15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 21: "SMART_RTL"}
FIX = {0: "GPS 없음", 1: "위치 못 잡음", 2: "2D", 3: "3D", 4: "DGPS", 5: "RTK Float", 6: "RTK Fixed"}


def x25(data: bytes, crc: int = 0xFFFF) -> int:
    for b in data:
        tmp = (b ^ crc) & 0xFF
        tmp = (tmp ^ (tmp << 4)) & 0xFF
        crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


class Parser:
    def __init__(self) -> None:
        self.buf = bytearray()
        self.ok = self.crc_errors = self.unknown = 0
        self.version = 0

    def feed(self, data: bytes) -> list[tuple[int, int, int, int, bytes]]:
        self.buf += data
        out, i, b = [], 0, self.buf
        while True:
            while i < len(b) and b[i] not in (0xFE, 0xFD):
                i += 1
            if i >= len(b):
                break
            v2 = b[i] == 0xFD
            header = 10 if v2 else 6
            if len(b) - i < header:
                break
            length = b[i + 1]
            incompat = b[i + 2] if v2 else 0
            if incompat & ~0x01:
                i += 1
                continue
            frame_len = header + length + 2 + (13 if incompat & 0x01 else 0)
            if len(b) - i < frame_len:
                break
            msgid = (b[i + 7] | b[i + 8] << 8 | b[i + 9] << 16) if v2 else b[i + 5]
            spec = SPECS.get(msgid)
            if spec is None:
                if len(b) - i == frame_len or b[i + frame_len] in (0xFE, 0xFD):
                    self.unknown += 1
                    i += frame_len
                else:
                    i += 1
                continue
            crc_at = i + header + length
            if x25(bytes([spec[0]]), x25(bytes(b[i + 1:crc_at]))) != (b[crc_at] | b[crc_at + 1] << 8):
                self.crc_errors += 1
                i += 1
                continue
            sysid, compid = (b[i + 5], b[i + 6]) if v2 else (b[i + 3], b[i + 4])
            out.append((2 if v2 else 1, sysid, compid, msgid, bytes(b[i + header:crc_at]).ljust(spec[1], b"\0")))
            self.version = 2 if v2 else 1
            self.ok += 1
            i += frame_len
        del self.buf[:i]
        return out


_seq = 0


def frame(msgid: int, payload: bytes, v2: bool) -> bytes:
    global _seq
    if v2:
        head = bytes([0xFD, len(payload), 0, 0, _seq, 255, 190, msgid & 0xFF, (msgid >> 8) & 0xFF, msgid >> 16])
    else:
        head = bytes([0xFE, len(payload), _seq, 255, 190, msgid])
    _seq = (_seq + 1) & 0xFF
    crc = x25(bytes([SPECS[msgid][0]]), x25(head[1:] + payload))
    return head + payload + struct.pack("<H", crc)


def gcs_heartbeat(v2: bool) -> bytes:
    return frame(0, struct.pack("<IBBBBB", 0, 6, 8, 0, 4, 3), v2)


def request_streams(sysid: int, compid: int, v2: bool) -> bytes:
    # 앱과 같은 요청: EXTENDED_STATUS 2Hz, POSITION 3Hz, EXTRA1 4Hz, EXTRA2 4Hz, EXTRA3 1Hz, RC 1Hz
    return b"".join(frame(66, struct.pack("<HBBBB", hz, sysid, compid, sid, 1), v2) for sid, hz in ((2, 2), (6, 3), (10, 4), (11, 4), (12, 1), (3, 1)))


def open_serial(path: str, baud: int) -> int:
    fd = os.open(path, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
    tty.setraw(fd)
    attrs = termios.tcgetattr(fd)
    speed = getattr(termios, f"B{baud}", termios.B115200)
    attrs[4] = attrs[5] = speed
    attrs[2] |= termios.CLOCAL | termios.CREAD
    termios.tcsetattr(fd, termios.TCSANOW, attrs)
    termios.tcflush(fd, termios.TCIOFLUSH)
    return fd


def find_port() -> str | None:
    for pattern in ("/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/cu.usbserial*", "/dev/cu.wchusbserial*", "/dev/cu.SLAB*", "/dev/ttyUSB*"):
        found = sorted(glob.glob(pattern))
        if found:
            return found[0]
    return None


class State:
    def __init__(self) -> None:
        self.counts: dict[int, int] = {}
        self.sys = self.comp = 0
        self.info: dict[str, object] = {}
        self.texts: list[str] = []

    def apply(self, sysid: int, compid: int, msgid: int, p: bytes) -> str | None:
        self.counts[msgid] = self.counts.get(msgid, 0) + 1
        if msgid == 0:
            custom, typ, ap, base, status, _ = struct.unpack_from("<IBBBBB", p)
            if typ == 6 or ap == 8:
                return None
            self.sys, self.comp = sysid, compid
            mode = COPTER.get(custom, f"MODE {custom}") if ap == 3 else f"custom {custom}"
            changed = self.info.get("mode") != mode or self.info.get("armed") != bool(base & 0x80)
            self.info.update(autopilot={3: "ArduPilot", 12: "PX4"}.get(ap, ap), type=typ, mode=mode, armed=bool(base & 0x80), status=status)
            return f"HEARTBEAT {self.info['autopilot']} type={typ} mode={mode} armed={self.info['armed']} status={status}" if changed else None
        if msgid == 1:
            f = struct.unpack_from("<IIIHHhHHHHHHb", p)
            present, enabled, health = f[0], f[1], f[2]
            bad = [n for bit, n in ((1, "자이로"), (2, "가속도계"), (4, "나침반"), (8, "기압계"), (0x20, "GPS"), (0x10000, "RC 수신기"),
                                    (0x200000, "AHRS"), (0x1000000, "로깅"), (0x2000000, "배터리"), (0x10000000, "PreArm"))
                   if present & bit and enabled & bit and not health & bit]
            self.info.update(voltage=f[4] / 1000 if 0 < f[4] < 65535 else None, battery=f[12], unhealthy=bad)
        elif msgid == 24:
            _, lat, lon, alt, eph, _, vel, _, fix, sats = struct.unpack_from("<QiiiHHHHBB", p)
            self.info.update(fix=FIX.get(fix, fix), sats=sats, hdop=None if eph == 0xFFFF else eph / 100)
        elif msgid == 33:
            _, lat, lon, alt, rel, *_ = struct.unpack_from("<IiiiihhhH", p)
            self.info.update(lat=lat / 1e7, lon=lon / 1e7, rel_alt=rel / 1000)
        elif msgid == 74:
            air, ground, alt, climb, heading, throttle = struct.unpack_from("<ffffhH", p)
            self.info.update(heading=heading, groundspeed=round(ground, 2))
        elif msgid == 30:
            _, roll, pitch, yaw, *_ = struct.unpack_from("<Iffffff", p)
            self.info.update(roll=round(roll * 57.2958, 1), pitch=round(pitch * 57.2958, 1), yaw=round(yaw * 57.2958, 1))
        elif msgid == 253:
            sev, raw = struct.unpack_from("<B50s", p)
            text = raw.split(b"\0", 1)[0].decode("utf-8", "replace").strip()
            self.texts.append(text)
            return f"STATUSTEXT [{sev}] {text}"
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--serial", help="시리얼 포트 (기본: 자동)")
    ap.add_argument("--baud", type=int, default=57600)
    ap.add_argument("--udp", help="중계할 곳 host:port (예: 127.0.0.1:14550)")
    ap.add_argument("--probe", type=float, default=0, help="N초 동안 읽고 요약한 뒤 끝내기")
    ap.add_argument("--no-request", action="store_true", help="데이터 묶음 요청을 보내지 않음(듣기만)")
    args = ap.parse_args()

    port = args.serial or find_port()
    if not port:
        print("비행컨트롤러 시리얼 포트를 찾지 못했어요. USB 케이블(데이터용)과 전원을 확인하세요.", file=sys.stderr)
        return 1
    fd = open_serial(port, args.baud)
    print(f"시리얼: {port} ({args.baud}bps)")
    sock = None
    target = None
    if args.udp:
        host, _, p = args.udp.rpartition(":")
        target = (host or "127.0.0.1", int(p))
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))
        print(f"UDP 중계: → {target[0]}:{target[1]} (돌아오는 바이트는 다시 시리얼로)")

    parser, state = Parser(), State()
    started = last_hb = time.time()
    requested = False
    raw_bytes = 0
    try:
        while True:
            now = time.time()
            if args.probe and now - started > args.probe:
                break
            readable, _, _ = select.select([fd] + ([sock] if sock else []), [], [], 0.2)
            if fd in readable:
                data = os.read(fd, 4096)
                raw_bytes += len(data)
                if sock and target:
                    sock.sendto(data, target)
                for v, sysid, compid, msgid, payload in parser.feed(data):
                    line = state.apply(sysid, compid, msgid, payload)
                    if line:
                        print(f"{now - started:6.1f}s  {line}")
            if sock and sock in readable:
                data, _ = sock.recvfrom(4096)
                os.write(fd, data)  # 앱(지상국)이 보낸 하트비트·요청을 기체로
            if not sock:  # 중계가 아니면 이 스크립트가 지상국 역할
                if now - last_hb >= 1:
                    os.write(fd, gcs_heartbeat(parser.version == 2))
                    last_hb = now
                if state.sys and not requested and not args.no_request:
                    os.write(fd, request_streams(state.sys, state.comp, parser.version == 2))
                    requested = True
    except KeyboardInterrupt:
        pass
    finally:
        os.close(fd)

    elapsed = time.time() - started
    print("\n== 요약")
    print(f"{elapsed:.1f}초 · 받은 바이트 {raw_bytes} · 프레임 {parser.ok} (MAVLink v{parser.version}) · CRC 오류 {parser.crc_errors} · 모르는 메시지 {parser.unknown}")
    print("메시지별:", ", ".join(f"{NAMES.get(k, k)} {v}" for k, v in sorted(state.counts.items())))
    print("상태:", {k: v for k, v in state.info.items()})
    if state.texts:
        print("기체 메시지:", " | ".join(dict.fromkeys(state.texts)))
    return 0 if parser.ok else 2


if __name__ == "__main__":
    sys.exit(main())
