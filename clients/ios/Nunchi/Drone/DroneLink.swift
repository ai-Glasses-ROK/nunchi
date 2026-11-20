import Foundation
import Network

/// 비행컨트롤러의 '지금 상태' (Android `DroneTelemetry.kt`의 아이폰판 — 화면 표시와 허브에 보낼 실시간 상태 글)
struct DroneTelemetry {
    struct Event: Identifiable {
        let id = UUID()
        let time: Date
        let severity: Int
        let text: String
    }

    var linkState = "연결 안 됨"
    var live = false
    var lastHeartbeat: Date?
    var sysID = 0
    var autopilot = -1
    var vehicleType = -1
    var mode = ""
    var armed = false
    var voltage: Double?
    var batteryPct: Int?
    var gpsFix = -1
    var satellites = -1
    var lat: Double?
    var lon: Double?
    var relAlt: Double?
    var groundSpeed: Double?
    var heading: Int?
    var messages: [Event] = []
    var frames = 0

    var hasVehicle: Bool { autopilot >= 0 }

    mutating func apply(_ f: MavFrame, now: Date) {
        guard let spec = MavID.specs[f.msgID] else { return }
        let p = LE(f.payload, length: spec.1)
        frames += 1
        if f.msgID == MavID.heartbeat {
            let type = p.u8(4), ap = p.u8(5)
            if type == 6 || ap == 8 { return } // 다른 지상국·주변 장치
            if sysID != 0 && f.sysID != sysID { return }
            let newMode = DroneTelemetry.modeName(autopilot: ap, type: type, custom: Int(p.u32(0)))
            sysID = f.sysID; autopilot = ap; vehicleType = type; mode = newMode
            armed = p.u8(6) & 0x80 != 0
            lastHeartbeat = now; live = true; linkState = "연결됨"
            return
        }
        if sysID != 0 && f.sysID != sysID { return }
        switch f.msgID {
        case MavID.sysStatus:
            let mv = p.u16(14)
            if mv > 0 && mv < 65535 { voltage = Double(mv) / 1000 }
            let pct = p.i8(30)
            if (0...100).contains(pct) { batteryPct = pct }
        case MavID.gpsRawInt:
            gpsFix = p.u8(28)
            satellites = p.u8(29) == 255 ? -1 : p.u8(29)
        case MavID.globalPositionInt:
            let la = p.i32(4), lo = p.i32(8)
            if la != 0 || lo != 0 { lat = Double(la) / 1e7; lon = Double(lo) / 1e7 }
            relAlt = Double(p.i32(16)) / 1000
        case MavID.vfrHud:
            groundSpeed = p.f32(4)
            heading = p.i16(16)
        case MavID.batteryStatus:
            let pct = p.i8(35)
            if (0...100).contains(pct) { batteryPct = pct }
        case MavID.statusText:
            let raw = Array(p.b[1..<51])
            let text = String(decoding: raw.prefix { $0 != 0 }, as: UTF8.self).trimmingCharacters(in: .whitespaces)
            guard !text.isEmpty else { return }
            messages.removeAll { $0.text == text } // PreArm은 30초마다 다시 옴 → 시간만 갱신
            messages.append(Event(time: now, severity: p.u8(0), text: text))
            if messages.count > 30 { messages.removeFirst(messages.count - 30) }
        default:
            break
        }
    }

    // MARK: - 글로 바꾸기

    func summary() -> String {
        guard live else { return linkState }
        var parts = [mode, armed ? "시동 ON" : "시동 OFF"]
        if let v = voltage { parts.append(String(format: "%.1fV", v)) }
        if let b = batteryPct { parts.append("\(b)%") }
        if gpsFix >= 0 { parts.append(gpsFix >= 3 ? "GPS \(satellites)개" : DroneTelemetry.fixName(gpsFix)) }
        return parts.joined(separator: " · ")
    }

    /// 허브 프롬프트의 [실시간 기체 상태] 칸에 들어가는 글
    func report() -> String {
        var lines = ["기체: \(autopilot == 3 ? "ArduPilot" : autopilot == 12 ? "PX4" : "autopilot \(autopilot)") · 모드 \(mode) · " + (armed ? "시동 걸림(ARM)" : "시동 꺼짐(DISARM)")]
        lines.append("배터리: " + [voltage.map { String(format: "%.2fV", $0) }, batteryPct.map { "잔량 \($0)%" }].compactMap { $0 }.joined(separator: " · "))
        lines.append("GPS: \(DroneTelemetry.fixName(gpsFix))" + (satellites >= 0 ? " · 위성 \(satellites)개" : ""))
        if let la = lat, let lo = lon {
            lines.append(String(format: "위치: 위도 %.6f, 경도 %.6f", la, lo) + (relAlt.map { String(format: " · 이륙 지점 기준 고도 %.1fm", $0) } ?? ""))
        }
        if let s = groundSpeed { lines.append(String(format: "움직임: 지면 속도 %.1fm/s", s) + (heading.map { " · 기수 방향 \($0)°" } ?? "")) }
        let prearm = currentPrearm()
        if !prearm.isEmpty {
            lines.append("지금 시동을 막는 점검(PreArm) \(prearm.count)개:")
            for p in prearm { lines.append("- \(p.text)") }
        }
        // 지난 묶음에만 있던 PreArm은 이미 해결된 것이라 빼고 보여 줌
        let currentIDs = Set(prearm.map(\.id))
        let shown = messages.filter { !$0.text.hasPrefix("PreArm") || currentIDs.contains($0.id) }
        if !shown.isEmpty {
            lines.append("최근 기체 메시지(새것부터):")
            for e in shown.reversed().prefix(6) { lines.append("- [\(e.severity <= 4 ? "경고" : "정보")] \(e.text)") }
        }
        return lines.joined(separator: "\n")
    }

    /// 검색어에 붙일 상태 키워드 (PreArm 메시지 등 — '상태→검색')
    func retrievalHint() -> String {
        let recent = messages.filter { Date().timeIntervalSince($0.time) < 300 }
        var parts = currentPrearm().prefix(4).map(\.text)
        if let w = recent.last(where: { $0.severity <= 4 && !$0.text.hasPrefix("PreArm") }) { parts.append(w.text) }
        // GPS 미수신은 넣지 않음 (STABILIZE는 GPS 없이 시동 가능 — GPS가 막으면 PreArm 메시지가 따로 옴)
        if let b = batteryPct, b <= 30 { parts.append("배터리 부족") }
        return String(parts.joined(separator: " ").prefix(300))
    }

    /// 지금 시동을 막는 PreArm들. ArduPilot은 실패한 점검을 30초마다 '한꺼번에' 다시 보냅니다
    /// (실기 픽스호크 2.4.8 + ArduCopter 3.6.12에서 확인) → 가장 최근 묶음(1.5초 안)만 현재, 그보다 오래된 건 해결된 것.
    func currentPrearm(now: Date = Date()) -> [Event] {
        guard !armed else { return [] }
        let pre = messages.filter { $0.text.hasPrefix("PreArm") }
        guard let newest = pre.map(\.time).max(), now.timeIntervalSince(newest) < 40 else { return [] }
        return pre.filter { newest.timeIntervalSince($0.time) <= 1.5 }
    }

    static let droneWords = ["드론", "기체", "픽스호크", "pixhawk", "비행", "시동", "이륙", "착륙", "배터리", "전압", "gps", "위성", "고도",
                             "모드", "나침반", "컴퍼스", "프리암", "prearm", "경고", "에러", "오류", "메시지", "상태", "위치", "어디", "속도",
                             "방향", "자세", "arm", "센서", "신호", "조종기", "홈", "출발점", "귀환", "rtl", "고장", "문제", "왜", "안 돼", "안돼"]

    static func isDroneQuestion(_ q: String) -> Bool {
        let s = q.lowercased()
        return droneWords.contains { s.contains($0) } || s.replacingOccurrences(of: " ", with: "").count <= 8
    }

    static func fixName(_ f: Int) -> String {
        switch f {
        case 0: return "GPS 없음"
        case 1: return "위치 못 잡음"
        case 2: return "2D 위치"
        case 3: return "3D 위치"
        case 4...8: return "3D 위치(보정)"
        default: return "정보 없음"
        }
    }

    static func modeName(autopilot: Int, type: Int, custom: Int) -> String {
        if autopilot == 3 {
            let copter = [0: "STABILIZE", 1: "ACRO", 2: "ALT_HOLD", 3: "AUTO", 4: "GUIDED", 5: "LOITER", 6: "RTL", 7: "CIRCLE", 9: "LAND",
                          13: "SPORT", 15: "AUTOTUNE", 16: "POSHOLD", 17: "BRAKE", 21: "SMART_RTL"]
            let plane = [0: "MANUAL", 1: "CIRCLE", 2: "STABILIZE", 5: "FBWA", 6: "FBWB", 7: "CRUISE", 10: "AUTO", 11: "RTL", 12: "LOITER", 15: "GUIDED"]
            let table = [1, 16, 19, 20, 21, 22, 23, 24, 25].contains(type) ? plane : copter
            return table[custom] ?? "MODE \(custom)"
        }
        if autopilot == 12 {
            let main = (custom >> 16) & 0xFF, sub = (custom >> 24) & 0xFF
            if main == 4 { return [2: "TAKEOFF", 3: "LOITER", 4: "MISSION", 5: "RTL", 6: "LAND"][sub] ?? "AUTO" }
            return [1: "MANUAL", 2: "ALTCTL", 3: "POSCTL", 5: "ACRO", 6: "OFFBOARD", 7: "STABILIZED"][main] ?? "MODE \(custom)"
        }
        return "MODE \(custom)"
    }
}

/// 와이파이 텔레메트리: UDP 14550을 열고 기다리면 기체(또는 중계기)가 보내 옵니다. 1초마다 지상국 하트비트로 답합니다.
final class DroneLink: ObservableObject {
    @Published private(set) var telemetry = DroneTelemetry()
    private var listener: NWListener?
    private var connection: NWConnection?
    private let parser = MavlinkParser()
    private let queue = DispatchQueue(label: "nunchi.mavlink")
    private var working = DroneTelemetry()
    private var timer: DispatchSourceTimer?
    private var lastPublish = Date.distantPast

    func startUDP(port: UInt16 = 14550) throws {
        stop()
        guard let p = NWEndpoint.Port(rawValue: port) else { return }
        let params = NWParameters.udp
        params.allowLocalEndpointReuse = true
        let l = try NWListener(using: params, on: p)
        l.newConnectionHandler = { [weak self] conn in self?.accept(conn) }
        l.stateUpdateHandler = { [weak self] state in
            if case .failed(let error) = state { self?.publish { $0.linkState = "열기 실패: \(error.localizedDescription)" } }
        }
        working = DroneTelemetry(linkState: "UDP :\(port) 신호 기다리는 중")
        publish(force: true) { _ in }
        l.start(queue: queue)
        listener = l
        let t = DispatchSource.makeTimerSource(queue: queue)
        t.schedule(deadline: .now() + 1, repeating: 1)
        t.setEventHandler { [weak self] in self?.tick() }
        t.resume()
        timer = t
    }

    func stop() {
        timer?.cancel(); timer = nil
        connection?.cancel(); connection = nil
        listener?.cancel(); listener = nil
        queue.async { [weak self] in self?.publish(force: true) { $0 = DroneTelemetry() } }
    }

    private func accept(_ conn: NWConnection) {
        connection?.cancel()
        connection = conn
        conn.start(queue: queue)
        receive(conn)
    }

    private func receive(_ conn: NWConnection) {
        conn.receiveMessage { [weak self] data, _, _, error in
            guard let self else { return }
            if let data, !data.isEmpty {
                let frames = self.parser.feed([UInt8](data))
                let now = Date()
                self.publish { t in for f in frames { t.apply(f, now: now) } }
            }
            if error == nil { self.receive(conn) }
        }
    }

    private func tick() {
        connection?.send(content: MavlinkWriter.heartbeat(v2: parser.lastVersion == 2), completion: .contentProcessed { _ in })
        if working.live, let hb = working.lastHeartbeat, Date().timeIntervalSince(hb) > 5 {
            publish(force: true) { $0.live = false; $0.linkState = "신호 끊김 (5초 동안 하트비트 없음)" }
        }
    }

    /// 상태를 바꾸고 화면에는 초당 최대 4번만 내보냄 (MAVLink는 초당 수십 개)
    private func publish(force: Bool = false, _ change: (inout DroneTelemetry) -> Void) {
        change(&working)
        let now = Date()
        guard force || now.timeIntervalSince(lastPublish) > 0.25 || working.messages.last?.id != telemetry.messages.last?.id else { return }
        lastPublish = now
        let snapshot = working
        DispatchQueue.main.async { self.telemetry = snapshot }
    }
}
