import Foundation

/// 최소 MAVLink v1/v2 (Android `drone/Mavlink.kt`와 같은 규칙). 픽스호크 상태를 읽는 메시지만 다룹니다.
/// 아이폰은 USB 시리얼을 못 쓰므로 와이파이(UDP 14550)로 받습니다 — ESP8266 텔레메트리, 또는 맥에 꽂은
/// 텔레메트리 라디오를 MAVProxy로 중계(`--out udp:아이폰IP:14550`).
enum MavCRC {
    static func accumulate(_ b: UInt8, _ crc: UInt16) -> UInt16 {
        var tmp = b ^ UInt8(truncatingIfNeeded: crc)
        tmp ^= tmp << 4
        return (crc >> 8) ^ (UInt16(tmp) << 8) ^ (UInt16(tmp) << 3) ^ (UInt16(tmp) >> 4)
    }

    static func compute(_ bytes: ArraySlice<UInt8>, extra: UInt8) -> UInt16 {
        var crc: UInt16 = 0xFFFF
        for b in bytes { crc = accumulate(b, crc) }
        return accumulate(extra, crc)
    }
}

enum MavID {
    static let heartbeat: UInt32 = 0, sysStatus: UInt32 = 1, gpsRawInt: UInt32 = 24, globalPositionInt: UInt32 = 33
    static let vfrHud: UInt32 = 74, batteryStatus: UInt32 = 147, statusText: UInt32 = 253

    /// 메시지 번호 → (CRC_EXTRA, 기본 길이). 이 밖의 메시지(ATTITUDE 등)도 CRC를 알아야 프레임을 확인할 수 있어 함께 둡니다.
    static let specs: [UInt32: (UInt8, Int)] = [
        0: (50, 9), 1: (124, 31), 2: (137, 12), 24: (24, 30), 29: (115, 14), 30: (39, 28), 33: (104, 28), 42: (28, 2),
        65: (118, 42), 66: (148, 6), 74: (20, 20), 76: (152, 33), 77: (143, 3), 125: (203, 6), 147: (154, 36),
        193: (71, 22), 242: (104, 52), 253: (83, 51),
    ]
}

struct MavFrame {
    let version: Int
    let sysID: Int
    let compID: Int
    let msgID: UInt32
    let payload: [UInt8]
}

/// 조각조각 들어오는 바이트에서 프레임을 복원 (CRC가 틀리면 시작 바이트 하나만 버리고 다시 찾음)
final class MavlinkParser {
    private var buf: [UInt8] = []
    private(set) var lastVersion = 0

    func feed(_ data: [UInt8]) -> [MavFrame] {
        buf.append(contentsOf: data)
        var out: [MavFrame] = []
        var i = 0
        scan: while true {
            while i < buf.count && buf[i] != 0xFE && buf[i] != 0xFD { i += 1 }
            if i >= buf.count { break }
            let v2 = buf[i] == 0xFD
            let header = v2 ? 10 : 6
            if buf.count - i < header { break }
            let len = Int(buf[i + 1])
            let incompat = v2 ? buf[i + 2] : 0
            if incompat & ~0x01 != 0 { i += 1; continue }
            let frameLen = header + len + 2 + (incompat & 0x01 != 0 ? 13 : 0)
            if buf.count - i < frameLen { break }
            let msgID: UInt32 = v2 ? UInt32(buf[i + 7]) | UInt32(buf[i + 8]) << 8 | UInt32(buf[i + 9]) << 16 : UInt32(buf[i + 5])
            guard let spec = MavID.specs[msgID] else {
                if buf.count - i == frameLen || buf[i + frameLen] == 0xFE || buf[i + frameLen] == 0xFD { i += frameLen } else { i += 1 }
                continue scan
            }
            let crcAt = i + header + len
            let crc = MavCRC.compute(buf[(i + 1)..<crcAt], extra: spec.0)
            if crc != UInt16(buf[crcAt]) | UInt16(buf[crcAt + 1]) << 8 { i += 1; continue }
            out.append(MavFrame(version: v2 ? 2 : 1, sysID: Int(buf[i + (v2 ? 5 : 3)]), compID: Int(buf[i + (v2 ? 6 : 4)]), msgID: msgID, payload: Array(buf[(i + header)..<crcAt])))
            lastVersion = v2 ? 2 : 1
            i += frameLen
        }
        buf.removeFirst(i)
        if buf.count > 65536 { buf.removeAll() }
        return out
    }
}

/// 리틀엔디언 필드 읽기 (v2는 끝의 0 바이트를 잘라 보내므로 모자란 곳은 0)
struct LE {
    let b: [UInt8]
    init(_ payload: [UInt8], length: Int) { b = payload.count >= length ? payload : payload + [UInt8](repeating: 0, count: length - payload.count) }
    func u8(_ o: Int) -> Int { Int(b[o]) }
    func i8(_ o: Int) -> Int { Int(Int8(bitPattern: b[o])) }
    func u16(_ o: Int) -> Int { Int(UInt16(b[o]) | UInt16(b[o + 1]) << 8) }
    func i16(_ o: Int) -> Int { Int(Int16(bitPattern: UInt16(b[o]) | UInt16(b[o + 1]) << 8)) }
    func u32(_ o: Int) -> UInt32 { UInt32(b[o]) | UInt32(b[o + 1]) << 8 | UInt32(b[o + 2]) << 16 | UInt32(b[o + 3]) << 24 }
    func i32(_ o: Int) -> Int { Int(Int32(bitPattern: u32(o))) }
    func f32(_ o: Int) -> Double { Double(Float(bitPattern: u32(o))) }
}

/// 지상국(폰) 하트비트 — 기체가 '지상국이 붙어 있다'고 알게 합니다 (sys 255, comp 190)
enum MavlinkWriter {
    private static var seq: UInt8 = 0

    static func heartbeat(v2: Bool) -> Data {
        frame(msgID: MavID.heartbeat, payload: [0, 0, 0, 0, 6, 8, 0, 4, 3], v2: v2)
    }

    static func frame(msgID: UInt32, payload: [UInt8], v2: Bool) -> Data {
        let extra = MavID.specs[msgID]!.0
        var f: [UInt8] = v2
            ? [0xFD, UInt8(payload.count), 0, 0, seq, 255, 190, UInt8(msgID & 0xFF), UInt8((msgID >> 8) & 0xFF), UInt8((msgID >> 16) & 0xFF)]
            : [0xFE, UInt8(payload.count), seq, 255, 190, UInt8(msgID & 0xFF)]
        seq &+= 1
        f += payload
        let crc = MavCRC.compute(f[1...], extra: extra)
        f += [UInt8(crc & 0xFF), UInt8(crc >> 8)]
        return Data(f)
    }
}
